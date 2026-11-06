"""SMS-Empfang: Log + Weiterleitungsregeln (Teams-Webhook, SMS an Gruppen/Mitglieder/Ad-hoc).

Wird vom SMS-Gateway-WebSocket (app/routers/ws.py) bei "sms.received" aufgerufen:
  1. record_inbound_sms()  – synchron, legt IMMER einen Log-Eintrag an
  2. process_inbound_sms() – als Hintergrund-Task, wertet Regeln aus und leitet weiter
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core.audit import write_audit
from app.core.telefon import telefon_kompakt
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.sms import SmsForwardRule, SmsInbox
from app.services.loop_thread_io import im_db_thread

logger = logging.getLogger("einsatzleiter.sms_inbox")


def _rule_matches(rule: SmsForwardRule, from_normalized: str) -> bool:
    pattern = telefon_kompakt(rule.match_number)
    if not pattern:
        return False
    if rule.match_type == "prefix":
        return from_normalized.startswith(pattern)
    return from_normalized == pattern


def record_inbound_sms(
    org_id: int,
    gateway_token_id: int | None,
    from_number: str,
    text: str,
) -> int:
    """Speichert eine empfangene SMS im Log. Rueckgabe: SmsInbox.id.

    Eigene DB-Session (unabhaengig vom WS-Handler-Request-Lifecycle). Der Log-Eintrag
    wird IMMER angelegt, unabhaengig davon ob Empfang/Regeln aktiviert sind.
    """
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        entry = SmsInbox(
            org_id=org_id,
            received_at=datetime.now(UTC),
            from_number=(from_number or "").strip(),
            text=text or "",
            gateway_token_id=gateway_token_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.id
    finally:
        db.close()


async def process_inbound_sms(inbox_id: int) -> None:
    """Wendet die erste passende aktive Regel an; Transporte bleiben auf dem Loop."""
    from app.services.sms_dispatch_service import send_bulk
    from app.services.teams_service import post_teams_karte

    try:
        daten = await im_db_thread(_inbound_auftrag_sync, inbox_id)
        if daten is None:
            return
        summary_parts: list[str] = []
        if daten["forward_teams"]:
            if daten["webhook"]:
                ok = await post_teams_karte(daten["webhook"], f"SMS von {daten['sender']}", daten["text"])
                summary_parts.append("Teams ✓" if ok else "Teams ✗")
            else:
                summary_parts.append("Teams: kein Webhook konfiguriert")
        if daten["jobs"]:
            total, success = await send_bulk(daten["org_id"], daten["jobs"])
            summary_parts.append(f"{success}/{total} SMS")
        summary = ", ".join(summary_parts) if summary_parts else "Regel ohne Ziele"
        await im_db_thread(_inbound_ergebnis_sync, inbox_id, daten, summary)
    except Exception:
        logger.exception("Fehler bei der Verarbeitung empfangener SMS (inbox_id=%d)", inbox_id)


def _inbound_auftrag_sync(inbox_id: int) -> dict | None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        entry = db.get(SmsInbox, inbox_id)
        if entry is None or entry.org_id is None:
            return None
        org_id = entry.org_id

        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        if not org_settings or not org_settings.sms_receive_enabled:
            entry.processed = True
            entry.forward_summary = "Empfang deaktiviert – nur geloggt"
            db.commit()
            return None

        from_norm = telefon_kompakt(entry.from_number)
        rules = (
            db.query(SmsForwardRule)
            .filter(SmsForwardRule.org_id == org_id, SmsForwardRule.enabled.is_(True))
            .order_by(SmsForwardRule.display_order, SmsForwardRule.id)
            .all()
        )
        rule = next((r for r in rules if _rule_matches(r, from_norm)), None)

        if rule is None:
            entry.processed = True
            entry.forward_summary = "Keine Regel getroffen"
            db.commit()
            return None

        # ── SMS-Weiterleitung (Gruppen + Mitglieder + Ad-hoc, dedupliziert) ───
        phones: dict[str, str] = {}
        for rg in rule.groups:
            grp = rg.group
            if not grp:
                continue
            for gm in grp.members:
                m = gm.member
                if m and m.active and m.phone:
                    norm = telefon_kompakt(m.phone)
                    if norm:
                        phones[norm] = m.full_name
        for rm in rule.members:
            m = rm.member
            if m and m.active and m.phone:
                norm = telefon_kompakt(m.phone)
                if norm:
                    phones[norm] = m.full_name
        for raw in (rule.forward_adhoc_numbers or "").split(","):
            norm = telefon_kompakt(raw)
            if norm:
                phones.setdefault(norm, raw.strip())

        return {
            "org_id": org_id, "rule_id": rule.id, "rule_name": rule.name,
            "forward_teams": rule.forward_teams,
            "webhook": rule.teams_webhook_url or org_settings.sms_receive_teams_webhook_url,
            "sender": entry.from_number, "text": entry.text,
            "jobs": [(phone, f"SMS von {entry.from_number}: {entry.text}" if rule.prepend_sender else entry.text)
                     for phone in phones],
        }
    finally:
        db.close()


def _inbound_ergebnis_sync(inbox_id: int, daten: dict, summary: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        entry = db.get(SmsInbox, inbox_id)
        if entry is None:
            return
        entry.matched_rule_id = daten["rule_id"]
        entry.processed = True
        entry.forward_summary = summary
        write_audit(
            db, "sms.inbound_forwarded", org_id=daten["org_id"],
            entity_type="sms_inbox", entity_id=entry.id,
            payload={"rule_id": daten["rule_id"], "rule_name": daten["rule_name"], "summary": summary},
        )
        db.commit()
    finally:
        db.close()
