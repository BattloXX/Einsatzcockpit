"""ELVis: sichere Modi, Tenant-Grenze, Idempotenz und beide Transporte."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident
from app.models.master import FireDept
from app.services.elvis_event_service import elvis_modus, verarbeite_elvis_event
from app.services.serial_alarm_service import process_ingest
from tests.test_alarm_apply_service import db as db
from tests.test_print_agent_ws import _anlegen, _auth, _recv


def args(**values):
    return {"Nr": f"f{uuid4().int % 100000000:08d}", "ID": uuid4().hex, "ESW": "t1",
            "Einsatz": "Wasserschaden", "Straße": "Hauptstraße", "Hausnummer": "7",
            "Ort": "Wolfurt", **values}


@pytest.fixture
def agent(db):
    gw = db.info["gateway"]
    gw.kind = "windows_agent"
    gw.device_token_hash = hash_api_key("elvis-" + str(gw.id))
    db.commit()
    return gw


@pytest.fixture(autouse=True)
def effects(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.alarm_apply_service.nachlauf", lambda *a, **kw: calls.append(a[1].id))
    monkeypatch.setattr("app.services.alarm_apply_service.melde_ingest_geaendert", lambda *_: None)
    return calls


def mode(db, agent, value):
    agent.wut_config = {"elvis": {"mode": value}, "unrelated": True}
    db.commit()


def send(db, agent, data=None, event_id=None):
    return verarbeite_elvis_event(db, agent, event_id or uuid4().hex, data or args(),
                                 "2026-11-06T13:00:00+01:00")


@pytest.mark.parametrize("value,status,incident", [("aus", "discarded", False),
                                                    ("protokollieren", "logged", False),
                                                    ("alarmquelle", "applied", True)])
def test_modes(db, agent, effects, value, status, incident):
    mode(db, agent, value)
    result = send(db, agent)
    assert result["process_status"] == status
    assert bool(result["einsatz_id"]) is incident
    assert len(effects) == int(incident)
    if result["ingest_id"]:
        row = db.get(AlarmIngest, result["ingest_id"])
        assert row.source == "elvis"
        assert row.received_at_gateway == datetime(2026, 11, 6, 12)  # noqa: DTZ001
        assert process_ingest(db, row.id, org_id=agent.org_id).einsatz_id == result["einsatz_id"]
    else:
        assert db.query(AlarmIngest).filter_by(gateway_id=agent.id).count() == 0


def test_default_and_agent_info_cannot_enable_alarm(db, agent):
    agent.agent_info = {"elvis": {"mode": "alarmquelle"}}
    db.commit()
    assert elvis_modus(agent) == "protokollieren"
    assert send(db, agent)["einsatz_id"] is None


@pytest.mark.parametrize("values", [{"Nr": ""}, {"Nr": "0"}, {"ESW": "TESTEINSATZ"},
                                     {"Einsatz": "Test"}, {"Kommentar": "ein TEST"}, {"ESW": "te\x00st"},
                                     {"Einsatz": "Dummy"}, {"test": True}, {"ESW": "F30"}, {"Nr": "000"},
                                     {"Kommentar": "a" * 2100 + " test"}])
def test_test_events_never_incident_or_alarm(db, agent, effects, values):
    mode(db, agent, "alarmquelle")
    result = send(db, agent, args(**values))
    assert result["test"] is True and result["process_status"] == "logged"
    row = db.get(AlarmIngest, result["ingest_id"])
    # Auch eine manuelle Wiederaufnahme darf einen Test nicht zum Einsatz machen.
    row.process_status = "error"
    db.commit()
    assert process_ingest(db, row.id, org_id=agent.org_id).einsatz_id is None
    assert effects == []


def test_sanitizing_and_size(db, agent):
    data = args(Kommentar="\x00\n\t\x1b" + "a" * 2200, Objekt="b" * 600,
                org_id=98765, evil={"mode": "alarmquelle"})
    result = send(db, agent, data)
    row = db.get(AlarmIngest, result["ingest_id"])
    clean = row.parsed["elvis_args"]
    assert "org_id" not in clean and "evil" not in clean
    assert clean["Kommentar"] == "a" * 2000 and clean["Objekt"] == "b" * 500
    assert row.org_id == agent.org_id
    assert "\x00" not in row.raw_text and "\t" not in row.raw_text
    with pytest.raises(ValueError, match="32 KiB"):
        send(db, agent, args(Kommentar="a" * 32768))
    for event_id in (None, "", 123, "a" * 129):
        with pytest.raises(ValueError, match="event_id"):
            verarbeite_elvis_event(db, agent, event_id, {}, None)


def test_idempotency_event_id_and_hash(db, agent, effects):
    mode(db, agent, "alarmquelle")
    data = args()
    event_id = uuid4().hex
    first = send(db, agent, data, event_id)
    row = db.get(AlarmIngest, first["ingest_id"])
    row.received_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    db.commit()
    retry = send(db, agent, {**data, "Kommentar": "anderer Inhalt"}, event_id)
    assert retry == {**first, "duplicate": True}
    assert len(effects) == 1
    # Inhaltsdedup im selben Einsatz bleibt auch nach dem Zeitfenster wirksam.
    by_hash = send(db, agent, {**data, "Zeit": "anderer Zeitstempel"})
    assert by_hash["duplicate"] is True and by_hash["einsatz_id"] == first["einsatz_id"]
    assert len(effects) == 1


def test_logged_content_dedup_remembers_each_event_id(db, agent):
    data = args()
    first = send(db, agent, data)
    event_id = uuid4().hex
    second = send(db, agent, {**data, "Zeit": "neu"}, event_id)
    assert second["duplicate"] is True
    retry = send(db, agent, args(), event_id)
    assert retry["ingest_id"] == second["ingest_id"] and retry["duplicate"] is True
    assert db.get(AlarmIngest, first["ingest_id"]).raw_text == db.get(AlarmIngest, second["ingest_id"]).raw_text


def test_followup_merges_and_matches_lis_number(db, agent, effects):
    mode(db, agent, "alarmquelle")
    data = args()
    first = send(db, agent, data)
    second = send(db, agent, {**data, "Kommentar": "Weitere Information", "Straße": "Neue Straße"})
    assert second["einsatz_id"] == first["einsatz_id"] and not second["duplicate"]
    inc = db.get(Incident, first["einsatz_id"])
    assert inc.lis_operation_number == data["Nr"] and inc.external_key is None
    assert inc.address_street == "Neue Straße" and "Weitere Information" in inc.report_text
    assert db.query(Incident).filter_by(primary_org_id=agent.org_id).count() == 1


@pytest.mark.parametrize("source", ["dibos", "lis"])
def test_matches_existing_source_by_nr_not_id(db, agent, source):
    mode(db, agent, "alarmquelle")
    data = args()
    inc = Incident(primary_org_id=agent.org_id, lis_operation_number=data["Nr"],
                   lis_operation_id=uuid4().hex if source == "lis" else None,
                   alarm_type_code="T1", reason=source, status="active")
    db.add(inc)
    db.commit()
    assert send(db, agent, data)["einsatz_id"] == inc.id


def test_id_is_not_a_leitstellennummer(db, agent):
    mode(db, agent, "alarmquelle")
    result = send(db, agent, args(Nr="123456", ID="f12345678"))
    assert db.get(Incident, result["einsatz_id"]).lis_operation_number is None


def test_http_auth_limits_and_org_isolation(db, agent, client):
    org = FireDept(slug=uuid4().hex, name="Fremde Organisation")
    db.add(org)
    db.flush()
    other = Gateway(org_id=org.id, name="Fremder Agent", kind="windows_agent",
                    device_token_hash=hash_api_key(uuid4().hex))
    db.add(other)
    db.commit()
    data = {"event_id": uuid4().hex, "args": args(org_id=org.id, Organisation=str(org.id)),
            "org_id": org.id, "gateway_id": other.id}
    path = "/api/v1/gateway/elvis-events"
    headers = _auth("elvis-" + str(agent.id))
    response = client.post(path, json=data, headers=headers)
    assert response.status_code == 200, response.text
    row = db.get(AlarmIngest, response.json()["ingest_id"])
    assert row.org_id == agent.org_id and row.gateway_id == agent.id
    assert db.query(AlarmIngest).filter_by(gateway_id=other.id).count() == 0
    assert client.post(path, json=data, headers=_auth("fremdes-token")).status_code == 401
    agent.kind = "linux"
    db.commit()
    assert client.post(path, json=data, headers=headers).status_code == 401
    agent.kind = "windows_agent"
    db.commit()
    assert client.post(path, content=b"x" * 32769, headers=headers).status_code == 413
    assert client.post(path, content=b"{", headers=headers).status_code == 400
    assert client.post(path, json=[], headers=headers).status_code == 400
    assert client.post(path, json={"event_id": "1", "args": []}, headers=headers).status_code == 400


def test_ws_ack_and_retry(client, db, agent):
    connection = _anlegen(agent.org_id, routen=False)
    data = {"event_id": uuid4().hex, "received_at": "2026-11-06T12:00:00Z", "args": args()}
    with client.websocket_connect("/ws/print-agent", headers=_auth(connection["token"])) as socket:
        config = _recv(socket, "config_sync")
        assert config["payload"]["elvis"] == {"mode": "protokollieren"}
        for _ in range(2):
            socket.send_json({"type": "elvis_event", "id": "outbox-1", "payload": data})
            assert _recv(socket, "ack")["payload"] == {"id": "outbox-1"}
        socket.send_json({"type": "elvis_event", "id": "bad", "payload": {"args": {}}})
        assert _recv(socket, "error")["payload"]["id"] == "bad"
    db.expire_all()
    assert db.query(AlarmIngest).filter_by(gateway_id=connection["gateway_id"]).count() == 1


def test_admin_mode_partial_and_config_push(client, monkeypatch):
    from tests.test_print_agent_ui import _admin, _agent, _push_mitschneiden

    csrf = _admin(client, "elvis-admin-" + uuid4().hex)
    gateway_id = _agent()
    messages = _push_mitschneiden(monkeypatch)
    response = client.get(f"/gateway/{gateway_id}")
    assert response.status_code == 200 and 'id="elvis-panel"' in response.text
    response = client.post(f"/gateway/{gateway_id}/elvis", data={"_csrf": csrf, "mode": "alarmquelle"},
                           headers={"HX-Request": "true"})
    assert response.status_code == 200 and "<html" not in response.text
    assert 'value="alarmquelle" selected' in response.text and "Gespeichert" in response.text
    assert messages[-1][2]["payload"]["elvis"] == {"mode": "alarmquelle"}
    with SessionLocal() as session:
        set_tenant_context(session, None)
        assert session.get(Gateway, gateway_id).wut_config["elvis"]["mode"] == "alarmquelle"
    assert client.get(f"/gateway/{gateway_id}/elvis?events=1").status_code == 200
    assert client.post(f"/gateway/{gateway_id}/elvis", data={"_csrf": csrf, "mode": "falsch"}).status_code == 400


@pytest.mark.parametrize("zeit", ["2026-11-06T13:00:00", "06.11.2026 13:00:00", "2026-11-06T13:00:00+01:00"])
def test_alarmzeit_als_utc(db, agent, zeit):
    mode(db, agent, "alarmquelle")
    result = send(db, agent, args(Zeit=zeit))
    incident = db.get(Incident, result["einsatz_id"])
    assert incident.started_at == datetime(2026, 11, 6, 12)  # noqa: DTZ001


@pytest.mark.parametrize("role", ["readonly", "recorder"])
def test_admin_mode_requires_role(client, role):
    from tests.test_gateway_druckregeln_render import _login, _setup
    from tests.test_print_agent_ui import _agent

    name = "elvis-role-" + uuid4().hex
    _setup(name, role)
    _login(client, name, "Test1234!")
    gateway_id = _agent()
    csrf = client.cookies.get("ec_csrf")
    assert client.get(f"/gateway/{gateway_id}/elvis").status_code == 403
    assert client.post(f"/gateway/{gateway_id}/elvis", data={"_csrf": csrf, "mode": "alarmquelle"}).status_code == 403


def test_admin_mode_csrf_and_foreign_gateway(client):
    from tests.test_print_agent_ui import _admin, _agent

    csrf = _admin(client, "elvis-guard-" + uuid4().hex)
    gateway_id = _agent()
    assert client.post(f"/gateway/{gateway_id}/elvis", data={"mode": "alarmquelle"}).status_code == 403
    other = _anlegen(993996, routen=False)
    path = f"/gateway/{other['gateway_id']}/elvis"
    assert client.get(path).status_code == 404
    assert client.post(path, data={"_csrf": csrf, "mode": "alarmquelle"}).status_code == 404


def test_agent_schluessel_snake_case_werden_abgebildet():
    from app.services.elvis_event_service import _sanitizing

    clean, test = _sanitizing({"nr": "F26012345", "esw": "B1", "strasse": "Hauptstr.", "ort": "Wolfurt"})
    assert clean["Nr"] == "F26012345" and clean["Straße"] == "Hauptstr." and clean["Ort"] == "Wolfurt"
    assert not test
