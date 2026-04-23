from __future__ import annotations

from typing import Any
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def log_event(
    session: Session,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    session.add(
        AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            payload=payload or {},
        )
    )
