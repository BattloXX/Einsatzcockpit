"""Admin: Förderstrecken-Gerätekatalog (Pumpen- und Schlauchtypen) je Org.

Self-Service: Organisationen legen eigene Pumpen an und konfigurieren die Kennlinie
selbst. Mitgelieferte Vorlagen (app/data/foerder_vorlagen.py) sind ein globaler,
read-only Startpunkt — „Aus Vorlage anlegen" erzeugt eine frei editierbare Org-Kopie.

Prefixe: /admin/foerderpumpen und /admin/foerderschlaeuche — Rolle org_admin,
zusätzlich Modul-Guard (404 wenn Förderstrecke nicht effektiv aktiv).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.data.foerder_vorlagen import (
    PUMPEN_VORLAGEN,
    SCHLAUCH_VORLAGEN,
    pumpen_vorlage,
    schlauch_vorlage,
)
from app.db import get_db
from app.models.foerderstrecke import (
    QUELLE_MANUELL,
    QUELLE_VORLAGE,
    FoerderPumpenTyp,
    FoerderSchlauchTyp,
    wasserinhalt_pro_meter,
)
from app.models.user import User
from app.services.foerderstrecke_service import normalisiere_kennlinie_punkte

router = APIRouter(tags=["foerderstrecke"])


# ── Modul-Guard ──────────────────────────────────────────────────────────────────

def require_foerderstrecke_enabled(request: Request) -> None:
    """Guard-Dependency: HTTP 404 wenn Förderstrecke nicht effektiv aktiv (System+Org)."""
    if not getattr(request.state, "foerderstrecke_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


# ── Hilfsfunktionen: Formular-Parsing ─────────────────────────────────────────────

def _f(roh: str) -> float | None:
    roh = (roh or "").strip().replace(",", ".")
    if not roh:
        return None
    try:
        return float(roh)
    except ValueError:
        return None


def _i(roh: str) -> int | None:
    v = _f(roh)
    return int(v) if v is not None else None


def _parse_kennlinien(roh_json: str) -> tuple[dict[str, list[list[float]]], list[str]]:
    """Parst das vom Editor gelieferte kennlinien_json und validiert jede Stufe.

    Rückgabe: (kanonische Kennlinien, Fehlerliste). Leere Stufen werden verworfen.
    """
    fehler: list[str] = []
    try:
        roh = json.loads(roh_json) if roh_json.strip() else {}
    except (ValueError, TypeError):
        return {}, ["Kennlinien-Daten sind kein gültiges JSON."]
    if not isinstance(roh, dict):
        return {}, ["Kennlinien-Daten haben ein unerwartetes Format."]

    kanonisch: dict[str, list[list[float]]] = {}
    for stufe, punkte in roh.items():
        stufe_key = str(stufe).strip()
        if not stufe_key:
            continue
        q_werte: list[str] = []
        h_werte: list[str] = []
        if isinstance(punkte, list):
            for p in punkte:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    q_werte.append(str(p[0]))
                    h_werte.append(str(p[1]))
        norm, stufen_fehler = normalisiere_kennlinie_punkte(q_werte, h_werte)
        for f in stufen_fehler:
            fehler.append(f"Stufe {stufe_key}: {f}")
        if norm:
            kanonisch[stufe_key] = norm
    if not kanonisch and not fehler:
        fehler.append("Mindestens ein Kennlinienpunkt ist erforderlich.")
    return kanonisch, fehler


def _pumpe_or_404(pid: int, db: Session, user: User) -> FoerderPumpenTyp:
    p = db.get(FoerderPumpenTyp, pid)
    if not p or p.org_id != user.org_id:
        raise HTTPException(404, "Pumpentyp nicht gefunden")
    return p


def _schlauch_or_404(sid: int, db: Session, user: User) -> FoerderSchlauchTyp:
    s = db.get(FoerderSchlauchTyp, sid)
    if not s or s.org_id != user.org_id:
        raise HTTPException(404, "Schlauchtyp nicht gefunden")
    return s


# ══════════════════════════════════════════════════════════════════════════════
# Pumpen
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/foerderpumpen", response_class=HTMLResponse)
def pumpen_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    rows = (
        db.query(FoerderPumpenTyp)
        .filter(FoerderPumpenTyp.org_id == user.org_id)
        .order_by(FoerderPumpenTyp.aktiv.desc(), FoerderPumpenTyp.name)
        .all()
    )
    return templates.TemplateResponse(request, "admin/foerder_pumpen.html", {
        "user": user,
        "pumpen": rows,
        "vorlagen": PUMPEN_VORLAGEN,
    })


@router.get("/admin/foerderpumpen/neu", response_class=HTMLResponse)
def pumpe_neu_formular(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    return templates.TemplateResponse(request, "admin/foerder_pumpe_form.html", {
        "user": user,
        "pumpe": None,
        "vehicles": _org_vehicles(db, user),
        "fehler": [],
        "form": {},
    })


@router.get("/admin/foerderpumpen/vorlagen", response_class=HTMLResponse)
def pumpen_vorlagen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    return templates.TemplateResponse(request, "admin/foerder_pumpen_vorlagen.html", {
        "user": user,
        "vorlagen": PUMPEN_VORLAGEN,
    })


@router.post("/admin/foerderpumpen/aus-vorlage/{key}")
def pumpe_aus_vorlage(
    key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    vorlage = pumpen_vorlage(key)
    if not vorlage:
        raise HTTPException(404, "Vorlage nicht gefunden")
    felder = vorlage["felder"]
    p = FoerderPumpenTyp(
        org_id=user.org_id,
        name=vorlage["name"],
        kennlinien_json=json.dumps(felder.get("kennlinien") or {}),
        druck_anschluss_dn=felder.get("druck_anschluss_dn"),
        druck_parallel_max=felder.get("druck_parallel_max", 1),
        saug_anschluss_dn=felder.get("saug_anschluss_dn"),
        saug_parallel_max=felder.get("saug_parallel_max", 1),
        max_ansaughoehe_m=felder.get("max_ansaughoehe_m", 7.5),
        min_eingangsdruck_bar=felder.get("min_eingangsdruck_bar", 1.5),
        max_ausgangsdruck_bar=felder.get("max_ausgangsdruck_bar"),
        npshr_json=json.dumps(felder["npshr"]) if felder.get("npshr") else None,
        tank_l=felder.get("tank_l"),
        verbrauch_json=json.dumps(felder.get("verbrauch") or {}) if felder.get("verbrauch") else None,
        hinweise=felder.get("hinweise"),
        aktiv=True,
        quelle=QUELLE_VORLAGE,
        vorlage_key=key,
        erstellt_von_id=user.id,
        aktualisiert_von_id=user.id,
    )
    db.add(p)
    db.flush()
    write_audit(db, "foerder_pumpe.aus_vorlage", org_id=user.org_id, user_id=user.id,
                entity_type="foerder_pumpen_typ", entity_id=p.id,
                payload={"vorlage": key, "name": p.name})
    db.commit()
    # Direkt in den Bearbeiten-Dialog, damit der Anwender justieren kann.
    return RedirectResponse(f"/admin/foerderpumpen/{p.id}/bearbeiten", status_code=303)


@router.get("/admin/foerderpumpen/{pid}/bearbeiten", response_class=HTMLResponse)
def pumpe_bearbeiten_formular(
    pid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    p = _pumpe_or_404(pid, db, user)
    return templates.TemplateResponse(request, "admin/foerder_pumpe_form.html", {
        "user": user,
        "pumpe": p,
        "vehicles": _org_vehicles(db, user),
        "fehler": [],
        "form": {},
    })


@router.post("/admin/foerderpumpen/neu")
def pumpe_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
    name: str = Form(...),
    kennlinien_json: str = Form(""),
    druck_anschluss_dn: str = Form(""),
    druck_parallel_max: str = Form("1"),
    saug_anschluss_dn: str = Form(""),
    saug_parallel_max: str = Form("1"),
    max_ansaughoehe_m: str = Form("7.5"),
    min_eingangsdruck_bar: str = Form("1.5"),
    max_ausgangsdruck_bar: str = Form(""),
    tank_l: str = Form(""),
    vehicle_id: str = Form(""),
    hinweise: str = Form(""),
    verbrauch_json: str = Form(""),
    npshr_json: str = Form(""),
):
    return _pumpe_speichern(
        None, request, db, user, name, kennlinien_json, druck_anschluss_dn,
        druck_parallel_max, saug_anschluss_dn, saug_parallel_max, max_ansaughoehe_m,
        min_eingangsdruck_bar, max_ausgangsdruck_bar, tank_l, vehicle_id, hinweise,
        verbrauch_json, npshr_json,
    )


@router.post("/admin/foerderpumpen/{pid}/bearbeiten")
def pumpe_bearbeiten(
    pid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
    name: str = Form(...),
    kennlinien_json: str = Form(""),
    druck_anschluss_dn: str = Form(""),
    druck_parallel_max: str = Form("1"),
    saug_anschluss_dn: str = Form(""),
    saug_parallel_max: str = Form("1"),
    max_ansaughoehe_m: str = Form("7.5"),
    min_eingangsdruck_bar: str = Form("1.5"),
    max_ausgangsdruck_bar: str = Form(""),
    tank_l: str = Form(""),
    vehicle_id: str = Form(""),
    hinweise: str = Form(""),
    verbrauch_json: str = Form(""),
    npshr_json: str = Form(""),
):
    p = _pumpe_or_404(pid, db, user)
    return _pumpe_speichern(
        p, request, db, user, name, kennlinien_json, druck_anschluss_dn,
        druck_parallel_max, saug_anschluss_dn, saug_parallel_max, max_ansaughoehe_m,
        min_eingangsdruck_bar, max_ausgangsdruck_bar, tank_l, vehicle_id, hinweise,
        verbrauch_json, npshr_json,
    )


def _pumpe_speichern(
    p: FoerderPumpenTyp | None, request, db, user, name, kennlinien_json,
    druck_anschluss_dn, druck_parallel_max, saug_anschluss_dn, saug_parallel_max,
    max_ansaughoehe_m, min_eingangsdruck_bar, max_ausgangsdruck_bar, tank_l,
    vehicle_id, hinweise, verbrauch_json, npshr_json,
):
    kennlinien, fehler = _parse_kennlinien(kennlinien_json)
    if not name.strip():
        fehler.append("Name ist erforderlich.")
    if fehler:
        # Formular mit Fehlern und Eingaben neu rendern
        form = {
            "name": name, "kennlinien_json": kennlinien_json,
            "druck_anschluss_dn": druck_anschluss_dn, "druck_parallel_max": druck_parallel_max,
            "saug_anschluss_dn": saug_anschluss_dn, "saug_parallel_max": saug_parallel_max,
            "max_ansaughoehe_m": max_ansaughoehe_m, "min_eingangsdruck_bar": min_eingangsdruck_bar,
            "max_ausgangsdruck_bar": max_ausgangsdruck_bar, "tank_l": tank_l,
            "vehicle_id": vehicle_id, "hinweise": hinweise,
            "verbrauch_json": verbrauch_json, "npshr_json": npshr_json,
        }
        return templates.TemplateResponse(request, "admin/foerder_pumpe_form.html", {
            "user": user, "pumpe": p, "vehicles": _org_vehicles(db, user),
            "fehler": fehler, "form": form,
        }, status_code=400)

    neu = p is None
    if neu:
        p = FoerderPumpenTyp(org_id=user.org_id, quelle=QUELLE_MANUELL, erstellt_von_id=user.id)
        db.add(p)
    p.name = name.strip()[:150]
    p.kennlinien_json = json.dumps(kennlinien)
    p.druck_anschluss_dn = _i(druck_anschluss_dn)
    p.druck_parallel_max = max(1, _i(druck_parallel_max) or 1)
    p.saug_anschluss_dn = _i(saug_anschluss_dn)
    p.saug_parallel_max = max(1, _i(saug_parallel_max) or 1)
    p.max_ansaughoehe_m = _f(max_ansaughoehe_m) or 7.5
    p.min_eingangsdruck_bar = _f(min_eingangsdruck_bar) if _f(min_eingangsdruck_bar) is not None else 1.5
    p.max_ausgangsdruck_bar = _f(max_ausgangsdruck_bar)
    p.tank_l = _i(tank_l)
    p.hinweise = hinweise.strip() or None
    p.aktualisiert_von_id = user.id
    # verbrauch_json / npshr_json werden als Rohwerte durchgereicht (Editor optional)
    p.verbrauch_json = verbrauch_json.strip() or None
    p.npshr_json = npshr_json.strip() or None
    # Fahrzeugzuordnung (nur eigene Org)
    vid = _i(vehicle_id)
    p.vehicle_id = vid if vid else None

    db.flush()
    write_audit(db, "foerder_pumpe.created" if neu else "foerder_pumpe.updated",
                org_id=user.org_id, user_id=user.id,
                entity_type="foerder_pumpen_typ", entity_id=p.id,
                payload={"name": p.name})
    db.commit()
    return RedirectResponse("/admin/foerderpumpen", status_code=303)


@router.post("/admin/foerderpumpen/{pid}/loeschen")
def pumpe_loeschen(
    pid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    p = _pumpe_or_404(pid, db, user)
    write_audit(db, "foerder_pumpe.deleted", org_id=user.org_id, user_id=user.id,
                entity_type="foerder_pumpen_typ", entity_id=p.id, payload={"name": p.name})
    db.delete(p)
    db.commit()
    return RedirectResponse("/admin/foerderpumpen", status_code=303)


def _org_vehicles(db: Session, user: User) -> list:
    from app.models.master import VehicleMaster
    return (
        db.query(VehicleMaster)
        .filter(VehicleMaster.dept_id == user.org_id, VehicleMaster.active.is_(True),
                VehicleMaster.deleted.is_(False))
        .order_by(VehicleMaster.display_order, VehicleMaster.name)
        .all()
    )


# ══════════════════════════════════════════════════════════════════════════════
# Schläuche
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/foerderschlaeuche", response_class=HTMLResponse)
def schlaeuche_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    rows = (
        db.query(FoerderSchlauchTyp)
        .filter(FoerderSchlauchTyp.org_id == user.org_id)
        .order_by(FoerderSchlauchTyp.aktiv.desc(), FoerderSchlauchTyp.durchmesser_mm.desc())
        .all()
    )
    return templates.TemplateResponse(request, "admin/foerder_schlaeuche.html", {
        "user": user,
        "schlaeuche": rows,
        "vorlagen": SCHLAUCH_VORLAGEN,
    })


@router.post("/admin/foerderschlaeuche/aus-vorlage/{key}")
def schlauch_aus_vorlage(
    key: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    vorlage = schlauch_vorlage(key)
    if not vorlage:
        raise HTTPException(404, "Vorlage nicht gefunden")
    felder = vorlage["felder"]
    d = felder["durchmesser_mm"]
    s = FoerderSchlauchTyp(
        org_id=user.org_id,
        kuerzel=felder["kuerzel"],
        durchmesser_mm=d,
        k_verlust=felder["k_verlust"],
        element_laenge_m=felder.get("element_laenge_m", 20),
        max_betriebsdruck_bar=felder.get("max_betriebsdruck_bar"),
        wasserinhalt_l_m=wasserinhalt_pro_meter(d),
        aktiv=True,
        quelle=QUELLE_VORLAGE,
        vorlage_key=key,
        erstellt_von_id=user.id,
        aktualisiert_von_id=user.id,
    )
    db.add(s)
    db.flush()
    write_audit(db, "foerder_schlauch.aus_vorlage", org_id=user.org_id, user_id=user.id,
                entity_type="foerder_schlauch_typ", entity_id=s.id,
                payload={"vorlage": key, "kuerzel": s.kuerzel})
    db.commit()
    return RedirectResponse("/admin/foerderschlaeuche", status_code=303)


@router.post("/admin/foerderschlaeuche/neu")
def schlauch_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
    kuerzel: str = Form(...),
    durchmesser_mm: str = Form(...),
    k_verlust: str = Form(...),
    element_laenge_m: str = Form("20"),
    max_betriebsdruck_bar: str = Form(""),
    vorrat_m: str = Form(""),
):
    d = _i(durchmesser_mm)
    k = _f(k_verlust)
    if not kuerzel.strip() or not d or d <= 0 or k is None or k < 0:
        return RedirectResponse("/admin/foerderschlaeuche?fehler=eingabe", status_code=303)
    s = FoerderSchlauchTyp(
        org_id=user.org_id,
        kuerzel=kuerzel.strip()[:30],
        durchmesser_mm=d,
        k_verlust=k,
        element_laenge_m=_i(element_laenge_m) or 20,
        max_betriebsdruck_bar=_f(max_betriebsdruck_bar),
        wasserinhalt_l_m=wasserinhalt_pro_meter(d),
        vorrat_m=_i(vorrat_m),
        quelle=QUELLE_MANUELL,
        erstellt_von_id=user.id,
        aktualisiert_von_id=user.id,
    )
    db.add(s)
    db.flush()
    write_audit(db, "foerder_schlauch.created", org_id=user.org_id, user_id=user.id,
                entity_type="foerder_schlauch_typ", entity_id=s.id, payload={"kuerzel": s.kuerzel})
    db.commit()
    return RedirectResponse("/admin/foerderschlaeuche", status_code=303)


@router.post("/admin/foerderschlaeuche/{sid}/bearbeiten")
def schlauch_bearbeiten(
    sid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
    kuerzel: str = Form(...),
    durchmesser_mm: str = Form(...),
    k_verlust: str = Form(...),
    element_laenge_m: str = Form("20"),
    max_betriebsdruck_bar: str = Form(""),
    vorrat_m: str = Form(""),
):
    s = _schlauch_or_404(sid, db, user)
    d = _i(durchmesser_mm)
    k = _f(k_verlust)
    if not kuerzel.strip() or not d or d <= 0 or k is None or k < 0:
        return RedirectResponse("/admin/foerderschlaeuche?fehler=eingabe", status_code=303)
    s.kuerzel = kuerzel.strip()[:30]
    s.durchmesser_mm = d
    s.k_verlust = k
    s.element_laenge_m = _i(element_laenge_m) or 20
    s.max_betriebsdruck_bar = _f(max_betriebsdruck_bar)
    s.wasserinhalt_l_m = wasserinhalt_pro_meter(d)
    s.vorrat_m = _i(vorrat_m)
    s.aktualisiert_von_id = user.id
    write_audit(db, "foerder_schlauch.updated", org_id=user.org_id, user_id=user.id,
                entity_type="foerder_schlauch_typ", entity_id=s.id, payload={"kuerzel": s.kuerzel})
    db.commit()
    return RedirectResponse("/admin/foerderschlaeuche", status_code=303)


@router.post("/admin/foerderschlaeuche/{sid}/loeschen")
def schlauch_loeschen(
    sid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    s = _schlauch_or_404(sid, db, user)
    write_audit(db, "foerder_schlauch.deleted", org_id=user.org_id, user_id=user.id,
                entity_type="foerder_schlauch_typ", entity_id=s.id, payload={"kuerzel": s.kuerzel})
    db.delete(s)
    db.commit()
    return RedirectResponse("/admin/foerderschlaeuche", status_code=303)
