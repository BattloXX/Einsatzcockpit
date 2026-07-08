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
    DOC_AS_PRUEFUNG,
    DOC_EINSATZINFO,
    DOC_GSL_JOURNAL,
    DOC_GSL_LAGEBLATT,
    DOC_OBJEKT_DOKUMENT,
    DOC_OBJEKT_SAMMEL,
    DOC_OBJEKTBLATT,
    DOC_TEILNAHME,
    DOC_TROOP_PROTOKOLL,
    DOC_UAS,
    DOC_VERLEIH_SCHEIN,
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
    if job.document_type == DOC_AS_PRUEFUNG:
        return _render_as_pruefung(db, job, base_url)
    if job.document_type == DOC_TROOP_PROTOKOLL:
        return _render_troop(db, job, base_url)
    if job.document_type == DOC_TEILNAHME:
        return _render_teilnahme(db, job, base_url)
    if job.document_type == DOC_OBJEKT_SAMMEL:
        return _render_objekt_sammel(db, job)
    if job.document_type == DOC_UAS:
        return _render_uas(db, job)
    if job.document_type == DOC_GSL_JOURNAL:
        return _render_gsl_journal(db, job, base_url)
    if job.document_type == DOC_VERLEIH_SCHEIN:
        return _render_verleih_schein(db, job, base_url)
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


def _parse_ref_ids(artifact_ref: str | None) -> list[int]:
    """Kommagetrennte IDs aus artifact_ref (z. B. "42" oder "42,43") → list[int]."""
    if not artifact_ref:
        return []
    ids = []
    for teil in str(artifact_ref).split(","):
        teil = teil.strip()
        if teil.isdigit():
            ids.append(int(teil))
    return ids


def _render_as_pruefung(db, job: PrintJob, base_url: str) -> bytes:
    """Atemschutzgeräteprüfung(en) (artifact_ref = kommagetrennte AtemschutzPruefung-IDs).

    Org-Scope explizit (db.get/Query im tenant-losen Kontext) – nur Prüfungen der
    Job-Org werden gerendert. Timezone/Org kommt über ein Pseudo-User-Objekt
    (SimpleNamespace(org=...)) an das Template (Muster ui_druck)."""
    from types import SimpleNamespace

    from app.models.atemschutz_pruefung import AtemschutzPruefung
    from app.services.pdf_service import render_as_pruefung_pdf

    ids = _parse_ref_ids(job.artifact_ref)
    if not ids:
        raise ArtifactError("Atemschutzprüfung ohne IDs (artifact_ref)")
    pruefungen = (
        db.query(AtemschutzPruefung)
        .filter(
            AtemschutzPruefung.id.in_(ids),
            AtemschutzPruefung.org_id == job.org_id,
        )
        .order_by(AtemschutzPruefung.eingesetzt_am.desc())
        .execution_options(include_all_tenants=True)
        .all()
    )
    if not pruefungen:
        raise ArtifactError("Keine Atemschutzprüfung gefunden")
    org = getattr(pruefungen[0], "org", None)
    pseudo_user = SimpleNamespace(org=org)
    return render_as_pruefung_pdf(pruefungen, user=pseudo_user, base_url=base_url)


def _org(db, org_id: int):
    """Lädt die FireDept (Org) – für Timezone/Anzeige im Pseudo-User-Kontext."""
    from app.models.master import FireDept
    return db.get(FireDept, org_id)


def _render_troop(db, job: PrintJob, base_url: str) -> bytes:
    """Atemschutztrupp-Protokoll (incident_id + artifact_ref = troop_id)."""
    from app.models.breathing import BreathingTroop
    from app.models.incident import Incident
    from app.services.pdf_service import render_troop_pdf

    if not job.incident_id or not job.artifact_ref:
        raise ArtifactError("Atemschutz-Protokoll ohne incident_id/troop_id")
    troop = db.get(BreathingTroop, int(job.artifact_ref))
    incident = db.get(Incident, job.incident_id)
    if troop is None or incident is None or troop.incident_id != incident.id:
        raise ArtifactError("Atemschutztrupp nicht gefunden")
    if getattr(incident, "primary_org_id", None) != job.org_id:
        raise ArtifactError("Atemschutztrupp gehört nicht zur Org")
    return render_troop_pdf(troop, incident, base_url=base_url)


def teilnahme_bezug_gehoert_org(db, bezug_typ: str, bezug_id: int, org_id: int) -> bool:
    """True, wenn der Bezug (Einsatz/Termin) der Org gehört. Verhindert, dass über
    _bezug_meta Titel/Zeit eines fremden Bezugs geleakt werden (db.get umgeht Tenant)."""
    if bezug_typ == "einsatz":
        from app.models.incident import Incident
        inc = db.get(Incident, bezug_id)
        return inc is not None and getattr(inc, "primary_org_id", None) == org_id
    from app.models.teilnahme import Termin
    t = db.get(Termin, bezug_id)
    return t is not None and getattr(t, "org_id", None) == org_id


def _render_teilnahme(db, job: PrintJob, base_url: str) -> bytes:
    """Teilnehmerliste (artifact_ref = "<bezug_typ>:<bezug_id>[:<sort>]")."""
    from app.models.teilnahme import Teilnahme
    from app.services.pdf_service import render_teilnahme_pdf

    teile = (job.artifact_ref or "").split(":")
    if len(teile) < 2 or not teile[1].isdigit():
        raise ArtifactError("Teilnehmerliste ohne Bezug (artifact_ref)")
    bezug_typ, bezug_id = teile[0], int(teile[1])
    if not teilnahme_bezug_gehoert_org(db, bezug_typ, bezug_id, job.org_id):
        raise ArtifactError("Bezug nicht gefunden")
    teilnahmen = (
        db.query(Teilnahme)
        .filter(
            Teilnahme.org_id == job.org_id,
            Teilnahme.bezug_typ == bezug_typ,
            Teilnahme.bezug_id == bezug_id,
        )
        .order_by(Teilnahme.hinzugefuegt_am)
        .execution_options(include_all_tenants=True)
        .all()
    )
    from app.routers.ui_termin import _bezug_meta
    titel, beginn, ort = _bezug_meta(db, bezug_typ, bezug_id)
    pseudo_user = SimpleNamespace(org=_org(db, job.org_id))
    return render_teilnahme_pdf(
        teilnahmen=teilnahmen, bezug_typ=bezug_typ, titel=titel,
        beginn=beginn, ort=ort, user=pseudo_user, base_url=base_url,
    )


def _render_objekt_sammel(db, job: PrintJob) -> bytes:
    """Objekt-Dokumente Sammelmappe (objekt_id, artifact_ref = optionaler art-Filter)."""
    from app.models.objekt import Objekt, ObjektDokumentSeite
    from app.services.objekt_dokument_service import sammel_pdf

    if not job.objekt_id:
        raise ArtifactError("Objekt-Sammelmappe ohne objekt_id")
    objekt = db.get(Objekt, job.objekt_id)
    if objekt is None or objekt.org_id != job.org_id:
        raise ArtifactError("Objekt nicht gefunden")
    q = (
        db.query(ObjektDokumentSeite)
        .filter(ObjektDokumentSeite.objekt_id == objekt.id)
        .order_by(ObjektDokumentSeite.dokument_id, ObjektDokumentSeite.seiten_nr)
    )
    # artifact_ref: "einsatzdruck" = nur „bei Einsatz drucken"-Seiten; sonst dokumentart-Filter; leer = alle.
    ref = job.artifact_ref
    if ref == "einsatzdruck":
        q = q.filter(ObjektDokumentSeite.bei_einsatz_drucken.is_(True))
    elif ref:
        q = q.filter(ObjektDokumentSeite.dokumentart == ref)
    seiten = q.execution_options(include_all_tenants=True).all()
    if not seiten:
        raise ArtifactError("Keine Seiten für die Sammelmappe")
    return sammel_pdf(seiten)


def _render_uas(db, job: PrintJob) -> bytes:
    """UAS-PDFs (artifact_ref = "<subtyp>:<id>[:<id2>]"). Alle UAS-Modelle sind
    org-gebunden (org_id) → strikte Org-Prüfung je Subtyp."""
    import json as _json

    from app.models.uas import (
        UASCheckliste, UASDevice, UASEinsatz, UASEreignis, UASFlug, UASPilot, UASWartung,
    )
    from app.services import uas_pdf

    teile = (job.artifact_ref or "").split(":")
    if len(teile) < 2 or not teile[1].isdigit():
        raise ArtifactError("UAS-Dokument ohne Subtyp/ID (artifact_ref)")
    subtyp, oid = teile[0], int(teile[1])
    org_id = job.org_id
    org = _org(db, org_id)

    def _one(model, obj_id):
        obj = db.get(model, obj_id)
        if obj is None or getattr(obj, "org_id", None) != org_id:
            raise ArtifactError("UAS-Datensatz nicht gefunden")
        return obj

    if subtyp == "flugbuch":
        flug = _one(UASFlug, oid)
        pilot = db.get(UASPilot, flug.pilot_id) if flug.pilot_id else None
        device = db.get(UASDevice, flug.device_id) if flug.device_id else None
        return uas_pdf.flugbuch_pdf(flug, pilot, device)
    if subtyp == "checkliste":
        if len(teile) < 3 or not teile[2].isdigit():
            raise ArtifactError("UAS-Checkliste ohne flug_id")
        cl = _one(UASCheckliste, oid)
        return uas_pdf.checkliste_pdf(cl, flug_id=int(teile[2]), org=org)
    if subtyp == "ereignis_protokoll":
        return uas_pdf.ereignis_pdf(_one(UASEreignis, oid))
    if subtyp == "ereignis_acg":
        return uas_pdf.acg_unfall_pdf(_one(UASEreignis, oid))
    if subtyp == "wartungsbuch":
        device = _one(UASDevice, oid)
        wartungen = (
            db.query(UASWartung)
            .filter(UASWartung.uas_device_id == oid, UASWartung.org_id == org_id)
            .order_by(UASWartung.faellig_am)
            .execution_options(include_all_tenants=True)
            .all()
        )
        return uas_pdf.wartungsbuch_pdf(wartungen, device)
    if subtyp == "eintreffmeldung":
        einsatz = _one(UASEinsatz, oid)
        rollen = einsatz.rollen
        pilot_ids = {r.pilot_id for r in rollen if r.pilot_id}
        piloten = (
            db.query(UASPilot).filter(UASPilot.id.in_(pilot_ids))
            .execution_options(include_all_tenants=True).all()
            if pilot_ids else []
        )
        return uas_pdf.eintreffmeldung_pdf(einsatz, piloten)
    if subtyp == "gesamt":
        einsatz = _one(UASEinsatz, oid)
        from app.models.incident import Incident
        incident = db.get(Incident, einsatz.incident_id) if einsatz.incident_id else None
        fluege = (
            db.query(UASFlug)
            .filter(UASFlug.uas_einsatz_id == oid, UASFlug.org_id == org_id)
            .order_by(UASFlug.lfd_nr)
            .execution_options(include_all_tenants=True)
            .all()
        )
        fluege_daten = []
        for flug in fluege:
            pilot = db.get(UASPilot, flug.pilot_id) if flug.pilot_id else None
            device = db.get(UASDevice, flug.device_id) if flug.device_id else None
            checklisten_parsed = []
            for cl in sorted(flug.checklisten, key=lambda c: c.created_at):
                punkte = []
                if cl.punkte:
                    try:
                        punkte = _json.loads(cl.punkte)
                    except Exception:
                        pass
                checklisten_parsed.append({"checkliste": cl, "punkte": punkte})
            fluege_daten.append({"flug": flug, "pilot": pilot, "device": device,
                                 "checklisten": checklisten_parsed})
        return uas_pdf.einsatz_gesamt_pdf(
            einsatz, incident=incident, rollen=einsatz.rollen,
            fluege_daten=fluege_daten, org=org,
        )
    raise ArtifactError(f"Unbekannter UAS-Subtyp: {subtyp}")


def _render_gsl_journal(db, job: PrintJob, base_url: str) -> bytes:
    """GSL-Einsatzjournal – einzelner Eintrag (gsl_id + artifact_ref = Eintrag-ID).

    Rendert die bestehende, eigenständige Druck-Template zu PDF (WeasyPrint)."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.core.templating import templates as _t
    from app.models.major_incident import LageJournalEntry, MajorIncident

    if not job.gsl_id or not job.artifact_ref:
        raise ArtifactError("GSL-Journal ohne gsl_id/Eintrag-ID")
    lage = db.get(MajorIncident, job.gsl_id)
    if lage is None or lage.org_id != job.org_id:
        raise ArtifactError("Großschadenslage nicht gefunden")
    entry = db.get(LageJournalEntry, int(job.artifact_ref))
    if entry is None or entry.major_incident_id != lage.id:
        raise ArtifactError("Journaleintrag nicht gefunden")
    from app.routers.ui_major_incident import JOURNAL_CATEGORIES
    html_str = _t.env.get_template("incident_major/_journal_entry_druck.html").render(
        lage=lage, entry=entry, journal_categories=JOURNAL_CATEGORIES,
        now=datetime.now(UTC), user=SimpleNamespace(org=lage.org),
    )
    return _html_to_pdf(html_str, base_url)


def _render_verleih_schein(db, job: PrintJob, base_url: str) -> bytes:
    """Verleihschein (gsl_id = lage_id + artifact_ref = ausleihe_id).

    Rendert die bestehende, eigenständige Druck-Template zu PDF. Org-Scope strikt:
    Lage der Job-Org UND Ausleihe gehört zur Lage. Kein Journal-Seiteneffekt (der
    liegt im interaktiven Route-Pfad)."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.core.templating import templates as _t
    from app.models.major_incident import IncidentSite, MajorIncident
    from app.models.master import FireDept
    from app.models.verleih import VerleihAusleihe

    if not job.gsl_id or not job.artifact_ref:
        raise ArtifactError("Verleihschein ohne gsl_id/ausleihe_id")
    lage = db.get(MajorIncident, job.gsl_id)
    if lage is None or lage.org_id != job.org_id:
        raise ArtifactError("Großschadenslage nicht gefunden")
    ausleihe = db.get(VerleihAusleihe, int(job.artifact_ref))
    if ausleihe is None or ausleihe.lage_id != lage.id:
        raise ArtifactError("Ausleihe nicht gefunden")
    site = db.get(IncidentSite, ausleihe.site_id) if ausleihe.site_id else None
    dept = db.get(FireDept, lage.org_id)
    html_str = _t.env.get_template("verleih/druck.html").render(
        user=SimpleNamespace(org=dept), lage=lage, a=ausleihe, site=site,
        org_name=(dept.name if dept else "Feuerwehr"), now=datetime.now(UTC),
    )
    return _html_to_pdf(html_str, base_url)


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
