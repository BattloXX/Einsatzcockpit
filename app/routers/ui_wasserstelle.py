"""Verwaltung der Wasserstellen-/Löschwasser-Stammdaten (Admin).

Prefix: /admin/wasserstellen — Rolle org_admin.
Funktionen: Liste + Karte, Einzelpflege (anlegen/bearbeiten/löschen), CSV-Import
des Vorarlberger GIS-Exports (Koordinaten MGI EPSG:31281 → WGS84).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.user import User
from app.models.wasserstelle import WASSERSTELLE_TYPEN, Wasserstelle
from app.services.wasserstelle_service import (
    importiere_eintraege,
    parse_wasserstellen_csv,
)

router = APIRouter(prefix="/admin/wasserstellen", tags=["wasserstelle"])

_ERLAUBTE_TYPEN = set(WASSERSTELLE_TYPEN.keys())


def _koord(roh: str) -> float | None:
    roh = (roh or "").strip().replace(",", ".")
    if not roh:
        return None
    try:
        return float(roh)
    except ValueError:
        return None


# ── Karten-JSON (vor dynamischen Pfaden) ────────────────────────────────────────

@router.get(".json")
def wasserstellen_json(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    """Alle Wasserstellen der Org als JSON (Admin-Übersichtskarte + CSV-Export).

    Enthält auch Einträge ohne Koordinaten (Export); die Karte überspringt diese.
    """
    rows = (
        db.query(Wasserstelle)
        .filter(Wasserstelle.org_id == user.org_id)
        .order_by(Wasserstelle.typ, Wasserstelle.bezeichnung)
        .all()
    )
    return {
        "wasserstellen": [
            {
                "id": w.id,
                "bezeichnung": w.bezeichnung,
                "typ": w.typ,
                "typ_label": w.typ_label,
                "icon_kat": w.icon_kat,
                "lat": w.lat,
                "lng": w.lng,
                "ergiebigkeit_l_min": w.ergiebigkeit_l_min,
                "aktiv": w.aktiv,
                "quelle": w.quelle,
            }
            for w in rows
        ]
    }


# ── Liste / Seite ───────────────────────────────────────────────────────────────

@router.get("")
def wasserstellen_seite(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    rows = (
        db.query(Wasserstelle)
        .filter(Wasserstelle.org_id == user.org_id)
        .order_by(Wasserstelle.typ, Wasserstelle.bezeichnung)
        .all()
    )
    # Zählung je Typ
    counts: dict[str, int] = {}
    for w in rows:
        counts[w.typ] = counts.get(w.typ, 0) + 1
    return templates.TemplateResponse(request, "admin/wasserstellen.html", {
        "user": user,
        "wasserstellen": rows,
        "typen": WASSERSTELLE_TYPEN,
        "counts": counts,
        "gesamt": len(rows),
        "aktiv_count": sum(1 for w in rows if w.aktiv),
        # Verfügbare Löschwasser-Kapazität (nur aktive Stellen mit hinterlegter Ergiebigkeit)
        "kapazitaet_l_min": sum(w.ergiebigkeit_l_min or 0 for w in rows if w.aktiv),
    })


# ── Anlegen ─────────────────────────────────────────────────────────────────────

@router.post("/neu")
def wasserstelle_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    bezeichnung: str = Form(...),
    typ: str = Form("ueberflur"),
    lat: str = Form(""),
    lng: str = Form(""),
    hinweis: str = Form(""),
    ergiebigkeit_l_min: str = Form(""),
):
    if not bezeichnung.strip():
        raise HTTPException(400, "Bezeichnung ist erforderlich")
    if typ not in _ERLAUBTE_TYPEN:
        typ = "sonstige"
    w = Wasserstelle(
        org_id=user.org_id,
        bezeichnung=bezeichnung.strip()[:250],
        typ=typ,
        lat=_koord(lat),
        lng=_koord(lng),
        hinweis=hinweis.strip() or None,
        ergiebigkeit_l_min=int(ergiebigkeit_l_min) if ergiebigkeit_l_min.strip().isdigit() else None,
        quelle="manuell",
        aktiv=True,
        erstellt_von_id=user.id,
        aktualisiert_von_id=user.id,
    )
    db.add(w)
    db.flush()
    write_audit(db, "wasserstelle.created", org_id=user.org_id, user_id=user.id,
                entity_type="wasserstelle", entity_id=w.id,
                payload={"bezeichnung": w.bezeichnung, "typ": w.typ})
    db.commit()
    return RedirectResponse("/admin/wasserstellen", status_code=303)


# ── Bearbeiten ──────────────────────────────────────────────────────────────────

@router.post("/{wid}/bearbeiten")
def wasserstelle_bearbeiten(
    wid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    bezeichnung: str = Form(...),
    typ: str = Form("ueberflur"),
    lat: str = Form(""),
    lng: str = Form(""),
    hinweis: str = Form(""),
    ergiebigkeit_l_min: str = Form(""),
    aktiv: str = Form("1"),
):
    w = db.get(Wasserstelle, wid)
    if not w or w.org_id != user.org_id:
        raise HTTPException(404, "Wasserstelle nicht gefunden")
    if typ not in _ERLAUBTE_TYPEN:
        typ = "sonstige"
    w.bezeichnung = bezeichnung.strip()[:250] or w.bezeichnung
    w.typ = typ
    w.lat = _koord(lat)
    w.lng = _koord(lng)
    w.hinweis = hinweis.strip() or None
    w.ergiebigkeit_l_min = int(ergiebigkeit_l_min) if ergiebigkeit_l_min.strip().isdigit() else None
    w.aktiv = aktiv in ("1", "true", "on")
    w.aktualisiert_von_id = user.id
    write_audit(db, "wasserstelle.updated", org_id=user.org_id, user_id=user.id,
                entity_type="wasserstelle", entity_id=w.id,
                payload={"bezeichnung": w.bezeichnung})
    db.commit()
    return RedirectResponse("/admin/wasserstellen", status_code=303)


# ── Löschen ─────────────────────────────────────────────────────────────────────

@router.post("/{wid}/loeschen")
def wasserstelle_loeschen(
    wid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
):
    w = db.get(Wasserstelle, wid)
    if not w or w.org_id != user.org_id:
        raise HTTPException(404, "Wasserstelle nicht gefunden")
    write_audit(db, "wasserstelle.deleted", org_id=user.org_id, user_id=user.id,
                entity_type="wasserstelle", entity_id=w.id,
                payload={"bezeichnung": w.bezeichnung})
    db.delete(w)
    db.commit()
    return RedirectResponse("/admin/wasserstellen", status_code=303)


# ── CSV-Import ──────────────────────────────────────────────────────────────────

@router.post("/import")
async def wasserstellen_import(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    datei: UploadFile = File(...),
    ersetzen: str = Form(""),
):
    inhalt = await datei.read()
    if not inhalt:
        raise HTTPException(400, "Leere Datei")
    parsed = parse_wasserstellen_csv(inhalt)
    if not parsed["eintraege"]:
        return RedirectResponse(
            "/admin/wasserstellen?import_fehler=keine-gueltigen-zeilen", status_code=303
        )
    res = importiere_eintraege(
        db, user.org_id, parsed["eintraege"], user.id,  # type: ignore[arg-type]
        ersetzen=ersetzen in ("1", "true", "on"),
    )
    write_audit(db, "wasserstelle.imported", org_id=user.org_id, user_id=user.id,
                entity_type="wasserstelle", entity_id=None,
                payload={"neu": res["neu"], "aktualisiert": res["aktualisiert"],
                         "uebersprungen": parsed["uebersprungen"]})
    db.commit()
    return RedirectResponse(
        f"/admin/wasserstellen?import_neu={res['neu']}&import_akt={res['aktualisiert']}"
        f"&import_skip={parsed['uebersprungen']}",
        status_code=303,
    )
