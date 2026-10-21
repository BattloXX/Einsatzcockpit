"""Rechtegepruefte Offlinefassung aus derselben Fassung wie MCP."""

import base64
import io
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.templating import templates
from app.core.timezones import format_local_datetime
from app.models.katplan import KATPLAN_GEFAHRENARTEN, KatplanAnhang, KatplanPlan, KatplanVersion
from app.models.user import User
from app.services.katplan_geo_service import ableite_punkt
from app.services.katplan_mcp_service import _rohdaten, fassung_laden, link_ansichten
from app.services.katplan_quelle_service import absolute_pfad
from app.services.katplan_service import bereinige_beschreibung
from app.services.katplan_typen import FELD_LABELS, PLANARTEN
from app.services.kontakt_druck_service import organisationslogo_data_uri

logger = logging.getLogger(__name__)


_PHASENFARBEN = {"gruen": "#15803d", "gelb": "#ca8a04", "orange": "#ea580c", "rot": "#b91c1c",
                 "blau": "#1d4ed8", "grau": "#6b7280"}


def typdaten_fuer_druck(daten: dict) -> list[dict]:
    """Typspezifische Angaben als Druckbausteine (statt JSON): Text, Liste, Schritte, Wenn/Dann, Bedarf."""
    bausteine = []
    for feld, wert in daten.items():
        if not wert:
            continue
        label = FELD_LABELS.get(feld, feld.replace("_", " ").capitalize())
        if isinstance(wert, str):
            bausteine.append({"art": "text", "label": label, "wert": wert})
        elif feld == "schritte":
            schritte = [s for s in wert if isinstance(s, dict) and s.get("text")]
            if schritte:
                bausteine.append({"art": "schritte", "label": label, "wert": schritte})
        elif feld == "bedingungen":
            zeilen = [b for b in wert if isinstance(b, dict) and (b.get("wenn") or b.get("dann"))]
            if zeilen:
                bausteine.append({"art": "bedingungen", "label": label, "wert": zeilen})
        elif feld == "bedarf":
            zeilen = [b for b in wert if isinstance(b, dict) and b.get("bezeichnung")]
            if zeilen:
                bausteine.append({"art": "bedarf", "label": label, "wert": zeilen})
        elif isinstance(wert, list):
            eintraege = [str(w) for w in wert if isinstance(w, str | int | float) and str(w).strip()]
            if eintraege:
                bausteine.append({"art": "liste", "label": label, "wert": eintraege})
    return bausteine


def _ortsangabe(geometrie: dict | None, geo_status: str | None) -> str | None:
    """Koordinaten lesbar: Punkt als Lat/Lng, Linie/Flaeche mit Bezugspunkt."""
    if not geometrie:
        return None
    try:
        lat, lng = ableite_punkt(geometrie)
    except (ValueError, TypeError, KeyError):
        return None
    art = {"Point": "Punkt", "LineString": "Linie", "Polygon": "Fläche"}.get(geometrie.get("type", ""), "Geometrie")
    status = {"geprueft": "geprüft", "ungeprueft": "ungeprüft"}.get(geo_status or "", "")
    text = f"{art} {lat:.5f}, {lng:.5f}" if art == "Punkt" else f"{art}, Bezugspunkt {lat:.5f}, {lng:.5f}"
    return f"{text} ({status})" if status else text


def _kapitelpfade(abschnitte: list[dict]) -> list[str]:
    """Pfade "Kapitel > Unterkapitel" in Baumreihenfolge (Tiefensuche nach sort)."""
    kinder: dict[int | None, list[dict]] = {}
    for a in sorted(abschnitte, key=lambda a: (a.get("sort") or 0, a["id"])):
        kinder.setdefault(a.get("parent_id"), []).append(a)
    pfade: list[str] = []

    def besuche(parent: int | None, praefix: str, tiefe: int) -> None:
        if tiefe > 20:
            return
        for a in kinder.get(parent, []):
            pfad = f"{praefix} > {a['titel']}" if praefix else a["titel"]
            pfade.append(pfad)
            besuche(a["id"], pfad, tiefe + 1)

    besuche(None, "", 0)
    return pfade


def render_katplan_html(db: Session, user: User, plan: KatplanPlan, *,
                       version_id: int | None = None, entwurf: bool = False,
                       gliederung: str = "kapitel", akteur: str | None = None) -> str:
    kopf, elemente, vid, _ = fassung_laden(
        db, user, plan, "arbeitsstand" if entwurf else "freigegeben", version_id,
    )
    if gliederung not in ("kapitel", "akteur"):
        raise ValueError("Unbekannte Gliederung.")
    katalog = {a["schluessel"]: a for a in kopf.get("akteure", [])}
    if akteur is not None:
        if akteur not in katalog:
            raise ValueError(f"Unbekannter Akteur: {akteur}")
        elemente = [e for e in elemente if any(a["schluessel"] == akteur for a in e["akteure"])]
    _, roh, _ = _rohdaten(db, plan, entwurf, vid)
    nach_uuid = {e["element_uuid"]: e for e in roh}
    for e in elemente:
        e["beschreibung_html"] = bereinige_beschreibung(nach_uuid[e["element_id"]].get("beschreibung_html"))
        e["typdaten"] = typdaten_fuer_druck(e.get("daten") or {})
        e["ortsangabe"] = _ortsangabe(e.get("geometry"), e.get("geo_status"))
        e["bilder"] = []
        for a in e["attachments"]:
            if a.get("vertraulich") or not (a.get("mime") or "").startswith("image/"):
                continue
            datei = db.query(KatplanAnhang).filter_by(
                org_id=plan.org_id, plan_id=plan.id, uuid=a["uuid"], vertraulich=False,
            ).one_or_none()
            if datei is None or not datei.thumb_pfad:
                continue
            try:
                data = absolute_pfad(datei.thumb_pfad).read_bytes()
                if len(data) <= 2_000_000:
                    e["bilder"].append("data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"))
            except (OSError, ValueError):
                logger.warning("Anhang-Vorschaubild nicht verfügbar: %s", a["uuid"])
            if len(e["bilder"]) == 2:
                break
    version = db.query(KatplanVersion).filter_by(org_id=plan.org_id, plan_id=plan.id, id=vid).one_or_none()
    freigeber = db.query(User).filter_by(
        org_id=plan.org_id, id=version.freigegeben_von_id,
    ).one_or_none() if version else None
    gruppen: dict[str, list] = {}
    for e in elemente:
        gruppen.setdefault(e["chapter"] or "Ohne Kapitel", []).append(e)
    # Kapitel in Planreihenfolge (Abschnittsbaum), "Ohne Kapitel" ans Ende.
    pfade = _kapitelpfade(kopf.get("abschnitte", []))
    reihenfolge = {pfad: i for i, pfad in enumerate(pfade)}
    kapitel = {name: gruppen[name] for name in sorted(
        gruppen, key=lambda n: (n == "Ohne Kapitel", reihenfolge.get(n, len(reihenfolge)), n))}
    if gliederung == "akteur":
        kapitel = {a["bezeichnung"]: [e for e in elemente if any(
            ea["schluessel"] == a["schluessel"] for ea in e["akteure"])] for a in katalog.values()}
        kapitel["Ohne Akteur"] = [e for e in elemente if not e["akteure"]]
        kapitel = {name: eintraege for name, eintraege in kapitel.items() if eintraege}
    return templates.env.get_template("pdf/katplan.html").render(
        auszug=katalog[akteur]["bezeichnung"] if akteur is not None else None,
        planart_label=PLANARTEN[kopf.get("planart", "katastrophenplan")].label,
        kopf=kopf, kapitel=kapitel, version=version, entwurf=entwurf, org=user.org,
        freigeber=freigeber.display_name if freigeber else "Gelöschter Benutzer",
        gefahrenarten=KATPLAN_GEFAHRENARTEN, now=datetime.now(UTC),
        format_local_datetime=format_local_datetime, logo=organisationslogo_data_uri(db, user.org) if user.org else "",
        plan_links=link_ansichten(db, user, kopf.get("links", [])),
        phasenfarben=_PHASENFARBEN, beschreibung_plan=bereinige_beschreibung(kopf.get("beschreibung_html")),
    )


def render_katplan_pdf(db: Session, user: User, plan: KatplanPlan, *,
                      version_id: int | None = None, entwurf: bool = False,
                      gliederung: str = "kapitel", akteur: str | None = None) -> bytes:
    html = render_katplan_html(db, user, plan, version_id=version_id, entwurf=entwurf,
                               gliederung=gliederung, akteur=akteur)
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except OSError:
        from xhtml2pdf import pisa

        from app.services.pdf_service import strip_font_face_for_xhtml2pdf
        buf = io.BytesIO()
        result = pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html)), dest=buf)
        if result.err:
            raise ValueError("PDF konnte nicht erstellt werden.") from None
        return buf.getvalue()
