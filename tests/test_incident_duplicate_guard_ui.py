"""End-to-End: Duplikat-Sperre bei manueller Einsatzanlage (POST /einsatz/neu) —
siehe app/services/incident_service.py::create_incident() und
app/routers/ui_incident.py::new_incident(). Eigene, frische Org je Test (setup_db
ist session-scoped, siehe tests/test_api.py fuer dieselbe Begruendung)."""
import uuid

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_incident_leader(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"dup-guard-ui-{uuid.uuid4().hex[:8]}", name="Duplikat-UI-Test-Org",
            color="#987654", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Duplikat Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "incident_leader").id))
        db.commit()
        return org.id
    finally:
        db.close()


def test_doppel_submit_legt_nur_einen_einsatz_an(client, monkeypatch):
    org_id = _setup_incident_leader("dup_guard_ui_user")
    _login(client, "dup_guard_ui_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")
    notify_calls = []

    async def fake_notify(db, incident, **kwargs):
        notify_calls.append(incident.id)

    monkeypatch.setattr(
        "app.services.incident_notify.notify_incident_created", fake_notify,
    )

    form = {
        "_csrf": csrf,
        "alarm_type_code": "T1",
        "confirm_real_incident": "true",
        "address_street": "Teststraße",
        "address_no": "1",
        "address_city": "Wolfurt",
        "report_text": "Doppel-Submit-Test",
    }

    r1 = client.post("/einsatz/neu", data=form, follow_redirects=False)
    assert r1.status_code == 303
    erste_url = r1.headers["location"]
    erster_id = int(erste_url.rstrip("/").split("/")[-1])
    assert notify_calls == [erster_id]

    r2 = client.post("/einsatz/neu", data=form, follow_redirects=False)
    assert r2.status_code == 303
    zweite_url = r2.headers["location"]
    assert zweite_url == erste_url  # redirected auf denselben, bereits bestehenden Einsatz
    assert notify_calls == [erster_id]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        anzahl = db.query(Incident).filter(
            Incident.primary_org_id == org_id, Incident.alarm_type_code == "T1",
        ).count()
        assert anzahl == 1
        incident = db.get(Incident, erster_id)
        assert incident is not None
        assert incident.report_text == "Doppel-Submit-Test"
    finally:
        db.close()


def test_uebung_braucht_keine_echt_einsatz_bestaetigung(client, monkeypatch):
    username = f"exercise_confirm_{uuid.uuid4().hex[:8]}"
    org_id = _setup_incident_leader(username)
    _login(client, username, "Test1234!")
    notify_calls = []

    async def fake_notify(db, incident, **kwargs):
        notify_calls.append(incident.id)

    monkeypatch.setattr(
        "app.services.incident_notify.notify_incident_created", fake_notify,
    )

    response = client.post("/einsatz/neu", data={
        "_csrf": client.cookies.get("ec_csrf"),
        "alarm_type_code": "T1",
        "is_exercise": "true",
        "report_text": "Uebung ohne Echt-Einsatz-Bestaetigung",
    }, follow_redirects=False)

    assert response.status_code == 303
    incident_id = int(response.headers["location"].rstrip("/").split("/")[-1])
    assert notify_calls == [incident_id]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.query(Incident).filter(
            Incident.id == incident_id, Incident.primary_org_id == org_id,
        ).one()
        assert incident.is_exercise is True
    finally:
        db.close()


def test_echter_einsatz_ohne_bestaetigung_wird_vollstaendig_blockiert(client, monkeypatch):
    username = f"real_unconfirmed_{uuid.uuid4().hex[:8]}"
    org_id = _setup_incident_leader(username)
    _login(client, username, "Test1234!")
    notify_calls = []

    async def fake_notify(db, incident, **kwargs):
        notify_calls.append(incident.id)

    monkeypatch.setattr(
        "app.services.incident_notify.notify_incident_created", fake_notify,
    )

    response = client.post("/einsatz/neu", data={
        "_csrf": client.cookies.get("ec_csrf"),
        "alarm_type_code": "T1",
        "report_text": "Unbestaetigter echter Einsatz",
    }, follow_redirects=False)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Bitte bestätigen, dass dies ein echter Einsatz ist.",
    }
    assert notify_calls == []

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(Incident).filter(
            Incident.primary_org_id == org_id,
            Incident.report_text == "Unbestaetigter echter Einsatz",
        ).count() == 0
    finally:
        db.close()


def test_bestaetigter_echter_einsatz_alarmiert_normal(client, monkeypatch):
    username = f"real_confirmed_{uuid.uuid4().hex[:8]}"
    org_id = _setup_incident_leader(username)
    _login(client, username, "Test1234!")
    notify_calls = []

    async def fake_notify(db, incident, **kwargs):
        notify_calls.append(incident.id)

    monkeypatch.setattr(
        "app.services.incident_notify.notify_incident_created", fake_notify,
    )

    response = client.post("/einsatz/neu", data={
        "_csrf": client.cookies.get("ec_csrf"),
        "alarm_type_code": "T1",
        "confirm_real_incident": "true",
        "report_text": "Bestaetigter echter Einsatz",
    }, follow_redirects=False)

    assert response.status_code == 303
    incident_id = int(response.headers["location"].rstrip("/").split("/")[-1])
    assert notify_calls == [incident_id]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        incident = db.query(Incident).filter(
            Incident.id == incident_id, Incident.primary_org_id == org_id,
        ).one()
        assert incident.is_exercise is False
    finally:
        db.close()
