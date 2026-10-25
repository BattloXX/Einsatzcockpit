"""Gemeinsamer Einstieg; bestehende Plan- und Nachschlagewerk-Routen bleiben erhalten."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.permissions import can_view_katplan_request, can_view_nachschlagewerke_request
from app.core.templating import templates
from app.db import get_db
from app.services.einsatzwissen_kategorien import KATEGORIEN
from app.services.einsatzwissen_service import zuletzt_verwendet
from app.services.katplan_suche_service import sichtbare_plaene, suche
from app.services.katplan_zugriff import darf_bearbeiten

router = APIRouter(prefix="/einsatzwissen", tags=["Einsatzwissen"])


@router.get("")
@router.get("/", include_in_schema=False)
def uebersicht(request: Request, q: str = Query("", max_length=500), kategorie: str = "",
               unterkategorie: str = "", fassung: str = "freigabe", db: Session = Depends(get_db)):
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
    inhalte = []
    if q.strip() and plaene_aktiv:
        inhalte = suche(db, user, q=q, arbeitsstand=arbeit)["treffer"]
    inhalt_ids = {t["plan_id"] for t in inhalte}
    woerter = q.casefold().split()

    def passt(p):
        text = " ".join([p["titel"], p["kurzbeschreibung"], p["unterkategorie"],
                         *p["schlagwoerter"]]).casefold()
        return (not kategorie or p["kategorie"] == kategorie) and (
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
        "zuletzt": zuletzt_verwendet(db, user, alle),
    })
