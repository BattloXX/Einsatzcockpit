"""Liste und Stammdaten der Katastrophenplaene."""

import json
from datetime import UTC, date, datetime
from html import escape
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.async_bridge import run_async
from app.core.permissions import can_view_kontakt_directory, has_role
from app.core.templating import templates
from app.core.timezones import now_local
from app.db import get_db
from app.models.katplan import (
    KATPLAN_GEFAHRENARTEN,
    KATPLAN_STATUS_LABELS,
    KatplanAbschnitt,
    KatplanAenderung,
    KatplanAnhang,
    KatplanDokument,
    KatplanDokumentSeite,
    KatplanElement,
    KatplanElementPhase,
    KatplanImportVorschlag,
    KatplanLink,
    KatplanPhase,
    KatplanPlan,
    KatplanPruefung,
    KatplanRelation,
    KatplanVersion,
    KatplanVersionElement,
)
from app.models.major_incident import AUFTRAGSART_LABEL, AUFTRAGSART_WERTE
from app.models.master import FireDept
from app.models.objekt import Objekt, ObjektDokument
from app.models.user import User
from app.routers.ui_objekt import _LESE_ROLLEN
from app.services import katplan_anhang_service as anhang_service
from app.services import katplan_freigabe_service as freigabe_service
from app.services import katplan_ki_service as ki_service
from app.services import katplan_link_service as link_service
from app.services import katplan_qs_service as qs_service
from app.services import katplan_quelle_service as quelle_service
from app.services import katplan_suche_service as suche_service
from app.services.katplan_service import (
    KatplanKonflikt,
    abschnitte_baum,
    aktualisiere_element_suchtext,
    bereinige_beschreibung,
    lege_abschnitt_an,
    lege_element_an,
    lege_phase_an,
    loesche_abschnitt,
    markiere_ueberarbeitung,
    naechste_kennung,
    protokolliere,
    pruefe_version,
    sortiere_baum,
    stammdaten_aendern,
    validiere_element_felder,
    validiere_stammdaten,
    verschiebe_nachbar,
)
from app.services.katplan_typen import ELEMENT_TYPEN, ELEMENT_VORLAGEN, bereinige_daten
from app.services.katplan_ziel_service import ziel_laden, ziel_suchen, ziel_verfuegbar
from app.services.katplan_zugriff import (
    darf_bearbeiten,
    darf_freigeben,
    require_katplan_bearbeiten,
    require_katplan_freigeben,
    require_katplan_lesen,
    sieht_vertraulich,
)
from app.services.media_service import _storage_root

router = APIRouter(prefix="/katastrophenplaene", tags=["Katastrophenplaene"])


def _plan(db: Session, plan_id: int, user: User) -> KatplanPlan:
    plan = db.query(KatplanPlan).filter(
        KatplanPlan.id == plan_id, KatplanPlan.org_id == user.org_id,
    ).one_or_none()
    if plan is None:
        raise HTTPException(404, "Nicht gefunden")
    return plan


def _kontext(db: Session, user: User, plan: KatplanPlan | None = None) -> dict[str, Any]:
    protokoll = []
    aktuelle_version = None
    freigegeben_von = None
    if plan and plan.aktuelle_version_id:
        try:
            aktuelle_version, _, _ = freigabe_service.lade_version(db, plan)
            freigeber = db.query(User).filter_by(
                id=aktuelle_version.freigegeben_von_id, org_id=plan.org_id,
            ).one_or_none()
            freigegeben_von = freigeber.display_name if freigeber else "Gelöschter Benutzer"
        except ValueError:
            aktuelle_version = None
    if plan:
        protokoll = db.query(KatplanAenderung, User.display_name).outerjoin(
            User, User.id == KatplanAenderung.user_id,
        ).filter(KatplanAenderung.plan_id == plan.id).order_by(
            KatplanAenderung.zeit.desc(), KatplanAenderung.id.desc(),
        ).limit(50).all()
    return {"user": user, "plan": plan, "gefahrenarten": KATPLAN_GEFAHRENARTEN,
            "status_labels": KATPLAN_STATUS_LABELS, "can_edit": darf_bearbeiten(user),
            "can_release": darf_freigeben(user), "protokoll": protokoll,
            "aktuelle_version": aktuelle_version, "freigegeben_von": freigegeben_von,
            "heute": now_local(user.org).date(),
            **(_editor_kontext(db, plan) if plan and darf_bearbeiten(user) else {}),
            **(_link_kontext(db, user, plan) if plan else {}),
            **(_anhaenge_kontext(db, user, plan) if plan else {})}


class Stammdaten:
    def __init__(
        self, titel: str = Form(""), gefahrenarten: list[str] = Form([]),
        beschreibung_html: str = Form(""), geltungsbereich: str = Form(""),
        verantwortliche_stelle: str = Form(""), wiedervorlage_am: str = Form(""),
    ):
        self.form = {"titel": titel, "gefahrenarten": gefahrenarten,
                     "beschreibung_html": bereinige_beschreibung(beschreibung_html) or "",
                     "geltungsbereich": geltungsbereich,
                     "verantwortliche_stelle": verantwortliche_stelle, "wiedervorlage_am": wiedervorlage_am}
        self.fehler: str | None = None
        self.werte = {}
        try:
            self.werte = validiere_stammdaten(
                titel=titel, gefahrenarten=gefahrenarten, beschreibung_html=beschreibung_html,
                geltungsbereich=geltungsbereich, verantwortliche_stelle=verantwortliche_stelle,
                wiedervorlage_am=wiedervorlage_am)
        except ValueError as exc:
            self.fehler = str(exc)


def _anhang(db: Session, plan: KatplanPlan, uuid: str) -> KatplanAnhang:
    anhang = db.query(KatplanAnhang).filter_by(org_id=plan.org_id, plan_id=plan.id, uuid=uuid).one_or_none()
    if anhang is None:
        raise HTTPException(404, "Nicht gefunden")
    return anhang


def _anhaenge_kontext(db: Session, user: User, plan: KatplanPlan,
                      elemente: list | None = None, kopf: dict | None = None,
                      version_id: int | None = None) -> dict:
    dateien = {a.uuid: a for a in db.query(KatplanAnhang).filter_by(
        org_id=plan.org_id, plan_id=plan.id).order_by(KatplanAnhang.sort, KatplanAnhang.id).all()}
    objekt_recht = has_role(user, *_LESE_ROLLEN) and ziel_verfuegbar(db, plan.org_id, "objekt")
    dokumente = {d.id: d for d in db.query(ObjektDokument).filter_by(
        org_id=plan.org_id).all()} if objekt_recht else {}

    def ansichten(rohdaten: list[dict]) -> list[dict]:
        result = []
        for daten in rohdaten:
            if daten.get("vertraulich") and not sieht_vertraulich(user):
                continue
            a = dateien.get(daten["uuid"])
            url = "/katastrophenplaene/anhang/" + daten["uuid"]
            suffix = f"?version_id={version_id}" if version_id is not None else ""
            dokument = dokumente.get(daten.get("objekt_dokument_id") or 0)
            result.append({**daten, "datei_url": url + suffix, "thumb_url": url + "/thumb" + suffix,
                           "groesse": f"{a.groesse_bytes / 1024:.1f} KB" if a and a.groesse_bytes else "",
                           "urheber": a.urheber if a else "",
                           "aufnahme_datum": a.aufnahme_datum if a else None,
                           "sort": a.sort if a else 0,
                           "objekt_recht": objekt_recht and dokument is not None,
                           "dokument_name": dokument.dateiname_original if dokument else "Dokument"})
        return result

    if elemente is not None:
        element_map = {e.id: ansichten(json.loads(e.anhaenge_json)) for e in elemente}
        plan_roh = (kopf or {}).get("anhaenge", [])
    else:
        element_map = {}
        plan_roh = []
        for a in dateien.values():
            if a.geloescht_am:
                continue
            daten = {**anhang_service.anhang_snapshot(a), "abschnitt_id": a.abschnitt_id}
            if a.element_id:
                element_map.setdefault(a.element_id, []).extend(ansichten([daten]))
            else:
                plan_roh.append(daten)
    kapitel_map: dict[int, list] = {}
    for a in plan_roh:
        if a.get("abschnitt_id"):
            kapitel_map.setdefault(a["abschnitt_id"], []).extend(ansichten([a]))
    return {"element_anhaenge": element_map, "kapitel_anhaenge": kapitel_map,
            "plan_anhaenge": ansichten([a for a in plan_roh if not a.get("abschnitt_id")]),
            "objektmodul": ziel_verfuegbar(db, plan.org_id, "objekt")}


def _anhang_antwort(request: Request, db: Session, user: User, plan: KatplanPlan,
                    element_id: int | None = None, abschnitt_id: int | None = None,
                    fehler: str = "", status: int = 200):
    element = db.query(KatplanElement).filter_by(
        org_id=plan.org_id, plan_id=plan.id, id=element_id).one_or_none() if element_id else None
    return templates.TemplateResponse(request, "katplan/_anhaenge.html", {
        **_kontext(db, user, plan), "anhang_element_id": element_id,
        "anhang_abschnitt_id": abschnitt_id, "anhang_fehler": fehler,
        "anhang_version": element.version if element else None, "anhang_oob": True,
    }, status_code=status)


@router.get("/suche")
def suche(request: Request, db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    form = dict(request.query_params)
    fehler = None
    zahlen: dict[str, Any] = {}
    try:
        for name in ("plan_id", "prioritaet", "phase", "lat", "lng", "radius_m"):
            roh = form.get(name, "").strip()
            zahlen[name] = (float(roh) if name in {"lat", "lng", "radius_m"} else int(roh)) if roh else None
        for name in ("plan_id", "phase"):
            if zahlen[name] is not None and not 0 < zahlen[name] <= 9223372036854775807:
                raise ValueError("Ungültige ID.")
        if zahlen["prioritaet"] is not None and zahlen["prioritaet"] not in (1, 2, 3):
            raise ValueError("Bitte eine gültige Priorität auswählen.")
    except (ValueError, OverflowError):
        fehler = "Bitte gültige Zahlen für Plan, Priorität, Phase und Umkreis eingeben."
    arbeit = form.get("arbeitsstand") == "1" and darf_bearbeiten(user)
    plaene = suche_service.sichtbare_plaene(db, user, arbeit)
    phasen: list[dict[str, Any]] = next((p["phasen"] for p in plaene if p["id"] == zahlen.get("plan_id")), [])
    ergebnis: dict[str, Any] = {"treffer": [], "gesamt": 0, "abgeschnitten": False}
    if not fehler:
        try:
            ergebnis = suche_service.suche(
                db, user, q=form.get("q", ""), plan_id=zahlen["plan_id"], gefahr=form.get("gefahr", ""),
                typ=form.get("typ", ""), prioritaet=zahlen["prioritaet"], phase=str(zahlen["phase"] or ""),
                ort=form.get("ort", ""), nur_geprueft=form.get("nur_geprueft") == "1",
                lat=zahlen["lat"], lng=zahlen["lng"], radius_m=zahlen["radius_m"], arbeitsstand=arbeit,
            )
        except ValueError as exc:
            fehler = str(exc)
    for treffer in ergebnis["treffer"]:
        treffer["ausschnitt_html"] = suche_service.markiere(treffer["ausschnitt"], form.get("q", ""))
    leer = not any(form.get(k, "").strip() for k in (
        "q", "plan_id", "gefahr", "typ", "prioritaet", "ort", "nur_geprueft", "lat", "lng", "radius_m"))
    context = {"user": user, "can_edit": darf_bearbeiten(user), "form": form, "plaene": plaene,
               "phasen": phasen, "typen": ELEMENT_TYPEN, "gefahrenarten": KATPLAN_GEFAHRENARTEN,
               "fehler": fehler, "leere_suche": leer, **ergebnis}
    fragment = request.headers.get("HX-Request") == "true"
    return templates.TemplateResponse(request, "katplan/_suche_treffer.html" if fragment else "katplan/suche.html",
                                      {**context, "fragment": fragment})


@router.get("/{plan_id}/objektdokumente/suche")
def objektdokument_suche(plan_id: int, request: Request, q: str = "",
                         db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    treffer = []
    if ziel_verfuegbar(db, plan.org_id, "objekt") and len(q.strip()) >= 2:
        muster = "%" + q.strip().replace("%", r"\%").replace("_", r"\_") + "%"
        treffer = db.query(ObjektDokument, Objekt.name).join(Objekt, Objekt.id == ObjektDokument.objekt_id).filter(
            ObjektDokument.org_id == plan.org_id, Objekt.org_id == plan.org_id,
            ObjektDokument.ist_aktuelle_version.is_(True),
            or_(Objekt.name.ilike(muster, escape="\\"),
                ObjektDokument.dateiname_original.ilike(muster, escape="\\")),
        ).order_by(Objekt.name, ObjektDokument.dateiname_original).limit(30).all()
    return templates.TemplateResponse(request, "katplan/_objektdokument_treffer.html", {"treffer": treffer})


@router.get("/anhang/{uuid}")
@router.get("/anhang/{uuid}/thumb")
def anhang_datei(uuid: str, request: Request, version_id: int | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    anhang = db.query(KatplanAnhang).filter_by(org_id=user.org_id, uuid=uuid).one_or_none()
    if anhang is None:
        raise HTTPException(404, "Nicht gefunden")
    plan = _plan(db, anhang.plan_id, user)
    bearbeiten = darf_bearbeiten(user)
    # Leser duerfen wie in der Versionshistorie jede freigegebene Version des Plans sehen;
    # anhaenge_aus_version prueft, dass die Version zu diesem Plan gehoert.
    if not bearbeiten and (plan.status == "archiviert" or not plan.aktuelle_version_id):
        raise HTTPException(404, "Nicht gefunden")
    nur_version = not bearbeiten or anhang.geloescht_am is not None or version_id is not None
    if nur_version:
        vid = version_id if version_id is not None else plan.aktuelle_version_id
        referenz = next((a for a in anhang_service.anhaenge_aus_version(db, plan, vid)
                         if a["uuid"] == uuid), None) if vid else None
        if referenz is None or (referenz["vertraulich"] and not sieht_vertraulich(user)):
            raise HTTPException(404, "Nicht gefunden")
    else:
        element = db.query(KatplanElement).filter_by(
            org_id=plan.org_id, plan_id=plan.id, id=anhang.element_id,
        ).one_or_none() if anhang.element_id else None
        if (anhang.vertraulich or (element and element.vertraulich)) and not sieht_vertraulich(user):
            raise HTTPException(404, "Nicht gefunden")
    thumb = request.url.path.endswith("/thumb")
    if anhang.art == "objekt_dokument" and not thumb:
        from app.models.objekt import ObjektDokument
        if not has_role(user, *_LESE_ROLLEN) or not ziel_verfuegbar(db, user.org_id, "objekt"):
            raise HTTPException(404, "Nicht gefunden")
        dokument = db.query(ObjektDokument).filter_by(id=anhang.objekt_dokument_id, org_id=user.org_id).first()
        if dokument is None:
            raise HTTPException(404, "Nicht gefunden")
        return RedirectResponse(f"/objekt-medien/dokument/{dokument.id}/original", status_code=303)
    rel = anhang.thumb_pfad if thumb else anhang.pfad
    if anhang.art not in ("foto", "datei") or not rel:
        raise HTTPException(404, "Nicht gefunden")
    root = _storage_root().resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Nicht gefunden")
    mime = "image/jpeg" if thumb else anhang.mime or "application/octet-stream"
    disposition = "inline" if anhang.art == "foto" or mime == "application/pdf" else "attachment"
    return FileResponse(path, media_type=mime, filename=anhang.dateiname or "datei",
                        content_disposition_type=disposition, headers={"X-Content-Type-Options": "nosniff"})


@router.post("/{plan_id}/anhaenge")
def anhang_anlegen(plan_id: int, request: Request, datei: UploadFile | None = File(None),
                   url: str = Form(""), objekt_dokument_id: int | None = Form(None),
                   element_id: int | None = Form(None), abschnitt_id: int | None = Form(None),
                   beschriftung: str = Form(""), urheber: str = Form(""), aufnahme_datum: str = Form(""),
                   vertraulich: bool = Form(False), seiten: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    try:
        if sum((datei is not None, bool(url), objekt_dokument_id is not None)) != 1:
            raise ValueError("Bitte genau eine Datei, einen Link oder ein Objektdokument angeben.")
        element = db.query(KatplanElement).filter_by(
            id=element_id, org_id=plan.org_id, plan_id=plan.id,
        ).one_or_none() if element_id is not None else None
        abschnitt = db.query(KatplanAbschnitt).filter_by(
            id=abschnitt_id, org_id=plan.org_id, plan_id=plan.id,
        ).one_or_none() if abschnitt_id is not None else None
        if (element_id is not None and element is None) or (abschnitt_id is not None and abschnitt is None):
            raise ValueError("Element/Kapitel gehört nicht zu diesem Plan.")
        kwargs: dict[str, Any] = {"element": element, "abschnitt": abschnitt, "beschriftung": beschriftung,
                  "urheber": urheber, "aufnahme_datum": date.fromisoformat(aufnahme_datum) if aufnahme_datum else None,
                  "vertraulich": vertraulich}
        if datei is not None:
            anhang_service.anhang_hochladen(db, plan, user, run_async(datei.read),
                                            datei.filename or "datei", datei.content_type, **kwargs)
        elif objekt_dokument_id is not None:
            anhang_service.objektdokument_verlinken(db, plan, user, objekt_dokument_id, seiten, **kwargs)
        else:
            anhang_service.link_hinzufuegen(db, plan, user, url, **kwargs)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _anhang_antwort(request, db, user, plan, element_id, abschnitt_id, str(exc), 422)
    except HTTPException as exc:
        db.rollback()
        if exc.status_code not in (413, 415, 422):
            raise
        return _anhang_antwort(request, db, user, plan, element_id, abschnitt_id, str(exc.detail), exc.status_code)
    return _anhang_antwort(request, db, user, plan, element_id, abschnitt_id)


@router.post("/{plan_id}/anhaenge/{uuid}")
def anhang_bearbeiten(plan_id: int, uuid: str, request: Request, beschriftung: str = Form(""),
                      urheber: str = Form(""), aufnahme_datum: str = Form(""),
                      vertraulich: bool = Form(False), sort: int = Form(0),
                      db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    anhang = _anhang(db, plan, uuid)
    try:
        anhang_service.anhang_aendern(db, plan, user, anhang, beschriftung=beschriftung,
                                     urheber=urheber, aufnahme_datum=date.fromisoformat(aufnahme_datum)
                                     if aufnahme_datum else None, vertraulich=vertraulich, sort=sort)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _anhang_antwort(request, db, user, plan, anhang.element_id, anhang.abschnitt_id, str(exc), 422)
    return _anhang_antwort(request, db, user, plan, anhang.element_id, anhang.abschnitt_id)


@router.post("/{plan_id}/anhaenge/{uuid}/loeschen")
def anhang_loeschen(plan_id: int, uuid: str, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    anhang = _anhang(db, plan, uuid)
    element_id, abschnitt_id = anhang.element_id, anhang.abschnitt_id
    try:
        anhang_service.anhang_entfernen(db, plan, user, anhang)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _anhang_antwort(request, db, user, plan, element_id, abschnitt_id, str(exc), 422)
    except HTTPException as exc:
        db.rollback()
        if exc.status_code not in (413, 415, 422):
            raise
        return _anhang_antwort(request, db, user, plan, element_id, abschnitt_id, str(exc.detail), exc.status_code)
    return _anhang_antwort(request, db, user, plan, element_id, abschnitt_id)


@router.get("")
def liste(request: Request, gefahr: str = "", status: str = "", q: str = "", archiv: int = 0,
          db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    query = db.query(KatplanPlan).filter(KatplanPlan.org_id == user.org_id)
    if not darf_bearbeiten(user):
        query = query.filter(KatplanPlan.aktuelle_version_id.is_not(None), KatplanPlan.status != "archiviert")
    elif archiv != 1:
        query = query.filter(KatplanPlan.status != "archiviert")
    if status and darf_bearbeiten(user):
        query = query.filter(KatplanPlan.status == status)
    elif status and status != "freigegeben":
        query = query.filter(KatplanPlan.id == -1)
    if q.strip() and darf_bearbeiten(user):
        query = query.filter(KatplanPlan.titel.ilike(f"%{q.strip()}%"))
    plaene: list[Any] = query.order_by(KatplanPlan.aktualisiert_am.desc()).all()
    version_ids = [p.aktuelle_version_id for p in plaene if p.aktuelle_version_id]
    versionen = {v.id: v for v in db.query(KatplanVersion).filter(
        KatplanVersion.org_id == user.org_id, KatplanVersion.id.in_(version_ids),
    ).all()} if version_ids else {}
    if not darf_bearbeiten(user):
        leseliste = []
        for plan in plaene:
            v = versionen.get(plan.aktuelle_version_id or 0)
            if v is None:
                continue
            daten = json.loads(v.plan_json)
            termin = daten.get("wiedervorlage_am")
            leseliste.append({**daten, "id": plan.id, "status": "freigegeben",
                              "wiedervorlage_am": date.fromisoformat(termin) if termin else None,
                              "aktualisiert_am": v.freigegeben_am})
        plaene = leseliste
        if q.strip():
            plaene = [p for p in plaene if q.strip().casefold() in p["titel"].casefold()]
    if gefahr:
        plaene = [plan for plan in plaene
                  if gefahr in (plan["gefahrenarten"] if isinstance(plan, dict) else plan.gefahrenarten)]
    context = {**_kontext(db, user), "plaene": plaene, "gefahr": gefahr,
               "status": status, "q": q, "archiv": archiv, "versionen": versionen,
               "qs_anzahlen": {p.id: sum(qs_service.plan_zusammenfassung(db, p, now_local(user.org).date()).values())
                               for p in plaene} if darf_bearbeiten(user) else {}}
    name = "_liste.html" if request.headers.get("HX-Request") == "true" else "index.html"
    return templates.TemplateResponse(request, f"katplan/{name}", context)


@router.get("/adress-suche")
async def adress_suche(q: str = "", user: User = Depends(require_katplan_lesen)):
    from app.services.geocoding import search_addresses

    return {"treffer": await search_addresses(q, limit=6)}


@router.get("/neu")
def neu(request: Request, db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    return templates.TemplateResponse(request, "katplan/formular.html", {**_kontext(db, user), "form": {}})


@router.post("/neu")
def anlegen(request: Request, daten: Stammdaten = Depends(), db: Session = Depends(get_db),
            user: User = Depends(require_katplan_bearbeiten)):
    if daten.fehler:
        return templates.TemplateResponse(request, "katplan/formular.html", {
            **_kontext(db, user), "form": daten.form, "fehler": daten.fehler}, status_code=422)
    plan = KatplanPlan(org_id=user.org_id, erstellt_von_id=user.id, aktualisiert_von_id=user.id, **daten.werte)
    db.add(plan)
    db.flush()
    protokolliere(db, plan, "plan", "angelegt", user)
    db.commit()
    return RedirectResponse(f"/katastrophenplaene/{plan.id}", status_code=303)


def _sichtbarer_plan(db: Session, plan_id: int, user: User) -> KatplanPlan:
    plan = _plan(db, plan_id, user)
    if not darf_bearbeiten(user) and (plan.aktuelle_version_id is None or plan.status == "archiviert"):
        raise HTTPException(404, "Nicht gefunden")
    return plan


def _lade_version(db: Session, plan: KatplanPlan, vid: int | None = None):
    try:
        return freigabe_service.lade_version(db, plan, vid)
    except ValueError as exc:
        raise HTTPException(404, "Nicht gefunden") from exc


def _lesekontext(db: Session, user: User, plan: KatplanPlan, vid: int | None = None) -> dict:
    version, daten, elemente = _lade_version(db, plan, vid)
    elemente = [e for e in elemente if sieht_vertraulich(user) or not e.vertraulich]
    termin = daten.get("wiedervorlage_am")
    return {"lesekopf": {**daten, "wiedervorlage_am": date.fromisoformat(termin) if termin else None},
            "plan_daten": daten, "leseversion": version,
            "quellen_nach_id": {q["id"]: q for q in daten.get("quellen", [])},
            **_link_kontext(db, user, plan, elemente, daten),
            **_anhaenge_kontext(db, user, plan, elemente, daten, version.id),
            "version_baum": freigabe_service.version_baum(daten, elemente, mit_vertraulichen=True),
            "typen": ELEMENT_TYPEN,
            "element_daten": {e.id: json.loads(e.daten_json or "{}") for e in elemente},
            "element_tags": {e.id: json.loads(e.tags_json or "[]") for e in elemente},
            "element_gsl": {e.id: json.loads(e.gsl_json or "{}") for e in elemente},
            "element_phasen": {e.id: [p for p in daten.get("phasen", [])
                                      if p["id"] in json.loads(e.phasen_json)] for e in elemente}}


@router.get("/{plan_id}")
def detail(plan_id: int, request: Request, ansicht: str = "", db: Session = Depends(get_db),
           user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    arbeitsstand = _ist_arbeitsstand(user, plan, ansicht)
    context = {**_kontext(db, user, plan), "arbeitsstand": arbeitsstand}
    if not arbeitsstand:
        context.update(_lesekontext(db, user, plan))
    context.update(_kartenkontext(db, user, plan, arbeitsstand))
    return templates.TemplateResponse(request, "katplan/detail.html", context)


def _ist_arbeitsstand(user: User, plan: KatplanPlan, ansicht: str) -> bool:
    return darf_bearbeiten(user) and (not plan.aktuelle_version_id or ansicht == "arbeitsstand"
           or (ansicht != "freigabe" and plan.status != "freigegeben"))


def _kartendaten(db: Session, user: User, plan: KatplanPlan, arbeitsstand: bool) -> tuple[dict, list[dict]]:
    if arbeitsstand:
        kopf, elemente = freigabe_service.erstelle_snapshot(db, plan, flush=False)
    else:
        _, kopf, snapshots = _lade_version(db, plan)
        elemente = [{c.name: getattr(e, c.name) for c in KatplanVersionElement.__table__.columns}
                    for e in snapshots]
    return kopf, [e for e in elemente if sieht_vertraulich(user) or not e["vertraulich"]]


def _kartenkontext(db: Session, user: User, plan: KatplanPlan, arbeitsstand: bool) -> dict:
    kopf, elemente = _kartendaten(db, user, plan, arbeitsstand)
    features = []
    unverortet = []
    suffix = "?ansicht=arbeitsstand" if arbeitsstand else "?ansicht=freigabe"
    for e in elemente:
        uuid = e["element_uuid"]
        typ = ELEMENT_TYPEN[e["typ"]]
        eigenschaften = {"uuid": uuid, "kennung": e["kennung"], "titel": e["titel"],
                        "typ": e["typ"], "typ_label": typ.label, "typ_icon": typ.icon,
                        "prioritaet": e["prioritaet"], "geo_status": e["geo_status"],
                        "phasen": json.loads(e["phasen_json"]),
                        "url": f"/katastrophenplaene/{plan.id}"
                               + (suffix if arbeitsstand else "") + f"#element-{uuid}"}
        geometrie = json.loads(e["geometry_json"]) if e["geometry_json"] else None
        if geometrie is None and e["lat"] is not None and e["lng"] is not None:
            geometrie = {"type": "Point", "coordinates": [e["lng"], e["lat"]]}
        if geometrie:
            features.append({"type": "Feature", "geometry": geometrie, "properties": eigenschaften})
        else:
            unverortet.append(eigenschaften)
    return {"karten_geojson": {"type": "FeatureCollection", "features": features},
            "karten_unverortet": unverortet, "karten_phasen": kopf.get("phasen", []),
            "karten_titel": kopf["titel"], "karten_suffix": suffix,
            "karten_url": f"/katastrophenplaene/{plan.id}/karte" + suffix}


def _kopfkontext(db: Session, user: User, plan: KatplanPlan) -> dict:
    arbeitsstand = _ist_arbeitsstand(user, plan, "")
    return {**_kontext(db, user, plan), "arbeitsstand": arbeitsstand,
            **(_lesekontext(db, user, plan) if not arbeitsstand else {}),
            **_kartenkontext(db, user, plan, arbeitsstand)}


@router.get("/{plan_id}/karte.geojson")
def karte_geojson(plan_id: int, ansicht: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    return _kartenkontext(db, user, plan, _ist_arbeitsstand(user, plan, ansicht))["karten_geojson"]


@router.get("/{plan_id}/karte")
def karte(plan_id: int, request: Request, ansicht: str = "", db: Session = Depends(get_db),
          user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    arbeitsstand = _ist_arbeitsstand(user, plan, ansicht)
    context = {"user": user, "plan": plan, "can_edit": darf_bearbeiten(user),
               "arbeitsstand": arbeitsstand, "typen": ELEMENT_TYPEN,
               **_kartenkontext(db, user, plan, arbeitsstand)}
    return templates.TemplateResponse(request, "katplan/karte.html", context)


@router.get("/{plan_id}/versionen")
def versionen(plan_id: int, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    zeilen = db.query(KatplanVersion, User.display_name).outerjoin(
        User, (User.id == KatplanVersion.freigegeben_von_id) & (User.org_id == plan.org_id),
    ).filter(KatplanVersion.plan_id == plan.id, KatplanVersion.org_id == plan.org_id).order_by(
        KatplanVersion.versionsnummer).all()
    pruefungen = db.query(KatplanPruefung, User.display_name).outerjoin(
        User, (User.id == KatplanPruefung.user_id) & (User.org_id == plan.org_id),
    ).filter(KatplanPruefung.plan_id == plan.id, KatplanPruefung.org_id == plan.org_id).order_by(
        KatplanPruefung.zeit.desc(), KatplanPruefung.id.desc()).all()
    context = {**_kontext(db, user, plan), "versionen_liste": zeilen, "pruefungen": pruefungen}
    if not darf_bearbeiten(user):
        context.update(_lesekontext(db, user, plan))
    return templates.TemplateResponse(request, "katplan/versionen.html", context)


@router.get("/{plan_id}/versionen/{vid}")
def historische_version(plan_id: int, vid: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    return templates.TemplateResponse(request, "katplan/detail.html", {
        **_kontext(db, user, plan), **_lesekontext(db, user, plan, vid), "historisch": True})


@router.get("/{plan_id}/vergleich")
def vergleich(plan_id: int, request: Request, von: int | None = None, nach: str = "",
              db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    if nach == "arbeitsstand" and not darf_bearbeiten(user):
        raise HTTPException(403, "Keine Berechtigung")
    ids = [v.id for v in db.query(KatplanVersion).filter_by(plan_id=plan.id, org_id=plan.org_id).order_by(
        KatplanVersion.versionsnummer).all()]
    if not ids:
        raise HTTPException(404, "Nicht gefunden")
    alt, alt_kopf, alt_elemente = _lade_version(db, plan, von if von is not None else ids[max(0, len(ids) - 2)])
    if nach == "arbeitsstand":
        with db.no_autoflush:
            neu_kopf, snapshot = freigabe_service.erstelle_snapshot(db, plan, flush=False)
        neu_elemente = [KatplanVersionElement(org_id=plan.org_id, **daten) for daten in snapshot]
        nach_label = "Arbeitsstand"
    else:
        try:
            nid = int(nach) if nach else ids[-1]
        except ValueError as exc:
            raise HTTPException(404, "Nicht gefunden") from exc
        neu, neu_kopf, neu_elemente = _lade_version(db, plan, nid)
        nach_label = f"Version {neu.versionsnummer}"
    # Bei Vertraulichkeitswechseln weder alte noch neue Inhalte offenlegen.
    verborgen = {e.element_uuid for e in [*alt_elemente, *neu_elemente] if e.vertraulich}
    if not sieht_vertraulich(user):
        alt_elemente = [e for e in alt_elemente if e.element_uuid not in verborgen]
        neu_elemente = [e for e in neu_elemente if e.element_uuid not in verborgen]
    diff = freigabe_service.vergleiche_versionen(alt_elemente, neu_elemente, alt_kopf, neu_kopf,
                                                mit_vertraulichen=sieht_vertraulich(user))
    feldgruppen = [diff["kopf"], *(e["felder"] for e in diff["geaendert"])]
    for gruppe in feldgruppen:
        for feld in gruppe:
            if feld["feld"] == "wiedervorlage_am":
                for seite in ("alt", "neu"):
                    if feld[seite]:
                        feld[seite] = date.fromisoformat(feld[seite])
    namen = {e.element_uuid: e.titel for e in [*alt_elemente, *neu_elemente]}
    context = {**_kontext(db, user, plan), "vergleich": diff, "element_namen": namen,
               "von_label": f"Version {alt.versionsnummer}", "nach_label": nach_label}
    if not darf_bearbeiten(user):
        context.update(_lesekontext(db, user, plan))
    return templates.TemplateResponse(request, "katplan/vergleich.html", context)


@router.get("/{plan_id}/stammdaten")
def stammdaten(plan_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    form = {feld: getattr(plan, feld) or "" for feld in (
        "titel", "beschreibung_html", "geltungsbereich", "verantwortliche_stelle", "wiedervorlage_am")}
    form["gefahrenarten"] = plan.gefahrenarten
    return templates.TemplateResponse(request, "katplan/_formular.html", {
        **_kontext(db, user, plan), "form": form, "formular_version": plan.version})


@router.post("/{plan_id}/stammdaten")
def speichern(plan_id: int, request: Request, version: int = Form(-1), daten: Stammdaten = Depends(),
              db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    fehler = daten.fehler
    status_code = 422
    try:
        pruefe_version(plan, version)
    except KatplanKonflikt as exc:
        fehler = str(exc)
        status_code = 409
    if fehler:
        return templates.TemplateResponse(request, "katplan/_formular.html", {
            **_kontext(db, user, plan), "form": daten.form, "fehler": fehler,
            "formular_version": version}, status_code=status_code)
    stammdaten_aendern(db, plan, user, version=version, werte=daten.werte)
    db.commit()
    return templates.TemplateResponse(request, "katplan/_kopf.html", {
        **_kopfkontext(db, user, plan), "protokoll_oob": True})


def _statuswechsel(request: Request, db: Session, user: User, plan_id: int, status: str, aktion: str):
    plan = _plan(db, plan_id, user)
    alt = plan.status
    plan.status = status
    plan.version += 1
    plan.aktualisiert_am = datetime.now(UTC).replace(tzinfo=None)
    plan.aktualisiert_von_id = user.id
    protokolliere(db, plan, "plan", aktion, user, feld="status", alt=alt, neu=status)
    db.add(KatplanPruefung(org_id=plan.org_id, plan_id=plan.id, aktion=aktion, user_id=user.id))
    db.commit()
    return templates.TemplateResponse(request, "katplan/_kopf.html", {
        **_kopfkontext(db, user, plan), "protokoll_oob": True})


@router.post("/{plan_id}/archivieren")
def archivieren(plan_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_katplan_freigeben)):
    return _statuswechsel(request, db, user, plan_id, "archiviert", "archiviert")


@router.post("/{plan_id}/reaktivieren")
def reaktivieren(plan_id: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_katplan_freigeben)):
    plan = _plan(db, plan_id, user)
    return _statuswechsel(request, db, user, plan_id,
                          "freigegeben" if plan.aktuelle_version_id else "entwurf", "reaktiviert")


_FARBEN = ("gruen", "gelb", "orange", "rot", "blau", "grau")


def _objekt[Objekt: (KatplanAbschnitt, KatplanElement, KatplanPhase)](
    db: Session, modell: type[Objekt], objekt_id: int, plan: KatplanPlan, user: User,
) -> Objekt:
    obj = db.query(modell).filter_by(id=objekt_id, plan_id=plan.id, org_id=user.org_id).one_or_none()
    if obj is None:
        raise HTTPException(404, "Nicht gefunden")
    return obj


def _editor_kontext(db: Session, plan: KatplanPlan) -> dict[str, Any]:
    phasen = db.query(KatplanPhase).filter_by(org_id=plan.org_id, plan_id=plan.id).order_by(
        KatplanPhase.sort, KatplanPhase.id,
    ).all()
    elemente = db.query(KatplanElement).filter_by(org_id=plan.org_id, plan_id=plan.id).all()
    zuordnungen = db.query(KatplanElementPhase).filter(
        KatplanElementPhase.org_id == plan.org_id,
        KatplanElementPhase.element_id.in_([e.id for e in elemente]),
        KatplanElementPhase.phase_id.in_([p.id for p in phasen]),
    ).all()
    heute = now_local(db.get(FireDept, plan.org_id)).date()
    version_links = qs_service.freigabe_links(db, plan)
    hinweise = {e.id: qs_service.element_hinweise(db, plan, e, heute=heute, version_links=version_links)
                for e in elemente}
    summen: dict[str, dict] = {}
    for e in elemente:
        for h in hinweise[e.id]:
            if h["code"] not in summen:
                summen[h["code"]] = {"text": h["text"], "anzahl": 0, "element_id": e.id}
            summen[h["code"]]["anzahl"] += 1
    return {"qs_hinweise": hinweise, "qs_summen": summen, "qs_symbole": qs_service.SYMBOLE,
            "qs_anzahl": sum(h["anzahl"] for h in summen.values()),
            "baum": abschnitte_baum(db, plan), "typen": ELEMENT_TYPEN, "phasen": phasen,
            "vorlagen": ELEMENT_VORLAGEN, "auftragsarten": AUFTRAGSART_LABEL,
            "ziel_plaene": db.query(KatplanPlan).filter(
                KatplanPlan.org_id == plan.org_id, KatplanPlan.status != "archiviert",
            ).order_by(KatplanPlan.titel).all(),
            "fehlende_link_elemente": {link.element_id for link in db.query(KatplanLink).filter_by(
                org_id=plan.org_id, plan_id=plan.id).all()
                if link_service.link_ansicht(db, link, voll=False)["fehlt"]},
            "farben": _FARBEN, "abschnitte": db.query(KatplanAbschnitt).filter_by(
                org_id=plan.org_id, plan_id=plan.id,
            ).order_by(KatplanAbschnitt.sort, KatplanAbschnitt.id).all(),
            "element_daten": {e.id: json.loads(e.daten_json or "{}") for e in elemente},
            "quellen_nach_id": {q.id: q for q in db.query(KatplanDokument).filter_by(
                org_id=plan.org_id, plan_id=plan.id).all()},
            "element_tags": {e.id: json.loads(e.tags_json or "[]") for e in elemente},
            "element_phasen": {e.id: [p for p in phasen if any(
                z.element_id == e.id and z.phase_id == p.id for z in zuordnungen
            )] for e in elemente}}


def _antwort(request: Request, db: Session, user: User, plan: KatplanPlan,
             partial: str = "_leer.html", status: int = 200, **werte: Any):
    return templates.TemplateResponse(request, "katplan/_editor_antwort.html", {
        **_kontext(db, user, plan), "partial": partial, "baum_oob": True, **werte,
    }, status_code=status)


def _formular_element(db: Session, element: KatplanElement) -> dict[str, Any]:
    form = {feld: getattr(element, feld) or "" for feld in (
        "titel", "kennung", "abschnitt_id", "beschreibung_html", "prioritaet", "vertraulich",
        "gueltig_bis", "pruefen_am", "strasse", "hausnummer", "plz", "ort", "version",
        "gsl_vorlage", "gsl_auftragsart", "gsl_prioritaet", "gsl_voraussetzungen",
        "quelle_dokument_id", "quelle_seiten")}
    form["gsl_dauer_min"] = element.gsl_dauer_min if element.gsl_dauer_min is not None else ""
    form["gsl_ressourcentypen"] = ", ".join(json.loads(element.gsl_ressourcentypen_json or "[]"))
    form["tags"] = ", ".join(json.loads(element.tags_json or "[]"))
    form["phasen"] = [str(z.phase_id) for z in db.query(KatplanElementPhase).filter_by(
        org_id=element.org_id, element_id=element.id,
    ).all()]
    form["geometry_json"] = element.geometry_json or ""
    form["geo_geprueft"] = element.geo_status == "geprueft"
    form["version"] = element.version
    return form


def _pflicht(wert: str, maximum: int, name: str) -> str:
    if not wert.strip() or len(wert.strip()) > maximum:
        raise ValueError(f"{name}: Bitte 1 bis {maximum} Zeichen eingeben.")
    return wert.strip()


def _abschnitt_id(db: Session, roh: str, plan: KatplanPlan, user: User) -> int | None:
    return _objekt(db, KatplanAbschnitt, int(roh), plan, user).id if roh else None


@router.get("/{plan_id}/bearbeiten")
def editor(plan_id: int, request: Request, element: int | None = None, db: Session = Depends(get_db),
           user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    context = _kontext(db, user, plan)
    if element is not None:
        vorauswahl = _objekt(db, KatplanElement, element, plan, user)
        context.update(element=vorauswahl, aktiv_element_id=vorauswahl.id,
                       form=_formular_element(db, vorauswahl), daten=json.loads(vorauswahl.daten_json or "{}"))
    return templates.TemplateResponse(request, "katplan/editor.html", context)


@router.post("/{plan_id}/abschnitte")
def abschnitt_anlegen(plan_id: int, request: Request, titel: str = Form(""), parent_id: str = Form(""),
                      db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    try:
        titel = _pflicht(titel, 200, "Titel")
        parent = _abschnitt_id(db, parent_id, plan, user)
    except ValueError as exc:
        return _antwort(request, db, user, plan, status=422, fehler=str(exc))
    abschnitt = lege_abschnitt_an(db, plan, user, titel=titel, parent_id=parent)
    db.commit()
    return _antwort(request, db, user, plan, "_abschnitt_form.html", abschnitt=abschnitt)


@router.get("/{plan_id}/abschnitte/{aid}")
def abschnitt_form(plan_id: int, aid: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    return _antwort(request, db, user, plan, "_abschnitt_form.html",
                    abschnitt=_objekt(db, KatplanAbschnitt, aid, plan, user), baum_oob=False)


@router.post("/{plan_id}/abschnitte/{aid}")
def abschnitt_speichern(plan_id: int, aid: int, request: Request, titel: str = Form(""),
                        beschreibung_html: str = Form(""), db: Session = Depends(get_db),
                        user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    abschnitt = _objekt(db, KatplanAbschnitt, aid, plan, user)
    try:
        werte = {"titel": _pflicht(titel, 200, "Titel"), "beschreibung_html": bereinige_beschreibung(beschreibung_html)}
    except ValueError as exc:
        return _antwort(request, db, user, plan, "_abschnitt_form.html", status=422,
                        abschnitt=abschnitt, fehler=str(exc),
                        form={"titel": titel, "beschreibung_html": bereinige_beschreibung(beschreibung_html) or ""})
    for feld, wert in werte.items():
        alt = getattr(abschnitt, feld)
        if alt != wert:
            setattr(abschnitt, feld, wert)
            protokolliere(db, plan, "abschnitt", "geaendert", user, feld=feld, alt=alt, neu=wert)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_abschnitt_form.html", abschnitt=abschnitt, erfolg="Kapitel gespeichert.")


@router.post("/{plan_id}/abschnitte/{aid}/loeschen")
def abschnitt_loeschen(plan_id: int, aid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    loesche_abschnitt(db, plan, _objekt(db, KatplanAbschnitt, aid, plan, user), user)
    db.commit()
    return _antwort(request, db, user, plan)


@router.get("/{plan_id}/phasen")
def phasen_form(plan_id: int, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_katplan_bearbeiten)):
    return _antwort(request, db, user, _plan(db, plan_id, user), "_phasen.html", baum_oob=False)


def _phase_speichern(request: Request, db: Session, user: User, plan: KatplanPlan, phase: KatplanPhase | None):
    roh = run_async(request.form)
    form = {k: str(v) for k, v in roh.items()}
    try:
        name = _pflicht(form.get("name", ""), 120, "Name")
        farbe = form.get("farbe", "grau")
        if farbe not in _FARBEN:
            raise ValueError("Bitte eine gültige Farbe wählen.")
        werte = {"name": name, "beschreibung": form.get("beschreibung", ""),
                 "ausloeser": form.get("ausloeser", ""), "farbe": farbe, "sort": int(form.get("sort", "0"))}
    except ValueError as exc:
        return _antwort(request, db, user, plan, "_phasen.html", status=422,
                        fehler=str(exc), phase_form=form, phase_fehler_id=phase.id if phase else 0)
    neu = phase is None
    if phase is None:
        phase = lege_phase_an(db, plan, user, name=name)
    for feld, wert in werte.items():
        alt = getattr(phase, feld)
        if not neu and alt != wert:
            protokolliere(db, plan, "phase", "geaendert", user, feld=feld,
                          alt=str(alt) if alt is not None else None, neu=str(wert))
        setattr(phase, feld, wert)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_phasen.html", erfolg="Phase gespeichert.")


@router.post("/{plan_id}/phasen")
def phase_anlegen(plan_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_katplan_bearbeiten)):
    return _phase_speichern(request, db, user, _plan(db, plan_id, user), None)


@router.post("/{plan_id}/phasen/{pid}")
def phase_speichern(plan_id: int, pid: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    return _phase_speichern(request, db, user, plan, _objekt(db, KatplanPhase, pid, plan, user))


@router.post("/{plan_id}/phasen/{pid}/loeschen")
def phase_loeschen(plan_id: int, pid: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    phase = _objekt(db, KatplanPhase, pid, plan, user)
    for zuordnung in db.query(KatplanElementPhase).join(
        KatplanElement, KatplanElement.id == KatplanElementPhase.element_id,
    ).filter(KatplanElementPhase.org_id == user.org_id, KatplanElementPhase.phase_id == phase.id,
             KatplanElement.org_id == user.org_id, KatplanElement.plan_id == plan.id).all():
        element = _objekt(db, KatplanElement, zuordnung.element_id, plan, user)
        element.version += 1
        element.aktualisiert_von_id = user.id
        aktualisiere_element_suchtext(element)
        protokolliere(db, plan, "element", "geaendert", user, feld="phasen",
                      alt=phase.name, element_id=element.id)
        db.delete(zuordnung)
    db.delete(phase)
    protokolliere(db, plan, "phase", "geloescht", user, alt=phase.name)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_phasen.html")


@router.post("/{plan_id}/elemente")
def element_anlegen(plan_id: int, request: Request, typ: str = Form(""), titel: str = Form(""),
                    abschnitt_id: str = Form(""), vorlage: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    muster = ELEMENT_VORLAGEN.get(vorlage)
    try:
        if vorlage and muster is None:
            raise ValueError("Unbekannte Elementvorlage.")
        if muster:
            typ = muster.typ
            titel = titel.strip() or muster.titel
        titel = _pflicht(titel, 200, "Titel")
        if typ not in ELEMENT_TYPEN:
            raise ValueError("Unbekannter Elementtyp.")
        abschnitt = _abschnitt_id(db, abschnitt_id, plan, user)
        element = lege_element_an(
            db, plan, user, typ=typ, titel=titel, abschnitt_id=abschnitt,
            daten=bereinige_daten(typ, muster.daten) if muster else {},
            beschreibung_html=muster.beschreibung_html if muster else None,
        )
    except ValueError as exc:
        return _antwort(request, db, user, plan, status=422, fehler=str(exc))
    db.commit()
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element),
                    daten=json.loads(element.daten_json or "{}"))


@router.get("/{plan_id}/elemente/{eid}")
def element_form(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element),
                    daten=json.loads(element.daten_json or "{}"))


@router.post("/{plan_id}/elemente/{eid}")
def element_speichern(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    roh = run_async(request.form)
    form: dict[str, Any] = {k: str(v) for k, v in roh.items()}
    form["beschreibung_html"] = bereinige_beschreibung(form.get("beschreibung_html", "")) or ""
    form["phasen"] = [str(v) for v in roh.getlist("phasen")]
    form["vertraulich"] = bool(roh.get("vertraulich"))
    form["gsl_vorlage"] = bool(roh.get("gsl_vorlage"))
    form["geo_geprueft"] = bool(roh.get("geo_geprueft"))
    daten = json.loads(element.daten_json or "{}")
    try:
        titel = _pflicht(form.get("titel", ""), 200, "Titel")
        kennung = _pflicht(form.get("kennung", ""), 30, "Kennung")
        if db.query(KatplanElement).filter(
            KatplanElement.org_id == user.org_id, KatplanElement.plan_id == plan.id,
            KatplanElement.kennung == kennung, KatplanElement.id != element.id,
        ).first():
            raise ValueError("Diese Kennung ist im Plan bereits vergeben.")
        eingabe = json.loads(form.get("daten_json", "{}"))
        if not isinstance(eingabe, dict):
            raise ValueError("Daten müssen ein JSON-Objekt sein.")
        tags = [t.strip() for t in form.get("tags", "").split(",") if t.strip()]
        werte = validiere_element_felder(
            element.typ, titel=titel, prioritaet=int(form.get("prioritaet", "2")), daten=eingabe,
            tags=tags, adresse=form, beschreibung_html=form.get("beschreibung_html", ""),
            geometrie=form.get("geometry_json"), geo_geprueft=form["geo_geprueft"],
        )
        daten = json.loads(werte["daten_json"])
        phase_ids = {int(v) for v in form["phasen"]}
        for pid in phase_ids:
            _objekt(db, KatplanPhase, pid, plan, user)
        werte.update(kennung=kennung, vertraulich=form["vertraulich"],
                     abschnitt_id=_abschnitt_id(db, form.get("abschnitt_id", ""), plan, user))
        for feld in ("gueltig_bis", "pruefen_am"):
            werte[feld] = date.fromisoformat(form[feld]) if form.get(feld) else None
        auftragsart = form.get("gsl_auftragsart", "").strip() or None
        if auftragsart is not None and auftragsart not in AUFTRAGSART_WERTE:
            raise ValueError("Ungültige GSL-Auftragsart.")
        gsl_prioritaet = int(form["gsl_prioritaet"]) if form.get("gsl_prioritaet") else None
        if gsl_prioritaet is not None and gsl_prioritaet not in (1, 2, 3):
            raise ValueError("GSL-Priorität muss 1, 2 oder 3 sein.")
        dauer = int(form["gsl_dauer_min"]) if form.get("gsl_dauer_min") else None
        if dauer is not None and not 0 <= dauer <= 10080:
            raise ValueError("GSL-Dauer muss zwischen 0 und 10080 Minuten liegen.")
        ressourcen = [r.strip() for r in form.get("gsl_ressourcentypen", "").split(",") if r.strip()]
        if len(ressourcen) > 20 or any(len(r) > 50 for r in ressourcen):
            raise ValueError("Höchstens 20 Ressourcentypen mit je 50 Zeichen eingeben.")
        voraussetzungen = form.get("gsl_voraussetzungen", "").strip()
        if len(voraussetzungen) > 2000:
            raise ValueError("GSL-Voraussetzungen: höchstens 2000 Zeichen.")
        werte.update(gsl_vorlage=form["gsl_vorlage"], gsl_auftragsart=auftragsart,
                     gsl_prioritaet=gsl_prioritaet, gsl_dauer_min=dauer,
                     gsl_ressourcentypen_json=json.dumps(ressourcen, ensure_ascii=False) if ressourcen else None,
                     gsl_voraussetzungen=voraussetzungen or None)
        if "quelle_dokument_id" in form or "quelle_seiten" in form:
            quelle_id = int(form["quelle_dokument_id"]) if form.get("quelle_dokument_id") else None
            quelle_seiten = form.get("quelle_seiten", "").strip()
            quelle_service.validiere_element_quelle(db, plan, quelle_id, quelle_seiten)
            werte.update(quelle_dokument_id=quelle_id, quelle_seiten=quelle_seiten or None)
        pruefe_version(element, int(form.get("version", "-1")))
    except (ValueError, KatplanKonflikt) as exc:
        return _antwort(request, db, user, plan, "_element_form.html",
                        status=409 if isinstance(exc, KatplanKonflikt) else 422,
                        element=element, aktiv_element_id=element.id, form=form, daten=daten,
                        fehler=str(exc))
    for feld, wert in werte.items():
        alt = getattr(element, feld)
        if alt != wert:
            setattr(element, feld, wert)
            protokolliere(db, plan, "element", "geaendert", user,
                          feld={"daten_json": "daten", "geometry_json": "geometrie"}.get(feld, feld),
                          alt=(str(alt)[:200] if feld == "geometry_json" else str(alt)) if alt is not None else None,
                          neu=(str(wert)[:200] if feld == "geometry_json" else str(wert)) if wert is not None else None,
                          element_id=element.id)
    zuordnungen = db.query(KatplanElementPhase).filter_by(org_id=user.org_id, element_id=element.id).all()
    alte_ids = {z.phase_id for z in zuordnungen}
    if alte_ids != phase_ids:
        for z in zuordnungen:
            if z.phase_id not in phase_ids:
                db.delete(z)
        for pid in phase_ids - alte_ids:
            db.add(KatplanElementPhase(org_id=user.org_id, element_id=element.id, phase_id=pid))
        protokolliere(db, plan, "element", "geaendert", user, feld="phasen",
                      alt=json.dumps(sorted(alte_ids)), neu=json.dumps(sorted(phase_ids)), element_id=element.id)
    # Auch das bewusste Speichern unveraenderter KI-Angaben gilt als UI-Pruefung.
    if element.herkunft == "ai_suggested":
        protokolliere(db, plan, "element", "geaendert", user, feld="pruefung",
                      neu="In der UI gespeichert", element_id=element.id)
    element.version += 1
    element.aktualisiert_am = datetime.now(UTC).replace(tzinfo=None)
    element.aktualisiert_von_id = user.id
    aktualisiere_element_suchtext(element)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element), daten=daten,
                    erfolg="Element gespeichert.")


@router.post("/{plan_id}/elemente/{eid}/loeschen")
def element_loeschen(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    for z in db.query(KatplanElementPhase).filter_by(org_id=user.org_id, element_id=element.id).all():
        db.delete(z)
    protokolliere(db, plan, "element", "geloescht", user, element_id=element.id, alt=element.titel)
    db.delete(element)
    markiere_ueberarbeitung(db, plan, user)
    db.commit()
    return _antwort(request, db, user, plan)


class AbschnittSortierung(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int
    parent_id: int | None
    sort: int


class ElementSortierung(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int
    abschnitt_id: int | None
    sort: int


class BaumSortierung(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    abschnitte: list[AbschnittSortierung]
    elemente: list[ElementSortierung]


@router.post("/{plan_id}/sortierung")
def sortierung(plan_id: int, request: Request, daten: BaumSortierung, db: Session = Depends(get_db),
               user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    try:
        sortiere_baum(db, plan, user, [a.model_dump() for a in daten.abschnitte],
                      [e.model_dump() for e in daten.elemente])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return templates.TemplateResponse(request, "katplan/_baum.html", {
        **_kontext(db, user, plan), "sortierung_status": True})


@router.post("/{plan_id}/elemente/{eid}/verschieben")
def element_verschieben(plan_id: int, eid: int, request: Request, richtung: str = Form(""),
                        db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    try:
        verschiebe_nachbar(db, plan, user, element, richtung)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _antwort(request, db, user, plan, "_element_form.html", element=element,
                    aktiv_element_id=element.id, form=_formular_element(db, element),
                    daten=json.loads(element.daten_json or "{}"))


@router.post("/{plan_id}/abschnitte/{aid}/verschieben")
def abschnitt_verschieben(plan_id: int, aid: int, request: Request, richtung: str = Form(""),
                          db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    abschnitt = _objekt(db, KatplanAbschnitt, aid, plan, user)
    try:
        verschiebe_nachbar(db, plan, user, abschnitt, richtung)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _antwort(request, db, user, plan, "_abschnitt_form.html", abschnitt=abschnitt)


@router.post("/{plan_id}/elemente/{eid}/duplizieren")
def element_duplizieren(plan_id: int, eid: int, request: Request, ziel_plan_id: int | None = Form(None),
                        db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    quelle = _objekt(db, KatplanElement, eid, plan, user)
    ziel = _plan(db, ziel_plan_id if ziel_plan_id is not None else plan.id, user)
    if ziel.status == "archiviert":
        raise HTTPException(422, "In archivierte Pläne kann nicht dupliziert werden.")
    gleicher_plan = ziel.id == plan.id
    inhalt = {feld: getattr(quelle, feld) for feld in (
        "typ", "beschreibung_html", "daten_json", "prioritaet", "status", "tags_json",
        "gueltig_bis", "pruefen_am", "vertraulich", "strasse", "hausnummer", "plz", "ort",
        "lat", "lng", "geometry_json", "geo_status", "herkunft", "quelle_dokument_id", "quelle_seiten",
        "gsl_vorlage", "gsl_auftragsart", "gsl_prioritaet", "gsl_ressourcentypen_json",
        "gsl_dauer_min", "gsl_voraussetzungen",
    )}
    abschnitt_id = quelle.abschnitt_id if gleicher_plan else None
    sort = max((e.sort for e in db.query(KatplanElement).filter_by(
        org_id=user.org_id, plan_id=ziel.id, abschnitt_id=abschnitt_id,
    ).all()), default=0) + 10
    kopie = KatplanElement(org_id=user.org_id, plan_id=ziel.id, abschnitt_id=abschnitt_id,
                          kennung=naechste_kennung(db, ziel, quelle.typ),
                          titel=(quelle.titel[:192] + " (Kopie)") if gleicher_plan else quelle.titel,
                          version=0, sort=sort, erstellt_von_id=user.id, aktualisiert_von_id=user.id, **inhalt)
    aktualisiere_element_suchtext(kopie)
    db.add(kopie)
    db.flush()
    if gleicher_plan:
        for z in db.query(KatplanElementPhase).join(
            KatplanPhase, KatplanPhase.id == KatplanElementPhase.phase_id,
        ).filter(KatplanElementPhase.org_id == user.org_id, KatplanElementPhase.element_id == quelle.id,
                 KatplanPhase.org_id == user.org_id, KatplanPhase.plan_id == plan.id).all():
            db.add(KatplanElementPhase(org_id=user.org_id, element_id=kopie.id, phase_id=z.phase_id))
    verweis = f"Quelle: Plan {plan.id}, Element {quelle.uuid}; Ziel: Plan {ziel.id}, Element {kopie.uuid}"
    verweis += "; Anhänge nicht kopiert"
    protokolliere(db, ziel, "element", "dupliziert", user, neu=verweis, element_id=kopie.id)
    if not gleicher_plan:
        protokolliere(db, plan, "element", "dupliziert", user, neu=verweis, element_id=quelle.id)
    markiere_ueberarbeitung(db, ziel, user)
    db.commit()
    if gleicher_plan:
        return _antwort(request, db, user, plan, "_element_form.html", element=kopie,
                        aktiv_element_id=kopie.id, form=_formular_element(db, kopie),
                        daten=json.loads(kopie.daten_json or "{}"))
    return _antwort(request, db, user, plan, "_dupliziert.html", ziel_plan=ziel, baum_oob=False)


def _workflow(request: Request, db: Session, user: User, plan_id: int, aktion: str, text: str):
    plan = _plan(db, plan_id, user)
    try:
        getattr(freigabe_service, aktion)(db, plan, user, text)
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "katplan/_kopf.html", {
            **_kopfkontext(db, user, plan), "fehler": str(exc)}, status_code=422)
    db.commit()
    return templates.TemplateResponse(request, "katplan/_kopf.html", {
        **_kopfkontext(db, user, plan), "protokoll_oob": True})


@router.post("/{plan_id}/einreichen")
def einreichen(plan_id: int, request: Request, kommentar: str = Form(""),
               db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    return _workflow(request, db, user, plan_id, "einreichen", kommentar)


@router.post("/{plan_id}/zurueckweisen")
def zurueckweisen(plan_id: int, request: Request, kommentar: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_katplan_freigeben)):
    return _workflow(request, db, user, plan_id, "zurueckweisen", kommentar)


@router.post("/{plan_id}/freigeben")
def freigeben(plan_id: int, request: Request, aenderungsnotiz: str = Form(""),
              db: Session = Depends(get_db), user: User = Depends(require_katplan_freigeben)):
    return _workflow(request, db, user, plan_id, "freigeben", aenderungsnotiz)


_ZIEL_TYPEN = {"kontakt": "Kontakt", "kontakt_organisation": "Organisation", "objekt": "Objekt",
               "fahrzeug": "Fahrzeug", "wasserstelle": "Wasserstelle"}


def _relation_ansichten(db: Session, user: User, plan: KatplanPlan, relationen: list) -> list[dict]:
    ergebnis = []
    for relation in relationen:
        uuid = relation["zu_element_uuid"]
        ziel = db.query(KatplanElement).join(KatplanPlan, KatplanPlan.id == KatplanElement.plan_id).filter(
            KatplanElement.org_id == plan.org_id, KatplanPlan.org_id == plan.org_id,
            KatplanPlan.status != "archiviert", KatplanElement.uuid == uuid,
        ).one_or_none()
        if ziel is None:
            continue
        ziel_plan = db.query(KatplanPlan).filter_by(id=ziel.plan_id, org_id=plan.org_id).one()
        titel, kennung, plan_titel = ziel.titel, ziel.kennung, ziel_plan.titel
        # Leser duerfen keine Titel oder vertraulichen Elemente aus Arbeitsstaenden sehen.
        if not darf_bearbeiten(user):
            if not ziel_plan.aktuelle_version_id:
                continue
            _, ziel_plan_daten, version_elemente = _lade_version(db, ziel_plan)
            plan_titel = ziel_plan_daten["titel"]
            snapshot = next((e for e in version_elemente if e.element_uuid == uuid), None)
            if snapshot is None or (snapshot.vertraulich and not sieht_vertraulich(user)):
                continue
            titel, kennung = snapshot.titel, snapshot.kennung
        ergebnis.append({**relation, "titel": titel, "kennung": kennung,
                         "plan_titel": plan_titel if ziel_plan.id != plan.id else "",
                         "url": (f"/katastrophenplaene/{ziel.plan_id}" if ziel.plan_id != plan.id else "")
                                + f"#element-{uuid}"})
    return ergebnis


def _link_kontext(db: Session, user: User, plan: KatplanPlan, version_elemente: list | None = None,
                  plan_daten: dict | None = None) -> dict[str, Any]:
    voll = can_view_kontakt_directory(user)
    rechte: dict[str, Any] = {"org_id": plan.org_id, "kontakt": voll,
              "objekt": ziel_verfuegbar(db, plan.org_id, "objekt") and has_role(user, *_LESE_ROLLEN)}
    element_links: dict[int, list] = {}
    element_relationen: dict[int, list] = {}
    if version_elemente is not None:
        for e in version_elemente:
            element_links[e.id] = [{**ansicht, "hinweis": roh.get("hinweis", "")} for ansicht, roh in zip(
                link_service.links_aus_version(db, e.links_json, voll=voll, user_rechte=rechte),
                json.loads(e.links_json), strict=True)]
            element_relationen[e.id] = _relation_ansichten(db, user, plan, json.loads(e.relationen_json or "[]"))
        plan_roh = (plan_daten or {}).get("links", [])
        plan_links = [{**ansicht, "hinweis": roh.get("hinweis", "")} for ansicht, roh in zip(
            link_service.links_aus_version(db, plan_roh, voll=voll, user_rechte=rechte), plan_roh, strict=True)]
    else:
        plan_links = []
        for link in db.query(KatplanLink).filter_by(org_id=plan.org_id, plan_id=plan.id).order_by(KatplanLink.id):
            ansicht = {**link_service.link_ansicht(db, link, voll=voll, user_rechte=rechte),
                       "id": link.id, "hinweis": link.hinweis}
            if link.element_id is None:
                plan_links.append(ansicht)
            else:
                element_links.setdefault(link.element_id, []).append(ansicht)
        for relation in db.query(KatplanRelation).filter_by(org_id=plan.org_id, plan_id=plan.id):
            element_relationen.setdefault(relation.von_element_id, []).extend(_relation_ansichten(
                db, user, plan, [{"id": relation.id, "zu_element_uuid": relation.zu_element_uuid,
                                  "art": relation.art, "hinweis": relation.hinweis}]))
    return {"element_links": element_links, "element_relationen": element_relationen,
            "plan_links": plan_links, "link_rollen": link_service.KATPLAN_LINK_ROLLEN,
            "relation_arten": link_service.KATPLAN_RELATION_ARTEN,
            "ziel_typen": {typ: label for typ, label in _ZIEL_TYPEN.items()
                           if darf_bearbeiten(user) and ziel_verfuegbar(db, plan.org_id, typ)}}


def _link_antwort(request: Request, db: Session, user: User, plan: KatplanPlan,
                  element: KatplanElement | None, *, beziehung: bool = False, fehler: str = ""):
    return templates.TemplateResponse(request, "katplan/_link_antwort.html", {
        **_kontext(db, user, plan), "element": element, "beziehung": beziehung,
        "fehler": fehler, "version_oob": element is not None,
    }, status_code=422 if fehler else 200)


@router.get("/{plan_id}/verknuepfungen/suche")
def link_suche(plan_id: int, request: Request, ziel_typ: str = "", q: str = "", element_id: int | None = None,
               db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, element_id, plan, user) if element_id is not None else None
    treffer = ziel_suchen(db, plan.org_id, ziel_typ, q) if plan.org_id is not None and len(q.strip()) >= 2 else []
    return templates.TemplateResponse(request, "katplan/_link_treffer.html", {
        **_kontext(db, user, plan), "element": element, "treffer": treffer, "q": q, "ziel_typ": ziel_typ})


def _link_anlegen(request: Request, db: Session, user: User, plan: KatplanPlan, element: KatplanElement | None):
    form = run_async(request.form)
    try:
        link_service.link_anlegen(db, plan, user, element=element, ziel_typ=str(form.get("ziel_typ", "")),
                                  ziel_id=int(str(form.get("ziel_id", "0"))),
                                  rolle=str(form.get("rolle", "")), hinweis=str(form.get("hinweis", "")))
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _link_antwort(request, db, user, plan, element, fehler=str(exc))
    return _link_antwort(request, db, user, plan, element)


@router.post("/{plan_id}/elemente/{eid}/links")
def element_link_anlegen(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    return _link_anlegen(request, db, user, plan, _objekt(db, KatplanElement, eid, plan, user))


@router.post("/{plan_id}/links")
def plan_link_anlegen(plan_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_katplan_bearbeiten)):
    return _link_anlegen(request, db, user, _plan(db, plan_id, user), None)


@router.post("/{plan_id}/links/{lid}/loeschen")
def link_loeschen(plan_id: int, lid: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    link = db.query(KatplanLink).filter_by(id=lid, org_id=user.org_id, plan_id=plan.id).one_or_none()
    if link is None:
        raise HTTPException(404, "Nicht gefunden")
    element = _objekt(db, KatplanElement, link.element_id, plan, user) if link.element_id is not None else None
    try:
        link_service.link_entfernen(db, plan, user, link)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _link_antwort(request, db, user, plan, element, fehler=str(exc))
    return _link_antwort(request, db, user, plan, element)


@router.get("/{plan_id}/elemente/{eid}/beziehungen/suche")
def beziehung_suche(plan_id: int, eid: int, request: Request, q: str = "",
                    db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    treffer = db.query(KatplanElement, KatplanPlan.titel).join(
        KatplanPlan, KatplanPlan.id == KatplanElement.plan_id,
    ).filter(KatplanElement.org_id == user.org_id, KatplanPlan.org_id == user.org_id,
             KatplanPlan.status != "archiviert", KatplanElement.id != eid,
             or_(KatplanElement.titel.ilike(f"%{q.strip()}%"), KatplanElement.kennung.ilike(f"%{q.strip()}%")),
    ).order_by(KatplanElement.titel, KatplanElement.id).limit(20).all() if len(q.strip()) >= 2 else []
    return templates.TemplateResponse(request, "katplan/_beziehung_treffer.html", {
        **_kontext(db, user, plan), "element": element, "treffer": treffer, "q": q})


@router.post("/{plan_id}/elemente/{eid}/beziehungen")
def beziehung_anlegen(plan_id: int, eid: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    form = run_async(request.form)
    try:
        uuid = str(form.get("zu_element_uuid", ""))
        ziel = db.query(KatplanElement).join(KatplanPlan, KatplanPlan.id == KatplanElement.plan_id).filter(
            KatplanElement.uuid == uuid, KatplanElement.org_id == user.org_id,
            KatplanPlan.org_id == user.org_id, KatplanPlan.status != "archiviert",
        ).one_or_none()
        if ziel is None:
            raise ValueError("Zielelement nicht verfügbar.")
        link_service.relation_anlegen(db, plan, user, element, uuid, str(form.get("art", "")),
                                      str(form.get("hinweis", "")))
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _link_antwort(request, db, user, plan, element, beziehung=True, fehler=str(exc))
    return _link_antwort(request, db, user, plan, element, beziehung=True)


@router.post("/{plan_id}/beziehungen/{rid}/loeschen")
def beziehung_loeschen(plan_id: int, rid: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    relation = db.query(KatplanRelation).filter_by(id=rid, org_id=user.org_id, plan_id=plan.id).one_or_none()
    if relation is None:
        raise HTTPException(404, "Nicht gefunden")
    element = _objekt(db, KatplanElement, relation.von_element_id, plan, user)
    try:
        link_service.relation_entfernen(db, plan, user, relation)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _link_antwort(request, db, user, plan, element, beziehung=True, fehler=str(exc))
    return _link_antwort(request, db, user, plan, element, beziehung=True)


def _quelle(db: Session, plan: KatplanPlan, dok_id: int) -> KatplanDokument:
    dokument = db.query(KatplanDokument).filter_by(
        id=dok_id, plan_id=plan.id, org_id=plan.org_id,
    ).one_or_none()
    if dokument is None:
        raise HTTPException(404, "Nicht gefunden")
    return dokument


def _quellen_antwort(request: Request, db: Session, plan: KatplanPlan, user: User,
                     *, status: int = 200, fehler: str = "", hinweis: str = ""):
    return templates.TemplateResponse(request, "katplan/_quellen.html", {
        "user": user, "plan": plan, "quellen": quelle_service.sichtbare_quellen(db, plan, user),
        "fehler": fehler, "hinweis": hinweis, "can_edit": darf_bearbeiten(user),
        **(_ki_kontext(db, plan) if darf_bearbeiten(user) else {}),
    }, status_code=status)


@router.post("/{plan_id}/quellen")
def quelle_hochladen(plan_id: int, request: Request, background_tasks: BackgroundTasks,
                     datei: UploadFile | None = File(None), titel: str = Form(""),
                     herausgeber: str = Form(""), stand: str = Form(""),
                     ersetzt_dokument_id: str = Form(""), db: Session = Depends(get_db),
                     user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    try:
        if datei is None:
            raise ValueError("Bitte eine PDF-Datei auswählen.")
        dokument = quelle_service.quelle_hochladen(
            db, plan, user, run_async(datei.read), datei.filename or "original.pdf", titel=titel,
            herausgeber=herausgeber, stand=date.fromisoformat(stand) if stand else None,
            ersetzt_dokument_id=int(ersetzt_dokument_id) if ersetzt_dokument_id else None,
        )
    except HTTPException as exc:
        db.rollback()
        return _quellen_antwort(request, db, plan, user, status=exc.status_code, fehler=str(exc.detail))
    except ValueError as exc:
        db.rollback()
        return _quellen_antwort(request, db, plan, user, status=422, fehler=str(exc))
    db.commit()
    background_tasks.add_task(quelle_service.verarbeite_quelle, dokument.id)
    return _quellen_antwort(request, db, plan, user)


@router.get("/{plan_id}/quellen/{dok_id}/status")
def quelle_status(plan_id: int, dok_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_katplan_lesen)):
    plan = _plan(db, plan_id, user)
    dokument = _quelle(db, plan, dok_id)
    if dokument not in quelle_service.sichtbare_quellen(db, plan, user):
        raise HTTPException(404, "Nicht gefunden")
    return templates.TemplateResponse(request, "katplan/_quelle_status.html", {
        "plan": plan, "dokument": dokument,
    })


@router.post("/{plan_id}/quellen/{dok_id}/loeschen")
def quelle_loeschen(plan_id: int, dok_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    dokument = _quelle(db, plan, dok_id)
    try:
        quelle_service.quelle_entfernen(db, plan, user, dokument)
    except ValueError as exc:
        return _quellen_antwort(request, db, plan, user, status=422, fehler=str(exc))
    archiviert = dokument.archiviert
    db.commit()
    return _quellen_antwort(request, db, plan, user,
                           hinweis="Quelle wurde archiviert, da sie noch verknüpft ist." if archiviert else "")


@router.post("/{plan_id}/quellen/{dok_id}/neu-verarbeiten")
def quelle_neu_verarbeiten(plan_id: int, dok_id: int, request: Request, background_tasks: BackgroundTasks,
                           db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    dokument = _quelle(db, plan, dok_id)
    if plan.status == "archiviert":
        raise HTTPException(422, "Dieser Plan kann nicht bearbeitet werden.")
    if dokument.status != "verarbeitung":
        dokument.status, dokument.fehler_text = "neu", None
        db.commit()
        background_tasks.add_task(quelle_service.verarbeite_quelle, dokument.id)
    return _quellen_antwort(request, db, plan, user)


def _quelle_auslieferung(db: Session, user: User, uuid: str,
                        version_id: int | None) -> tuple[KatplanPlan, KatplanDokument]:
    dokument = db.query(KatplanDokument).filter_by(org_id=user.org_id, uuid=uuid).one_or_none()
    if dokument is None:
        raise HTTPException(404, "Nicht gefunden")
    plan = _plan(db, dokument.plan_id, user)
    if not darf_bearbeiten(user):
        if plan.status == "archiviert" or not plan.aktuelle_version_id:
            raise HTTPException(404, "Nicht gefunden")
        vid = version_id if version_id is not None else plan.aktuelle_version_id
        if dokument.id not in quelle_service.quellen_ids_aus_version(db, plan, vid, user):
            raise HTTPException(404, "Nicht gefunden")
    return plan, dokument


def _quelle_datei(relativ: str | None, mime: str, dateiname: str):
    try:
        path = quelle_service.absolute_pfad(relativ) if relativ else None
    except ValueError as exc:
        raise HTTPException(404, "Nicht gefunden") from exc
    if path is None or not path.is_file():
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(path, media_type=mime, filename=dateiname, content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.get("/quelle/{uuid}/original")
def quelle_original(uuid: str, version_id: int | None = None, db: Session = Depends(get_db),
                    user: User = Depends(require_katplan_lesen)):
    _, dokument = _quelle_auslieferung(db, user, uuid, version_id)
    return _quelle_datei(dokument.pfad, "application/pdf", dokument.dateiname_original)


@router.get("/quelle/{uuid}/seite/{nr}/bild")
@router.get("/quelle/{uuid}/seite/{nr}/thumb")
@router.get("/quelle/{uuid}/seite/{nr}/pdf")
@router.get("/quelle/{uuid}/seite/{nr}/text")
def quelle_seite(uuid: str, nr: int, request: Request, version_id: int | None = None,
                 db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    plan, dokument = _quelle_auslieferung(db, user, uuid, version_id)
    if not 1 <= nr <= dokument.seitenzahl:
        raise HTTPException(404, "Nicht gefunden")
    seite = quelle_service.quelle_der_seite(db, plan, dokument.id, nr)
    if seite is None:
        raise HTTPException(404, "Nicht gefunden")
    art = request.url.path.rsplit("/", 1)[1]
    if art == "text":
        from fastapi.responses import JSONResponse
        return JSONResponse({"volltext": seite.volltext, "text_quelle": seite.text_quelle},
                            headers={"X-Content-Type-Options": "nosniff"})
    pfad, mime, suffix = {
        "bild": (seite.bild_pfad, "image/png", "png"),
        "thumb": (seite.thumb_pfad, "image/jpeg", "jpg"),
        "pdf": (seite.einzel_pdf_pfad, "application/pdf", "pdf"),
    }[art]
    return _quelle_datei(pfad, mime, f"seite_{nr}.{suffix}")


@router.get("/{plan_id}/quellen")
def quellen(plan_id: int, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    return templates.TemplateResponse(request, "katplan/quellen.html", {
        "user": user, "plan": plan, "can_edit": darf_bearbeiten(user),
        "quellen": quelle_service.sichtbare_quellen(db, plan, user),
        **(_ki_kontext(db, plan) if darf_bearbeiten(user) else {}),
    })


def _seitenbereich_enthaelt(bereich: str | None, nr: int) -> bool:
    try:
        grenzen = (bereich or "").split("-")
        return int(grenzen[0]) <= nr <= int(grenzen[-1])
    except ValueError:
        return False


def _seitentitel(seite: KatplanDokumentSeite, fallback: str) -> str:
    """Seitentitel bzw. erste Textzeile bis zum ersten Aufzaehlungspunkt, auf ~120 Zeichen gekuerzt."""
    zeile = seite.titel or next(
        (z.strip() for z in (seite.volltext or "").splitlines() if z.strip()), fallback)
    zeile = zeile.split("\u2022")[0].strip() or fallback
    if len(zeile) > 120:
        zeile = zeile[:120].rsplit(" ", 1)[0] + " ..."
    return zeile[:200]


def _seitentext_als_html(volltext: str | None) -> str:
    """Seitentext als Absaetze; mit Aufzaehlungspunkt getrennte Teile werden zu einer Liste."""
    teile = []
    for zeile in (volltext or "").splitlines():
        stuecke = [t.strip() for t in zeile.split("\u2022")]
        if stuecke[0]:
            teile.append(f"<p>{escape(stuecke[0])}</p>")
        punkte = [t for t in stuecke[1:] if t]
        if punkte:
            teile.append("<ul>" + "".join(f"<li>{escape(t)}</li>" for t in punkte) + "</ul>")
    return "".join(teile)


@router.get("/{plan_id}/quellen/{dok_id}")
def quelle_ansicht(plan_id: int, dok_id: int, request: Request, seite: int | None = None,
                   version_id: int | None = None, db: Session = Depends(get_db),
                   user: User = Depends(require_katplan_lesen)):
    plan = _sichtbarer_plan(db, plan_id, user)
    dokument = _quelle(db, plan, dok_id)
    _quelle_auslieferung(db, user, dokument.uuid, version_id)
    seiten = db.query(KatplanDokumentSeite).filter_by(
        org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id,
    ).order_by(KatplanDokumentSeite.seiten_nr).all()
    aktuelle_seite = next((s for s in seiten if s.seiten_nr == seite), None)
    if seite is not None and (not 1 <= seite <= dokument.seitenzahl or aktuelle_seite is None):
        raise HTTPException(404, "Nicht gefunden")
    context = {"user": user, "plan": plan, "dokument": dokument, "seiten": seiten,
               "seite": aktuelle_seite, "can_edit": darf_bearbeiten(user), "typen": ELEMENT_TYPEN,
               "version_id": version_id, "version_query": f"?version_id={version_id}" if version_id else ""}
    if darf_bearbeiten(user):
        context.update(_editor_kontext(db, plan))
        context["ki_seiten_anzahl"] = db.query(KatplanImportVorschlag).filter_by(
            org_id=plan.org_id, plan_id=plan.id, dokument_id=dokument.id, seiten_nr=seite, status="offen",
        ).count()
        elemente = db.query(KatplanElement).filter_by(
            org_id=plan.org_id, plan_id=plan.id, quelle_dokument_id=dokument.id,
        ).all()
    else:
        _, _, elemente = _lade_version(db, plan, version_id)
        elemente = [e for e in elemente if e.quelle_dokument_id == dokument.id
                    and (not e.vertraulich or sieht_vertraulich(user))]
    context["verknuepfte_elemente"] = [e for e in elemente if seite is not None
                                         and _seitenbereich_enthaelt(e.quelle_seiten, seite)]
    context["seitentitel"] = _seitentitel(aktuelle_seite, dokument.titel) if aktuelle_seite else ""
    return templates.TemplateResponse(request, "katplan/quelle_seiten.html", context)


@router.post("/{plan_id}/quellen/{dok_id}/element")
def quelle_element_anlegen(plan_id: int, dok_id: int, request: Request, seite: int = Form(...),
                          typ: str = Form("info"), titel: str = Form(""), abschnitt_id: str = Form(""),
                          db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    dokument = _quelle(db, plan, dok_id)
    quelle_seite = quelle_service.quelle_der_seite(db, plan, dok_id, seite)
    if quelle_seite is None:
        raise HTTPException(404, "Nicht gefunden")
    try:
        quelle_service.validiere_element_quelle(db, plan, dokument.id, str(seite))
        abschnitt = _abschnitt_id(db, abschnitt_id, plan, user)
        beschreibung = _seitentext_als_html(quelle_seite.volltext)
        element = lege_element_an(db, plan, user, typ=typ, titel=_pflicht(titel, 200, "Titel"),
                                  abschnitt_id=abschnitt, beschreibung_html=beschreibung,
                                  herkunft="imported", quelle_dokument_id=dokument.id, quelle_seiten=str(seite))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    ziel = f"/katastrophenplaene/{plan.id}/bearbeiten?element={element.id}"
    if request.headers.get("HX-Request"):
        from fastapi.responses import Response

        return Response(headers={"HX-Redirect": ziel})
    return RedirectResponse(ziel, status_code=303)


def _ki_kontext(db: Session, plan: KatplanPlan) -> dict:
    offene = db.query(KatplanImportVorschlag).filter_by(
        org_id=plan.org_id, plan_id=plan.id, status="offen",
    ).all()
    return {"ki_enabled": ki_service.ki_import_enabled(plan.org_id, db),
            "ki_offene": {dok_id: sum(v.dokument_id == dok_id for v in offene)
                          for dok_id in {v.dokument_id for v in offene}}}


@router.post("/{plan_id}/quellen/{dok_id}/ki-analyse")
def quelle_ki_analyse(plan_id: int, dok_id: int, request: Request, background_tasks: BackgroundTasks,
                      db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    dokument = _quelle(db, plan, dok_id)
    if not ki_service.ki_import_enabled(plan.org_id, db):
        return _quellen_antwort(request, db, plan, user, status=409, fehler="KI-Import ist nicht aktiviert.")
    if (plan.status == "archiviert" or dokument.archiviert or dokument.status != "fertig"
            or dokument.ki_status == "laeuft"):
        return _quellen_antwort(request, db, plan, user, status=409,
                                fehler="Quelle nicht bereit oder Analyse läuft bereits.")
    dokument.ki_status, dokument.ki_fehler = "laeuft", None
    db.commit()
    background_tasks.add_task(ki_service.analysiere_quelle, dokument.id)
    return _quellen_antwort(request, db, plan, user)


@router.get("/{plan_id}/quellen/{dok_id}/ki-status")
def quelle_ki_status(plan_id: int, dok_id: int, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    dokument = _quelle(db, plan, dok_id)
    return templates.TemplateResponse(request, "katplan/_import_status.html", {
        "plan": plan, "dokument": dokument, **_ki_kontext(db, plan),
    })


def _import_vorschlag(db: Session, plan: KatplanPlan, vid: int) -> KatplanImportVorschlag:
    vorschlag = db.query(KatplanImportVorschlag).filter_by(
        id=vid, org_id=plan.org_id, plan_id=plan.id,
    ).one_or_none()
    if vorschlag is None:
        raise HTTPException(404, "Nicht gefunden")
    return vorschlag


def _import_kontext(db: Session, plan: KatplanPlan, user: User, quelle: int | None,
                    status: str, seite: int | None) -> dict:
    query = db.query(KatplanImportVorschlag).filter_by(org_id=plan.org_id, plan_id=plan.id)
    if quelle is not None:
        query = query.filter_by(dokument_id=quelle)
    if status:
        query = query.filter_by(status=status)
    if seite is not None:
        query = query.filter_by(seiten_nr=seite)
    vorschlaege = query.order_by(KatplanImportVorschlag.dokument_id, KatplanImportVorschlag.seiten_nr,
                                KatplanImportVorschlag.reihenfolge, KatplanImportVorschlag.id).all()
    kontakte = {}
    for v in vorschlaege:
        liste = json.loads(v.kontakte_json or "[]")
        for k in liste:
            ziel = ziel_laden(db, plan.org_id, "kontakt", k["kontakt_id"]) if k.get("kontakt_id") else None
            k["kontakt_name"] = ziel.anzeigename if ziel else None
        kontakte[v.id] = liste
    texte = {v.id: ki_service.html_zu_text(v.beschreibung_html) for v in vorschlaege}
    typdaten = {v.id: json.loads(v.daten_json or "{}") for v in vorschlaege}
    return {"user": user, "plan": plan, "can_edit": True, "import_aktiv": True,
            "vorschlaege": vorschlaege, "kontakte": kontakte, "texte": texte, "typdaten": typdaten,
            "quelle_filter": quelle,
            "status_filter": status, "seite_filter": seite,
            "quellen": db.query(KatplanDokument).filter_by(org_id=plan.org_id, plan_id=plan.id).all(),
            **_editor_kontext(db, plan),
            "objekt_recht": has_role(user, *_LESE_ROLLEN) and ziel_verfuegbar(db, plan.org_id, "objekt"),
            "kontakt_modul": ziel_verfuegbar(db, plan.org_id, "kontakt")}


@router.get("/{plan_id}/import")
def import_review(plan_id: int, request: Request, quelle: str = "", status: str = "offen",
                  seite: int | None = None, db: Session = Depends(get_db),
                  user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    context = _import_kontext(db, plan, user, int(quelle) if quelle.isdigit() else None, status, seite)
    return templates.TemplateResponse(request, "katplan/import.html", context)


@router.post("/{plan_id}/import/sammel")
def import_sammel(plan_id: int, request: Request, ids: list[int] = Form([]),
                  db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    for vid in ids:
        _import_vorschlag(db, plan, vid)
    try:
        anzahl = ki_service.sammel_uebernehmen(db, plan, user, ids)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "katplan/_import_ergebnis.html",
                                          {"fehler": str(exc)}, status_code=409)
    context = _import_kontext(db, plan, user, None, "offen", None)
    context["erfolg"] = f"{anzahl} Vorschläge übernommen."
    return templates.TemplateResponse(request, "katplan/_import_liste.html", context)


@router.post("/{plan_id}/import/{vid}/uebernehmen")
def import_uebernehmen(plan_id: int, vid: int, request: Request,
                       db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    vorschlag = _import_vorschlag(db, plan, vid)
    form = run_async(request.form)
    werte = {k: str(v) for k, v in form.items()}
    if "beschreibung_text" in werte:
        werte["beschreibung_html"] = ki_service.text_zu_html(werte.pop("beschreibung_text"))
    try:
        daten: dict[str, Any] = {**werte, "kontakt_ids": [int(str(v)) for v in form.getlist("kontakt_ids")],
                                 "objekt_verknuepfen": werte.get("objekt_verknuepfen") == "1"}
        for feld, neu in (("abschnitt_id", "neues_kapitel"), ("phase_id", "neue_phase")):
            if werte.get(feld) == "neu":
                daten[feld] = None
                daten[neu] = werte.get(neu, "")
            else:
                daten[neu] = ""
        element = ki_service.vorschlag_uebernehmen(db, plan, user, vorschlag, werte=daten)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "katplan/_import_ergebnis.html",
                                          {"fehler": str(exc)}, status_code=409)
    return templates.TemplateResponse(request, "katplan/_import_ergebnis.html", {"plan": plan, "element": element})


@router.post("/{plan_id}/import/{vid}/verwerfen")
def import_verwerfen(plan_id: int, vid: int, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    vorschlag = _import_vorschlag(db, plan, vid)
    try:
        ki_service.vorschlag_verwerfen(db, plan, user, vorschlag)
        db.commit()
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "katplan/_import_ergebnis.html",
                                          {"fehler": str(exc)}, status_code=409)
    return templates.TemplateResponse(request, "katplan/_import_ergebnis.html", {"verworfen": True})


@router.get("/{plan_id}/pdf")
def pdf_export(plan_id: int, version_id: int | None = None, entwurf: int = 0,
               db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    import re
    import unicodedata

    from app.services.katplan_mcp_service import fassung_laden
    from app.services.katplan_pdf_service import render_katplan_pdf

    plan = _sichtbarer_plan(db, plan_id, user)
    if entwurf and not darf_bearbeiten(user):
        raise HTTPException(403, "Keine Berechtigung für den Arbeitsstand.")
    try:
        kopf, _, vid, _ = fassung_laden(db, user, plan, "arbeitsstand" if entwurf else "freigegeben", version_id)
        data = render_katplan_pdf(db, user, plan, version_id=version_id, entwurf=bool(entwurf))
    except LookupError as exc:
        raise HTTPException(404, "Nicht gefunden") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    version = db.get(KatplanVersion, vid) if vid else None
    titel = unicodedata.normalize("NFKD", kopf["titel"]).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", titel.lower()).strip("-") or "plan"
    nummer = str(version.versionsnummer) if version else "entwurf"
    return Response(data, media_type="application/pdf", headers={
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="katastrophenplan_{slug}_v{nummer}.pdf"',
    })


@router.get("/{plan_id}/quellen/{dok_id}/vergleich")
def quellen_vergleich(plan_id: int, dok_id: int, request: Request, mit: int | None = None,
                      db: Session = Depends(get_db), user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    try:
        vergleich = quelle_service.vergleiche_quellen(db, plan, dok_id, mit)
    except ValueError as exc:
        raise HTTPException(404, "Nicht gefunden") from exc
    return templates.TemplateResponse(request, "katplan/quellen_vergleich.html", {
        "user": user, "plan": plan, "vergleich": vergleich,
    })


@router.post("/{plan_id}/quellen/{dok_id}/elemente/{eid}/umstellen")
def quelle_umstellen(plan_id: int, dok_id: int, eid: int, db: Session = Depends(get_db),
                     user: User = Depends(require_katplan_bearbeiten)):
    plan = _plan(db, plan_id, user)
    element = _objekt(db, KatplanElement, eid, plan, user)
    try:
        quelle_service.stelle_quelle_um(db, plan, user, dok_id, element)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(404, "Nicht gefunden") from exc
    return RedirectResponse(f"/katastrophenplaene/{plan.id}/bearbeiten?element={element.id}", status_code=303)
