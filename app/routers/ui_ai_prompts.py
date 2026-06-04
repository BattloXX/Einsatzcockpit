"""Admin: KI-Prompt-Verwaltung mit Versionierung."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.master import AIPromptVersion
from app.services.ai_service import PROMPT_META

router = APIRouter(prefix="/admin", tags=["admin"])

_MAX_VERSIONS = 10
_VALID_KEYS = frozenset(PROMPT_META.keys())


def _next_version(db: Session, prompt_key: str) -> int:
    from sqlalchemy import func
    result = db.query(func.max(AIPromptVersion.version)).filter(
        AIPromptVersion.prompt_key == prompt_key
    ).scalar()
    return (result or 0) + 1


def _prune_old_versions(db: Session, prompt_key: str) -> None:
    """Keep only the latest _MAX_VERSIONS versions; delete older ones."""
    versions = (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.prompt_key == prompt_key)
        .order_by(AIPromptVersion.version.desc())
        .all()
    )
    for old in versions[_MAX_VERSIONS:]:
        db.delete(old)


@router.get("/ki-prompts", response_class=HTMLResponse)
async def ki_prompts_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
):
    saved = request.query_params.get("saved")
    prompts: dict[str, dict] = {}
    for key, meta in PROMPT_META.items():
        versions = (
            db.query(AIPromptVersion)
            .filter(AIPromptVersion.prompt_key == key)
            .order_by(AIPromptVersion.version.desc())
            .all()
        )
        current_variable = versions[0].variable_part if versions else meta["variable_default"]
        prompts[key] = {
            **meta,
            "current_variable": current_variable,
            "versions": versions,
        }
    return templates.TemplateResponse(request, "admin/ai_prompts.html", {
        "user": request.state.user,
        "prompts": prompts,
        "saved": saved,
    })


@router.post("/ki-prompts/{prompt_key}")
async def save_prompt_version(
    prompt_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
    variable_part: str = Form(...),
    note: str = Form(""),
):
    if prompt_key not in _VALID_KEYS:
        return RedirectResponse("/admin/ki-prompts?saved=error", status_code=303)

    variable_part = variable_part.strip()
    if not variable_part:
        return RedirectResponse(f"/admin/ki-prompts?saved=empty#{prompt_key}", status_code=303)

    user = request.state.user
    version = _next_version(db, prompt_key)
    db.add(AIPromptVersion(
        prompt_key=prompt_key,
        version=version,
        variable_part=variable_part,
        note=note.strip() or None,
        created_at=datetime.now(UTC),
        created_by_user_id=user.id,
        created_by_username=getattr(user, "username", None),
    ))
    _prune_old_versions(db, prompt_key)
    write_audit(db, f"admin.ai_prompt.saved.{prompt_key}", user_id=user.id)
    db.commit()
    return RedirectResponse(f"/admin/ki-prompts?saved={prompt_key}#{prompt_key}", status_code=303)


@router.post("/ki-prompts/{prompt_key}/restore/{version_id}")
async def restore_prompt_version(
    prompt_key: str,
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin")),
):
    if prompt_key not in _VALID_KEYS:
        return RedirectResponse("/admin/ki-prompts", status_code=303)

    source = db.get(AIPromptVersion, version_id)
    if not source or source.prompt_key != prompt_key:
        return RedirectResponse("/admin/ki-prompts", status_code=303)

    user = request.state.user
    version = _next_version(db, prompt_key)
    db.add(AIPromptVersion(
        prompt_key=prompt_key,
        version=version,
        variable_part=source.variable_part,
        note=f"Wiederhergestellt von v{source.version}",
        created_at=datetime.now(UTC),
        created_by_user_id=user.id,
        created_by_username=getattr(user, "username", None),
    ))
    _prune_old_versions(db, prompt_key)
    write_audit(db, f"admin.ai_prompt.restored.{prompt_key}", user_id=user.id)
    db.commit()
    return RedirectResponse(f"/admin/ki-prompts?saved={prompt_key}#{prompt_key}", status_code=303)
