"""ECPG – Druck-Artefakte: mappt document_type → Cloud-Renderer und erzeugt/prüft
kurzlebige signierte Download-URLs für das Gateway.

Die Cloud rendert das PDF on demand (kein Persistieren); das Gateway lädt es über
eine signierte URL (5 min gültig) und schickt es an CUPS.
"""
from __future__ import annotations

from app.config import settings
from app.core.security import sign_artifact_token, unsign_artifact_token
from app.models.gateway import (
    DOC_ALARM_ROHTEXT,
    DOC_EINSATZINFO,
    DOC_GSL_LAGEBLATT,
    DOC_OBJEKT_DOKUMENT,
    DOC_OBJEKTBLATT,
    PrintJob,
)


class ArtifactError(Exception):
    """Rendering nicht möglich (fehlende Bezugsdaten / unbekannter Typ)."""


def artifact_url(job: PrintJob) -> str:
    """Baut die signierte Download-URL für einen Job (Gateway-Sicht)."""
    token = sign_artifact_token(job.id, job.org_id)
    base = settings.effective_public_base_url.rstrip("/")
    return f"{base}/api/v1/print/artifacts/{job.id}?sig={token}"


def verify_artifact_token(job_id: int, token: str) -> int | None:
    """Prüft die Signatur. Gibt org_id zurück wenn gültig und zum Job passend."""
    data = unsign_artifact_token(token)
    if data is None:
        return None
    tok_job_id, org_id = data
    if tok_job_id != job_id:
        return None
    return org_id


def render_job_pdf(db, job: PrintJob, base_url: str = "") -> bytes:
    """Rendert das PDF für einen Druckauftrag anhand document_type."""
    base_url = base_url or settings.effective_public_base_url

    if job.document_type == DOC_EINSATZINFO:
        return _render_einsatzinfo(db, job, base_url)
    if job.document_type == DOC_OBJEKTBLATT:
        return _render_objektblatt(db, job, base_url)
    if job.document_type == DOC_OBJEKT_DOKUMENT:
        return _render_objekt_dokument(db, job)
    if job.document_type == DOC_GSL_LAGEBLATT:
        return _render_gsl_lageblatt(db, job, base_url)
    if job.document_type == DOC_ALARM_ROHTEXT:
        return _render_alarm_rohtext(db, job)
    raise ArtifactError(f"Unbekannter Dokumenttyp: {job.document_type}")


# ── Renderer ───────────────────────────────────────────────────────────────────

def _render_einsatzinfo(db, job: PrintJob, base_url: str) -> bytes:
    from app.models.incident import Incident
    from app.services.pdf_service import render_incident_pdf

    if not job.incident_id:
        raise ArtifactError("Einsatzinfo ohne incident_id")
    incident = db.get(Incident, job.incident_id)
    if incident is None:
        raise ArtifactError(f"Einsatz {job.incident_id} nicht gefunden")
    return render_incident_pdf(incident, base_url=base_url)


def _render_objektblatt(db, job: PrintJob, base_url: str) -> bytes:
    from app.models.objekt import Objekt
    from app.services.objekt_pdf_service import render_objektblatt_pdf

    if not job.objekt_id:
        raise ArtifactError("Objektblatt ohne objekt_id")
    objekt = db.get(Objekt, job.objekt_id)
    if objekt is None:
        raise ArtifactError(f"Objekt {job.objekt_id} nicht gefunden")
    org = objekt.org
    return render_objektblatt_pdf(objekt, org, base_url=base_url)


def _render_objekt_dokument(db, job: PrintJob) -> bytes:
    """Einzelne Objekt-Dokumentseite (artifact_ref = ObjektDokumentSeite.id)."""
    from app.models.objekt import ObjektDokumentSeite
    from app.services.objekt_dokument_service import absolute_pfad

    if not job.artifact_ref:
        raise ArtifactError("Objekt-Dokument ohne artifact_ref (Seiten-ID)")
    seite = db.get(ObjektDokumentSeite, int(job.artifact_ref))
    if seite is None or not seite.einzel_pdf_pfad:
        raise ArtifactError("Dokumentseite oder Einzel-PDF nicht gefunden")
    pfad = absolute_pfad(seite.einzel_pdf_pfad)
    if not pfad.exists():
        raise ArtifactError("Einzel-PDF-Datei fehlt")
    return pfad.read_bytes()


def _render_gsl_lageblatt(db, job: PrintJob, base_url: str) -> bytes:
    """GSL-Lageblatt als schlichtes A4-PDF (WeasyPrint + xhtml2pdf-Fallback)."""
    from app.models.major_incident import MajorIncident

    if not job.gsl_id:
        raise ArtifactError("GSL-Lageblatt ohne gsl_id")
    lage = db.get(MajorIncident, job.gsl_id)
    if lage is None:
        raise ArtifactError(f"Großschadenslage {job.gsl_id} nicht gefunden")
    from app.core.templating import templates as _t
    html_str = _t.env.get_template("pdf/gsl_lageblatt.html").render(lage=lage, org=lage.org)
    return _html_to_pdf(html_str, base_url)


def _render_alarm_rohtext(db, job: PrintJob) -> bytes:
    """Formatierter Original-Alarmtext (artifact_ref = AlarmIngest.id)."""
    from app.models.gateway import AlarmIngest

    raw = ""
    received = None
    if job.artifact_ref:
        ing = db.get(AlarmIngest, int(job.artifact_ref))
        if ing is not None:
            raw = ing.raw_text
            received = ing.received_at
    from app.core.templating import templates as _t
    html_str = _t.env.get_template("pdf/alarm_rohtext.html").render(
        raw_text=raw, received_at=received,
    )
    return _html_to_pdf(html_str, "")


def _html_to_pdf(html_str: str, base_url: str) -> bytes:
    """WeasyPrint mit xhtml2pdf-Fallback (Muster pdf_service.render_incident_pdf)."""
    import io
    import logging

    logger = logging.getLogger("einsatzleiter.print")
    try:
        from weasyprint import HTML  # noqa: PLC0415
        return HTML(string=html_str, base_url=base_url).write_pdf()
    except Exception as exc:  # pragma: no cover - GTK-abhängig
        logger.warning("WeasyPrint fehlgeschlagen, Fallback xhtml2pdf: %s", exc)
        from xhtml2pdf import pisa  # noqa: PLC0415

        from app.services.pdf_service import strip_font_face_for_xhtml2pdf
        buf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(strip_font_face_for_xhtml2pdf(html_str)), dest=buf)
        return buf.getvalue()
