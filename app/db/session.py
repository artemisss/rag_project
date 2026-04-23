from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base


def create_session_factory(database_url: str) -> tuple[object, sessionmaker[Session]]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    return engine, session_factory


def init_database(engine: object) -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if "sqlite" in str(engine.url):
        _ensure_sqlite_fts(engine)


def _ensure_sqlite_fts(engine: object) -> None:
    statements = [
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_items_fts
        USING fts5(
            title,
            body,
            context_text,
            answer_text,
            tags_text,
            marketplace,
            product_sku,
            issue_type,
            item_type,
            content='knowledge_items',
            content_rowid='id'
        )
        """,
        """
        CREATE TRIGGER IF NOT EXISTS knowledge_items_ai
        AFTER INSERT ON knowledge_items
        BEGIN
            INSERT INTO knowledge_items_fts(
                rowid, title, body, context_text, answer_text, tags_text,
                marketplace, product_sku, issue_type, item_type
            )
            VALUES (
                new.id, new.title, new.body, new.context_text, new.answer_text, new.tags_text,
                new.marketplace, new.product_sku, new.issue_type, new.item_type
            );
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS knowledge_items_ad
        AFTER DELETE ON knowledge_items
        BEGIN
            INSERT INTO knowledge_items_fts(knowledge_items_fts, rowid, title, body, context_text, answer_text, tags_text, marketplace, product_sku, issue_type, item_type)
            VALUES('delete', old.id, old.title, old.body, old.context_text, old.answer_text, old.tags_text, old.marketplace, old.product_sku, old.issue_type, old.item_type);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS knowledge_items_au
        AFTER UPDATE ON knowledge_items
        BEGIN
            INSERT INTO knowledge_items_fts(knowledge_items_fts, rowid, title, body, context_text, answer_text, tags_text, marketplace, product_sku, issue_type, item_type)
            VALUES('delete', old.id, old.title, old.body, old.context_text, old.answer_text, old.tags_text, old.marketplace, old.product_sku, old.issue_type, old.item_type);
            INSERT INTO knowledge_items_fts(
                rowid, title, body, context_text, answer_text, tags_text,
                marketplace, product_sku, issue_type, item_type
            )
            VALUES (
                new.id, new.title, new.body, new.context_text, new.answer_text, new.tags_text,
                new.marketplace, new.product_sku, new.issue_type, new.item_type
            );
        END
        """,
        "INSERT INTO knowledge_items_fts(knowledge_items_fts) VALUES('rebuild')",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def get_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()

