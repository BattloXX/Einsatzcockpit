"""Mail-Versand je Organisation: /admin/mail – Org-Admin konfiguriert eigenen
SMTP-Server und/oder Office 365 / Microsoft Graph.

Muster: ui_lis.py (Config-Tabelle 1:1 je Org, Fernet-verschlüsseltes Secret,
"secret_changed=1"-Idiom, Verbindungstest). Zwei Config-Tabellen auf einer
gemeinsamen Seite, weil die Fallback-Beziehung zwischen ihnen (O365 zuerst,
dann eigener SMTP, dann globaler SMTP — siehe mail_service.py::deliver())
für den Admin sichtbar sein soll.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import encrypt_secret
from app.core.permissions import is_system_admin, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept
from app.models.org_mail import OrgO365MailConfig, OrgSmtpConfig
from app.models.user import User

router = APIRouter(prefix="/admin")


def _get_org_id(user: User, target_org_id: int | None = None) -> int | None:
    if is_system_admin(user) and target_org_id:
        return target_org_id
    return user.org_id


def _get_or_create_smtp_config(db, org_id: int) -> OrgSmtpConfig:
    cfg = db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == org_id).first()
    if not cfg:
        cfg = OrgSmtpConfig(
            org_id=org_id, enabled=False, port=587, starttls=True, timeout=15,
            imap_enabled=False, imap_port=993, imap_use_ssl=True,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


def _get_or_create_o365_config(db, org_id: int) -> OrgO365MailConfig:
    cfg = db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
    if not cfg:
        cfg = OrgO365MailConfig(
            org_id=org_id, enabled=False, read_enabled=False,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


# ── GET /admin/mail ───────────────────────────────────────────────────────────
@router.get("/mail", response_class=HTMLResponse)
def mail_settings_page(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    org_id: int | None = None,
):
    is_sysadmin = is_system_admin(user)
    effective_org_id = _get_org_id(user, org_id)
    all_orgs = db.query(FireDept).order_by(FireDept.name).all() if is_sysadmin else []

    org = db.query(FireDept).filter(FireDept.id == effective_org_id).first() if effective_org_id else None
    smtp_config = (
        db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    o365_config = (
        db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )

    return templates.TemplateResponse(request, "admin/settings_org_mail.html", {
        "user": user,
        "org": org,
        "smtp_config": smtp_config,
        "o365_config": o365_config,
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "flash": request.query_params.get("flash"),
        "o365_globally_enabled": settings.O365_MAIL_ENABLED,
    })


# ── POST /admin/mail/smtp/save ────────────────────────────────────────────────
@router.post("/mail/smtp/save")
async def smtp_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    host: str = Form(""),
    port: int = Form(587),
    smtp_user: str = Form(""),
    password: str = Form(""),
    secret_changed: str = Form(""),
    from_addr: str = Form(""),
    starttls: str = Form(""),
    timeout: int = Form(15),
    imap_enabled: str = Form(""),
    imap_host: str = Form(""),
    imap_port: int = Form(993),
    imap_use_ssl: str = Form(""),
):
    from app.services.mail_service import _looks_like_email

    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/mail?flash=error_no_org", status_code=302)
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Organisation")

    from_addr_clean = from_addr.strip()
    if from_addr_clean and not _looks_like_email(from_addr_clean):
        return RedirectResponse("/admin/mail?flash=error_from_addr", status_code=302)

    cfg = _get_or_create_smtp_config(db, effective_org_id)
    cfg.enabled = enabled == "1"
    cfg.host = host.strip() or None
    cfg.port = max(1, min(port or 587, 65535))
    cfg.user = smtp_user.strip() or None
    cfg.from_addr = from_addr_clean or None
    cfg.starttls = starttls == "1"
    cfg.timeout = max(1, min(timeout or 15, 120))
    cfg.imap_enabled = imap_enabled == "1"
    cfg.imap_host = imap_host.strip() or None
    cfg.imap_port = max(1, min(imap_port or 993, 65535))
    cfg.imap_use_ssl = imap_use_ssl == "1"
    cfg.updated_at = datetime.now(UTC)

    if secret_changed == "1":
        raw_password = password.strip()
        if raw_password:
            cfg.password_enc = encrypt_secret(raw_password)
            write_audit(db, "org_mail.smtp.credentials_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)

    write_audit(db, "org_mail.smtp.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    redirect_url = (
        f"/admin/mail?org_id={effective_org_id}&flash=saved"
        if effective_org_id != user.org_id else "/admin/mail?flash=saved"
    )
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/mail/o365/save ────────────────────────────────────────────────
@router.post("/mail/o365/save")
async def o365_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    tenant_id: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    secret_changed: str = Form(""),
    sender_address: str = Form(""),
    read_enabled: str = Form(""),
):
    from app.services.mail_service import _looks_like_email

    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/mail?flash=error_no_org", status_code=302)
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Organisation")

    sender_clean = sender_address.strip()
    if sender_clean and not _looks_like_email(sender_clean):
        return RedirectResponse("/admin/mail?flash=error_sender_address", status_code=302)

    cfg = _get_or_create_o365_config(db, effective_org_id)
    cfg.enabled = enabled == "1"
    cfg.tenant_id = tenant_id.strip() or None
    cfg.client_id = client_id.strip() or None
    cfg.sender_address = sender_clean or None
    cfg.read_enabled = read_enabled == "1"
    cfg.updated_at = datetime.now(UTC)

    if secret_changed == "1":
        raw_secret = client_secret.strip()
        if raw_secret:
            cfg.client_secret_enc = encrypt_secret(raw_secret)
            write_audit(db, "org_mail.o365.credentials_rotated", org_id=effective_org_id,
                        user_id=user.id, ip=request.client.host if request.client else None)

    write_audit(db, "org_mail.o365.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()

    redirect_url = (
        f"/admin/mail?org_id={effective_org_id}&flash=saved"
        if effective_org_id != user.org_id else "/admin/mail?flash=saved"
    )
    return RedirectResponse(redirect_url, status_code=302)


# ── POST /admin/mail/smtp/test ────────────────────────────────────────────────
@router.post("/mail/smtp/test")
async def smtp_test(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    recipient: str = Form(""),
):
    effective_org_id = _get_org_id(user, target_org_id)
    if effective_org_id and not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Organisation")
    cfg = (
        db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    if not cfg or not cfg.is_fully_configured:
        return JSONResponse({"ok": False, "message": "Eigener SMTP-Server unvollständig konfiguriert."})
    to = recipient.strip() or cfg.from_addr
    if not to:
        return JSONResponse({"ok": False, "message": "Kein Test-Empfänger angegeben."})

    from app.services.mail_service import _build_message, _org_smtp_cfg, _send

    try:
        smtp_cfg = _org_smtp_cfg(db, effective_org_id)
        if not smtp_cfg:
            return JSONResponse({"ok": False, "message": "Eigener SMTP-Server unvollständig konfiguriert."})
        msg = _build_message(
            to=to, subject="Test-Mail von Einsatzcockpit (eigener SMTP-Server)",
            body_txt=f"Diese Test-Mail bestätigt, dass der eigene SMTP-Server der Organisation "
                     f"funktioniert.\n\nHost: {smtp_cfg['host']}\nPort: {smtp_cfg['port']}\n",
            smtp_cfg=smtp_cfg,
        )
        await _send(msg, smtp_cfg)
        write_audit(db, "org_mail.smtp.test", org_id=effective_org_id, user_id=user.id,
                    payload={"ok": True}, ip=request.client.host if request.client else None)
        db.commit()
        return JSONResponse({"ok": True, "message": f"Test-Mail an {to} versendet."})
    except Exception as exc:  # noqa: BLE001 – Testergebnis soll nie 500 werfen
        write_audit(db, "org_mail.smtp.test", org_id=effective_org_id, user_id=user.id,
                    payload={"ok": False, "error": str(exc)[:300]},
                    ip=request.client.host if request.client else None)
        db.commit()
        return JSONResponse({"ok": False, "message": f"Fehler: {exc}"})


# ── POST /admin/mail/o365/test ────────────────────────────────────────────────
@router.post("/mail/o365/test")
async def o365_test(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    recipient: str = Form(""),
):
    effective_org_id = _get_org_id(user, target_org_id)
    if effective_org_id and not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Organisation")
    cfg = (
        db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    if not cfg or not cfg.is_fully_configured:
        return JSONResponse({"ok": False, "message": "Office 365 unvollständig konfiguriert."})
    to = recipient.strip() or cfg.sender_address
    if not to:
        return JSONResponse({"ok": False, "message": "Kein Test-Empfänger angegeben."})

    from app.services.mail_service import _build_message
    from app.services.o365_mail_service import O365MailError, send_via_graph

    try:
        msg = _build_message(
            to=to, subject="Test-Mail von Einsatzcockpit (Office 365 / Microsoft Graph)",
            body_txt=f"Diese Test-Mail bestätigt, dass der Versand über Microsoft Graph für diese "
                     f"Organisation funktioniert.\n\nAbsender: {cfg.sender_address}\n",
        )
        await send_via_graph(msg, cfg)
        write_audit(db, "org_mail.o365.test", org_id=effective_org_id, user_id=user.id,
                    payload={"ok": True}, ip=request.client.host if request.client else None)
        db.commit()
        return JSONResponse({"ok": True, "message": f"Test-Mail an {to} über Microsoft Graph versendet."})
    except O365MailError as exc:
        write_audit(db, "org_mail.o365.test", org_id=effective_org_id, user_id=user.id,
                    payload={"ok": False, "error": str(exc)[:300]},
                    ip=request.client.host if request.client else None)
        db.commit()
        return JSONResponse({"ok": False, "message": f"Fehler: {exc}"})
    except Exception as exc:  # noqa: BLE001 – Testergebnis soll nie 500 werfen
        write_audit(db, "org_mail.o365.test", org_id=effective_org_id, user_id=user.id,
                    payload={"ok": False, "error": str(exc)[:300]},
                    ip=request.client.host if request.client else None)
        db.commit()
        return JSONResponse({"ok": False, "message": f"Unerwarteter Fehler: {exc}"})
