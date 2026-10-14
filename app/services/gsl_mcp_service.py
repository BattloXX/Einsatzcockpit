"""Datensparsame, rein lesende Projektionen fuer die GSL-MCP-Werkzeuge."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.models.major_incident import (
    CitizenReport,
    CommLogEntry,
    CrossSiteMarker,
    EinheitSiteDispatch,
    IncidentSite,
    LageJournalEntry,
    MajorIncident,
    SitePhase,
    SitePriority,
)
from app.services import gsl_fuehrung_service, lagemeldung_service, resource_service, verleih_service


def _text(html: str | None) -> str | None:
    """Macht Journal-HTML fuer MCP zu harmlosem Klartext."""
    if not html:
        return None
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html))).strip()


def _adresse(site: IncidentSite) -> str:
    parts = []
    if site.strasse:
        parts.append(site.strasse + (f" {site.hausnr}" if site.hausnr else ""))
    if site.ort:
        parts.append(site.ort)
    return ", ".join(parts)


def lage_kopf(lage: MajorIncident) -> dict[str, Any]:
    return {
        "id": lage.id,
        "bezeichnung": lage.name,
        "status": lage.status.value,
        "beginn": lage.started_at,
        "ende": lage.ended_at,
        "uebung": lage.is_exercise,
    }


def lagen_liste(db: Session, org_id: int, status: str, limit: int) -> list[dict[str, Any]]:
    query = db.query(MajorIncident).filter(MajorIncident.org_id == org_id)
    if status == "aktiv":
        query = query.filter(MajorIncident.status == "active")
    elif status == "beendet":
        query = query.filter(MajorIncident.status == "closed")
    lagen = query.order_by(MajorIncident.started_at.desc()).limit(limit).all()
    result = []
    for lage in lagen:
        sites = lage.sites
        offen = sum(site.phase not in (SitePhase.erledigt, SitePhase.abgebrochen) for site in sites)
        einheiten = sum(
            1
            for site in sites
            for dispatch in site.dispatched_einheiten
            if dispatch.withdrawn_at is None and dispatch.abgezogen_at is None and dispatch.beendet_at is None
        )
        meldungen = (
            db.query(CitizenReport)
            .filter(CitizenReport.major_incident_id == lage.id, CitizenReport.status == "new")
            .count()
        )
        result.append(
            {
                **lage_kopf(lage),
                "stellen_offen": offen,
                "stellen_gesamt": len(sites),
                "einheiten_im_einsatz": einheiten,
                "offene_buergermeldungen": meldungen,
            }
        )
    return result


def einsatzstellen_liste(
    db: Session,
    lage: MajorIncident,
    *,
    phase: str | None = None,
    prio: int | None = None,
    abschnitt_id: int | None = None,
    suchtext: str = "",
    inklusive_erledigte: bool = False,
) -> list[dict[str, Any]]:
    sites = sorted(lage.sites, key=lambda site: (site.sort_index, site.id))
    chips = resource_service.get_einheiten_chips_for_sites(
        db, sites, org_settings=lagemeldung_service.org_settings_for(db, lage.org_id)
    )
    sectors = {sector.id: sector for sector in lage.sectors}
    needle = suchtext.casefold().strip()
    result = []
    for site in sites:
        if not inklusive_erledigte and site.phase in (SitePhase.erledigt, SitePhase.abgebrochen):
            continue
        if phase and site.phase.value != phase:
            continue
        if prio is not None and (site.priority is None or site.priority.value != prio):
            continue
        if abschnitt_id is not None and site.sector_id != abschnitt_id:
            continue
        if (
            needle
            and needle not in " ".join(filter(None, (site.bezeichnung, site.einsatzgrund, _adresse(site)))).casefold()
        ):
            continue
        chip = chips.get(site.id, {})
        result.append(
            {
                "id": site.id,
                "bezeichnung": site.bezeichnung,
                "einsatzgrund": site.einsatzgrund,
                "adresse": _adresse(site),
                "lat": site.lat,
                "lng": site.lng,
                "phase": site.phase.value,
                "prio": site.priority.value if site.priority else None,
                "abschnitt": None
                if not site.sector_id
                else {"id": site.sector_id, "bezeichnung": sectors[site.sector_id].name},
                "einheiten": chip.get("chips", []),
                "nur_erkundet": chip.get("nur_erkundet", False),
                "alle_fertig": chip.get("alle_fertig", False),
                "lagemeldung_faellig_um": site.naechste_lagemeldung_at,
                "lagemeldung_ueberfaellig": bool(
                    site.naechste_lagemeldung_at
                    and site.naechste_lagemeldung_at <= datetime.now(UTC).replace(tzinfo=None)
                ),
                "foto_anzahl": len(site.media),
            }
        )
    return result


def _quelle(author: str | None) -> str | None:
    """Autorennamen niemals ausgeben; bekannte maschinelle Quellen bleiben nutzbar."""
    value = (author or "").casefold()
    for source in ("board", "tablet", "funk", "mcp", "system"):
        if source in value:
            return source
    return None


def _log_text(item: Any) -> str:
    """Blendet aus Bürgermeldungen übernommene Melder-Kontaktdaten aus."""
    text = item.text or ""
    if item.kind == "note" and (text.startswith("Anrufer: ") or text.startswith("Tel.: ")):
        return "[Kontaktdaten der Bürgermeldung ausgeblendet]"
    return text


def _medien(medien: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": medium.id,
            "dateiname": medium.original_filename,
            "typ": medium.media_type,
            "hochgeladen_am": medium.uploaded_at,
            "groesse": medium.bytes,
        }
        for medium in medien
    ]


def einsatzstelle_lesen(
    db: Session, lage: MajorIncident, site: IncidentSite, bereiche: set[str], *, material_aktiv: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "allgemein" in bereiche:
        result["allgemein"] = next(
            row for row in einsatzstellen_liste(db, lage, inklusive_erledigte=True) if row["id"] == site.id
        )
    if "auftraege" in bereiche:
        dispatches = (
            db.query(EinheitSiteDispatch)
            .options(joinedload(EinheitSiteDispatch.einheit))
            .filter(EinheitSiteDispatch.site_id == site.id)
            .order_by(EinheitSiteDispatch.reihenfolge, EinheitSiteDispatch.dispatched_at)
            .all()
        )
        result["auftraege"] = [
            {
                "dispatch_id": item.id,
                "einheit": {"id": item.einheit_id, "label": item.einheit.label if item.einheit else "Einheit"},
                "auftragsart": item.auftragsart,
                "status": item.einheit_status,
                "reihenfolge": item.reihenfolge,
                "auftrag": item.auftrag,
                "bestaetigt": item.bestaetigt_at,
                "vor_ort": item.vor_ort_at,
                "beendet": item.beendet_at,
                "withdrawn": item.withdrawn_at,
                "abgezogen": item.abgezogen_at,
            }
            for item in dispatches
        ]
    if "log" in bereiche:
        result["log"] = [
            {
                "id": item.id, "zeit": item.ts, "art": item.kind, "text": _log_text(item),
                "quelle": _quelle(item.author_name),
            }
            for item in site.log_entries
        ]
    if "medien" in bereiche:
        result["medien"] = _medien(site.media)
    if "material" in bereiche and material_aktiv:
        ausleihen = [item for item in verleih_service.get_ausleihen_fuer_lage(db, lage.id) if item.site_id == site.id]
        result["material"] = [
            {
                "id": item.id,
                "status": item.status.value,
                "ausgeliehen_am": item.ausgeliehen_at,
                "positionen": [
                    {"bezeichnung": pos.bezeichnung, "menge": pos.menge, "status": pos.status.value}
                    for pos in item.positionen
                ],
            }
            for item in ausleihen
        ]
    if "meldungen" in bereiche:
        result["meldungen"] = _meldungen(db, lage, site.id, "alle")
    return result


def kraefteuebersicht(db: Session, lage: MajorIncident) -> dict[str, Any]:
    raw = resource_service.kraefteuebersicht(db, lage)

    def einheit(item: Any) -> dict[str, Any]:
        return {
            "id": item.id,
            "label": item.label,
            "typ": item.resource_type,
            "status": item.status,
            "abschnitt_id": item.sector_id,
        }

    return {
        "gesamt": raw["total"],
        "frei": raw["reserve_count"],
        "im_einsatz": raw["im_einsatz_count"],
        "pool": [einheit(item) for item in raw["pool"]],
        "im_einsatz_ohne_abschnitt": [einheit(item) for item in raw["im_einsatz_no_sector"]],
        "abgerueckt": [einheit(item) for item in raw["abgerueckt"]],
        "abschnitte": [
            {
                "id": item["sector"].id,
                "bezeichnung": item["sector"].name,
                "einheiten": [einheit(row) for row in item["einheiten"]],
            }
            for item in raw["sectors"]
        ],
    }


def abschnitte(lage: MajorIncident, *, mit_geometrie: bool) -> list[dict[str, Any]]:
    result = []
    for sector in sorted(lage.sectors, key=lambda item: item.sort_order):
        sites = [site for site in lage.sites if site.sector_id == sector.id]
        active = sum(
            1
            for site in sites
            for dispatch in site.dispatched_einheiten
            if dispatch.withdrawn_at is None and dispatch.abgezogen_at is None and dispatch.beendet_at is None
        )
        item: dict[str, Any] = {
            "id": sector.id,
            "bezeichnung": sector.name,
            "farbe": sector.color,
            "leitung_funktion": "Abschnittsleitung" if sector.leader_assignment_id else None,
            "stellen_anzahl": len(sites),
            "einheiten_aktiv": active,
        }
        if mit_geometrie:
            item["geometrie"] = json.loads(sector.geometry) if sector.geometry else None
        result.append(item)
    return result


def journal(
    db: Session,
    lage: MajorIncident,
    *,
    quellen: set[str],
    seit: datetime | None,
    kategorie: str | None,
    nur_offen: bool,
    limit: int,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if "lagejournal" in quellen:
        query = db.query(LageJournalEntry).filter(LageJournalEntry.major_incident_id == lage.id)
        if seit:
            query = query.filter(LageJournalEntry.ts >= seit)
        if kategorie:
            query = query.filter(LageJournalEntry.category == kategorie)
        for item in query.all():
            entries.append(
                {
                    "id": item.id,
                    "quelle": item.quelle or "board",
                    "zeit": item.ts,
                    "kategorie": item.category,
                    "text": item.text,
                    "inhalt": _text(item.body_html),
                    "site_id": item.site_id,
                    "medien": _medien(item.media),
                }
            )
    if "funkjournal" in quellen:
        comm_query = db.query(CommLogEntry).filter(CommLogEntry.major_incident_id == lage.id)
        if seit:
            comm_query = comm_query.filter(CommLogEntry.ts >= seit)
        if nur_offen:
            comm_query = comm_query.filter(CommLogEntry.is_request.is_(True), CommLogEntry.handled.is_(False))
        for comm in comm_query.all():
            entries.append(
                {
                    "id": comm.id,
                    "quelle": "funk",
                    "zeit": comm.ts,
                    "kategorie": "funk",
                    "richtung": comm.direction,
                    "kanal": comm.channel,
                    "partner": comm.partner,
                    "text": comm.message,
                    "site_id": comm.related_site_id,
                    "offen": comm.is_request and not comm.handled,
                }
            )
    return sorted(entries, key=lambda item: item["zeit"], reverse=True)[:limit]


def _meldungen(db: Session, lage: MajorIncident, site_id: int | None, status: str) -> list[dict[str, Any]]:
    query = db.query(CitizenReport).filter(CitizenReport.major_incident_id == lage.id)
    if site_id is not None:
        query = query.filter(CitizenReport.site_id == site_id)
    if status != "alle":
        query = query.filter(CitizenReport.status == status)
    return [
        {
            "id": item.id,
            "status": item.status,
            "ort": item.ort,
            "strasse": item.strasse,
            "lat": item.lat,
            "lng": item.lng,
            "text": item.description,
            "foto_vorhanden": bool(item.photo_filename),
            "site_id": item.site_id,
            "kontakt_vorhanden": bool(item.reporter_contact),
            "telefon_verifiziert": item.phone_verified,
            "erstellt_am": item.created_at,
        }
        for item in query.order_by(CitizenReport.created_at.desc()).all()
    ]


def meldungen(db: Session, lage: MajorIncident, status: str) -> list[dict[str, Any]]:
    return _meldungen(db, lage, None, status)


def uebergreifend_liste(db: Session, lage: MajorIncident, status: str) -> list[dict[str, Any]]:
    query = db.query(CrossSiteMarker).filter(CrossSiteMarker.major_incident_id == lage.id)
    if status != "alle":
        query = query.filter(CrossSiteMarker.status == status)
    return [
        {
            "id": item.id,
            "titel": item.title,
            "typ": item.marker_type,
            "status": item.status,
            "ort": item.ort,
            "adresse": item.address_line,
            "lat": item.lat,
            "lng": item.lng,
            "beschreibung": item.description,
            "log_anzahl": len(item.log_entries),
            "medien": _medien(item.media),
            "erstellt_am": item.created_at,
        }
        for item in query.order_by(CrossSiteMarker.sort_index, CrossSiteMarker.id).all()
    ]


def lagebild(db: Session, lage: MajorIncident, *, funkjournal_aktiv: bool, meldungen_aktiv: bool) -> dict[str, Any]:
    sites = lage.sites
    phases = {phase.value: sum(site.phase == phase for site in sites) for phase in SitePhase}
    prios = {
        priority.value: sum(
            site.priority == priority and site.phase not in (SitePhase.erledigt, SitePhase.abgebrochen)
            for site in sites
        )
        for priority in SitePriority
    }
    kue = kraefteuebersicht(db, lage)
    quellen = {"lagejournal"} | ({"funkjournal"} if funkjournal_aktiv else set())
    result: dict[str, Any] = {
        "lage": lage_kopf(lage),
        "phasen": phases,
        "prioritaeten": prios,
        "fuehrungs_hinweise": gsl_fuehrung_service.fuehrungs_hinweise(db, lage),
        "kraefte_kurz": {key: kue[key] for key in ("frei", "im_einsatz")},
        "offene_stellen_prio_1_2": einsatzstellen_liste(db, lage, inklusive_erledigte=False, prio=1)
        + einsatzstellen_liste(db, lage, inklusive_erledigte=False, prio=2),
        "journal": journal(db, lage, quellen=quellen, seit=None, kategorie=None, nur_offen=False, limit=15),
    }
    if meldungen_aktiv:
        result["offene_buergermeldungen"] = meldungen(db, lage, "new")
    return result
