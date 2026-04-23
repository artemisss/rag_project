from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import GenerationRun, KnowledgeItem, KnowledgeItemType
from app.services.audit import log_event


@dataclass
class KnowledgeItemPayload:
    item_type: KnowledgeItemType
    title: str
    body: str
    context_text: str = ""
    answer_text: str = ""
    marketplace: str = ""
    product_sku: str = ""
    product_name: str = ""
    category: str = ""
    issue_type: str = ""
    rating_bucket: str = ""
    language: str = "ru"
    tags_text: str = ""
    priority: int = 50
    is_active: bool = True


def list_items(
    session: Session, *, item_type: Optional[str] = None, search: str = ""
) -> list[KnowledgeItem]:
    stmt = select(KnowledgeItem).order_by(desc(KnowledgeItem.priority), desc(KnowledgeItem.updated_at))
    if item_type:
        try:
            item_type_enum = KnowledgeItemType(item_type)
        except ValueError:
            item_type_enum = None
        if item_type_enum:
            stmt = stmt.where(KnowledgeItem.item_type == item_type_enum)
    if search:
        search_value = f"%{search.strip()}%"
        stmt = stmt.where(
            (KnowledgeItem.title.ilike(search_value))
            | (KnowledgeItem.body.ilike(search_value))
            | (KnowledgeItem.context_text.ilike(search_value))
            | (KnowledgeItem.answer_text.ilike(search_value))
            | (KnowledgeItem.tags_text.ilike(search_value))
        )
    return list(session.scalars(stmt))


def get_item(session: Session, item_id: int) -> Optional[KnowledgeItem]:
    return session.get(KnowledgeItem, item_id)


def create_item(session: Session, payload: KnowledgeItemPayload) -> KnowledgeItem:
    item = KnowledgeItem(**payload.__dict__)
    session.add(item)
    session.flush()
    log_event(
        session,
        entity_type="knowledge_item",
        entity_id=str(item.id),
        action="created",
        payload={"item_type": item.item_type.value, "title": item.title},
    )
    return item


def update_item(session: Session, item: KnowledgeItem, payload: KnowledgeItemPayload) -> KnowledgeItem:
    for key, value in payload.__dict__.items():
        setattr(item, key, value)
    session.flush()
    log_event(
        session,
        entity_type="knowledge_item",
        entity_id=str(item.id),
        action="updated",
        payload={"item_type": item.item_type.value, "title": item.title},
    )
    return item


def archive_item(session: Session, item: KnowledgeItem) -> None:
    item.is_active = False
    session.flush()
    log_event(
        session,
        entity_type="knowledge_item",
        entity_id=str(item.id),
        action="archived",
        payload={"title": item.title},
    )


def promote_run_to_example(
    session: Session,
    run: GenerationRun,
    *,
    title: str,
    notes: str = "",
) -> KnowledgeItem:
    reply_text = ""
    if run.reply_result:
        reply_text = run.reply_result.get("reply_text", "")

    item = KnowledgeItem(
        item_type=KnowledgeItemType.EXAMPLE,
        title=title.strip() or f"Пример из запуска #{run.id}",
        body=notes.strip() or "Добавлено из истории генераций.",
        context_text=run.review_text,
        answer_text=reply_text,
        marketplace=run.marketplace,
        product_sku=run.product_sku,
        product_name=run.product_name,
        issue_type=", ".join((run.classification_result or {}).get("issue_types", [])),
        rating_bucket=_bucket_rating(run.rating),
        language=run.language,
        tags_text=", ".join(run.reason_codes or []),
        priority=75,
        is_active=True,
    )
    session.add(item)
    session.flush()
    log_event(
        session,
        entity_type="knowledge_item",
        entity_id=str(item.id),
        action="promoted_from_run",
        payload={"run_id": run.id, "title": item.title},
    )
    return item


def seed_initial_knowledge(session: Session, workspace_do_not_say: str, workspace: object) -> None:
    if session.scalar(select(KnowledgeItem.id).limit(1)):
        return

    created = []
    if getattr(workspace, "tone_of_voice", ""):
        created.append(
            KnowledgeItemPayload(
                item_type=KnowledgeItemType.POLICY,
                title="Тон общения",
                body=workspace.tone_of_voice,
                priority=100,
            )
        )
    if getattr(workspace, "return_policy_summary", ""):
        created.append(
            KnowledgeItemPayload(
                item_type=KnowledgeItemType.POLICY,
                title="Возвраты и обмены",
                body=workspace.return_policy_summary,
                issue_type="refund",
                priority=90,
            )
        )
    if getattr(workspace, "public_contact_hint", ""):
        created.append(
            KnowledgeItemPayload(
                item_type=KnowledgeItemType.FAQ,
                title="Как переводить клиента в поддержку",
                body=workspace.public_contact_hint,
                priority=80,
            )
        )
    if getattr(workspace, "compensation_policy", ""):
        created.append(
            KnowledgeItemPayload(
                item_type=KnowledgeItemType.POLICY,
                title="Политика компенсаций",
                body=workspace.compensation_policy,
                issue_type="refund",
                priority=85,
            )
        )

    for line in workspace_do_not_say.splitlines():
        stripped = line.strip("- ").strip()
        if stripped:
            created.append(
                KnowledgeItemPayload(
                    item_type=KnowledgeItemType.FORBIDDEN_PHRASE,
                    title=f"Избегать формулировки: {stripped[:48]}",
                    body=stripped,
                    priority=95,
                )
            )

    for payload in created:
        create_item(session, payload)


def _bucket_rating(rating: int) -> str:
    if rating <= 2:
        return "1-2"
    if rating == 3:
        return "3"
    return "4-5"
