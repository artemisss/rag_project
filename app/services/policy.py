from __future__ import annotations

from dataclasses import dataclass

from app.db.models import GenerationDecision


HIGH_RISK_TOPICS = {"refund", "defect", "authenticity", "warranty", "safety", "legal"}
MEDIUM_RISK_TOPICS = {"delivery", "packaging", "communication", "size"}


@dataclass
class PolicyOutcome:
    decision: GenerationDecision
    risk_level: str
    reason_codes: list[str]


def decide_review_route(
    *,
    rating: int,
    issue_types: list[str],
    model_risk_level: str,
    model_needs_human: bool,
    reply_needs_human: bool,
    confidence_score: float,
    auto_publish_threshold: float,
) -> PolicyOutcome:
    issue_set = {item.strip().lower() for item in issue_types if item.strip()}
    reason_codes: list[str] = []

    if model_risk_level == "high" or issue_set & HIGH_RISK_TOPICS or rating <= 2:
        reason_codes.append("high_risk_case")
        return PolicyOutcome(
            decision=GenerationDecision.ESCALATE,
            risk_level="high",
            reason_codes=reason_codes + sorted(issue_set & HIGH_RISK_TOPICS),
        )

    if model_needs_human or reply_needs_human:
        reason_codes.append("llm_requires_human")
        return PolicyOutcome(
            decision=GenerationDecision.MANUAL_REVIEW,
            risk_level="medium" if model_risk_level == "medium" else "low",
            reason_codes=reason_codes,
        )

    if rating <= 3 or issue_set & MEDIUM_RISK_TOPICS or model_risk_level == "medium":
        reason_codes.append("manual_moderation_policy")
        return PolicyOutcome(
            decision=GenerationDecision.MANUAL_REVIEW,
            risk_level="medium",
            reason_codes=reason_codes + sorted(issue_set & MEDIUM_RISK_TOPICS),
        )

    if confidence_score >= auto_publish_threshold:
        return PolicyOutcome(
            decision=GenerationDecision.AUTO_PUBLISH_CANDIDATE,
            risk_level="low",
            reason_codes=["safe_standard_case"],
        )

    return PolicyOutcome(
        decision=GenerationDecision.MANUAL_REVIEW,
        risk_level="low",
        reason_codes=["confidence_below_threshold"],
    )

