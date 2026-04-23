from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppConfig
from app.main import create_app
from app.services.generation import ClassificationResult, ReplyResult
from app.services.openai_client import OpenAIStructuredResult, _normalize_strict_json_schema
from app.services.knowledge import KnowledgeItemPayload, create_item
from app.services.policy import decide_review_route
from app.services.retrieval import RetrievalRequest, retrieve_context
from app.db.models import KnowledgeItemType


def make_config(tmp_path: Path) -> AppConfig:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        app_name="ReviewOps AI Test",
        base_dir=tmp_path,
        data_dir=data_dir,
        templates_dir=Path(__file__).resolve().parents[1] / "app" / "templates",
        static_dir=Path(__file__).resolve().parents[1] / "app" / "static",
        database_url=f"sqlite:///{(data_dir / 'test.db').as_posix()}",
        secret_key_path=data_dir / "reviewops.key",
        openai_timeout_seconds=5,
    )


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request_structured_json(self, **kwargs) -> OpenAIStructuredResult:
        schema_name = kwargs["schema_name"]
        self.calls.append(schema_name)
        if schema_name == "connection_check":
            parsed = {"ok": True, "message": "Подключение к OpenAI работает."}
        elif schema_name == "review_classification":
            parsed = {
                "intent": "complaint",
                "issue_types": ["packaging"],
                "sentiment": "negative",
                "risk_level": "medium",
                "needs_human": False,
                "response_strategy": "apology_and_guidance",
                "summary": "Customer reports damaged packaging.",
                "reason_codes": ["packaging_issue"],
            }
        else:
            parsed = {
                "reply_text": "Здравствуйте! Спасибо, что написали. Нам жаль, что упаковка пришла в таком виде. Пожалуйста, свяжитесь с нашей поддержкой через чат заказа, чтобы мы помогли разобраться и предложили корректное решение. С уважением, команда бренда.",
                "tone": "calm, helpful",
                "confidence_score": 0.93,
                "needs_human": False,
                "reason_codes": ["safe_reply"],
                "used_knowledge_ids": [1],
                "decision_hint": "manual_review",
            }

        return OpenAIStructuredResult(
            parsed=parsed,
            raw_response={"output_text": str(parsed)},
            request_payload=kwargs,
            input_tokens=10,
            output_tokens=20,
            latency_ms=30,
        )


def complete_setup(client: TestClient) -> None:
    response = client.post(
        "/api/v1/setup",
        json={
            "project_name": "ReviewOps AI",
            "brand_name": "Acme",
            "brand_description": "Brand",
            "tone_of_voice": "Warm and concise",
            "brand_promises": "We help and clarify",
            "support_signature": "Team Acme",
            "public_contact_hint": "Ask buyer to contact support chat",
            "return_policy_summary": "Returns are handled through support",
            "compensation_policy": "Never promise refunds directly",
            "do_not_say": "- We guarantee refund",
            "default_language": "ru",
            "auto_publish_threshold": 0.9,
            "openai_api_key": "sk-test",
            "openai_model": "gpt-5.4",
            "openai_base_url": "https://api.openai.com/v1",
            "reasoning_effort": "low",
            "text_verbosity": "medium",
        },
    )
    assert response.status_code == 200


def test_setup_gate_redirects_until_workspace_is_configured(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    client = TestClient(app)

    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/welcome"

    setup_response = client.post(
        "/welcome",
        data={
            "project_name": "ReviewOps AI",
            "brand_name": "Acme",
            "brand_description": "Brand",
            "tone_of_voice": "Warm and concise",
            "brand_promises": "We help and clarify",
            "support_signature": "Team Acme",
            "public_contact_hint": "Ask buyer to contact support chat",
            "return_policy_summary": "Returns are handled through support",
            "compensation_policy": "Never promise refunds directly",
            "do_not_say": "- We guarantee refund",
            "default_language": "ru",
            "auto_publish_threshold": "0.9",
            "openai_api_key": "sk-test",
            "openai_model": "gpt-5.4",
            "openai_base_url": "https://api.openai.com/v1",
            "reasoning_effort": "low",
            "text_verbosity": "medium",
        },
        follow_redirects=False,
    )

    assert setup_response.status_code == 303
    assert setup_response.headers["location"].startswith("/dashboard")

    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 200
    assert "Обзор" in dashboard_response.text


def test_api_requires_setup_before_generation(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/api/v1/reviews/generate",
        json={"rating": 5, "review_text": "Все отлично"},
    )

    assert response.status_code == 423
    assert response.json()["error"] == "workspace_setup_required"


def test_retrieval_prefers_matching_issue_type_and_sku(tmp_path: Path) -> None:
    app = create_app(make_config(tmp_path))
    with app.state.session_factory() as session:
        create_item(
            session,
            KnowledgeItemPayload(
                item_type=KnowledgeItemType.POLICY,
                title="Defect handling",
                body="When the product has a defect, apologize and direct the customer to support.",
                product_sku="SKU-1",
                issue_type="defect",
                tags_text="defect, damage, support",
                priority=100,
            ),
        )
        create_item(
            session,
            KnowledgeItemPayload(
                item_type=KnowledgeItemType.FAQ,
                title="Delivery FAQ",
                body="Late delivery responses",
                issue_type="delivery",
                tags_text="delivery, courier",
                priority=20,
            ),
        )
        session.commit()

        result = retrieve_context(
            session,
            RetrievalRequest(
                review_text="Приехал товар с браком и трещиной, нужна помощь",
                product_sku="SKU-1",
                issue_types=["defect"],
                language="ru",
            ),
        )

        assert result.all_items
        assert result.all_items[0].title == "Defect handling"


def test_policy_engine_escalates_high_risk_cases() -> None:
    outcome = decide_review_route(
        rating=1,
        issue_types=["defect", "refund"],
        model_risk_level="high",
        model_needs_human=True,
        reply_needs_human=True,
        confidence_score=0.91,
        auto_publish_threshold=0.9,
    )

    assert outcome.decision.value == "escalate"
    assert outcome.risk_level == "high"


def test_strict_schema_normalizer_marks_all_properties_as_required() -> None:
    classification_schema = _normalize_strict_json_schema(ClassificationResult.model_json_schema())
    reply_schema = _normalize_strict_json_schema(ReplyResult.model_json_schema())

    assert classification_schema["required"] == list(
        classification_schema["properties"].keys()
    )
    assert classification_schema["additionalProperties"] is False

    assert reply_schema["required"] == list(reply_schema["properties"].keys())
    assert reply_schema["additionalProperties"] is False


def test_end_to_end_generation_history_analytics_and_connection_check(tmp_path: Path) -> None:
    fake_client = FakeOpenAIClient()
    app = create_app(make_config(tmp_path), openai_client=fake_client)
    client = TestClient(app)

    complete_setup(client)

    with app.state.session_factory() as session:
        create_item(
            session,
            KnowledgeItemPayload(
                item_type=KnowledgeItemType.EXAMPLE,
                title="Packaging reassurance",
                body="Use this when the customer complains about packaging damage.",
                context_text="Коробка пришла мятая",
                answer_text="Извинитесь и направьте в поддержку.",
                issue_type="packaging",
                tags_text="packaging, damaged box",
                priority=90,
            ),
        )
        session.commit()

    generate_response = client.post(
        "/api/v1/reviews/generate",
        json={
            "marketplace": "manual",
            "product_sku": "SKU-77",
            "product_name": "Bottle",
            "rating": 4,
            "review_text": "Упаковка пришла мятая, но товар внутри целый",
            "customer_name": "Анна",
            "language": "ru",
        },
    )
    assert generate_response.status_code == 200
    payload = generate_response.json()
    assert payload["status"] == "completed"
    assert payload["decision"] == "manual_review"
    assert "reply_text" in payload["reply_result"]

    history_response = client.get("/api/v1/reviews/history")
    assert history_response.status_code == 200
    assert len(history_response.json()["items"]) == 1

    analytics_response = client.get("/api/v1/analytics")
    assert analytics_response.status_code == 200
    analytics = analytics_response.json()
    assert analytics["totals"]["runs"] == 1
    assert analytics["breakdowns"]["decisions"]["manual_review"] == 1

    knowledge_response = client.get("/api/v1/knowledge")
    assert knowledge_response.status_code == 200
    assert len(knowledge_response.json()["items"]) >= 1

    connection_response = client.post("/api/v1/settings/test-openai")
    assert connection_response.status_code == 200
    assert connection_response.json()["ok"] is True

    audit_response = client.get("/api/v1/audit")
    assert audit_response.status_code == 200
    assert len(audit_response.json()["items"]) >= 1

    assert fake_client.calls.count("review_classification") == 1
    assert fake_client.calls.count("review_reply") == 1
    assert fake_client.calls.count("connection_check") == 1
