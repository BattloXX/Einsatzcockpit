"""Mail-Versand je Organisation: /admin/mail – Org-Admin konfiguriert eigenen
SMTP-Server und/oder Office 365 / Microsoft Graph.

Muster: ui_lis.py (Config-Tabelle 1:1 je Org, Fernet-verschlüsseltes Secret,
"secret_changed=1"-Idiom, Verbindungstest). Zwei Config-Tabellen auf einer
gemeinsamen Seite, weil die Fallback-Beziehung zwischen ihnen (O365 zuerst,
dann eigener SMTP, dann globaler SMTP — siehe mail_service.py::deliver())
für den Admin sichtbar sein soll.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func

from app.config import settings
from app.core.audit import write_audit
from app.core.crypto import encrypt_secret
from app.core.permissions import is_system_admin, require_role, same_org_or_system_admin
from app.core.templating import templates
from app.db import get_db
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.org_mail import OrgMailEingang, OrgO365MailConfig, OrgResendConfig, OrgSmtpConfig
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


def _get_or_create_resend_config(db, org_id: int) -> OrgResendConfig:
    cfg = db.query(OrgResendConfig).filter(OrgResendConfig.org_id == org_id).first()
    if not cfg:
        cfg = OrgResendConfig(
            org_id=org_id, enabled=False,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        db.add(cfg)
        db.flush()
    return cfg


def _get_or_create_org_settings(db, org_id: int) -> OrgSettings:
    row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if not row:
        row = OrgSettings(org_id=org_id)
        db.add(row)
        db.flush()
    return row


def _gueltige_absenderliste(raw: str) -> str | None:
    """Bereinigt die Freigabeliste oder liefert None bei unsicherer Eingabe."""
    from app.services.mail_service import _looks_like_email
    from app.services.org_mail_adresse import ist_gueltige_domain

    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return None
    lines = [line.strip().lower() for line in raw.splitlines() if line.strip()]
    if len(lines) > 200:
        return None
    if any(not (_looks_like_email(line) or (line.startswith("@") and ist_gueltige_domain(line[1:]))) for line in lines):
        return None
    return "\n".join(lines)


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
    resend_config = (
        db.query(OrgResendConfig).filter(OrgResendConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    org_settings = (
        db.query(OrgSettings).filter(OrgSettings.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    from app.services.org_mail_adresse import empfangs_adresse, inbound_domain

    empfangs_domain = inbound_domain(db)

    return templates.TemplateResponse(request, "admin/settings_org_mail.html", {
        "user": user,
        "org": org,
        "smtp_config": smtp_config,
        "o365_config": o365_config,
        "resend_config": resend_config,
        "org_settings": org_settings,
        "empfangs_domain": empfangs_domain,
        "empfangs_adresse": empfangs_adresse(org, empfangs_domain) if org else "",
        "is_sysadmin": is_sysadmin,
        "all_orgs": all_orgs,
        "flash": request.query_params.get("flash"),
        "o365_globally_enabled": settings.O365_MAIL_ENABLED,
        "system_inbound_configured": bool(db.get(SystemSettings, "resend_inbound_webhook_secret")),
    })


@router.post("/mail/eingang/einstellungen/save")
async def mail_eingang_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    mail_eingang_modus: str = Form("aus"),
    mail_eingang_rueckblick_min: int = Form(30),
    mail_eingang_mehrere: str = Form("posteingang"),
    mail_eingang_absender: str = Form(""),
    mail_eingang_max_pro_stunde: int = Form(30),
):
    effective_org_id = _get_org_id(user, target_org_id)
    if not effective_org_id:
        return RedirectResponse("/admin/mail?flash=error_no_org", status_code=302)
    if not same_org_or_system_admin(user, effective_org_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung fuer diese Organisation")
    if mail_eingang_modus not in {"aus", "freigegeben", "alle"}:
        return RedirectResponse("/admin/mail?flash=error_mail_eingang", status_code=302)
    if mail_eingang_mehrere not in {"posteingang", "neuester"}:
        return RedirectResponse("/admin/mail?flash=error_mail_eingang", status_code=302)
    if not 5 <= mail_eingang_rueckblick_min <= 240 or not 1 <= mail_eingang_max_pro_stunde <= 500:
        return RedirectResponse("/admin/mail?flash=error_mail_eingang", status_code=302)
    absender = _gueltige_absenderliste(mail_eingang_absender)
    if absender is None:
        return RedirectResponse("/admin/mail?flash=error_mail_eingang", status_code=302)
    row = _get_or_create_org_settings(db, effective_org_id)
    row.mail_eingang_modus = mail_eingang_modus
    row.mail_eingang_rueckblick_min = mail_eingang_rueckblick_min
    row.mail_eingang_mehrere = mail_eingang_mehrere
    row.mail_eingang_absender = absender or None
    row.mail_eingang_max_pro_stunde = mail_eingang_max_pro_stunde
    row.updated_at = datetime.now(UTC)
    write_audit(db, "org_mail.eingang.updated", org_id=effective_org_id, user_id=user.id,
                ip=request.client.host if request.client else None)
    db.commit()
    redirect_url = (f"/admin/mail?org_id={effective_org_id}&flash=saved"
                    if effective_org_id != user.org_id else "/admin/mail?flash=saved")
    return RedirectResponse(redirect_url, status_code=302)


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


@router.post("/mail/resend/save")
async def resend_settings_save(
    request: Request,
    db=Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    target_org_id: int | None = Form(None),
    enabled: str = Form(""),
    api_key: str = Form(""),
    secret_changed: str = Form(""),
    from_addr: str = Form(""),
    inbound_enabled: str = Form(""),
    inbound_webhook_secret: str = Form(""),
    inbound_secret_changed: str = Form(""),
    inbound_retention_days: int = Form(90),
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
    if not 1 <= inbound_retention_days <= 365:
        return RedirectResponse("/admin/mail?flash=error_retention", status_code=302)

    cfg = _get_or_create_resend_config(db, effective_org_id)
    cfg.enabled = enabled == "1"
    cfg.from_addr = from_addr_clean or None
    cfg.inbound_enabled = inbound_enabled == "1"
    cfg.inbound_retention_days = inbound_retention_days
    _get_or_create_org_settings(db, effective_org_id).mail_eingang_aufbewahrung_tage = inbound_retention_days
    cfg.updated_at = datetime.now(UTC)
    if secret_changed == "1" and api_key.strip():
        cfg.api_key_enc = encrypt_secret(api_key.strip())
        write_audit(db, "org_mail.resend.credentials_rotated", org_id=effective_org_id,
                    user_id=user.id, ip=request.client.host if request.client else None)
    if inbound_secret_changed == "1" and inbound_webhook_secret.strip():
        cfg.inbound_webhook_secret_enc = encrypt_secret(inbound_webhook_secret.strip())
    write_audit(db, "org_mail.resend.updated", org_id=effective_org_id, user_id=user.id,
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
    if not cfg:
        return JSONResponse({"ok": False, "message": "Eigener SMTP-Server unvollständig konfiguriert."})
    if not cfg.enabled:
        return JSONResponse({"ok": False, "message": "Eigener SMTP-Server ist für diese Organisation deaktiviert."})
    if not cfg.is_fully_configured:
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
    if not settings.O365_MAIL_ENABLED:
        return JSONResponse({"ok": False, "message": "Office 365 ist global deaktiviert (O365_MAIL_ENABLED=false)."})
    if not cfg:
        return JSONResponse({"ok": False, "message": "Office 365 unvollständig konfiguriert."})
    if not cfg.enabled:
        return JSONResponse({"ok": False, "message": "Office 365 ist für diese Organisation deaktiviert."})
    if not cfg.is_fully_configured:
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


@router.post("/mail/resend/test")
async def resend_test(
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
        db.query(OrgResendConfig).filter(OrgResendConfig.org_id == effective_org_id).first()
        if effective_org_id else None
    )
    if not cfg:
        return JSONResponse({"ok": False, "message": "Resend ist unvollständig konfiguriert."})
    if not cfg.enabled:
        return JSONResponse({"ok": False, "message": "Resend ist für diese Organisation deaktiviert."})
    if not cfg.is_fully_configured:
        return JSONResponse({"ok": False, "message": "Resend ist unvollständig konfiguriert."})
    to = recipient.strip() or cfg.from_addr
    if not to:
        return JSONResponse({"ok": False, "message": "Kein Test-Empfänger angegeben."})

    from app.core.crypto import decrypt_secret
    from app.services.mail_service import _build_message
    from app.services.resend_mail_service import ResendMailError, send_via_resend

    try:
        msg = _build_message(
            to=to, subject="Test-Mail von Einsatzcockpit (Resend)",
            body_txt="Diese Test-Mail bestätigt, dass der Versand über Resend funktioniert.",
        )
        await send_via_resend(msg, decrypt_secret(cfg.api_key_enc), cfg.from_addr)
        write_audit(db, "org_mail.resend.test", org_id=effective_org_id, user_id=user.id,
                    payload={"ok": True}, ip=request.client.host if request.client else None)
        db.commit()
        return JSONResponse({"ok": True, "message": f"Test-Mail an {to} über Resend versendet."})
    except ResendMailError as exc:
        message = f"Fehler: {exc}"
    except Exception as exc:  # noqa: BLE001
        message = f"Unerwarteter Fehler: {exc}"
    write_audit(db, "org_mail.resend.test", org_id=effective_org_id, user_id=user.id,
                payload={"ok": False, "error": message[:300]},
                ip=request.client.host if request.client else None)
    db.commit()
    return JSONResponse({"ok": False, "message": message})


# ── Resend-Posteingang ───────────────────────────────────────────────────────
def _eingang_org(user: User, requested: int | None) -> int:
    org_id = _get_org_id(user, requested)
    if not org_id or not same_org_or_system_admin(user, org_id):
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return org_id


def _eingang_row(db, org_id: int, entry_id: int) -> OrgMailEingang:
    row = db.query(OrgMailEingang).filter(OrgMailEingang.id == entry_id, OrgMailEingang.org_id == org_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return row


@router.get("/mail/eingang", response_class=HTMLResponse)
def eingang_list(request: Request, status: str = "", q: str = "", page: int = 1, org_id: int | None = None,
                 db=Depends(get_db), user: User = Depends(require_role("org_admin", "admin"))):
    oid = _eingang_org(user, org_id)
    query = db.query(OrgMailEingang).filter(OrgMailEingang.org_id == oid)
    if status in {"neu", "gelesen", "archiviert", "abruf_fehler", "posteingang", "wartend", "zugeordnet"}:
        query = query.filter(OrgMailEingang.status == status)
    if q.strip():
        term = f"%{q.strip()[:100]}%"
        query = query.filter((OrgMailEingang.betreff.like(term)) | (OrgMailEingang.absender.like(term)))
    page = max(1, page)
    per_page = 25
    total = query.with_entities(func.count(OrgMailEingang.id)).scalar() or 0
    entries = query.order_by(OrgMailEingang.empfangen_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    context = {
        "user": user, "entries": entries, "org_id": oid, "status": status, "q": q, "page": page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "offene_count": db.query(func.count(OrgMailEingang.id)).filter(
            OrgMailEingang.org_id == oid, OrgMailEingang.status.in_(("neu", "posteingang", "wartend"))
        ).scalar() or 0,
    }
    template = (
        "admin/_mail_eingang_content.html"
        if request.headers.get("HX-Request") == "true"
        else "admin/mail_eingang.html"
    )
    return templates.TemplateResponse(request, template, context)


@router.get("/mail/eingang/{entry_id:int}", response_class=HTMLResponse)
def eingang_detail(request: Request, entry_id: int, org_id: int | None = None, db=Depends(get_db),
                   user: User = Depends(require_role("org_admin", "admin"))):
    oid = _eingang_org(user, org_id)
    row = _eingang_row(db, oid, entry_id)
    if row.status == "neu":
        row.status, row.gelesen_von, row.gelesen_at = "gelesen", user.id, datetime.now(UTC)
        db.commit()
    return templates.TemplateResponse(request, "admin/mail_eingang_detail.html", {
        "user": user, "entry": row, "org_id": oid,
        "attachments": _entry_attachments(row),
        "active_incidents": (db.query(Incident).filter(Incident.primary_org_id == oid, Incident.status == "active",
            Incident.is_exercise == False).order_by(Incident.started_at.desc()).all()),  # noqa: E712
    })


def _entry_attachments(row: OrgMailEingang) -> list[dict]:
    try:
        value = json.loads(row.anhang_json or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("id")]


@router.get("/mail/eingang/{entry_id:int}/html")
def eingang_html(entry_id: int, org_id: int | None = None, db=Depends(get_db),
                 user: User = Depends(require_role("org_admin", "admin"))):
    from fastapi.responses import HTMLResponse
    row = _eingang_row(db, _eingang_org(user, org_id), entry_id)
    return HTMLResponse(row.html_body or "", headers={
        "Content-Security-Policy": "default-src 'none'; img-src data:", "Cache-Control": "no-store",
    })


@router.get("/mail/eingang/{entry_id:int}/anhang/{attachment_id}")
async def eingang_anhang(entry_id: int, attachment_id: str, org_id: int | None = None, db=Depends(get_db),
                         user: User = Depends(require_role("org_admin", "admin"))):
    """Proxy-Download: die signierte Provider-URL wird nie an den Browser gegeben."""
    oid = _eingang_org(user, org_id)
    row = _eingang_row(db, oid, entry_id)
    try:
        from app.services.mail_eingang_anhang import lade_anhang

        attachment = await lade_anhang(db, row, attachment_id)
    except Exception:
        attachment = None
    if not attachment:
        raise HTTPException(status_code=404, detail="Anhang nicht gefunden")
    data, filename, content_type = attachment
    from urllib.parse import quote

    return Response(data, media_type=content_type, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
    })


@router.post("/mail/eingang/{entry_id:int}/{aktion}")
async def eingang_aktion(request: Request, entry_id: int, aktion: str, org_id: int | None = Form(None),
                         incident_id: int | None = Form(None), db=Depends(get_db),
                         user: User = Depends(require_role("org_admin", "admin"))):
    oid = _eingang_org(user, org_id)
    row = _eingang_row(db, oid, entry_id)
    if aktion == "loeschen":
        write_audit(db, "org_mail.inbound.deleted", org_id=oid, user_id=user.id)
        db.delete(row)
    elif aktion == "archivieren":
        row.status = "archiviert"
        from app.services.mail_eingang_service import ereignis
        ereignis(db, row, "archiviert", user_id=user.id)
        write_audit(db, "org_mail.inbound.archived", org_id=oid, user_id=user.id)
    elif aktion == "gelesen":
        row.status, row.gelesen_von, row.gelesen_at = "gelesen", user.id, datetime.now(UTC)
        write_audit(db, "org_mail.inbound.read", org_id=oid, user_id=user.id)
        from app.services.mail_eingang_service import ereignis
        ereignis(db, row, "gelesen", user_id=user.id)
    elif aktion == "ungelesen":
        row.status, row.gelesen_at, row.gelesen_von = "neu", None, None
        write_audit(db, "org_mail.inbound.unread", org_id=oid, user_id=user.id)
        from app.services.mail_eingang_service import ereignis
        ereignis(db, row, "ungelesen", user_id=user.id)
    elif aktion == "erneut-abrufen":
        from app.services.resend_inbound_service import fetch_inbound_message
        write_audit(db, "org_mail.inbound.refetched", org_id=oid, user_id=user.id)
        from app.services.mail_eingang_service import ereignis
        ereignis(db, row, "erneut_abgerufen", user_id=user.id)
        await fetch_inbound_message(db, oid, row.id)
        return RedirectResponse(f"/admin/mail/eingang/{entry_id}", status_code=303)
    elif aktion == "uebernehmen":
        if not incident_id:
            raise HTTPException(status_code=400, detail="Einsatz fehlt")
        from app.services.mail_zuordnung_service import uebernehme_in_einsatz
        if not await uebernehme_in_einsatz(db, row.id, incident_id, user.id):
            raise HTTPException(status_code=400, detail="Mail kann nicht in diesen Einsatz übernommen werden")
        write_audit(db, "org_mail.inbound.taken_over", org_id=oid, user_id=user.id,
                    payload={"eingang_id": row.id, "incident_id": incident_id})
        return RedirectResponse(f"/admin/mail/eingang/{entry_id}", status_code=303)
    elif aktion == "absender-freigeben":
        settings_row = _get_or_create_org_settings(db, oid)
        sender = row.absender.strip().lower()
        if "@" not in sender or any(ord(char) < 32 for char in sender):
            raise HTTPException(status_code=400, detail="Absender ist ungültig")
        current = {
            line.strip().lower()
            for line in (settings_row.mail_eingang_absender or "").splitlines()
            if line.strip()
        }
        current.add(sender)
        settings_row.mail_eingang_absender = "\n".join(sorted(current))
        from app.services.mail_eingang_service import ereignis
        ereignis(db, row, "absender_freigegeben", user_id=user.id)
        write_audit(db, "org_mail.inbound.sender_approved", org_id=oid, user_id=user.id,
                    payload={"eingang_id": row.id, "absender": sender})
    else:
        raise HTTPException(status_code=404)
    db.commit()
    return RedirectResponse("/admin/mail/eingang", status_code=303)
