"""Asynchroner SMTP-Mailer (Phase 5.3 / Phase 7).

Schlanker Wrapper um aiosmtplib. Wird vom Passwort-Reset-Flow und vom
Settings-„Test-Mail"-Button verwendet. Wenn SMTP_HOST leer ist, läuft alles
in einem Trockenlauf: die Mail wird ins Log geschrieben, ohne tatsächlich
zu senden — praktisch für lokale Entwicklung.

SMTP-Konfiguration: Werte aus den System-Einstellungen (Datenbank) haben Vorrang
vor Umgebungsvariablen. So können SMTP-Parameter über das Admin-UI gepflegt werden,
ohne die .env-Datei anpassen zu müssen.
"""
import html
import logging
import re
from email.message import EmailMessage
from typing import Any

try:
    import aiosmtplib  # type: ignore
except ImportError:  # pragma: no cover
    aiosmtplib = None  # type: ignore

from app.config import settings

logger = logging.getLogger("einsatzleiter.mail")


class MailConfigError(RuntimeError):
    """SMTP nicht konfiguriert."""


def get_smtp_cfg(db=None) -> dict[str, Any]:
    """Lädt SMTP-Konfiguration – DB-Werte haben Vorrang vor Umgebungsvariablen."""
    cfg = {
        "host": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "user": settings.SMTP_USER,
        "password": settings.SMTP_PASSWORD,
        "from_addr": settings.SMTP_FROM,
        "starttls": settings.SMTP_STARTTLS,
        "timeout": settings.SMTP_TIMEOUT,
    }
    if db is not None:
        try:
            from app.models.master import SystemSettings

            def _get(key: str) -> str | None:
                row = db.query(SystemSettings).filter_by(key=key).first()
                return row.value if row and row.value else None

            if (v := _get("smtp_host")) is not None:
                cfg["host"] = v
            if (v := _get("smtp_port")) is not None:
                try:
                    cfg["port"] = int(v)
                except ValueError:
                    pass
            if (v := _get("smtp_user")) is not None:
                cfg["user"] = v
            if (v := _get("smtp_password")) is not None:
                cfg["password"] = v
            if (v := _get("smtp_from")) is not None:
                cfg["from_addr"] = v
            if (v := _get("smtp_starttls")) is not None:
                cfg["starttls"] = v.lower() == "true"
            if (v := _get("smtp_timeout")) is not None:
                try:
                    cfg["timeout"] = int(v)
                except ValueError:
                    pass
        except Exception:
            logger.exception("Fehler beim Laden der SMTP-Einstellungen aus der Datenbank")
    return cfg


def _build_message(*, to: str, subject: str, body_txt: str,
                   body_html: str | None = None, smtp_cfg: dict | None = None) -> EmailMessage:
    from_addr = (smtp_cfg or {}).get("from_addr") or (smtp_cfg or {}).get("user") or "noreply@example.com"
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_txt, subtype="plain", charset="utf-8")
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


async def _send(msg: EmailMessage, smtp_cfg: dict) -> None:
    """Versendet eine E-Mail. Bei fehlender SMTP-Konfiguration wird die Mail nur geloggt."""
    if not smtp_cfg.get("host"):
        logger.warning(
            "smtp_host nicht konfiguriert – Mail wird NICHT versendet. "
            "An: %s | Betreff: %s",
            msg["To"], msg["Subject"],
        )
        return

    if aiosmtplib is None:
        raise MailConfigError(
            "aiosmtplib ist nicht installiert. `pip install aiosmtplib` ausführen oder "
            "die Dependency in pyproject.toml aktivieren."
        )

    port = smtp_cfg["port"]
    # Port 465 = Implicit SSL (use_tls); Port 587/25 = STARTTLS
    use_implicit_ssl = port == 465
    kwargs: dict[str, Any] = {
        "hostname": smtp_cfg["host"],
        "port": port,
        "timeout": smtp_cfg["timeout"],
        "use_tls": use_implicit_ssl,
        "start_tls": bool(smtp_cfg["starttls"]) and not use_implicit_ssl,
    }
    if smtp_cfg.get("user"):
        kwargs["username"] = smtp_cfg["user"]
        kwargs["password"] = smtp_cfg.get("password", "")

    await aiosmtplib.send(msg, **kwargs)


async def send_password_reset(*, to: str, reset_url: str, user_display_name: str, db=None) -> None:
    smtp_cfg = get_smtp_cfg(db)
    subject = "Passwort zurücksetzen – Einsatzcockpit"
    ttl = settings.PASSWORD_RESET_TTL_MIN
    body_txt = (
        f"Hallo {user_display_name},\n\n"
        f"Du erhältst diese Mail, weil für deinen Account ein Passwort-Reset "
        f"angefordert wurde.\n\n"
        f"Öffne folgenden Link innerhalb von {ttl} Minuten, um ein neues "
        f"Passwort zu vergeben:\n\n"
        f"{reset_url}\n\n"
        f"Falls du den Reset nicht selbst angefordert hast, ignoriere diese Mail einfach.\n\n"
        f"Mit freundlichen Grüßen\n"
        f"Einsatzcockpit"
    )
    body_html = f"""<!doctype html>
<html lang="de"><body style="font-family: Arial, sans-serif; max-width: 540px; margin: 0 auto;">
<p>Hallo <strong>{user_display_name}</strong>,</p>
<p>Du erhältst diese Mail, weil für deinen Account ein Passwort-Reset
angefordert wurde.</p>
<p>Öffne folgenden Link innerhalb von <strong>{ttl} Minuten</strong>, um ein neues
Passwort zu vergeben:</p>
<p style="text-align:center;">
  <a href="{reset_url}" style="background:#d42225;color:#fff;padding:10px 18px;
     border-radius:6px;text-decoration:none;display:inline-block;">
     Neues Passwort setzen
  </a>
</p>
<p style="font-size:0.85rem;color:#666;">Falls du den Reset nicht selbst angefordert hast,
kannst du diese Mail ignorieren. Dein Passwort bleibt unverändert.</p>
<p style="font-size:0.85rem;color:#666;">URL: <code>{reset_url}</code></p>
</body></html>
"""
    msg = _build_message(to=to, subject=subject, body_txt=body_txt,
                         body_html=body_html, smtp_cfg=smtp_cfg)
    await _send(msg, smtp_cfg)


CONTACT_RECIPIENT = "johannes@battlogg.org"


def _header_safe(value: str) -> str:
    """Entfernt CR/LF (Header-Injection-Schutz) und kürzt überlange Werte."""
    return value.replace("\r", " ").replace("\n", " ").strip()[:200]


def _looks_like_email(value: str) -> bool:
    """Sehr einfache Plausibilitätsprüfung – verhindert v.a. Header-Injection."""
    if any(c in value for c in '\r\n<>",;'):
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def normalize_email_list(raw: str | None) -> str:
    """Zerlegt eine Eingabe mit mehreren E-Mail-Adressen (getrennt durch Komma,
    Semikolon, Leerzeichen oder Zeilenumbruch) in eine bereinigte, komma-separierte
    Liste. Ungültige und doppelte Adressen werden verworfen; das Ergebnis ist
    header-sicher (jede Adresse hat _looks_like_email bestanden) und kann direkt
    als To-Header verwendet werden. Gibt "" zurück, wenn keine gültige Adresse bleibt.
    """
    if not raw:
        return ""
    ergebnis: list[str] = []
    gesehen: set[str] = set()
    for teil in re.split(r"[,;\s]+", raw.strip()):
        teil = teil.strip()
        if not teil or not _looks_like_email(teil):
            continue
        if teil.lower() in gesehen:
            continue
        gesehen.add(teil.lower())
        ergebnis.append(teil)
    return ", ".join(ergebnis)


async def send_contact_message(*, name: str, reply_email: str, message: str, db=None) -> None:
    """Versendet eine Kontaktanfrage von der öffentlichen Startseite an den Betreiber.

    Name/E-Mail/Nachricht stammen aus einem öffentlichen, nicht authentifizierten
    Formular und werden daher konsequent escaped (HTML) bzw. von CR/LF befreit (Header).
    """
    smtp_cfg = get_smtp_cfg(db)
    # Header-sichere Variante für Subject/Reply-To (kein CR/LF).
    safe_name_hdr = _header_safe(name)
    subject = f"Kontaktanfrage von {safe_name_hdr or 'Unbekannt'} – Einsatzcockpit"

    body_txt = (
        f"Neue Kontaktanfrage über Einsatzcockpit:\n\n"
        f"Name:    {name}\n"
        f"E-Mail:  {reply_email}\n\n"
        f"Nachricht:\n{message}\n"
    )
    # HTML-Body: jeden Wert escapen (XSS-in-Email verhindern).
    safe_name = html.escape(name)
    safe_mail = html.escape(reply_email, quote=True)
    safe_msg = html.escape(message)
    body_html = f"""<!doctype html>
<html lang="de"><body style="font-family: Arial, sans-serif; max-width: 540px; margin: 0 auto;">
<h2 style="color:#d42225;">Neue Kontaktanfrage</h2>
<p><strong>Name:</strong> {safe_name}<br>
<strong>E-Mail:</strong> {safe_mail}</p>
<p><strong>Nachricht:</strong></p>
<p style="white-space:pre-wrap;border-left:3px solid #d42225;padding-left:12px;">{safe_msg}</p>
</body></html>
"""
    msg = _build_message(to=CONTACT_RECIPIENT, subject=subject, body_txt=body_txt,
                         body_html=body_html, smtp_cfg=smtp_cfg)
    # Reply-To nur setzen, wenn die Adresse plausibel & header-sicher ist.
    if _looks_like_email(reply_email):
        msg["Reply-To"] = reply_email
    await _send(msg, smtp_cfg)


async def send_welcome_mail(*, to: str, username: str, password: str,
                            user_display_name: str, app_url: str = "",
                            is_test: bool = False, db=None) -> None:
    smtp_cfg = get_smtp_cfg(db)
    subject = "Willkommen bei Einsatzcockpit – Zugangsdaten"
    test_notice_txt = "\n⚠️  HINWEIS: Dies ist ein TESTSYSTEM – bitte keine Echtdaten eingeben.\n" if is_test else ""
    test_notice_html = (
        '<p style="background:#7c3c00;color:#ffe0b2;padding:8px 12px;border-radius:4px;font-weight:bold;">'
        '⚠️  HINWEIS: Dies ist ein TESTSYSTEM – bitte keine Echtdaten eingeben.</p>'
    ) if is_test else ""
    url_txt = f"\nAnmelden unter: {app_url}\n" if app_url else ""
    safe_url = html.escape(app_url) if app_url else ""
    url_html = (
        f'<p style="text-align:center;margin:16px 0;">'
        f'<a href="{safe_url}" style="background:#d42225;color:#fff;padding:10px 18px;'
        f'border-radius:6px;text-decoration:none;display:inline-block;">Jetzt anmelden</a></p>'
        f'<p style="font-size:0.85rem;color:#666;">URL: <code>{safe_url}</code></p>'
    ) if app_url else ""
    body_txt = (
        f"Hallo {user_display_name},\n\n"
        f"Dein Account bei Einsatzcockpit wurde eingerichtet.\n"
        f"{test_notice_txt}"
        f"\nBenutzername: {username}\n"
        f"Passwort:     {password}\n"
        f"{url_txt}"
        f"\nBitte ändere dein Passwort nach dem ersten Login.\n\n"
        f"Mit freundlichen Grüßen\n"
        f"Einsatzcockpit"
    )
    safe_name = html.escape(user_display_name)
    safe_user = html.escape(username)
    safe_pw = html.escape(password)
    body_html = f"""<!doctype html>
<html lang="de"><body style="font-family: Arial, sans-serif; max-width: 540px; margin: 0 auto;">
<p>Hallo <strong>{safe_name}</strong>,</p>
<p>Dein Account bei Einsatzcockpit wurde eingerichtet.</p>
{test_notice_html}
<table style="border-collapse:collapse;margin:12px 0;">
<tr><td style="padding:4px 12px 4px 0;color:#666;">Benutzername:</td>
    <td style="padding:4px 0;"><code>{safe_user}</code></td></tr>
<tr><td style="padding:4px 12px 4px 0;color:#666;">Passwort:</td>
    <td style="padding:4px 0;"><code>{safe_pw}</code></td></tr>
</table>
<p><strong>Bitte ändere dein Passwort nach dem ersten Login.</strong></p>
{url_html}
<p style="font-size:0.85rem;color:#666;">Bewahre deine Zugangsdaten sicher auf.</p>
</body></html>
"""
    msg = _build_message(to=to, subject=subject, body_txt=body_txt,
                         body_html=body_html, smtp_cfg=smtp_cfg)
    await _send(msg, smtp_cfg)


async def send_sso_welcome_mail(*, to: str, user_display_name: str,
                                 app_url: str, org_slug: str, org_name: str = "",
                                 is_test: bool = False, db=None) -> None:
    smtp_cfg = get_smtp_cfg(db)
    subject = "Anmeldung per Microsoft-Login – Einsatzcockpit"
    sso_url = f"{app_url.rstrip('/')}/sso/{org_slug}/login"
    safe_url = html.escape(app_url.rstrip("/"))
    safe_sso_url = html.escape(sso_url)
    safe_name = html.escape(user_display_name)
    org_display = org_name if org_name else "deiner Organisation"
    safe_org = html.escape(org_display)
    test_notice_txt = "\n⚠️  HINWEIS: Dies ist ein TESTSYSTEM – bitte keine Echtdaten eingeben.\n" if is_test else ""
    test_notice_html = (
        '<p style="background:#7c3c00;color:#ffe0b2;padding:8px 12px;border-radius:4px;font-weight:bold;">'
        '⚠️  HINWEIS: Dies ist ein TESTSYSTEM – bitte keine Echtdaten eingeben.</p>'
    ) if is_test else ""
    body_txt = (
        f"Hallo {user_display_name},\n\n"
        f"Dein Account bei Einsatzcockpit wurde eingerichtet.\n"
        f"Du kannst dich mit deinem dienstlichen Microsoft-Account der {org_display} anmelden.\n"
        f"{test_notice_txt}"
        f"\n"
        f"WICHTIG: Verwende ausschliesslich deinen dienstlichen Microsoft-Account\n"
        f"der {org_display} – also jenen Account, den du auch fuer\n"
        f"Microsoft 365 / Teams der {org_display} nutzt.\n"
        f"Kein privates Microsoft-, Hotmail- oder Outlook.com-Konto verwenden!\n"
        f"\n"
        f"Anmeldung per Microsoft-Login:\n  {sso_url}\n\n"
        f"Alternativ: Gehe auf {app_url.rstrip('/')} und klicke auf \"Mit Microsoft anmelden\".\n\n"
        f"Kein eigenes Passwort erforderlich – die Anmeldung erfolgt vollstaendig ueber Microsoft.\n\n"
        f"Mit freundlichen Gruessen\n"
        f"Einsatzcockpit"
    )
    body_html = f"""<!doctype html>
<html lang="de"><body style="font-family: Arial, sans-serif; max-width: 540px; margin: 0 auto;">
<p>Hallo <strong>{safe_name}</strong>,</p>
<p>Dein Account bei Einsatzcockpit wurde eingerichtet.<br>
Du kannst dich mit deinem <strong>dienstlichen Microsoft-Account der {safe_org}</strong> anmelden.</p>
{test_notice_html}
<div style="background:#e8f4fd;border-left:4px solid #0078d4;padding:12px 16px;
border-radius:0 4px 4px 0;margin:16px 0;font-size:0.95rem;">
  <strong>Welches Konto verwenden?</strong><br><br>
  Melde dich mit deinem <strong>dienstlichen Microsoft-Account der {safe_org}</strong> an –
  also dem Konto, das du auch für Microsoft 365 / Teams der {safe_org} verwendest.<br><br>
  <span style="color:#c0392b;font-weight:bold;">&#10060; Kein privates
  Microsoft-, Hotmail- oder Outlook.com-Konto verwenden!</span>
</div>
<p style="text-align:center;margin:20px 0;">
  <a href="{safe_sso_url}" style="background:#0078d4;color:#fff;padding:12px 24px;
     border-radius:6px;text-decoration:none;display:inline-block;font-size:1rem;">
    &#128274; Mit Microsoft anmelden ({safe_org})
  </a>
</p>
<p style="font-size:0.85rem;color:#666;">
  Direkter SSO-Link: <code>{safe_sso_url}</code><br>
  Oder gehe auf <a href="{safe_url}">{safe_url}</a> und klicke auf &bdquo;Mit Microsoft anmelden&ldquo;.
</p>
<p style="font-size:0.85rem;color:#666;">
  Kein eigenes Passwort erforderlich – die Anmeldung erfolgt vollständig über Microsoft.
</p>
</body></html>
"""
    msg = _build_message(to=to, subject=subject, body_txt=body_txt,
                         body_html=body_html, smtp_cfg=smtp_cfg)
    await _send(msg, smtp_cfg)


async def send_test_mail(*, to: str, db=None) -> None:
    smtp_cfg = get_smtp_cfg(db)
    source = "Datenbank" if db is not None else "Umgebungsvariablen"
    msg = _build_message(
        to=to,
        subject="Test-Mail von Einsatzcockpit",
        body_txt=(
            "Diese Test-Mail bestätigt, dass die SMTP-Konfiguration funktioniert.\n\n"
            f"Konfigurationsquelle: {source}\n"
            f"Host: {smtp_cfg['host']}\nPort: {smtp_cfg['port']}\n"
            f"STARTTLS: {smtp_cfg['starttls']}\nAbsender: {smtp_cfg['from_addr']}\n"
        ),
        smtp_cfg=smtp_cfg,
    )
    await _send(msg, smtp_cfg)
