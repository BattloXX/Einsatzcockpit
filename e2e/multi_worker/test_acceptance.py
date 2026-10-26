import asyncio
import json
import os
import signal
from uuid import uuid4

import httpx

from e2e.multi_worker.lab import eventually
from e2e.multi_worker.peers import Peer


def org(lab, name="A"):
    return {"org_id": lab.orgs[name]}


def sms(lab, count=1):
    return [{"key": uuid4().hex, "phone": "+43000000999", "text": lab.sms_text} for _ in range(count)]


async def print_done(lab, jid):
    await eventually(lambda: lab.row("PrintJob", jid).status == "done", seconds=60)


async def remote_trace(lab, ids):
    records = await eventually(lambda: [row for row in lab.routes() if row["message_id"] in ids])
    if lab.workers > 1:
        assert any(row["publisher"] != row["owner"] for row in records), "Keine nachgewiesene Workergrenze"
    return records


async def test_00_real_shared_configuration(lab):
    configurations = []
    for worker in lab.worker_rows():
        data, actual = await lab.request("runtime", {}, worker=worker.worker_id)
        assert actual == worker.worker_id
        configurations.append(data["configuration"])
    assert len(set(configurations)) == 1
    assert lab.version.startswith("12.3")
    await eventually(
        lambda: any(row.loops.get("alarm_outbox_loop", {}).get("last_success_at") for row in lab.worker_rows())
    )
    assert sum(row.is_leader for row in lab.worker_rows()) == 1
    lab.record(
        "shared_configuration",
        worker_ids=[row.worker_id for row in lab.worker_rows()],
        configuration_hash=configurations[0],
        redis_connected=True,
    )


async def test_01_alarm_sms_across_workers(lab):
    async with Peer(lab, lab.gateway("sms")) as peer:
        tested = []
        for _ in range(3):
            iid = lab.incident()
            result, origin = await lab.request(
                "alarm", {**org(lab), "incident_id": iid}, worker=lab.other_worker(peer.owner)
            )

            def sent(iid=iid):
                with lab.db() as db:
                    return (
                        db.query(lab.m.SmsDispatchJob)
                        .filter(
                            lab.m.SmsDispatchJob.org_id == lab.orgs["A"],
                            lab.m.SmsDispatchJob.job_key.like(f"alarm:{iid}:%"),
                            lab.m.SmsDispatchJob.status == "sent",
                        )
                        .first()
                    )

            job = await eventually(sent)
            assert job.delivery_status == "unverified" and peer.ledger.submissions[job.id] == 1
            await eventually(lambda result=result: lab.row("IncidentAlarmJob", result["alarm_job"]).status == "sent")
            with lab.db() as db:
                route_ids = [
                    attempt.route_id for attempt in db.query(lab.m.SmsDispatchAttempt).filter_by(job_id=job.id)
                ]
            trace = await eventually(
                lambda route_ids=route_ids: [row for row in lab.routes() if row["message_id"] in route_ids]
            )
            tested.append(job.id)
            if lab.workers == 1 or any(row["publisher"] != row["owner"] for row in trace):
                break
        else:
            raise AssertionError("Kein Alarm wurde workeruebergreifend geroutet")
        lab.record(
            "alarm_sms",
            job_ids=tested,
            status="sent",
            origin=origin,
            gateway_worker=peer.owner,
            routing_workers=[row["publisher"] for row in trace],
            simulated_submissions=len(tested),
            delivery_verified=False,
        )


async def test_02_parallel_sms(lab):
    async with Peer(lab, lab.gateway("sms")) as peer:
        responses = await asyncio.gather(
            *(
                lab.request("sms", {**org(lab), "jobs": sms(lab, 3)}, worker=lab.other_worker(peer.owner))
                for _ in range(4)
            )
        )
        ids = [jid for (response, _) in responses for jid in response["job_ids"]]
        await eventually(lambda: all(lab.row("SmsDispatchJob", jid).status == "sent" for jid in ids), seconds=60)
        assert len(set(ids)) == 12
        assert all(peer.ledger.submissions[jid] == 1 for jid in ids)
        lab.record("parallel_sms", jobs=12, job_ids=ids, simulated_submissions=12)


async def test_03_gateway_abort_and_late_sms_result(lab):
    gateway = lab.gateway("sms")
    peer = Peer(lab, gateway)
    peer.drop_after_attempt = True
    async with peer:
        response, _ = await lab.request("sms", {**org(lab), "jobs": sms(lab)}, worker=lab.other_worker(peer.owner))
        jid = response["job_ids"][0]
        await eventually(lambda: lab.row("SmsDispatchJob", jid).status == "uncertain", seconds=85)
        wire = peer.wires[jid]
        ledger = peer.ledger
    async with Peer(lab, gateway, ledger=ledger) as reconnected:
        await reconnected.send({"type": "sms.result", **wire, "ok": True, "outcome": "sent", "provider_accepted": True})
        await asyncio.sleep(0.5)
        job = lab.row("SmsDispatchJob", jid)
        assert job.status == "uncertain" and job.generation == 1 and ledger.submissions[jid] == 1
        lab.record(
            "sms_abort", job_id=jid, status="uncertain", generation=1, simulated_submissions=1, late_result_ignored=True
        )


async def test_04_linux_print_across_workers(lab):
    async with Peer(lab, lab.gateway("linux")) as peer:
        ids = [lab.print_job(peer.gateway) for _ in range(3)]
        await asyncio.gather(
            *(lab.request("linux", {**org(lab), "job_id": jid}, worker=lab.other_worker(peer.owner)) for jid in ids)
        )
        for jid in ids:
            await print_done(lab, jid)
            assert peer.ledger.submissions[str(jid)] == 1
        trace = await remote_trace(lab, [lab.row("PrintJob", jid).push_route_id for jid in ids])
        lab.record(
            "linux_print",
            job_ids=ids,
            statuses=["done"] * 3,
            routing_workers=[row["publisher"] for row in trace],
            simulated_submissions=3,
        )


async def test_05_windows_print_across_workers(lab):
    peer = Peer(lab, lab.gateway("windows_agent"))
    peer.auto = False
    async with peer:
        jid = lab.print_job(peer.gateway)
        _, origin = await lab.request("windows", {**org(lab), "job_id": jid}, worker=lab.other_worker(peer.owner))
        await eventually(lambda: peer.hints > 0)
        grants = await peer.poll()
        assert len(grants) == 1 and grants[0]["job_id"] == jid
        await print_done(lab, jid)
        assert peer.ledger.submissions[str(jid)] == 1
        assert lab.row("PrintJob", jid).lease_gateway_id == peer.gateway["id"]
        assert origin != peer.owner if lab.workers > 1 else origin == peer.owner
        lab.record(
            "windows_print",
            job_id=jid,
            origin=origin,
            gateway_worker=peer.owner,
            hints=peer.hints,
            generation=1,
            simulated_submissions=1,
        )


async def test_06_two_agents_priority_takeover_and_uncertain(lab):
    gateway = lab.gateway("windows_agent")
    standby = lab.gateway("windows_agent", printer=gateway["printer_id"], priority=20)
    first, second = Peer(lab, gateway), Peer(lab, standby)
    first.auto = second.auto = False
    first.hold_before_intent = True
    async with first, second:
        jid = lab.print_job(gateway)
        await lab.request("windows", {**org(lab), "job_id": jid})
        # Ein nicht berechtigter Konkurrent kann kurz den Queue-Datensatz
        # sperren: SKIP LOCKED darf auch beim berechtigten Agent leer liefern.
        async with asyncio.timeout(15):
            while True:
                grants_a, grants_b = await asyncio.gather(first.poll(), second.poll())
                assert not grants_b
                if grants_a:
                    break
                await asyncio.sleep(0.1)
        assert len(grants_a) == 1
        assert first.ledger.submissions[str(jid)] == second.ledger.submissions[str(jid)] == 0
        await first.close()  # Vor dem Intent ist Uebernahme nach echter Lease-Frist sicher.
        second.auto = True
        await print_done_with_timeout(lab, jid, 95)
        assert lab.row("PrintJob", jid).lease_generation == 2
        assert second.ledger.submissions[str(jid)] == 1
        second.hold_after_intent = True
        unknown = lab.print_job(standby)
        await lab.request("windows", {**org(lab), "job_id": unknown})
        await eventually(lambda: second.ledger.submissions[str(unknown)] == 1)
        await second.status(second.held[unknown], "uncertain")
        await eventually(lambda: lab.row("PrintJob", unknown).status == "uncertain")
        await second.close()
        async with Peer(lab, gateway) as replacement:
            await asyncio.sleep(3)
            assert lab.row("PrintJob", unknown).status == "uncertain"
            assert not replacement.ledger.submissions[str(unknown)]
        lab.record(
            "two_windows_agents",
            takeover_job=jid,
            takeover_generation=2,
            uncertain_job=unknown,
            simulated_submissions={str(jid): 1, str(unknown): 1},
            automatic_reprint=False,
        )


async def print_done_with_timeout(lab, jid, seconds):
    await eventually(lambda: lab.row("PrintJob", jid).status == "done", seconds=seconds)


async def test_07_worker_crash_during_open_print(lab):
    gateway = lab.gateway("windows_agent")
    peer = Peer(lab, gateway)
    peer.hold_after_intent = True
    async with peer:
        jid = lab.print_job(gateway)
        await lab.request("windows", {**org(lab), "job_id": jid}, worker=lab.other_worker(peer.owner))
        await eventually(lambda: peer.ledger.submissions[str(jid)] == 1)
        old_owner, grant = peer.owner, peer.held[jid]
        os.kill(int(old_owner.split(":")[1]), signal.SIGKILL)
        await peer.close()
        await eventually(
            lambda: lab.ready_workers() and all(w.worker_id != old_owner for w in lab.worker_rows()), seconds=60
        )
        async with Peer(lab, gateway, ledger=peer.ledger) as recovered:
            await recovered.status(grant, "uncertain")
            await eventually(lambda: lab.row("PrintJob", jid).status == "uncertain")
            fresh = lab.print_job(gateway)
            await lab.request("windows", {**org(lab), "job_id": fresh})
            await print_done(lab, fresh)
            assert recovered.owner != old_owner and peer.ledger.submissions[str(jid)] == 1
            assert lab.row("PrintJob", jid).lease_generation == 1
            lab.record(
                "worker_crash_open_job",
                job_id=jid,
                old_worker=old_owner,
                new_worker=recovered.owner,
                status="uncertain",
                simulated_submissions=1,
                recovery_job=fresh,
            )


async def test_08_redis_outage_persistent_jobs_and_reconnect(lab):
    import redis

    async with (
        Peer(lab, lab.gateway("sms")) as sms_peer,
        Peer(lab, lab.gateway("linux")) as linux_peer,
        Peer(lab, lab.gateway("windows_agent")) as win_peer,
    ):
        lab.docker("stop", "--time", "0", lab.redis_container)
        try:
            await eventually(lambda: all(not w.redis.get("connected") for w in lab.worker_rows()), seconds=25)
            async with httpx.AsyncClient(base_url=lab.base) as monitor:
                response = await monitor.get(
                    "/health/dienste", headers={"Authorization": "Bearer " + lab.monitor_tokens["A"]}
                )
                assert response.json()["runtime"]["redis"]["state"] == "down"
            response, _ = await lab.request(
                "sms", {**org(lab), "jobs": sms(lab)}, worker=lab.other_worker(sms_peer.owner)
            )
            jid = response["job_ids"][0]
            linux_id = lab.print_job(linux_peer.gateway)
            await lab.request("linux", {**org(lab), "job_id": linux_id}, worker=lab.other_worker(linux_peer.owner))
            win_id = lab.print_job(win_peer.gateway)
            from app.services import print_agent_service

            with lab.db() as db:
                print_agent_service.enqueue_for_agents(db, db.get(lab.m.PrintJob, win_id))
            # Kein job_available: der Peer muss den Job durch Lease-Polling finden.
            await eventually(lambda: lab.row("SmsDispatchJob", jid).status == "sent")
            await print_done(lab, linux_id)
            await print_done(lab, win_id)
        finally:
            lab.docker("start", lab.redis_container)
        await eventually(lab.ready_workers, seconds=35)
        assert all(len(w.redis.get("channels", [])) >= 4 for w in lab.worker_rows())
        transport = lab.row("GatewayMessage", lab.row("PrintJob", linux_id).push_route_id)
        echo = {
            "kind": "route",
            "message_id": transport.id,
            **{
                key: getattr(transport, key)
                for key in ("org_id", "gateway_id", "gateway_kind", "connection_id", "worker_id")
            },
        }
        client = redis.Redis.from_url(lab.redis_url)
        for _ in range(3):
            client.publish("ec:gw", json.dumps(echo))
        client.close()
        await asyncio.sleep(0.5)
        assert linux_peer.ledger.submissions[str(linux_id)] == win_peer.ledger.submissions[str(win_id)] == 1
        assert sms_peer.ledger.submissions[jid] == 1
        lab.record(
            "redis_restart",
            sms_job=jid,
            linux_job=linux_id,
            windows_job=win_id,
            windows_hint_omitted=True,
            echo_repeats=3,
            duplicate_submissions=0,
            monitoring_reported_outage=True,
        )
