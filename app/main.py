from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import AppConfig
from app.core.security import SecretBox, mask_secret
from app.db.models import (
    AuditLog,
    GenerationRun,
    GenerationStatus,
    KnowledgeItem,
    KnowledgeItemType,
    PromptVersion,
)
from app.db.session import create_session_factory, init_database
from app.services.generation import ReviewInput, generate_review_reply
from app.services.knowledge import (
    KnowledgeItemPayload,
    archive_item,
    create_item,
    get_item,
    list_items,
    promote_run_to_example,
    seed_initial_knowledge,
    update_item,
)
from app.services.openai_client import OpenAIResponsesClient
from app.services.retrieval import RetrievalRequest, retrieve_context
from app.services.workspace import (
    WorkspaceSetupPayload,
    get_active_prompt_version,
    get_workspace,
    is_setup_complete,
    save_workspace_setup,
    update_prompts,
)


class SetupAPIRequest(BaseModel):
    project_name: str
    brand_name: str
    brand_description: str = ""
    tone_of_voice: str = ""
    brand_promises: str = ""
    support_signature: str = ""
    public_contact_hint: str = ""
    return_policy_summary: str = ""
    compensation_policy: str = ""
    do_not_say: str = ""
    default_language: str = "ru"
    auto_publish_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    openai_api_key: str
    openai_model: str = "gpt-5.4"
    openai_base_url: str = "https://api.openai.com/v1"
    reasoning_effort: str = "low"
    text_verbosity: str = "medium"


class GenerateAPIRequest(BaseModel):
    marketplace: str = "manual"
    product_sku: str = ""
    product_name: str = ""
    rating: int = Field(ge=1, le=5)
    review_text: str
    customer_name: str = ""
    language: str = "ru"


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
    config = config or AppConfig.from_env()
    config.ensure_directories()
    engine, session_factory = create_session_factory(config.database_url)
    init_database(engine)
    secret_box = SecretBox(config.secret_key_path)
    openai_client = OpenAIResponsesClient(
        base_url="https://api.openai.com/v1", timeout_seconds=config.openai_timeout_seconds
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with session_factory() as session:
            prompt = get_active_prompt_version(session)
            if prompt.id:
                session.commit()
        yield

    app = FastAPI(title=config.app_name, lifespan=lifespan)
    templates = Jinja2Templates(directory=str(config.templates_dir))
    app.mount("/static", StaticFiles(directory=str(config.static_dir)), name="static")

    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.secret_box = secret_box
    app.state.openai_client = openai_client
    app.state.templates = templates

    @app.middleware("http")
    async def setup_gate(request: Request, call_next):
        public_paths = {"/welcome", "/api/setup/status", "/api/health"}
        if request.url.path.startswith("/static") or request.url.path in public_paths:
            return await call_next(request)

        with session_factory() as session:
            if not is_setup_complete(session):
                return RedirectResponse(url="/welcome", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return await call_next(request)

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        return {"ok": True, "app": config.app_name}

    @app.get("/api/setup/status")
    async def api_setup_status() -> dict[str, Any]:
        with session_factory() as session:
            workspace = get_workspace(session)
            return {
                "setup_complete": is_setup_complete(session),
                "brand_name": workspace.brand_name if workspace else None,
            }

    @app.post("/api/setup")
    async def api_setup(payload: SetupAPIRequest) -> JSONResponse:
        with session_factory() as session:
            workspace = save_workspace_setup(
                session,
                secret_box,
                WorkspaceSetupPayload(**payload.model_dump()),
            )
            seed_initial_knowledge(session, workspace.do_not_say, workspace)
            session.commit()
            return JSONResponse(
                {
                    "setup_complete": True,
                    "brand_name": workspace.brand_name,
                    "project_name": workspace.project_name,
                }
            )

    @app.post("/api/reviews/generate")
    async def api_generate_review(payload: GenerateAPIRequest) -> JSONResponse:
        with session_factory() as session:
            result = generate_review_reply(
                session,
                input_data=ReviewInput(**payload.model_dump()),
                secret_box=secret_box,
                openai_client=_client_for_workspace(session, config, openai_client),
            )
            session.commit()
            return JSONResponse(_serialize_run(result.run))

    @app.get("/", response_class=HTMLResponse)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/welcome", response_class=HTMLResponse)
    async def welcome(request: Request):
        with session_factory() as session:
            if is_setup_complete(session):
                return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
            context = _base_context(request, session, current_page="welcome")
            return templates.TemplateResponse(request, "welcome.html", context)

    @app.post("/welcome", response_class=HTMLResponse)
    async def welcome_submit(
        request: Request,
        project_name: str = Form(...),
        brand_name: str = Form(...),
        brand_description: str = Form(""),
        tone_of_voice: str = Form(""),
        brand_promises: str = Form(""),
        support_signature: str = Form(""),
        public_contact_hint: str = Form(""),
        return_policy_summary: str = Form(""),
        compensation_policy: str = Form(""),
        do_not_say: str = Form(""),
        default_language: str = Form("ru"),
        auto_publish_threshold: float = Form(0.9),
        openai_api_key: str = Form(...),
        openai_model: str = Form("gpt-5.4"),
        openai_base_url: str = Form("https://api.openai.com/v1"),
        reasoning_effort: str = Form("low"),
        text_verbosity: str = Form("medium"),
    ):
        payload = WorkspaceSetupPayload(
            project_name=project_name,
            brand_name=brand_name,
            brand_description=brand_description,
            tone_of_voice=tone_of_voice,
            brand_promises=brand_promises,
            support_signature=support_signature,
            public_contact_hint=public_contact_hint,
            return_policy_summary=return_policy_summary,
            compensation_policy=compensation_policy,
            do_not_say=do_not_say,
            default_language=default_language,
            auto_publish_threshold=auto_publish_threshold,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            openai_base_url=openai_base_url,
            reasoning_effort=reasoning_effort,
            text_verbosity=text_verbosity,
        )
        with session_factory() as session:
            workspace = save_workspace_setup(session, secret_box, payload)
            seed_initial_knowledge(session, workspace.do_not_say, workspace)
            session.commit()
        return RedirectResponse(
            url="/dashboard?message=Workspace+configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        with session_factory() as session:
            context = _base_context(request, session, current_page="dashboard")
            total_runs = session.scalar(select(func.count(GenerationRun.id))) or 0
            total_items = session.scalar(select(func.count(KnowledgeItem.id))) or 0
            open_runs = session.scalar(
                select(func.count(GenerationRun.id)).where(
                    GenerationRun.status == GenerationStatus.COMPLETED,
                    GenerationRun.decision.in_(["manual_review", "escalate"]),
                )
            ) or 0
            examples = session.scalar(
                select(func.count(KnowledgeItem.id)).where(
                    KnowledgeItem.item_type == KnowledgeItemType.EXAMPLE
                )
            ) or 0
            recent_runs = list(
                session.scalars(
                    select(GenerationRun).order_by(GenerationRun.created_at.desc()).limit(8)
                )
            )
            recent_audit = list(
                session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8))
            )
            context.update(
                {
                    "stats": {
                        "total_runs": total_runs,
                        "total_items": total_items,
                        "open_runs": open_runs,
                        "examples": examples,
                    },
                    "recent_runs": recent_runs,
                    "recent_audit": recent_audit,
                }
            )
            return templates.TemplateResponse(request, "dashboard.html", context)

    @app.get("/playground", response_class=HTMLResponse)
    async def playground(request: Request):
        with session_factory() as session:
            context = _base_context(request, session, current_page="playground")
            selected_run = None
            run_id = request.query_params.get("run_id")
            if run_id and run_id.isdigit():
                selected_run = session.get(GenerationRun, int(run_id))
            context.update({"selected_run": selected_run})
            return templates.TemplateResponse(request, "playground.html", context)

    @app.post("/playground", response_class=HTMLResponse)
    async def playground_submit(
        request: Request,
        marketplace: str = Form("manual"),
        product_sku: str = Form(""),
        product_name: str = Form(""),
        rating: int = Form(...),
        review_text: str = Form(...),
        customer_name: str = Form(""),
        language: str = Form("ru"),
    ):
        with session_factory() as session:
            try:
                result = generate_review_reply(
                    session,
                    input_data=ReviewInput(
                        marketplace=marketplace,
                        product_sku=product_sku,
                        product_name=product_name,
                        rating=rating,
                        review_text=review_text,
                        customer_name=customer_name,
                        language=language,
                    ),
                    secret_box=secret_box,
                    openai_client=_client_for_workspace(session, config, openai_client),
                )
                session.commit()
                return RedirectResponse(
                    url=f"/playground?run_id={result.run.id}&message=Draft+generated",
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            except Exception as exc:
                session.commit()
                return RedirectResponse(
                    url=f"/playground?error={_quote_query(str(exc))}",
                    status_code=status.HTTP_303_SEE_OTHER,
                )

    @app.get("/storage", response_class=HTMLResponse)
    async def storage(request: Request, item_type: Optional[str] = None, search: str = ""):
        with session_factory() as session:
            items = list_items(session, item_type=item_type, search=search)
            edit_item = None
            edit_id = request.query_params.get("edit")
            if edit_id and edit_id.isdigit():
                edit_item = get_item(session, int(edit_id))
            context = _base_context(request, session, current_page="storage")
            context.update(
                {
                    "items": items,
                    "edit_item": edit_item,
                    "selected_item_type": item_type or "",
                    "search": search,
                    "item_types": list(KnowledgeItemType),
                }
            )
            return templates.TemplateResponse(request, "storage.html", context)

    @app.post("/storage", response_class=HTMLResponse)
    async def storage_submit(
        request: Request,
        item_id: int = Form(0),
        item_type: str = Form(...),
        title: str = Form(...),
        body: str = Form(""),
        context_text: str = Form(""),
        answer_text: str = Form(""),
        marketplace: str = Form(""),
        product_sku: str = Form(""),
        product_name: str = Form(""),
        category: str = Form(""),
        issue_type: str = Form(""),
        rating_bucket: str = Form(""),
        language: str = Form("ru"),
        tags_text: str = Form(""),
        priority: int = Form(50),
        is_active: bool = Form(False),
    ):
        payload = KnowledgeItemPayload(
            item_type=KnowledgeItemType(item_type),
            title=title,
            body=body,
            context_text=context_text,
            answer_text=answer_text,
            marketplace=marketplace,
            product_sku=product_sku,
            product_name=product_name,
            category=category,
            issue_type=issue_type,
            rating_bucket=rating_bucket,
            language=language,
            tags_text=tags_text,
            priority=priority,
            is_active=is_active,
        )
        with session_factory() as session:
            if item_id:
                item = get_item(session, item_id)
                if item:
                    update_item(session, item, payload)
            else:
                create_item(session, payload)
            session.commit()
        return RedirectResponse(
            url="/storage?message=Storage+updated",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/storage/{item_id}/archive", response_class=HTMLResponse)
    async def storage_archive(item_id: int):
        with session_factory() as session:
            item = get_item(session, item_id)
            if item:
                archive_item(session, item)
                session.commit()
        return RedirectResponse(
            url="/storage?message=Item+archived",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/storage/retrieval-test", response_class=HTMLResponse)
    async def storage_retrieval_test(
        request: Request,
        review_text: str = Form(...),
        marketplace: str = Form(""),
        product_sku: str = Form(""),
        product_name: str = Form(""),
        issue_type: str = Form(""),
        language: str = Form("ru"),
    ):
        with session_factory() as session:
            context = _base_context(request, session, current_page="storage")
            retrieval_result = retrieve_context(
                session,
                RetrievalRequest(
                    review_text=review_text,
                    marketplace=marketplace,
                    product_sku=product_sku,
                    product_name=product_name,
                    issue_types=[issue_type] if issue_type else [],
                    language=language,
                ),
            )
            context.update(
                {
                    "items": list_items(session),
                    "edit_item": None,
                    "selected_item_type": "",
                    "search": "",
                    "item_types": list(KnowledgeItemType),
                    "retrieval_result": retrieval_result,
                    "retrieval_preview": {
                        "review_text": review_text,
                        "marketplace": marketplace,
                        "product_sku": product_sku,
                        "product_name": product_name,
                        "issue_type": issue_type,
                        "language": language,
                    },
                }
            )
            return templates.TemplateResponse(request, "storage.html", context)

    @app.get("/history", response_class=HTMLResponse)
    async def history(request: Request):
        with session_factory() as session:
            runs = list(
                session.scalars(
                    select(GenerationRun).order_by(GenerationRun.created_at.desc()).limit(50)
                )
            )
            selected_run = None
            run_id = request.query_params.get("run_id")
            if run_id and run_id.isdigit():
                selected_run = session.get(GenerationRun, int(run_id))
            context = _base_context(request, session, current_page="history")
            context.update({"runs": runs, "selected_run": selected_run})
            return templates.TemplateResponse(request, "history.html", context)

    @app.post("/history/{run_id}/promote", response_class=HTMLResponse)
    async def history_promote(run_id: int, title: str = Form(...), notes: str = Form("")):
        with session_factory() as session:
            run = session.get(GenerationRun, run_id)
            if run:
                promote_run_to_example(session, run, title=title, notes=notes)
                session.commit()
        return RedirectResponse(
            url=f"/history?run_id={run_id}&message=Saved+to+storage",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings(request: Request):
        with session_factory() as session:
            workspace = get_workspace(session)
            prompt = get_active_prompt_version(session)
            prompt_versions = list(
                session.scalars(
                    select(PromptVersion).order_by(PromptVersion.created_at.desc()).limit(12)
                )
            )
            context = _base_context(request, session, current_page="settings")
            context.update(
                {
                    "workspace": workspace,
                    "prompt": prompt,
                    "prompt_versions": prompt_versions,
                    "masked_openai_key": mask_secret(
                        secret_box.decrypt(workspace.openai_api_key_encrypted)
                        if workspace
                        else None
                    ),
                }
            )
            return templates.TemplateResponse(request, "settings.html", context)

    @app.post("/settings/workspace", response_class=HTMLResponse)
    async def settings_workspace_update(
        project_name: str = Form(...),
        brand_name: str = Form(...),
        brand_description: str = Form(""),
        tone_of_voice: str = Form(""),
        brand_promises: str = Form(""),
        support_signature: str = Form(""),
        public_contact_hint: str = Form(""),
        return_policy_summary: str = Form(""),
        compensation_policy: str = Form(""),
        do_not_say: str = Form(""),
        default_language: str = Form("ru"),
        auto_publish_threshold: float = Form(0.9),
        openai_api_key: str = Form(""),
        openai_model: str = Form("gpt-5.4"),
        openai_base_url: str = Form("https://api.openai.com/v1"),
        reasoning_effort: str = Form("low"),
        text_verbosity: str = Form("medium"),
    ):
        with session_factory() as session:
            workspace = get_workspace(session)
            if workspace is None:
                return RedirectResponse(url="/welcome", status_code=status.HTTP_303_SEE_OTHER)

            api_key = openai_api_key.strip() or (
                secret_box.decrypt(workspace.openai_api_key_encrypted) or ""
            )
            payload = WorkspaceSetupPayload(
                project_name=project_name,
                brand_name=brand_name,
                brand_description=brand_description,
                tone_of_voice=tone_of_voice,
                brand_promises=brand_promises,
                support_signature=support_signature,
                public_contact_hint=public_contact_hint,
                return_policy_summary=return_policy_summary,
                compensation_policy=compensation_policy,
                do_not_say=do_not_say,
                default_language=default_language,
                auto_publish_threshold=auto_publish_threshold,
                openai_api_key=api_key,
                openai_model=openai_model,
                openai_base_url=openai_base_url,
                reasoning_effort=reasoning_effort,
                text_verbosity=text_verbosity,
            )
            save_workspace_setup(session, secret_box, payload)
            session.commit()
        return RedirectResponse(
            url="/settings?message=Workspace+settings+saved",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/settings/prompts", response_class=HTMLResponse)
    async def settings_prompts_update(
        prompt_name: str = Form("Custom prompt set"),
        system_prompt: str = Form(...),
        classifier_prompt: str = Form(...),
        generator_prompt: str = Form(...),
    ):
        with session_factory() as session:
            update_prompts(
                session,
                name=prompt_name,
                system_prompt=system_prompt,
                classifier_prompt=classifier_prompt,
                generator_prompt=generator_prompt,
            )
            session.commit()
        return RedirectResponse(
            url="/settings?message=Prompts+published",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return app


def _base_context(request: Request, session: Session, *, current_page: str) -> dict[str, Any]:
    workspace = get_workspace(session)
    return {
        "request": request,
        "app_name": request.app.state.config.app_name,
        "workspace": workspace,
        "current_page": current_page,
        "message": request.query_params.get("message", ""),
        "error": request.query_params.get("error", ""),
        "setup_complete": bool(workspace and workspace.setup_completed_at),
    }


def _serialize_run(run: GenerationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "marketplace": run.marketplace,
        "product_sku": run.product_sku,
        "product_name": run.product_name,
        "rating": run.rating,
        "review_text": run.review_text,
        "classification_result": run.classification_result,
        "reply_result": run.reply_result,
        "decision": run.decision.value if run.decision else None,
        "risk_level": run.risk_level,
        "confidence_score": run.confidence_score,
        "reason_codes": run.reason_codes,
        "retrieved_item_ids": run.retrieved_item_ids,
        "status": run.status.value,
        "error_text": run.error_text,
    }


def _client_for_workspace(
    session: Session, config: AppConfig, default_client: OpenAIResponsesClient
) -> OpenAIResponsesClient:
    workspace = get_workspace(session)
    if workspace and workspace.openai_base_url:
        return OpenAIResponsesClient(
            base_url=workspace.openai_base_url,
            timeout_seconds=config.openai_timeout_seconds,
        )
    return default_client


def _quote_query(value: str) -> str:
    return urlencode({"error": value}).split("=", 1)[1]
