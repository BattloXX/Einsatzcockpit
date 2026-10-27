"""P4a: Nummernzuordnung, Versionen, Savepoints und Inhaltsdedup."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import inspect

from app.core.security import generate_api_key, hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident, IncidentLog
from app.models.master import AlarmType, FireDept
from app.models.user import ApiKey
from app.services import alarm_ingest_recovery as recovery
from app.services import serial_alarm_service as service
from tests import test_gateway_alarm_apply_nachlauf as gateway_tests

alarm_api = gateway_tests.alarm_api


@pytest.fixture
def db(setup_db):
    with SessionLocal() as session:
        set_tenant_context(session, None)
        org = FireDept(slug="apply-" + uuid4().hex, name="Apply", city="Wolfurt")
        session.add(org)
        session.flush()
        session.add_all([AlarmType(org_id=org.id, code=code, label=code)
                         for code in ("T1", "T2", "SONSTIGE")])
        gw = Gateway(org_id=org.id, name="Apply")
        session.add(gw)
        session.commit()
        session.info["gateway"] = gw
        yield session
        session.rollback()


def nummer():
    return f"f{uuid4().int % 100000000:08d}"


def store(db, text, *, parsed=None, gw=None):
    gw = gw or db.info["gateway"]
    sequence = db.query(AlarmIngest).filter(AlarmIngest.org_id == gw.org_id,
                                          AlarmIngest.gateway_id == gw.id).count() + 1
    row, created = service.store_raw(db, org_id=gw.org_id, gateway_id=gw.id,
                                    raw_text=text, sequence=sequence,
                                    parsed=parsed, parse_status="parse_failed")
    assert created
    return row


def apply(db, text, **kwargs):
    row = store(db, text, **kwargs)
    result = service.process_ingest(db, row.id, org_id=row.org_id)
    assert result.process_status == "applied", result.process_error
    return result


def incident(db, row):
    return db.query(Incident).filter(Incident.primary_org_id == row.org_id,
                                     Incident.id == row.einsatz_id).one()


def test_new_duplicate_after_days_and_changed_message(db, caplog):
    text = nummer() + " t1 Wolfurt Testgasse 7 > wasser"
    first = apply(db, text)
    inc = incident(db, first)
    assert inc.lis_operation_number == first.leitstellennummer == text.split()[0]
    assert first.version_no == 1 and first.dedup_action == "created"
    before = {attr.key: getattr(inc, attr.key) for attr in inspect(Incident).column_attrs}
    logs = db.query(IncidentLog).filter(IncidentLog.incident_id == inc.id).count()
    first.received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)
    db.commit()
    with caplog.at_level("INFO", logger="einsatzleiter.alarm"):
        duplicate = apply(db, text)
    assert duplicate.dedup_action == "duplicate" and duplicate.version_no == 1
    assert "Identischer Alarm-Inhalt" in caplog.text
    changed = apply(db, text + " > weitere Information")
    assert changed.dedup_action == "merged" and changed.version_no == 2
    assert first.einsatz_id == duplicate.einsatz_id == changed.einsatz_id
    db.refresh(inc)
    assert inc.report_text.startswith(before["report_text"] + "\n\n--- Nachtrag v2 ")
    assert inc.report_text.endswith("weitere Information")
    assert db.query(IncidentLog).filter(IncidentLog.incident_id == inc.id).count() > logs
    assert db.query(Incident).filter(Incident.primary_org_id == first.org_id).count() == 1


@pytest.mark.parametrize("source", ["dibos", "lis"])
def test_existing_closed_source_incident_links_by_number(db, source):
    number = nummer()
    gw = db.info["gateway"]
    existing = Incident(primary_org_id=gw.org_id, lis_operation_number=number,
                        lis_operation_id="guid" if source == "lis" else None,
                        status="closed", alarm_type_code="T2", reason=source)
    db.add(existing)
    db.commit()
    row = apply(db, number + " t1 > neue Nachricht")
    assert row.einsatz_id == existing.id and row.dedup_action == "merged"
    assert row.version_no == 1 and existing.reason == source and existing.status == "closed"


def test_eus_rest_incident_links(db, client):
    gw = db.info["gateway"]
    raw = generate_api_key()
    db.add(ApiKey(key_hash=hash_api_key(raw), label="Apply", org_id=gw.org_id))
    db.commit()
    number = nummer()
    response = client.post("/api/v1/einsatz", headers={"X-API-Key": raw}, json={
        "Key": uuid4().hex, "Stufe": "t1", "Art": "T", "Uebung": True,
        "AlarmDatumZeit": "2026-01-01T10:00:00", "Leitstellennummer": number,
    })
    assert response.status_code == 200, response.text
    row = apply(db, number + " t1 > WUT")
    assert row.einsatz_id == response.json()["id"] and row.dedup_action == "merged"
    assert db.query(Incident).filter(Incident.primary_org_id == gw.org_id).count() == 1


def test_insert_race_rolls_back_only_loser_savepoint(db, monkeypatch):
    number = nummer()
    gw = db.info["gateway"]
    # Zweite Session schreibt den Gewinner. Der erste Lookup simuliert den
    # unmittelbar davor gelesenen Stand ohne Treffer; der UNIQUE-Fehler ist echt.
    with SessionLocal() as other:
        set_tenant_context(other, None)
        winner = Incident(primary_org_id=gw.org_id, lis_operation_number=number,
                          alarm_type_code="T1", reason="Gewinner")
        other.add(winner)
        other.commit()
        winner_id = winner.id
    monkeypatch.setattr("app.services.lis.lis_matching.find_matching_incident", lambda *a, **k: None)
    row = apply(db, number + " t1 > Race")
    assert row.einsatz_id == winner_id and row.dedup_action == "merged" and row.version_no == 1
    assert row.process_attempts == 1 and row.parser_version is not None and row.lease_until is None
    assert db.query(Incident).filter(Incident.primary_org_id == gw.org_id).count() == 1
    assert incident(db, row).reason == "Gewinner"


def test_without_number_keeps_heuristic_and_raw_fallback(db):
    parsed = {"alarm_type_code": "T1", "street": "Testgasse", "city": "Wolfurt"}
    first = apply(db, "Unbekannter Rohtext", parsed=parsed)
    second = apply(db, "Zusatztext", parsed=parsed)
    assert first.leitstellennummer is None and incident(db, first).lis_operation_number is None
    assert second.einsatz_id == first.einsatz_id and second.dedup_action == "merged"
    assert (first.version_no, second.version_no) == (1, 2)
    fallback = apply(db, "Unklassifizierbar")
    assert incident(db, fallback).alarm_type_code == "SONSTIGE"
    assert incident(db, fallback).reason == "Unklassifizierbar"


def test_heuristic_link_sets_number_only_when_available(db, monkeypatch):
    parsed = {"alarm_type_code": "T1", "street": "Testgasse", "city": "Wolfurt"}
    first = apply(db, "Unbekannt", parsed=parsed)
    number = nummer()
    linked = apply(db, number + " t1", parsed=parsed)
    assert linked.einsatz_id == first.einsatz_id
    assert incident(db, linked).lis_operation_number == number
    # Kollision beim Nachtragen: die heuristische Zuordnung bleibt erhalten.
    occupied = nummer()
    other = Incident(primary_org_id=first.org_id, lis_operation_number=occupied)
    db.add(other)
    inc = incident(db, first)
    inc.lis_operation_number = None
    db.commit()
    monkeypatch.setattr("app.services.lis.lis_matching.find_matching_incident", lambda *a, **k: inc)
    conflict = apply(db, occupied + " t1", parsed=parsed)
    assert conflict.einsatz_id == inc.id and conflict.dedup_action == "merged"
    assert inc.lis_operation_number is None


def test_same_number_cross_org_creates_own_incident(db):
    text = nummer() + " t1 > Alarm"
    first = apply(db, text)
    org = FireDept(slug="apply-b-" + uuid4().hex, name="B")
    db.add(org)
    db.flush()
    gw = Gateway(org_id=org.id, name="B")
    db.add(gw)
    db.commit()
    second = apply(db, text, gw=gw)
    assert second.einsatz_id != first.einsatz_id and second.dedup_action == "created"
    assert second.version_no == first.version_no == 1
    assert incident(db, second).primary_org_id == org.id


def test_exercise_from_parser(db):
    row = apply(db, nummer() + " t1 > Uebung")
    assert row.parsed["ist_uebung"] is True
    assert incident(db, row).is_exercise is True


def test_gateway_content_duplicate_has_no_second_nachlauf(alarm_api):
    post, _, calls = alarm_api
    text = nummer() + " t1 > Wasser"
    first = post({"raw_text": text, "sequence": 1}).json()
    assert calls
    calls.clear()
    second = post({"raw_text": text, "sequence": 2}).json()
    assert second["process_status"] == "applied" and second["einsatz_id"] == first["einsatz_id"]
    assert gateway_tests.saved(second["ingest_id"]).dedup_action == "duplicate" and calls == []


def test_recovery_content_duplicate_has_no_nachlauf(db, monkeypatch):
    text = nummer() + " t1 > Wasser"
    first = apply(db, text)
    second = store(db, text)
    second.received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    calls = []
    monkeypatch.setattr(recovery.alarm_apply_service, "nachlauf", lambda *a, **k: calls.append(a[1].id))
    recovery.run_alarm_ingest_recovery_once()
    db.refresh(second)
    assert second.einsatz_id == first.einsatz_id and second.dedup_action == "duplicate"
    assert second.process_status == "applied" and calls == []
