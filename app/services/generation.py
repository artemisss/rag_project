from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.security import SecretBox
from app.db.models import GenerationRun, GenerationStatus, LLMRun
from app.services.audit import log_event
from app.services.openai_client import (
    OpenAIRefusalError,
    OpenAIResponseError,
    OpenAIResponsesClient,
)
from app.services.policy import decide_review_route
from app.services.retrieval import RetrievalRequest, retrieve_context
from app.services.workspace import get_active_prompt_version, get_openai_api_key, get_workspace


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["complaint", "praise", "question", "mixed"]
    issue_types: list[
        Literal[
            "delivery",
            "defect",
            "size",
            "packaging",
            "authenticity",
            "warranty",
            "refund",
            "communication",
            "price",
            "usability",
            "other",
        ]
    ] = Field(default_factory=list)
    sentiment: Literal["positive", "neutral", "negative", "mixed"]
    risk_level: Literal["low", "medium", "high"]
    needs_human: bool
    response_strategy: Literal[
        "gratitude",
        "clarify",
        "apology_and_guidance",
        "escalate",
        "request_contact",
        "reassure",
    ]
    summary: str
    reason_codes: list[str] = Field(default_factory=list)


class ReplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_text: str
    tone: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    needs_human: bool
    reason_codes: list[str] = Field(default_factory=list)
    used_knowledge_ids: list[int] = Field(default_factory=list)
    decision_hint: Literal["auto_publish_candidate", "manual_review", "escalate"]


@dataclass
class ReviewInput:
    marketplace: str
    product_sku: str
    product_name: str
    rating: int
    review_text: str
    customer_name: str = ""
    language: str = "ru"


@dataclass
class GenerationPipelineResult:
    run: GenerationRun
    retrieved_context: Any


def generate_review_reply(
    session: Session,
    *,
    input_data: ReviewInput,
    secret_box: SecretBox,
    openai_client: OpenAIResponsesClient,
) -> GenerationPipelineResult:
    workspace = get_workspace(session)
    if not workspace or not workspace.setup_completed_at:
        raise ValueError("Workspace is not configured yet.")

    api_key = get_openai_api_key(workspace, secret_box)
    if not api_key:
        raise ValueError("OpenAI API key is missing or could not be decrypted.")

    prompt_version = get_active_prompt_version(session)

    heuristic_issue_types = detect_issue_types(input_data.review_text)
    retrieved_context = retrieve_context(
        session,
        RetrievalRequest(
            review_text=input_data.review_text,
            marketplace=input_data.marketplace,
            product_sku=input_data.product_sku,
            product_name=input_data.product_name,
            issue_types=heuristic_issue_types,
            language=input_data.language,
        ),
    )

    run = GenerationRun(
        marketplace=input_data.marketplace,
        product_sku=input_data.product_sku,
        product_name=input_data.product_name,
        rating=input_data.rating,
        review_text=input_data.review_text.strip(),
        customer_name=input_data.customer_name.strip(),
        language=input_data.language,
        retrieved_item_ids=[item.id for item in retrieved_context.all_items],
        retrieved_snapshot={
            "search_terms": retrieved_context.search_terms,
            "policies": [item.id for item in retrieved_context.policies],
            "examples": [item.id for item in retrieved_context.examples],
            "product_facts": [item.id for item in retrieved_context.product_facts],
            "faq": [item.id for item in retrieved_context.faq_items],
            "forbidden_phrases": [item.id for item in retrieved_context.forbidden_phrases],
        },
        prompt_version_id=prompt_version.id,
        status=GenerationStatus.FAILED,
    )
    session.add(run)
    session.flush()

    brand_context = build_brand_context(workspace)

    try:
        classification = openai_client.request_structured_json(
            api_key=api_key,
            model=workspace.openai_model,
            system_prompt=f"{prompt_version.system_prompt}\n\n{prompt_version.classifier_prompt}",
            user_prompt=build_classifier_user_prompt(
                input_data=input_data,
                heuristic_issue_types=heuristic_issue_types,
                brand_context=brand_context,
                retrieved_context_text=retrieved_context.to_prompt_block(),
            ),
            schema=ClassificationResult.model_json_schema(),
            schema_name="review_classification",
            reasoning_effort=workspace.reasoning_effort,
            verbosity=workspace.text_verbosity,
            max_output_tokens=1200,
        )
        classification_result = ClassificationResult.model_validate(classification.parsed)
        _store_llm_run(
            session,
            run_id=run.id,
            step_name="classification",
            model_name=workspace.openai_model,
            schema_name="review_classification",
            prompt_version_id=prompt_version.id,
            result=classification,
        )

        generation = openai_client.request_structured_json(
            api_key=api_key,
            model=workspace.openai_model,
            system_prompt=f"{prompt_version.system_prompt}\n\n{prompt_version.generator_prompt}",
            user_prompt=build_generator_user_prompt(
                input_data=input_data,
                brand_context=brand_context,
                classification=classification_result,
                retrieved_context_text=retrieved_context.to_prompt_block(),
            ),
            schema=ReplyResult.model_json_schema(),
            schema_name="review_reply",
            reasoning_effort=workspace.reasoning_effort,
            verbosity=workspace.text_verbosity,
            max_output_tokens=1600,
        )
        reply_result = ReplyResult.model_validate(generation.parsed)
        _store_llm_run(
            session,
            run_id=run.id,
            step_name="generation",
            model_name=workspace.openai_model,
            schema_name="review_reply",
            prompt_version_id=prompt_version.id,
            result=generation,
        )

        policy = decide_review_route(
            rating=input_data.rating,
            issue_types=classification_result.issue_types,
            model_risk_level=classification_result.risk_level,
            model_needs_human=classification_result.needs_human,
            reply_needs_human=reply_result.needs_human,
            confidence_score=reply_result.confidence_score,
            auto_publish_threshold=workspace.auto_publish_threshold,
        )

        run.classification_result = classification_result.model_dump()
        run.reply_result = reply_result.model_dump()
        run.decision = policy.decision
        run.risk_level = policy.risk_level
        run.confidence_score = reply_result.confidence_score
        run.reason_codes = sorted(
            set(
                classification_result.reason_codes
                + reply_result.reason_codes
                + policy.reason_codes
            )
        )
        run.status = GenerationStatus.COMPLETED
        run.error_text = ""

        log_event(
            session,
            entity_type="generation_run",
            entity_id=str(run.id),
            action="completed",
            payload={
                "decision": run.decision.value if run.decision else "",
                "risk_level": run.risk_level,
                "knowledge_ids": run.retrieved_item_ids,
            },
        )
        session.flush()
        return GenerationPipelineResult(run=run, retrieved_context=retrieved_context)

    except (OpenAIResponseError, OpenAIRefusalError, ValueError) as exc:
        run.error_text = str(exc)
        run.status = GenerationStatus.FAILED
        log_event(
            session,
            entity_type="generation_run",
            entity_id=str(run.id),
            action="failed",
            payload={"error": str(exc)},
        )
        session.flush()
        raise


def build_brand_context(workspace: Any) -> str:
    return f"""
Project: {workspace.project_name}
Brand: {workspace.brand_name}
Brand description: {workspace.brand_description}
Tone of voice: {workspace.tone_of_voice}
Brand promises: {workspace.brand_promises}
Support signature: {workspace.support_signature}
Public contact hint: {workspace.public_contact_hint}
Return policy summary: {workspace.return_policy_summary}
Compensation policy: {workspace.compensation_policy}
Forbidden phrases: {workspace.do_not_say}
Default language: {workspace.default_language}
""".strip()


def build_classifier_user_prompt(
    *,
    input_data: ReviewInput,
    heuristic_issue_types: list[str],
    brand_context: str,
    retrieved_context_text: str,
) -> str:
    return f"""
Analyze the review for a customer-care workflow and return strict JSON.

Brand context:
{brand_context}

Review:
- marketplace: {input_data.marketplace}
- product_sku: {input_data.product_sku}
- product_name: {input_data.product_name}
- rating: {input_data.rating}
- language: {input_data.language}
- customer_name: {input_data.customer_name}
- text: {input_data.review_text}

Heuristic issue hints:
{", ".join(heuristic_issue_types) if heuristic_issue_types else "none"}

Retrieved storage:
{retrieved_context_text}
""".strip()


def build_generator_user_prompt(
    *,
    input_data: ReviewInput,
    brand_context: str,
    classification: ClassificationResult,
    retrieved_context_text: str,
) -> str:
    return f"""
Draft a reply for a marketplace review and return strict JSON.

Brand context:
{brand_context}

Review:
- marketplace: {input_data.marketplace}
- product_sku: {input_data.product_sku}
- product_name: {input_data.product_name}
- rating: {input_data.rating}
- language: {input_data.language}
- customer_name: {input_data.customer_name}
- text: {input_data.review_text}

Classification:
{classification.model_dump_json(indent=2)}

Retrieved storage:
{retrieved_context_text}

Write a fresh reply. Do not quote forbidden phrases. Do not copy examples verbatim.
""".strip()


def _store_llm_run(
    session: Session,
    *,
    run_id: int,
    step_name: str,
    model_name: str,
    schema_name: str,
    prompt_version_id: int,
    result: Any,
) -> None:
    session.add(
        LLMRun(
            generation_run_id=run_id,
            step_name=step_name,
            model_name=model_name,
            schema_name=schema_name,
            prompt_version_id=prompt_version_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            request_payload=result.request_payload,
            response_payload=result.raw_response,
        )
    )


def detect_issue_types(review_text: str) -> list[str]:
    text = review_text.lower()
    mapping = {
        "defect": ["брак", "слом", "не работает", "broken", "defect", "трещин", "скол"],
        "refund": ["возврат", "refund", "деньги", "компенсац", "вернуть"],
        "delivery": ["доставка", "shipping", "курьер", "задерж", "опоздал"],
        "packaging": ["упаков", "box", "мятая", "пакет", "коробка"],
        "size": ["размер", "маломер", "большемер", "size", "fit"],
        "authenticity": ["поддел", "fake", "оригинал"],
        "warranty": ["гарант", "warranty"],
        "communication": ["поддержк", "service", "ответили", "менеджер", "хам"],
        "price": ["цена", "дорого", "price"],
        "usability": ["неудоб", "непонят", "сложно", "works badly"],
    }
    found: list[str] = []
    for issue_type, patterns in mapping.items():
        if any(pattern in text for pattern in patterns):
            found.append(issue_type)
    return found or ["other"]

