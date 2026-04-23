from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class KnowledgeItemType(str, enum.Enum):
    POLICY = "policy"
    EXAMPLE = "example"
    PRODUCT_FACT = "product_fact"
    FAQ = "faq"
    FORBIDDEN_PHRASE = "forbidden_phrase"


class GenerationDecision(str, enum.Enum):
    AUTO_PUBLISH_CANDIDATE = "auto_publish_candidate"
    MANUAL_REVIEW = "manual_review"
    ESCALATE = "escalate"


class GenerationStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class WorkspaceSettings(Base, TimestampMixin):
    __tablename__ = "workspace_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    project_name: Mapped[str] = mapped_column(String(200), default="ReviewOps AI")
    brand_name: Mapped[str] = mapped_column(String(200))
    brand_description: Mapped[str] = mapped_column(Text, default="")
    tone_of_voice: Mapped[str] = mapped_column(Text, default="")
    brand_promises: Mapped[str] = mapped_column(Text, default="")
    support_signature: Mapped[str] = mapped_column(Text, default="")
    public_contact_hint: Mapped[str] = mapped_column(Text, default="")
    return_policy_summary: Mapped[str] = mapped_column(Text, default="")
    compensation_policy: Mapped[str] = mapped_column(Text, default="")
    do_not_say: Mapped[str] = mapped_column(Text, default="")
    default_language: Mapped[str] = mapped_column(String(10), default="ru")
    auto_publish_threshold: Mapped[float] = mapped_column(Float, default=0.9)
    openai_api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    openai_model: Mapped[str] = mapped_column(String(100), default="gpt-5.4")
    openai_base_url: Mapped[str] = mapped_column(
        String(200), default="https://api.openai.com/v1"
    )
    reasoning_effort: Mapped[str] = mapped_column(String(20), default="low")
    text_verbosity: Mapped[str] = mapped_column(String(20), default="medium")
    setup_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PromptVersion(Base, TimestampMixin):
    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    system_prompt: Mapped[str] = mapped_column(Text)
    classifier_prompt: Mapped[str] = mapped_column(Text)
    generator_prompt: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class KnowledgeItem(Base, TimestampMixin):
    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_type: Mapped[KnowledgeItemType] = mapped_column(
        Enum(KnowledgeItemType, native_enum=False), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    context_text: Mapped[str] = mapped_column(Text, default="")
    answer_text: Mapped[str] = mapped_column(Text, default="")
    marketplace: Mapped[str] = mapped_column(String(50), default="")
    product_sku: Mapped[str] = mapped_column(String(100), default="", index=True)
    product_name: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(100), default="")
    issue_type: Mapped[str] = mapped_column(String(100), default="", index=True)
    rating_bucket: Mapped[str] = mapped_column(String(50), default="")
    language: Mapped[str] = mapped_column(String(10), default="ru")
    tags_text: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class GenerationRun(Base, TimestampMixin):
    __tablename__ = "generation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(50), default="manual")
    product_sku: Mapped[str] = mapped_column(String(100), default="")
    product_name: Mapped[str] = mapped_column(String(255), default="")
    rating: Mapped[int] = mapped_column(Integer, default=0)
    review_text: Mapped[str] = mapped_column(Text)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    language: Mapped[str] = mapped_column(String(10), default="ru")
    classification_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    reply_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    retrieved_item_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    retrieved_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    decision: Mapped[Optional[GenerationDecision]] = mapped_column(
        Enum(GenerationDecision, native_enum=False), nullable=True, index=True
    )
    risk_level: Mapped[str] = mapped_column(String(20), default="")
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[GenerationStatus] = mapped_column(
        Enum(GenerationStatus, native_enum=False),
        default=GenerationStatus.COMPLETED,
        index=True,
    )
    error_text: Mapped[str] = mapped_column(Text, default="")
    prompt_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    prompt_version: Mapped[Optional[PromptVersion]] = relationship()


class LLMRun(Base, TimestampMixin):
    __tablename__ = "llm_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("generation_runs.id"), nullable=True, index=True
    )
    step_name: Mapped[str] = mapped_column(String(50))
    model_name: Mapped[str] = mapped_column(String(100))
    schema_name: Mapped[str] = mapped_column(String(100))
    prompt_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=True
    )
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
