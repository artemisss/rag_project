from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import AppConfig
from app.main import create_app
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
    assert "Overview" in dashboard_response.text


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
