"""Interaktive, CSRF-geschuetzte MCP-Anmeldeseite."""

from datetime import UTC
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limit import limiter
from app.core.security import hash_api_key
from app.core.templating import templates
from app.db import get_db
from app.models.mcp import MCPOAuthCode
from app.services.auth_service import authenticate_user

router = APIRouter()


def _form_action_origin(redirect_uri: str) -> str:
    """CSP-Quelle fuer das Redirect-Ziel des Clients (form-action gilt auch fuer Redirects)."""
    teile = urlsplit(redirect_uri)
    if teile.scheme in ("http", "https") and teile.netloc:
        return f"{teile.scheme}://{teile.netloc}"
    return f"{teile.scheme}:" if teile.scheme else ""


def _anmeldeseite(request: Request, row: MCPOAuthCode, vorgang: str, error: str | None, status_code: int = 200):
    response = templates.TemplateResponse(
        request, "mcp/anmelden.html", {"vorgang": vorgang, "error": error}, status_code=status_code
    )
    ziel = _form_action_origin(row.redirect_uri)
    if ziel:
        response.headers["x-ec-form-action-extra"] = ziel
    return response


@router.get("/mcp/anmelden", response_class=HTMLResponse)
async def anmelden(request: Request, vorgang: str, db: Session = Depends(get_db)):
    row = db.query(MCPOAuthCode).filter(MCPOAuthCode.code_hash == hash_api_key(vorgang)).first()
    if not row or row.user_id or row.expires_at.replace(tzinfo=UTC) < __import__("datetime").datetime.now(UTC):
        return HTMLResponse("Anmeldevorgang ist abgelaufen.", status_code=400)
    return _anmeldeseite(request, row, vorgang, None)


@router.post("/mcp/anmelden", response_class=HTMLResponse)
@(limiter.limit(settings.MCP_LOGIN_RATELIMIT) if limiter else lambda f: f)
async def anmelden_submit(
    request: Request,
    vorgang: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    row = db.query(MCPOAuthCode).filter(MCPOAuthCode.code_hash == hash_api_key(vorgang)).first()
    if not row or row.user_id:
        return HTMLResponse("Anmeldevorgang ist abgelaufen.", status_code=400)
    user, error = authenticate_user(db, username, password, request.client.host if request.client else None)
    if not user or not user.org_id or user.is_device:
        return _anmeldeseite(request, row, vorgang, error or "Dieser Zugang darf MCP nicht verwenden.", 401)
    from app.mcp.context import mcp_effective_enabled

    if not mcp_effective_enabled(user.org_id, db):
        return _anmeldeseite(request, row, vorgang, "MCP ist für diese Organisation nicht aktiviert.", 403)
    from app.mcp.server import _raw

    code = _raw()
    row.code_hash, row.user_id, row.org_id = hash_api_key(code), user.id, user.org_id
    db.commit()
    query = [("code", code)]
    if row.state:
        query.append(("state", row.state))
    parsed = urlsplit(row.redirect_uri)
    return RedirectResponse(
        urlunsplit(parsed._replace(query=urlencode(list(parse_qsl(parsed.query)) + query))), status_code=302
    )
