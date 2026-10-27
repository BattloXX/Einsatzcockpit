"""P6: Cloud-Abnahme ab authentifiziertem Gateway-HTTP bis zur Alarm-Outbox.

Reale Parser-/Apply-/Recovery-Logik, nur externe Hintergrundwirkungen als Spies.
Die Leitstellennummer wird pro Test ersetzt; der restliche Fixture-Text bleibt gleich.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import and_

from app.core.async_bridge import run_async
from app.core.audit import write_incident_change
from app.core.security import hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal, get_db
from app.models.gateway import AlarmIngest, Gateway
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog
from app.models.incident_alarm_field import IncidentAlarmField
from app.models.master import AlarmType, FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt
from app.models.user import ApiKey, Role, User, UserRole
from app.routers.gateway_api import router
from app.services import alarm_apply_service as apply_service
from app.services import alarm_ingest_recovery as recovery
from app.services import serial_alarm_service as serial
from tests.test_gateway_druckregeln_render import _login

DATEN = Path(__file__).parent / "data" / "alarm_wut"
CHANNELS = {"sms", "push", "teams", "mail"}


@contextmanager
def database():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        yield db


def now():
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def offene_jobs(setup_db):
    # Wie test_alarm_outbox: Worker duerfen keine offenen Altjobs mitverarbeiten.
    def cleanup():
        with database() as db:
            for job in db.query(IncidentAlarmJob).filter(
                IncidentAlarmJob.status.in_(("pending", "sending", "retry")),
            ).all():
                db.delete(job)
            db.commit()
    cleanup()
    yield
    cleanup()


@pytest.fixture
def cloud(monkeypatch):
    calls = []
    token = uuid4().hex
    number = f"f{uuid4().int % 100000000:08d}"
    with database() as db:
        org = FireDept(slug="abnahme-" + uuid4().hex, name="Abnahme", city="Wolfurt",
                       street="Frühlingstraße", timezone="Europe/Vienna")
        db.add(org)
        db.flush()
        org_id = org.id
        db.add_all([AlarmType(org_id=org_id, code=code, label=code, category="F")
                    for code in ("F2", "F3", "F11", "F14", "SONSTIGE", "T1")])
        db.add_all([Objekt(org_id=org_id, name="bad lerchenau", ort="Lauterach"),
                    Objekt(org_id=org_id, name="Katalogobjekt Buch", ort="Buch")])
        gateway = Gateway(org_id=org_id, name="WUT Abnahme", device_token_hash=hash_api_key(token))
        db.add(gateway)
        db.commit()
        gateway_id = gateway.id

    def record(name):
        return lambda *args, **kwargs: calls.append((name, args[0]))

    for name, label in (("autoprint_alarm_background", "alarm_print"),
                        ("autoprint_incident_background", "incident_print")):
        monkeypatch.setattr("app.services.print_dispatcher." + name, record(label))
    monkeypatch.setattr("app.services.objekt_matching_service.match_incident_background", record("match"))
    monkeypatch.setattr("app.routers.api_v1._geocode_incident", record("geocode"))

    async def broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.broadcast.broadcast_org", broadcast)
    # Recovery darf keine absichtlich fehlerhaften Rohdaten anderer Tests aufnehmen.
    eligible = recovery._eligible
    monkeypatch.setattr(recovery, "_eligible", lambda time: and_(
        eligible(time), AlarmIngest.org_id == org_id,
    ))

    def session():
        with database() as db:
            yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = session
    with TestClient(app) as http:
        def post(data, *, client=None, bearer=None):
            response = (client or http).post("/api/v1/gateway/alarms", json=data,
                                            headers={"Authorization": f"Bearer {bearer or token}"})
            assert response.status_code == 200, response.text
            return response.json()
        yield SimpleNamespace(post=post, org_id=org_id, gateway_id=gateway_id,
                              number=number, calls=calls, token=token)


def payload(cloud, fixture="anhang_wolfurt", *, sequence=1, text=None, **values):
    if text is None:
        original = (DATEN / f"{fixture}.txt").read_text()
        text = cloud.number + original[original.index(" "):]
    data = {"raw_text": text, "raw_bytes_b64": base64.b64encode(text.encode("cp850")).decode(),
            "charset": "cp850", "sequence": sequence, "received_at": "2026-10-27T10:00:00+01:00",
            "incomplete": False}
    return {**data, **values}


def state(cloud, result):
    with database() as db:
        row = db.query(AlarmIngest).filter_by(org_id=cloud.org_id, id=result["ingest_id"]).one()
        inc = db.query(Incident).filter_by(primary_org_id=cloud.org_id, id=row.einsatz_id).one()
        db.expunge_all()
        return row, inc


def jobs(cloud, incident_id):
    with database() as db:
        return [(r.channel, r.variant) for r in db.query(IncidentAlarmJob).filter_by(
            org_id=cloud.org_id, incident_id=incident_id).order_by(IncidentAlarmJob.id)]


def assert_single(cloud, result):
    row, inc = state(cloud, result)
    assert row.process_status == "applied" and row.process_attempts == 1
    with database() as db:
        assert db.query(Incident).filter_by(primary_org_id=cloud.org_id).count() == 1
    assert jobs(cloud, inc.id) == [(c, "") for c in ("sms", "push", "teams", "mail")]
    assert sorted(name for name, _ in cloud.calls) == ["alarm_print", "geocode", "incident_print", "match"]
    return row, inc


def test_01_normalalarm_wolfurt(cloud):
    data = payload(cloud)
    result = cloud.post(data)
    row, inc = assert_single(cloud, result)
    assert inc.lis_operation_number == cloud.number and inc.alarm_type_code == "F2"
    assert (inc.address_city, inc.address_street, inc.address_no) == ("Wolfurt", "Frühlingstraße", None)
    assert inc.started_at == datetime(2026, 10, 6, 0, 15)  # noqa: DTZ001
    assert row.raw_bytes == data["raw_text"].encode("cp850")
    assert row.received_at_gateway == datetime(2026, 10, 27, 9)  # noqa: DTZ001
    assert row.parsed["adresszusatz"] == "naehe hnr 11"
    assert row.parsed["meldebild"] == "brand container"
    assert row.parsed["zusatzinfo"] == ["notrufer nicht mehr vorort"]


def test_02_mehrere_codes_buch(cloud):
    row, inc = assert_single(cloud, cloud.post(payload(cloud, "anhang_buch")))
    assert inc.alarm_type_code == "F3" and inc.address_city == "Buch"
    assert row.parsed["stichwort"] == "f3" and row.parsed["weitere_codes"] == ["r1"]
    # Ohne Ortsteil Koss im DB-Katalog bleibt die Lage sicher im Rest erhalten.
    assert "koss oestlich im wald" in " ".join(filter(None, [
        row.parsed["lagezusatz"], row.parsed["rest_text"],
    ]))


def test_03_nachbargemeinde_lauterach(cloud):
    row, inc = assert_single(cloud, cloud.post(payload(cloud, "anhang_lauterach")))
    assert (inc.address_city, inc.address_street, inc.address_no) == ("Lauterach", "bad lerchenau", "12")
    assert row.parsed["sonderkennzeichen"] == ["_drehleiter"]
    assert row.parsed["weitere_codes"] == ["r2"]


def test_04_fehlende_hausnummer(cloud):
    row, inc = assert_single(cloud, cloud.post(payload(cloud, "ohne_hausnummer")))
    assert "hausnummer_fehlt" in row.parsed["hinweise"] and inc.address_no is None
    assert inc.address_street == "Frühlingstraße"


def test_05_vertauschte_reihenfolge(cloud):
    row, inc = assert_single(cloud, cloud.post(payload(cloud, "reihenfolge")))
    assert (inc.address_city, inc.address_street, inc.address_no) == ("Wolfurt", "Frühlingstraße", "10")
    assert row.parsed["status"] == "ok"


def test_06_bma_melder_objektmatching(cloud):
    row, inc = assert_single(cloud, cloud.post(payload(cloud, "bma")))
    assert row.parsed["bma"] == ["1234"] and row.parsed["melder"] == "2/3"
    assert row.parsed["melderbereich"] == "7" and inc.alarm_type_code == "F14"
    assert ("match", inc.id) in cloud.calls


def test_07_identischer_inhalt_auch_nach_tagen(cloud):
    first = cloud.post(payload(cloud))
    original_jobs = jobs(cloud, first["einsatz_id"])
    cloud.calls.clear()
    for seq in (2, 3):
        with database() as db:
            row = db.query(AlarmIngest).filter_by(org_id=cloud.org_id, id=first["ingest_id"]).one()
            row.received_at = now() - timedelta(days=10)
            db.commit()
        result = cloud.post(payload(cloud, sequence=seq, received_at="2026-11-06T10:00:00+01:00"))
        row, _ = state(cloud, result)
        assert row.dedup_action == "duplicate" and row.version_no == 1
        assert result["einsatz_id"] == first["einsatz_id"]
    assert cloud.calls == [] and jobs(cloud, first["einsatz_id"]) == original_jobs
    with database() as db:
        assert db.query(Incident).filter_by(primary_org_id=cloud.org_id).count() == 1


def test_08_folgenachrichten_nachalarm_und_manuelle_adresse(cloud):
    text = cloud.number + " f2 wolfurt fruehlingstrasse 7 > brand"
    first = cloud.post(payload(cloud, text=text))
    incident_id = first["einsatz_id"]
    with database() as db:
        inc = db.query(Incident).filter_by(primary_org_id=cloud.org_id, id=incident_id).one()
        old = inc.address_street
        inc.address_street = "Manuelle Gasse"
        key = ApiKey(org_id=cloud.org_id, key_hash=hash_api_key(uuid4().hex), label="Korrektur")
        db.add(key)
        db.flush()
        write_incident_change(db, inc.id, "incident.address_updated", "incident", inc.id,
                              {"address_street": old}, {"address_street": inc.address_street},
                              api_key_id=key.id)
        db.commit()
        field = db.query(IncidentAlarmField).filter_by(org_id=cloud.org_id, incident_id=inc.id,
                                                      field="address_street").one()
        assert field.source == "manual"
    cloud.calls.clear()
    second = cloud.post(payload(cloud, sequence=2, text=text + " > Person im Keller"))
    row, inc = state(cloud, second)
    assert row.version_no == 2 and row.dedup_action == "merged"
    assert "--- Nachtrag v2 " in inc.report_text and "Person im Keller" in inc.report_text
    assert inc.address_street == "Manuelle Gasse" and len(jobs(cloud, inc.id)) == 4
    with database() as db:
        assert db.query(IncidentLog).filter(IncidentLog.incident_id == inc.id,
            IncidentLog.text.like("Alarmupdate v2: Meldebild ergaenzt%")).count() == 1
    for seq, code, suffix, count in ((3, "f2,r2", "neu", 8), (4, "f2,r2", "nochmals", 8),
                                      (5, "f3,r2", "erhoeht", 12)):
        result = cloud.post(payload(cloud, sequence=seq, text=text.replace("f2", code) + " > " + suffix))
        _, inc = state(cloud, result)
        assert inc.id == incident_id and inc.address_street == "Manuelle Gasse"
        assert len(jobs(cloud, inc.id)) == count
    assert inc.alarm_type_code == "F3" and cloud.calls == []
    variants = jobs(cloud, inc.id)
    for variant in ("", "nach:code:r2", "nach:stichwort:F3"):
        assert {channel for channel, v in variants if v == variant} == CHANNELS


def test_09_unvollstaendig_unklar_bleibt_erhalten(cloud):
    data = payload(cloud, "abgeschnitten", incomplete=True)
    row, inc = state(cloud, cloud.post(data))
    # Aktueller Fallback: unklar verhindert die Anlage nicht; SONSTIGE + Rohtext.
    assert row.parsed["status"] == "unklar" and row.parse_status == "parse_failed"
    assert row.parsed["rest_text"] == "f" and row.raw_text == data["raw_text"]
    assert row.raw_bytes == data["raw_text"].encode("cp850")
    assert inc.alarm_type_code == "SONSTIGE" and inc.reason == data["raw_text"]
    assert inc.lis_operation_number == cloud.number and row.process_status == "applied"


@pytest.mark.parametrize("parallel_first", [False, True], ids=["wiederholung", "erstempfang"])
def test_10_spool_sequenz_sequentiell_parallel_und_vertauscht(cloud, parallel_first):
    data = payload(cloud, sequence=100)
    first = None if parallel_first else cloud.post(data)
    barrier = Barrier(4)

    def resend(_):
        barrier.wait(timeout=10)
        return cloud.post(data)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(resend, range(4)))
    if parallel_first:
        assert sum(not r["duplicate"] for r in results) == 1
        first = next(r for r in results if not r["duplicate"])
    else:
        assert all(result == {**first, "duplicate": True} for result in results)
    assert {r["ingest_id"] for r in results} == {first["ingest_id"]}
    for _ in range(2):
        assert cloud.post(data) == {**first, "duplicate": True}
    assert_single(cloud, first)
    with database() as db:
        assert db.query(AlarmIngest).filter_by(org_id=cloud.org_id, gateway_id=cloud.gateway_id).count() == 1
    for seq in (102, 101):
        result = cloud.post(payload(cloud, sequence=seq))
        row, _ = state(cloud, result)
        assert row.sequence == seq and row.dedup_action == "duplicate"
    assert_single(cloud, first)


def defer_request(cloud, monkeypatch):
    original = serial.process_ingest
    with monkeypatch.context() as patch:
        patch.setattr(serial, "process_ingest", lambda db, ingest_id, **kw: db.get(AlarmIngest, ingest_id))
        result = cloud.post(payload(cloud))
    assert result["process_status"] == "stored" and result["einsatz_id"] is None
    with database() as db:
        row = db.query(AlarmIngest).filter_by(org_id=cloud.org_id, id=result["ingest_id"]).one()
        row.received_at = now() - timedelta(minutes=2)
        db.commit()
    return result, original


def process_worker(cloud, ingest_id, process, barrier):
    with database() as db:
        def after(row):
            tasks = BackgroundTasks()
            apply_service.nachlauf(db, row, org_id=cloud.org_id, background_tasks=tasks,
                                   base_url="https://abnahme.test")
            run_async(tasks)
        barrier.wait(timeout=10)
        process(db, ingest_id, org_id=cloud.org_id, on_applied=after)


def test_11_zwei_worker_ein_claim_ein_nachlauf(cloud, monkeypatch):
    result, original = defer_request(cloud, monkeypatch)
    barrier = Barrier(2)

    def worker(_):
        process_worker(cloud, result["ingest_id"], original, barrier)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker, range(2)))
    assert_single(cloud, result)


def test_11_recovery_und_request_konkurrieren(cloud, monkeypatch):
    original = serial.process_ingest
    barrier = Barrier(2)

    def request_process(db, ingest_id, **kwargs):
        with database() as other:
            row = other.query(AlarmIngest).filter_by(org_id=cloud.org_id, id=ingest_id).one()
            row.received_at = now() - timedelta(minutes=2)
            other.commit()
        barrier.wait(timeout=10)
        return original(db, ingest_id, **kwargs)

    def recover_process(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    # Recovery wird erst gestartet, nachdem store_raw committed hat.
    from threading import Event
    stored = Event()
    store_raw = serial.store_raw

    def store(*args, **kwargs):
        result = store_raw(*args, **kwargs)
        with database() as db:
            row = db.query(AlarmIngest).filter_by(org_id=cloud.org_id, id=result[0].id).one()
            row.received_at = now() - timedelta(minutes=2)
            db.commit()
        stored.set()
        return result

    monkeypatch.setattr(serial, "store_raw", store)
    monkeypatch.setattr(serial, "process_ingest", request_process)
    monkeypatch.setattr(recovery, "process_ingest", recover_process)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(cloud.post, payload(cloud))
        assert stored.wait(timeout=10)
        recover = pool.submit(recovery.run_alarm_ingest_recovery_once)
        result = request.result(timeout=30)
        recover.result(timeout=30)
    assert_single(cloud, result)


@pytest.mark.parametrize("wut_first", [False, True], ids=["eus-zuerst", "wut-zuerst"])
def test_12_eus_dedup_beide_richtungen(cloud, client, wut_first):
    key = uuid4().hex
    with database() as db:
        db.add(ApiKey(key_hash=hash_api_key(key), label="Abnahme", org_id=cloud.org_id))
        db.commit()
    if wut_first:
        wut = cloud.post(payload(cloud))
    response = client.post("/api/v1/einsatz", headers={"X-API-Key": key}, json={
        "Key": uuid4().hex, "Stufe": "f2", "Art": "F", "Uebung": False,
        "AlarmDatumZeit": "2026-10-06T02:15:00", "Leitstellennummer": cloud.number,
    })
    assert response.status_code == 200, response.text
    if not wut_first:
        wut = cloud.post(payload(cloud))
    assert response.json()["id"] == wut["einsatz_id"]
    with database() as db:
        assert db.query(Incident).filter_by(primary_org_id=cloud.org_id).count() == 1
    assert {c for c, _ in jobs(cloud, wut["einsatz_id"])} == CHANNELS


@pytest.mark.parametrize("source", ["dibos", "lis"])
def test_12_dibos_lis_und_cross_org(cloud, source):
    token = uuid4().hex
    with database() as db:
        existing = Incident(primary_org_id=cloud.org_id, lis_operation_number=cloud.number,
                            lis_operation_id=uuid4().hex if source == "lis" else None,
                            alarm_type_code="F2", reason=source, status="closed")
        org = FireDept(slug="abnahme-b-" + uuid4().hex, name="B")
        db.add_all([existing, org])
        db.flush()
        other_org_id, existing_id = org.id, existing.id
        db.add_all([AlarmType(org_id=org.id, code="F2", label="F2"),
                    Gateway(org_id=org.id, name="B", device_token_hash=hash_api_key(token))])
        db.commit()
    result = cloud.post(payload(cloud))
    row, inc = state(cloud, result)
    assert inc.id == existing_id and inc.reason == source and row.dedup_action == "merged"
    foreign = cloud.post(payload(cloud), bearer=token)
    assert foreign["einsatz_id"] != existing_id
    with database() as db:
        assert db.query(Incident).filter_by(primary_org_id=other_org_id,
            lis_operation_number=cloud.number, id=foreign["einsatz_id"]).count() == 1
        assert db.query(Incident).filter_by(primary_org_id=cloud.org_id).count() == 1


def test_13_parser_exception_gateway_fallback_http_200(cloud, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Abnahme Parserfehler")
    monkeypatch.setattr(serial, "parse_alarm", fail)
    data = payload(cloud, parsed={"alarm_type_code": "F2", "street": "Fallbackgasse",
                                  "house_no": "4", "city": "Wolfurt"})
    row, inc = assert_single(cloud, cloud.post(data))
    assert row.raw_text == data["raw_text"] and row.raw_bytes == data["raw_text"].encode("cp850")
    assert row.parsed["hinweise"] == ["parser_fehler"]
    assert row.process_error == "Cloud-Parser: Abnahme Parserfehler"
    assert (inc.address_street, inc.address_no) == ("Fallbackgasse", "4")


def test_13_verarbeitungsfehler_recovery(cloud, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Abnahme Applyfehler")
    data = payload(cloud)
    with monkeypatch.context() as patch:
        patch.setattr(serial, "_create_or_link_incident", fail)
        result = cloud.post(data)
    assert result["process_status"] == "error" and result["einsatz_id"] is None
    with database() as db:
        row = db.query(AlarmIngest).filter_by(org_id=cloud.org_id, id=result["ingest_id"]).one()
        assert row.process_error == "Abnahme Applyfehler" and row.raw_bytes == data["raw_text"].encode("cp850")
        row.parsed_at = now() - timedelta(minutes=2)
        db.commit()
    recovery.run_alarm_ingest_recovery_once()
    recovery.run_alarm_ingest_recovery_once()
    row, inc = state(cloud, result)
    assert row.process_status == "applied" and row.process_attempts == 2
    assert row.process_error is None and inc.lis_operation_number == cloud.number
    assert len(jobs(cloud, inc.id)) == 4 and len(cloud.calls) == 4


def test_14_altes_gateway(cloud):
    data = {"raw_text": "Altgateway " + uuid4().hex, "charset": "cp850",
            "parsed": {"alarm_type_code": "F2", "street": "Altgasse", "house_no": "9", "city": "Wolfurt"}}
    row, inc = assert_single(cloud, cloud.post(data))
    assert row.sequence is None and row.raw_bytes is None and row.received_at_gateway is None
    assert row.parsed["gateway_parsed"] == data["parsed"]
    assert (inc.alarm_type_code, inc.address_street, inc.address_no) == ("F2", "Altgasse", "9")


def test_15_diagnose_normalalarm_org_admin_und_cross_org(cloud, client):
    result = cloud.post(payload(cloud))
    with database() as db:
        flag = db.get(SystemSettings, "gateway_module_enabled")
        old = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="gateway_module_enabled", value="true")
            db.add(flag)
        flag.value = "true"
        other = FireDept(slug="ui-b-" + uuid4().hex, name="UI B")
        db.add(other)
        db.flush()
        users = []
        role = db.query(Role).filter_by(code="org_admin").one()
        for org_id in (cloud.org_id, other.id):
            db.add(OrgSettings(org_id=org_id, gateway_module_enabled=True))
            user = User(username=uuid4().hex, display_name="Abnahme Admin", org_id=org_id,
                        password_hash=hash_password("Test1234!"), active=True)
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=role.id))
            users.append(user.username)
        db.commit()
    base = f"/gateway/{cloud.gateway_id}/alarmempfang"
    try:
        assert _login(client, users[0], "Test1234!").status_code in (302, 303)
        response = client.get(base)
        assert response.status_code == 200 and cloud.number in response.text
        assert f'/alarmempfang/{result["ingest_id"]}' in response.text
        detail = client.get(f'{base}/{result["ingest_id"]}')
        assert detail.status_code == 200 and "brand container" in detail.text
        assert _login(client, users[1], "Test1234!").status_code in (302, 303)
        assert client.get(base).status_code == 404
        assert client.get(f'{base}/{result["ingest_id"]}').status_code == 404
    finally:
        with database() as db:
            flag = db.get(SystemSettings, "gateway_module_enabled")
            if old is None:
                db.delete(flag)
            else:
                flag.value = old
            db.commit()
