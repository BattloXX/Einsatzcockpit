"""Buergerportal-Verifizierungen teilen nur die DB zwischen Requests/Workern."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.citizen_verification import CitizenReportVerification
from app.models.major_incident import CitizenReport, MajorIncident
from app.models.master import FireDept
from app.routers import ui_major_incident
from app.services import sms_service
from app.services.citizen_verification_service import purge_citizen_verifications
from app.services.sms_log_retention import purge_all_sms_logs


@pytest.fixture
def portal(client, monkeypatch):
    token = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=token, name="Buergerportal", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        lage = MajorIncident(org_id=org.id, name="Hochwasser", public_token=token)
        db.add(lage)
        db.commit()
        lage_id, org_id = lage.id, org.id
    messages = []

    async def send_sms(org, phone, text):
        assert org == org_id
        messages.append(text)
        return True

    monkeypatch.setattr(sms_service, "sms_available", lambda org, db: True)
    monkeypatch.setattr(sms_service, "send_sms", send_sms)
    monkeypatch.setattr(ui_major_incident.secrets, "randbelow", lambda maximum: 7)
    client.cookies.clear()
    assert client.get(f"/melden/{token}").status_code == 200
    yield {"token": token, "lage_id": lage_id, "org_id": org_id, "messages": messages}
    with SessionLocal() as db:
        db.query(CitizenReportVerification).filter_by(org_id=org_id).delete()
        db.commit()


def _submit(client, portal, **kwargs):
    response = client.post(f"/melden/{portal['token']}", data={
        "_csrf": client.cookies.get("ec_csrf"), "description": "Keller unter Wasser",
        "reporter_name": "Mia", "reporter_contact": "+436641234567",
        "ort": "Graz", "strasse": "Hauptstrasse", "lat": "47.1", "lng": "15.4",
    }, **kwargs)
    assert response.status_code == 200
    verify_token = re.search(r'/verify/([^"/]+)', response.text).group(1)
    pin = re.search(r"Bestätigungscode lautet (\d{4})", portal["messages"][-1]).group(1)
    return f"/melden/{portal['token']}/verify/{verify_token}", verify_token, pin


def _confirm(client, url, pin):
    return client.post(url, data={"_csrf": client.cookies.get("ec_csrf"), "pin": pin})


def _reports(portal):
    with SessionLocal() as db:
        return db.query(CitizenReport).filter_by(major_incident_id=portal["lage_id"]).all()


def test_creation_stores_only_hashes_and_complete_form(client, portal, monkeypatch):
    async def save_photo(file):
        return "buergerfoto.jpg"

    monkeypatch.setattr(ui_major_incident, "_save_citizen_photo", save_photo)
    url, verify_token, pin = _submit(client, portal, files={"file": ("foto.jpg", b"test", "image/jpeg")})
    assert pin == "0007"
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(CitizenReportVerification).filter_by(org_id=portal["org_id"]).one()
        assert row.token_hash == hash_api_key(verify_token)
        assert row.pin_hash == hash_api_key(f"{verify_token}:{pin}")
        assert row.attempts == 0 and row.consumed_at is None
        assert row.created_at.tzinfo is None and row.expires_at.tzinfo is None
        assert row.phone == "+436641234567"
        assert row.form_data == {
            "description": "Keller unter Wasser", "reporter_name": "Mia",
            "reporter_contact": "+436641234567", "ort": "Graz", "strasse": "Hauptstrasse",
            "lat": 47.1, "lng": 15.4, "photo_fn": "buergerfoto.jpg", "source_ip": "testclient",
        }
        stored = json.dumps({column.name: str(getattr(row, column.name)) for column in row.__table__.columns})
        assert verify_token not in stored
        assert row.pin_hash != pin and pin not in row.form_data.values()
    assert _confirm(client, url, pin).status_code == 200
    report, = _reports(portal)
    assert report.photo_filename == "buergerfoto.jpg"
    assert report.source_ip == "testclient"


def test_another_client_confirms_from_database_and_duplicate_is_rejected(client, portal):
    url, verify_token, pin = _submit(client, portal)
    assert not hasattr(ui_major_incident, "_pending_verifications")
    # Eigener Client, eigene Request-Sessions; nur die DB verbindet beide Requests.
    with TestClient(app) as other:
        assert other.get(url).status_code == 200
        assert _confirm(other, url, pin).status_code == 200
    assert _confirm(client, url, pin).status_code == 410
    assert client.get(url).status_code == 410
    reports = _reports(portal)
    assert len(reports) == 1
    assert reports[0].phone_verified is True
    assert reports[0].description == "Keller unter Wasser"
    assert reports[0].ort == "Graz" and reports[0].lat == 47.1
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(CitizenReportVerification, hash_api_key(verify_token)).consumed_at is not None


def test_five_wrong_pins_block_correct_pin(client, portal):
    url, verify_token, pin = _submit(client, portal)
    for attempt in range(1, 6):
        response = _confirm(client, url, "9999")
        assert response.status_code == (200 if attempt < 5 else 410)
        with SessionLocal() as db:
            set_tenant_context(db, None)
            assert db.get(CitizenReportVerification, hash_api_key(verify_token)).attempts == attempt
    assert _confirm(client, url, pin).status_code == 410
    assert client.get(url).status_code == 410
    assert _reports(portal) == []
    assert response.text == _confirm(client, url, pin).text
    assert response.text == _confirm(client, url + "unknown", pin).text


def test_expired_verification_is_rejected(client, portal):
    url, verify_token, pin = _submit(client, portal)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.get(CitizenReportVerification, hash_api_key(verify_token))
        row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    assert client.get(url).status_code == 410
    assert _confirm(client, url, pin).status_code == 410
    assert _reports(portal) == []


def test_parallel_confirmation_consumes_once(client, portal, monkeypatch):
    url, _, pin = _submit(client, portal)
    barrier = Barrier(2)
    load = ui_major_incident._citizen_verification

    def concurrent_load(*args):
        result = load(*args)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(ui_major_incident, "_citizen_verification", concurrent_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: _confirm(client, url, pin), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 410]
    assert len(_reports(portal)) == 1


def test_failed_report_rolls_back_consumption(client, portal):
    url, verify_token, pin = _submit(client, portal)

    def fail_report(session, flush_context, instances):
        if any(isinstance(row, CitizenReport) for row in session.new):
            raise RuntimeError("Meldung konnte nicht gespeichert werden")

    from sqlalchemy.orm import Session
    event.listen(Session, "before_flush", fail_report)
    try:
        with pytest.raises(RuntimeError, match="Meldung konnte nicht gespeichert werden"):
            _confirm(client, url, pin)
    finally:
        event.remove(Session, "before_flush", fail_report)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(CitizenReportVerification, hash_api_key(verify_token)).consumed_at is None
    assert _confirm(client, url, pin).status_code == 200
    assert len(_reports(portal)) == 1


def test_no_gateway_keeps_unverified_fallback(client, portal, monkeypatch):
    monkeypatch.setattr(sms_service, "sms_available", lambda org, db: False)
    response = client.post(f"/melden/{portal['token']}", data={
        "_csrf": client.cookies.get("ec_csrf"), "description": "Wasser", "reporter_contact": "+436641234567",
    })
    assert response.status_code == 200
    assert portal["messages"] == []
    reports = _reports(portal)
    assert len(reports) == 1 and reports[0].phone_verified is False


def test_retention_scopes_org_and_keeps_recent_rows(client, portal):
    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        foreign = FireDept(slug=uuid4().hex, name="Fremd", color="#123456", bos="Feuerwehr")
        db.add(foreign)
        db.flush()
        foreign_id = foreign.id
        for key, org_id, expiry, consumed in [
            ("expired", portal["org_id"], now - timedelta(days=2), None),
            ("consumed", portal["org_id"], now + timedelta(days=2), now - timedelta(days=2)),
            ("recent", portal["org_id"], now - timedelta(hours=1), None),
            ("foreign", foreign_id, now - timedelta(days=2), None),
        ]:
            db.add(CitizenReportVerification(
                token_hash=hash_api_key(key), org_id=org_id, major_incident_id=portal["lage_id"],
                pin_hash="a" * 64, phone="+43664", form_data={}, expires_at=expiry, consumed_at=consumed,
            ))
        db.commit()
    # Opportunistisches Aufraeumen beim Anlegen betrifft ausschliesslich die eigene Org.
    _submit(client, portal)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(CitizenReportVerification, hash_api_key("expired")) is None
        assert db.get(CitizenReportVerification, hash_api_key("consumed")) is None
        assert db.get(CitizenReportVerification, hash_api_key("foreign")) is not None
        assert db.get(CitizenReportVerification, hash_api_key("recent")) is not None
        assert purge_citizen_verifications(db, portal["org_id"], now) == 0
    # Bestehender Leader-Retention-Lauf bereinigt auch die anderen Organisationen.
    purge_all_sms_logs()
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(CitizenReportVerification, hash_api_key("foreign")) is None
        assert db.get(CitizenReportVerification, hash_api_key("recent")) is not None


@pytest.mark.parametrize("foreign_org", [False, True])
def test_verification_is_bound_to_portal_lage_and_org(client, portal, foreign_org):
    url, _, pin = _submit(client, portal)
    other_token = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org_id = portal["org_id"]
        if foreign_org:
            org = FireDept(slug=other_token, name="Andere Org", color="#123456", bos="Feuerwehr")
            db.add(org)
            db.flush()
            org_id = org.id
        db.add(MajorIncident(org_id=org_id, name="Andere Lage", public_token=other_token))
        db.commit()
    foreign_url = url.replace(portal["token"], other_token)
    assert client.get(foreign_url).status_code == 410
    assert _confirm(client, foreign_url, pin).status_code == 410
    assert _reports(portal) == []
    assert _confirm(client, url, pin).status_code == 200
    assert len(_reports(portal)) == 1


def test_mismatched_verification_org_is_rejected(client, portal):
    url, verify_token, pin = _submit(client, portal)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=uuid4().hex, name="Fremde Org", color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()
        row = db.get(CitizenReportVerification, hash_api_key(verify_token))
        row.org_id = org.id
        db.commit()
        foreign_id = org.id
    try:
        assert client.get(url).status_code == 410
        assert _confirm(client, url, pin).status_code == 410
        assert _reports(portal) == []
    finally:
        with SessionLocal() as db:
            db.query(CitizenReportVerification).filter_by(org_id=foreign_id).delete()
            db.commit()


def test_parallel_wrong_pins_increment_without_lost_updates(client, portal, monkeypatch):
    url, verify_token, pin = _submit(client, portal)
    barrier = Barrier(5)
    load = ui_major_incident._citizen_verification

    def concurrent_load(*args):
        result = load(*args)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(ui_major_incident, "_citizen_verification", concurrent_load)
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: _confirm(client, url, "9999"), range(5)))
    assert all(response.status_code in (200, 410) for response in responses)
    monkeypatch.setattr(ui_major_incident, "_citizen_verification", load)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.get(CitizenReportVerification, hash_api_key(verify_token)).attempts == 5
    assert _confirm(client, url, pin).status_code == 410
    assert _reports(portal) == []
