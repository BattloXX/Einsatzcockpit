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
    lage_id: int | None = None,
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
        "lagen": _aktive_lagen(db, user.org_id),
        "prelink_lage_id": lage_id,
    })


def _aktive_lagen(db: Session, org_id: int | None) -> list[dict]:
    """Aktive/Standby-Lagen der Org für das Verknüpfen einer Förderstrecke (Auftrag)."""
    try:
        from app.models.major_incident import MajorIncident, MajorIncidentStatus
        rows = (
            db.query(MajorIncident)
            .filter(MajorIncident.org_id == org_id,
                    MajorIncident.status != MajorIncidentStatus.closed)
            .order_by(MajorIncident.created_at.desc())
            .all()
        )
        return [{"id": r.id, "name": r.name, "status": str(r.status)} for r in rows]
    except Exception:
        return []


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


# ── Wasserstellen als (ein-/ausblendbarer) Karten-Layer ───────────────────────

@router.get("/wasserstellen.json")
def wasserstellen_layer(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    """Aktive Wasserstellen der Org (mit Koordinaten) für den Förderstrecken-Kartenlayer."""
    from app.models.wasserstelle import Wasserstelle
    rows = (
        db.query(Wasserstelle)
        .filter(Wasserstelle.org_id == user.org_id, Wasserstelle.aktiv.is_(True),
                Wasserstelle.lat.isnot(None), Wasserstelle.lng.isnot(None))
        .order_by(Wasserstelle.typ, Wasserstelle.bezeichnung)
        .all()
    )
    return JSONResponse({"wasserstellen": [
        {"id": w.id, "bezeichnung": w.bezeichnung, "typ": w.typ, "typ_label": w.typ_label,
         "icon_kat": w.icon_kat, "lat": w.lat, "lng": w.lng, "hinweis": w.hinweis,
         "ergiebigkeit_l_min": w.ergiebigkeit_l_min, "status": w.status,
         "status_label": w.status_label}
        for w in rows
    ]})


# ── Straßen-Routing (Start→Ende der Förderleitung entlang der Straße) ──────────

@router.post("/strassenroute")
async def strassenroute(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    """Sucht eine straßenfolgende Route über die übergebenen Wegpunkte (Start, [Vias], Ende).

    Rückgabe {coords:[[lat,lng],…], laenge_m}. `ok:false`, wenn kein Routing möglich —
    der Client fällt dann auf die Luftlinie zurück (manuelles Zeichnen bleibt maßgeblich).
    """
    from app.services.routing_service import strassen_route
    daten = await request.json()
    punkte = [(float(p[0]), float(p[1])) for p in (daten.get("punkte") or []) if len(p) >= 2]
    if len(punkte) < 2:
        raise HTTPException(400, "Start- und Endpunkt erforderlich.")
    res = await strassen_route(punkte)
    if res is None:
        return JSONResponse({"ok": False})
    return JSONResponse({"ok": True, **res})


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


def _eingangsdruck(ansaug: dict) -> float | None:
    """Vordruck der ersten Pumpe (Hydrant/Netz) aus dem Ansaug-Dict, sonst None.

    Nur bei aktivierter Druckspeisung (`druckspeisung`==true) und gültigem Wert; leere
    Eingabe/0 ohne Aktivierung → None (offene Saugstelle).
    """
    if not ansaug.get("druckspeisung"):
        return None
    wert = ansaug.get("eingangsdruck_bar")
    if wert is None or wert == "":
        return None
    try:
        return float(wert)
    except (TypeError, ValueError):
        return None


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
        saug_scheitel_m=float(a.get("saug_scheitel_m") or 0.0),
        eingangsdruck_bar=_eingangsdruck(a),
    )
    # Gesamt-Höhenprofil der Route [[s_m, hoehe_m], …]: liefert je Abschnitt das
    # tatsächliche Zwischengelände (Damm etc.) für die segmentweise Drucklinie.
    full_profil = daten.get("hoehenprofil")
    s_kumuliert = 0.0
    stationen: list[engine.PumpenStation] = []
    material_abschnitte: list[dict] = []
    for st in (daten.get("stationen") or []):
        kl, meta = _pump_kennlinie(st, db, org_id)
        abschnitt_roh = st.get("abschnitt") or {}
        schlauch = _schlauch_daten(abschnitt_roh, db, org_id)
        n_par = int(abschnitt_roh.get("n_parallel") or 1)
        laenge = float(abschnitt_roh.get("laenge_m") or 0.0)
        # Höhenprofil des Abschnitts: bevorzugt aus dem Gesamtprofil (echtes Gelände),
        # sonst der vom Client gelieferte Stützpunkt-Fallback.
        stuetz = abschnitt_roh.get("hoehen_stuetzpunkte")
        delta = float(abschnitt_roh.get("delta_hoehe_m") or 0.0)
        if full_profil and laenge > 0:
            aus_profil = engine.abschnitt_hoehen_stuetzpunkte(
                full_profil, s_kumuliert, s_kumuliert + laenge)
            if aus_profil:
                stuetz = aus_profil
                delta = aus_profil[-1]     # Endhöhe = letzter Stützpunkt (konsistent)
        abschnitt = engine.Abschnitt(
            schlauch_k=float(schlauch["k_verlust"]),
            laenge_m=laenge,
            n_parallel=n_par,
            delta_hoehe_m=delta,
            max_betriebsdruck_bar=schlauch.get("max_betriebsdruck_bar"),
            hoehen_stuetzpunkte=stuetz,
        )
        s_kumuliert += laenge
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
    ansaug, stationen, material_abschnitte = _baue_eingabe(daten, db, user.org_id)  # type: ignore[arg-type]
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


# ── Modus B: Pumpenstandorte vorschlagen ──────────────────────────────────────

def _route_laenge_m(route: list) -> float:
    from app.services.hoehen_service import haversine_m
    laenge = 0.0
    for i in range(len(route) - 1):
        laenge += haversine_m(route[i][0], route[i][1], route[i + 1][0], route[i + 1][1])
    return laenge


def _punkt_bei_s(route: list, s_ziel: float) -> list | None:
    """Koordinate [lat, lng] an der Bogenlänge s entlang der Route (Haversine)."""
    from app.services.hoehen_service import haversine_m
    if not route:
        return None
    if len(route) == 1 or s_ziel <= 0:
        return [route[0][0], route[0][1]]
    acc = 0.0
    for i in range(len(route) - 1):
        d = haversine_m(route[i][0], route[i][1], route[i + 1][0], route[i + 1][1])
        if acc + d >= s_ziel:
            t = (s_ziel - acc) / d if d > 0 else 0.0
            return [route[i][0] + t * (route[i + 1][0] - route[i][0]),
                    route[i][1] + t * (route[i + 1][1] - route[i][1])]
        acc += d
    return [route[-1][0], route[-1][1]]


@router.post("/standort-vorschlag")
async def standort_vorschlag(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_foerderstrecke_enabled),
):
    """Empfiehlt Pumpenstandorte (Modus B) für eine Ziel-Fördermenge entlang der Route.

    Nutzt die gewählte Quellpumpe + eine Relaispumpe (aus dem Katalog) und liefert eine
    fertige Stationenliste (mit Koordinaten) zurück, die der Wizard übernimmt.
    """
    daten = await request.json()
    route = [[float(p[0]), float(p[1])] for p in (daten.get("route") or []) if len(p) >= 2]
    if len(route) < 2:
        raise HTTPException(400, "Route mit mindestens 2 Punkten erforderlich.")
    laenge = _route_laenge_m(route)

    quelle = _pumpe_oder_400(daten.get("quelle_pumpe_id"), db, user.org_id)  # type: ignore[arg-type]
    relais = _pumpe_oder_400(daten.get("relais_pumpe_id"), db, user.org_id)  # type: ignore[arg-type]
    schlauch = db.get(FoerderSchlauchTyp, int(daten["schlauch_typ_id"])) if daten.get("schlauch_typ_id") else None
    if schlauch is None or schlauch.org_id != user.org_id:
        raise HTTPException(400, "Schlauchtyp erforderlich.")
    n_par = int(daten.get("n_parallel") or 1)
    quelle_rpm = str(daten.get("quelle_rpm") or "")
    relais_rpm = str(daten.get("relais_rpm") or "")

    a = daten.get("ansaug") or {}
    ansaug = engine.Ansaugpunkt(
        seehoehe_m=float(a.get("seehoehe_m") or 430.0),
        geodaetische_saughoehe_m=float(a.get("geodaetische_saughoehe_m") or 3.0),
        saug_k=float(a.get("saug_k") or 0.23),
        saug_n_parallel=int(a.get("saug_n_parallel") or 1),
        max_ansaughoehe_m=float(a.get("max_ansaughoehe_m") or 7.5),
        saug_scheitel_m=float(a.get("saug_scheitel_m") or 0.0),
        eingangsdruck_bar=_eingangsdruck(a),
    )

    ergebnis = engine.standort_vorschlag(
        laenge, _kennlinie_stufe(quelle, quelle_rpm), _kennlinie_stufe(relais, relais_rpm),
        schlauch.k_verlust, float(daten.get("ziel_q_l_min") or 0.0),
        n_parallel=n_par, hoehenprofil=daten.get("hoehenprofil"), ansaug=ansaug,
        max_ausgang_quelle=quelle.max_ausgangsdruck_bar, max_ausgang_relais=relais.max_ausgangsdruck_bar,
    )

    # Standorte → Koordinaten + fertige Stationenliste (Abschnitt bis zur Folgestation/Auslass)
    full_profil = daten.get("hoehenprofil")
    standorte = ergebnis["standorte"]
    stationen_out = []
    for i, st in enumerate(standorte):
        s_start = st["s_m"]
        s_end = standorte[i + 1]["s_m"] if i + 1 < len(standorte) else laenge
        koord = _punkt_bei_s(route, s_start) or [None, None]
        st["lat"], st["lng"] = koord[0], koord[1]
        ab_laenge = round(s_end - s_start, 1)
        delta = 0.0
        if full_profil and ab_laenge > 0:
            stz = engine.abschnitt_hoehen_stuetzpunkte(full_profil, s_start, s_end)
            if stz:
                delta = stz[-1]
        ist_quelle = st["typ"] == "quellpumpe"
        stationen_out.append({
            "typ": st["typ"],
            "pumpen_typ_id": quelle.id if ist_quelle else relais.id,
            "rpm": quelle_rpm if ist_quelle else relais_rpm,
            "lat": koord[0], "lng": koord[1],
            "abschnitt": {"schlauch_typ_id": schlauch.id, "n_parallel": n_par,
                          "laenge_m": ab_laenge, "delta_hoehe_m": delta},
        })

    return JSONResponse({**ergebnis, "laenge_m": round(laenge, 1), "stationen": stationen_out})


def _pumpe_oder_400(pid, db: Session, org_id: int) -> FoerderPumpenTyp:
    if not pid:
        raise HTTPException(400, "Pumpe erforderlich.")
    p = db.get(FoerderPumpenTyp, int(pid))
    if p is None or p.org_id != org_id:
        raise HTTPException(400, "Unbekannte Pumpe.")
    return p


def _kennlinie_stufe(pumpe: FoerderPumpenTyp, rpm: str) -> list:
    kl = pumpe.kennlinien
    if rpm and rpm in kl:
        return list(kl[rpm])
    if pumpe.drehzahlstufen:
        return list(kl.get(pumpe.drehzahlstufen[0]) or [])
    return []


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
        "lagen": _aktive_lagen(db, user.org_id),
        "prelink_lage_id": None,
    })


def _strecke_json(s: Foerderstrecke) -> str:
    return json.dumps({
        "id": s.id, "name": s.name, "status": s.status, "lage_id": s.lage_id,
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
    from app.services.foerderstrecke_pdf_service import (
        berechne_gespeicherte_strecke,
        karte_png_datauri,
    )

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
        "gesamt_laenge_m": daten["gesamt_laenge_m"],
        "karte_png": karte_png_datauri(strecke, daten["stationen_info"]),
    })
