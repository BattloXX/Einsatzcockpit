"""Gemeinsame Druck-PDF-Vorschau für den lokalen Druck ("Dieses Gerät").

Rendert exakt dasselbe PDF wie der Gateway-Stationsdruck (Wiederverwendung von
print_artifact_service.render_job_pdf) und liefert es inline aus, damit der Browser-
Druckdialog es übernimmt. Nicht an das Gateway-Modul gebunden – lokaler Druck soll
immer möglich sein. Zugriff org-scoped (Session), Rolle recorder+ analog manueller Druck.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.db import get_db
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
    DOCUMENT_TYPE_LABELS,
)
from app.models.user import User

logger = logging.getLogger("einsatzleiter.druck")
router = APIRouter(prefix="/druck", tags=["druck"])


def _verify_org(db: Session, org_id: int, document_type: str,
                incident_id: int | None, gsl_id: int | None,
                objekt_id: int | None, artifact_ref: str | None) -> None:
    """Stellt sicher, dass das angeforderte Dokument der Org des Nutzers gehört.

    db.get(...) umgeht den Tenant-Filter (Zugriff per PK) – daher hier explizit die
    org-Zugehörigkeit prüfen, bevor gerendert wird (Muster ui_gateway.manual_print)."""
    def _own(obj, attr: str) -> bool:
        return obj is not None and getattr(obj, attr, None) == org_id

    if document_type == DOC_EINSATZINFO:
        from app.models.incident import Incident
        if not (incident_id and _own(db.get(Incident, incident_id), "primary_org_id")):
            raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")
    elif document_type == DOC_OBJEKTBLATT:
        from app.models.objekt import Objekt
        if not (objekt_id and _own(db.get(Objekt, objekt_id), "org_id")):
            raise HTTPException(status_code=404, detail="Objekt nicht gefunden")
    elif document_type == DOC_OBJEKT_DOKUMENT:
        from app.models.objekt import ObjektDokumentSeite
        seite = db.get(ObjektDokumentSeite, int(artifact_ref)) if artifact_ref else None
        if not _own(seite, "org_id"):
            raise HTTPException(status_code=404, detail="Dokumentseite nicht gefunden")
    elif document_type == DOC_GSL_LAGEBLATT:
        from app.models.major_incident import MajorIncident
        if not (gsl_id and _own(db.get(MajorIncident, gsl_id), "org_id")):
            raise HTTPException(status_code=404, detail="Großschadenslage nicht gefunden")
    elif document_type == DOC_ALARM_ROHTEXT:
        from app.models.gateway import AlarmIngest
        ing = db.get(AlarmIngest, int(artifact_ref)) if artifact_ref else None
        if not _own(ing, "org_id"):
            raise HTTPException(status_code=404, detail="Alarmtext nicht gefunden")
    elif document_type == DOC_AS_PRUEFUNG:
        # artifact_ref = kommagetrennte IDs – jede muss der Org gehören.
        from app.services.print_artifact_service import _parse_ref_ids
        from app.models.atemschutz_pruefung import AtemschutzPruefung
        ids = _parse_ref_ids(artifact_ref)
        treffer = (
            db.query(AtemschutzPruefung)
            .filter(AtemschutzPruefung.id.in_(ids), AtemschutzPruefung.org_id == org_id)
            .execution_options(include_all_tenants=True)
            .count()
        ) if ids else 0
        if not ids or treffer != len(set(ids)):
            raise HTTPException(status_code=404, detail="Atemschutzprüfung nicht gefunden")
    elif document_type == DOC_TROOP_PROTOKOLL:
        from app.models.breathing import BreathingTroop
        from app.models.incident import Incident
        troop = db.get(BreathingTroop, int(artifact_ref)) if artifact_ref else None
        inc = db.get(Incident, incident_id) if incident_id else None
        if (troop is None or inc is None or troop.incident_id != inc.id
                or getattr(inc, "primary_org_id", None) != org_id):
            raise HTTPException(status_code=404, detail="Atemschutztrupp nicht gefunden")
    elif document_type == DOC_TEILNAHME:
        from app.services.print_artifact_service import teilnahme_bezug_gehoert_org
        teile = (artifact_ref or "").split(":")
        if len(teile) < 2 or not teile[1].isdigit() or not teilnahme_bezug_gehoert_org(
            db, teile[0], int(teile[1]), org_id
        ):
            raise HTTPException(status_code=404, detail="Teilnehmerliste nicht gefunden")
    elif document_type == DOC_OBJEKT_SAMMEL:
        from app.models.objekt import Objekt
        if not (objekt_id and _own(db.get(Objekt, objekt_id), "org_id")):
            raise HTTPException(status_code=404, detail="Objekt nicht gefunden")
    elif document_type == DOC_UAS:
        # Org wird strikt im Renderer je Subtyp geprüft; hier nur Format validieren.
        teile = (artifact_ref or "").split(":")
        if len(teile) < 2 or not teile[1].isdigit():
            raise HTTPException(status_code=422, detail="Ungültige UAS-Referenz")
    elif document_type == DOC_GSL_JOURNAL:
        from app.models.major_incident import LageJournalEntry, MajorIncident
        lage = db.get(MajorIncident, gsl_id) if gsl_id else None
        entry = db.get(LageJournalEntry, int(artifact_ref)) if artifact_ref else None
        if (lage is None or getattr(lage, "org_id", None) != org_id
                or entry is None or entry.major_incident_id != lage.id):
            raise HTTPException(status_code=404, detail="Journaleintrag nicht gefunden")
    elif document_type == DOC_VERLEIH_SCHEIN:
        from app.models.major_incident import MajorIncident
        from app.models.verleih import VerleihAusleihe
        lage = db.get(MajorIncident, gsl_id) if gsl_id else None
        ausleihe = db.get(VerleihAusleihe, int(artifact_ref)) if artifact_ref else None
        if (lage is None or getattr(lage, "org_id", None) != org_id
                or ausleihe is None or ausleihe.lage_id != lage.id):
            raise HTTPException(status_code=404, detail="Verleihschein nicht gefunden")
    else:
        raise HTTPException(status_code=400, detail="Unbekannter Dokumenttyp")


@router.get("/dokument.pdf")
def dokument_pdf(
    request: Request,
    document_type: str,
    incident_id: int | None = None,
    gsl_id: int | None = None,
    objekt_id: int | None = None,
    artifact_ref: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
):
    """Liefert das Druck-PDF eines Dokuments inline (lokaler Druck / Vorschau)."""
    if document_type not in DOCUMENT_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="Unbekannter Dokumenttyp")
    _verify_org(db, user.org_id, document_type, incident_id, gsl_id, objekt_id, artifact_ref)

    from app.services.print_artifact_service import ArtifactError, render_job_pdf

    job = SimpleNamespace(
        document_type=document_type,
        incident_id=incident_id,
        gsl_id=gsl_id,
        objekt_id=objekt_id,
        artifact_ref=artifact_ref,
        org_id=user.org_id,
    )
    try:
        pdf = render_job_pdf(db, job)
    except ArtifactError as exc:
        # Fehlende Bezugsdaten – als klare Meldung statt PDF ausliefern.
        return _fehler_seite(str(exc), status=422)
    except Exception:
        # Jeder andere Fehler (z. B. PDF-Renderer) darf keinen rohen 500 zeigen –
        # verständliche Seite ausliefern und den vollständigen Traceback loggen.
        logger.exception(
            "Druck-PDF-Rendering fehlgeschlagen (document_type=%s, incident_id=%s, gsl_id=%s, "
            "objekt_id=%s, artifact_ref=%s, org_id=%s)",
            document_type, incident_id, gsl_id, objekt_id, artifact_ref, user.org_id,
        )
        return _fehler_seite(
            "Das Druck-PDF konnte auf dem Server nicht erstellt werden. Bitte über den "
            "Stationsdrucker drucken oder den Administrator informieren.",
            status=502,
        )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{document_type}.pdf"'},
    )


def _fehler_seite(text: str, *, status: int) -> Response:
    """Kleine, lesbare Fehlerseite (der Druck öffnet in einem neuen Tab)."""
    html = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>Druck nicht möglich</title>"
        "<body style='font-family:system-ui,sans-serif;background:#081425;color:#d8e3fb;"
        "display:flex;min-height:90vh;align-items:center;justify-content:center;text-align:center;padding:24px'>"
        f"<div><div style='font-size:2.2rem;margin-bottom:10px'>🖨️</div>"
        f"<p style='max-width:36rem;line-height:1.5'>{text}</p></div></body>"
    )
    return Response(content=html, media_type="text/html", status_code=status)
