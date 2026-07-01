"""PDF generation via WeasyPrint (mit xhtml2pdf-Fallback)."""
import base64
import io
import logging
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


def _media_b64_uri(media) -> str:
    """Returns a base64 data URI for an image media object, or '' if unavailable."""
    if media.kind != "image":
        return ""
    path = Path(settings.MEDIA_STORAGE_DIR) / media.storage_path
    if not path.exists():
        return ""
    data = path.read_bytes()
    return f"data:{media.mime_type};base64,{base64.b64encode(data).decode()}"


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

        return primary_org, teilnahmen, fahrten_km, journal
    except Exception:
        return None, [], [], []
    finally:
        db.close()


def render_incident_pdf(incident: Incident, base_url: str = "") -> bytes:
    template = templates.env.get_template("pdf/incident_report.html")
    primary_org, teilnahmen, fahrten_km, journal = _load_pdf_context(incident)
    pseudo_user = SimpleNamespace(org=primary_org)
    teilnahmen.sort(key=lambda t: (t.funktion.sortierung if t.funktion else 9999, t.hinzugefuegt_am or 0))

    html_str = template.render(
        incident=incident,
        teilnahmen=teilnahmen,
        fahrten_km=fahrten_km,
        journal=journal,
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
        pisa.CreatePDF(io.StringIO(html_str), dest=buf)
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
