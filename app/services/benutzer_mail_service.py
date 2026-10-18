"""Gebrandete Benutzer-Mails mit HTML- und Text-Alternative."""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.models.master import FireDept, OrgSettings
from app.services.einsatzinfo_mail_service import _absolute_url

_FARBE = re.compile(r"#[0-9a-fA-F]{6}")
_templates = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "templates")),
    autoescape=select_autoescape(enabled_extensions=("html",), default=False),
)


def mail_kontext(db, org_id: int | None) -> dict:
    """Liefert den gemeinsamen, request-unabhaengigen Mail-Kontext einer Org."""
    org = db.query(FireDept).filter(FireDept.id == org_id).first() if db is not None and org_id else None
    org_settings = (
        db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
        if db is not None and org is not None else None
    )
    logo_path = (getattr(org_settings, "logo_path", None) or getattr(org, "logo_path", None)
                 or "/static/img/Logo-rot.png")
    color = getattr(org_settings, "primary_color", None) or getattr(org, "color", None)
    app_url = settings.effective_public_base_url.rstrip("/")
    return {
        "org_name": getattr(org, "name", None) or "Einsatzcockpit",
        "logo_url": _absolute_url(logo_path),
        "brand_color": color if _FARBE.fullmatch(color or "") else "#c8102e",
        "app_url": app_url,
        "android_app_url": f"{app_url}/app/android",
        "iphone_app_url": f"{app_url}/app",
        "is_test": settings.TEST_SYSTEM,
        "hilfe_url": f"{app_url}/hilfe",
    }


def render(name: str, **daten) -> tuple[str, str]:
    """Rendert eine Benutzer-Mail ohne Request- oder App-Globals."""
    html = _templates.get_template(f"email/benutzer/{name}.html").render(**daten)
    text = _templates.get_template(f"email/benutzer/{name}.txt").render(**daten)
    return html, text


def _reset_gueltigkeit(minuten: int) -> str:
    if minuten >= 60:
        stunden = minuten / 60
        return f"{stunden:g} Stunde" + ("n" if stunden != 1 else "")
    return f"{minuten} Minuten"


def _einladung_gueltigkeit(stunden: int) -> str:
    if stunden % 24 == 0:
        tage = stunden // 24
        return f"{tage} Tag" + ("e" if tage != 1 else "")
    return f"{stunden} Stunde" + ("n" if stunden != 1 else "")


async def send_einladung(*, to: str, username: str, einladung_url: str,
                          user_display_name: str, db=None, org_id: int | None = None) -> None:
    """Versendet eine Einladung zum Festlegen eines Passworts."""
    from app.services import mail_service

    context = mail_kontext(db, org_id)
    html, text = render(
        "einladung", **context, user_display_name=user_display_name, username=username,
        einladung_url=einladung_url, gueltigkeit=_einladung_gueltigkeit(settings.INVITE_TTL_HOURS),
    )
    smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=to, subject=f"Willkommen bei Einsatzcockpit – {context['org_name']}",
        body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, org_id, msg, smtp_cfg)


async def send_welcome_mail(*, to: str, username: str, password: str,
                            user_display_name: str, app_url: str = "",
                            is_test: bool = False, db=None, org_id: int | None = None) -> None:
    """Versendet die Willkommensmail fuer lokale Benutzer."""
    from app.services import mail_service

    context = mail_kontext(db, org_id)
    html, text = render(
        "willkommen", **context, user_display_name=user_display_name,
        username=username, password=password,
    )
    smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=to, subject=f"Willkommen bei Einsatzcockpit – {context['org_name']}",
        body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, org_id, msg, smtp_cfg)


async def send_sso_welcome_mail(*, to: str, user_display_name: str,
                                app_url: str, org_slug: str, org_name: str = "",
                                is_test: bool = False, db=None, org_id: int | None = None) -> None:
    """Versendet die Willkommensmail fuer Microsoft-SSO-Benutzer."""
    from app.services import mail_service

    context = mail_kontext(db, org_id)
    sso_url = f"{context['app_url']}/sso/{org_slug}/login"
    html, text = render(
        "willkommen_sso", **context, user_display_name=user_display_name,
        sso_url=sso_url,
    )
    smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=to, subject="Willkommen bei Einsatzcockpit – Anmeldung mit Microsoft",
        body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, org_id, msg, smtp_cfg)


async def send_password_reset(*, to: str, reset_url: str, user_display_name: str,
                              db=None, org_id: int | None = None) -> None:
    """Versendet eine Passwort-Reset-Mail im Organisations-Layout."""
    from app.services import mail_service

    context = mail_kontext(db, org_id)
    html, text = render(
        "passwort_reset", **context, user_display_name=user_display_name,
        reset_url=reset_url, gueltigkeit=_reset_gueltigkeit(settings.PASSWORD_RESET_TTL_MIN),
    )
    smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=to, subject=f"Passwort zurücksetzen – Einsatzcockpit {context['org_name']}",
        body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, org_id, msg, smtp_cfg)
