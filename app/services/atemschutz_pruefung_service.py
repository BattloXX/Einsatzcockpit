"""Atemschutzgeräteprüfung: Wart-Benachrichtigung bei Defekt (Mail + Teams),
mit Protokollierung via AtemschutzPruefBenachrichtigung.

Analog zu app/services/schaden_service.py (Fahrtenbuch-Schadensmeldung).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.atemschutz_pruefung import AtemschutzPruefBenachrichtigung, AtemschutzPruefung
from app.models.master import OrgSettings

logger = logging.getLogger("einsatzleiter.atemschutz_pruefung")


async def melde_defekt_background(pruefung_id: int, base_url: str = "") -> None:
    """Background-Variante von melde_defekt für BackgroundTasks.

    Öffnet eine eigene DB-Session (unabhängig vom Request-Lifecycle) und
    committet selbst. Fehler werden geloggt, aber nie propagiert — ein
    Mail-/Teams-Ausfall darf das Prüfprotokoll nicht beeinträchtigen.
    """
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        pruefung = db.get(AtemschutzPruefung, pruefung_id)
        if not pruefung:
            return
        await melde_defekt(pruefung, db, base_url=base_url)
        db.commit()
    except Exception:
        logger.exception("Background-Defektmeldung für Prüfung %d fehlgeschlagen", pruefung_id)
        db.rollback()
    finally:
        db.close()


async def melde_defekt(pruefung: AtemschutzPruefung, db: Session, base_url: str = "") -> None:
    """Sendet Defektmeldung per Mail & Teams (non-blocking) und protokolliert das Ergebnis."""
    org = db.query(OrgSettings).filter(OrgSettings.org_id == pruefung.org_id).first()
    mail_addr = org.atemschutz_wart_mail if org else None
    teams_url = org.atemschutz_wart_teams_webhook_url if org else None
    if not mail_addr and not teams_url:
        return

    geraet = pruefung.geraet
    geraet_label = geraet.anzeige_label if geraet else f"Gerät #{pruefung.geraet_id}"
    punkte = ", ".join(pruefung.defekte_punkte) or "unbekannt"

    betreff = f"Atemschutzgeräteprüfung NICHT IN ORDNUNG – {geraet_label}"
    body_lines = [
        f"Gerät: {geraet_label}",
        f"Nicht in Ordnung: {punkte}",
        f"Geprüft von: {pruefung.traeger_name}",
        f"Geprüft am: {pruefung.eingesetzt_am.strftime('%d.%m.%Y')}",
        f"Ort: {pruefung.ort_text or '—'}",
        f"Zusatzinfo: {pruefung.defekt_info or '—'}",
    ]
    body_text = "\n".join(body_lines)
    detail_url = f"{base_url}/atemschutz-pruefung/{pruefung.id}" if base_url else ""

    # Mail
    if mail_addr:
        try:
            from app.services.mail_service import _build_message, _send, get_smtp_cfg
            smtp_cfg = get_smtp_cfg()
            body_html = "<pre>" + body_text + "</pre>"
            if detail_url:
                body_html += f'<p><a href="{detail_url}">Prüfprotokoll öffnen</a></p>'
            msg = _build_message(to=mail_addr, subject=betreff, body_txt=body_text,
                                 body_html=body_html, smtp_cfg=smtp_cfg)
            await _send(msg, smtp_cfg)
            ok, err = True, None
        except Exception as exc:
            logger.error("Atemschutz-Defektmeldung-Mail-Fehler: %s", exc)
            ok, err = False, str(exc)[:500]
        db.add(AtemschutzPruefBenachrichtigung(
            pruefung_id=pruefung.id,
            org_id=pruefung.org_id,
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
        db.add(AtemschutzPruefBenachrichtigung(
            pruefung_id=pruefung.id,
            org_id=pruefung.org_id,
            kanal="teams",
            empfaenger=teams_url[:200],
            status="gesendet" if ok else "fehler",
            fehlertext=None if ok else "Teams-Post fehlgeschlagen",
            gesendet_am=datetime.now(UTC),
        ))
