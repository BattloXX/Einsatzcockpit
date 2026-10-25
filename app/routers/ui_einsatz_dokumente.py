"""Phase 4: Übernehmen, im Einsatz verwalten und gezielt freigeben."""

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.templating import templates
from app.db import get_db
from app.models.einsatz_dokument import EinsatzDokument, EinsatzDokumentEmpfaenger
from app.models.incident import Incident, Task
from app.models.major_incident import IncidentSite, LageEinheit, MajorIncident
from app.models.user import User
from app.services import einsatz_dokument_service as service
from app.services.dokumentviewer_service import viewer_auswahl
from app.services.katplan_zugriff import sieht_vertraulich

router = APIRouter(prefix="/einsatz-dokumente", tags=["Einsatzdokumente"])


def _user(request: Request, response: Response) -> User:
    response.headers["Cache-Control"] = "no-store"
    return service.vollzugang(request, getattr(request.state, "user", None))


class Dokument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    pages: str = Field(default="alle", min_length=1, max_length=2048)


class Uebernahme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[Dokument] = Field(min_length=1, max_length=20)
    art: Literal["einsatz", "lage"]
    einsatz_id: int = Field(gt=0)
    auftrag_id: int | None = Field(default=None, gt=0)
    einheit_ids: list[int] = Field(default_factory=list, max_length=200)
    auftrag_freigeben: bool = False
    vertraulich_bestaetigt: bool = False


def _optionen(db: Session, user: User, art: str, ident: int, *, schreiben: bool = True) -> dict:
    service.ziel(db, user, art, ident, schreiben=schreiben)
    if art == "einsatz":
        auftraege = [{"id": t.id, "titel": t.title} for t in db.query(Task).filter_by(incident_id=ident)]
        einheiten = []
    else:
        auftraege = [{"id": s.id, "titel": s.bezeichnung} for s in db.query(IncidentSite).filter_by(
            major_incident_id=ident, org_id=user.org_id)]
        einheiten = [{"id": e.id, "titel": e.label} for e in db.query(LageEinheit).filter_by(lage_id=ident)]
    return {"auftraege": auftraege, "einheiten": einheiten}


@router.get("/ziele")
def ziele(db: Session = Depends(get_db), user: User = Depends(_user)):
    from app.core.permissions import has_role
    from app.core.queries import visible_incidents_q

    if not has_role(user, "incident_leader", "admin", "org_admin", "recorder") or getattr(user, "gsl_nur_lesen", False):
        raise HTTPException(403, "Keine Berechtigung zur Dokumentübernahme.")
    einsaetze = visible_incidents_q(db, user).filter(Incident.status == "active").order_by(Incident.id.desc()).all()
    lagen = db.query(MajorIncident).filter_by(org_id=user.org_id, status="active").order_by(
        MajorIncident.id.desc()).all()
    return {"ziele": [{"art": "einsatz", "id": e.id, "titel": f"Einsatz #{e.id} · {e.alarm_type_code}"}
                      for e in einsaetze]
                    + [{"art": "lage", "id": lage.id, "titel": lage.name} for lage in lagen]}


@router.get("/optionen/{art}/{ident}")
def optionen(art: str, ident: int, db: Session = Depends(get_db), user: User = Depends(_user)):
    return _optionen(db, user, art, ident)


@router.post("/uebernehmen")
def uebernehmen(daten: Uebernahme, db: Session = Depends(get_db), user: User = Depends(_user)):
    docs = service.uebernehmen(db, user, daten.model_dump())
    ids = [d.id for d in docs]
    db.commit()
    return {"ids": ids, "url": f"/einsatz-dokumente/{daten.art}/{daten.einsatz_id}"}


@router.get("/sammlung/{plan_id}/{element_uuid}")
def sammlung(plan_id: int, element_uuid: str, request: Request, version_id: int,
             db: Session = Depends(get_db), user: User = Depends(_user)):
    from app.models.katplan import KatplanDokument, KatplanPlan, KatplanVersion, KatplanVersionElement
    from app.services.katplan_zugriff import require_katplan_lesen

    require_katplan_lesen(request, db)
    plan = db.query(KatplanPlan).filter_by(id=plan_id, org_id=user.org_id).first()
    element = db.query(KatplanVersionElement).filter_by(org_id=user.org_id, version_id=version_id,
                                                       element_uuid=element_uuid).first()
    version = db.query(KatplanVersion).filter_by(id=version_id, plan_id=plan_id, org_id=user.org_id).first()
    if plan is None or version is None or element is None or (element.vertraulich and not sieht_vertraulich(user)):
        raise HTTPException(404, "Maßnahme nicht gefunden.")
    documents = []
    for anhang in json.loads(element.anhaenge_json):
        if anhang.get("vertraulich") and not sieht_vertraulich(user):
            continue
        if anhang["art"] in ("datei", "foto", "objekt_dokument"):
            url = f'/plaene/anhang/{anhang["uuid"]}?version_id={version_id}&auszug=1'
            # Prüft auch Plan-/Versionszugehörigkeit und übergeordnete Vertraulichkeit.
            _, meta = service.quellreferenz(db, user, url)
            documents.append({"url": url, "title": meta["titel"]})
    if element.quelle_dokument_id:
        quelle = db.query(KatplanDokument).filter_by(org_id=user.org_id, plan_id=plan_id,
                                                    id=element.quelle_dokument_id).first()
        if quelle is None:
            raise HTTPException(404, "Quellendokument nicht gefunden.")
        from urllib.parse import urlencode

        params = {"version_id": str(version_id)}
        if element.quelle_seiten:
            params["seiten"] = element.quelle_seiten
        url = f"/plaene/quelle/{quelle.uuid}/original?" + urlencode(params)
        service.quellreferenz(db, user, url)
        documents.append({"url": url, "title": quelle.titel})
    if not documents:
        raise HTTPException(422, "Diese Maßnahme enthält keine übernehmbaren Dokumente.")
    return {"documents": documents}


def _liste(request: Request, db: Session, user: User, art: str, ident: int, *, voll: bool):
    from app.core.permissions import has_role

    ziel = service.ziel(db, user, art, ident)
    docs = db.query(EinsatzDokument).filter(service.ziel_filter(art, ident),
        EinsatzDokument.org_id == user.org_id, EinsatzDokument.entfernt_am.is_(None))
    if not sieht_vertraulich(user):
        docs = docs.filter(EinsatzDokument.vertraulich.is_(False))
    edit = (ziel.status == "active" and has_role(user, "incident_leader", "admin", "org_admin", "recorder")
            and not getattr(user, "gsl_nur_lesen", False))
    opts = _optionen(db, user, art, ident, schreiben=False)
    template = "einsatz_dokumente/verwaltung.html" if voll else "einsatz_dokumente/_liste.html"
    return templates.TemplateResponse(request, template, {"user": user, "dokumente": docs.order_by(
        EinsatzDokument.uebernommen_am.desc(), EinsatzDokument.id.desc()).all(), "art": art, "ident": ident,
        "can_edit_documents": edit, "json_laden": json.loads,
        "auftrag_titel": {a["id"]: a["titel"] for a in opts["auftraege"]},
        "einheit_titel": {e["id"]: e["titel"] for e in opts["einheiten"]}, **opts},
        headers={"Cache-Control": "no-store"})


@router.get("/liste/{art}/{ident}", response_class=HTMLResponse)
def liste(art: str, ident: int, request: Request, db: Session = Depends(get_db), user: User = Depends(_user)):
    return _liste(request, db, user, art, ident, voll=False)


@router.get("/datei/{dokument_id}")
def datei(dokument_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(_user)):
    obj = service.dokument(db, user, dokument_id)
    return viewer_auswahl(service.pdf_daten(db, obj), request.query_params)


@router.get("/quelle/{dokument_id}")
def quelle(dokument_id: int, db: Session = Depends(get_db), user: User = Depends(_user)):
    obj = service.dokument(db, user, dokument_id)
    return RedirectResponse(service.quell_link(db, user, obj), status_code=303,
                            headers={"Cache-Control": "no-store"})


@router.post("/{dokument_id}/zuordnung")
def zuordnen(dokument_id: int, request: Request, auftrag_id: str = Form(""),
             einheit_ids: list[int] = Form([]), auftrag_freigeben: bool = Form(False),
             vertraulich_bestaetigt: bool = Form(False), revision: int = Form(...),
             db: Session = Depends(get_db), user: User = Depends(_user)):
    obj = service.dokument(db, user, dokument_id, schreiben=True)
    if obj.revision != revision:
        raise HTTPException(409, "Zuordnung wurde inzwischen geändert. Bitte neu laden.")
    art, ident = service.dokument_ziel(obj)
    if auftrag_id and (not auftrag_id.isdigit() or len(auftrag_id) > 18):
        raise HTTPException(422, "Ungültiger Auftrag.")
    task_id, site_id = service.zuordnung(db, user, art, ident, int(auftrag_id) if auftrag_id else None,
                                       einheit_ids, auftrag_freigeben)
    if obj.vertraulich and (einheit_ids or auftrag_freigeben) and not vertraulich_bestaetigt:
        raise HTTPException(422, "Vertrauliche Freigabe ausdrücklich bestätigen.")
    vorher = {"task_id": obj.task_id, "site_id": obj.site_id, "einheit_ids": [e.einheit_id for e in obj.empfaenger],
              "auftrag_freigegeben": obj.auftrag_freigegeben}
    obj.task_id, obj.site_id, obj.auftrag_freigegeben = task_id, site_id, auftrag_freigeben
    obj.empfaenger.clear()
    db.flush()
    obj.empfaenger = [EinsatzDokumentEmpfaenger(org_id=user.org_id, einheit_id=e) for e in sorted(set(einheit_ids))]
    obj.revision += 1
    service.audit(db, user, obj, "zuordnung_geaendert", {"vorher": vorher, "task_id": task_id, "site_id": site_id,
        "einheit_ids": einheit_ids, "auftrag_freigegeben": auftrag_freigeben,
        "vertraulich_bestaetigt": vertraulich_bestaetigt})
    db.commit()
    if request.headers.get("HX-Request") == "true":
        return _liste(request, db, user, art, ident, voll=False)
    return RedirectResponse(f"/einsatz-dokumente/{art}/{ident}", status_code=303)


@router.post("/{dokument_id}/entfernen")
def entfernen(dokument_id: int, request: Request, revision: int = Form(...),
              db: Session = Depends(get_db), user: User = Depends(_user)):
    obj = service.dokument(db, user, dokument_id, schreiben=True)
    if obj.revision != revision:
        raise HTTPException(409, "Dokument wurde inzwischen geändert. Bitte neu laden.")
    obj.entfernt_am = datetime.now(UTC).replace(tzinfo=None)
    obj.revision += 1
    service.audit(db, user, obj, "entfernt", {"fassung_id": obj.fassung_id})
    art, ident = service.dokument_ziel(obj)
    db.commit()
    if request.headers.get("HX-Request") == "true":
        return _liste(request, db, user, art, ident, voll=False)
    return RedirectResponse(f"/einsatz-dokumente/{art}/{ident}", status_code=303)


@router.get("/{art}/{ident}", response_class=HTMLResponse)
def verwaltung(art: str, ident: int, request: Request, db: Session = Depends(get_db), user: User = Depends(_user)):
    return _liste(request, db, user, art, ident, voll=True)
