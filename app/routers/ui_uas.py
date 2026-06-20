"""UAS-Modul Router (PR 0–2).

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
