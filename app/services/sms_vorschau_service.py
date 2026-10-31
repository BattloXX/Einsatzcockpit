"""Lesende SMS-Vorschau fuer die letzten Einsaetze einer Organisation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.incident import Incident, IncidentOrg
from app.models.incident_alarm_field import IncidentAlarmField
from app.models.master import AlarmType, FireDept, OrgSettings
from app.models.sms import SmsLog
from app.services.alarm_outbox import _address
from app.services.sms_dispatch_service import baue_einsatzinfo_text, einsatzinfo_vorlage

# Erweiterungszeichen belegen zwei Septette (Escape + Zeichen).
_GSM7 = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./"
    "0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_ERWEITERT = frozenset("\f^{}\\[~]|€")


@dataclass(frozen=True)
class SmsTextVorschau:
    text: str
    zeichen: int
    segmente: int
    zeichensatz: str
    herkunft: str


def sms_text_vorschau(text: str, herkunft: str = "Beispiel mit aktueller Vorlage") -> SmsTextVorschau:
    """Zaehlt GSM-Septette bzw. UTF-16-Einheiten fuer die SMS-Segmentierung."""
    gsm = all(c in _GSM7 or c in _GSM7_ERWEITERT for c in text)
    einheiten = sum(2 if c in _GSM7_ERWEITERT else 1 for c in text) if gsm else len(text.encode("utf-16-be")) // 2
    einzeln, verkettet = (160, 153) if gsm else (70, 67)
    segmente = (1 if einheiten <= einzeln else (einheiten + verkettet - 1) // verkettet) if text else 0
    return SmsTextVorschau(text, len(text), segmente, "GSM-7" if gsm else "Unicode", herkunft)


@dataclass(frozen=True)
class EinsatzSmsVorschau:
    nummer: int
    zeit: datetime
    stichwort: str
    erstalarm: SmsTextVorschau
    nachalarm: SmsTextVorschau


def lade_sms_vorschau(db: Session, org_id: int) -> list[EinsatzSmsVorschau]:
    """Liest nur eigene oder gemeinsam gefuehrte Einsaetze und eigene SMS-Protokolle."""
    incidents = db.query(Incident).filter(or_(
        Incident.primary_org_id == org_id,
        Incident.collaborating_orgs.any(IncidentOrg.org_id == org_id),
    )).order_by(Incident.started_at.desc(), Incident.id.desc()).limit(5).all()
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    alarm_types = {a.code: a for a in db.query(AlarmType).filter(AlarmType.org_id == org_id).all()}
    dept = db.get(FireDept, org_id)
    result = []
    for incident in incidents:
        # Versand nutzt den Versandzeitpunkt; ohne Protokoll dient der Einsatzbeginn
        # als nachvollziehbarer Beispielzeitpunkt fuer {datum} und {zeit}.
        now = incident.started_at.replace(tzinfo=UTC)
        try:
            local_now = now.astimezone(ZoneInfo((dept.timezone if dept else None) or "Europe/Vienna"))
        except Exception:
            local_now = now
        link = (
            f"{settings.effective_public_base_url.rstrip('/')}/alarm/{incident.alarm_token}"
            if incident.alarm_token else ""
        )
        template = einsatzinfo_vorlage(alarm_types.get(incident.alarm_type_code), org_settings)

        render = partial(
            baue_einsatzinfo_text,
            template=template, alarm_type_code=incident.alarm_type_code, address=_address(incident),
            ort=incident.address_city, meldung=incident.report_text, einsatzgrund=incident.reason,
            local_now=local_now, is_exercise=incident.is_exercise, link=link,
            anmeldelink=f"{link}/anmelden" if link and incident.access_pin_hash else "",
            leitstellennummer=incident.lis_operation_number,
        )

        # Nur Text-/Statusspalten laden, keine Empfaenger oder Benutzerbeziehungen.
        logs = db.query(SmsLog.text, SmsLog.variant, SmsLog.success_count).filter(
            SmsLog.org_id == org_id, SmsLog.incident_id == incident.id, SmsLog.source == "alarm",
        ).order_by(SmsLog.sent_at.asc(), SmsLog.id.asc()).all()
        first = next((log for log in logs if log.variant == ""), None)
        changed = next((log for log in reversed(logs) if log.variant != ""), None)

        def aus_log(log) -> SmsTextVorschau:
            return sms_text_vorschau(
                log.text, "Tatsächlich gesendet" if log.success_count > 0 else "Versandprotokoll (Versand unbestätigt)",
            )

        codes_row = db.query(IncidentAlarmField.value).filter(
            IncidentAlarmField.org_id == org_id, IncidentAlarmField.incident_id == incident.id,
            IncidentAlarmField.field == "codes",
        ).first()
        codes = []
        if codes_row and codes_row.value:
            try:
                value = json.loads(codes_row.value)
                if isinstance(value, list):
                    codes = [c for c in value if isinstance(c, str) and c.strip()]
            except (ValueError, TypeError):
                codes = []
        prefix = "NACHALARMIERUNG " + (", ".join(codes) if codes else "Beispiel-Code") + " "
        result.append(EinsatzSmsVorschau(
            nummer=incident.nummer or incident.id, zeit=incident.started_at, stichwort=incident.alarm_type_code,
            erstalarm=aus_log(first) if first else sms_text_vorschau(render()),
            nachalarm=aus_log(changed) if changed else sms_text_vorschau(render(text_prefix=prefix)),
        ))
    return result
