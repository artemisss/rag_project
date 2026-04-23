from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.security import SecretBox
from app.db.models import PromptVersion, WorkspaceSettings
from app.services.audit import log_event
from app.services.defaults import (
    DEFAULT_CLASSIFIER_PROMPT,
    DEFAULT_GENERATOR_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
)


@dataclass
class WorkspaceSetupPayload:
    project_name: str
    brand_name: str
    brand_description: str
    tone_of_voice: str
    brand_promises: str
    support_signature: str
    public_contact_hint: str
    return_policy_summary: str
    compensation_policy: str
    do_not_say: str
    default_language: str
    auto_publish_threshold: float
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    reasoning_effort: str
    text_verbosity: str


def get_workspace(session: Session) -> Optional[WorkspaceSettings]:
    return session.scalar(select(WorkspaceSettings).limit(1))


def is_setup_complete(session: Session) -> bool:
    workspace = get_workspace(session)
    return bool(workspace and workspace.setup_completed_at)


def ensure_default_prompt_version(session: Session) -> PromptVersion:
    active_prompt = session.scalar(
        select(PromptVersion).where(PromptVersion.is_active.is_(True)).limit(1)
    )
    if active_prompt:
        return active_prompt

    prompt = PromptVersion(
        name="Базовый набор промптов",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        classifier_prompt=DEFAULT_CLASSIFIER_PROMPT,
        generator_prompt=DEFAULT_GENERATOR_PROMPT,
        is_active=True,
    )
    session.add(prompt)
    session.flush()
    return prompt


def save_workspace_setup(
    session: Session, secret_box: SecretBox, payload: WorkspaceSetupPayload
) -> WorkspaceSettings:
    workspace = get_workspace(session)
    encrypted_key = secret_box.encrypt(payload.openai_api_key.strip())

    if workspace is None:
        workspace = WorkspaceSettings(
            project_name=payload.project_name.strip(),
            brand_name=payload.brand_name.strip(),
            brand_description=payload.brand_description.strip(),
            tone_of_voice=payload.tone_of_voice.strip(),
            brand_promises=payload.brand_promises.strip(),
            support_signature=payload.support_signature.strip(),
            public_contact_hint=payload.public_contact_hint.strip(),
            return_policy_summary=payload.return_policy_summary.strip(),
            compensation_policy=payload.compensation_policy.strip(),
            do_not_say=payload.do_not_say.strip(),
            default_language=payload.default_language.strip() or "ru",
            auto_publish_threshold=payload.auto_publish_threshold,
            openai_api_key_encrypted=encrypted_key,
            openai_model=payload.openai_model.strip() or "gpt-5.4",
            openai_base_url=payload.openai_base_url.strip()
            or "https://api.openai.com/v1",
            reasoning_effort=payload.reasoning_effort.strip() or "low",
            text_verbosity=payload.text_verbosity.strip() or "medium",
        )
        session.add(workspace)
    else:
        workspace.project_name = payload.project_name.strip()
        workspace.brand_name = payload.brand_name.strip()
        workspace.brand_description = payload.brand_description.strip()
        workspace.tone_of_voice = payload.tone_of_voice.strip()
        workspace.brand_promises = payload.brand_promises.strip()
        workspace.support_signature = payload.support_signature.strip()
        workspace.public_contact_hint = payload.public_contact_hint.strip()
        workspace.return_policy_summary = payload.return_policy_summary.strip()
        workspace.compensation_policy = payload.compensation_policy.strip()
        workspace.do_not_say = payload.do_not_say.strip()
        workspace.default_language = payload.default_language.strip() or "ru"
        workspace.auto_publish_threshold = payload.auto_publish_threshold
        workspace.openai_api_key_encrypted = encrypted_key
        workspace.openai_model = payload.openai_model.strip() or "gpt-5.4"
        workspace.openai_base_url = (
            payload.openai_base_url.strip() or "https://api.openai.com/v1"
        )
        workspace.reasoning_effort = payload.reasoning_effort.strip() or "low"
        workspace.text_verbosity = payload.text_verbosity.strip() or "medium"

    if workspace.setup_completed_at is None:
        from app.db.base import utc_now

        workspace.setup_completed_at = utc_now()

    ensure_default_prompt_version(session)
    log_event(
        session,
        entity_type="workspace",
        entity_id="1",
        action="setup_completed",
        payload={"brand_name": workspace.brand_name, "model": workspace.openai_model},
    )
    session.flush()
    return workspace


def get_active_prompt_version(session: Session) -> PromptVersion:
    prompt = session.scalar(
        select(PromptVersion).where(PromptVersion.is_active.is_(True)).limit(1)
    )
    if prompt:
        return prompt
    return ensure_default_prompt_version(session)


def update_prompts(
    session: Session,
    *,
    name: str,
    system_prompt: str,
    classifier_prompt: str,
    generator_prompt: str,
) -> PromptVersion:
    session.execute(update(PromptVersion).values(is_active=False))
    prompt = PromptVersion(
        name=name.strip() or "Пользовательский набор промптов",
        system_prompt=system_prompt.strip(),
        classifier_prompt=classifier_prompt.strip(),
        generator_prompt=generator_prompt.strip(),
        is_active=True,
    )
    session.add(prompt)
    session.flush()
    log_event(
        session,
        entity_type="prompt_version",
        entity_id=str(prompt.id),
        action="prompt_published",
        payload={"name": prompt.name},
    )
    return prompt


def get_openai_api_key(
    workspace: Optional[WorkspaceSettings], secret_box: SecretBox
) -> Optional[str]:
    if workspace is None:
        return None
    return secret_box.decrypt(workspace.openai_api_key_encrypted)
