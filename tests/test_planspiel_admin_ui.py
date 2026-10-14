"""Tests fuer die Verwaltung des Planspiel-Szenariokatalogs."""

import json
from decimal import Decimal
from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.planspiel import PlanspielSzenario, PlanspielSzenarioVersion
from app.models.user import Role, User, UserRole
from app.services.planspiel_katalog_service import aktive_version, katalog_importieren, version_als_dict

BASIS = "/admin/settings/planspiele"


def _setup_user(role: str, *, system: bool = True, modul: bool = True, org_id: int | None = None) -> User:
    """Legt einen Benutzer in einer eigenen Org an (Tests beeinflussen sich nicht)."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        setting = db.get(SystemSettings, "planspiel_module_enabled")
        if setting is None:
            db.add(SystemSettings(key="planspiel_module_enabled", value="true" if system else "false"))
        else:
            setting.value = "true" if system else "false"
        if org_id is None:
            org = FireDept(slug=f"ps-admin-{uuid4().hex}", name="Planspiel Admin", timezone="Europe/Vienna")
            db.add(org)
            db.flush()
            org_id = org.id
            db.add(OrgSettings(org_id=org_id, planspiel_modul_aktiv=modul))
        username = f"planspiel_admin_{uuid4().hex}"
        user = User(username=username, display_name=username, password_hash=hash_password("Test1234!"),
                    org_id=org_id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> None:
    client.cookies.clear()
    client.get("/login")
    response = client.post("/login", data={"username": user.username, "password": "Test1234!",
                                             "_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 200


def _csrf(client) -> str:
    return client.cookies.get("ec_csrf")


def _szenario(org_id: int, code: str) -> PlanspielSzenario:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        item = db.query(PlanspielSzenario).filter_by(org_id=org_id, code=code).one()
        db.expunge(item)
        return item
    finally:
        db.close()


def _versionen(szenario_id: int) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(PlanspielSzenarioVersion).filter_by(szenario_id=szenario_id).count()
    finally:
        db.close()


def _inhalt(szenario_id: int) -> dict:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        item = db.get(PlanspielSzenario, szenario_id)
        return version_als_dict(aktive_version(db, item), db)
    finally:
        db.close()


def _formular(client, szenario_id: int, **aenderungen) -> dict:
    """Baut die Formulardaten so, wie die Detailseite sie sendet."""
    inhalt = _inhalt(szenario_id)
    ereignisse = []
    for ereignis in inhalt["ereignisse"]:
        zeile = dict(ereignis)
        for feld in ("offset_min_s", "offset_max_s"):
            if zeile[feld] is not None:
                zeile[feld] = str(Decimal(zeile[feld]) / 60)
        zeile["wahrscheinlichkeit"] = str(Decimal(str(zeile["wahrscheinlichkeit"])) * 100)
        ereignisse.append(zeile)
    geo = inhalt["geografie_bedingung"]
    daten = {
        "_csrf": _csrf(client),
        "titel": inhalt["titel"],
        "beschreibung": inhalt["beschreibung"] or "",
        "kategorie": inhalt["kategorie"],
        "stichwort_code": inhalt["stichwort_code"],
        "stichwort_quelle": inhalt["stichwort_quelle"] or "",
        "alarmtext_template": inhalt["alarmtext_template"],
        "regie_geheimlage": inhalt["regie_geheimlage"] or "",
        "grundsituation": inhalt["grundsituation"] or "",
        "ort_typen": list(inhalt["ort_typen"]),
        "geografie_bedingung": "&".join(f"{k}={v}" for k, v in geo.items()) if geo else "",
        "schwierigkeit": inhalt["schwierigkeit"],
        "schlagworte": inhalt["schlagworte"] or "",
        "lernziele": inhalt["lernziele"] or "",
        "gsl_modus": inhalt["gsl_modus"] or "",
        "aktiv": "1",
        "zufall_erlaubt": "1",
        "gewicht": "10",
        "ereignisse_json": json.dumps(ereignisse),
    }
    if inhalt["requires_existing_object"]:
        daten["requires_existing_object"] = "1"
    if inhalt["requires_verified_bma"]:
        daten["requires_verified_bma"] = "1"
    daten.update(aenderungen)
    return daten


def _ohne_meta(inhalt: dict) -> dict:
    return {k: v for k, v in inhalt.items() if k not in {"beschreibung", "stichwort_quelle"}}


# a) Modul, Rollen, automatischer Katalog
def test_a_modulschalter_und_rollen(client):
    _login(client, _setup_user("org_admin", system=False))
    assert client.get(f"{BASIS}/szenarien").status_code == 404

    _login(client, _setup_user("readonly"))
    assert client.get(f"{BASIS}/szenarien").status_code == 403

    leiter = _setup_user("uebungsleiter")
    _login(client, leiter)
    assert client.get(f"{BASIS}/szenarien").status_code == 200
    b09 = _szenario(leiter.org_id, "B09")
    antwort = client.post(f"{BASIS}/szenarien/{b09.id}", data=_formular(client, b09.id, titel="X"))
    assert antwort.status_code == 403

    _login(client, _setup_user("org_admin"))
    liste = client.get(f"{BASIS}/szenarien")
    assert liste.status_code == 200
    assert "B09" in liste.text


# b) Filter + HTMX-Partial
def test_b_filter_und_partial(client):
    _login(client, _setup_user("org_admin"))
    gsl = client.get(f"{BASIS}/szenarien?kategorie=gsl")
    assert "G01" in gsl.text
    assert "B09" not in gsl.text
    partial = client.get(f"{BASIS}/szenarien?kategorie=gsl", headers={"HX-Request": "true"})
    assert partial.status_code == 200
    assert "<html" not in partial.text
    assert "G01" in partial.text


# c) Bearbeiten erzeugt neue Version und Badge
def test_c_bearbeiten_erzeugt_version(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    b09 = _szenario(admin.org_id, "B09")
    vorher = _versionen(b09.id)
    antwort = client.post(f"{BASIS}/szenarien/{b09.id}", data=_formular(client, b09.id, titel="Zimmerbrand geaendert"),
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert _versionen(b09.id) == vorher + 1
    assert _szenario(admin.org_id, "B09").org_geaendert is True
    assert _inhalt(b09.id)["titel"] == "Zimmerbrand geaendert"
    assert "angepasst" in client.get(f"{BASIS}/szenarien?q=B09").text


# d) Unbekannter Platzhalter wird abgelehnt
def test_d_unbekannter_platzhalter(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    b09 = _szenario(admin.org_id, "B09")
    vorher = _versionen(b09.id)
    antwort = client.post(f"{BASIS}/szenarien/{b09.id}",
                          data=_formular(client, b09.id, alarmtext_template="Brand {__class__}"))
    assert antwort.status_code == 400
    assert _versionen(b09.id) == vorher


# e) Duplizieren
def test_e_duplizieren(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    b09 = _szenario(admin.org_id, "B09")
    code = f"EIG-{uuid4().hex[:6]}"
    antwort = client.post(f"{BASIS}/szenarien/{b09.id}/duplizieren", data={"_csrf": _csrf(client), "code": code},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert _szenario(admin.org_id, code).herkunft == "org"
    doppelt = client.post(f"{BASIS}/szenarien/{b09.id}/duplizieren", data={"_csrf": _csrf(client), "code": code})
    assert doppelt.status_code == 400


# f) Zuruecksetzen
def test_f_zuruecksetzen(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    b09 = _szenario(admin.org_id, "B09")
    client.post(f"{BASIS}/szenarien/{b09.id}", data=_formular(client, b09.id, titel="Geaendert"))
    assert _szenario(admin.org_id, "B09").org_geaendert is True
    antwort = client.post(f"{BASIS}/szenarien/{b09.id}/zuruecksetzen", data={"_csrf": _csrf(client)},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert _szenario(admin.org_id, "B09").org_geaendert is False
    assert _inhalt(b09.id)["titel"] == "Zimmerbrand in Mehrfamilienhaus"


# g) Entfernen + fremde Org
def test_g_entfernen_und_fremde_org(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    b09 = _szenario(admin.org_id, "B09")
    code = f"DEL-{uuid4().hex[:6]}"
    client.post(f"{BASIS}/szenarien/{b09.id}/duplizieren", data={"_csrf": _csrf(client), "code": code})
    kopie = _szenario(admin.org_id, code)
    antwort = client.post(f"{BASIS}/szenarien/{kopie.id}/entfernen", data={"_csrf": _csrf(client)},
                          follow_redirects=False)
    assert antwort.status_code == 303
    assert "geloescht" in antwort.headers["location"]

    fremd = _setup_user("org_admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        katalog_importieren(db, fremd.org_id)
        db.commit()
    finally:
        db.close()
    fremdes_b09 = _szenario(fremd.org_id, "B09")
    assert client.get(f"{BASIS}/szenarien/{fremdes_b09.id}").status_code == 404
    assert client.post(f"{BASIS}/szenarien/{fremdes_b09.id}/entfernen",
                       data={"_csrf": _csrf(client)}).status_code == 404
    assert client.post(f"{BASIS}/szenarien/{fremdes_b09.id}",
                       data=_formular(client, fremdes_b09.id)).status_code == 404


# h) Export und Import
def test_h_export_import(client):
    _login(client, _setup_user("org_admin"))
    client.get(f"{BASIS}/szenarien")
    export = client.get(f"{BASIS}/szenarien/export.json")
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
    daten = export.json()
    assert daten["format"] == "ecp-planspiel-katalog"

    kaputt = client.post(f"{BASIS}/szenarien/import", data={"_csrf": _csrf(client)},
                         files={"file": ("k.json", b'{"format": "falsch"}', "application/json")})
    assert kaputt.status_code == 400
    assert "Import fehlgeschlagen" in kaputt.text

    _login(client, _setup_user("org_admin"))
    ok = client.post(f"{BASIS}/szenarien/import", data={"_csrf": _csrf(client)},
                     files={"file": ("k.json", json.dumps(daten).encode(), "application/json")})
    assert ok.status_code == 200
    assert "aktualisiert" in ok.text


# i) CSRF
def test_i_csrf_pflicht(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    b09 = _szenario(admin.org_id, "B09")
    antwort = client.post(f"{BASIS}/szenarien/{b09.id}/zuruecksetzen", data={})
    assert antwort.status_code == 403


# j) Stichwort-Referenz
def test_j_stichwort_referenz(client):
    _login(client, _setup_user("org_admin"))
    antwort = client.get(f"{BASIS}/stichworte")
    assert antwort.status_code == 200
    assert "f14" in antwort.text
    assert "registrierter Notrufteilnehmer" in antwort.text


# k) Vorschau rendert und escaped
def test_k_vorschau_escaped(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    b09 = _szenario(admin.org_id, "B09")
    client.post(f"{BASIS}/szenarien/{b09.id}", data=_formular(client, b09.id, titel="<script>alert(1)</script>"))
    detail = client.get(f"{BASIS}/szenarien/{b09.id}")
    assert detail.status_code == 200
    assert "Musterstraße 1" in detail.text
    assert "<script>alert(1)</script>" not in detail.text
    assert "&lt;script&gt;" in detail.text


# l) Formular-Roundtrip ohne Aenderung verfaelscht nichts
def test_l_formular_roundtrip_verlustfrei(client):
    admin = _setup_user("org_admin")
    _login(client, admin)
    client.get(f"{BASIS}/szenarien")
    for code in ("B09", "B20", "B25", "G03"):
        item = _szenario(admin.org_id, code)
        vorher = _inhalt(item.id)
        antwort = client.post(f"{BASIS}/szenarien/{item.id}", data=_formular(client, item.id),
                              follow_redirects=False)
        assert antwort.status_code == 303, code
        assert _ohne_meta(_inhalt(item.id)) == _ohne_meta(vorher), code
