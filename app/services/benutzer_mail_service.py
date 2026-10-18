"""Gebrandete Benutzer-Mails mit HTML- und Text-Alternative."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.core.timezones import format_local_datetime
from app.models.master import FireDept, OrgSettings
from app.services.einsatzinfo_mail_service import _absolute_url

_FARBE = re.compile(r"#[0-9a-fA-F]{6}")
_templates = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "templates")),
    autoescape=select_autoescape(enabled_extensions=("html",), default=False),
)

MAIL_VORSCHAU_TYPEN = {
    "einladung": "Einladung",
    "willkommen_sso": "Willkommen mit Microsoft",
    "passwort_reset": "Passwort zurücksetzen",
    "sso_hinweis": "Microsoft-Anmeldung",
    "passwort_geaendert": "Passwort geändert",
}


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
                              durch_admin: bool = False, db=None, org_id: int | None = None) -> None:
    """Versendet eine Passwort-Reset-Mail im Organisations-Layout."""
    from app.services import mail_service

    context = mail_kontext(db, org_id)
    html, text = render(
        "passwort_reset", **context, user_display_name=user_display_name,
        reset_url=reset_url, gueltigkeit=_reset_gueltigkeit(settings.PASSWORD_RESET_TTL_MIN),
        durch_admin=durch_admin,
    )
    smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=to, subject=f"Passwort zurücksetzen – Einsatzcockpit {context['org_name']}",
        body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, org_id, msg, smtp_cfg)


async def send_sso_hinweis(*, to: str, user_display_name: str, org_slug: str,
                           db=None, org_id: int | None = None) -> None:
    """Weist SSO-Benutzer beim Passwort-Reset auf die Microsoft-Anmeldung hin."""
    from app.services import mail_service

    context = mail_kontext(db, org_id)
    sso_url = f"{context['app_url']}/sso/{org_slug}/login"
    html, text = render("sso_hinweis", **context, user_display_name=user_display_name, sso_url=sso_url)
    smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=to, subject="Anmeldung mit Microsoft – Einsatzcockpit",
        body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, org_id, msg, smtp_cfg)


async def send_passwort_geaendert(user, db, *, ausloeser: str, ip: str | None) -> None:
    """Versendet nach einer erfolgreichen Passwortänderung die Sicherheitsbestätigung."""
    if not user.email:
        return
    from app.services import mail_service

    context = mail_kontext(db, user.org_id)
    org = db.query(FireDept).filter(FireDept.id == user.org_id).first()
    zeitpunkt = format_local_datetime(datetime.now(UTC), org)
    html, text = render(
        "passwort_geaendert", **context,
        user_display_name=user.full_name or user.display_name or user.username,
        zeitpunkt=zeitpunkt, ausloeser=ausloeser, ip=ip,
    )
    smtp_cfg = mail_service._org_smtp_cfg(db, user.org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=user.email, subject=(
            f"Dein Konto ist eingerichtet – Einsatzcockpit {context['org_name']}" if ausloeser == "einladung"
            else "Dein Passwort wurde geändert – Einsatzcockpit"
        ),
        body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, user.org_id, msg, smtp_cfg)


def mail_vorschau(typ: str, db, org_id: int | None) -> tuple[str, str, str]:
    """Rendert eine Benutzer-Mail mit sicheren Beispieldaten für die Admin-Vorschau."""
    if typ not in MAIL_VORSCHAU_TYPEN:
        raise ValueError("Unbekannter Mail-Typ.")
    context = mail_kontext(db, org_id)
    context["app_url"] = "https://example.invalid"
    context["android_app_url"] = "https://example.invalid/app/android"
    context["iphone_app_url"] = "https://example.invalid/app"
    org = db.query(FireDept).filter(FireDept.id == org_id).first() if org_id else None
    beispiel = {
        "user_display_name": "Max Mustermann",
        "username": "max.mustermann",
        "einladung_url": "https://example.invalid/passwort-zuruecksetzen?token=beispiel",
        "reset_url": "https://example.invalid/passwort-zuruecksetzen?token=beispiel",
        "sso_url": f"https://example.invalid/sso/{getattr(org, 'slug', 'organisation')}/login",
        "gueltigkeit": _einladung_gueltigkeit(settings.INVITE_TTL_HOURS),
        "durch_admin": True,
        "zeitpunkt": format_local_datetime(datetime.now(UTC), org),
        "ausloeser": "reset",
        "ip": None,
    }
    if typ == "passwort_reset":
        beispiel["gueltigkeit"] = _reset_gueltigkeit(settings.PASSWORD_RESET_TTL_MIN)
    html, text = render(typ, **context, **beispiel)
    subjects = {
        "einladung": f"Dein Konto ist eingerichtet – Einsatzcockpit {context['org_name']}",
        "willkommen_sso": "Willkommen bei Einsatzcockpit – Anmeldung mit Microsoft",
        "passwort_reset": f"Passwort zurücksetzen – Einsatzcockpit {context['org_name']}",
        "sso_hinweis": "Anmeldung mit Microsoft – Einsatzcockpit",
        "passwort_geaendert": "Dein Passwort wurde geändert – Einsatzcockpit",
    }
    return subjects[typ], html, text


async def send_mail_vorschau(typ: str, *, to: str, db, org_id: int | None) -> None:
    """Versendet eine gerenderte Vorschau ohne echte Zugangsdaten oder Tokens."""
    from app.services import mail_service

    subject, html, text = mail_vorschau(typ, db, org_id)
    smtp_cfg = mail_service._org_smtp_cfg(db, org_id) or mail_service.get_smtp_cfg(db)
    msg = mail_service._build_message(
        to=to, subject=f"[Test] {subject}", body_txt=text, body_html=html, smtp_cfg=smtp_cfg,
    )
    await mail_service.deliver(db, org_id, msg, smtp_cfg)
