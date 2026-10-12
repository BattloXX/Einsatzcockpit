"""Adress-Picker beim manuellen Anlegen einer GSL-Einsatzstelle."""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.services.geocoding import GeocodeResult


def _login(client, username):
    client.get("/login")
    return client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )


def _lage(username, slug, role="incident_leader"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=slug, name=slug, city="Pickerstadt", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(username=username, display_name=username, password_hash=hash_password("Test1234!"), org_id=org.id, active=True)
        db.add(user)
        db.flush()
        rolle = db.query(Role).filter(Role.code == role).first()
        if rolle is None:  # org_admin fehlt im Seed
            rolle = Role(code=role, label=role)
            db.add(rolle)
            db.flush()
        db.add(UserRole(user_id=user.id, role_id=rolle.id))
        lage = MajorIncident(org_id=org.id, name="Pickerlage", status=MajorIncidentStatus.active)
        db.add(lage)
        db.commit()
        return lage.id
    finally:
        db.close()


def _site(lage_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(IncidentSite).filter(IncidentSite.major_incident_id == lage_id).one()
    finally:
        db.close()


def test_pin_koordinaten_ueberspringen_geocoding_und_speichern_einsatzgrund(client, setup_db, monkeypatch):
    lage_id = _lage("picker_pin", "picker-pin")
    called = False

    async def geocode(*_args):
        nonlocal called
        called = True
        return GeocodeResult(1, 2, "darf nicht verwendet werden")

    monkeypatch.setattr("app.services.geocoding.geocode_address", geocode)
    _login(client, "picker_pin")
    response = client.post(f"/lage/{lage_id}/stellen/neu", data={
        "_csrf": client.cookies.get("ec_csrf"), "bezeichnung": "Mit Pin", "einsatzgrund": "Wassereintritt",
        "ort": "Pickerstadt", "strasse": "Hauptstraße", "hausnr": "7", "lat": "47.5001", "lng": "9.7002",
    }, headers={"HX-Request": "true"})
    assert response.status_code == 204
    site = _site(lage_id)
    assert (site.lat, site.lng, site.einsatzgrund) == (47.5001, 9.7002, "Wassereintritt")
    assert not called


def test_ohne_oder_mit_ungueltigen_koordinaten_geocodiert(client, setup_db, monkeypatch):
    lage_id = _lage("picker_geo", "picker-geo")
    calls = []

    async def geocode(*args):
        calls.append(args)
        return GeocodeResult(47.51, 9.71, "Gefunden")

    monkeypatch.setattr("app.services.geocoding.geocode_address", geocode)
    _login(client, "picker_geo")
    for name, lat, lng in [("Ohne", "", ""), ("Ungültig", "200", "abc")]:
        response = client.post(f"/lage/{lage_id}/stellen/neu", data={
            "_csrf": client.cookies.get("ec_csrf"), "bezeichnung": name, "ort": "Pickerstadt", "lat": lat, "lng": lng,
        }, headers={"HX-Request": "true"})
        assert response.status_code == 204
    assert len(calls) == 2
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sites = db.query(IncidentSite).filter(IncidentSite.major_incident_id == lage_id).all()
        assert [(site.lat, site.lng) for site in sites] == [(47.51, 9.71), (47.51, 9.71)]
    finally:
        db.close()


def test_board_enthaelt_gemeinsamen_picker_mit_org_stadt(client, setup_db):
    lage_id = _lage("picker_board", "picker-board")
    _login(client, "picker_board")
    response = client.get(f"/lage/{lage_id}")
    assert response.status_code == 200
    assert 'id="neuStelleMapContainer"' in response.text
    assert 'name="lat"' in response.text
    assert 'value="Pickerstadt"' in response.text
    assert "address-picker.js" in response.text
    # Einsatzgrund wird auf Board-Karte/Detail nicht angezeigt -> kein Feld im Dialog
    assert 'name="einsatzgrund"' not in response.text


def test_geocode_ist_fuer_gsl_rollen_erlaubt_und_readonly_gesperrt(client, setup_db, monkeypatch):
    async def geocode(*_args):
        return GeocodeResult(47.5, 9.7, "Gefunden")

    monkeypatch.setattr("app.services.geocoding.geocode_address", geocode)
    for role in ("recorder", "org_admin"):
        _lage("picker_" + role, "picker-" + role, role)
        _login(client, "picker_" + role)
        response = client.post("/adresse/geocode", data={"_csrf": client.cookies.get("ec_csrf"), "address_city": "Pickerstadt"})
        assert response.status_code == 200
        client.get("/logout")
    _lage("picker_readonly", "picker-readonly", "readonly")
    _login(client, "picker_readonly")
    response = client.post("/adresse/geocode", data={"_csrf": client.cookies.get("ec_csrf"), "address_city": "Pickerstadt"})
    assert response.status_code == 403


def test_neuer_einsatz_dialog_nutzt_gemeinsamen_picker(client, setup_db):
    _lage("picker_index", "picker-index")
    _login(client, "picker_index")
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="newIncMapContainer"' in response.text
    assert 'name="address_city" id="newIncCity"' in response.text
    assert 'value="Pickerstadt"' in response.text
    assert "address-picker.js" in response.text
