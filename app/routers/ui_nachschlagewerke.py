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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.permissions import require_role
from app.core.templating import templates
from app.models.user import User
from app.services import gefahrgut_service

router = APIRouter(prefix="/nachschlagewerke", tags=["nachschlagewerke"])

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
