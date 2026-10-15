"""UI-Tests fuer Planspiel-Einstellungen und Adresspool."""

import json
from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import ObjektKategorie
from app.models.planspiel import PlanspielAdresse, PlanspielOrtTypZuordnung
from app.models.user import Role, User, UserRole
from app.services.planspiel_ort_service import OsmImportFehler, PoolErgebnis

BASIS = "/admin/settings/planspiele"


def _setup_user(role: str, *, system: bool = True, modul: bool = True) -> User:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        setting = db.get(SystemSettings, "planspiel_module_enabled")
        if setting is None:
            db.add(SystemSettings(key="planspiel_module_enabled", value=str(system).lower()))
        else:
            setting.value = str(system).lower()
        org = FireDept(
            slug=f"ps-settings-{uuid4().hex}",
            name="Planspiel Einstellungen",
            timezone="Europe/Vienna",
            fallback_lat=47.47,
            fallback_lng=9.75,
        )
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, planspiel_modul_aktiv=modul))
        username = f"planspiel_settings_{uuid4().hex}"
        user = User(
            username=username,
            display_name=username,
            password_hash=hash_password("Test1234!"),
            org_id=org.id,
            active=True,
        )
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
    response = client.post(
        "/login",
        data={"username": user.username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
    )
    assert response.status_code == 200


def _csrf(client) -> str:
    return client.cookies.get("ec_csrf")


def _adresse(org_id: int) -> PlanspielAdresse:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = PlanspielAdresse(
            org_id=org_id, quelle="manuell", quell_id=uuid4().hex, strasse="Teststraße", hausnr="1",
            plz=None, ort="Testort", lat=47.47, lng=9.75, qualitaet="C", ort_typen=["boot"],
            merkmale=None, aktiv=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def test_seite_rollen_und_modul(client):
    _login(client, _setup_user("readonly"))
    assert client.get(BASIS).status_code == 403
    leiter = _setup_user("uebungsleiter")
    _login(client, leiter)
    assert client.get(BASIS).status_code == 200
    assert client.post(f"{BASIS}/einstellungen/grenzen", data={"_csrf": _csrf(client)}).status_code == 403
    _login(client, _setup_user("org_admin", system=False))
    assert client.get(BASIS).status_code == 404


def test_gebiet_polygon_speichern_und_ungueltig_ablehnen(client):
    user = _setup_user("org_admin")
    _login(client, user)
    polygon = {"type": "Polygon", "coordinates": [[[9.74, 47.46], [9.76, 47.46], [9.75, 47.48], [9.74, 47.46]]]}
    data = {"_csrf": _csrf(client), "radius_m": "5000", "gebiet_geojson": json.dumps(polygon)}
    answer = client.post(f"{BASIS}/einstellungen/gebiet", data=data)
    assert answer.status_code == 200
    bad = {"type": "Polygon", "coordinates": [[[9.74, 47.46], [9.76, 47.46], [9.75, 47.48]]]}
    data["gebiet_geojson"] = json.dumps(bad)
    assert client.post(f"{BASIS}/einstellungen/gebiet", data=data).status_code == 400
    data["gebiet_geojson"] = ""
    assert client.post(f"{BASIS}/einstellungen/gebiet", data=data).status_code == 200


def test_grenzen_validierung(client):
    _login(client, _setup_user("org_admin"))
    data = {"_csrf": _csrf(client), "max_laufende_runs": "0", "max_stellen_je_minute": "61"}
    assert client.post(f"{BASIS}/einstellungen/grenzen", data=data).status_code == 400
    data.update(max_laufende_runs="2", max_stellen_je_minute="6")
    assert client.post(f"{BASIS}/einstellungen/grenzen", data=data).status_code == 200


def test_pool_aufbauen_button(client):
    _login(client, _setup_user("org_admin"))
    assert client.post(f"{BASIS}/adresspool/aufbauen", data={"_csrf": _csrf(client)}).status_code == 200


def _osm_ergebnis(client) -> str:
    import time

    antwort = client.post(f"{BASIS}/adresspool/osm", data={"_csrf": _csrf(client)})
    assert antwort.status_code == 200
    for _ in range(50):
        if "OSM-Import läuft" not in antwort.text:
            return antwort.text
        time.sleep(0.05)
        antwort = client.get(f"{BASIS}/adresspool/osm/status")
        assert antwort.status_code == 200
    raise AssertionError("OSM-Import wurde nicht fertig")


def test_osm_import_button_fehler_und_erfolg(client, monkeypatch):
    _login(client, _setup_user("org_admin"))
    async def fails(*args):
        raise OsmImportFehler("Overpass ausgelastet")
    monkeypatch.setattr("app.routers.ui_planspiel_admin.osm_importieren", fails)
    text = _osm_ergebnis(client)
    assert "form-error" in text and "Overpass ausgelastet" in text
    async def succeeds(*args):
        return PoolErgebnis(neu=1)
    monkeypatch.setattr("app.routers.ui_planspiel_admin.osm_importieren", succeeds)
    assert "1 neu" in _osm_ergebnis(client)


def test_osm_import_laeuft_wird_nicht_doppelt_gestartet(client, monkeypatch):
    import asyncio

    _login(client, _setup_user("org_admin"))
    aufrufe = []
    async def langsam(*args):
        aufrufe.append(args)
        await asyncio.sleep(0.3)
        return PoolErgebnis(neu=2)
    monkeypatch.setattr("app.routers.ui_planspiel_admin.osm_importieren", langsam)
    erste = client.post(f"{BASIS}/adresspool/osm", data={"_csrf": _csrf(client)})
    assert "OSM-Import läuft" in erste.text
    zweite = client.post(f"{BASIS}/adresspool/osm", data={"_csrf": _csrf(client)})
    assert "OSM-Import läuft" in zweite.text
    assert "OSM-Import läuft" in client.get(BASIS).text
    import time

    for _ in range(40):
        status = client.get(f"{BASIS}/adresspool/osm/status").text
        if "OSM-Import läuft" not in status:
            break
        time.sleep(0.05)
    assert "2 neu" in status
    assert len(aufrufe) == 1


def test_manueller_punkt_innerhalb_und_ausserhalb(client):
    _login(client, _setup_user("org_admin"))
    data = {"_csrf": _csrf(client), "lat": "47.47", "lng": "9.75", "strasse": "Seeweg", "ort_typen": "boot"}
    assert client.post(f"{BASIS}/adresspool/punkt", data=data).status_code == 200
    data["lat"] = "48.47"
    assert client.post(f"{BASIS}/adresspool/punkt", data=data).status_code == 400


def test_pool_liste_filter_und_aktiv_toggle(client):
    user = _setup_user("org_admin")
    _login(client, user)
    row = _adresse(user.org_id)
    answer = client.get(f"{BASIS}/adresspool?q=Test", headers={"HX-Request": "true"})
    assert answer.status_code == 200 and "<html" not in answer.text
    answer = client.post(
        f"{BASIS}/adresspool/{row.id}/aktiv",
        data={"_csrf": _csrf(client)},
        headers={"HX-Request": "true"},
    )
    assert answer.status_code == 200


def test_ortstyp_zuordnung_ersetzen(client):
    user = _setup_user("org_admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        category = ObjektKategorie(org_id=user.org_id, name="Test")
        db.add(category)
        db.commit()
        category_id = category.id
    finally:
        db.close()
    _login(client, user)
    data = {"_csrf": _csrf(client), f"kategorie_{category_id}": "boot"}
    assert client.post(f"{BASIS}/einstellungen/ortstypen", data=data).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(PlanspielOrtTypZuordnung).filter_by(org_id=user.org_id, ort_typ="boot").count() == 1
    finally:
        db.close()


def test_fremde_pool_id_404(client):
    owner = _setup_user("org_admin")
    row = _adresse(owner.org_id)
    _login(client, _setup_user("org_admin"))
    assert client.post(f"{BASIS}/adresspool/{row.id}/aktiv", data={"_csrf": _csrf(client)}).status_code == 404


def test_post_ohne_csrf_403(client):
    _login(client, _setup_user("org_admin"))
    data = {"max_laufende_runs": "2", "max_stellen_je_minute": "6"}
    assert client.post(f"{BASIS}/einstellungen/grenzen", data=data).status_code == 403
