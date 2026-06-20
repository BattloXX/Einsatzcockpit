"""UAS-Modul Router (PR 0–3).

Alle Routen brauchen require_uas_enabled (HTTP 404 wenn Modul inaktiv).
Prefix: /uas
"""
from __future__ import annotations

import json
import secrets
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.user import User

router = APIRouter(prefix="/uas", tags=["uas"])


# ── Guard ──────────────────────────────────────────────────────────────────────

def require_uas_enabled(request: Request) -> None:
    """Guard-Dependency: HTTP 404 wenn UAS-Modul nicht effektiv aktiv (System+Org)."""
    if not getattr(request.state, "uas_module_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


# ── Startseite ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def uas_index(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.services.uas_compliance import compliance_dashboard
    dashboard = compliance_dashboard(user.org_id, db)
    return templates.TemplateResponse(request, "uas/index.html", {
        "user": user,
        "dashboard": dashboard,
    })


# ══════════════════════════════════════════════════════════════════════════════
# PR 2: Geräteregister
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/geraete", response_class=HTMLResponse)
def geraete_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASDevice
    from app.services.uas_compliance import device_einsatzbereit, wartung_faelligkeit

    devices = (
        db.query(UASDevice)
        .filter(UASDevice.org_id == user.org_id)
        .order_by(UASDevice.bezeichnung)
        .all()
    )
    rows = [
        {
            "device": d,
            "bereit": device_einsatzbereit(d),
            "wartung": wartung_faelligkeit(d),
        }
        for d in devices
    ]
    return templates.TemplateResponse(request, "uas/geraete_liste.html", {
        "user": user,
        "rows": rows,
    })


@router.get("/geraete/neu", response_class=HTMLResponse)
def geraet_neu_form(
    request: Request,
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASDeviceCeKlasse, UASDeviceUnterkategorie
    return templates.TemplateResponse(request, "uas/geraet_form.html", {
        "user": user,
        "device": None,
        "ce_klassen": list(UASDeviceCeKlasse),
        "unterkategorien": list(UASDeviceUnterkategorie),
    })


@router.post("/geraete/neu")
async def geraet_neu_save(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
    bezeichnung: str = Form(...),
    hersteller: str = Form(""),
    typ: str = Form(""),
    registriernummer: str = Form(""),
    ce_klasse: str = Form("C2"),
    unterkategorie: str = Form("A2"),
    mtom_g: str = Form(""),
    leergewicht_g: str = Form(""),
    hat_waermebildkamera: str = Form(""),
    allwettertauglich: str = Form(""),
    versicherung_polizze: str = Form(""),
    versicherung_gueltig_bis: str = Form(""),
    sybos_id: str = Form(""),
    beschaffungsdatum: str = Form(""),
    tauschintervall_jahre: str = Form("7"),
    notizen: str = Form(""),
):
    from app.models.uas import UASDevice

    def _parse_date(s: str) -> date | None:
        s = s.strip()
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None

    def _parse_int(s: str) -> int | None:
        s = s.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    dev = UASDevice(
        org_id=user.org_id,
        bezeichnung=bezeichnung.strip(),
        hersteller=hersteller.strip(),
        typ=typ.strip(),
        registriernummer=registriernummer.strip() or None,
        ce_klasse=ce_klasse,
        unterkategorie=unterkategorie,
        mtom_g=_parse_int(mtom_g),
        leergewicht_g=_parse_int(leergewicht_g),
        hat_waermebildkamera=hat_waermebildkamera in ("1", "on", "true"),
        allwettertauglich=allwettertauglich in ("1", "on", "true"),
        versicherung_polizze=versicherung_polizze.strip() or None,
        versicherung_gueltig_bis=_parse_date(versicherung_gueltig_bis),
        sybos_id=sybos_id.strip() or None,
        beschaffungsdatum=_parse_date(beschaffungsdatum),
        tauschintervall_jahre=int(tauschintervall_jahre) if tauschintervall_jahre.strip() else 7,
        notizen=notizen.strip() or None,
    )
    db.add(dev)
    db.commit()
    return RedirectResponse(f"/uas/geraete/{dev.id}", status_code=303)


@router.get("/geraete/{device_id}", response_class=HTMLResponse)
def geraet_detail(
    device_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASDevice
    from app.services.uas_compliance import device_einsatzbereit, wartung_faelligkeit

    device = db.query(UASDevice).filter(
        UASDevice.id == device_id, UASDevice.org_id == user.org_id
    ).first()
    if not device:
        raise HTTPException(404)

    wartungen = sorted(device.wartungen, key=lambda w: w.datum, reverse=True)
    return templates.TemplateResponse(request, "uas/geraet_detail.html", {
        "user": user,
        "device": device,
        "bereit": device_einsatzbereit(device),
        "wartung_ampel": wartung_faelligkeit(device),
        "wartungen": wartungen,
        "qr_url": f"{request.base_url}uas/geraete/qr/{device.qr_token}",
    })


@router.get("/geraete/{device_id}/bearbeiten", response_class=HTMLResponse)
def geraet_bearbeiten_form(
    device_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASDevice, UASDeviceCeKlasse, UASDeviceUnterkategorie

    device = db.query(UASDevice).filter(
        UASDevice.id == device_id, UASDevice.org_id == user.org_id
    ).first()
    if not device:
        raise HTTPException(404)

    return templates.TemplateResponse(request, "uas/geraet_form.html", {
        "user": user,
        "device": device,
        "ce_klassen": list(UASDeviceCeKlasse),
        "unterkategorien": list(UASDeviceUnterkategorie),
    })


@router.post("/geraete/{device_id}/bearbeiten")
async def geraet_bearbeiten_save(
    device_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
    bezeichnung: str = Form(...),
    hersteller: str = Form(""),
    typ: str = Form(""),
    registriernummer: str = Form(""),
    ce_klasse: str = Form("C2"),
    unterkategorie: str = Form("A2"),
    mtom_g: str = Form(""),
    leergewicht_g: str = Form(""),
    hat_waermebildkamera: str = Form(""),
    allwettertauglich: str = Form(""),
    versicherung_polizze: str = Form(""),
    versicherung_gueltig_bis: str = Form(""),
    sybos_id: str = Form(""),
    beschaffungsdatum: str = Form(""),
    tauschintervall_jahre: str = Form("7"),
    status: str = Form("aktiv"),
    notizen: str = Form(""),
):
    from app.models.uas import UASDevice

    def _d(s: str) -> date | None:
        s = s.strip()
        try:
            return date.fromisoformat(s) if s else None
        except ValueError:
            return None

    def _i(s: str) -> int | None:
        s = s.strip()
        try:
            return int(s) if s else None
        except ValueError:
            return None

    device = db.query(UASDevice).filter(
        UASDevice.id == device_id, UASDevice.org_id == user.org_id
    ).first()
    if not device:
        raise HTTPException(404)

    device.bezeichnung = bezeichnung.strip()
    device.hersteller = hersteller.strip()
    device.typ = typ.strip()
    device.registriernummer = registriernummer.strip() or None
    device.ce_klasse = ce_klasse
    device.unterkategorie = unterkategorie
    device.mtom_g = _i(mtom_g)
    device.leergewicht_g = _i(leergewicht_g)
    device.hat_waermebildkamera = hat_waermebildkamera in ("1", "on", "true")
    device.allwettertauglich = allwettertauglich in ("1", "on", "true")
    device.versicherung_polizze = versicherung_polizze.strip() or None
    device.versicherung_gueltig_bis = _d(versicherung_gueltig_bis)
    device.sybos_id = sybos_id.strip() or None
    device.beschaffungsdatum = _d(beschaffungsdatum)
    device.tauschintervall_jahre = int(tauschintervall_jahre) if tauschintervall_jahre.strip() else 7
    device.status = status
    device.notizen = notizen.strip() or None
    db.commit()
    return RedirectResponse(f"/uas/geraete/{device_id}", status_code=303)


@router.get("/geraete/qr/{qr_token}", response_class=HTMLResponse)
def geraet_per_qr(
    qr_token: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASDevice
    device = db.query(UASDevice).filter(
        UASDevice.qr_token == qr_token, UASDevice.org_id == user.org_id
    ).first()
    if not device:
        raise HTTPException(404)
    return RedirectResponse(f"/uas/geraete/{device.id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# PR 2: Wartungsbuch
# ══════════════════════════════════════════════════════════════════════════════

# Prüfpunkte-Vorlage laut Anhang 8.5
_PRUEFPUNKTE_MONATLICH = [
    {"key": "p01", "label": "Propeller / Rotoren: Beschädigungen, Risse, Verbiegungen?", "erledigt": False, "bemerkung": ""},
    {"key": "p02", "label": "Motoren: Lagerspiel, Geräusche, Verschmutzung", "erledigt": False, "bemerkung": ""},
    {"key": "p03", "label": "Rahmen / Arme: Risse, Brüche, Verbindungselemente fest?", "erledigt": False, "bemerkung": ""},
    {"key": "p04", "label": "Akkus: Aufblähung, Beschädigungen, Kapazitätsverlust?", "erledigt": False, "bemerkung": ""},
    {"key": "p05", "label": "Ladegeräte und Kabel: Zustand, Isolierung", "erledigt": False, "bemerkung": ""},
    {"key": "p06", "label": "Kamera / Gimbal: Befestigung, Funktion, Sauberkeit", "erledigt": False, "bemerkung": ""},
    {"key": "p07", "label": "Fernsteuerung: Akku, Display, Antennen, Verbindungstest", "erledigt": False, "bemerkung": ""},
    {"key": "p08", "label": "Failsafe-Einstellungen (RTH, Lost-Link) geprüft?", "erledigt": False, "bemerkung": ""},
    {"key": "p09", "label": "Firmware / Software: aktueller Stand?", "erledigt": False, "bemerkung": ""},
    {"key": "p10", "label": "Lagerung: Transportkoffer, Temperaturbedingungen OK?", "erledigt": False, "bemerkung": ""},
]

_PRUEFPUNKTE_JAHRESSERVICE = _PRUEFPUNKTE_MONATLICH + [
    {"key": "j01", "label": "Herstellerservice / Inspektion durchgeführt?", "erledigt": False, "bemerkung": ""},
    {"key": "j02", "label": "Motortausch / Propellerersatz nach Stundenplan?", "erledigt": False, "bemerkung": ""},
    {"key": "j03", "label": "Registrierung und Kennzeichnung aktuell?", "erledigt": False, "bemerkung": ""},
    {"key": "j04", "label": "Versicherungsnachweis vorhanden und gültig?", "erledigt": False, "bemerkung": ""},
]


@router.get("/geraete/{device_id}/wartung/neu", response_class=HTMLResponse)
def wartung_neu_form(
    device_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
    art: str = "monatliche_sichtkontrolle",
):
    from app.models.uas import UASDevice, UASWartungArt

    device = db.query(UASDevice).filter(
        UASDevice.id == device_id, UASDevice.org_id == user.org_id
    ).first()
    if not device:
        raise HTTPException(404)

    punkte = _PRUEFPUNKTE_JAHRESSERVICE if art == "jahresservice" else _PRUEFPUNKTE_MONATLICH
    return templates.TemplateResponse(request, "uas/wartung_form.html", {
        "user": user,
        "device": device,
        "art": art,
        "wartung_arten": list(UASWartungArt),
        "pruefpunkte": punkte,
        "heute": date.today().isoformat(),
    })


@router.post("/geraete/{device_id}/wartung/neu")
async def wartung_neu_save(
    device_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
):
    from datetime import timedelta

    from app.models.uas import UASDevice, UASWartung

    device = db.query(UASDevice).filter(
        UASDevice.id == device_id, UASDevice.org_id == user.org_id
    ).first()
    if not device:
        raise HTTPException(404)

    form = await request.form()
    art = str(form.get("art", "monatliche_sichtkontrolle"))
    datum_raw = str(form.get("datum", date.today().isoformat()))
    pruefer = str(form.get("pruefer", "")).strip()
    ergebnis = str(form.get("ergebnis", "io"))
    bemerkung = str(form.get("bemerkung", "")).strip()

    try:
        datum = date.fromisoformat(datum_raw)
    except ValueError:
        datum = date.today()

    # Prüfpunkte aus Formular sammeln
    vorlage = _PRUEFPUNKTE_JAHRESSERVICE if art == "jahresservice" else _PRUEFPUNKTE_MONATLICH
    punkte = []
    for p in vorlage:
        punkte.append({
            "key": p["key"],
            "label": p["label"],
            "erledigt": str(form.get(f"punkt_{p['key']}", "")) in ("on", "1"),
            "bemerkung": str(form.get(f"bemerkung_{p['key']}", "")).strip(),
        })

    # Nächste Fälligkeit berechnen
    if art == "monatliche_sichtkontrolle":
        naechste = datum + timedelta(days=30)
    elif art == "jahresservice":
        naechste = datum + timedelta(days=365)
    else:
        naechste = None

    wartung = UASWartung(
        org_id=user.org_id,
        device_id=device_id,
        datum=datum,
        art=art,
        pruefpunkte=json.dumps(punkte, ensure_ascii=False),
        pruefer=pruefer or None,
        ergebnis=ergebnis,
        bemerkung=bemerkung or None,
        naechste_faellig=naechste,
    )
    db.add(wartung)
    db.commit()
    return RedirectResponse(f"/uas/geraete/{device_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# PR 2: Pilotenverwaltung
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/piloten", response_class=HTMLResponse)
def piloten_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASPilot
    from app.services.uas_compliance import pilot_freigabe_status

    piloten = (
        db.query(UASPilot)
        .filter(UASPilot.org_id == user.org_id)
        .order_by(UASPilot.nachname, UASPilot.vorname)
        .all()
    )
    rows = [{"pilot": p, "freigabe": pilot_freigabe_status(p, db)} for p in piloten]
    return templates.TemplateResponse(request, "uas/piloten_liste.html", {
        "user": user,
        "rows": rows,
    })


@router.get("/piloten/neu", response_class=HTMLResponse)
def pilot_neu_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.master import Member

    members = (
        db.query(Member)
        .filter(Member.org_id == user.org_id, Member.active == True)  # noqa: E712
        .order_by(Member.lastname, Member.firstname)
        .all()
    )
    return templates.TemplateResponse(request, "uas/pilot_form.html", {
        "user": user,
        "pilot": None,
        "members": members,
    })


@router.post("/piloten/neu")
async def pilot_neu_save(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
    nachname: str = Form(...),
    vorname: str = Form(...),
    geburtsdatum: str = Form(""),
    person_id: str = Form(""),
    ist_truppfuehrer: str = Form(""),
    a1a3_id: str = Form(""),
    a1a3_gueltig_bis: str = Form(""),
    a2_id: str = Form(""),
    a2_gueltig_bis: str = Form(""),
    bos_stufe: str = Form("0"),
    bos_ausbildung_datum: str = Form(""),
    bos_rezert_bis: str = Form(""),
    lfv_zugelassen: str = Form(""),
    qualifikationen_teamleiter: str = Form(""),
    qualifikationen_pilot: str = Form(""),
    qualifikationen_operator: str = Form(""),
    notizen: str = Form(""),
):
    from app.models.uas import UASPilot

    def _d(s: str) -> date | None:
        try:
            return date.fromisoformat(s.strip()) if s.strip() else None
        except ValueError:
            return None

    qualifikationen = json.dumps({
        "teamleiter": qualifikationen_teamleiter in ("1", "on"),
        "pilot": qualifikationen_pilot in ("1", "on"),
        "operator": qualifikationen_operator in ("1", "on"),
    })

    pilot = UASPilot(
        org_id=user.org_id,
        person_id=int(person_id) if person_id.strip() else None,
        nachname=nachname.strip(),
        vorname=vorname.strip(),
        geburtsdatum=_d(geburtsdatum),
        ist_truppfuehrer=ist_truppfuehrer in ("1", "on"),
        a1a3_id=a1a3_id.strip() or None,
        a1a3_gueltig_bis=_d(a1a3_gueltig_bis),
        a2_id=a2_id.strip() or None,
        a2_gueltig_bis=_d(a2_gueltig_bis),
        bos_stufe=bos_stufe,
        bos_ausbildung_datum=_d(bos_ausbildung_datum),
        bos_rezert_bis=_d(bos_rezert_bis),
        lfv_zugelassen=lfv_zugelassen in ("1", "on"),
        qualifikationen=qualifikationen,
        aktiv=True,
        notizen=notizen.strip() or None,
    )
    db.add(pilot)
    db.commit()
    return RedirectResponse(f"/uas/piloten/{pilot.id}", status_code=303)


@router.get("/piloten/{pilot_id}", response_class=HTMLResponse)
def pilot_detail(
    pilot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASFlugbewegung, UASPilot
    from app.services.uas_compliance import pilot_freigabe_status

    pilot = db.query(UASPilot).filter(
        UASPilot.id == pilot_id, UASPilot.org_id == user.org_id
    ).first()
    if not pilot:
        raise HTTPException(404)

    bewegungen = (
        db.query(UASFlugbewegung)
        .filter(UASFlugbewegung.pilot_id == pilot_id)
        .order_by(UASFlugbewegung.datum.desc())
        .limit(20)
        .all()
    )
    freigabe = pilot_freigabe_status(pilot, db)
    qualifikationen = {}
    if pilot.qualifikationen:
        try:
            qualifikationen = json.loads(pilot.qualifikationen)
        except Exception:
            pass

    return templates.TemplateResponse(request, "uas/pilot_detail.html", {
        "user": user,
        "pilot": pilot,
        "freigabe": freigabe,
        "qualifikationen": qualifikationen,
        "bewegungen": bewegungen,
    })


@router.get("/piloten/{pilot_id}/bearbeiten", response_class=HTMLResponse)
def pilot_bearbeiten_form(
    pilot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.master import Member
    from app.models.uas import UASPilot

    pilot = db.query(UASPilot).filter(
        UASPilot.id == pilot_id, UASPilot.org_id == user.org_id
    ).first()
    if not pilot:
        raise HTTPException(404)

    members = (
        db.query(Member)
        .filter(Member.org_id == user.org_id, Member.active == True)  # noqa: E712
        .order_by(Member.lastname, Member.firstname)
        .all()
    )
    qualifikationen = {}
    if pilot.qualifikationen:
        try:
            qualifikationen = json.loads(pilot.qualifikationen)
        except Exception:
            pass

    return templates.TemplateResponse(request, "uas/pilot_form.html", {
        "user": user,
        "pilot": pilot,
        "members": members,
        "qualifikationen": qualifikationen,
    })


@router.post("/piloten/{pilot_id}/bearbeiten")
async def pilot_bearbeiten_save(
    pilot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
    nachname: str = Form(...),
    vorname: str = Form(...),
    geburtsdatum: str = Form(""),
    person_id: str = Form(""),
    ist_truppfuehrer: str = Form(""),
    a1a3_id: str = Form(""),
    a1a3_gueltig_bis: str = Form(""),
    a2_id: str = Form(""),
    a2_gueltig_bis: str = Form(""),
    bos_stufe: str = Form("0"),
    bos_ausbildung_datum: str = Form(""),
    bos_rezert_bis: str = Form(""),
    lfv_zugelassen: str = Form(""),
    qualifikationen_teamleiter: str = Form(""),
    qualifikationen_pilot: str = Form(""),
    qualifikationen_operator: str = Form(""),
    aktiv: str = Form(""),
    notizen: str = Form(""),
):
    from app.models.uas import UASPilot

    def _d(s: str) -> date | None:
        try:
            return date.fromisoformat(s.strip()) if s.strip() else None
        except ValueError:
            return None

    pilot = db.query(UASPilot).filter(
        UASPilot.id == pilot_id, UASPilot.org_id == user.org_id
    ).first()
    if not pilot:
        raise HTTPException(404)

    pilot.person_id = int(person_id) if person_id.strip() else None
    pilot.nachname = nachname.strip()
    pilot.vorname = vorname.strip()
    pilot.geburtsdatum = _d(geburtsdatum)
    pilot.ist_truppfuehrer = ist_truppfuehrer in ("1", "on")
    pilot.a1a3_id = a1a3_id.strip() or None
    pilot.a1a3_gueltig_bis = _d(a1a3_gueltig_bis)
    pilot.a2_id = a2_id.strip() or None
    pilot.a2_gueltig_bis = _d(a2_gueltig_bis)
    pilot.bos_stufe = bos_stufe
    pilot.bos_ausbildung_datum = _d(bos_ausbildung_datum)
    pilot.bos_rezert_bis = _d(bos_rezert_bis)
    pilot.lfv_zugelassen = lfv_zugelassen in ("1", "on")
    pilot.qualifikationen = json.dumps({
        "teamleiter": qualifikationen_teamleiter in ("1", "on"),
        "pilot": qualifikationen_pilot in ("1", "on"),
        "operator": qualifikationen_operator in ("1", "on"),
    })
    pilot.aktiv = aktiv in ("1", "on")
    pilot.notizen = notizen.strip() or None
    db.commit()
    return RedirectResponse(f"/uas/piloten/{pilot_id}", status_code=303)


# ── Flugbewegung manuell eintragen ────────────────────────────────────────────

@router.post("/piloten/{pilot_id}/flugbewegung")
async def flugbewegung_eintragen(
    pilot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin", "admin")),
    _guard: None = Depends(require_uas_enabled),
    datum: str = Form(...),
    dauer_min: str = Form(""),
    art: str = Form("ausbildung"),
    device_id: str = Form(""),
):
    from app.models.uas import UASFlugbewegung, UASPilot

    pilot = db.query(UASPilot).filter(
        UASPilot.id == pilot_id, UASPilot.org_id == user.org_id
    ).first()
    if not pilot:
        raise HTTPException(404)

    bewegung = UASFlugbewegung(
        org_id=user.org_id,
        pilot_id=pilot_id,
        device_id=int(device_id) if device_id.strip() else None,
        datum=date.fromisoformat(datum),
        dauer_min=int(dauer_min) if dauer_min.strip() else None,
        art=art,
    )
    db.add(bewegung)
    db.commit()
    return RedirectResponse(f"/uas/piloten/{pilot_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# PR 3: Drohnen-Einsatz (aus Incident heraus starten)
# ══════════════════════════════════════════════════════════════════════════════

_MINDESTBESETZUNG_EINSATZMITTEL = {
    "pilot",
    "luftraumbeobachter",
}
_MINDESTBESETZUNG_EINHEIT = {
    "teamleiter",
    "pilot",
}  # +1 beliebige Unterstützung (Operator/Luftraum/Versorger/Bildschirm/Gerätewart)


def _validate_mindestbesetzung(rollen: list) -> list[str]:
    """RL 6.1 Mindestbesetzung prüfen. Gibt Liste fehlender Rollen zurück."""
    besetzt = {r.rolle for r in rollen}
    fehlt = []
    if "pilot" not in besetzt:
        fehlt.append("Pilot fehlt (RL 6.1)")
    if "luftraumbeobachter" not in besetzt:
        fehlt.append("Luftraumbeobachter fehlt (RL 6.1)")
    # Mindestens 2 Personen gesamt
    if len(rollen) < 2:
        fehlt.append("Mindestens 2 Personen erforderlich (RL 6.1 Einsatzmittel)")
    return fehlt


@router.get("/incidents/{incident_id}/einsatz/starten", response_class=HTMLResponse)
def einsatz_starten_form(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.incident import Incident
    from app.models.uas import UASEinsatz

    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(404)

    existing = db.query(UASEinsatz).filter(UASEinsatz.incident_id == incident_id).first()
    if existing:
        return RedirectResponse(f"/uas/einsatz/{existing.id}", status_code=303)

    return templates.TemplateResponse(request, "uas/einsatz_starten.html", {
        "user": user,
        "incident": incident,
        "heute": date.today().isoformat(),
    })


@router.post("/incidents/{incident_id}/einsatz/starten")
async def einsatz_starten_save(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    tetra_rufname: str = Form(""),
    betreibernummer: str = Form(""),
    einsatzgrund: str = Form(""),
    gesamteinsatzleiter: str = Form(""),
    datenschutz_bestaetigt: str = Form(""),
    alarmierung_at: str = Form(""),
):
    from datetime import UTC, datetime

    from app.models.uas import UASEinsatz, UASEinsatzStatus

    def _dt(s: str):
        s = s.strip()
        if not s:
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(s).replace(tzinfo=UTC)
        except ValueError:
            return datetime.now(UTC)

    einsatz = UASEinsatz(
        org_id=user.org_id,
        incident_id=incident_id,
        status=UASEinsatzStatus.alarmiert.value,
        alarmierung_at=_dt(alarmierung_at),
        tetra_rufname=tetra_rufname.strip() or None,
        betreibernummer=betreibernummer.strip() or None,
        einsatzgrund=einsatzgrund.strip() or None,
        gesamteinsatzleiter=gesamteinsatzleiter.strip() or None,
        datenschutz_bestaetigt=datenschutz_bestaetigt in ("1", "on"),
    )
    db.add(einsatz)
    db.commit()
    return RedirectResponse(f"/uas/einsatz/{einsatz.id}", status_code=303)


@router.get("/einsatz/{einsatz_id}", response_class=HTMLResponse)
def einsatz_detail(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.incident import Incident
    from app.models.uas import UASDevice, UASEinsatz, UASPilot
    from app.services.uas_compliance import pilot_freigabe_status

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    incident = db.query(Incident).filter(Incident.id == einsatz.incident_id).first()

    # Verfügbare Piloten (für Dropdown)
    alle_piloten = (
        db.query(UASPilot)
        .filter(UASPilot.org_id == user.org_id, UASPilot.aktiv == True)  # noqa: E712
        .order_by(UASPilot.nachname)
        .all()
    )
    piloten_mit_status = [
        {"pilot": p, "freigabe": pilot_freigabe_status(p, db)}
        for p in alle_piloten
    ]

    # Verfügbare Geräte
    alle_geraete = (
        db.query(UASDevice)
        .filter(UASDevice.org_id == user.org_id, UASDevice.status == "aktiv")
        .order_by(UASDevice.bezeichnung)
        .all()
    )

    mindest_fehlt = _validate_mindestbesetzung(einsatz.rollen)

    komm = {}
    if einsatz.kommunikationsmatrix:
        try:
            komm = json.loads(einsatz.kommunikationsmatrix)
        except Exception:
            pass

    risiko = {}
    if einsatz.risikobewertung:
        try:
            risiko = json.loads(einsatz.risikobewertung)
        except Exception:
            pass

    from app.models.uas import UASEinsatzRolle
    return templates.TemplateResponse(request, "uas/einsatz_detail.html", {
        "user": user,
        "einsatz": einsatz,
        "incident": incident,
        "rollen": einsatz.rollen,
        "piloten_mit_status": piloten_mit_status,
        "alle_geraete": alle_geraete,
        "mindest_fehlt": mindest_fehlt,
        "komm": komm,
        "risiko": risiko,
        "alle_rollen": list(UASEinsatzRolle),
    })


@router.post("/einsatz/{einsatz_id}/status")
async def einsatz_status_aendern(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    neuer_status: str = Form(...),
):
    from datetime import UTC, datetime

    from app.models.uas import UASEinsatz, UASEinsatzStatus

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    jetzt = datetime.now(UTC)
    einsatz.status = neuer_status

    if neuer_status == UASEinsatzStatus.angemeldet.value:
        einsatz.anmeldung_el_at = jetzt
    elif neuer_status == UASEinsatzStatus.abgemeldet.value:
        einsatz.abmeldung_el_at = jetzt
    elif neuer_status == UASEinsatzStatus.im_einsatz.value:
        fehlt = _validate_mindestbesetzung(einsatz.rollen)
        if fehlt:
            raise HTTPException(400, detail="; ".join(fehlt))

    db.commit()
    return RedirectResponse(f"/uas/einsatz/{einsatz_id}", status_code=303)


@router.post("/einsatz/{einsatz_id}/rolle/hinzufuegen")
async def rolle_hinzufuegen(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    rolle: str = Form(...),
    pilot_id: str = Form(""),
    helfer_name: str = Form(""),
    override_begruendung: str = Form(""),
):
    from app.models.uas import UASEinsatz, UASEinsatzRolleEintrag, UASPilot
    from app.services.uas_compliance import pilot_freigabe_status

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    pid = int(pilot_id) if pilot_id.strip() else None

    # Gesperrter Pilot ohne Override-Begründung blockieren
    if pid:
        pilot = db.query(UASPilot).filter(UASPilot.id == pid).first()
        if pilot:
            freigabe = pilot_freigabe_status(pilot, db)
            if freigabe.status == "rot" and not override_begruendung.strip():
                raise HTTPException(400, detail="Pilot gesperrt – Override-Begründung erforderlich")

    eintrag = UASEinsatzRolleEintrag(
        org_id=user.org_id,
        uas_einsatz_id=einsatz_id,
        pilot_id=pid,
        helfer_name=helfer_name.strip() or None,
        rolle=rolle,
        override_begruendung=override_begruendung.strip() or None,
    )
    db.add(eintrag)
    db.commit()
    return RedirectResponse(f"/uas/einsatz/{einsatz_id}", status_code=303)


@router.post("/einsatz/{einsatz_id}/rolle/{rolle_id}/loeschen")
async def rolle_loeschen(
    einsatz_id: int,
    rolle_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASEinsatzRolleEintrag

    eintrag = db.query(UASEinsatzRolleEintrag).filter(
        UASEinsatzRolleEintrag.id == rolle_id,
        UASEinsatzRolleEintrag.uas_einsatz_id == einsatz_id,
        UASEinsatzRolleEintrag.org_id == user.org_id,
    ).first()
    if eintrag:
        db.delete(eintrag)
        db.commit()
    return RedirectResponse(f"/uas/einsatz/{einsatz_id}", status_code=303)


@router.post("/einsatz/{einsatz_id}/kommunikationsmatrix")
async def kommunikationsmatrix_speichern(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    el_sprechgruppe: str = Form(""),
    flug_sprechgruppe: str = Form(""),
    tmo_dmo: str = Form("TMO"),
    luftfahrt_abstimmung: str = Form(""),
    notizen: str = Form(""),
):
    from app.models.uas import UASEinsatz

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    einsatz.kommunikationsmatrix = json.dumps({
        "el_sprechgruppe": el_sprechgruppe.strip(),
        "flug_sprechgruppe": flug_sprechgruppe.strip(),
        "tmo_dmo": tmo_dmo,
        "luftfahrt_abstimmung": luftfahrt_abstimmung.strip(),
        "notizen": notizen.strip(),
    }, ensure_ascii=False)
    db.commit()
    return RedirectResponse(f"/uas/einsatz/{einsatz_id}", status_code=303)


@router.post("/einsatz/{einsatz_id}/risikobewertung")
async def risikobewertung_speichern(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    gelande: str = Form(""),
    menschen: str = Form(""),
    luftraum: str = Form(""),
    wetter: str = Form(""),
    sonstiges: str = Form(""),
    gesamt: str = Form(""),
):
    from app.models.uas import UASEinsatz

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    einsatz.risikobewertung = json.dumps({
        "gelande": gelande.strip(),
        "menschen": menschen.strip(),
        "luftraum": luftraum.strip(),
        "wetter": wetter.strip(),
        "sonstiges": sonstiges.strip(),
        "gesamt": gesamt.strip(),
    }, ensure_ascii=False)
    db.commit()
    return RedirectResponse(f"/uas/einsatz/{einsatz_id}", status_code=303)


@router.get("/einsatz/{einsatz_id}/eintreffmeldung", response_class=HTMLResponse)
def eintreffmeldung_form(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.incident import Incident
    from app.models.uas import UASDevice, UASEinsatz

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    incident = db.query(Incident).filter(Incident.id == einsatz.incident_id).first()

    alle_geraete = (
        db.query(UASDevice)
        .filter(UASDevice.org_id == user.org_id, UASDevice.status == "aktiv")
        .order_by(UASDevice.bezeichnung)
        .all()
    )

    return templates.TemplateResponse(request, "uas/eintreffmeldung.html", {
        "user": user,
        "einsatz": einsatz,
        "incident": incident,
        "rollen": einsatz.rollen,
        "alle_geraete": alle_geraete,
        "jetzt": date.today().isoformat(),
    })


# ══════════════════════════════════════════════════════════════════════════════
# PR 4: Flugbuch & Checklisten
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/einsatz/{einsatz_id}/flug/neu", response_class=HTMLResponse)
def flug_neu_form(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import (
        UASDevice, UASEinsatz, UASFlugDurchfuehrung, UASFlugGrundlage, UASPilot,
    )
    from app.services.uas_compliance import pilot_freigabe_status

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    piloten = db.query(UASPilot).filter(
        UASPilot.org_id == user.org_id, UASPilot.aktiv == True  # noqa: E712
    ).order_by(UASPilot.nachname).all()
    geraete = db.query(UASDevice).filter(
        UASDevice.org_id == user.org_id, UASDevice.status == "aktiv"
    ).order_by(UASDevice.bezeichnung).all()

    return templates.TemplateResponse(request, "uas/flug_form.html", {
        "user": user,
        "einsatz": einsatz,
        "piloten": [{"pilot": p, "freigabe": pilot_freigabe_status(p, db)} for p in piloten],
        "geraete": geraete,
        "durchfuehrungen": list(UASFlugDurchfuehrung),
        "grundlagen": list(UASFlugGrundlage),
        "heute": date.today().isoformat(),
    })


@router.post("/einsatz/{einsatz_id}/flug/neu")
async def flug_neu_save(
    einsatz_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    datum: str = Form(...),
    pilot_id: str = Form(""),
    device_id: str = Form(""),
    start_ort: str = Form(""),
    landung_ort: str = Form(""),
    durchfuehrung: str = Form("vlos"),
    grundlage: str = Form("open_a1"),
    bescheid_nr: str = Form(""),
    geplante_flughoehe_m: str = Form(""),
    nachtbetrieb: str = Form(""),
    gesamteinsatzleiter: str = Form(""),
    einsatzleiter_drohne: str = Form(""),
    bemerkungen: str = Form(""),
):
    from app.models.uas import UASEinsatz, UASFlug, UASFlugStatus
    from app.services.uas_flugbuch import berechne_flugsicherheitswerte

    einsatz = db.query(UASEinsatz).filter(
        UASEinsatz.id == einsatz_id, UASEinsatz.org_id == user.org_id
    ).first()
    if not einsatz:
        raise HTTPException(404)

    # Laufende Nummer
    from app.models.uas import UASFlug as _UASFlug
    lfd = (db.query(_UASFlug).filter(_UASFlug.uas_einsatz_id == einsatz_id).count() or 0) + 1

    hoehe = float(geplante_flughoehe_m) if geplante_flughoehe_m.strip() else None
    sicherheit = berechne_flugsicherheitswerte(hoehe) if hoehe else {}

    # BVLOS erfordert Bescheid
    if durchfuehrung == "bvlos" and not bescheid_nr.strip():
        raise HTTPException(400, detail="BVLOS erfordert Bescheid-Nr. (RL 4.4)")

    # Flughöhe > 120 m → Warnung (nur im UI, kein hard-Block)

    flug = UASFlug(
        org_id=user.org_id,
        uas_einsatz_id=einsatz_id,
        lfd_nr=lfd,
        datum=date.fromisoformat(datum),
        pilot_id=int(pilot_id) if pilot_id.strip() else None,
        device_id=int(device_id) if device_id.strip() else None,
        start_ort=start_ort.strip() or None,
        landung_ort=landung_ort.strip() or None,
        durchfuehrung=durchfuehrung,
        grundlage=grundlage,
        bescheid_nr=bescheid_nr.strip() or None,
        geplante_flughoehe_m=hoehe,
        contingency_volume_m=sicherheit.get("contingency_volume_m"),
        ground_risk_buffer_m=sicherheit.get("ground_risk_buffer_m"),
        abstand_menschenansammlung_m=sicherheit.get("abstand_menschenansammlung_m"),
        nachtbetrieb=nachtbetrieb in ("1", "on"),
        gesamteinsatzleiter=gesamteinsatzleiter.strip() or None,
        einsatzleiter_drohne=einsatzleiter_drohne.strip() or None,
        bemerkungen=bemerkungen.strip() or None,
        status=UASFlugStatus.offen.value,
    )
    db.add(flug)
    db.flush()

    # Vorflug-Checkliste automatisch anlegen
    from app.models.uas import UASCheckliste
    from app.services.uas_flugbuch import CHECKLISTE_VORFLUG
    vfl = UASCheckliste(
        org_id=user.org_id,
        uas_flug_id=flug.id,
        typ="vorflug",
        punkte=json.dumps(CHECKLISTE_VORFLUG, ensure_ascii=False),
    )
    db.add(vfl)
    db.commit()
    return RedirectResponse(f"/uas/flug/{flug.id}", status_code=303)


@router.get("/flug/{flug_id}", response_class=HTMLResponse)
def flug_detail(
    flug_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASFlug
    from app.services.uas_flugbuch import berechne_flugsicherheitswerte, pruefe_1zu1_regel

    flug = db.query(UASFlug).filter(
        UASFlug.id == flug_id, UASFlug.org_id == user.org_id
    ).first()
    if not flug:
        raise HTTPException(404)

    sicherheit = {}
    konform = None
    if flug.geplante_flughoehe_m:
        sicherheit = berechne_flugsicherheitswerte(flug.geplante_flughoehe_m)
        konform = pruefe_1zu1_regel(
            flug.geplante_flughoehe_m,
            flug.abstand_menschenansammlung_m,
        )

    checklisten = sorted(flug.checklisten, key=lambda c: c.created_at)
    checklisten_parsed = []
    for cl in checklisten:
        punkte = []
        if cl.punkte:
            try:
                punkte = json.loads(cl.punkte)
            except Exception:
                pass
        checklisten_parsed.append({"checkliste": cl, "punkte": punkte})

    return templates.TemplateResponse(request, "uas/flug_detail.html", {
        "user": user,
        "flug": flug,
        "sicherheit": sicherheit,
        "flughoehe_konform": konform,
        "checklisten": checklisten_parsed,
    })


@router.post("/flug/{flug_id}/abschliessen")
async def flug_abschliessen(
    flug_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    landung_at: str = Form(""),
    start_at: str = Form(""),
):
    from datetime import UTC, datetime

    from app.models.uas import UASFlug, UASFlugStatus
    from app.services.uas_flugbuch import berechne_dauer_min, inhalt_hash

    flug = db.query(UASFlug).filter(
        UASFlug.id == flug_id, UASFlug.org_id == user.org_id
    ).first()
    if not flug:
        raise HTTPException(404)
    if flug.status == UASFlugStatus.abgeschlossen.value:
        raise HTTPException(400, detail="Flug bereits abgeschlossen (Append-Only)")

    def _dt(s: str) -> datetime | None:
        s = s.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    if start_at:
        flug.start_at = _dt(start_at)
    if landung_at:
        flug.landung_at = _dt(landung_at)

    flug.dauer_min = berechne_dauer_min(flug.start_at, flug.landung_at)
    flug.status = UASFlugStatus.abgeschlossen.value

    # Audit-Hash setzen (Verbesserung 9)
    data = {
        "id": flug.id, "datum": str(flug.datum), "pilot_id": flug.pilot_id,
        "device_id": flug.device_id, "dauer_min": flug.dauer_min,
        "durchfuehrung": flug.durchfuehrung, "grundlage": flug.grundlage,
    }
    flug.inhalt_hash = inhalt_hash(data)

    db.commit()

    # Automatische Flugbewegung für Currency
    if flug.pilot_id:
        from app.models.uas import UASFlugbewegung
        bewegung = UASFlugbewegung(
            org_id=user.org_id,
            pilot_id=flug.pilot_id,
            device_id=flug.device_id,
            datum=flug.datum,
            dauer_min=flug.dauer_min,
            art="einsatz",
            uas_flug_id=flug.id,
        )
        db.add(bewegung)
        db.commit()

    return RedirectResponse(f"/uas/flug/{flug_id}", status_code=303)


@router.post("/flug/{flug_id}/checkliste/{cl_id}/speichern")
async def checkliste_speichern(
    flug_id: int,
    cl_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    erledigt_von_pilot: str = Form(""),
    erledigt_von_zweitperson: str = Form(""),
):
    from datetime import UTC, datetime

    from app.models.uas import UASCheckliste

    cl = db.query(UASCheckliste).filter(
        UASCheckliste.id == cl_id,
        UASCheckliste.uas_flug_id == flug_id,
        UASCheckliste.org_id == user.org_id,
    ).first()
    if not cl:
        raise HTTPException(404)

    form = await request.form()
    punkte = []
    if cl.punkte:
        try:
            punkte = json.loads(cl.punkte)
        except Exception:
            pass

    for p in punkte:
        p["erledigt"] = str(form.get(f"punkt_{p['key']}", "")) in ("on", "1")
        p["bemerkung"] = str(form.get(f"bemerkung_{p['key']}", "")).strip()

    cl.punkte = json.dumps(punkte, ensure_ascii=False)
    cl.erledigt_von_pilot = erledigt_von_pilot.strip() or None
    cl.erledigt_von_zweitperson = erledigt_von_zweitperson.strip() or None

    if all(p["erledigt"] for p in punkte):
        cl.abgeschlossen_at = datetime.now(UTC)

    db.commit()
    return RedirectResponse(f"/uas/flug/{flug_id}", status_code=303)


@router.post("/flug/{flug_id}/nachflug-checkliste")
async def nachflug_checkliste_anlegen(
    flug_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASCheckliste, UASFlug
    from app.services.uas_flugbuch import CHECKLISTE_NACHFLUG

    flug = db.query(UASFlug).filter(
        UASFlug.id == flug_id, UASFlug.org_id == user.org_id
    ).first()
    if not flug:
        raise HTTPException(404)

    existing = next(
        (c for c in flug.checklisten if c.typ == "nachflug"), None
    )
    if not existing:
        cl = UASCheckliste(
            org_id=user.org_id,
            uas_flug_id=flug_id,
            typ="nachflug",
            punkte=json.dumps(CHECKLISTE_NACHFLUG, ensure_ascii=False),
        )
        db.add(cl)
        db.commit()
    return RedirectResponse(f"/uas/flug/{flug_id}", status_code=303)


# ══════════════════════════════════════════════════════════════════════════════
# PR 5: Notfall- & Unfall-Workflow (ACG-Meldung)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/notfallcheckliste", response_class=HTMLResponse)
def notfallcheckliste_schnell(
    request: Request,
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    """Schnellabruf-Panel – offline-fähig, in ≤1 Tap aus aktivem Flug."""
    from app.services.uas_ereignis import NOTFALLCHECKLISTE
    return templates.TemplateResponse(request, "uas/notfallcheckliste.html", {
        "user": user,
        "vorfaelle": NOTFALLCHECKLISTE,
    })


@router.get("/flug/{flug_id}/ereignis/neu", response_class=HTMLResponse)
def ereignis_neu_form(
    flug_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASEreignisTyp, UASFlug
    from app.services.uas_ereignis import NOTFALLCHECKLISTE

    flug = db.query(UASFlug).filter(
        UASFlug.id == flug_id, UASFlug.org_id == user.org_id
    ).first()
    if not flug:
        raise HTTPException(404)

    return templates.TemplateResponse(request, "uas/ereignis_form.html", {
        "user": user,
        "flug": flug,
        "typen": list(UASEreignisTyp),
        "vorfaelle": NOTFALLCHECKLISTE,
        "heute": date.today().isoformat(),
    })


@router.post("/flug/{flug_id}/ereignis/neu")
async def ereignis_neu_save(
    flug_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    typ: str = Form("stoerung"),
    kategorie: str = Form(""),
    datum_lokal: str = Form(""),
    zeit_lokal: str = Form(""),
    ort_icao: str = Form(""),
    klassifizierung: str = Form(""),
    beschreibung: str = Form(""),
):
    from app.models.uas import UASEreignis, UASFlug
    from app.services.uas_ereignis import lokal_zu_utc

    flug = db.query(UASFlug).filter(
        UASFlug.id == flug_id, UASFlug.org_id == user.org_id
    ).first()
    if not flug:
        raise HTTPException(404)

    datum_l = None
    if datum_lokal.strip():
        try:
            datum_l = date.fromisoformat(datum_lokal.strip())
        except ValueError:
            datum_l = date.today()
    else:
        datum_l = date.today()

    datum_utc_val = None
    zeit_utc_val = None
    if datum_l and zeit_lokal.strip():
        datum_utc_val, zeit_utc_val = lokal_zu_utc(datum_l, zeit_lokal.strip())

    ereignis = UASEreignis(
        org_id=user.org_id,
        uas_flug_id=flug_id,
        typ=typ,
        kategorie=kategorie.strip() or None,
        datum_lokal=datum_l,
        zeit_lokal=zeit_lokal.strip() or None,
        datum_utc=datum_utc_val,
        zeit_utc=zeit_utc_val,
        ort_icao=ort_icao.strip() or None,
        klassifizierung=klassifizierung.strip() or None,
        beschreibung=beschreibung.strip() or None,
        gemeldet_an=json.dumps({
            "stuetzpunktleiter": None,
            "behoerde_acg": None,
            "einsatzleitung": None,
            "lfv": None,
        }),
    )
    db.add(ereignis)
    if typ == "unfall":
        flug.unfall = True
    db.commit()
    return RedirectResponse(f"/uas/ereignis/{ereignis.id}", status_code=303)


@router.get("/ereignis/{ereignis_id}", response_class=HTMLResponse)
def ereignis_detail(
    ereignis_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
):
    from app.models.uas import UASEreignis

    ereignis = db.query(UASEreignis).filter(
        UASEreignis.id == ereignis_id, UASEreignis.org_id == user.org_id
    ).first()
    if not ereignis:
        raise HTTPException(404)

    meldekette = {}
    if ereignis.gemeldet_an:
        try:
            meldekette = json.loads(ereignis.gemeldet_an)
        except Exception:
            pass

    return templates.TemplateResponse(request, "uas/ereignis_detail.html", {
        "user": user,
        "ereignis": ereignis,
        "meldekette": meldekette,
    })


@router.post("/ereignis/{ereignis_id}/meldekette")
async def meldekette_update(
    ereignis_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("recorder")),
    _guard: None = Depends(require_uas_enabled),
    stufe: str = Form(...),
):
    from datetime import UTC, datetime

    from app.models.uas import UASEreignis

    ereignis = db.query(UASEreignis).filter(
        UASEreignis.id == ereignis_id, UASEreignis.org_id == user.org_id
    ).first()
    if not ereignis:
        raise HTTPException(404)

    meldekette = {}
    if ereignis.gemeldet_an:
        try:
            meldekette = json.loads(ereignis.gemeldet_an)
        except Exception:
            pass

    meldekette[stufe] = datetime.now(UTC).isoformat()
    ereignis.gemeldet_an = json.dumps(meldekette, ensure_ascii=False)
    db.commit()
    return RedirectResponse(f"/uas/ereignis/{ereignis_id}", status_code=303)
