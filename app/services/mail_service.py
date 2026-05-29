"""Asynchroner SMTP-Mailer (Phase 5.3 / Phase 7).

Schlanker Wrapper um aiosmtplib. Wird vom Passwort-Reset-Flow und vom
Settings-„Test-Mail"-Button verwendet. Wenn SMTP_HOST leer ist, läuft alles
in einem Trockenlauf: die Mail wird ins Log geschrieben, ohne tatsächlich
zu senden — praktisch für lokale Entwicklung.
"""
import logging
from email.message import EmailMessage

try:
    import aiosmtplib  # type: ignore
except ImportError:  # pragma: no cover – Dependency wird via pyproject installiert
    aiosmtplib = None  # type: ignore

from app.config import settings

logger = logging.getLogger("einsatzleiter.mail")


class MailConfigError(RuntimeError):
    """SMTP nicht konfiguriert."""


def _build_message(*, to: str, subject: str, body_txt: str, body_html: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER or "noreply@example.com"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_txt, subtype="plain", charset="utf-8")
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


async def _send(msg: EmailMessage) -> None:
    """Versendet eine E-Mail. Bei fehlender SMTP-Konfiguration wird die Mail nur geloggt."""
    if not settings.SMTP_HOST:
        logger.warning(
            "SMTP_HOST nicht gesetzt – Mail wird NICHT versendet. "
            "An: %s | Betreff: %s",
            msg["To"], msg["Subject"],
        )
        return

    if aiosmtplib is None:
        raise MailConfigError(
            "aiosmtplib ist nicht installiert. `pip install aiosmtplib` ausführen oder "
            "die Dependency in pyproject.toml aktivieren."
        )

    kwargs = {
        "hostname": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "timeout": settings.SMTP_TIMEOUT,
        "start_tls": bool(settings.SMTP_STARTTLS),
    }
    if settings.SMTP_USER:
        kwargs["username"] = settings.SMTP_USER
        kwargs["password"] = settings.SMTP_PASSWORD

    await aiosmtplib.send(msg, **kwargs)


async def send_password_reset(*, to: str, reset_url: str, user_display_name: str) -> None:
    subject = "Passwort zurücksetzen – Einsatzleiter-Hilfswerkzeug"
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
        f"Einsatzleiter-Hilfswerkzeug"
    )
    body_html = f"""<!doctype html>
<html lang="de"><body style="font-family: Arial, sans-serif; max-width: 540px; margin: 0 auto;">
<p>Hallo <strong>{user_display_name}</strong>,</p>
<p>Du erhältst diese Mail, weil für deinen Account ein Passwort-Reset
angefordert wurde.</p>
<p>Öffne folgenden Link innerhalb von <strong>{ttl} Minuten</strong>, um ein neues
Passwort zu vergeben:</p>
<p style="text-align:center;">
  <a href="{reset_url}" style="background:#b71921;color:#fff;padding:10px 18px;
     border-radius:6px;text-decoration:none;display:inline-block;">
     Neues Passwort setzen
  </a>
</p>
<p style="font-size:0.85rem;color:#666;">Falls du den Reset nicht selbst angefordert hast,
kannst du diese Mail ignorieren. Dein Passwort bleibt unverändert.</p>
<p style="font-size:0.85rem;color:#666;">URL: <code>{reset_url}</code></p>
</body></html>
"""
    msg = _build_message(to=to, subject=subject, body_txt=body_txt, body_html=body_html)
    await _send(msg)


async def send_test_mail(*, to: str) -> None:
    msg = _build_message(
        to=to,
        subject="Test-Mail vom Einsatzleiter-Hilfswerkzeug",
        body_txt=(
            "Diese Test-Mail bestätigt, dass die SMTP-Konfiguration funktioniert.\n\n"
            f"Host: {settings.SMTP_HOST}\nPort: {settings.SMTP_PORT}\n"
            f"STARTTLS: {settings.SMTP_STARTTLS}\nAbsender: {settings.SMTP_FROM}\n"
        ),
    )
    await _send(msg)
