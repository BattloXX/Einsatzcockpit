"""PDF-Lagebericht (Phase 2, F12): Kopfdaten + Kartenausschnitt + Legende + Kräfteübersicht
+ Chronologie eines Einsatzes.

Muster: app/services/objekt_pdf_service.py (Karten-PNG via staticmap, Data-URI-Einbettung)
+ app/services/pdf_service.py::render_incident_pdf (WeasyPrint mit xhtml2pdf-Fallback).
"""
from __future__ import annotations

import base64
import io
import json
import logging

from sqlalchemy.orm import Session

from app.core.templating import templates
from app.core.timezones import format_local_datetime
from app.models.incident import Incident, IncidentVehicle
from app.models.lagefuehrung import LagefuehrungEvent, LagefuehrungFeature
from app.models.master import FireDept
from app.services.tz_service import tz_symbol_name

logger = logging.getLogger("einsatzleiter.lagefuehrung_pdf")

# Vereinfachte Farbzuordnung für die Kartenapproximation (staticmap kann keine SVGs
# rendern) — dieselbe Vereinfachung wie beim Objektblatt (objekt_pdf_service.py).
_TYP_FARBE = {
    "taktisches_zeichen": "#2563eb",
    "meldung": "#f59e0b",
    "distanz": "#6b7280",
    "zeichnung": "#e53e3e",
    "marker": "#e53e3e",
}

_EVENT_LABELS = {
    "feature.created": "Element angelegt",
    "feature.updated": "Element bearbeitet",
    "feature.deleted": "Element gelöscht",
    "fuehrer.changed": "Lageführung übernommen",
    "berechtigung.erteilt": "Editor-Recht vergeben",
    "berechtigung.entzogen": "Editor-Recht entzogen",
    "snapshot.erstellt": "Momentaufnahme erstellt",
    "vehicle.pinned": "Fahrzeug manuell platziert",
}


def _event_label(e: LagefuehrungEvent) -> str:
    return _EVENT_LABELS.get(e.event_typ, e.event_typ)


def render_lagefuehrung_map_png(
    incident: Incident,
    features: list[LagefuehrungFeature],
    vehicles_positions: list[tuple[IncidentVehicle, float | None, float | None]],
    objekt_marker: tuple[float, float] | None,
    *, size: tuple[int, int] = (640, 400),
) -> bytes | None:
    """Statische OSM-Karte: Einsatzort + Fahrzeuge + Objekt + Feature-Punkte als farbige Kreise.

    Vereinfachung für den Druck (wie objekt_pdf_service.render_objekt_map_png): Symbole
    werden als Farbkreise approximiert, die Legende löst die Farben auf. None ohne
    Koordinaten/bei Fehler — Aufrufer behandeln die Karte als optionalen Baustein.
    """
    if incident.lat is None or incident.lng is None:
        return None
    try:
        from staticmap import CircleMarker, StaticMap  # noqa: PLC0415

        karte = StaticMap(
            size[0], size[1],
            url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
            headers={"User-Agent": "Einsatzcockpit (Lagebericht-Druck)"},
        )
        karte.add_marker(CircleMarker((incident.lng, incident.lat), "#d42225", 16))

        if objekt_marker:
            karte.add_marker(CircleMarker((objekt_marker[1], objekt_marker[0]), "#2563eb", 12))

        for _vehicle, lat, lng in vehicles_positions:
            if lat is None or lng is None:
                continue
            karte.add_marker(CircleMarker((lng, lat), "#f6ad55", 10))

        for f in features:
            try:
                geo = json.loads(f.geometry)
            except (ValueError, TypeError):
                continue
            farbe = _TYP_FARBE.get(f.typ, "#e53e3e")
            coords = geo.get("coordinates") if isinstance(geo, dict) else None
            if geo.get("type") == "Point" and coords:
                karte.add_marker(CircleMarker((coords[0], coords[1]), farbe, 9))
            elif geo.get("type") == "LineString" and coords:
                # staticmap kennt keine benannten Linien-Marker — Mittelpunkt approximieren
                mid = coords[len(coords) // 2]
                karte.add_marker(CircleMarker((mid[0], mid[1]), farbe, 7))

        bild = karte.render(zoom=16)
        buf = io.BytesIO()
        bild.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.exception("Statische Lagekarte fehlgeschlagen (Einsatz %d)", incident.id)
        return None


def gather_map_render_context(incident: Incident, db: Session) -> dict:
    """Sammelt Features/Fahrzeugpositionen/Objekt-Marker für render_lagefuehrung_map_png().

    Eigene Funktion (statt nur Teil von _load_lagebericht_context), weil die Momentaufnahme
    (Phase 3, ui_lagefuehrung.py::lagefuehrung_momentaufnahme) dieselbe Kartenkachel wie der
    PDF-Lagebericht rendert, aber weder Events noch die Zeichen-Legende braucht.
    """
    features = (
        db.query(LagefuehrungFeature)
        .filter(
            LagefuehrungFeature.incident_id == incident.id,
            LagefuehrungFeature.deleted_at.is_(None),
        )
        .order_by(LagefuehrungFeature.created_at)
        .all()
    )

    from app.models.major_incident import VehiclePosition

    vehicles = (
        db.query(IncidentVehicle)
        .filter(IncidentVehicle.incident_id == incident.id, IncidentVehicle.removed_at.is_(None))
        .all()
    )
    vehicle_master_ids = [v.vehicle_master_id for v in vehicles]
    latest_position: dict[int, VehiclePosition] = {}
    if vehicle_master_ids and incident.primary_org_id:
        rows = (
            db.query(VehiclePosition)
            .filter(
                VehiclePosition.vehicle_id.in_(vehicle_master_ids),
                VehiclePosition.org_id == incident.primary_org_id,
            )
            .order_by(VehiclePosition.vehicle_id, VehiclePosition.received_at.desc())
            .all()
        )
        for row in rows:
            if row.vehicle_id is not None:
                latest_position.setdefault(row.vehicle_id, row)
    vehicles_positions = [
        (v, latest_position[v.vehicle_master_id].lat if v.vehicle_master_id in latest_position else None,
         latest_position[v.vehicle_master_id].lon if v.vehicle_master_id in latest_position else None)
        for v in vehicles
    ]

    objekt_marker = None
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz
    ov = (
        db.query(ObjektEinsatz)
        .filter(
            ObjektEinsatz.incident_id == incident.id,
            ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT,
        )
        .first()
    )
    if ov and ov.objekt and ov.objekt.lat is not None and ov.objekt.lng is not None:
        objekt_marker = (ov.objekt.lat, ov.objekt.lng)

    return {
        "features": features,
        "vehicles_positions": vehicles_positions,
        "objekt_marker": objekt_marker,
    }


def _load_lagebericht_context(incident: Incident, db: Session) -> dict:
    primary_org = db.get(FireDept, incident.primary_org_id) if incident.primary_org_id else None
    map_ctx = gather_map_render_context(incident, db)
    features = map_ctx["features"]

    events = (
        db.query(LagefuehrungEvent)
        .filter(LagefuehrungEvent.incident_id == incident.id)
        .order_by(LagefuehrungEvent.ts)
        .all()
    )

    # Legende: genutzte taktische Zeichen + Farbschlüssel der Kartenapproximation
    zeichen_legende = sorted({
        (f.zeichen_key, tz_symbol_name(f.zeichen_key) or f.zeichen_key)
        for f in features if f.typ == "taktisches_zeichen" and f.zeichen_key
    }, key=lambda t: t[1] or "")

    return {
        "primary_org": primary_org,
        "features": features,
        "vehicles_positions": map_ctx["vehicles_positions"],
        "objekt_marker": map_ctx["objekt_marker"],
        "events": events,
        "zeichen_legende": zeichen_legende,
    }


def render_lagefuehrung_pdf(incident: Incident, db: Session, base_url: str = "") -> bytes:
    ctx = _load_lagebericht_context(incident, db)
    template = templates.env.get_template("pdf/lagebericht.html")

    fuehrer_name = None
    if incident.lagefuehrung_fuehrer_user_id:
        from app.models.user import User
        fuehrer = db.get(User, incident.lagefuehrung_fuehrer_user_id)
        fuehrer_name = fuehrer.display_name if fuehrer else None

    karte_png = render_lagefuehrung_map_png(
        incident, ctx["features"], ctx["vehicles_positions"], ctx["objekt_marker"],
    )
    karte_datauri = (
        "data:image/png;base64," + base64.b64encode(karte_png).decode("ascii")
        if karte_png else None
    )

    html_str = template.render(
        incident=incident,
        org=ctx["primary_org"],
        fuehrer_name=fuehrer_name,
        karte_datauri=karte_datauri,
        vehicles_positions=ctx["vehicles_positions"],
        zeichen_legende=ctx["zeichen_legende"],
        events=ctx["events"],
        event_label=_event_label,
        format_local_datetime=format_local_datetime,
        base_url=base_url,
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("WeasyPrint fehlgeschlagen (Lagebericht), Fallback auf xhtml2pdf: %s", exc)
        from xhtml2pdf import pisa  # noqa: PLC0415

        from app.services.pdf_service import strip_font_face_for_xhtml2pdf  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html_str)), dest=buf)
        return buf.getvalue()
