"""Einzige Freigabegrenze für öffentliche Proben: ausschließlich Positivlisten."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.timezones import org_tz
from app.models.master import FireDept, OrgSettings
from app.models.probenplanung import Probeart, ProbePublicToken
from app.models.teilnahme import Termin
from app.services.probenplanung_service import probenplanung_system_enabled


@dataclass(frozen=True)
class OeffentlicheProbe:
    datum: date
    beginn: datetime
    ende: datetime | None
    ganztaegig: bool
    probeart_name: str
    probeart_farbe: str | None
    thema: str | None
    objekt: str | None
    ort: str | None
    info: str | None
    status_abgesagt: bool


@dataclass(frozen=True)
class OeffentlicherKalendereintrag:
    """Nur Protokollmetadaten neben der fachlichen Positivliste; niemals ORM."""
    probe: OeffentlicheProbe
    uid: str
    sequence: int
    last_modified: datetime


def oeffentliche_proben(db: Session, token: str) -> tuple[OeffentlicherKalendereintrag, ...]:
    row = (db.query(ProbePublicToken)
           .execution_options(include_all_tenants=True)
           .filter(ProbePublicToken.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                   ProbePublicToken.widerrufen_am.is_(None)).first())
    if row is None or row.art not in {"plan", "ics"}:
        raise HTTPException(404, "Nicht gefunden")
    org_settings = (db.query(OrgSettings).execution_options(include_all_tenants=True)
                    .filter(OrgSettings.org_id == row.org_id).first())
    org = (db.query(FireDept).execution_options(include_all_tenants=True)
           .filter(FireDept.id == row.org_id).first())
    if (org is None or not probenplanung_system_enabled(db) or org_settings is None
            or not org_settings.probenplanung_modul_aktiv or not org_settings.probenplanung_public_aktiv):
        raise HTTPException(404, "Nicht gefunden")
    tz = org_tz(org)
    # Spalten statt ORM-Entitäten laden. Auch der Join ist explizit tenantgebunden.
    query = (db.query(
        Termin.beginn, Termin.ende, Termin.ganztaegig, Probeart.name, Probeart.farbe,
        Termin.thema, Termin.titel, Termin.objekt, Termin.ort, Termin.info,
        Termin.public_ort_sichtbar, Termin.public_info_sichtbar, Termin.status,
        Termin.ics_uid, Termin.ics_sequence, Termin.geaendert_am, Termin.erstellt_am,
    ).outerjoin(Probeart, and_(Probeart.id == Termin.probeart_id, Probeart.org_id == row.org_id))
        .execution_options(include_all_tenants=True)
        .filter(Termin.org_id == row.org_id, Termin.public_sichtbar.is_(True),
                Termin.status != "entwurf", Termin.archiviert_am.is_(None),
                Termin.ics_uid.is_not(None)))
    if row.termin_id is not None:
        query = query.filter(Termin.id == row.termin_id)
    if row.filter_probeart_ids is not None:
        try:
            ids = json.loads(row.filter_probeart_ids)
            if not isinstance(ids, list) or any(type(i) is not int for i in ids):
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(404, "Nicht gefunden") from None
        query = query.filter(Termin.probeart_id.in_(ids))
    result = []
    for t in query.order_by(Termin.beginn).all():
        beginn = t.beginn.replace(tzinfo=UTC).astimezone(tz)
        ende = t.ende.replace(tzinfo=UTC).astimezone(tz) if t.ende else None
        if row.jahr is not None and beginn.year != row.jahr:
            continue
        probe = OeffentlicheProbe(
            datum=beginn.date(), beginn=beginn, ende=ende, ganztaegig=t.ganztaegig,
            probeart_name=t.name or "Probe",
            probeart_farbe=t.farbe if t.farbe and re.fullmatch(r"#[0-9a-fA-F]{6}", t.farbe) else None,
            thema=t.thema or t.titel, objekt=t.objekt,
            ort=t.ort if t.public_ort_sichtbar else None,
            info=t.info if t.public_info_sichtbar else None, status_abgesagt=t.status == "abgesagt",
        )
        result.append(OeffentlicherKalendereintrag(
            probe, t.ics_uid, t.ics_sequence,
            (t.geaendert_am or t.erstellt_am).replace(tzinfo=UTC),
        ))
    row.zuletzt_genutzt_am = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    return tuple(result)
