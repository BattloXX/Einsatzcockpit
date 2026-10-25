"""Zentraler Druckdialog: geprüfte Auswahl, lokale PDF und bestehende Stationsjobs."""

import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.db import get_db
from app.models.gateway import DOC_DOKUMENTPAKET, Printer, PrintJob
from app.models.user import User
from app.services import dokumentdruck_service as service
from app.services.print_dispatcher import create_print_job, dispatch_job

router = APIRouter(prefix="/druck/viewer", tags=["Dokumentendruck"])


def _user(request: Request, response: Response) -> User:
    response.headers["Cache-Control"] = "no-store"
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    if user.org_id is None or not user.active:
        raise HTTPException(403, "Keine Druckberechtigung")
    return user


class Dokument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    pages: str = Field(default="alle", min_length=1, max_length=2048)


class Auswahl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[Dokument] = Field(min_length=1, max_length=20)
    format: str = Field(default="original", pattern="^(original|A4|A3)$")
    orientation: str = Field(default="original", pattern="^(original|portrait|landscape)$")


class Auftrag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(max_length=30000)
    printer_id: int = Field(gt=0)
    copies: int = Field(default=1, ge=1, le=20, strict=True)
    request_id: UUID


@router.post("/vorbereiten")
def vorbereiten(daten: Auswahl, db: Session = Depends(get_db), user: User = Depends(_user)):
    _, paket = service.zusammenstellen(db, user, daten.model_dump(), vorbereiten=True)
    token = service.versiegeln(user, paket)
    hinweis = ("Originale Seitengrößen bleiben erhalten. Stationsdruck erhält 6,35 mm Sicherheitsrand."
               if daten.format == "original" else
               f"Formatumwandlung: alle Seiten auf {daten.format} vollständig einpassen.")
    return {"token": token, "pdf_url": "/druck/viewer/pdf?paket=" + token,
            "page_count": paket["page_count"], "media": paket["media"], "note": hinweis}


@router.get("/pdf")
def pdf(paket: str, download: bool = False, db: Session = Depends(get_db), user: User = Depends(_user)):
    manifest = service.entsiegeln(user, paket)
    data, _ = service.zusammenstellen(db, user, manifest)
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": ('attachment' if download else 'inline') + '; filename="ECP-Druckauswahl.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get("/drucker")
def drucker(db: Session = Depends(get_db), user: User = Depends(_user)):
    erlaubt = service.station_erlaubt(db, user)
    printers = (db.query(Printer).filter_by(org_id=user.org_id, aktiv=True).order_by(Printer.name).all()
                if erlaubt else [])
    return {"allowed": erlaubt, "printers": [service.drucker_info(db, p) for p in printers],
            "note": "" if erlaubt else "Stationsdruck ist für diesen Zugang nicht freigeschaltet."}


@router.post("/auftrag")
def auftrag(daten: Auftrag, db: Session = Depends(get_db), user: User = Depends(_user)):
    if not service.station_erlaubt(db, user):
        raise HTTPException(403, "Keine Berechtigung für Stationsdruck.")
    manifest = service.entsiegeln(user, daten.token)
    _, paket = service.zusammenstellen(db, user, manifest)
    # Bestehende CUPS-Clients besitzen keinen Fit-Schalter. Daher Inhalt vektoriell
    # innerhalb desselben Papierformats einpassen; ein A3-Plan bleibt A3.
    paket["station_margin"] = 18
    signature = hashlib.sha256(json.dumps([paket, daten.printer_id, daten.copies], sort_keys=True).encode()).hexdigest()
    # Auch nach Netzwerkabbruch denselben Auftrag wiederfinden, niemals erneut ausführen.
    request_key = "viewer:" + str(daten.request_id)
    dedup_key = "manual-request:" + hashlib.sha256(f"{user.org_id}:{user.id}:{request_key}".encode()).hexdigest()
    existing = db.query(PrintJob).filter_by(org_id=user.org_id, idempotency_key=dedup_key).first()
    if existing:
        if (existing.options or {}).get("packet_signature") != signature:
            raise HTTPException(409, "Diese Auftragskennung wurde bereits für eine andere Auswahl verwendet.")
        return service.job_status(existing)
    printer = db.query(Printer).filter_by(org_id=user.org_id, id=daten.printer_id, aktiv=True).first()
    if printer is None:
        raise HTTPException(404, "Drucker nicht verfügbar.")
    info = service.drucker_info(db, printer)
    if not info["ready"]:
        raise HTTPException(409, info["detail"] + ". Bitte Verbindung prüfen oder auf diesem Gerät drucken.")
    if len(paket["media"]) != 1 or paket["media"][0] not in info["media"]:
        raise HTTPException(422, "Dieser Druckpfad benötigt ein unterstütztes einheitliches Papierformat. "
                            "Bitte A4/A3 ausdrücklich auswählen oder Originalgrößen auf diesem Gerät drucken.")
    options = {"copies": daten.copies, "media": paket["media"][0], "duplex": "off",
               "document_packet": paket, "packet_signature": signature}
    defaults = printer.defaults or {}
    if defaults.get("duplex") in ("off", "long-edge", "short-edge"):
        options["duplex"] = defaults["duplex"]
    if defaults.get("color") in ("color", "monochrome"):
        options["color"] = defaults["color"]
    assert user.org_id is not None
    job, created = create_print_job(db, org_id=user.org_id, gateway_id=printer.gateway_id, printer_id=printer.id,
                                   document_type=DOC_DOKUMENTPAKET, options=options, created_by_id=user.id,
                                   request_key=request_key)
    if not created and (job.options or {}).get("packet_signature") != signature:
        raise HTTPException(409, "Auftragskennung bereits verwendet.")
    db.commit()
    if created:
        run_async(dispatch_job, db, job)
        db.refresh(job)
    return service.job_status(job)


@router.get("/auftrag/{job_id}")
def status(job_id: int, db: Session = Depends(get_db), user: User = Depends(_user)):
    if not service.station_erlaubt(db, user):
        raise HTTPException(403, "Keine Berechtigung für Stationsdruck.")
    job = db.query(PrintJob).filter_by(org_id=user.org_id, created_by_id=user.id,
                                     document_type=DOC_DOKUMENTPAKET, id=job_id).first()
    if job is None:
        raise HTTPException(404, "Druckauftrag nicht gefunden.")
    return service.job_status(job)
