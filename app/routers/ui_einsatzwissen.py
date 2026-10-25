"""Gemeinsamer Einstieg; bestehende Plan- und Nachschlagewerk-Routen bleiben erhalten."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import can_view_katplan_request, can_view_nachschlagewerke_request
from app.core.templating import templates
from app.core.timezones import now_local
from app.db import get_db
from app.models.katplan import KATPLAN_GEFAHRENARTEN, KatplanNutzung
from app.models.user import User
from app.services.einsatzwissen_kategorien import KATEGORIEN
from app.services.einsatzwissen_service import (
    einsatzbereiche,
    merke_zugriff,
    schnellzugriff,
    setze_favorit,
    zuletzt_verwendet,
)
from app.services.katplan_suche_service import sichtbare_plaene, suche
from app.services.katplan_zugriff import darf_bearbeiten, require_katplan_lesen

router = APIRouter(prefix="/einsatzwissen", tags=["Einsatzwissen"])


@router.get("")
@router.get("/", include_in_schema=False)
def uebersicht(request: Request, q: str = Query("", max_length=500), kategorie: str = "",
               unterkategorie: str = "", fassung: str = "freigabe", gefahr: str = "",
               auswahl: str = "", db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Nicht angemeldet")
    plaene_aktiv = can_view_katplan_request(request)
    nachschlagewerk_aktiv = can_view_nachschlagewerke_request(request)
    if not (plaene_aktiv or nachschlagewerk_aktiv):
        raise HTTPException(404, "Nicht gefunden")
    if kategorie and kategorie not in KATEGORIEN:
        raise HTTPException(422, "Unbekannte Kategorie")
    if fassung not in ("freigabe", "arbeitsstand"):
        raise HTTPException(422, "Unbekannte Fassung")
    can_edit = plaene_aktiv and darf_bearbeiten(user)
    arbeit = fassung == "arbeitsstand" and can_edit
    alle = sichtbare_plaene(db, user, arbeit) if plaene_aktiv else []
    schnell = schnellzugriff(db, user, alle)
    if auswahl not in ("", "favoriten", "zuletzt", "haeufig"):
        raise HTTPException(422, "Unbekannter Schnellzugriff")
    zuletzt = zuletzt_verwendet(db, user, alle)
    auswahl_ids = {p["id"] for p in {"favoriten": schnell["favoriten"], "zuletzt": zuletzt,
                                    "haeufig": schnell["haeufig"]}.get(auswahl, alle)}
    inhalte = []
    begrenzt = False
    if q.strip() and plaene_aktiv:
        ergebnis = suche(db, user, q=q, arbeitsstand=arbeit, gefahr=gefahr, nur_freigegebene_quellen=not arbeit)
        inhalte, begrenzt = ergebnis["treffer"], ergebnis["abgeschnitten"]
    inhalt_ids = {t["plan_id"] for t in inhalte}
    woerter = q.casefold().split()

    def passt(p):
        text = " ".join([p["titel"], p["kurzbeschreibung"], p["unterkategorie"],
                         KATEGORIEN[p["kategorie"]], *p["gefahrenarten"],
                         *[KATPLAN_GEFAHRENARTEN.get(g, g) for g in p["gefahrenarten"]],
                         *p["schlagwoerter"]]).casefold()
        return p["id"] in auswahl_ids and (not gefahr or gefahr in p["gefahrenarten"]) and (
            not kategorie or p["kategorie"] == kategorie) and (
            not unterkategorie or p["unterkategorie"] == unterkategorie) and (
            not woerter or all(w in text for w in woerter) or p["id"] in inhalt_ids)

    treffer = [p for p in alle if passt(p)]
    ids = {p["id"] for p in treffer}
    return templates.TemplateResponse(request, "einsatzwissen/index.html", {
        "user": user, "kategorien": KATEGORIEN, "kategorie": kategorie,
        "unterkategorie": unterkategorie,
        "unterkategorien": sorted({p["unterkategorie"] for p in alle if p["unterkategorie"] and (
            not kategorie or p["kategorie"] == kategorie)}),
        "anzahlen": {code: sum(p["kategorie"] == code for p in alle) for code in KATEGORIEN},
        "q": q, "arbeitsstand": arbeit, "can_edit": can_edit, "plaene_aktiv": plaene_aktiv,
        "nachschlagewerk_aktiv": nachschlagewerk_aktiv, "eintraege": treffer,
        "inhalte": [t for t in inhalte if t["plan_id"] in ids],
        "suche_begrenzt": begrenzt,
        "gefahr": gefahr, "gefahrenarten": KATPLAN_GEFAHRENARTEN, "auswahl": auswahl,
        "zuletzt": zuletzt, **schnell,
    }, headers={"Cache-Control": "no-store"})


def _freigabekontext(db: Session, user: User, plan_id: int, *, nur_kern: bool = False) -> dict:
    from app.routers.ui_katplan import _lade_version, _lesekontext, _sichtbarer_plan
    from app.services.katplan_freigabe_service import version_baum

    plan = _sichtbarer_plan(db, plan_id, user)
    if plan.status == "archiviert" or not plan.aktuelle_version_id:
        raise HTTPException(404, "Kein freigegebener Eintrag")
    if nur_kern:
        version, daten, elemente = _lade_version(db, plan)
        lesekontext = {"plan_daten": daten, "lesekopf": daten, "leseversion": version,
                      "version_baum": version_baum(daten, elemente, mit_vertraulichen=True),
                      "element_daten": {e.id: json.loads(e.daten_json or "{}") for e in elemente}}
    else:
        lesekontext = _lesekontext(db, user, plan)
    context = {"user": user, "plan": plan, "can_edit": darf_bearbeiten(user),
               "gefahrenarten": KATPLAN_GEFAHRENARTEN, "heute": now_local(user.org).date(), **lesekontext}
    context["bereiche"] = einsatzbereiche(context)
    return context


@router.get("/offline-paket")
def offline_paket(request: Request, ids: str = Query(..., max_length=250),
                  db: Session = Depends(get_db), user: User = Depends(require_katplan_lesen)):
    """Explizite kurzlebige Offlineberechtigung, ausschließlich nicht vertrauliche Kerntexte."""
    from app.config import settings
    from app.services.katplan_service import _HTMLText
    from app.services.katplan_typen import FELD_LABELS

    if getattr(request.state, "qr_incident_id", None) or getattr(request.state, "qr_lage_id", None):
        raise HTTPException(403, "Für temporäre QR-Zugänge nicht verfügbar")
    try:
        plan_ids = list(dict.fromkeys(int(i) for i in ids.split(",")))
    except ValueError as exc:
        raise HTTPException(422, "Ungültige Eintragsauswahl") from exc
    if not 1 <= len(plan_ids) <= 20:
        raise HTTPException(422, "Bitte höchstens 20 Einträge wählen")

    def klartext(html):
        parser = _HTMLText()
        parser.feed(html or "")
        return " ".join("".join(parser.teile).split())

    jetzt = int(datetime.now(UTC).timestamp() * 1000)
    # Das reguläre Sessionfenster wird von der Middleware bei dieser Antwort erneuert.
    dauer = min(900, settings.SESSION_MAX_AGE_SECONDS, settings.SESSION_INACTIVITY_SECONDS,
                settings.SESSION_REMEMBER_MAX_AGE_SECONDS, settings.SESSION_REMEMBER_INACTIVITY_SECONDS)
    eintraege = []
    for plan_id in plan_ids:
        c = _freigabekontext(db, user, plan_id, nur_kern=True)
        bereiche = []
        for b in c["bereiche"]:
            if b["id"] in ("dokumente", "kontakte", "verwandt"):
                continue
            bereiche.append({"titel": b["titel"], "elemente": [
                {"titel": e.titel, "text": klartext(e.beschreibung_html),
                 "daten": {FELD_LABELS.get(k, k): v for k, v in json.loads(e.daten_json or "{}").items()}}
                for e in b["elemente"] if not e.vertraulich]})
        eintraege.append({"id": plan_id, "titel": c["lesekopf"]["titel"],
                          "version": c["leseversion"].versionsnummer,
                          "version_id": c["leseversion"].id, "bereiche": bereiche,
                          "gefahren": [KATPLAN_GEFAHRENARTEN.get(g, g)
                                       for g in c["plan_daten"].get("gefahrenarten", [])],
                          "pruefung_faellig": bool(c["plan"].wiedervorlage_am and
                                                  c["plan"].wiedervorlage_am <= datetime.now(UTC).date())})
    payload = {"owner": f"{user.id}:{user.org_id}", "erstellt": jetzt,
               "gueltig_bis": jetzt + dauer * 1000, "eintraege": eintraege}
    if len(json.dumps(payload).encode()) > 2_000_000:
        raise HTTPException(413, "Kerninhalte überschreiten 2 MB. Bitte weniger Einträge wählen.")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.post("/{plan_id}/favorit")
def favorit(plan_id: int, aktiv: bool = Form(...), db: Session = Depends(get_db),
            user: User = Depends(require_katplan_lesen)):
    c = _freigabekontext(db, user, plan_id, nur_kern=True)
    setze_favorit(db, user, c["plan"], aktiv)
    db.commit()
    return RedirectResponse(f"/einsatzwissen/{plan_id}", status_code=303)


@router.get("/{plan_id}")
def einsatzansicht(plan_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_katplan_lesen)):
    c = _freigabekontext(db, user, plan_id)
    n = db.query(KatplanNutzung).filter_by(org_id=user.org_id, user_id=user.id, plan_id=plan_id).one_or_none()
    c["ist_favorit"] = bool(n and n.favorit)
    merke_zugriff(db, user, c["plan"])
    db.commit()
    return templates.TemplateResponse(request, "einsatzwissen/einsatz.html", c,
                                      headers={"Cache-Control": "no-store"})
