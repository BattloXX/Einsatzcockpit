"""Nachschlagewerke-Modul (PR 0: Grundgeruest).

Freies, offlinefaehiges Nachschlagewerk fuer Einsatzkraefte:
- Gefahrgut-Suche (UN-Nummer/Stoffname -> ERI-Karte)   [PR 1]
- Rettungsdatenblaetter fuer die technische Rettung     [PR 4/5]
- Karten-Overlays (Evakuierungsradius, Ausbreitung)     [PR 6/7]

Alle Routen brauchen require_nachschlagewerke_enabled (HTTP 404 wenn Modul
inaktiv). Effektiv aktiv = SystemSettings-Key "nachschlagewerke_module_enabled"
== "true" UND OrgSettings.nachschlagewerke_module_enabled (Muster UAS/Objekt).
Prefix: /nachschlagewerke

Die Daten sind ein geteiltes Nachschlagewerk (kein Org-Bezug), daher keine
Tenant-Tabellen. Lesend fuer alle angemeldeten Nutzer der Org.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.user import User
from app.services import gefahrgut_service, rettungskarten_service

router = APIRouter(prefix="/nachschlagewerke", tags=["nachschlagewerke"])
# Zweiter Router OHNE /nachschlagewerke-Praefix: die PDF-Auslieferung liegt unter
# /nachschlagewerk-cache/, damit der Service Worker sie cache-first behandelt
# (unveraenderliche IDs -> offline verfuegbar, siehe sw.js).
cache_router = APIRouter(tags=["nachschlagewerke"])

# Alle Rollen der Org duerfen das Nachschlagewerk lesen.
_LESE_ROLLEN = (
    "readonly", "recorder", "breathing_supervisor", "incident_leader",
    "fahrtenbuch_admin", "objekt_verwalter",
)


def require_nachschlagewerke_enabled(request: Request) -> None:
    """Guard: HTTP 404 wenn das Nachschlagewerke-Modul nicht effektiv aktiv ist."""
    if not getattr(request.state, "nachschlagewerke_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


@router.get("/", response_class=HTMLResponse)
def nachschlagewerke_start(
    request: Request,
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
):
    """Landing mit Tab-Navigation (Gefahrgut / Rettungskarten)."""
    return templates.TemplateResponse(request, "nachschlagewerke/start.html", {
        "user": user,
    })


# ── Gefahrgut ────────────────────────────────────────────────────────────────

@router.get("/gefahrgut", response_class=HTMLResponse)
def gefahrgut_seite(
    request: Request,
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
    q: str = "",
):
    """Gefahrgut-Suche (UN-Nummer/Stoffname). Serverseitige Erst-Treffer bei ?q=."""
    treffer = gefahrgut_service.suche(q) if q.strip() else []
    return templates.TemplateResponse(request, "nachschlagewerke/gefahrgut.html", {
        "user": user,
        "q": q,
        "treffer": treffer,
    })


@router.get("/gefahrgut/index.json")
def gefahrgut_index_json(
    request: Request,
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
):
    """Kompletter Datensatz fuer die Offline-Suche im Browser (vom SW gecacht)."""
    eintraege = gefahrgut_service.alle_eintraege()
    return JSONResponse(
        {"anzahl": len(eintraege), "eintraege": eintraege},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/gefahrgut/treffer", response_class=HTMLResponse)
def gefahrgut_treffer(
    request: Request,
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
    q: str = "",
):
    """HTMX-Fragment: Trefferliste zur Live-Suche (Ziel-Container im Suchformular)."""
    treffer = gefahrgut_service.suche(q) if q.strip() else []
    return templates.TemplateResponse(request, "nachschlagewerke/_gefahrgut_treffer.html", {
        "user": user,
        "q": q,
        "treffer": treffer,
    })


@router.get("/gefahrgut/suche.json")
def gefahrgut_suche_json(
    request: Request,
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
    q: str = "",
):
    """Live-Suche (HTMX/JS): JSON-Trefferliste zu UN-Nummer oder Stoffname."""
    return JSONResponse({"q": q, "treffer": gefahrgut_service.suche(q)})


@router.get("/gefahrgut/{un}", response_class=HTMLResponse)
def gefahrgut_detail(
    request: Request,
    un: str,
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
):
    """ERI-Detailansicht zu einer UN-Nummer (Eigenschaften, Klasse, Deep-Links)."""
    eintrag = gefahrgut_service.eintrag_un(un)
    if eintrag is None:
        raise HTTPException(status_code=404, detail="UN-Nummer nicht gefunden")
    return templates.TemplateResponse(request, "nachschlagewerke/gefahrgut_detail.html", {
        "user": user,
        "eintrag": eintrag,
    })


# ── Rettungsdatenblaetter ────────────────────────────────────────────────────

@router.get("/rettungskarten", response_class=HTMLResponse)
def rettungskarten_seite(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
):
    """Rettungsdatenblatt-Suche (Hersteller/Modell) + Liste der bereits gecachten."""
    return templates.TemplateResponse(request, "nachschlagewerke/rettungskarten.html", {
        "user": user,
        "gecacht": rettungskarten_service.suche(db, ""),
    })


@router.post("/rettungskarten/suchen", response_class=HTMLResponse)
def rettungskarten_suchen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
    hersteller: str = Form(...),
    modell: str = Form(...),
    baujahr_von: str = Form(""),
    kraftstoff: str = Form(""),
):
    """On-demand: Cache-Treffer oder Einzelabruf; rendert das Ergebnis-Fragment (HTMX)."""
    bj = None
    if baujahr_von.strip().isdigit():
        bj = int(baujahr_von.strip())
    eintrag, links = rettungskarten_service.finde_oder_hole(
        db, hersteller, modell, baujahr_von=bj, kraftstoff=kraftstoff or None)
    return templates.TemplateResponse(request, "nachschlagewerke/_rettungskarten_result.html", {
        "user": user,
        "eintrag": eintrag,
        "links": links,
        "hersteller": hersteller,
        "modell": modell,
    })


@cache_router.get("/nachschlagewerk-cache/rettungskarten/{rk_id:int}/original.pdf")
def rettungskarten_pdf(
    request: Request,
    rk_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_nachschlagewerke_enabled),
):
    """Liefert das gecachte Rettungsdatenblatt-PDF (unveraenderliche URL -> SW cache-first)."""
    from app.models.nachschlagewerk import RettungsdatenblattCache
    eintrag = db.get(RettungsdatenblattCache, rk_id)
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Rettungsdatenblatt nicht gefunden")
    pfad = rettungskarten_service.absolute_pfad(eintrag)
    if pfad is None or not pfad.exists():
        raise HTTPException(status_code=404, detail="Datei nicht vorhanden")
    return FileResponse(
        str(pfad),
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'inline; filename="rettungskarte-{rk_id}.pdf"'},
    )
