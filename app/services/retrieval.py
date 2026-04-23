from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import KnowledgeItem, KnowledgeItemType


TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9-]{3,}")


@dataclass
class RetrievalRequest:
    review_text: str
    marketplace: str = ""
    product_sku: str = ""
    product_name: str = ""
    issue_types: Optional[list[str]] = None
    language: str = "ru"


@dataclass
class RetrievedContext:
    all_items: list[KnowledgeItem]
    policies: list[KnowledgeItem]
    examples: list[KnowledgeItem]
    product_facts: list[KnowledgeItem]
    forbidden_phrases: list[KnowledgeItem]
    faq_items: list[KnowledgeItem]
    search_terms: list[str]

    def to_prompt_block(self) -> str:
        def format_items(prefix: str, items: list[KnowledgeItem]) -> str:
            if not items:
                return f"{prefix}: none"
            lines = [prefix + ":"]
            for item in items:
                base = f"[#{item.id}] {item.title}"
                if item.item_type == KnowledgeItemType.EXAMPLE:
                    lines.append(
                        f"- {base}\n  review: {item.context_text}\n  answer: {item.answer_text}\n  note: {item.body}"
                    )
                else:
                    lines.append(f"- {base}: {item.body}")
            return "\n".join(lines)

        blocks = [
            f"Search terms: {', '.join(self.search_terms) if self.search_terms else 'none'}",
            format_items("Policies", self.policies),
            format_items("Product facts", self.product_facts),
            format_items("FAQ", self.faq_items),
            format_items("Forbidden phrases", self.forbidden_phrases),
            format_items("Golden examples", self.examples),
        ]
        return "\n\n".join(blocks)


def retrieve_context(session: Session, request: RetrievalRequest, limit: int = 14) -> RetrievedContext:
    search_terms = _extract_terms(
        request.review_text,
        request.product_sku,
        request.product_name,
        request.issue_types or [],
    )
    query = _build_match_query(search_terms)

    items: list[KnowledgeItem] = []
    if query:
        sql = text(
            """
            SELECT k.id
            FROM knowledge_items_fts f
            JOIN knowledge_items k ON k.id = f.rowid
            WHERE knowledge_items_fts MATCH :query
              AND k.is_active = 1
              AND (:marketplace = '' OR k.marketplace = '' OR k.marketplace = :marketplace)
              AND (:product_sku = '' OR k.product_sku = '' OR k.product_sku = :product_sku)
              AND (:language = '' OR k.language = :language)
            ORDER BY bm25(knowledge_items_fts), k.priority DESC
            LIMIT :limit
            """
        )
        result = session.execute(
            sql,
            {
                "query": query,
                "marketplace": request.marketplace,
                "product_sku": request.product_sku,
                "language": request.language,
                "limit": limit,
            },
        )
        ids = [row[0] for row in result]
        if ids:
            mapped = {
                item.id: item
                for item in session.query(KnowledgeItem).filter(KnowledgeItem.id.in_(ids)).all()
            }
            items = [mapped[item_id] for item_id in ids if item_id in mapped]

    if not items:
        items = (
            session.query(KnowledgeItem)
            .filter(KnowledgeItem.is_active.is_(True))
            .order_by(KnowledgeItem.priority.desc(), KnowledgeItem.updated_at.desc())
            .limit(limit)
            .all()
        )

    policies = [item for item in items if item.item_type == KnowledgeItemType.POLICY][:5]
    examples = [item for item in items if item.item_type == KnowledgeItemType.EXAMPLE][:4]
    product_facts = [
        item for item in items if item.item_type == KnowledgeItemType.PRODUCT_FACT
    ][:4]
    forbidden = [
        item for item in items if item.item_type == KnowledgeItemType.FORBIDDEN_PHRASE
    ][:6]
    faq = [item for item in items if item.item_type == KnowledgeItemType.FAQ][:4]

    return RetrievedContext(
        all_items=items,
        policies=policies,
        examples=examples,
        product_facts=product_facts,
        forbidden_phrases=forbidden,
        faq_items=faq,
        search_terms=search_terms,
    )


def _extract_terms(review_text: str, product_sku: str, product_name: str, issue_types: list[str]) -> list[str]:
    raw_terms = TOKEN_RE.findall(" ".join([review_text, product_sku, product_name, " ".join(issue_types)]).lower())
    stop_words = {
        "это",
        "как",
        "что",
        "было",
        "очень",
        "товар",
        "заказ",
        "просто",
        "есть",
        "пришел",
        "пришла",
        "потому",
        "когда",
        "после",
        "with",
        "this",
        "that",
        "very",
        "have",
        "from",
    }
    unique_terms: list[str] = []
    for term in raw_terms:
        if term in stop_words:
            continue
        if term not in unique_terms:
            unique_terms.append(term)
    return unique_terms[:10]


def _build_match_query(terms: list[str]) -> str:
    if not terms:
        return ""
    parts = [f'"{term}"' if "-" in term else term for term in terms]
    return " OR ".join(parts)
