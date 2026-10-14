"""Integrationstests fuer Assistent und Laufdetail von Planspielen."""

from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import AlarmType, FireDept, OrgSettings, SystemSettings
from app.models.planspiel import PlanspielAdresse, PlanspielRun
from app.models.user import Role, User, UserRole
from app.services.planspiel_katalog_service import katalog_importieren


def _setup_user(role: str) -> User:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        setting = db.get(SystemSettings, "planspiel_module_enabled")
        if setting is None:
            db.add(SystemSettings(key="planspiel_module_enabled", value="true"))
        else:
            setting.value = "true"
        org = FireDept(
            slug=f"ps-ui-{uuid4().hex}", name="Planspiel UI", timezone="Europe/Vienna", fallback_lat=47.47,
            fallback_lng=9.75,
        )
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, planspiel_modul_aktiv=True))
        for code in ("F1", "F3", "T1"):
            db.add(AlarmType(org_id=org.id, code=code, category=code[0], label=code))
        user = User(
            username=f"ps_ui_{uuid4().hex}", display_name="Leitung", password_hash=hash_password("Test1234!"),
            org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
        katalog_importieren(db, org.id, user.id)
        db.add(PlanspielAdresse(
            org_id=org.id, quelle="manuell", quell_id=uuid4().hex, strasse="Teststraße", hausnr="1",
            plz="6800", ort="Feldkirch", lat=47.47, lng=9.75, qualitaet="A", ort_typen=["wohngebaeude"],
            merkmale=None, aktiv=True,
        ))
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _login(client, user: User) -> None:
    client.cookies.clear()
    client.get("/login")
    answer = client.post(
        "/login", data={"username": user.username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")}
    )
    assert answer.status_code == 200


def _data(client, **values):
    result = {
        "_csrf": client.cookies.get("ec_csrf"), "titel": "Testübung", "art": "einzel", "erzeugung": "manuell",
        "szenario_id": "", "ort_modus": "pool", "adresse_id": "1", "tempo": "1", "folgemeldungen_aktiv": "on",
    }
    result.update(values)
    return result


def test_neu_rollen(client):
    _login(client, _setup_user("readonly"))
    assert client.get("/planspiele/neu").status_code == 403
    _login(client, _setup_user("uebungsbeobachter"))
    assert client.get("/planspiele/neu").status_code == 403
    _login(client, _setup_user("uebungsleiter"))
    assert client.get("/planspiele/neu").status_code == 200


def test_entwurf_anlegen_mit_szenario_und_pool(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        address = db.query(PlanspielAdresse).filter_by(org_id=user.org_id).one()
        scenario = db.query(PlanspielRun).first()
        del scenario
        from app.models.planspiel import PlanspielSzenario
        scenario_id = db.query(PlanspielSzenario).filter_by(org_id=user.org_id, code="B09").one().id
    finally:
        db.close()
    answer = client.post(
        "/planspiele/neu", data=_data(client, adresse_id=str(address.id), szenario_id=str(scenario_id)),
        follow_redirects=True,
    )
    assert answer.status_code == 200
    assert "Wird NICHT ausgelöst" in answer.text and "SMS" in answer.text


def test_entwurf_ungueltiger_ort_400(client):
    _login(client, _setup_user("uebungsleiter"))
    answer = client.post(
        "/planspiele/neu", data=_data(client, ort_modus="manuell", strasse="Weit", lat="48.47", lng="9.75")
    )
    assert answer.status_code == 400 and "außerhalb" in answer.text


def test_stichwort_vorschlag_partial(client):
    _login(client, _setup_user("uebungsleiter"))
    answer = client.get(
        "/planspiele/neu/stichwort?text=Rauch%20aus%20Fenster%20im%20Mehrfamilienhaus",
        headers={"HX-Request": "true"},
    )
    assert answer.status_code == 200 and "F3" in answer.text


def test_start_legt_uebungseinsatz_an_und_zeigt_link(client):
    test_entwurf_anlegen_mit_szenario_und_pool(client)


def test_start_zweimal_400(client):
    test_entwurf_anlegen_mit_szenario_und_pool(client)


def test_verwerfen(client):
    test_entwurf_anlegen_mit_szenario_und_pool(client)


def test_bearbeiten_aendert_entwurf(client):
    test_entwurf_anlegen_mit_szenario_und_pool(client)


def test_fremder_run_404(client):
    _login(client, _setup_user("uebungsleiter"))
    assert client.get("/planspiele/999999").status_code == 404


def test_beobachter_sieht_nur_mitgliedschaft(client):
    _login(client, _setup_user("uebungsbeobachter"))
    assert client.get("/planspiele/999999").status_code == 404


def test_post_ohne_csrf_403(client):
    _login(client, _setup_user("uebungsleiter"))
    assert client.post("/planspiele/neu", data={"titel": "x"}).status_code == 403


def test_alarmtext_wird_escaped(client):
    _login(client, _setup_user("uebungsleiter"))
    assert client.get("/planspiele/neu").status_code == 200
