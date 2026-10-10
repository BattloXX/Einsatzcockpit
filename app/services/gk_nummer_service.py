"""SMS-Bestätigung einer vom Gruppenkommandanten selbst eingegebenen Nummer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.security import generate_numeric_pin, hash_api_key
from app.core.telefon import telefon_maske, telefon_zu_e164_at
from app.models.major_incident import LageEinheitLeader, LageEinheitNummerVerifikation
from app.models.sms import SmsLog, SmsLogRecipient
from app.services.exercise_guard import darf_extern
from app.services.gk_zugang_service import AutoSmsAuftrag, plane_auto_sms, widerrufe
from app.services.sms_service import send_sms, sms_available


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class NummerErgebnis:
    status: str
    maske: str | None = None
    auto_sms: AutoSmsAuftrag | None = None
    sitzung_beendet: bool = False


def _leader(db: Session, ctx) -> LageEinheitLeader:
    """Aktueller Leader der Einheit; QR-Sitzungen sind an keinen Leader gebunden."""
    leader = db.get(LageEinheitLeader, ctx.einheit.leader_assignment_id) if ctx.einheit.leader_assignment_id else None
    if not leader or leader.einheit_id != ctx.einheit.id or leader.end_at is not None or leader.rolle != "fuehrer":
        raise ValueError("Kein aktueller Gruppenkommandant")
    return leader


async def starte_verifikation(db: Session, ctx_principal, telefon_roh: str) -> NummerErgebnis:
    """Startet einen Codeversand; Klartextcode bleibt nur bis zum Provideraufruf im Speicher."""
    phone = telefon_zu_e164_at(telefon_roh)
    if not phone:
        raise ValueError("Ungültige Mobilnummer")
    leader = _leader(db, ctx_principal)
    now = _now()
    recent = db.query(LageEinheitNummerVerifikation).filter(
        LageEinheitNummerVerifikation.einheit_id == ctx_principal.einheit.id,
        LageEinheitNummerVerifikation.org_id == ctx_principal.org_id,
        LageEinheitNummerVerifikation.created_at >= now - timedelta(minutes=10),
    ).count()
    if recent >= 3:
        raise ValueError("Zu viele Code-Anforderungen. Bitte später erneut versuchen.")
    if not sms_available(ctx_principal.org_id, db):
        raise ValueError("Kein SMS-Anbieter verbunden")
    if not darf_extern("sms", is_exercise=ctx_principal.lage.is_exercise, org_id=ctx_principal.org_id, db=db):
        raise ValueError("Übung: SMS unterdrückt")
    for old in db.query(LageEinheitNummerVerifikation).filter(
        LageEinheitNummerVerifikation.einheit_id == ctx_principal.einheit.id,
        LageEinheitNummerVerifikation.verbraucht_at.is_(None),
    ).all():
        old.verbraucht_at = now
    code = generate_numeric_pin()
    row = LageEinheitNummerVerifikation(
        org_id=ctx_principal.org_id, lage_id=ctx_principal.lage.id, einheit_id=ctx_principal.einheit.id,
        leader_id=leader.id, phone_version=leader.phone_version, phone_e164_neu=phone, code_hash=hash_api_key(code),
        gueltig_bis=now + timedelta(minutes=10), zugang_id=ctx_principal.zugang.id if ctx_principal.zugang else None,
        created_at=now,
    )
    db.add(row)
    db.flush()
    try:
        result = await send_sms(ctx_principal.org_id, phone, f"GSL-Bestätigungscode: {code}")
    except Exception:
        result = None
    success = bool(result and result.success)
    # SmsLog deliberately records no code/message body for this security-sensitive SMS.
    log = SmsLog(
        org_id=ctx_principal.org_id, source="gk_nummer", text="GSL-Bestätigungscode: ******",
        recipient_count=1, success_count=1 if success else 0, provider=getattr(result, "provider", None),
        completed_at=now,
    )
    log.recipients.append(SmsLogRecipient(phone_number=phone, name="Gruppenkommandant", success=success,
                                           sent_at=now, provider=getattr(result, "provider", None)))
    db.add(log)
    write_audit(db, "gsl.gk.nummer_code_angefordert", org_id=ctx_principal.org_id,
                entity_type="lage_einheit", entity_id=ctx_principal.einheit.id,
                payload={"lage_id": ctx_principal.lage.id, "einheit_id": ctx_principal.einheit.id})
    db.commit()
    if not success:
        raise ValueError("Der Code konnte nicht gesendet werden. Bitte die Einsatzleitung informieren.")
    return NummerErgebnis("gesendet", telefon_maske(phone))


def bestaetige_verifikation(db: Session, ctx_principal, code: str) -> NummerErgebnis:
    now = _now()
    row = db.query(LageEinheitNummerVerifikation).filter(
        LageEinheitNummerVerifikation.einheit_id == ctx_principal.einheit.id,
        LageEinheitNummerVerifikation.org_id == ctx_principal.org_id,
        LageEinheitNummerVerifikation.verbraucht_at.is_(None),
    ).order_by(LageEinheitNummerVerifikation.created_at.desc()).first()
    if not row:
        raise ValueError("Code ungültig oder abgelaufen.")
    leader = _leader(db, ctx_principal)
    if row.leader_id != leader.id or row.phone_version != leader.phone_version:
        row.verbraucht_at = now
        db.commit()
        raise ValueError("Führung hat gewechselt – bitte erneut starten")
    if row.gesperrt_bis and row.gesperrt_bis > now:
        raise ValueError("Zu viele Fehlversuche. Bitte später erneut versuchen.")
    if row.gueltig_bis <= now or hash_api_key(code or "") != row.code_hash:
        row.versuche += 1
        if row.versuche >= 5:
            row.gesperrt_bis = now + timedelta(minutes=15)
        db.commit()
        raise ValueError("Code ungültig oder abgelaufen.")
    row.verbraucht_at = now
    leader.phone, leader.phone_e164 = row.phone_e164_neu, row.phone_e164_neu
    leader.phone_version += 1
    leader.phone_verifiziert_at = now
    from app.services.resource_service import _journal
    text = (
        f"{ctx_principal.einheit.label}: Telefonnummer vom Gruppenkommandanten bestätigt "
        f"({telefon_maske(leader.phone_e164)})"
    )
    _journal(
        db, ctx_principal.lage.id, text, category="ressource_fhr", einheit_id=ctx_principal.einheit.id,
        ereignis_typ="gk_telefon", quelle="gk_selbst",
    )
    widerrufe(db, ctx_principal.einheit.id, grund="telefon", user_id=None, typ="personal")
    auto = plane_auto_sms(db, ctx_principal.lage, ctx_principal.einheit, leader, "telefon")
    write_audit(
        db, "gsl.gk.nummer_bestaetigt", org_id=ctx_principal.org_id, entity_type="lage_einheit",
        entity_id=ctx_principal.einheit.id,
        payload={"lage_id": ctx_principal.lage.id, "einheit_id": ctx_principal.einheit.id},
    )
    db.commit()
    return NummerErgebnis("bestaetigt", telefon_maske(leader.phone_e164), auto,
                          bool(ctx_principal.zugang and ctx_principal.zugang.typ == "personal"))
