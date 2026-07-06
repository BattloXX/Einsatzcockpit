"""Alarm-Infoscreen (Wandmonitor im Geraetehaus) — Objektverwaltung PR6.

- Oeffentliche Token-Route /infoscreen/alarm/{token} (Muster Wetter-Infoscreen):
  Vollbild, dunkel, keine Interaktion. Bei aktivem Alarm: Stichwort/Adresse gross,
  verknuepftes Objekt mit Gefahren-Piktogrammen, Karte mit Objektsymbolen,
  FSD/BMZ/FBF-Standorte. Sonst konfigurierbarer Idle-Modus (Uhr/Wetter/Einsatzliste).
- WS /ws/infoscreen/{token}: abonniert nach Token-Pruefung den Org-Kanal
  (incident_created/objekt_match → sofortiger Wechsel in die Alarmansicht).
- Verwaltung unter /infoscreen-alarm/verwaltung (org_admin): Tokens + Idle-Konfig.

Hinweis: Wohnanlagen-Hinweise (einsatztaktisch) werden auf Wunsch am Wandmonitor
angezeigt — der Token ist nur intern (Gerätehaus) bekannt.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.security import hash_api_key
from app.core.templating import templates
from app.db import get_db
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    AUSWAHL_PIKTOGRAMM,
    OBJEKT_EINSATZ_BESTAETIGT,
    AlarmInfoscreenToken,
    Objekt,
    ObjektEinsatz,
)
from app.models.user import User
from app.services.objekt_service import lade_auswahl

router = APIRouter(tags=["infoscreen-alarm"])

IDLE_MODI = {
    "uhr": "Uhr",
    "wetter": "Wetter-Infoscreen",
    "einsatzliste": "Letzte Einsätze",
}


def _token_org(db: Session, token: str) -> tuple[AlarmInfoscreenToken, FireDept]:
    """Validiert den Token (Hash-Lookup) und liefert Token-Zeile + Org."""
    token_hash = hash_api_key(token)
    eintrag = (
        db.query(AlarmInfoscreenToken)
        .execution_options(include_all_tenants=True)
        .filter(AlarmInfoscreenToken.token_hash == token_hash,
                AlarmInfoscreenToken.aktiv.is_(True))
        .first()
    )
    if eintrag is None or eintrag.org_id is None:
        raise HTTPException(status_code=401, detail="Ungueltiger oder gesperrter Token")
    org = db.query(FireDept).filter(FireDept.id == eintrag.org_id).first()
    if org is None:
        raise HTTPException(status_code=404)
    return eintrag, org


def _org_settings(db: Session, org_id: int) -> OrgSettings | None:
    return (
        db.query(OrgSettings)
        .execution_options(include_all_tenants=True)
        .filter(OrgSettings.org_id == org_id)
        .first()
    )


def _baue_idle_urls(db: Session, token_row: AlarmInfoscreenToken, wetter_url: str | None) -> list[dict]:
    """Rotations-URLs dieses Monitors (konfigurierte Reihenfolge) + optional Wetter."""
    from app.models.objekt import InfoscreenUrl

    urls: list[dict] = []
    ids = token_row.url_ids
    if ids:
        rows = (
            db.query(InfoscreenUrl)
            .filter(InfoscreenUrl.id.in_(ids), InfoscreenUrl.aktiv.is_(True))
            .execution_options(include_all_tenants=True)
            .all()
        )
        by_id = {r.id: r for r in rows}
        for i in ids:  # konfigurierte Reihenfolge erhalten
            r = by_id.get(i)
            if r is not None:
                urls.append({"label": r.label, "url": r.url, "dwell_sec": r.dwell_sec})
    if token_row.zeigt_wetter and wetter_url:
        urls.append({"label": "Wetter", "url": wetter_url, "dwell_sec": 30})
    return urls


def _aktive_gsl(db: Session, org_id: int):  # type: ignore[no-untyped-def]
    """Aktive Großschadenslage der Org (bleibt, solange aktiv) oder None."""
    from app.models.major_incident import MajorIncident, MajorIncidentStatus

    return (
        db.query(MajorIncident)
        .filter(MajorIncident.org_id == org_id,
                MajorIncident.status == MajorIncidentStatus.active)
        .order_by(MajorIncident.started_at.desc())
        .first()
    )


def _iso_z(dt):  # type: ignore[no-untyped-def]
    """Naive-UTC oder aware Datetime → ISO-String mit Z-Suffix (JS rechnet lokal um)."""
    if dt is None:
        return None
    from datetime import UTC
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.isoformat() + "Z"


# Phase → 3 Kanban-Gruppen (Wandmonitor: kompakter als die 6 Board-Spalten)
_GSL_PHASE_GRUPPE = {
    "eingegangen": "eingegangen",
    "erkundung":   "in_arbeit",
    "bewertet":    "in_arbeit",
    "disponiert":  "in_arbeit",
    "in_arbeit":   "in_arbeit",
    "erledigt":    "erledigt",
}
_GSL_PRIO_LETTER = {1: "S", 2: "D", 3: "N", 4: "A"}
# Prioritätsfarbe (Kartenmarker + Kartenkante), analog Lagekarte
_GSL_PRIO_COLOR = {1: "#ef4444", 2: "#f97316", 3: "#eab308", 4: "#6b7280"}


def _gsl_infoscreen_daten(db: Session, gsl) -> dict:  # type: ignore[no-untyped-def]
    """Reicher GSL-Datensatz für den Wandmonitor: KPIs, Einsatzstellen (Kanban +
    Kartenmarker), Abschnitte, übergreifende Meldungen (Ticker)."""
    from datetime import UTC, datetime

    from app.models.major_incident import (
        SITE_PRIORITY_LABEL,
        SitePhase,
        SitePriority,
    )

    now = datetime.now(UTC)
    sektor_namen = {s.id: s.name for s in gsl.sectors}

    sites = [s for s in gsl.sites if s.phase != SitePhase.abgebrochen]
    site_dicts: list[dict] = []
    ressourcen_aktiv = 0

    for s in sites:
        aktiv = sum(1 for r in s.resources if not r.released_at)
        ressourcen_aktiv += aktiv

        teile = []
        if s.strasse:
            teile.append(s.strasse + (f" {s.hausnr}" if s.hausnr else ""))
        if s.ort:
            teile.append(s.ort)
        adresse = ", ".join(teile)

        # Lagemeldung überfällig (SKKM-Regelkreis: naechste_lagemeldung_at in Vergangenheit)
        ueberfaellig_min = None
        if s.naechste_lagemeldung_at is not None and s.phase not in (
            SitePhase.erledigt, SitePhase.abgebrochen
        ):
            faellig = s.naechste_lagemeldung_at
            if faellig.tzinfo is None:
                faellig = faellig.replace(tzinfo=UTC)
            delta_min = int((now - faellig).total_seconds() // 60)
            if delta_min > 0:
                ueberfaellig_min = delta_min

        prio = int(s.priority) if s.priority is not None else None
        site_dicts.append({
            "id": s.id,
            "bezeichnung": s.bezeichnung,
            "einsatzgrund": s.einsatzgrund or "",
            "adresse": adresse,
            "phase": s.phase.value,
            "gruppe": _GSL_PHASE_GRUPPE.get(s.phase.value, "in_arbeit"),
            "prio": prio,
            "prio_letter": _GSL_PRIO_LETTER.get(prio, "") if prio else "",
            "prio_label": SITE_PRIORITY_LABEL.get(SitePriority(prio), "") if prio else "",
            "prio_color": _GSL_PRIO_COLOR.get(prio, "#6b7280") if prio else "#6b7280",
            "sektor": sektor_namen.get(s.sector_id) if s.sector_id else None,
            "aktive_res": aktiv,
            "res_labels": [r.label for r in s.resources if not r.released_at and r.label][:3],
            "lat": s.lat,
            "lng": s.lng,
            "zeit": _iso_z(s.created_at),
            "ueberfaellig_min": ueberfaellig_min,
        })

    # Sortierung: Priorität (S<D<N<A, ohne Prio zuletzt), dann neueste zuerst
    site_dicts.sort(key=lambda d: (d["prio"] or 9, d["zeit"] or ""), reverse=False)

    def _zaehle(gruppe: str) -> int:
        return sum(1 for d in site_dicts if d["gruppe"] == gruppe)

    kpi = {
        "total": len(site_dicts),
        "eingegangen": _zaehle("eingegangen"),
        "in_arbeit": _zaehle("in_arbeit"),
        "erledigt": _zaehle("erledigt"),
        "ressourcen": ressourcen_aktiv,
    }

    # Ticker: übergreifende Meldungen (Cross-Marker), offene zuerst
    ticker: list[str] = []
    for m in gsl.cross_site_markers:
        if m.status == "behoben":
            continue
        teil = m.title or m.type_label or "Meldung"
        if m.type_label and m.type_label != teil:
            teil += " · " + m.type_label
        if m.status_label:
            teil += " · " + m.status_label
        if m.address_line:
            teil += " · " + m.address_line
        ticker.append(teil)

    # Abschnitte: Legende + Polygon-Geometrie (GeoJSON) fuer die Karte
    import json as _json
    sektoren = []
    for s in gsl.sectors:
        geo = None
        if s.geometry:
            try:
                geo = _json.loads(s.geometry)
            except (ValueError, TypeError):
                geo = None
        sektoren.append({
            "name": s.name,
            "color": s.color or "#6b7280",
            "geometry": geo,
        })

    return {
        "name": gsl.name,
        "beschreibung": gsl.description or "",
        "beginn": _iso_z(gsl.started_at),
        "uebung": bool(gsl.is_exercise),
        "kpi": kpi,
        "sites": site_dicts,
        "sektoren": sektoren,
        "ticker": ticker,
    }


# ── Oeffentliche Infoscreen-Seite ──────────────────────────────────────────────

@router.get("/infoscreen/alarm/{token}", response_class=HTMLResponse, include_in_schema=False)
def infoscreen_seite(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _, org = _token_org(db, token)
    return templates.TemplateResponse(request, "objekt/infoscreen_alarm.html", {
        "org": org,
        "token": token,
    })


@router.get("/infoscreen/alarm/{token}/daten", include_in_schema=False)
def infoscreen_daten(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Aktueller Zustand als JSON (Polling-Fallback + Initial-Load)."""
    import json as _json

    from app.models.incident import Incident

    token_row, org = _token_org(db, token)
    settings_row = _org_settings(db, org.id)
    idle_modus = settings_row.alarm_infoscreen_idle_modus if settings_row else "uhr"
    wetter_url = settings_row.alarm_infoscreen_wetter_url if settings_row else None
    gsl_enabled = settings_row.alarm_infoscreen_gsl_enabled if settings_row else True

    from app.services.objekt_symbol_service import symbol_katalog_json
    daten: dict = {
        "org_name": org.name,
        "modus": "idle",
        "idle_modus": idle_modus,
        "wetter_url": wetter_url if idle_modus == "wetter" else None,
        # Frei konfigurierte Rotations-URLs dieses Monitors (Ruhezustand)
        "idle_urls": _baue_idle_urls(db, token_row, wetter_url),
        # Org-Symbolkatalog fuer das clientseitige Rendering (auch eigene Bildsymbole)
        "symbole": symbol_katalog_json(db, org.id),
    }

    # ── Vorrang 1: Großschadenslage (bleibt, solange aktiv) ──
    if gsl_enabled:
        gsl = _aktive_gsl(db, org.id)
        if gsl is not None:
            daten["modus"] = "gsl"
            daten["gsl"] = _gsl_infoscreen_daten(db, gsl)
            return daten

    # ── Vorrang 2: aktiver Einsatz (bleibt, solange status == active) ──
    incident = (
        db.query(Incident)
        .filter(
            Incident.primary_org_id == org.id,
            Incident.status == "active",
        )
        .order_by(Incident.started_at.desc())
        .first()
    )

    if incident is not None:
        adresse = " ".join(
            t for t in [incident.address_street, incident.address_no] if t
        )
        if incident.address_city:
            adresse = f"{adresse}, {incident.address_city}".strip(", ")
        # Rückmeldungen (Zu-/Absagen) inkl. Namen — das ist der eigene Wandmonitor
        # im Gerätehaus (Token nur intern bekannt), daher werden Namen angezeigt.
        from app.models.teilnahme import Teilnahme
        rsvp_rows = (
            db.query(Teilnahme)
            .filter(
                Teilnahme.org_id == org.id,
                Teilnahme.bezug_typ == "einsatz",
                Teilnahme.bezug_id == incident.id,
                Teilnahme.rsvp_status.isnot(None),
            )
            .execution_options(include_all_tenants=True)
            .all()
        )
        zusagen = sum(1 for t in rsvp_rows if t.rsvp_status == "zugesagt")
        absagen = sum(1 for t in rsvp_rows if t.rsvp_status == "abgesagt")
        rsvp_namen = [
            {"name": t.anzeige_name, "status": t.rsvp_status}
            for t in sorted(rsvp_rows, key=lambda x: (x.rsvp_status != "zugesagt", x.anzeige_name))
        ]

        # Stichwort-Klartext (z. B. F3 → "Brand Gebäude") aus dem Alarmtypen-Katalog
        from app.models.master import AlarmType
        at_row = (
            db.query(AlarmType)
            .filter(AlarmType.org_id == org.id, AlarmType.code == incident.alarm_type_code)
            .execution_options(include_all_tenants=True)
            .first()
        )
        stichwort_label = at_row.label.strip() if at_row and at_row.label else ""

        # ── Kräfte im Einsatz: Ausrückordnung als Grundgerüst + Live-Status ──
        # Angezeigt wird die vollständige Ausrückordnung (AAO) des Stichworts.
        # Fahrzeuge, die noch nicht ausgerückt sind (kein IncidentVehicle), stehen
        # laut AAO auf der Wache → Status S2. Sobald das LIS einen Status meldet
        # (bzw. das Fahrzeug im Einsatz geführt wird), wird der reale Status gezogen
        # (unit_status → S-Code: S4 zum Einsatzort, S5 am Einsatzort, S1 einsatzbereit).
        # Nachalarmierte Fahrzeuge außerhalb der AAO werden zusätzlich angehängt.
        from app.models.incident import IncidentVehicle
        from app.models.master import AlarmDispatchVehicle, VehicleMaster
        from app.services.lis.lis_mapping import unit_status_to_lis_prefix

        iv_rows = (
            db.query(IncidentVehicle)
            .filter(IncidentVehicle.incident_id == incident.id,
                    IncidentVehicle.removed_at.is_(None))
            .order_by(IncidentVehicle.display_order, IncidentVehicle.created_at)
            .all()
        )
        iv_by_vm: dict[int, IncidentVehicle] = {}
        for v in iv_rows:
            iv_by_vm.setdefault(v.vehicle_master_id, v)

        # AAO des Stichworts (explizit gepflegt), sonst Erstausrückung (is_first_train)
        roster: list[VehicleMaster] = []
        if at_row is not None:
            dispatch = (
                db.query(AlarmDispatchVehicle)
                .options(selectinload(AlarmDispatchVehicle.vehicle))
                .filter(AlarmDispatchVehicle.alarm_type_id == at_row.id)
                .order_by(AlarmDispatchVehicle.display_order)
                .execution_options(include_all_tenants=True)
                .all()
            )
            roster = [d.vehicle for d in dispatch if d.vehicle and d.vehicle.active]
            if not roster:
                q = (
                    db.query(VehicleMaster)
                    .filter(VehicleMaster.dept_id == org.id,
                            VehicleMaster.active.is_(True))
                    .order_by(VehicleMaster.display_order)
                    .execution_options(include_all_tenants=True)
                )
                if at_row.default_first_train_only:
                    q = q.filter(VehicleMaster.is_first_train.is_(True))
                roster = q.all()

        def _scode(iv: IncidentVehicle | None) -> str:
            if iv is None:
                return "S2"  # laut Ausrückordnung disponiert, noch auf der Wache
            return unit_status_to_lis_prefix(iv.unit_status) or "S2"

        def _eintrag(vm: VehicleMaster | None, iv: IncidentVehicle | None) -> dict:
            return {
                "rufname": vm.display_label if vm else "–",
                "typ": (vm.type or vm.name) if vm else "",
                "org": vm.org_display_name if vm else "",
                "status_code": _scode(iv),
                "extern": bool(vm and (vm.is_external or vm.is_adhoc)),
                "geplant": iv is None,
            }

        fahrzeuge = []
        gesehen: set[int] = set()
        for vm in roster:
            if vm.id in gesehen:
                continue
            gesehen.add(vm.id)
            fahrzeuge.append(_eintrag(vm, iv_by_vm.get(vm.id)))
        for v in iv_rows:  # Nachalarmierte / nicht in der AAO
            if v.vehicle_master_id in gesehen:
                continue
            gesehen.add(v.vehicle_master_id)
            fahrzeuge.append(_eintrag(v.vehicle_master, v))

        daten.update({
            "modus": "alarm",
            "fahrzeuge": fahrzeuge,
            "incident": {
                "id": incident.id,
                "stichwort": incident.alarm_type_code,
                "stichwort_label": stichwort_label,
                "adresse": adresse,
                "meldung": incident.report_text or incident.reason or "",
                "beginn": incident.started_at.isoformat() + "Z" if incident.started_at else None,
                "lat": incident.lat,
                "lng": incident.lng,
                "rsvp": {"zusagen": zusagen, "absagen": absagen, "namen": rsvp_namen},
            },
        })

        # Verknuepftes Objekt (bestaetigt bevorzugt, sonst erster Vorschlag)
        verknuepfung = (
            db.query(ObjektEinsatz)
            .options(
                selectinload(ObjektEinsatz.objekt).selectinload(Objekt.gefahren),
                selectinload(ObjektEinsatz.objekt).selectinload(Objekt.bma),
                selectinload(ObjektEinsatz.objekt).selectinload(Objekt.karten_objekte),
                selectinload(ObjektEinsatz.objekt).selectinload(Objekt.wohnanlage),
            )
            .execution_options(include_all_tenants=True)
            .filter(ObjektEinsatz.incident_id == incident.id,
                    ObjektEinsatz.org_id == org.id)
            .order_by(ObjektEinsatz.status)  # "bestaetigt" < "vorschlag"
            .first()
        )
        if verknuepfung is not None and verknuepfung.objekt is not None:
            o = verknuepfung.objekt
            piktogramme = lade_auswahl(db, org.id, AUSWAHL_PIKTOGRAMM)
            bma = o.bma
            karten = []
            for k in o.karten_objekte:
                geometry = None
                if k.geometry_json:
                    try:
                        geometry = _json.loads(k.geometry_json)
                    except (ValueError, TypeError):
                        geometry = None
                karten.append({
                    "typ": k.typ, "lat": k.lat, "lng": k.lng,
                    "geometry": geometry, "label": k.label,
                })
            daten["objekt"] = {
                "nummer": o.anzeige_nummer,
                "name": o.name,
                "vulgoname": o.vulgoname,
                "bestaetigt": verknuepfung.status == OBJEKT_EINSATZ_BESTAETIGT,
                "lat": o.lat,
                "lng": o.lng,
                "gefahren": [
                    {
                        "name": g.gefahr.name if g.gefahr else "",
                        "piktogramm": piktogramme.get(
                            g.gefahr.piktogramm_typ if g.gefahr else "sonstig", "⚠️"
                        )[:2].strip(),
                        "un_nummer": g.un_nummer,
                    }
                    for g in o.gefahren
                ],
                "bma": {
                    "bma_nummer": bma.bma_nummer,
                    "bmz_standort": bma.bmz_standort,
                    "fbf_standort": bma.fbf_standort,
                    "laufkarten_ablageort": bma.laufkarten_ablageort,
                    "fsd_standort": bma.schluesselsafe_standort if bma.schluesselsafe_vorhanden else None,
                    "fsd_inhalt": bma.schluesselsafe_inhalt if bma.schluesselsafe_vorhanden else None,
                } if bma else None,
                "karten_objekte": karten,
                # Wohnanlagen-Hinweise (einsatztaktisch) — auf Wunsch am Wandmonitor sichtbar
                "wohnanlage_hinweise": (
                    o.wohnanlage.hinweise.strip()
                    if o.wohnanlage and o.wohnanlage.hinweise else None
                ),
            }

    # Idle: letzte Einsaetze (reduziert: Stichwort/Adresse/Zeit)
    if daten["modus"] == "idle" and idle_modus == "einsatzliste":
        letzte = (
            db.query(Incident)
            .filter(Incident.primary_org_id == org.id)
            .order_by(Incident.started_at.desc())
            .limit(5)
            .all()
        )
        daten["einsaetze"] = [
            {
                "stichwort": i.alarm_type_code,
                "adresse": " ".join(t for t in [i.address_street, i.address_no] if t),
                "beginn": i.started_at.isoformat() + "Z" if i.started_at else None,
            }
            for i in letzte
        ]

    return daten


@router.get("/infoscreen/alarm/{token}/hydranten.json", include_in_schema=False)
async def infoscreen_hydranten(
    token: str,
    db: Session = Depends(get_db),
):
    """Löschwasser-Entnahmestellen um den aktiven Einsatz (OSM + manuelle Objekt-Symbole).

    Separater async-Endpunkt, damit die (netzgebundene) Overpass-Abfrage nicht den
    synchronen /daten-Poll blockiert. Bezugspunkt ist bevorzugt das verknüpfte
    Objekt, sonst die Einsatzkoordinate.
    """
    from app.config import settings
    from app.models.incident import Incident
    from app.models.objekt import ObjektKartenObjekt
    from app.services.hydrant_service import (
        fetch_osm_hydranten,
        manuelle_objekt_hydranten,
        merge_hydranten,
    )

    _, org = _token_org(db, token)
    settings_row = _org_settings(db, org.id)
    if not settings.HYDRANT_ENABLED or (settings_row and not settings_row.hydrant_layer_enabled):
        return {"hydranten": []}

    incident = (
        db.query(Incident)
        .filter(Incident.primary_org_id == org.id, Incident.status == "active")
        .order_by(Incident.started_at.desc())
        .first()
    )
    if incident is None:
        return {"hydranten": []}

    verknuepfung = (
        db.query(ObjektEinsatz)
        .options(selectinload(ObjektEinsatz.objekt))
        .execution_options(include_all_tenants=True)
        .filter(ObjektEinsatz.incident_id == incident.id, ObjektEinsatz.org_id == org.id)
        .order_by(ObjektEinsatz.status)  # "bestaetigt" < "vorschlag"
        .first()
    )
    objekt = verknuepfung.objekt if verknuepfung else None
    ref_lat = objekt.lat if objekt and objekt.lat is not None else incident.lat
    ref_lng = objekt.lng if objekt and objekt.lng is not None else incident.lng
    if ref_lat is None or ref_lng is None:
        return {"hydranten": []}

    osm = await fetch_osm_hydranten(ref_lat, ref_lng)
    manuell: list = []
    if objekt is not None:
        karten = (
            db.query(ObjektKartenObjekt)
            .execution_options(include_all_tenants=True)
            .filter(ObjektKartenObjekt.objekt_id == objekt.id,
                    ObjektKartenObjekt.typ.in_(("hydrant_ueberflur", "hydrant_unterflur")))
            .all()
        )
        manuell = manuelle_objekt_hydranten(karten, ref_lat, ref_lng)
    return {"hydranten": merge_hydranten(osm, manuell)}


# In-Memory-Cache für den Wetter-Badge (org_id → (timestamp, daten)); TTL 10 min.
_wetter_cache: dict[int, tuple[float, dict]] = {}


@router.get("/infoscreen/alarm/{token}/wetter.json", include_in_schema=False)
async def infoscreen_wetter(
    token: str,
    db: Session = Depends(get_db),
):
    """Kleiner Wetter-Badge für den Kopf (Open-Meteo, frei/kostenlos, kein Key).

    Bezugspunkt sind die Org-Fallback-Koordinaten (Gerätehaus). Ohne Koordinaten
    oder bei Fehler → leeres Objekt, der Badge bleibt dann ausgeblendet.
    """
    import time

    import httpx

    _, org = _token_org(db, token)
    lat, lng = org.fallback_lat, org.fallback_lng
    if lat is None or lng is None:
        return {}

    now = time.time()
    cached = _wetter_cache.get(org.id)
    if cached and (now - cached[0]) < 600:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lng,
                    "current": "temperature_2m,wind_speed_10m,weather_code",
                    "timezone": "auto",
                },
            )
            resp.raise_for_status()
            cur = resp.json().get("current", {})
    except Exception:
        return {}

    def _r(v):  # type: ignore[no-untyped-def]
        return round(v) if isinstance(v, (int, float)) else None

    daten = {
        "temp": _r(cur.get("temperature_2m")),
        "wind": _r(cur.get("wind_speed_10m")),
        "code": cur.get("weather_code"),
    }
    _wetter_cache[org.id] = (now, daten)
    return daten


# ── WebSocket: Org-Kanal nach Token-Pruefung ──────────────────────────────────

@router.websocket("/ws/infoscreen/{token}")
async def infoscreen_ws(websocket: WebSocket, token: str):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.broadcast import ORG_WS_OFFSET, manager

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        try:
            _, org = _token_org(db, token)
        except HTTPException:
            await websocket.close(code=4401)
            return
        org_id = org.id
    finally:
        db.close()

    channel = ORG_WS_OFFSET + org_id
    await manager.connect(channel, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await manager.disconnect(channel, websocket)


# ── Verwaltung (org_admin) ─────────────────────────────────────────────────────

@router.get("/infoscreen-alarm/verwaltung", response_class=HTMLResponse)
def verwaltung(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    from app.core.crypto import decrypt_secret
    from app.models.objekt import InfoscreenUrl

    tokens = (
        db.query(AlarmInfoscreenToken)
        .order_by(AlarmInfoscreenToken.erstellt_am.desc())
        .all()
    )
    urls = (
        db.query(InfoscreenUrl)
        .order_by(InfoscreenUrl.sort, InfoscreenUrl.label)
        .all()
    )
    basis_url = str(request.base_url).rstrip("/")
    # Dauerhaft anzeigbare Monitor-URL aus dem verschluesselten Token (nur org_admin)
    token_urls: dict[int, str] = {}
    for t in tokens:
        if t.token_enc:
            try:
                token_urls[t.id] = f"{basis_url}/infoscreen/alarm/{decrypt_secret(t.token_enc)}"
            except Exception:
                pass
    settings_row = _org_settings(db, user.org_id) if user.org_id else None
    return templates.TemplateResponse(request, "objekt/infoscreen_verwaltung.html", {
        "user": user,
        "tokens": tokens,
        "urls": urls,
        "token_urls": token_urls,
        "einstellungen": settings_row,
        "idle_modi": IDLE_MODI,
        "neuer_token": request.query_params.get("neuer_token"),
        "basis_url": basis_url,
    })


@router.post("/infoscreen-alarm/verwaltung/token/neu")
def token_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    name: str = Form(...),
):
    from app.core.crypto import encrypt_secret
    token = secrets.token_urlsafe(32)
    db.add(AlarmInfoscreenToken(
        org_id=user.org_id,
        token_hash=hash_api_key(token),
        token_enc=encrypt_secret(token),  # dauerhaft anzeigbare Monitor-URL
        name=name.strip() or "Infoscreen",
        aktiv=True,
    ))
    write_audit(db, "objekt.infoscreen_token_created", org_id=user.org_id, user_id=user.id,
                payload={"name": name.strip()})
    db.commit()
    # Token einmalig im Klartext anzeigen (danach nur noch Hash gespeichert)
    return RedirectResponse(
        url=f"/infoscreen-alarm/verwaltung?neuer_token={token}", status_code=303
    )


@router.post("/infoscreen-alarm/verwaltung/token/{token_id}/deaktivieren")
def token_deaktivieren(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    eintrag = db.query(AlarmInfoscreenToken).filter(AlarmInfoscreenToken.id == token_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Token nicht gefunden")
    eintrag.aktiv = False
    write_audit(db, "objekt.infoscreen_token_deactivated", org_id=user.org_id, user_id=user.id,
                payload={"name": eintrag.name})
    db.commit()
    return RedirectResponse(url="/infoscreen-alarm/verwaltung", status_code=303)


@router.post("/infoscreen-alarm/verwaltung/einstellungen")
def einstellungen_speichern(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    idle_modus: str = Form("uhr"),
    alarm_dauer_min: int = Form(60),
    wetter_url: str = Form(""),
    gsl_enabled: str = Form(""),
):
    if idle_modus not in IDLE_MODI:
        idle_modus = "uhr"
    settings_row = _org_settings(db, user.org_id) if user.org_id else None
    if settings_row is None:
        raise HTTPException(status_code=404, detail="Org-Einstellungen nicht gefunden")
    settings_row.alarm_infoscreen_idle_modus = idle_modus
    settings_row.alarm_infoscreen_alarm_dauer_min = max(5, min(alarm_dauer_min, 720))
    settings_row.alarm_infoscreen_wetter_url = wetter_url.strip() or None
    settings_row.alarm_infoscreen_gsl_enabled = bool(gsl_enabled)
    db.commit()
    return RedirectResponse(url="/infoscreen-alarm/verwaltung?saved=1", status_code=303)


# ── Rotations-URLs (InfoscreenUrl) + Monitor-Matrix ────────────────────────────

@router.post("/infoscreen-alarm/verwaltung/url/neu")
def url_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    label: str = Form(...),
    url: str = Form(...),
    dwell_sec: int = Form(30),
    sort: int = Form(0),
):
    from app.models.objekt import InfoscreenUrl

    ziel = url.strip()
    if not ziel:
        raise HTTPException(status_code=400, detail="URL ist erforderlich")
    if not (ziel.startswith("http://") or ziel.startswith("https://")):
        ziel = "https://" + ziel
    db.add(InfoscreenUrl(
        org_id=user.org_id, label=label.strip() or ziel, url=ziel,
        dwell_sec=max(3, min(dwell_sec, 3600)), sort=sort, aktiv=True,
    ))
    db.commit()
    return RedirectResponse(url="/infoscreen-alarm/verwaltung?saved=1", status_code=303)


@router.post("/infoscreen-alarm/verwaltung/url/{url_id}/loeschen")
def url_loeschen(
    url_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    from app.models.objekt import InfoscreenUrl

    eintrag = db.query(InfoscreenUrl).filter(InfoscreenUrl.id == url_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="URL nicht gefunden")
    db.delete(eintrag)
    # Verweise in den Monitor-Zuordnungen mit entfernen
    for t in db.query(AlarmInfoscreenToken).all():
        ids = t.url_ids
        if url_id in ids:
            import json as _json
            t.url_ids_json = _json.dumps([i for i in ids if i != url_id])
    db.commit()
    return RedirectResponse(url="/infoscreen-alarm/verwaltung?saved=1", status_code=303)


@router.post("/infoscreen-alarm/verwaltung/token/{token_id}/monitor")
def token_monitor_speichern(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    url_id: list[int] = Form(default=[]),
    zeigt_wetter: str = Form(""),
):
    """Speichert die Matrix-Auswahl (welche URLs + Wetter) eines Monitors."""
    import json as _json

    from app.models.objekt import InfoscreenUrl

    token = db.query(AlarmInfoscreenToken).filter(AlarmInfoscreenToken.id == token_id).first()
    if token is None:
        raise HTTPException(status_code=404, detail="Monitor nicht gefunden")
    gueltige = {r.id for r in db.query(InfoscreenUrl).all()}
    gewaehlt = [i for i in url_id if i in gueltige]
    token.url_ids_json = _json.dumps(gewaehlt)
    token.zeigt_wetter = bool(zeigt_wetter)
    db.commit()
    return RedirectResponse(url="/infoscreen-alarm/verwaltung?saved=1", status_code=303)
