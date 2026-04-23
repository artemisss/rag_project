from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, GenerationDecision, GenerationRun, GenerationStatus, KnowledgeItem


def build_analytics_snapshot(session: Session) -> dict[str, Any]:
    runs = list(session.scalars(select(GenerationRun).order_by(GenerationRun.created_at.desc())))
    storage_items = list(session.scalars(select(KnowledgeItem)))

    total_runs = len(runs)
    completed_runs = sum(1 for run in runs if run.status == GenerationStatus.COMPLETED)
    failed_runs = total_runs - completed_runs

    decision_counts = Counter(
        run.decision.value for run in runs if run.decision is not None
    )
    risk_counts = Counter(run.risk_level for run in runs if run.risk_level)
    issue_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    sku_counts: Counter[str] = Counter()

    confidence_values = [run.confidence_score for run in runs if run.confidence_score is not None]
    rating_values = [run.rating for run in runs if run.rating]

    for run in runs:
        if run.product_sku:
            sku_counts[run.product_sku] += 1
        for reason in run.reason_codes or []:
            reason_counts[reason] += 1
        for issue_type in (run.classification_result or {}).get("issue_types", []):
            issue_counts[issue_type] += 1

    avg_confidence = round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else None
    avg_rating = round(sum(rating_values) / len(rating_values), 2) if rating_values else None

    storage_breakdown = Counter(item.item_type.value for item in storage_items if item.is_active)
    audit_count = session.scalar(select(func.count(AuditLog.id))) or 0

    return {
        "totals": {
            "runs": total_runs,
            "completed_runs": completed_runs,
            "failed_runs": failed_runs,
            "audit_events": audit_count,
            "storage_items": len(storage_items),
            "active_storage_items": sum(1 for item in storage_items if item.is_active),
        },
        "rates": {
            "success_rate": round((completed_runs / total_runs) * 100, 1) if total_runs else 0.0,
            "auto_publish_share": round(
                (decision_counts.get(GenerationDecision.AUTO_PUBLISH_CANDIDATE.value, 0) / total_runs) * 100,
                1,
            )
            if total_runs
            else 0.0,
        },
        "averages": {
            "confidence": avg_confidence,
            "rating": avg_rating,
        },
        "breakdowns": {
            "decisions": dict(decision_counts),
            "risk_levels": dict(risk_counts),
            "storage_types": dict(storage_breakdown),
        },
        "top_issue_types": _top_counter(issue_counts),
        "top_reason_codes": _top_counter(reason_counts),
        "top_skus": _top_counter(sku_counts),
        "recent_run_ids": [run.id for run in runs[:10]],
    }


def _top_counter(counter: Counter[str], limit: int = 8) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]
