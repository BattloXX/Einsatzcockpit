"""PDF generation via WeasyPrint."""
import io
from datetime import UTC, datetime
from types import SimpleNamespace

from weasyprint import HTML

from app.core.templating import templates
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept


def _resolve_primary_org(incident: Incident) -> FireDept | None:
    """Lädt die Primary-Org für die Zeitzonen-Konvertierung in den Filtern."""
    if not incident.primary_org_id:
        return None
    db = SessionLocal()
    try:
        return db.get(FireDept, incident.primary_org_id)
    finally:
        db.close()


def render_incident_pdf(incident: Incident, base_url: str = "") -> bytes:
    template = templates.env.get_template("pdf/incident_report.html")
    primary_org = _resolve_primary_org(incident)
    pseudo_user = SimpleNamespace(org=primary_org)

    html_str = template.render(
        incident=incident,
        now=datetime.now(UTC),
        base_url=base_url,
        user=pseudo_user,
    )
    buf = io.BytesIO()
    HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
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
    buf = io.BytesIO()
    HTML(string=html_str, base_url=base_url or ".").write_pdf(buf)
    return buf.getvalue()
