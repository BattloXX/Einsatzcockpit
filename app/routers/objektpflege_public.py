"""Loginfreie Objektpflege; einzige Freigabegrenze sind explizite Positivlisten.

Anonyme Token-Routen haben keinen Tenant-Kontext. Jede Abfrage wird deshalb mit
``include_all_tenants`` und einer org_id aus dem bereits aufgeloesten Auftrag
begrenzt; der Tenant-Listener ist hier keine Sicherheitsgrenze.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.db import get_db
from app.models.kontakt import Kontakt
from app.models.master import FireDept
from app.models.objekt import (
    KONTAKT_ARTEN,
    PFLEGEAUFTRAG_STATUS_EINGEREICHT,
    PFLEGEAUFTRAG_STATUS_WIDERRUFEN,
    GefahrenKatalog,
    KontaktAenderungsvorschlag,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektDokument,
    ObjektGefahr,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektPflegeAbschnitt,
    ObjektPflegeauftrag,
    ObjektPflegeEreignis,
    ObjektWohnanlage,
)
from app.services.objekt_dokument_service import (
    absolute_pfad,
    naechste_versionsnummer,
    store_dokument_upload,
    verarbeite_dokument,
)
from app.services.objekt_pflege_service import (
    BEREICHE_MIT_EDITFORMULAR,
    STAMMDATEN_BEREICHE_FELDER,
    alle_pflichtbereiche_bearbeitet,
    bereiche_liste,
    bestaetige_abschnitt,
    hole_oder_erstelle_arbeitskopie_fuer_auftrag,
    hole_pflegeauftrag_by_token,
    markiere_abschnitt_geaendert,
    markiere_pflegeauftrag_zugriff,
    pflegeauftrag_status_wechsel,
    pflegeauftrag_token_gueltig,
    wende_externe_feldaenderungen_an,
)

public_router = APIRouter(tags=["objektpflege-public"])
public_templates = Jinja2Templates(directory="app/templates")
_PUBLIC_HEADERS = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow",
                   "Referrer-Policy": "no-referrer"}


def _auftrag_oder_404(db: Session, token: str) -> ObjektPflegeauftrag:
    auftrag = hole_pflegeauftrag_by_token(db, token)
    if auftrag is None:
        raise HTTPException(404, "Nicht gefunden")
    return auftrag


def _objekt(db: Session, auftrag: ObjektPflegeauftrag) -> Objekt:
    objekt = (db.query(Objekt).execution_options(include_all_tenants=True)
              .filter(Objekt.id == auftrag.objekt_id, Objekt.org_id == auftrag.org_id).first())
    if objekt is None:
        raise HTTPException(404, "Nicht gefunden")
    return objekt


def _kontakt(db: Session, auftrag: ObjektPflegeauftrag) -> Kontakt:
    kontakt = (db.query(Kontakt).execution_options(include_all_tenants=True)
               .filter(Kontakt.id == auftrag.kontakt_id, Kontakt.org_id == auftrag.org_id).first())
    if kontakt is None:
        raise HTTPException(404, "Nicht gefunden")
    return kontakt


def _org(db: Session, auftrag: ObjektPflegeauftrag) -> FireDept | None:
    return (db.query(FireDept).execution_options(include_all_tenants=True)
            .filter(FireDept.id == auftrag.org_id).first())


def _org_name(org: FireDept | None) -> str:
    return org.name if org else "Einsatzcockpit"


def _status_response(request: Request, db: Session, auftrag: ObjektPflegeauftrag) -> HTMLResponse:
    if auftrag.status == PFLEGEAUFTRAG_STATUS_WIDERRUFEN:
        headline, text = "Zugang wurde widerrufen", "Dieser Link kann nicht mehr verwendet werden."
    elif auftrag.status in {"freigegeben", "verworfen"}:
        headline, text = "Diese Prüfung ist bereits abgeschlossen", "Es sind keine weiteren Eingaben möglich."
    else:
        headline, text = "Link abgelaufen", "Dieser Link ist nicht mehr gültig."
    org = _org(db, auftrag)
    return public_templates.TemplateResponse(request, "objektpflege/status.html", {
        "headline": headline, "text": text, "org": org, "org_name": _org_name(org),
    }, headers=_PUBLIC_HEADERS)


def _redirect(token: str) -> RedirectResponse:
    return RedirectResponse(f"/objektpflege/{token}/pruefen", status_code=303, headers=_PUBLIC_HEADERS)


@public_router.get("/objektpflege/{token}", response_class=HTMLResponse)
def start(token: str, request: Request, db: Session = Depends(get_db)):
    auftrag = _auftrag_oder_404(db, token)
    org = _org(db, auftrag)
    if auftrag.status == PFLEGEAUFTRAG_STATUS_EINGEREICHT:
        return public_templates.TemplateResponse(request, "objektpflege/abgeschlossen.html", {
            "org": org, "org_name": _org_name(org),
        }, headers=_PUBLIC_HEADERS)
    if not pflegeauftrag_token_gueltig(auftrag):
        return _status_response(request, db, auftrag)
    markiere_pflegeauftrag_zugriff(db, auftrag)
    db.commit()
    objekt, kontakt = _objekt(db, auftrag), _kontakt(db, auftrag)
    return public_templates.TemplateResponse(request, "objektpflege/start.html", {
        "objekt": objekt, "kontakt": kontakt, "auftrag": auftrag, "token": token,
        "org": org, "org_name": _org_name(org),
    }, headers=_PUBLIC_HEADERS)


@public_router.get("/objektpflege/{token}/pruefen", response_class=HTMLResponse)
def pruefen(token: str, request: Request, db: Session = Depends(get_db)):
    auftrag = _auftrag_oder_404(db, token)
    org = _org(db, auftrag)
    if auftrag.status == PFLEGEAUFTRAG_STATUS_EINGEREICHT:
        return RedirectResponse(f"/objektpflege/{token}", status_code=303, headers=_PUBLIC_HEADERS)
    if not pflegeauftrag_token_gueltig(auftrag):
        return RedirectResponse(f"/objektpflege/{token}", status_code=303, headers=_PUBLIC_HEADERS)
    objekt, kontakt = _objekt(db, auftrag), _kontakt(db, auftrag)
    live_objekt = objekt
    if auftrag.arbeitskopie_id is not None:
        live_objekt = (db.query(Objekt).execution_options(include_all_tenants=True)
                       .filter(Objekt.id == auftrag.arbeitskopie_id, Objekt.org_id == auftrag.org_id,
                               Objekt.entwurf_von_id == objekt.id).first()) or objekt
    abschnitte = (db.query(ObjektPflegeAbschnitt).execution_options(include_all_tenants=True)
                  .filter(ObjektPflegeAbschnitt.pflegeauftrag_id == auftrag.id,
                          ObjektPflegeAbschnitt.org_id == auftrag.org_id).all())
    bereiche = bereiche_liste(auftrag)
    dokumente = []
    if "dokumente" in bereiche:
        dokumente = (db.query(ObjektDokument).execution_options(include_all_tenants=True)
                     .filter(ObjektDokument.objekt_id == objekt.id, ObjektDokument.org_id == auftrag.org_id,
                             ObjektDokument.ist_aktuelle_version.is_(True)).all())
    merkmale = []
    wohnanlage = None
    hausverwaltung_name = None
    if "stammdaten" in bereiche:
        merkmale_roh = (db.query(ObjektMerkmal, MerkmalKatalog).execution_options(include_all_tenants=True)
                         .join(MerkmalKatalog, ObjektMerkmal.merkmal_id == MerkmalKatalog.id)
                         .filter(ObjektMerkmal.objekt_id == objekt.id, ObjektMerkmal.org_id == auftrag.org_id,
                                 MerkmalKatalog.org_id == auftrag.org_id).all())
        merkmale = [
            {
                "name": f"{merkmal.icon} {merkmal.name}".strip() if merkmal.icon else merkmal.name,
                "hinweis": zuordnung.hinweis,
            }
            for zuordnung, merkmal in merkmale_roh
        ]
        wohnanlage = (db.query(ObjektWohnanlage).execution_options(include_all_tenants=True)
                      .filter(ObjektWohnanlage.objekt_id == objekt.id,
                              ObjektWohnanlage.org_id == auftrag.org_id).first())
        if wohnanlage and wohnanlage.hausverwaltung_kontakt_id:
            hausverwaltung_name = (db.query(Kontakt.anzeigename).execution_options(include_all_tenants=True)
                                   .join(ObjektKontakt, ObjektKontakt.kontakt_id == Kontakt.id)
                                   .filter(ObjektKontakt.id == wohnanlage.hausverwaltung_kontakt_id,
                                           ObjektKontakt.objekt_id == objekt.id,
                                           ObjektKontakt.org_id == auftrag.org_id,
                                           Kontakt.org_id == auftrag.org_id).scalar())
    weitere_kontakte = []
    if "kontakte" in bereiche:
        weitere_kontakte = (db.query(ObjektKontakt, Kontakt).execution_options(include_all_tenants=True)
                            .join(Kontakt, ObjektKontakt.kontakt_id == Kontakt.id)
                            .filter(ObjektKontakt.objekt_id == objekt.id,
                                    ObjektKontakt.org_id == auftrag.org_id,
                                    ObjektKontakt.kontakt_id != auftrag.kontakt_id,
                                    Kontakt.org_id == auftrag.org_id)
                            .order_by(ObjektKontakt.sort, ObjektKontakt.id).all())
    bma = (db.query(ObjektBMA).execution_options(include_all_tenants=True)
           .filter(ObjektBMA.objekt_id == objekt.id, ObjektBMA.org_id == auftrag.org_id).first())
    gefahren = (db.query(ObjektGefahr).execution_options(include_all_tenants=True)
                .join(GefahrenKatalog, ObjektGefahr.gefahr_id == GefahrenKatalog.id)
                .filter(ObjektGefahr.objekt_id == objekt.id, ObjektGefahr.org_id == auftrag.org_id,
                        GefahrenKatalog.org_id == auftrag.org_id)
                .order_by(ObjektGefahr.sort, ObjektGefahr.id).all())
    abschnitte_by_bereich = {a.bereich: a for a in abschnitte}
    bearbeitet_anzahl = sum(
        1 for bereich in bereiche
        if abschnitte_by_bereich.get(bereich) and abschnitte_by_bereich[bereich].status != "offen"
    )
    return public_templates.TemplateResponse(request, "objektpflege/pruefen.html", {
        "auftrag": auftrag, "objekt": objekt, "live_objekt": live_objekt, "kontakt": kontakt,
        "token": token, "bereiche": bereiche, "abschnitte": abschnitte_by_bereich,
        "dokumente": dokumente, "editierbar": BEREICHE_MIT_EDITFORMULAR,
        "merkmale": merkmale, "wohnanlage": wohnanlage, "hausverwaltung_name": hausverwaltung_name,
        "weitere_kontakte": weitere_kontakte, "kontakt_arten": KONTAKT_ARTEN,
        "bma": bma, "gefahren": gefahren,
        "bearbeitet_anzahl": bearbeitet_anzahl,
        "vollstaendig": alle_pflichtbereiche_bearbeitet(db, auftrag),
        "org": org, "org_name": _org_name(org),
    }, headers=_PUBLIC_HEADERS)


def _aktive_aktion(db: Session, token: str) -> ObjektPflegeauftrag:
    auftrag = _auftrag_oder_404(db, token)
    if not pflegeauftrag_token_gueltig(auftrag):
        raise HTTPException(400, "Link ist nicht mehr gültig")
    return auftrag


@public_router.post("/objektpflege/{token}/bereich/{bereich}/bestaetigen")
def bereich_bestaetigen(token: str, bereich: str, db: Session = Depends(get_db)):
    auftrag = _aktive_aktion(db, token)
    if bereich not in bereiche_liste(auftrag):
        raise HTTPException(400, "Ungültiger Bereich")
    bestaetige_abschnitt(db, auftrag, bereich, kontakt_id=auftrag.kontakt_id)
    db.commit()
    return _redirect(token)


@public_router.post("/objektpflege/{token}/bereich/{bereich}/aendern")
async def bereich_aendern(token: str, bereich: str, request: Request, db: Session = Depends(get_db)):
    auftrag = _aktive_aktion(db, token)
    if bereich not in bereiche_liste(auftrag):
        raise HTTPException(400, "Ungültiger Bereich")
    form = await request.form()
    if bereich in STAMMDATEN_BEREICHE_FELDER:
        felder = {feld: str(form.get(feld, "")) for feld in STAMMDATEN_BEREICHE_FELDER[bereich]}
        kopie = hole_oder_erstelle_arbeitskopie_fuer_auftrag(db, auftrag)
        geaendert = wende_externe_feldaenderungen_an(db, auftrag, kopie, felder, kontakt_id=auftrag.kontakt_id)
        if geaendert:
            markiere_abschnitt_geaendert(db, auftrag, bereich)
    elif bereich == "kontakte":
        kontakt = _kontakt(db, auftrag)
        diff = {}
        for feld in ("funktion", "email", "erreichbarkeit", "notizen"):
            neu, alt = str(form.get(feld, "")), getattr(kontakt, feld) or ""
            if neu != alt:
                diff[feld] = {"alt": alt, "neu": neu}
        telefon_neu = str(form.get("telefon", "")).strip()
        telefon_alt = kontakt.telefone[0].nummer if kontakt.telefone else ""
        if telefon_neu != telefon_alt:
            diff["telefon"] = {"alt": telefon_alt, "neu": telefon_neu}
        if diff:
            db.add(KontaktAenderungsvorschlag(org_id=auftrag.org_id, pflegeauftrag_id=auftrag.id,
                   kontakt_id=kontakt.id, basis_version=kontakt.version,
                   diff_json=json.dumps(diff, ensure_ascii=False), status="offen"))
            db.add(ObjektPflegeEreignis(org_id=auftrag.org_id, pflegeauftrag_id=auftrag.id,
                   typ="feld_geaendert", kontakt_id=auftrag.kontakt_id,
                   text="Ansprechpartner-Änderung gemeldet"))
            markiere_abschnitt_geaendert(db, auftrag, bereich)
    else:
        raise HTTPException(400, "Für diesen Bereich sind keine Änderungen möglich")
    db.commit()
    return _redirect(token)


def _dokument_oder_404(db: Session, auftrag: ObjektPflegeauftrag, dokument_id: int) -> tuple[Objekt, ObjektDokument]:
    objekt = _objekt(db, auftrag)
    dokument = (db.query(ObjektDokument).execution_options(include_all_tenants=True)
                .filter(ObjektDokument.id == dokument_id, ObjektDokument.objekt_id == objekt.id,
                        ObjektDokument.org_id == auftrag.org_id).first())
    if dokument is None:
        raise HTTPException(404, "Nicht gefunden")
    return objekt, dokument


@public_router.get("/objektpflege/{token}/dokumente/{dokument_id}/anzeigen")
def dokument_anzeigen(token: str, dokument_id: int, db: Session = Depends(get_db)):
    """Open a token-scoped document in the browser's native PDF viewer.

    Android WebView/Chrome can reject a PDF iframe when an upstream proxy adds
    restrictive frame headers. A top-level PDF response is not frameable and
    therefore works consistently while retaining the same token authorization.
    """
    auftrag = _aktive_aktion(db, token)
    if "dokumente" not in bereiche_liste(auftrag):
        raise HTTPException(404, "Nicht gefunden")
    _, dokument = _dokument_oder_404(db, auftrag, dokument_id)
    if not dokument.ist_aktuelle_version:
        raise HTTPException(404, "Nicht gefunden")
    return RedirectResponse(
        f"/objektpflege/{token}/dokumente/{dokument.id}/datei",
        status_code=303,
        headers=_PUBLIC_HEADERS,
    )


@public_router.get("/objektpflege/{token}/dokumente/{dokument_id}/datei")
def dokument_datei(token: str, dokument_id: int, db: Session = Depends(get_db)):
    """Serve only the requested document, inline and only while the token is valid."""
    auftrag = _aktive_aktion(db, token)
    if "dokumente" not in bereiche_liste(auftrag):
        raise HTTPException(404, "Nicht gefunden")
    _, dokument = _dokument_oder_404(db, auftrag, dokument_id)
    if not dokument.ist_aktuelle_version:
        raise HTTPException(404, "Nicht gefunden")
    pfad = absolute_pfad(dokument.pfad)
    if not pfad.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(
        pfad,
        media_type=dokument.mime or "application/pdf",
        filename=dokument.dateiname_original,
        content_disposition_type="inline",
        headers=_PUBLIC_HEADERS,
    )


async def _speichere_dokument(datei: UploadFile, db: Session, auftrag: ObjektPflegeauftrag,
                              objekt: Objekt, text: str, dokument: ObjektDokument | None = None) -> ObjektDokument:
    neu = await store_dokument_upload(datei, objekt, None, db)
    if dokument is not None:
        neu.dokument_gruppe_id = dokument.dokument_gruppe_id or dokument.id
        neu.versionsnummer = naechste_versionsnummer(db, dokument)
    neu.ist_aktuelle_version = False
    neu.freigabe_status = "wartet_freigabe"
    neu.pflegeauftrag_id = auftrag.id
    db.add(ObjektPflegeEreignis(org_id=auftrag.org_id, pflegeauftrag_id=auftrag.id,
           typ="dokument_hochgeladen", kontakt_id=auftrag.kontakt_id, text=text))
    markiere_abschnitt_geaendert(db, auftrag, "dokumente")
    return neu


@public_router.post("/objektpflege/{token}/dokumente/{dokument_id}/version")
async def dokument_version(token: str, dokument_id: int, background_tasks: BackgroundTasks,
                            datei: UploadFile = File(...), db: Session = Depends(get_db)):
    auftrag = _aktive_aktion(db, token)
    if "dokumente" not in bereiche_liste(auftrag):
        raise HTTPException(400, "Bereich nicht beauftragt")
    objekt, dokument = _dokument_oder_404(db, auftrag, dokument_id)
    neu = await _speichere_dokument(
        datei, db, auftrag, objekt, f"Neue Version von {dokument.dateiname_original} hochgeladen", dokument,
    )
    db.commit()
    background_tasks.add_task(verarbeite_dokument, neu.id)
    return _redirect(token)


@public_router.post("/objektpflege/{token}/dokumente/neu")
async def dokument_neu(
    token: str, background_tasks: BackgroundTasks, datei: UploadFile = File(...), db: Session = Depends(get_db),
):
    auftrag = _aktive_aktion(db, token)
    if "dokumente" not in bereiche_liste(auftrag):
        raise HTTPException(400, "Bereich nicht beauftragt")
    neu = await _speichere_dokument(datei, db, auftrag, _objekt(db, auftrag), "Neues Dokument hochgeladen")
    db.commit()
    background_tasks.add_task(verarbeite_dokument, neu.id)
    return _redirect(token)


@public_router.post("/objektpflege/{token}/dokumente/{dokument_id}/ungueltig-melden")
def dokument_ungueltig(token: str, dokument_id: int, db: Session = Depends(get_db)):
    auftrag = _aktive_aktion(db, token)
    if "dokumente" not in bereiche_liste(auftrag):
        raise HTTPException(400, "Bereich nicht beauftragt")
    _, dokument = _dokument_oder_404(db, auftrag, dokument_id)
    db.add(ObjektPflegeEreignis(org_id=auftrag.org_id, pflegeauftrag_id=auftrag.id,
           typ="dokument_ungueltig_gemeldet", kontakt_id=auftrag.kontakt_id,
           text=f"{dokument.dateiname_original} als nicht mehr gültig gemeldet",
           metadaten_json=json.dumps({"dokument_id": dokument.id})))
    markiere_abschnitt_geaendert(db, auftrag, "dokumente")
    db.commit()
    return _redirect(token)


@public_router.post("/objektpflege/{token}/einreichen")
def einreichen(token: str, bestaetigung: str = Form(...), kontakt_notiz: str = Form(""), db: Session = Depends(get_db)):
    auftrag = _aktive_aktion(db, token)
    if not bestaetigung or not alle_pflichtbereiche_bearbeitet(db, auftrag):
        raise HTTPException(400, "Bitte bearbeiten Sie alle Bereiche und bestätigen Sie die Vollständigkeit")
    notiz = kontakt_notiz.strip()
    if len(notiz) > 5000:
        raise HTTPException(400, "Die Notiz ist zu lang (max. 5000 Zeichen)")
    auftrag.kontakt_notiz = notiz or None
    pflegeauftrag_status_wechsel(db, auftrag, PFLEGEAUFTRAG_STATUS_EINGEREICHT)
    db.commit()
    return RedirectResponse(f"/objektpflege/{token}", status_code=303, headers=_PUBLIC_HEADERS)
