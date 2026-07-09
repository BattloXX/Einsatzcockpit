"""PDF generation via WeasyPrint (mit xhtml2pdf-Fallback)."""
import base64
import io
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.config import settings
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept

logger = logging.getLogger("einsatzleiter.pdf")

_FONT_FACE_RE = re.compile(r"@font-face\s*\{.*?\}", re.DOTALL)


def strip_font_face_for_xhtml2pdf(html_str: str) -> str:
    """xhtml2pdf/reportlab kann @font-face mit data:-URIs nicht laden (versucht
    das Base64 als Dateipfad zu oeffnen -> TTFError, siehe Vorfall 2026-07-06,
    Emoji-Icons in Objektblatt/Einsatz-PDF). xhtml2pdf ist ohnehin nur der
    Fallback fuer den seltenen Fall, dass WeasyPrint/GTK fehlt -- dann lieber
    ohne Emoji-Icons (wie zuvor) statt PDF-Generierung komplett abbrechen."""
    return _FONT_FACE_RE.sub("", html_str)


def _media_b64_uri(media) -> str:
    """Returns a base64 data URI for an image media object, or '' if unavailable.

    Bevorzugt die annotierte Version (flaches PNG liegt als {stem}_annotated.png
    neben dem Original), damit Einzeichnungen im Einsatzbericht erscheinen.
    """
    if media.kind != "image":
        return ""
    orig = Path(settings.MEDIA_STORAGE_DIR) / media.storage_path
    annotated = orig.with_name(orig.stem + "_annotated.png")
    path = annotated if annotated.exists() else orig
    if not path.exists():
        return ""
    data = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else media.mime_type
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _media_file_exists(media) -> bool:
    path = Path(settings.MEDIA_STORAGE_DIR) / media.storage_path
    return path.exists()


def _resolve_primary_org(incident: Incident) -> FireDept | None:
    """Lädt die Primary-Org für die Zeitzonen-Konvertierung in den Filtern."""
    if not incident.primary_org_id:
        return None
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.get(FireDept, incident.primary_org_id)
    finally:
        db.close()


def _load_incident_teilnahmen(incident_id: int) -> list:
    """Lädt Teilnahmen für einen Einsatz ohne Tenant-Filter (PDF-Kontext)."""
    from app.models.teilnahme import Teilnahme
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(Teilnahme).filter(
            Teilnahme.bezug_typ == "einsatz",
            Teilnahme.bezug_id == incident_id,
        ).execution_options(include_all_tenants=True).order_by(Teilnahme.hinzugefuegt_am).all()
    finally:
        db.close()


def load_fahrten_km(incident_id: int, db=None) -> list[dict]:
    """Liefert [{label, km}] je Fahrzeug aus dem Fahrtenbuch für diesen Einsatz.

    Kann mit einer bestehenden Session (``db``) aufgerufen werden (kein eigener
    Connection-Acquire) oder öffnet selbst eine (für PDF-Kontext ohne Request).
    Nutzt joinedload, um den N+1-Lazy-Load von ``fahrzeug`` zu vermeiden.
    """
    try:
        from sqlalchemy.orm import joinedload as _jl

        from app.models.fahrtenbuch import Fahrt, FahrtStatus
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            fahrten = (
                db.query(Fahrt)
                .options(_jl(Fahrt.fahrzeug))
                .filter(
                    Fahrt.incident_id == incident_id,
                    Fahrt.status == FahrtStatus.aktiv,
                )
                .all()
            )
            km_by: dict[int, dict] = {}
            for f in fahrten:
                if f.fahrzeug_id not in km_by:
                    label = f.fahrzeug.display_label if f.fahrzeug else f"Fahrzeug #{f.fahrzeug_id}"
                    km_by[f.fahrzeug_id] = {"label": label, "km": 0}
                if f.km_delta:
                    km_by[f.fahrzeug_id]["km"] += f.km_delta
            return [v for v in km_by.values() if v["km"] > 0]
        finally:
            if own_db:
                db.close()
    except Exception:
        return []


# Rückwärtskompatibles Alias (intern genutzt)
_load_incident_fahrten_km = load_fahrten_km


def _load_pdf_context(incident: Incident) -> tuple:
    """Lädt Primary-Org, Teilnahmen, Fahrtenbuch-km und Verlauf in einer einzigen DB-Session.

    Gibt (primary_org, teilnahmen, fahrten_km, journal) zurück. ``journal`` kombiniert das
    strukturierte Karten-Journal (IncidentChange) mit den Freitext-Notizen (IncidentLog) in
    chronologischer Reihenfolge, damit der Ausdruck denselben Verlauf zeigt wie Board und
    Karten-Journal (vorher enthielt der Ausdruck nur die Freitext-Notizen).
    """
    from sqlalchemy.orm import joinedload as _jl

    from app.models.fahrtenbuch import Fahrt, FahrtStatus
    from app.models.master import VehicleMaster
    from app.models.teilnahme import Teilnahme
    from app.services.incident_service import combined_verlauf

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        primary_org = (
            db.get(FireDept, incident.primary_org_id)
            if incident.primary_org_id else None
        )

        teilnahmen = (
            db.query(Teilnahme)
            # fahrzeug ist lazy="joined", dessen dept aber nicht – ohne dieses nested
            # Eager-Loading scheitert t.fahrzeug.display_label (→ dept.short_code) im
            # Template mit DetachedInstanceError, sobald diese Session geschlossen ist.
            .options(_jl(Teilnahme.fahrzeug).joinedload(VehicleMaster.dept))
            .filter(
                Teilnahme.bezug_typ == "einsatz",
                Teilnahme.bezug_id == incident.id,
            )
            .execution_options(include_all_tenants=True)
            .order_by(Teilnahme.hinzugefuegt_am)
            .all()
        )

        fahrten = (
            db.query(Fahrt)
            .options(_jl(Fahrt.fahrzeug))
            .filter(
                Fahrt.incident_id == incident.id,
                Fahrt.status == FahrtStatus.aktiv,
            )
            .all()
        )
        km_by: dict[int, dict] = {}
        for f in fahrten:
            if f.fahrzeug_id not in km_by:
                label = f.fahrzeug.display_label if f.fahrzeug else f"Fahrzeug #{f.fahrzeug_id}"
                km_by[f.fahrzeug_id] = {"label": label, "km": 0}
            if f.km_delta:
                km_by[f.fahrzeug_id]["km"] += f.km_delta
        fahrten_km = [v for v in km_by.values() if v["km"] > 0]

        journal = list(reversed(combined_verlauf(db, incident.id)))

        # Verknüpfte Objekte inkl. statischer Objektkarte (solange die Session offen
        # ist – render_objekt_map_png greift auf objekt.karten_objekte zu).
        objekte = _load_incident_objekte(db, incident)

        return primary_org, teilnahmen, fahrten_km, journal, objekte
    except Exception:
        logger.exception("PDF-Kontext laden fehlgeschlagen (Einsatz %s)", incident.id)
        return None, [], [], [], []
    finally:
        db.close()


def _load_incident_objekte(db, incident: Incident) -> list[dict]:
    """Lädt die mit dem Einsatz verknüpften Objekte für den Ausdruck.

    Rendert je Objekt die statische Objektkarte (roter Marker + Lagekarten-Symbole)
    als Base64-Data-URI. Bestätigte Treffer zuerst, danach Vorschläge. Fail-safe:
    fehlt das Objekt-Modul oder die Kartenkacheln, bleibt map_uri leer.
    """
    from sqlalchemy.orm import selectinload as _sl

    from app.models.objekt import (
        OBJEKT_EINSATZ_BESTAETIGT,
        Objekt,
        ObjektEinsatz,
    )
    from app.services.objekt_pdf_service import render_objekt_map_png

    verknuepfungen = (
        db.query(ObjektEinsatz)
        .options(
            _sl(ObjektEinsatz.objekt).selectinload(Objekt.karten_objekte),
            _sl(ObjektEinsatz.objekt).selectinload(Objekt.gefahren),
            _sl(ObjektEinsatz.objekt).selectinload(Objekt.bma),
        )
        .filter(ObjektEinsatz.incident_id == incident.id)
        .execution_options(include_all_tenants=True)
        .all()
    )
    if not verknuepfungen:
        return []

    # Bestätigt vor Vorschlag, dann nach Objektnummer/Name stabil sortieren.
    verknuepfungen.sort(
        key=lambda oe: (
            0 if oe.status == OBJEKT_EINSATZ_BESTAETIGT else 1,
            (oe.objekt.anzeige_nummer or "") if oe.objekt else "",
        )
    )

    ergebnis: list[dict] = []
    for oe in verknuepfungen:
        o = oe.objekt
        if o is None:
            continue
        gefahren = []
        for og in o.gefahren:
            name = og.gefahr.name if og.gefahr else "Gefahr"
            zusatz = []
            if og.un_nummer:
                zusatz.append(f"UN {og.un_nummer}")
            if og.gefahrnummer:
                zusatz.append(f"GN {og.gefahrnummer}")
            if og.stoffname:
                zusatz.append(og.stoffname)
            gefahren.append({"name": name, "detail": " · ".join(zusatz)})

        map_uri = None
        try:
            png = render_objekt_map_png(o)
            if png:
                map_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        except Exception:
            logger.exception("Objektkarte für Einsatz-PDF fehlgeschlagen (Objekt %s)", o.id)

        adresse = f"{o.strasse or ''} {o.hausnummer or ''}".strip()
        if o.ort:
            adresse = (adresse + ", " + o.ort).strip(", ")

        ergebnis.append({
            "name": o.name,
            "nummer": o.anzeige_nummer,
            "adresse": adresse,
            "bma": (o.bma.bma_nummer if o.bma else None),
            "status": oe.status,
            "quelle": oe.quelle,
            "distanz_m": oe.distanz_m,
            "gefahren": gefahren,
            "map_uri": map_uri,
        })
    return ergebnis


def render_incident_pdf(incident: Incident, base_url: str = "") -> bytes:
    template = templates.env.get_template("pdf/incident_report.html")
    primary_org, teilnahmen, fahrten_km, journal, objekte = _load_pdf_context(incident)
    pseudo_user = SimpleNamespace(org=primary_org)
    teilnahmen.sort(key=lambda t: (t.funktion.sortierung if t.funktion else 9999, t.hinzugefuegt_am or 0))

    html_str = template.render(
        incident=incident,
        teilnahmen=teilnahmen,
        fahrten_km=fahrten_km,
        journal=journal,
        objekte=objekte,
        now=datetime.now(UTC),
        base_url=base_url,
        user=pseudo_user,
        media_b64=_media_b64_uri,
        media_exists=_media_file_exists,
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("WeasyPrint fehlgeschlagen (Einsatz-PDF), Fallback auf xhtml2pdf: %s", exc)
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html_str)), dest=buf)
        return buf.getvalue()


def render_troop_pdf(troop, incident: Incident, base_url: str = "") -> bytes:
    """Einzelexport eines Atemschutztrupps als vollständiges A4-PDF."""
    template = templates.env.get_template("pdf/troop_protocol.html")
    primary_org = _resolve_primary_org(incident)
    pseudo_user = SimpleNamespace(org=primary_org)

    html_str = template.render(
        troop=troop,
        incident=incident,
        now=datetime.now(UTC),
        base_url=base_url,
        user=pseudo_user,
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("WeasyPrint fehlgeschlagen (Trupp-PDF), Fallback auf xhtml2pdf: %s", exc)
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html_str)), dest=buf)
        return buf.getvalue()


def render_as_pruefung_pdf(pruefungen: list, user=None, base_url: str = "") -> bytes:
    """Atemschutzgeräteprüfung(en) als A4-PDF – ein Protokoll oder mehrere (Sammel-PDF).

    ``pruefungen`` ist immer eine Liste (auch für den Einzel-Export mit genau
    einem Element) — vereinfacht Router und Template (kein Sonderfall nötig).
    """
    template = templates.env.get_template("pdf/as_pruefung_protocol.html")
    html_str = template.render(
        pruefungen=pruefungen,
        now=datetime.now(UTC),
        base_url=base_url,
        user=user,
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("WeasyPrint fehlgeschlagen (Atemschutz-Prüf-PDF), Fallback auf xhtml2pdf: %s", exc)
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_str), dest=buf)
        return buf.getvalue()


def render_teilnahme_pdf(
    teilnahmen: list,
    bezug_typ: str,
    titel: str,
    beginn,
    ort: str | None,
    user,
    base_url: str = "",
) -> bytes:
    """Teilnehmerliste als A4-PDF (WeasyPrint wenn GTK verfügbar, sonst xhtml2pdf)."""
    template = templates.env.get_template("pdf/teilnahme_report.html")
    html_str = template.render(
        teilnahmen=teilnahmen,
        bezug_typ=bezug_typ,
        titel=titel,
        beginn=beginn,
        ort=ort,
        user=user,
        now=datetime.now(UTC),
        base_url=base_url,
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415 – lazy: GTK not available on Windows
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except OSError:
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_str), dest=buf)
        return buf.getvalue()


def render_fahrtenbuch_bericht_pdf(
    daten: dict,
    filter_info: dict,
    user,
    base_url: str = "",
) -> bytes:
    """Fahrtenbuch-Statistik-Bericht als A4-Querformat-PDF (drei Seiten).

    ``daten`` stammt aus ``fahrtenbuch_service.berechne_bericht_daten`` und enthält
    die Auswertungen für alle Fahrzeuge, alle Maschinisten und Maschinisten je
    Fahrzeug. ``filter_info`` trägt den (vorgefilterten) Zeitraum für den Kopf.
    """
    from app.services.chart_svg import build_bericht_charts
    charts = build_bericht_charts(daten)
    template = templates.env.get_template("pdf/fahrtenbuch_bericht.html")
    html_str = template.render(
        daten=daten,
        charts=charts,
        filter=filter_info,
        user=user,
        now=datetime.now(UTC),
        base_url=base_url,
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415 – lazy: GTK ggf. nicht verfügbar
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except OSError:
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html_str)), dest=buf)
        return buf.getvalue()
