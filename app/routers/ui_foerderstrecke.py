"""Förderstrecken-Planer — Karten-Wizard, Live-Berechnung, Persistenz (PR 4/5-UI).

Prefix: /foerderstrecke — Rolle recorder+ (Planung), Modul-Guard (404 wenn inaktiv).

Der Wizard (Leaflet+Geoman) postet die gezeichnete Route + bestückte Technik als JSON;
`/berechnen` löst Katalogdaten auf, ruft die Hydraulik-Engine und liefert Ergebnis +
Höhenprofil-SVG zurück. `/hoehenprofil` liefert Geländehöhen für die gezeichnete Route.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.foerderstrecke import (
    STATION_TYPEN,
    STRECKE_STATUS,
    STRECKE_STATUS_ENTWURF,
    FoerderMaschinistToken,
    FoerderPumpenTyp,
    FoerderSchlauchTyp,
    FoerderStation,
    Foerderstrecke,
)
from app.models.user import User
from app.routers.ui_foerderstrecke_admin import require_foerderstrecke_enabled
from app.services import foerderstrecke_service as engine
from app.services.chart_svg import foerderprofil_svg
from app.services.foerderstrecke_persist_service import ergebnis_anhaengen, setze_status

router = APIRouter(prefix="/foerderstrecke", tags=["foerderstrecke"])


# ── Wizard-Seite + Liste ──────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def wizard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    strecken = (
        db.query(Foerderstrecke)
        .filter(Foerderstrecke.org_id == user.org_id)
        .order_by(Foerderstrecke.aktualisiert_am.desc())
        .all()
    )
    pumpen = (
        db.query(FoerderPumpenTyp)
        .filter(FoerderPumpenTyp.org_id == user.org_id, FoerderPumpenTyp.aktiv.is_(True))
        .order_by(FoerderPumpenTyp.name).all()
    )
    schlaeuche = (
        db.query(FoerderSchlauchTyp)
        .filter(FoerderSchlauchTyp.org_id == user.org_id, FoerderSchlauchTyp.aktiv.is_(True))
        .order_by(FoerderSchlauchTyp.durchmesser_mm.desc()).all()
    )
    return templates.TemplateResponse(request, "foerderstrecke/wizard.html", {
        "user": user,
        "strecken": strecken,
        "pumpen": pumpen,
        "schlaeuche": schlaeuche,
        "station_typen": STATION_TYPEN,
        "status_labels": STRECKE_STATUS,
        "pumpen_json": _pumpen_json(pumpen),
        "schlaeuche_json": _schlaeuche_json(schlaeuche),
        "lade_strecke_id": None,
    })


def _pumpen_json(pumpen: list[FoerderPumpenTyp]) -> str:
    return json.dumps([{
        "id": p.id, "name": p.name, "kennlinien": p.kennlinien,
        "drehzahlstufen": p.drehzahlstufen,
        "druck_anschluss_dn": p.druck_anschluss_dn, "druck_parallel_max": p.druck_parallel_max,
        "saug_anschluss_dn": p.saug_anschluss_dn, "saug_parallel_max": p.saug_parallel_max,
        "max_ansaughoehe_m": p.max_ansaughoehe_m, "min_eingangsdruck_bar": p.min_eingangsdruck_bar,
        "max_ausgangsdruck_bar": p.max_ausgangsdruck_bar,
    } for p in pumpen])


def _schlaeuche_json(schlaeuche: list[FoerderSchlauchTyp]) -> str:
    return json.dumps([{
        "id": s.id, "kuerzel": s.kuerzel, "durchmesser_mm": s.durchmesser_mm,
        "k_verlust": s.k_verlust, "element_laenge_m": s.element_laenge_m,
        "max_betriebsdruck_bar": s.max_betriebsdruck_bar, "wasserinhalt_l_m": s.wasserinhalt_l_m,
    } for s in schlaeuche])


# ── Höhenprofil für eine gezeichnete Route ────────────────────────────────────

@router.post("/hoehenprofil")
async def hoehenprofil(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    from app.services.hoehen_service import hoehenprofil as hp
    daten = await request.json()
    route = [(float(p[0]), float(p[1])) for p in (daten.get("route") or []) if len(p) >= 2]
    if len(route) < 2:
        return JSONResponse({"stuetzpunkte": [], "quelle": "keine", "grob": False})
    res = await hp(route, segment_m=float(daten.get("segment_m") or 25.0), db=db)
    return JSONResponse(res)


# ── Live-Berechnung ───────────────────────────────────────────────────────────

def _pump_kennlinie(payload: dict, db: Session, org_id: int) -> tuple[list, dict]:
    """Löst Kennlinie + Pumpen-Kenngrößen aus Katalog (pumpen_typ_id) oder Inline-Daten."""
    pid = payload.get("pumpen_typ_id")
    meta: dict = {}
    if pid:
        p = db.get(FoerderPumpenTyp, int(pid))
        if p is None or p.org_id != org_id:
            raise HTTPException(400, "Unbekannter Pumpentyp")
        kl_alle = p.kennlinien
        rpm = str(payload.get("rpm") or "")
        kl = kl_alle.get(rpm) if rpm in kl_alle else (
            kl_alle.get(p.drehzahlstufen[0]) if p.drehzahlstufen else [])
        meta = {
            "max_ausgangsdruck_bar": p.max_ausgangsdruck_bar,
            "min_eingangsdruck_bar": p.min_eingangsdruck_bar,
            "max_ansaughoehe_m": p.max_ansaughoehe_m,
            "name": p.name,
        }
        return list(kl or []), meta
    return list(payload.get("kennlinie") or []), {
        "max_ausgangsdruck_bar": payload.get("max_ausgangsdruck_bar"),
        "min_eingangsdruck_bar": payload.get("min_eingangsdruck_bar", engine.DEFAULT_MIN_EINGANGSDRUCK_BAR),
        "max_ansaughoehe_m": payload.get("max_ansaughoehe_m", 7.5),
        "name": payload.get("name", ""),
    }


def _schlauch_daten(payload: dict, db: Session, org_id: int) -> dict:
    sid = payload.get("schlauch_typ_id")
    if sid:
        s = db.get(FoerderSchlauchTyp, int(sid))
        if s is None or s.org_id != org_id:
            raise HTTPException(400, "Unbekannter Schlauchtyp")
        return {"kuerzel": s.kuerzel, "k_verlust": s.k_verlust,
                "element_laenge_m": s.element_laenge_m,
                "wasserinhalt_l_m": s.wasserinhalt_l_m,
                "max_betriebsdruck_bar": s.max_betriebsdruck_bar}
    return {"kuerzel": payload.get("schlauch_kuerzel", ""),
            "k_verlust": float(payload.get("schlauch_k") or 0.0),
            "element_laenge_m": float(payload.get("element_laenge_m") or 20.0),
            "wasserinhalt_l_m": payload.get("wasserinhalt_l_m"),
            "max_betriebsdruck_bar": payload.get("max_betriebsdruck_bar")}


def _baue_eingabe(daten: dict, db: Session, org_id: int):
    """Baut Engine-Eingaben + Material-Abschnitte aus dem Wizard-Payload."""
    a = daten.get("ansaug") or {}
    ansaug = engine.Ansaugpunkt(
        seehoehe_m=float(a.get("seehoehe_m") or 430.0),
        geodaetische_saughoehe_m=float(a.get("geodaetische_saughoehe_m") or 3.0),
        saug_k=float(a.get("saug_k") or 0.23),
        saug_n_parallel=int(a.get("saug_n_parallel") or 1),
        saugleitung_laenge_m=float(a.get("saugleitung_laenge_m") or 0.0),
        max_ansaughoehe_m=float(a.get("max_ansaughoehe_m") or 7.5),
        npshr_m=float(a.get("npshr_m") or 0.0),
    )
    stationen: list[engine.PumpenStation] = []
    material_abschnitte: list[dict] = []
    for st in (daten.get("stationen") or []):
        kl, meta = _pump_kennlinie(st, db, org_id)
        abschnitt_roh = st.get("abschnitt") or {}
        schlauch = _schlauch_daten(abschnitt_roh, db, org_id)
        n_par = int(abschnitt_roh.get("n_parallel") or 1)
        laenge = float(abschnitt_roh.get("laenge_m") or 0.0)
        abschnitt = engine.Abschnitt(
            schlauch_k=float(schlauch["k_verlust"]),
            laenge_m=laenge,
            n_parallel=n_par,
            delta_hoehe_m=float(abschnitt_roh.get("delta_hoehe_m") or 0.0),
            max_betriebsdruck_bar=schlauch.get("max_betriebsdruck_bar"),
            hoehen_stuetzpunkte=abschnitt_roh.get("hoehen_stuetzpunkte"),
        )
        stationen.append(engine.PumpenStation(
            kennlinie=kl,
            typ=st.get("typ") or "verstaerker",
            max_ausgangsdruck_bar=meta.get("max_ausgangsdruck_bar"),
            min_eingangsdruck_bar=float(meta.get("min_eingangsdruck_bar") or engine.DEFAULT_MIN_EINGANGSDRUCK_BAR),
            behaelter_volumen_l=st.get("behaelter_volumen_l"),
            name=st.get("name") or meta.get("name") or "",
            abschnitt_danach=abschnitt if laenge > 0 else None,
        ))
        if laenge > 0:
            material_abschnitte.append({
                "kuerzel": schlauch["kuerzel"], "laenge_m": laenge, "n_parallel": n_par,
                "element_laenge_m": schlauch["element_laenge_m"],
                "wasserinhalt_l_m": schlauch["wasserinhalt_l_m"],
            })
    return ansaug, stationen, material_abschnitte


@router.post("/berechnen")
async def berechnen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    daten = await request.json()
    param = daten.get("parameter") or {}
    ansaug, stationen, material_abschnitte = _baue_eingabe(daten, db, user.org_id)
    if not stationen:
        return JSONResponse({"machbar": False, "warnungen": ["Keine Pumpenstation gesetzt."],
                             "q_max_l_min": 0, "druckprofil": [], "stationswerte": [],
                             "svg": foerderprofil_svg([]), "material": {}})

    ergebnis = engine.berechne_modus_a(
        ansaug, stationen,
        ziel_druck_bar=float(daten.get("ziel_druck_bar") or 0.0),
        armaturen_zuschlag=float(param.get("armaturen_zuschlag") or 0.05),
        hochpunkt_min_bar=float(param.get("hochpunkt_min_bar") or engine.HOCHPUNKT_MIN_BAR),
    )
    q = ergebnis["q_max_l_min"]
    material = engine.materialbilanz(material_abschnitte, q, reserve=float(param.get("reserve") or 0.10))

    # Stationsmarken (kumulierte Distanz) für das Profil
    marken = []
    s = 0.0
    for st in stationen:
        marken.append({"s_m": s, "label": st.name or STATION_TYPEN.get(st.typ, st.typ)})
        if st.abschnitt_danach:
            s += st.abschnitt_danach.laenge_m

    # max. Betriebsdruck der schwächsten Grenze als Profil-Grenzlinie
    grenzen = [st.abschnitt_danach.max_betriebsdruck_bar for st in stationen
               if st.abschnitt_danach and st.abschnitt_danach.max_betriebsdruck_bar]
    p_max = min(grenzen) if grenzen else None

    svg = foerderprofil_svg(
        ergebnis["druckprofil"],
        hoehenprofil=daten.get("hoehenprofil"),
        p_max_bar=p_max,
        hochpunkt_min_bar=float(param.get("hochpunkt_min_bar") or engine.HOCHPUNKT_MIN_BAR),
        stationen=marken,
        titel=daten.get("name") or None,
    )
    return JSONResponse({**ergebnis, "material": material, "svg": svg})


# ── Persistenz (PR 5-UI): Speichern / Laden / Status / Löschen ────────────────

@router.post("/speichern")
async def speichern(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    daten = await request.json()
    sid = daten.get("id")
    if sid:
        strecke = db.get(Foerderstrecke, int(sid))
        if strecke is None or strecke.org_id != user.org_id:
            raise HTTPException(404, "Strecke nicht gefunden")
        strecke.stationen.clear()
        db.flush()
    else:
        strecke = Foerderstrecke(org_id=user.org_id, status=STRECKE_STATUS_ENTWURF,
                                 erstellt_von_id=user.id)
        db.add(strecke)

    strecke.name = (daten.get("name") or "Förderstrecke").strip()[:150]
    strecke.route_geojson = json.dumps(daten.get("route_geojson")) if daten.get("route_geojson") else None
    strecke.ansaug_json = json.dumps(daten.get("ansaug")) if daten.get("ansaug") else None
    strecke.auslass_json = json.dumps(daten.get("auslass")) if daten.get("auslass") else None
    strecke.hoehenprofil_json = json.dumps(daten.get("hoehenprofil")) if daten.get("hoehenprofil") else None
    strecke.parameter_json = json.dumps(daten.get("parameter")) if daten.get("parameter") else None
    strecke.objekt_id = daten.get("objekt_id")
    strecke.incident_id = daten.get("incident_id")
    strecke.lage_id = daten.get("lage_id")
    strecke.aktualisiert_von_id = user.id
    db.flush()

    for i, st in enumerate(daten.get("stationen") or []):
        ab = st.get("abschnitt") or {}
        db.add(FoerderStation(
            org_id=user.org_id, strecke_id=strecke.id, sort=i,
            strang_nr=int(st.get("strang_nr") or 1),
            lat=st.get("lat"), lng=st.get("lng"),
            typ=st.get("typ") or "verstaerker",
            pumpen_typ_id=st.get("pumpen_typ_id"), rpm=st.get("rpm"),
            druck_parallel=int(ab.get("n_parallel") or 1),
            schlauch_typ_id=ab.get("schlauch_typ_id"),
            abschnitt_laenge_m=ab.get("laenge_m"),
            abschnitt_delta_hoehe_m=float(ab.get("delta_hoehe_m") or 0.0),
            saug_parallel=int(st.get("saug_parallel") or 1),
            behaelter_volumen_l=st.get("behaelter_volumen_l"),
        ))
    if daten.get("ergebnis"):
        ergebnis_anhaengen(db, strecke, daten["ergebnis"], modus=daten.get("modus", "A"))

    write_audit(db, "foerderstrecke.saved", org_id=user.org_id, user_id=user.id,
                entity_type="foerderstrecke", entity_id=strecke.id, payload={"name": strecke.name})
    db.commit()
    return JSONResponse({"id": strecke.id, "name": strecke.name})


@router.get("/{sid}", response_class=HTMLResponse)
def laden(
    sid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    strecke = db.get(Foerderstrecke, sid)
    if strecke is None or strecke.org_id != user.org_id:
        raise HTTPException(404, "Strecke nicht gefunden")
    pumpen = (db.query(FoerderPumpenTyp).filter(FoerderPumpenTyp.org_id == user.org_id,
              FoerderPumpenTyp.aktiv.is_(True)).order_by(FoerderPumpenTyp.name).all())
    schlaeuche = (db.query(FoerderSchlauchTyp).filter(FoerderSchlauchTyp.org_id == user.org_id,
                  FoerderSchlauchTyp.aktiv.is_(True)).order_by(FoerderSchlauchTyp.durchmesser_mm.desc()).all())
    strecken = (db.query(Foerderstrecke).filter(Foerderstrecke.org_id == user.org_id)
                .order_by(Foerderstrecke.aktualisiert_am.desc()).all())
    return templates.TemplateResponse(request, "foerderstrecke/wizard.html", {
        "user": user, "strecken": strecken, "pumpen": pumpen, "schlaeuche": schlaeuche,
        "station_typen": STATION_TYPEN, "status_labels": STRECKE_STATUS,
        "pumpen_json": _pumpen_json(pumpen), "schlaeuche_json": _schlaeuche_json(schlaeuche),
        "lade_strecke_id": strecke.id,
        "lade_strecke_json": _strecke_json(strecke),
    })


def _strecke_json(s: Foerderstrecke) -> str:
    return json.dumps({
        "id": s.id, "name": s.name, "status": s.status,
        "route_geojson": json.loads(s.route_geojson) if s.route_geojson else None,
        "ansaug": s.ansaug, "auslass": s.auslass,
        "hoehenprofil": json.loads(s.hoehenprofil_json) if s.hoehenprofil_json else None,
        "parameter": s.parameter,
        "stationen": [{
            "sort": st.sort, "strang_nr": st.strang_nr, "typ": st.typ,
            "lat": st.lat, "lng": st.lng, "pumpen_typ_id": st.pumpen_typ_id,
            "rpm": st.rpm, "druck_parallel": st.druck_parallel,
            "schlauch_typ_id": st.schlauch_typ_id, "saug_parallel": st.saug_parallel,
            "behaelter_volumen_l": st.behaelter_volumen_l,
            "abschnitt_laenge_m": st.abschnitt_laenge_m,
            "abschnitt_delta_hoehe_m": st.abschnitt_delta_hoehe_m,
        } for st in s.stationen],
    })


@router.get("/{sid}/pdf")
def pdf(
    sid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    from fastapi.responses import Response

    from app.services.foerderstrecke_pdf_service import render_foerderstrecke_pdf
    strecke = db.get(Foerderstrecke, sid)
    if strecke is None or strecke.org_id != user.org_id:
        raise HTTPException(404, "Strecke nicht gefunden")
    org = getattr(user, "org", None)
    pdf_bytes = render_foerderstrecke_pdf(strecke, org, db, base_url=str(request.base_url))
    dateiname = f"Wasserfoerderung_{strecke.name[:40]}.pdf".replace(" ", "_")
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{dateiname}"'})


@router.post("/{sid}/status")
def status_setzen(
    sid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    strecke = db.get(Foerderstrecke, sid)
    if strecke is None or strecke.org_id != user.org_id:
        raise HTTPException(404, "Strecke nicht gefunden")
    ziel_status = request.query_params.get("ziel") or ""
    if not setze_status(strecke, ziel_status):
        raise HTTPException(400, "Statusübergang nicht erlaubt")
    strecke.aktualisiert_von_id = user.id
    strecke.aktualisiert_am = datetime.now(UTC)
    db.commit()
    return RedirectResponse(f"/foerderstrecke/{sid}", status_code=303)


@router.post("/{sid}/loeschen")
def loeschen(
    sid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    strecke = db.get(Foerderstrecke, sid)
    if strecke is None or strecke.org_id != user.org_id:
        raise HTTPException(404, "Strecke nicht gefunden")
    write_audit(db, "foerderstrecke.deleted", org_id=user.org_id, user_id=user.id,
                entity_type="foerderstrecke", entity_id=strecke.id, payload={"name": strecke.name})
    db.delete(strecke)
    db.commit()
    return RedirectResponse("/foerderstrecke/", status_code=303)


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


@router.post("/{sid}/maschinisten-token")
def maschinisten_token_erzeugen(
    sid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    """Erzeugt (oder erneuert) den login-freien Maschinisten-Token einer Strecke.

    Der Klartext wird nur hier einmalig zurückgegeben (nur der Hash wird gespeichert).
    """
    strecke = db.get(Foerderstrecke, sid)
    if strecke is None or strecke.org_id != user.org_id:
        raise HTTPException(404, "Strecke nicht gefunden")
    # bestehende aktive Tokens widerrufen (frischer Link)
    for alt in (db.query(FoerderMaschinistToken)
                .filter(FoerderMaschinistToken.strecke_id == strecke.id,
                        FoerderMaschinistToken.org_id == user.org_id,
                        FoerderMaschinistToken.widerrufen_am.is_(None)).all()):
        alt.widerrufen_am = datetime.now(UTC)
    plain = secrets.token_urlsafe(24)
    db.add(FoerderMaschinistToken(org_id=user.org_id, strecke_id=strecke.id,
                                  token_hash=_hash_token(plain)))
    write_audit(db, "foerderstrecke.token_erzeugt", org_id=user.org_id, user_id=user.id,
                entity_type="foerderstrecke", entity_id=strecke.id)
    db.commit()
    basis = str(request.base_url).rstrip("/")
    return JSONResponse({"url": f"{basis}/m/foerderstrecke/{plain}"})


# ── Öffentliche, login-freie Maschinisten-Seite (SEC-11: scopet über Token) ───────

public_router = APIRouter(tags=["foerderstrecke-public"])


@public_router.get("/m/foerderstrecke/{token}", response_class=HTMLResponse)
def maschinisten_seite(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Login-freie Maschinisten-Zettel-Seite. Scopet ausschließlich über den Token."""
    from app.services.foerderstrecke_pdf_service import berechne_gespeicherte_strecke

    row = (
        db.query(FoerderMaschinistToken)
        .filter(FoerderMaschinistToken.token_hash == _hash_token(token))
        .execution_options(include_all_tenants=True)
        .first()
    )
    if row is None or not row.is_active:
        raise HTTPException(404, "Nicht gefunden")
    # Strecke ausschließlich innerhalb der Token-Org laden (SEC-11)
    strecke = (
        db.query(Foerderstrecke)
        .filter(Foerderstrecke.id == row.strecke_id, Foerderstrecke.org_id == row.org_id)
        .execution_options(include_all_tenants=True)
        .first()
    )
    if strecke is None:
        raise HTTPException(404, "Nicht gefunden")
    row.zuletzt_genutzt_am = datetime.now(UTC)
    db.commit()

    daten = berechne_gespeicherte_strecke(strecke, db)
    return templates.TemplateResponse(request, "foerderstrecke/maschinist.html", {
        "strecke": strecke,
        "stationswerte": daten["stationswerte"],
        "stationen_info": daten["stationen_info"],
        "q_max_l_min": daten["ergebnis"]["q_max_l_min"],
        "warnungen": daten["ergebnis"]["warnungen"],
    })
