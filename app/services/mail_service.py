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


def mail_available(db, org_id: int | None) -> bool:
    """True, wenn O365, Resend oder SMTP tatsächlich konfiguriert ist."""
    if org_id is not None and settings.O365_MAIL_ENABLED:
        from app.models.org_mail import OrgO365MailConfig
        cfg = db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
        if cfg and cfg.enabled and cfg.is_fully_configured:
            return True
    return bool(
        _org_resend_cfg(db, org_id)
        or get_resend_cfg(db)
        or (_org_smtp_cfg(db, org_id) or get_smtp_cfg(db)).get("host")
    )


def versandweg(db, org_id: int | None) -> dict[str, Any]:
    """Liefert den aktiven Versandweg in derselben Priorität wie ``deliver``."""
    wege: list[tuple[str, str | None]] = []
    if org_id is not None and settings.O365_MAIL_ENABLED:
        try:
            from app.models.org_mail import OrgO365MailConfig

            o365_cfg = db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
            if o365_cfg and o365_cfg.enabled and o365_cfg.is_fully_configured:
                wege.append(("o365", o365_cfg.sender_address))
        except Exception:
            logger.exception("O365-Config-Lookup fehlgeschlagen (Org %s)", org_id)
    own_resend_cfg = _org_resend_cfg(db, org_id)
    if own_resend_cfg:
        wege.append(("resend_org", own_resend_cfg["from_addr"]))
    else:
        global_resend_cfg = get_resend_cfg(db)
        if global_resend_cfg:
            wege.append((
                "resend_global",
                _resend_from_addr_for_org(db, org_id, global_resend_cfg["from_domain"]),
            ))
    own_smtp_cfg = _org_smtp_cfg(db, org_id)
    if own_smtp_cfg:
        wege.append(("smtp_org", own_smtp_cfg["from_addr"]))
    else:
        global_smtp_cfg = get_smtp_cfg(db)
        if global_smtp_cfg.get("host"):
            wege.append(("smtp_global", global_smtp_cfg.get("from_addr")))
    if not wege:
        return {"weg": "keiner", "absender": None, "fallbacks": []}
    weg, absender = wege[0]
    return {"weg": weg, "absender": absender, "fallbacks": [item[0] for item in wege[1:]]}


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


def _org_smtp_cfg(db, org_id: int | None) -> dict[str, Any] | None:
    """Lädt den eigenen SMTP-Server einer Org (OrgSmtpConfig), falls vorhanden,
    aktiviert und vollständig konfiguriert. Liefert ein Dict im selben Format wie
    get_smtp_cfg() oder None (dann greift der globale SMTP als Fallback)."""
    if db is None or org_id is None:
        return None
    try:
        from app.core.crypto import decrypt_secret
        from app.models.org_mail import OrgSmtpConfig

        cfg = db.query(OrgSmtpConfig).filter(OrgSmtpConfig.org_id == org_id).first()
        if not cfg or not cfg.enabled or not cfg.is_fully_configured:
            return None
        return {
            "host": cfg.host,
            "port": cfg.port,
            "user": cfg.user,
            "password": decrypt_secret(cfg.password_enc),
            "from_addr": cfg.from_addr,
            "starttls": cfg.starttls,
            "timeout": cfg.timeout,
        }
    except Exception:
        logger.exception("Eigener SMTP-Server der Org %s konnte nicht geladen werden — Fallback global", org_id)
        return None


def _org_resend_cfg(db, org_id: int | None) -> dict[str, Any] | None:
    """Lädt die aktive und vollständige Resend-Konfiguration einer Org."""
    if db is None or org_id is None:
        return None
    try:
        from app.core.crypto import decrypt_secret
        from app.models.org_mail import OrgResendConfig

        cfg = db.query(OrgResendConfig).filter(OrgResendConfig.org_id == org_id).first()
        if not cfg or not cfg.enabled or not cfg.is_fully_configured:
            return None
        return {"api_key": decrypt_secret(cfg.api_key_enc), "from_addr": cfg.from_addr}
    except Exception:
        logger.exception("Resend-Konfiguration der Org %s konnte nicht geladen werden", org_id)
        return None


def get_resend_cfg(db) -> dict[str, str] | None:
    """Lädt die globale Resend-Konfiguration und entschlüsselt den API-Key."""
    if db is None:
        return None
    try:
        from app.core.crypto import decrypt_secret
        from app.models.master import SystemSettings

        def _get(key: str) -> str | None:
            row = db.query(SystemSettings).filter_by(key=key).first()
            return row.value if row and row.value else None

        enabled = (_get("resend_enabled") or "").lower() == "true"
        api_key_enc = _get("resend_api_key")
        from_domain = (_get("resend_from_domain") or "").strip().lower()
        from app.services.org_mail_adresse import ist_gueltige_domain

        if not enabled or not api_key_enc or not ist_gueltige_domain(from_domain):
            return None
        return {"api_key": decrypt_secret(api_key_enc), "from_domain": from_domain}
    except Exception:
        logger.exception("Globale Resend-Konfiguration konnte nicht geladen werden")
        return None


def _resend_from_addr_for_org(db, org_id: int | None, from_domain: str) -> str:
    """Leitet die Absenderadresse sicher aus Typ, Org-Slug und Domain ab."""
    if db is not None and org_id is not None:
        try:
            from app.models.master import FireDept
            from app.services.org_mail_adresse import absender_adresse

            org = db.query(FireDept).filter(FireDept.id == org_id).first()
            if org and org.slug:
                return absender_adresse(org, from_domain)
        except Exception:
            logger.exception("Org-Adresse fuer Resend-Absender konnte nicht geladen werden (Org %s)", org_id)
    from app.services.org_mail_adresse import ist_gueltige_domain

    safe_domain = from_domain.strip().lower() if ist_gueltige_domain(from_domain) else "example.invalid"
    return f"noreply@{safe_domain}"


def _set_resend_reply_to_if_enabled(db, org_id: int | None, msg: EmailMessage) -> None:
    """Setzt die zentrale Empfangsadresse nur fuer aktive Org-Posteingänge."""
    if org_id is None or msg.get("Reply-To"):
        return
    try:
        from app.models.master import FireDept
        from app.services.org_mail_adresse import empfangs_adresse, inbound_domain, mail_eingang_aktiv

        if not mail_eingang_aktiv(db, org_id):
            return
        org = db.query(FireDept).filter(FireDept.id == org_id).first()
        if org:
            msg["Reply-To"] = empfangs_adresse(org, inbound_domain(db))
    except Exception:
        logger.exception("Reply-To fuer Resend konnte nicht gesetzt werden (Org %s)", org_id)


async def deliver(db, org_id: int | None, msg: EmailMessage, smtp_cfg: dict | None = None) -> None:
    """Zentraler Versand-Dispatcher: Office 365 (falls für die Org aktiviert +
    vollständig konfiguriert) zuerst versuchen, danach Resend (Org, sonst global)
    und zuletzt SMTP (Org, sonst global).

    Graph-Fehler werden verschluckt (das ist der Sinn des Fallbacks) und nur
    geloggt; ein anschließender SMTP-Fehler wird wie bisher propagiert, damit
    Aufrufer (z.B. schaden_service) einen echten Fehlschlag weiterhin korrekt
    protokollieren können.
    """
    if org_id is not None and settings.O365_MAIL_ENABLED:
        o365_cfg = None
        try:
            from app.models.org_mail import OrgO365MailConfig

            o365_cfg = (
                db.query(OrgO365MailConfig).filter(OrgO365MailConfig.org_id == org_id).first()
                if db is not None else None
            )
        except Exception:
            logger.exception("O365-Config-Lookup fehlgeschlagen (Org %s) — Fallback Resend", org_id)
            o365_cfg = None

        if o365_cfg and o365_cfg.enabled and o365_cfg.is_fully_configured:
            try:
                from app.services.o365_mail_service import send_via_graph
                await send_via_graph(msg, o365_cfg)
                return  # Erfolg — kein SMTP-Versand mehr noetig
            except Exception as exc:
                logger.warning(
                    "O365-Mailversand fehlgeschlagen (Org %s) — Fallback Resend: %s", org_id, exc,
                )

    own_resend_cfg = _org_resend_cfg(db, org_id)
    if own_resend_cfg:
        try:
            from app.services.resend_mail_service import send_via_resend

            _set_resend_reply_to_if_enabled(db, org_id, msg)
            await send_via_resend(msg, own_resend_cfg["api_key"], own_resend_cfg["from_addr"])
            return
        except Exception as exc:
            logger.warning("Resend (Org) fehlgeschlagen (Org %s) — Fallback SMTP: %s", org_id, exc)
    else:
        global_resend_cfg = get_resend_cfg(db)
        if global_resend_cfg:
            try:
                from app.services.resend_mail_service import send_via_resend

                from_addr = _resend_from_addr_for_org(
                    db, org_id, global_resend_cfg["from_domain"],
                )
                _set_resend_reply_to_if_enabled(db, org_id, msg)
                await send_via_resend(msg, global_resend_cfg["api_key"], from_addr)
                return
            except Exception as exc:
                logger.warning(
                    "Resend (global) fehlgeschlagen (Org %s) — Fallback SMTP: %s", org_id, exc,
                )

    cfg = smtp_cfg
    if cfg is None:
        cfg = _org_smtp_cfg(db, org_id) or get_smtp_cfg(db)
    await _send(msg, cfg)


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


async def send_password_reset(*, to: str, reset_url: str, user_display_name: str,
                               db=None, org_id: int | None = None) -> None:
    from app.services.benutzer_mail_service import send_password_reset as _send_password_reset

    await _send_password_reset(
        to=to, reset_url=reset_url, user_display_name=user_display_name, db=db, org_id=org_id,
    )


async def send_pflegeauftrag_einladung(
    *, to: str, kontakt_name: str, objekt_name: str, link: str, gueltig_bis_text: str,
    auftrag_text: str | None = None, db=None, org_id: int | None = None,
) -> None:
    smtp_cfg = _org_smtp_cfg(db, org_id) or get_smtp_cfg(db)
    subject = f"Bitte um Prüfung der Objektdaten – {objekt_name}"
    hinweis_txt = f"\nNachricht der Feuerwehr:\n\"{auftrag_text}\"\n" if auftrag_text else ""
    body_txt = (
        f"Hallo {kontakt_name},\n\n"
        f"bitte prüfen Sie die bei uns hinterlegten Daten zum Objekt \"{objekt_name}\". "
        f"Ihre Rückmeldung hilft uns, die Einsatzinformationen aktuell zu halten.\n\n"
        f"Öffnen Sie dazu den folgenden Link und bestätigen oder ergänzen Sie die Angaben. "
        f"Dafür ist kein Login erforderlich.\n\n"
        f"{link}\n\n"
        f"Der Link ist bis {gueltig_bis_text} gültig. Änderungen werden erst nach "
        f"einer internen Prüfung und Freigabe aktiv."
        f"{hinweis_txt}\n"
        f"Mit freundlichen Grüßen\n"
        f"Einsatzcockpit"
    )
    safe_name = html.escape(kontakt_name)
    safe_objekt = html.escape(objekt_name)
    safe_link = html.escape(link, quote=True)
    safe_hinweis = html.escape(auftrag_text) if auftrag_text else ""
    hinweis_html = (
        f'<p style="white-space:pre-wrap;border-left:3px solid #d42225;padding-left:12px;">'
        f'<strong>Nachricht der Feuerwehr:</strong><br>{safe_hinweis}</p>'
        if auftrag_text else ""
    )
    body_html = f"""<!doctype html>
<html lang="de"><body style="font-family: Arial, sans-serif; max-width: 540px; margin: 0 auto;">
<p>Hallo <strong>{safe_name}</strong>,</p>
<p>bitte prüfen Sie die bei uns hinterlegten Daten zum Objekt <strong>{safe_objekt}</strong>.
Ihre Rückmeldung hilft uns, die Einsatzinformationen aktuell zu halten.</p>
<p>Öffnen Sie dazu den folgenden Link und bestätigen oder ergänzen Sie die Angaben.
Dafür ist <strong>kein Login erforderlich</strong>.</p>
<p style="text-align:center;">
  <a href="{safe_link}" style="background:#d42225;color:#fff;padding:10px 18px;
     border-radius:6px;text-decoration:none;display:inline-block;">
     Objektdaten prüfen
  </a>
</p>
<p>Der Link ist bis <strong>{gueltig_bis_text}</strong> gültig. Änderungen werden erst nach
einer internen Prüfung und Freigabe aktiv.</p>
{hinweis_html}
<p style="font-size:0.85rem;color:#666;">URL: <code>{safe_link}</code></p>
</body></html>
"""
    msg = _build_message(to=to, subject=subject, body_txt=body_txt,
                         body_html=body_html, smtp_cfg=smtp_cfg)
    await deliver(db, org_id, msg, smtp_cfg)


async def send_pflegeauftrag_nacharbeit(
    *, to: str, kontakt_name: str, objekt_name: str, link: str, text: str,
    gueltig_bis_text: str, db=None, org_id: int | None = None,
) -> None:
    """Versendet die Bitte um Nacharbeit zu einem Objekt-Pflegeauftrag."""
    smtp_cfg = _org_smtp_cfg(db, org_id) or get_smtp_cfg(db)
    subject = f"Bitte ergänzen Sie die Angaben – {objekt_name}"
    body_txt = (
        f"Hallo {kontakt_name},\n\n"
        f"wir haben Ihre Rückmeldung zum Objekt \"{objekt_name}\" geprüft und benötigen "
        f"noch eine Ergänzung oder Korrektur:\n\n{text}\n\n"
        f"Öffnen Sie dazu bitte erneut den folgenden Link. Dafür ist kein Login erforderlich; "
        f"der Link funktioniert genauso wie bisher und ist bis {gueltig_bis_text} gültig.\n\n{link}\n\n"
        f"Mit freundlichen Grüßen\nEinsatzcockpit"
    )
    safe_name = html.escape(kontakt_name)
    safe_objekt = html.escape(objekt_name)
    safe_link = html.escape(link, quote=True)
    safe_text = html.escape(text)
    body_html = f'''<!doctype html>
<html lang="de"><body style="font-family: Arial, sans-serif; max-width: 540px; margin: 0 auto;">
<p>Hallo <strong>{safe_name}</strong>,</p>
<p>wir haben Ihre Rückmeldung zum Objekt <strong>{safe_objekt}</strong> geprüft und benötigen
noch eine Ergänzung oder Korrektur:</p>
<p style="white-space:pre-wrap;border-left:3px solid #d42225;padding-left:12px;">{safe_text}</p>
<p>Öffnen Sie dazu bitte erneut den folgenden Link. Dafür ist <strong>kein Login erforderlich</strong>;
der Link funktioniert genauso wie bisher und ist bis <strong>{html.escape(gueltig_bis_text)}</strong> gültig.</p>
<p style="text-align:center;"><a href="{safe_link}" style="background:#d42225;color:#fff;padding:10px 18px;
border-radius:6px;text-decoration:none;display:inline-block;">Angaben ergänzen</a></p>
<p style="font-size:0.85rem;color:#666;">URL: <code>{safe_link}</code></p>
</body></html>'''
    msg = _build_message(to=to, subject=subject, body_txt=body_txt,
                         body_html=body_html, smtp_cfg=smtp_cfg)
    await deliver(db, org_id, msg, smtp_cfg)


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
    # Systemweiter SMTP hat Vorrang. Ohne SMTP (oder wenn er scheitert) über das
    # zentrale Resend mit noreply@<Versanddomain>; Antworten gehen per Reply-To an den Absender.
    resend_cfg = get_resend_cfg(db)
    if smtp_cfg.get("host"):
        try:
            await _send(msg, smtp_cfg)
            return
        except Exception as exc:
            if not resend_cfg:
                raise
            logger.warning("Kontaktformular: SMTP fehlgeschlagen, Fallback Resend: %s", exc)
    if resend_cfg:
        from app.services.org_mail_adresse import ist_gueltige_domain
        from app.services.resend_mail_service import send_via_resend

        domain = resend_cfg["from_domain"].strip().lower()
        if not ist_gueltige_domain(domain):
            raise MailConfigError("Resend-Versanddomain ist ungültig.")
        await send_via_resend(msg, resend_cfg["api_key"], f"noreply@{domain}")
        return
    # Ohne Versandweg würde _send nur protokollieren; das Formular soll dann einen Fehler zeigen.
    raise MailConfigError("Weder SMTP noch Resend ist konfiguriert.")


async def send_welcome_mail(*, to: str, username: str, password: str,
                            user_display_name: str, app_url: str = "",
                            is_test: bool = False, db=None, org_id: int | None = None) -> None:
    from app.services.benutzer_mail_service import send_welcome_mail as _send_welcome_mail

    await _send_welcome_mail(
        to=to, username=username, password=password, user_display_name=user_display_name,
        app_url=app_url, is_test=is_test, db=db, org_id=org_id,
    )


async def send_sso_welcome_mail(*, to: str, user_display_name: str,
                                 app_url: str, org_slug: str, org_name: str = "",
                                 is_test: bool = False, db=None, org_id: int | None = None) -> None:
    from app.services.benutzer_mail_service import send_sso_welcome_mail as _send_sso_welcome_mail

    await _send_sso_welcome_mail(
        to=to, user_display_name=user_display_name, app_url=app_url, org_slug=org_slug,
        org_name=org_name, is_test=is_test, db=db, org_id=org_id,
    )


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
