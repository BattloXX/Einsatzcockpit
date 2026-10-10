"""Lesesicht fuer die Detailkarte einer Ressource."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.major_incident import (
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    LageJournalEntry,
    MajorIncident,
    SiteLogEntry,
)
from app.models.master import FireDept, VehicleMaster
from app.services import einheit_service, resource_service


@dataclass(frozen=True)
class JournalZeile:
    ts: datetime
    ereignis_typ: str
    text: str
    quelle: str | None
    autor: str | None
    site_id: int | None
    dispatch_id: int | None
    ref: tuple[str, int]
    storniert: bool
    storno_grund: str | None


def _naiv(wert: datetime | None) -> datetime | None:
    if wert is None:
        return None
    return wert.replace(tzinfo=None) if wert.tzinfo is not None else wert


def _zeit(wert: datetime | None) -> datetime:
    """Zeitstempel persistierter Journal- und Dispatch-Zeilen sind nie NULL."""
    assert wert is not None
    return _naiv(wert) or wert


def _einsatznummer(db: Session, site: IncidentSite) -> str:
    incident = db.get(Incident, site.incident_id) if site.incident_id else None
    if incident and incident.lis_operation_number:
        return incident.lis_operation_number
    return site.external_key or f"S{site.id}"


def _einsatz(db: Session, dispatch: EinheitSiteDispatch) -> dict:
    site = dispatch.site
    beendet_at = _naiv(dispatch.beendet_at)
    vor_ort_at = _naiv(dispatch.vor_ort_at)
    dispatched_at = _naiv(dispatch.dispatched_at)
    dauer_sekunden = None
    if beendet_at is not None:
        start = vor_ort_at or dispatched_at
        assert start is not None
        dauer_sekunden = int((beendet_at - start).total_seconds())
    letzte_lagemeldung = (
        db.query(SiteLogEntry)
        .filter(
            SiteLogEntry.incident_site_id == dispatch.site_id,
            SiteLogEntry.einheit_id == dispatch.einheit_id,
            SiteLogEntry.kind == "lagemeldung",
        )
        .order_by(SiteLogEntry.ts.desc(), SiteLogEntry.id.desc())
        .first()
    )
    massnahmen = (
        db.query(SiteLogEntry)
        .filter(
            SiteLogEntry.incident_site_id == dispatch.site_id,
            SiteLogEntry.einheit_id == dispatch.einheit_id,
            SiteLogEntry.kind == "massnahmen",
        )
        .order_by(SiteLogEntry.ts.desc(), SiteLogEntry.id.desc())
        .limit(10)
        .all()
    )
    return {
        "dispatch_id": dispatch.id,
        "site_id": site.id,
        "bezeichnung": site.bezeichnung,
        "auftrag": dispatch.auftrag,
        "einsatznummer": _einsatznummer(db, site),
        "dispatched_at": dispatched_at,
        "bestaetigt_at": _naiv(dispatch.bestaetigt_at),
        "vor_ort_at": vor_ort_at,
        "beendet_at": beendet_at,
        "beendet_grund": dispatch.beendet_grund,
        "letzte_rueckmeldung_at": _naiv(dispatch.letzte_rueckmeldung_at),
        "dauer_sekunden": dauer_sekunden,
        "withdrawn_at": _naiv(dispatch.withdrawn_at),
        "withdrawn_grund": dispatch.withdrawn_grund,
        "withdrawn_author": dispatch.withdrawn_author,
        "letzte_lagemeldung": (
            {"ts": _naiv(letzte_lagemeldung.ts), "text": letzte_lagemeldung.text}
            if letzte_lagemeldung else None
        ),
        "massnahmen": [
            {"ts": _naiv(eintrag.ts), "text": eintrag.text} for eintrag in massnahmen
        ],
    }


def einsaetze(db: Session, lage: MajorIncident, einheit: LageEinheit) -> dict:
    """Vollstaendige, um Kartendetails ergaenzte Auftragshistorie."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    vehicle = db.get(VehicleMaster, einheit.vehicle_id) if einheit.vehicle_id else None
    ctx = einheit_service.EinheitKontext(
        device_token=None, vehicle=vehicle, einheit=einheit, lage=lage,
        org_id=lage.org_id, quelle="funk",
    )
    kategorien = einheit_service.auftraege_fuer_einheit(db, ctx)["kategorien"]
    def laden(wert: dict) -> dict:
        dispatch = db.get(EinheitSiteDispatch, wert["dispatch_id"])
        assert dispatch is not None
        return _einsatz(db, dispatch)

    return {
        "aktuell": laden(kategorien["aktuell"]) if kategorien["aktuell"] else None,
        "weitere": [laden(wert) for wert in kategorien["weitere"]],
        "abgeschlossen": [laden(wert) for wert in kategorien["abgeschlossen"]],
        "zurueckgezogen": [laden(wert) for wert in kategorien["zurueckgezogen"]],
    }


def journal(
    db: Session, lage: MajorIncident, einheit: LageEinheit, *, typen: set[str] | None = None,
    site_id: int | None = None, vor_ts: datetime | None = None, limit: int = 50,
) -> list[JournalZeile]:
    """Fuehrt Ressourcen-, Stellen- und fehlende Dispositionsereignisse zusammen."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    zeilen: list[JournalZeile] = []
    eintraege = db.query(LageJournalEntry).filter(
        LageJournalEntry.major_incident_id == lage.id,
        LageJournalEntry.einheit_id == einheit.id,
        LageJournalEntry.category.in_(resource_service.RESSOURCE_CATEGORIES),
    )
    for entry in eintraege:
        zeilen.append(JournalZeile(
            _zeit(entry.ts), entry.ereignis_typ or entry.category, entry.text, entry.quelle,
            entry.author_name, entry.site_id, None, ("lage_journal_entry", entry.id),
            entry.storniert_at is not None, entry.storno_grund,
        ))
    logs = db.query(SiteLogEntry).join(IncidentSite).filter(
        IncidentSite.major_incident_id == lage.id, SiteLogEntry.einheit_id == einheit.id,
    )
    typ_by_kind = {
        "lagemeldung": "lagemeldung", "massnahmen": "massnahmen", "note": "meldung",
        "einheit": "status", "media": "medien",
    }
    for site_log in logs:
        typ = typ_by_kind.get(site_log.kind, site_log.kind)
        zeilen.append(JournalZeile(
            _zeit(site_log.ts), typ, site_log.text, None, site_log.author_name,
            site_log.incident_site_id, None, ("site_log_entry", site_log.id), False, None,
        ))
    vorhandene = {(entry.site_id, entry.ereignis_typ) for entry in eintraege}
    dispatches = db.query(EinheitSiteDispatch).join(IncidentSite).filter(
        EinheitSiteDispatch.einheit_id == einheit.id, IncidentSite.major_incident_id == lage.id,
    )
    for dispatch in dispatches:
        for typ, ts, text in (
            ("disponiert", dispatch.dispatched_at, "Disposition erteilt"),
            ("uebernommen", dispatch.bestaetigt_at, "Auftrag übernommen"),
            ("begonnen", dispatch.vor_ort_at, "Einsatz begonnen"),
            ("abgeschlossen", dispatch.beendet_at, "Einsatz abgeschlossen"),
            ("zurueckgezogen", dispatch.withdrawn_at, "Auftrag zurückgezogen"),
        ):
            if ts is not None and (dispatch.site_id, typ) not in vorhandene:
                zeilen.append(JournalZeile(
                    _zeit(ts), typ, text, "system", dispatch.author_name, dispatch.site_id,
                    dispatch.id, ("einheit_site_dispatch", dispatch.id), False, None,
                ))
    if typen is not None:
        zeilen = [zeile for zeile in zeilen if zeile.ereignis_typ in typen]
    if site_id is not None:
        zeilen = [zeile for zeile in zeilen if zeile.site_id == site_id]
    vor_ts = _naiv(vor_ts)
    if vor_ts is not None:
        zeilen = [zeile for zeile in zeilen if zeile.ts < vor_ts]
    zeilen.sort(key=lambda zeile: (zeile.ts, zeile.ref), reverse=True)
    return zeilen[:limit]


def kommunikation(db: Session, lage: MajorIncident, einheit: LageEinheit) -> dict:
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    letzte_lagemeldung = db.query(SiteLogEntry).join(IncidentSite).filter(
        IncidentSite.major_incident_id == lage.id, SiteLogEntry.einheit_id == einheit.id,
        SiteLogEntry.kind == "lagemeldung",
    ).order_by(SiteLogEntry.ts.desc(), SiteLogEntry.id.desc()).first()
    aktive = db.query(EinheitSiteDispatch).join(IncidentSite).filter(
        IncidentSite.major_incident_id == lage.id, EinheitSiteDispatch.einheit_id == einheit.id,
        EinheitSiteDispatch.withdrawn_at.is_(None), EinheitSiteDispatch.beendet_at.is_(None),
    )
    nicht_quittiert = aktive.filter(EinheitSiteDispatch.einheit_status == "zugewiesen").all()
    uebernommen = aktive.filter(EinheitSiteDispatch.bestaetigt_at.is_not(None)).all()
    return {
        "letzte_lagemeldung": (
            {"ts": _naiv(letzte_lagemeldung.ts), "text": letzte_lagemeldung.text,
             "site_id": letzte_lagemeldung.incident_site_id}
            if letzte_lagemeldung else None
        ),
        "letzte_statusaenderung": _naiv(einheit.status_at),
        "nicht_quittierte_auftraege": {
            "anzahl": len(nicht_quittiert),
            "aeltester_dispatched_at": _naiv(min((d.dispatched_at for d in nicht_quittiert), default=None)),
        },
        "uebernommene_auftraege": len(uebernommen),
        "offene_anforderungen": [],
        "gk_aktivitaet": None,
    }


def _leader(leader: LageEinheitLeader | None) -> dict | None:
    if leader is None:
        return None
    return {
        "name": leader.display_name, "member_id": leader.member_id,
        "extern": leader.member_id is None, "phone": leader.phone,
        "phone_e164": leader.phone_e164, "start_at": _naiv(leader.start_at),
    }


def karte(db: Session, lage: MajorIncident, einheit: LageEinheit) -> dict:
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    vehicle = db.get(VehicleMaster, einheit.vehicle_id) if einheit.vehicle_id else None
    org = db.get(FireDept, lage.org_id)
    site = db.get(IncidentSite, einheit.incident_site_id) if einheit.incident_site_id else None
    current = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    stellvertreter = db.query(LageEinheitLeader).filter(
        LageEinheitLeader.einheit_id == einheit.id,
        LageEinheitLeader.rolle == "stellvertreter", LageEinheitLeader.end_at.is_(None),
    ).first()
    historie = db.query(LageEinheitLeader).filter(
        LageEinheitLeader.einheit_id == einheit.id, LageEinheitLeader.rolle == "fuehrer",
    ).order_by(LageEinheitLeader.start_at.desc(), LageEinheitLeader.id.desc()).all()
    return {
        "allgemein": {
            "label": einheit.label,
            "org_name": einheit.org_name or (org.display_name if org else None),
            "org_display": einheit.org_name or (org.display_name if org else None),
            "resource_type": einheit.resource_type,
            "vehicle_code": vehicle.code if vehicle else None, "vehicle_name": vehicle.name if vehicle else None,
            "kennzeichen": vehicle.kennzeichen if vehicle else None, "funkrufname": einheit.funkrufname,
            "status": einheit.status, "sector": einheit.sector,
            "standort": site.bezeichnung if site else einheit.bereitstellungsraum,
            "bereitstellung_at": _naiv(einheit.arrived_at or einheit.added_at),
            "status_at": _naiv(einheit.status_at),
        },
        "gruppenkommandant": {
            "current": _leader(current), "stellvertreter": _leader(stellvertreter),
            "historie": [_leader(leader) for leader in historie],
        },
        "einsaetze": einsaetze(db, lage, einheit),
        "kommunikation": kommunikation(db, lage, einheit),
    }
