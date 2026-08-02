"""Admin: KI-Prompt-Verwaltung mit Versionierung."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import has_role, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.master import AIPromptVersion, AIRequestLog, FireDept
from app.services.ai_service import PROMPT_META

router = APIRouter(prefix="/admin", tags=["admin"])

_MAX_VERSIONS = 10
_VALID_KEYS = frozenset(PROMPT_META.keys())


@router.get("/ki-anfragen", response_class=HTMLResponse)
async def ai_request_log_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin", "org_admin")),
):
    user = request.state.user
    is_system_admin = has_role(user, "system_admin")
    query = db.query(AIRequestLog)
    if not is_system_admin:
        query = query.filter(AIRequestLog.org_id == user.org_id)
    entries = query.order_by(AIRequestLog.created_at.desc()).limit(500).all()
    org_names: dict[int, str] = {}
    if is_system_admin:
        org_ids = {entry.org_id for entry in entries if entry.org_id is not None}
        if org_ids:
            rows = db.query(FireDept.id, FireDept.name).filter(FireDept.id.in_(org_ids)).all()
            org_names = {row[0]: row[1] for row in rows}
    feature_labels = {key: meta["label"] for key, meta in PROMPT_META.items()}
    return templates.TemplateResponse(request, "admin/ai_request_log.html", {
        "user": user,
        "entries": entries,
        "is_system_admin": is_system_admin,
        "org_names": org_names,
        "feature_labels": feature_labels,
    })


def _next_version(db: Session, prompt_key: str, org_id: int) -> int:
    from sqlalchemy import func
    result = db.query(func.max(AIPromptVersion.version)).filter(
        AIPromptVersion.prompt_key == prompt_key,
        AIPromptVersion.org_id == org_id,
    ).scalar()
    return (result or 0) + 1


def _prune_old_versions(db: Session, prompt_key: str, org_id: int) -> None:
    """Keep only the latest _MAX_VERSIONS versions; delete older ones."""
    versions = (
        db.query(AIPromptVersion)
        .filter(AIPromptVersion.prompt_key == prompt_key, AIPromptVersion.org_id == org_id)
        .order_by(AIPromptVersion.version.desc())
        .all()
    )
    for old in versions[_MAX_VERSIONS:]:
        db.delete(old)


@router.get("/ki-prompts", response_class=HTMLResponse)
async def ki_prompts_page(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin", "org_admin")),
):
    user = request.state.user
    saved = request.query_params.get("saved")
    prompts: dict[str, dict] = {}
    for key, meta in PROMPT_META.items():
        versions = (
            db.query(AIPromptVersion)
            .filter(AIPromptVersion.prompt_key == key, AIPromptVersion.org_id == user.org_id)
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
        "user": user,
        "prompts": prompts,
        "saved": saved,
    })


@router.post("/ki-prompts/{prompt_key}")
async def save_prompt_version(
    prompt_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin", "org_admin")),
    variable_part: str = Form(...),
    note: str = Form(""),
):
    if prompt_key not in _VALID_KEYS:
        return RedirectResponse("/admin/ki-prompts?saved=error", status_code=303)

    variable_part = variable_part.strip()
    if not variable_part:
        return RedirectResponse(f"/admin/ki-prompts?saved=empty#{prompt_key}", status_code=303)

    user = request.state.user
    version = _next_version(db, prompt_key, user.org_id)
    db.add(AIPromptVersion(
        org_id=user.org_id,
        prompt_key=prompt_key,
        version=version,
        variable_part=variable_part,
        note=note.strip() or None,
        created_at=datetime.now(UTC),
        created_by_user_id=user.id,
        created_by_username=getattr(user, "username", None),
    ))
    _prune_old_versions(db, prompt_key, user.org_id)
    write_audit(db, f"admin.ai_prompt.saved.{prompt_key}", user_id=user.id)
    db.commit()
    return RedirectResponse(f"/admin/ki-prompts?saved={prompt_key}#{prompt_key}", status_code=303)


@router.post("/ki-prompts/{prompt_key}/restore/{version_id}")
async def restore_prompt_version(
    prompt_key: str,
    version_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_role("system_admin", "org_admin")),
):
    if prompt_key not in _VALID_KEYS:
        return RedirectResponse("/admin/ki-prompts", status_code=303)

    user = request.state.user
    source = db.get(AIPromptVersion, version_id)
    if not source or source.prompt_key != prompt_key or source.org_id != user.org_id:
        return RedirectResponse("/admin/ki-prompts", status_code=303)

    version = _next_version(db, prompt_key, user.org_id)
    db.add(AIPromptVersion(
        org_id=user.org_id,
        prompt_key=prompt_key,
        version=version,
        variable_part=source.variable_part,
        note=f"Wiederhergestellt von v{source.version}",
        created_at=datetime.now(UTC),
        created_by_user_id=user.id,
        created_by_username=getattr(user, "username", None),
    ))
    _prune_old_versions(db, prompt_key, user.org_id)
    write_audit(db, f"admin.ai_prompt.restored.{prompt_key}", user_id=user.id)
    db.commit()
    return RedirectResponse(f"/admin/ki-prompts?saved={prompt_key}#{prompt_key}", status_code=303)
