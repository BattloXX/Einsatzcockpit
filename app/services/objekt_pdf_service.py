"""Objektblatt-Druck (PR7): A4-PDF je Objekt + Sammel-Mappen.

- render_objektblatt_pdf: Jinja pdf/objektblatt.html → WeasyPrint mit
  xhtml2pdf-Fallback (Muster pdf_service.render_incident_pdf, NICHT das
  String-HTML-Muster aus uas_pdf.py)
- render_objekt_map_png: statische Karte (staticmap/OSM) mit Objektmarker und
  vereinfachten Symbolpunkten (Druck-Approximation; Legende listet die Symbole)
- objektblatt_mit_anhang: Objektblatt + alle "bei Einsatz drucken"-Seiten
  als ein PDF (pypdf-Merge)
- sammelmappe: mehrere Objektblaetter (+ optional Anhaenge) in einem PDF
"""
from __future__ import annotations

import base64
import io
import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.templating import templates
from app.models.master import FireDept
from app.models.objekt import (
    OBJEKT_SYMBOL_TYPEN,
    Objekt,
    ObjektDokumentSeite,
)
from app.services.objekt_service import lade_auswahl

logger = logging.getLogger("einsatzleiter.objekt_pdf")


def render_objekt_map_png(objekt: Objekt, *, size: tuple[int, int] = (640, 400)) -> bytes | None:
    """Statische OSM-Karte: roter Objektmarker + Symbolpunkte der Lagekarte.

    Vereinfachung fuer den Druck: Punktsymbole als farbige Kreise
    (Gefahren gelb, Zugaenge rot, sonst weiss mit rotem Rand); die Legende im
    Objektblatt ordnet Farben/Labels zu. None bei Fehler/ohne Koordinaten.
    """
    if objekt.lat is None or objekt.lng is None:
        return None
    try:
        from staticmap import CircleMarker, StaticMap  # noqa: PLC0415

        karte = StaticMap(
            size[0], size[1],
            url_template="https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
            headers={"User-Agent": "Einsatzcockpit (Objektblatt-Druck)"},
        )
        karte.add_marker(CircleMarker((objekt.lng, objekt.lat), "#d42225", 16))
        for k in objekt.karten_objekte:
            lat, lng = k.lat, k.lng
            if (lat is None or lng is None) and k.geometry_json:
                # Punkte koennen (z. B. aus EUS-Import) als GeoJSON-Point liegen
                try:
                    geo = json.loads(k.geometry_json)
                    if geo.get("type") == "Point":
                        lng, lat = geo["coordinates"][0], geo["coordinates"][1]
                except (ValueError, KeyError, IndexError, TypeError):
                    pass
            if lat is None or lng is None:
                continue
            if k.typ.startswith("gefahr_"):
                farbe = "#facc15"
            elif k.typ in ("hauptzugang", "nebenzugang"):
                farbe = "#b71921"
            elif k.typ.startswith("hydrant"):
                farbe = "#2563eb"
            else:
                farbe = "#ffffff"
            karte.add_marker(CircleMarker((lng, lat), farbe, 10))
        bild = karte.render(zoom=17)
        buf = io.BytesIO()
        bild.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        logger.exception("Statische Objektkarte fehlgeschlagen (Objekt %d)", objekt.id)
        return None


def render_objektblatt_pdf(
    objekt: Objekt,
    org: FireDept | None,
    base_url: str = "",
    *,
    mit_hinweisen: bool = False,
    karte_png: bytes | None = None,
) -> bytes:
    """Objektblatt A4 (1–2 Seiten). mit_hinweisen: Wohnanlagen-Hinweise andrucken
    (Checkbox, Default aus — DSGVO-Entscheidung 2026-07-05)."""
    from app.services.qr_service import generate_qr_datauri

    template = templates.env.get_template("pdf/objektblatt.html")

    if karte_png is None:
        karte_png = render_objekt_map_png(objekt)
    karte_datauri = (
        "data:image/png;base64," + base64.b64encode(karte_png).decode("ascii")
        if karte_png else None
    )
    qr_datauri = generate_qr_datauri(
        f"{base_url.rstrip('/')}/objekte/{objekt.id}/einsatz", druck=True
    ) if base_url else None

    # Org-spezifische Auswahllisten/Symbole fuer die Label-Ausgabe (Fallback = Konstante)
    from sqlalchemy.orm import object_session
    _db = object_session(objekt)
    if _db is not None:
        from app.services.objekt_symbol_service import lade_symbol_labels
        gefahr_piktogramme = lade_auswahl(_db, objekt.org_id, "piktogramm")
        kontakt_arten = lade_auswahl(_db, objekt.org_id, "kontaktart")
        symbol_labels = lade_symbol_labels(_db, objekt.org_id)
    else:
        from app.models.objekt import GEFAHR_PIKTOGRAMME, KONTAKT_ARTEN
        gefahr_piktogramme, kontakt_arten = GEFAHR_PIKTOGRAMME, KONTAKT_ARTEN
        symbol_labels = OBJEKT_SYMBOL_TYPEN

    symbol_legende = [
        (symbol_labels.get(k.typ, k.typ), k.label)
        for k in objekt.karten_objekte
        if k.lat is not None or k.geometry_json
    ]

    from app.core.timezones import format_local_datetime

    html_str = template.render(
        objekt=objekt,
        org=org,
        now=datetime.now(UTC),
        karte_datauri=karte_datauri,
        qr_datauri=qr_datauri,
        gefahr_piktogramme=gefahr_piktogramme,
        kontakt_arten=kontakt_arten,
        symbol_legende=symbol_legende,
        mit_hinweisen=mit_hinweisen,
        erstellt_str=format_local_datetime(objekt.erstellt_am, org),
        geaendert_str=format_local_datetime(objekt.aktualisiert_am, org),
        gedruckt_str=format_local_datetime(datetime.now(UTC), org),
    )
    try:
        from weasyprint import HTML  # noqa: PLC0415
        buf = io.BytesIO()
        HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("WeasyPrint fehlgeschlagen (Objektblatt), Fallback auf xhtml2pdf: %s", exc)
        from xhtml2pdf import pisa  # noqa: PLC0415
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_str), dest=buf)
        return buf.getvalue()


def _einsatzdruck_seiten(db: Session, objekt_id: int) -> list[ObjektDokumentSeite]:
    return (
        db.query(ObjektDokumentSeite)
        .filter(
            ObjektDokumentSeite.objekt_id == objekt_id,
            ObjektDokumentSeite.bei_einsatz_drucken.is_(True),
        )
        .order_by(ObjektDokumentSeite.dokumentart, ObjektDokumentSeite.dokument_id,
                  ObjektDokumentSeite.seiten_nr)
        .all()
    )


def objektblatt_mit_anhang(
    objekt: Objekt,
    org: FireDept | None,
    db: Session,
    base_url: str = "",
    *,
    mit_anhang: bool = True,
    mit_hinweisen: bool = False,
) -> bytes:
    """Objektblatt + optional alle 'bei Einsatz drucken'-Seiten als ein PDF."""
    from pypdf import PdfReader, PdfWriter

    from app.services.objekt_dokument_service import absolute_pfad

    blatt = render_objektblatt_pdf(objekt, org, base_url, mit_hinweisen=mit_hinweisen)
    if not mit_anhang:
        return blatt

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(blatt)).pages:
        writer.add_page(page)
    for seite in _einsatzdruck_seiten(db, objekt.id):
        if not seite.einzel_pdf_pfad:
            continue
        pfad = absolute_pfad(seite.einzel_pdf_pfad)
        if not pfad.exists():
            continue
        for page in PdfReader(str(pfad)).pages:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def sammelmappe(
    objekte: list[Objekt],
    org: FireDept | None,
    db: Session,
    base_url: str = "",
    *,
    mit_anhang: bool = False,
) -> bytes:
    """Mehrere Objektblaetter (Mappe fuers Fahrzeug) in einem PDF."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for objekt in objekte:
        pdf = objektblatt_mit_anhang(objekt, org, db, base_url, mit_anhang=mit_anhang)
        for page in PdfReader(io.BytesIO(pdf)).pages:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


__all__ = [
    "objektblatt_mit_anhang",
    "render_objekt_map_png",
    "render_objektblatt_pdf",
    "sammelmappe",
]
