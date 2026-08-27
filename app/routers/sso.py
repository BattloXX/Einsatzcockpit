"""SSO/Entra-ID-Router: /sso/{slug}/login + /sso/{slug}/callback + /sso/discover."""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import decrypt_secret
from app.core.multi_account import ACCOUNTS_COOKIE, add_account, load_accounts, set_accounts_cookie
from app.core.rate_limit import limiter as _limiter
from app.core.security import sign_session
from app.core.tenant import set_tenant_context
from app.db import get_db
from app.models.master import FireDept
from app.models.sso import OrgSsoConfig
from app.models.user import Role, User, UserRole
from app.routers.auth import _set_session_cookie
from app.services.sso_service import (
    SsoError,
    build_authorize_url,
    exchange_code,
    generate_nonce,
    generate_pkce,
    generate_state,
    get_groups,
    map_groups_to_roles,
    validate_id_token,
)

router = APIRouter()
logger = logging.getLogger("einsatzleiter.sso")

_flow_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="sso-flow")
_FLOW_COOKIE = "sso_flow"

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _safe_next(next_url: str | None) -> str:
    if not next_url:
        return "/"
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


def _unique_username(db: Session, base: str, slug: str) -> str:
    candidate = base[:90].lower().replace("@", "_").replace(" ", "_")
    candidate = re.sub(r"[^a-z0-9._-]", "", candidate) or "sso_user"
    if not db.query(User).filter(User.username == candidate).first():
        return candidate
    for i in range(2, 100):
        alt = f"{candidate[:80]}-{slug}-{i}"
        if not db.query(User).filter(User.username == alt).first():
            return alt
    return f"{candidate[:70]}-{generate_state()[:8]}"


def _set_flow_cookie(response, data: dict) -> None:
    token = _flow_signer.dumps(data)
    response.set_cookie(
        _FLOW_COOKIE, token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.SSO_FLOW_MAX_AGE,
    )


def _read_flow_cookie(request: Request) -> dict | None:
    raw = request.cookies.get(_FLOW_COOKIE)
    if not raw:
        return None
    try:
        return _flow_signer.loads(raw, max_age=settings.SSO_FLOW_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _load_sso_config(db: Session, slug: str) -> tuple[FireDept, OrgSsoConfig] | tuple[None, None]:
    set_tenant_context(db, None)
    org = db.query(FireDept).filter(
        FireDept.slug == slug,
        FireDept.is_active == True,  # noqa: E712
        FireDept.deleted_at.is_(None),
    ).first()
    if not org:
        return None, None
    config = db.query(OrgSsoConfig).filter(OrgSsoConfig.org_id == org.id).first()
    return org, config  # type: ignore[return-value]


# ── GET /sso/{slug}/login ─────────────────────────────────────────────────────

@router.get("/sso/{slug}/login")
@(_limiter.limit(settings.LOGIN_RATELIMIT) if _limiter else lambda f: f)
async def sso_login(
    request: Request, slug: str, next: str | None = None,
    login_hint: str | None = None, db: Session = Depends(get_db),
):
    if not settings.SSO_ENABLED:
        return RedirectResponse("/login?error=sso_disabled", status_code=302)

    org, config = _load_sso_config(db, slug)
    if not org:
        return RedirectResponse("/login?error=sso_unknown_org", status_code=302)
    if not config or not config.enabled or not config.is_fully_configured:
        return RedirectResponse("/login?error=sso_disabled", status_code=302)

    verifier, challenge = generate_pkce()
    state = generate_state()
    nonce = generate_nonce()
    next_safe = _safe_next(next)

    redirect_uri = f"{settings.effective_public_base_url}/sso/{slug}/callback"
    url = build_authorize_url(
        tenant_id=config.tenant_id,  # type: ignore[arg-type]
        client_id=config.client_id,  # type: ignore[arg-type]
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        authority_base=config.authority_base,
        login_hint=login_hint,
    )

    response = RedirectResponse(url, status_code=302)
    _set_flow_cookie(response, {
        "v": verifier, "s": state, "n": nonce, "slug": slug, "next": next_safe,
    })
    return response


# ── GET /sso/{slug}/callback ──────────────────────────────────────────────────

@router.get("/sso/{slug}/callback")
@(_limiter.limit(settings.LOGIN_RATELIMIT) if _limiter else lambda f: f)
async def sso_callback(
    request: Request,
    slug: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else None

    if error:
        logger.warning(
            "SSO callback returned OAuth error: slug=%s error=%s description=%s",
            slug, error, error_description,
        )
        return RedirectResponse("/login?error=sso_failed", status_code=302)

    flow = _read_flow_cookie(request)
    if not flow or flow.get("s") != state or flow.get("slug") != slug:
        logger.warning(
            "SSO callback flow validation failed: slug=%s flow_present=%s state_matches=%s slug_matches=%s",
            slug, bool(flow), bool(flow and flow.get("s") == state),
            bool(flow and flow.get("slug") == slug),
        )
        return RedirectResponse("/login?error=sso_failed", status_code=302)

    org, config = _load_sso_config(db, slug)
    if not org or not config or not config.enabled or not config.is_fully_configured:
        logger.warning("SSO callback configuration unavailable or disabled: slug=%s", slug)
        return RedirectResponse("/login?error=sso_disabled", status_code=302)

    try:
        client_secret = decrypt_secret(config.client_secret_enc)  # type: ignore[arg-type]
    except Exception:
        logger.exception(
            "SSO client secret decryption failed: slug=%s tenant_id=%s",
            slug, config.tenant_id,
        )
        return RedirectResponse("/login?error=sso_failed", status_code=302)

    redirect_uri = f"{settings.effective_public_base_url}/sso/{slug}/callback"

    try:
        token_resp = await exchange_code(
            tenant_id=config.tenant_id,  # type: ignore[arg-type]
            client_id=config.client_id,  # type: ignore[arg-type]
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            code=code or "",
            code_verifier=flow["v"],
            authority_base=config.authority_base,
        )
        claims = await validate_id_token(
            id_token=token_resp.get("id_token", ""),
            tenant_id=config.tenant_id,  # type: ignore[arg-type]
            client_id=config.client_id,  # type: ignore[arg-type]
            nonce=flow["n"],
            authority_base=config.authority_base,
        )
        access_token = token_resp.get("access_token", "")
        groups = await get_groups(claims, access_token)
    except SsoError as exc:
        logger.warning(
            "SSO callback denied: slug=%s tenant_id=%s code=%s message=%s",
            slug, config.tenant_id, exc.code, exc.message,
        )
        write_audit(db, "auth.sso.denied", org_id=org.id, ip=ip,
                    payload={"code": exc.code, "message": exc.message, "slug": slug})
        db.commit()
        return RedirectResponse(f"/login?error={exc.code}", status_code=302)

    # Domain-Policy
    email_raw = claims.get("email") or claims.get("preferred_username") or ""
    email = email_raw.lower()
    if config.allowed_domain_list:
        domain = email.split("@")[-1] if "@" in email else ""
        if domain not in config.allowed_domain_list:
            logger.warning(
                "SSO callback denied: slug=%s tenant_id=%s code=domain_not_allowed domain=%s",
                slug, config.tenant_id, domain,
            )
            write_audit(db, "auth.sso.denied", org_id=org.id, ip=ip,
                        payload={"code": "domain_not_allowed", "domain": domain})
            db.commit()
            return RedirectResponse("/login?error=domain_not_allowed", status_code=302)

    # Rollen-Mapping
    system_admin_role = db.query(Role).filter(Role.code == "system_admin").first()
    role_ids = map_groups_to_roles(
        groups, config,
        system_admin_role_id=system_admin_role.id if system_admin_role else None,
    )
    if role_ids is None:
        logger.warning(
            "SSO callback denied: slug=%s tenant_id=%s code=no_group_denied",
            slug, config.tenant_id,
        )
        write_audit(db, "auth.sso.denied", org_id=org.id, ip=ip,
                    payload={"code": "no_group_denied", "slug": slug})
        db.commit()
        return RedirectResponse("/login?error=no_group_denied", status_code=302)

    # F-04: Org-Aktivitätsprüfung VOR JIT-Provisioning
    if not org.is_active or org.deleted_at:
        logger.warning("SSO callback denied: slug=%s code=org_inactive", slug)
        write_audit(db, "auth.sso.denied", org_id=org.id, ip=ip,
                    payload={"code": "org_inactive"})
        db.commit()
        return RedirectResponse("/login?error=sso_failed", status_code=302)

    # JIT-Provisioning
    # F-11: leerer OID ist ungültig
    oid = claims.get("oid") or claims.get("sub")
    if not oid:
        logger.warning(
            "SSO callback denied: slug=%s tenant_id=%s code=idtoken_invalid reason=missing_oid",
            slug, config.tenant_id,
        )
        write_audit(db, "auth.sso.denied", org_id=org.id, ip=ip,
                    payload={"code": "idtoken_invalid", "reason": "missing_oid"})
        db.commit()
        return RedirectResponse("/login?error=sso_failed", status_code=302)

    tid = claims.get("tid", "")
    display_name = claims.get("name") or email_raw
    full_name = claims.get("name")
    upn = claims.get("preferred_username") or email_raw

    set_tenant_context(db, None)
    user = db.query(User).filter(User.entra_oid == oid).first()
    jit_provisioned = False

    # F-07: E-Mail-Linking nur wenn explizit erlaubt (opt-in, default False)
    if not user and email and getattr(config, "allow_email_linking", False):
        existing = db.query(User).filter(
            User.email == email, User.org_id == org.id,
            User.auth_provider == "local",
        ).first()
        if existing:
            existing.entra_oid = oid
            existing.entra_tid = tid
            existing.auth_provider = "entra"
            user = existing
            write_audit(db, "auth.sso.email_linked", org_id=org.id, user_id=existing.id, ip=ip,
                        payload={"email": email})

    if not user:
        # F-12: slug bereinigen bevor er in Usernamen eingebettet wird
        username = _unique_username(db, upn, re.sub(r"[^a-z0-9-]", "", slug.lower())[:20])
        user = User(
            username=username,
            password_hash=None,
            display_name=display_name,
            full_name=full_name,
            email=email or None,
            org_id=org.id,
            active=True,
            auth_provider="entra",
            entra_oid=oid,
            entra_tid=tid,
        )
        db.add(user)
        db.flush()
        jit_provisioned = True
    else:
        if not user.active:
            logger.warning(
                "SSO callback denied: slug=%s tenant_id=%s code=user_inactive user_id=%s",
                slug, config.tenant_id, user.id,
            )
            write_audit(db, "auth.sso.denied", org_id=org.id, ip=ip,
                        payload={"code": "user_inactive", "user_id": user.id})
            db.commit()
            return RedirectResponse("/login?error=sso_failed", status_code=302)
        if config.sync_profile:
            user.display_name = display_name
            if full_name:
                user.full_name = full_name
            if email:
                user.email = email

    # Rollen nur für neu angelegte JIT-Benutzer setzen – bestehende Benutzer behalten ihre Rechte
    if jit_provisioned:
        old_role_ids = {ur.role_id for ur in user.user_roles}
        if old_role_ids != role_ids:
            old_codes = {ur.role.code for ur in user.user_roles if ur.role}
            for ur in list(user.user_roles):
                db.delete(ur)
            db.flush()
            for rid in role_ids:
                db.add(UserRole(user_id=user.id, role_id=rid))
            new_codes = {
                db.get(Role, rid).code for rid in role_ids  # type: ignore[union-attr]
                if db.get(Role, rid)
            }
            write_audit(db, "auth.sso.role_sync", org_id=org.id, user_id=user.id, ip=ip,
                        payload={"before": sorted(old_codes), "after": sorted(new_codes)})

    user.last_login_at = datetime.now(UTC)
    write_audit(db, "auth.sso.login", org_id=org.id, user_id=user.id, ip=ip,
                payload={"slug": slug})
    if jit_provisioned:
        write_audit(db, "auth.sso.jit_provision", org_id=org.id, user_id=user.id, ip=ip,
                    payload={"username": user.username})
    db.commit()

    logger.info(
        "SSO login successful: slug=%s tenant_id=%s user_id=%s jit_provisioned=%s",
        slug, config.tenant_id, user.id, jit_provisioned,
    )

    session_token = sign_session(user.id)
    redirect = RedirectResponse(_safe_next(flow.get("next")), status_code=302)
    _set_session_cookie(redirect, session_token)
    set_accounts_cookie(
        redirect,
        add_account(load_accounts(request.cookies.get(ACCOUNTS_COOKIE)), user.id, False),
    )
    redirect.delete_cookie(_FLOW_COOKIE, path="/")
    return redirect


# ── GET /sso/discover ─────────────────────────────────────────────────────────

@router.get("/sso/discover")
@(_limiter.limit("20/minute") if _limiter else lambda f: f)  # F-08: Rate-Limit
async def sso_discover(request: Request, email: str, db: Session = Depends(get_db)):
    """Ermittelt die SSO-Login-URL für eine E-Mail-Domain."""
    if not settings.SSO_ENABLED or "@" not in email:
        return JSONResponse({"found": False})

    domain = email.split("@")[-1].lower().strip()
    if not domain:
        return JSONResponse({"found": False})

    set_tenant_context(db, None)
    configs = db.query(OrgSsoConfig).filter(
        OrgSsoConfig.enabled == True  # noqa: E712
    ).all()

    matches = []
    for cfg in configs:
        if domain in cfg.allowed_domain_list:
            org = cfg.org
            if org and org.is_active and not org.deleted_at:  # type: ignore[attr-defined]
                # F-08: kein org.name in Response (verhindert Kundenlisten-Enumeration)  # type: ignore[attr-defined]
                matches.append(f"/sso/{org.slug}/login")  # type: ignore[attr-defined]

    if len(matches) == 1:
        return JSONResponse({"found": True, "redirect": matches[0]})
    if len(matches) > 1:
        # Bei mehreren Treffern: nur Login-URLs, keine Namen
        return JSONResponse({"found": True, "multiple": True,
                             "orgs": [{"login_url": u} for u in matches]})
    return JSONResponse({"found": False})
