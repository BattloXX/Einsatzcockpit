"""GSL-Formulare ueber den echten SMS-Dispatcher mit Android-Rueckmeldung."""
import asyncio
import json
import re
from uuid import uuid4

import pytest

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.citizen_verification import CitizenReportVerification
from app.models.major_incident import CitizenReport, MajorIncident
from app.models.verleih import VerleihAusleihe
from app.routers import ws
from app.services import verleih_service
from tests.gateway_helpers import bind, cleanup
from tests.test_gsl_sektoren import _login, _user_lage


class AndroidGateway:
    def __init__(self, ok):
        self.ok = ok
        self.jobs = []

    async def send_text(self, payload):
        job = json.loads(payload)
        self.jobs.append(job)
        from app.services import sms_outbox
        await asyncio.to_thread(sms_outbox.event, self.connection, self.token_hash, {
            "type": "sms.result", "id": job["id"], "ok": self.ok,
            "error": None if self.ok else "SmsManager resultCode=1",
        })

    async def close(self, code=1000):
        pass


@pytest.fixture
def sms_context(sms_outbox_state):
    username = f"sms-flows-{uuid4().hex}"
    lage_id, org_id = _user_lage(username, username)
    token = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(MajorIncident, lage_id).public_token = token
        db.commit()
    yield username, lage_id, org_id, token
    ws._sms_gateways.pop(org_id, None)
    ws._sms_last_gateway.pop(org_id, None)
    with SessionLocal() as db:
        db.query(CitizenReportVerification).filter_by(org_id=org_id, major_incident_id=lage_id).delete()
        db.commit()


@pytest.fixture
def android_gateway():
    connections = []
    def create(org_id, ok=True, **kwargs):
        connection, gateway = bind(org_id=org_id, socket=AndroidGateway(ok), **kwargs)
        connections.append(connection)
        return gateway
    yield create
    for connection in connections:
        cleanup(connection)


@pytest.mark.parametrize("ok", [True, False])
def test_citizen_sms_and_verification(client, sms_context, android_gateway, ok):
    _, lage_id, org_id, token = sms_context
    gateway = android_gateway(org_id, ok)
    client.cookies.clear()
    assert client.get(f"/melden/{token}").status_code == 200
    response = client.post(f"/melden/{token}", data={
        "_csrf": client.cookies.get("ec_csrf"), "description": "Keller voll Wasser",
        "reporter_name": "Mia", "reporter_contact": "+436641234567",
    })
    assert response.status_code == 200
    assert len(gateway.jobs) == 1
    assert gateway.jobs[0]["type"] == "sms.send"
    assert gateway.jobs[0]["to"] == "+436641234567"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(CitizenReport).filter_by(major_incident_id=lage_id).count() == 0
    if not ok:
        assert "SMS konnte nicht gesendet werden" in response.text
        with SessionLocal() as db:
            set_tenant_context(db, None)
            assert db.query(CitizenReportVerification).filter_by(major_incident_id=lage_id).count() == 0
        return
    verify_token = re.search(r'/verify/([^"/]+)', response.text).group(1)
    pin = re.search(r"Bestätigungscode lautet (\d{4})", gateway.jobs[0]["text"]).group(1)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        pending = db.query(CitizenReportVerification).filter_by(major_incident_id=lage_id).one()
        assert pending.token_hash == hash_api_key(verify_token)
        assert pending.pin_hash == hash_api_key(f"{verify_token}:{pin}")
    verified = client.post(f"/melden/{token}/verify/{verify_token}", data={
        "_csrf": client.cookies.get("ec_csrf"), "pin": pin,
    })
    assert verified.status_code == 200
    with SessionLocal() as db:
        set_tenant_context(db, None)
        report = db.query(CitizenReport).filter_by(major_incident_id=lage_id).one()
        assert report.phone_verified is True
        assert report.reporter_contact == "+436641234567"


@pytest.mark.parametrize("action", ["pin-senden", "sms-ausleih", "erinnerung-manuell"])
@pytest.mark.parametrize("ok", [True, False])
def test_loan_sms_actions_use_android_result(client, sms_context, android_gateway, action, ok):
    username, lage_id, org_id, _ = sms_context
    with SessionLocal() as db:
        set_tenant_context(db, None)
        loan = verleih_service.create_ausleihe(
            db, lage_id, org_id, "Mia", None, "+436641234567", None,
            [{"bezeichnung": "Pumpe", "menge": 1}], None,
        )
        loan_id = loan.id
    gateway = android_gateway(org_id, ok)
    _login(client, username)
    response = client.post(f"/lage/{lage_id}/verleih/{loan_id}/{action}", data={
        "_csrf": client.cookies.get("ec_csrf"), "sms_text": "Test Ausleihe",
    })
    assert response.status_code == 200
    assert len(gateway.jobs) == 1
    assert gateway.jobs[0]["to"] == "+436641234567"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        loan = db.get(VerleihAusleihe, loan_id)
        if action == "sms-ausleih":
            assert loan.sms_ausleih_gesendet is ok
        elif action == "erinnerung-manuell":
            assert (loan.erinnerung_gesendet_at is not None) is ok
        else:
            assert loan.pin and loan.pin in gateway.jobs[0]["text"]
    if ok:
        assert "erfolgreich gesendet" in response.text or "SMS gesendet." in response.text
    else:
        assert re.search(r"SMS konnte nicht gesendet werden", response.text)


def test_citizen_and_loan_share_gateway_rotation(client, sms_context, android_gateway):
    username, lage_id, org_id, token = sms_context
    first, second = android_gateway(org_id), android_gateway(org_id)
    client.cookies.clear()
    client.get(f"/melden/{token}")
    submitted = client.post(f"/melden/{token}", data={
        "_csrf": client.cookies.get("ec_csrf"), "description": "Keller voll Wasser",
        "reporter_contact": "+436641234567",
    })
    assert submitted.status_code == 200
    assert len(second.jobs) == 1 and not first.jobs

    with SessionLocal() as db:
        set_tenant_context(db, None)
        loan = verleih_service.create_ausleihe(
            db, lage_id, org_id, "Mia", None, "+436641234567", None,
            [{"bezeichnung": "Pumpe", "menge": 1}], None,
        )
        loan_id = loan.id
    _login(client, username)
    for action in ["pin-senden", "sms-ausleih"]:
        response = client.post(f"/lage/{lage_id}/verleih/{loan_id}/{action}", data={
            "_csrf": client.cookies.get("ec_csrf"), "sms_text": "Test Ausleihe",
        })
        assert response.status_code == 200
    assert len(first.jobs) == 1 and len(second.jobs) == 2
    assert "Geraeteausleihe-PIN" in first.jobs[0]["text"]
    assert "Bestätigungscode" in second.jobs[0]["text"]
    assert second.jobs[1]["text"] == "Test Ausleihe"
