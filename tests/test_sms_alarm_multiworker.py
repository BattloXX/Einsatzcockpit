"""Alarm-Outbox -> Prozess A -> Socket in Prozess B -> persistenter Alarmstatus.

REDIS_RESTART_TEST_URL und REDIS_RESTART_TEST_CONTAINER aktivieren zusaetzlich
einen echten Neustart eines ausschliesslich fuer diesen Test bestimmten Containers.
Das Modem wird durch einen Protokoll-Peer ersetzt; dies ist keine Geraeteabnahme.
"""

import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.security import hash_api_key
from app.main import app
from app.models.incident import Incident, IncidentAlarmJob
from app.models.master import FireDept, Member, OrgSettings
from app.models.sms import SmsDispatchJob, SmsEinsatzinfoRecipient, SmsLog
from app.models.user import SmsGatewayToken
from app.services import gateway_presence as presence
from app.services import sms_outbox, ws_bus
from tests.test_print_agent_ws import _recv

pytestmark = pytest.mark.usefixtures("sms_outbox_state")

CODE = """
import asyncio,json,sys
from app.services import alarm_outbox, gateway_routing, sms_outbox, ws_bus
from app.models.incident import IncidentAlarmJob
assert not gateway_routing._sockets
org,iid=json.loads(sys.stdin.readline())
async def run():
    await ws_bus.start()
    try: await alarm_outbox.process_incident_alarm(iid)
    finally: await ws_bus.stop()
    with sms_outbox.database() as db:
        job=db.query(IncidentAlarmJob).filter_by(org_id=org,incident_id=iid,channel='sms').one()
        return {'status':job.status}
print(json.dumps(asyncio.run(run())),flush=True)
"""


@pytest.mark.parametrize("restart_redis", [False, True])
@pytest.mark.parametrize("accepted", [True, False])
def test_alarm_across_processes_requires_provider_acceptance(monkeypatch, restart_redis, accepted):
    redis_url = os.environ.get("REDIS_RESTART_TEST_URL", "") if restart_redis else ""
    container = os.environ.get("REDIS_RESTART_TEST_CONTAINER", "")
    if restart_redis and (not redis_url or not container):
        pytest.skip("dedizierter Redis-Neustart-Testcontainer nicht konfiguriert")
    monkeypatch.setattr(settings, "REDIS_URL", redis_url)
    token, org_id = uuid4().hex, None
    stopped = False
    process = None
    with sms_outbox.database() as db:
        org = FireDept(slug="alarm-phase3-" + uuid4().hex, name="Multiworker SMS Test")
        db.add(org)
        db.flush()
        org_id = org.id
        member = Member(org_id=org_id, firstname="Test", lastname="Alarm", phone="+43000000000", active=True)
        db.add(member)
        db.flush()
        db.add(OrgSettings(org_id=org_id, einsatzinfo_sms_enabled=True))
        db.add(SmsEinsatzinfoRecipient(org_id=org_id, member_id=member.id))
        db.add(SmsGatewayToken(org_id=org_id, token_hash=hash_api_key(token), label="Socket worker B"))
        incident = Incident(
            primary_org_id=org_id, alarm_type_code="B2", status="active", address_city="Testort", is_exercise=False
        )
        db.add(incident)
        db.flush()
        incident_id = incident.id
        db.add(
            IncidentAlarmJob(
                org_id=org_id,
                incident_id=incident.id,
                channel="sms",
                status="pending",
                next_attempt_at=presence.utc(),
                context={},
            )
        )
        db.commit()
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/sms-gateway", headers={"Authorization": "Bearer " + token}) as socket:
                _recv(socket, "config")
                socket.send_json({"type": "hello", "capabilities": ["sms-transaction-v2"], "open_jobs": []})
                socket.send_json({"type": "ping"})
                _recv(socket, "pong")
                environment = {**os.environ, "REDIS_URL": redis_url}
                process = subprocess.Popen(
                    [sys.executable, "-c", CODE],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                )
                process.stdin.write(json.dumps([org_id, incident_id]) + "\n")
                process.stdin.flush()
                prepare = _recv(socket, "sms.prepare")
                wire = {k: prepare[k] for k in ("id", "job_id", "generation")}
                if restart_redis:
                    subprocess.run(
                        ["docker", "stop", "--time", "0", container], check=True, capture_output=True, timeout=20
                    )
                    stopped = True
                socket.send_json({**wire, "type": "sms.ack"})
                socket.send_json({**wire, "type": "sms.ack"})
                _recv(socket, "sms.commit")
                with sms_outbox.database() as db:
                    job = db.query(SmsDispatchJob).filter_by(org_id=org_id, id=wire["job_id"]).one()
                    assert job.status == "acknowledged" and job.accepted_at is None
                    alarm = db.query(IncidentAlarmJob).filter_by(org_id=org_id, incident_id=incident_id).one()
                    assert alarm.status != "sent"
                if accepted:
                    socket.send_json({**wire, "type": "sms.attempt"})
                    result = {
                        **wire,
                        "type": "sms.result",
                        "ok": True,
                        "provider_accepted": True,
                        "outcome": "sent",
                        "attempted": True,
                        "delivery_verified": False,
                    }
                    socket.send_json(result)
                    socket.send_json(result)
                    assert _recv(socket, "sms.result.ack")["applied"] is True
                    assert _recv(socket, "sms.result.ack")["applied"] is False
                else:
                    socket.close()  # Freigabe moeglicherweise beim Modem, Ergebnis verloren.
                output, error = process.communicate(timeout=30)
                assert process.returncode == 0, error
                assert json.loads(output)["status"] in (("sent",) if accepted else ("retry", "uncertain"))
                if restart_redis:
                    subprocess.run(["docker", "start", container], check=True, capture_output=True, timeout=20)
                    stopped = False
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and not ws_bus.status()["connected"]:
                        time.sleep(0.1)
                    assert ws_bus.status()["connected"]
            if not accepted:
                with sms_outbox.database() as db:
                    db.query(SmsDispatchJob).filter_by(org_id=org_id).update(
                        {"lease_until": presence.utc() - timedelta(seconds=1)}, synchronize_session=False
                    )
                    db.query(IncidentAlarmJob).filter_by(org_id=org_id, incident_id=incident_id).update(
                        {"next_attempt_at": presence.utc() - timedelta(seconds=1)}, synchronize_session=False
                    )
                    db.commit()
                sms_outbox.sweep()
                from app.services.alarm_outbox import process_incident_alarm

                client.portal.call(process_incident_alarm, incident_id)
            with sms_outbox.database() as db:
                job = db.query(SmsDispatchJob).filter_by(org_id=org_id).one()
                alarm = db.query(IncidentAlarmJob).filter_by(org_id=org_id, incident_id=incident_id).one()
                assert job.status == alarm.status == ("sent" if accepted else "uncertain")
                assert job.generation == 1
                log = db.query(SmsLog).filter_by(org_id=org_id, incident_id=incident_id).one()
                assert log.success_count == int(accepted)
    finally:
        if stopped:
            subprocess.run(["docker", "start", container], check=True, capture_output=True, timeout=20)
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
