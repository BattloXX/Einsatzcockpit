"""Schadenmeldung: Mail + Teams, mit Protokollierung via FahrtBenachrichtigung."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.fahrtenbuch import Fahrt, FahrtBenachrichtigung
from app.models.master import OrgSettings, VehicleMaster

logger = logging.getLogger("einsatzleiter.schaden")


async def melde_schaden_background(fahrt_id: int, base_url: str = "") -> None:
    """Background-Variante von melde_schaden für BackgroundTasks (STAB-4).

    Öffnet eine eigene DB-Session (unabhängig vom Request-Lifecycle, das der
    Request-Handler bereits geschlossen hat, wenn BackgroundTasks laufen) und
    committet selbst. Fehler werden geloggt, aber nie propagiert — ein
    Mail-/Teams-Ausfall darf den Fahrtenbuch-Eintrag nicht beeinträchtigen.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fahrt = db.get(Fahrt, fahrt_id)
        if not fahrt:
            return
        await melde_schaden(fahrt, db, base_url=base_url)
        db.commit()
    except Exception:
        logger.exception("Background-Schadenmeldung für Fahrt %d fehlgeschlagen", fahrt_id)
        db.rollback()
    finally:
        db.close()


def _empfaenger(fahrzeug: VehicleMaster, org: OrgSettings | None) -> tuple[str | None, str | None]:
    mail = fahrzeug.schaden_mail_override or (org.schaden_mail if org else None)
    teams = fahrzeug.schaden_teams_webhook_override or (org.schaden_teams_webhook_url if org else None)
    return mail, teams


async def melde_schaden(fahrt: Fahrt, db: Session, base_url: str = "") -> None:
    """Sendet Schadenmeldung per Mail & Teams (non-blocking) und protokolliert das Ergebnis."""
    fahrzeug = (
        db.query(VehicleMaster)
        .filter(VehicleMaster.id == fahrt.fahrzeug_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if not fahrzeug:
        return

    org = db.query(OrgSettings).filter(OrgSettings.org_id == fahrt.org_id).first()
    mail_addr, teams_url = _empfaenger(fahrzeug, org)
    # Mehrere Empfänger möglich: auf eine bereinigte, komma-separierte Liste normalisieren
    # (deckt auch Alt-Werte mit Semikolon/Leerzeichen ab). Leer -> nicht senden.
    from app.services.mail_service import normalize_email_list
    mail_addr = normalize_email_list(mail_addr) or None

    betriebsfaehig_text = "Ja" if fahrt.schaden_betriebsfaehig else "Nein"
    betreff = (
        f"Schadenmeldung {fahrzeug.code}"
        f"{' ' + fahrzeug.kennzeichen if fahrzeug.kennzeichen else ''}"
        f" – {fahrt.zeitpunkt.strftime('%d.%m.%Y')}"
    )
    body_lines = [
        f"Fahrzeug: {fahrzeug.code} {fahrzeug.kennzeichen or ''}".strip(),
        f"Maschinist: {fahrt.maschinist_name}",
        f"Zeitpunkt: {fahrt.zeitpunkt.strftime('%d.%m.%Y %H:%M')}",
        f"Betriebsfähig: {betriebsfaehig_text}",
        f"Beschreibung: {fahrt.schaden_beschreibung or '—'}",
    ]
    body_text = "\n".join(body_lines)
    detail_url = f"{base_url}/verwaltung/fahrten/{fahrt.id}" if base_url else ""

    # Mail
    if mail_addr:
        try:
            from app.services.mail_service import _build_message, _send, get_smtp_cfg
            smtp_cfg = get_smtp_cfg()
            body_html = "<pre>" + body_text + "</pre>"
            if detail_url:
                body_html += f'<p><a href="{detail_url}">Fahrt in der Verwaltung öffnen</a></p>'
            msg = _build_message(to=mail_addr, subject=betreff, body_txt=body_text,
                                 body_html=body_html, smtp_cfg=smtp_cfg)
            await _send(msg, smtp_cfg)
            ok, err = True, None
        except Exception as exc:
            logger.error("Schadenmeldung-Mail-Fehler: %s", exc)
            ok, err = False, str(exc)[:500]
        db.add(FahrtBenachrichtigung(
            fahrt_id=fahrt.id,
            org_id=fahrt.org_id,
            kanal="mail",
            empfaenger=mail_addr,
            status="gesendet" if ok else "fehler",
            fehlertext=err,
            gesendet_am=datetime.now(UTC),
        ))

    # Teams
    if teams_url:
        from app.services.teams_service import post_teams_karte
        ok = await post_teams_karte(teams_url, betreff, body_text, url=detail_url or None)
        db.add(FahrtBenachrichtigung(
            fahrt_id=fahrt.id,
            org_id=fahrt.org_id,
            kanal="teams",
            empfaenger=teams_url[:200],
            status="gesendet" if ok else "fehler",
            fehlertext=None if ok else "Teams-Post fehlgeschlagen",
            gesendet_am=datetime.now(UTC),
        ))
