import asyncio
import json
import os
import signal
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from e2e.multi_worker.lab import eventually
from e2e.multi_worker.peers import Peer


def browser_path():
    configured = os.environ.get("ECP_ACCEPTANCE_BROWSER")
    paths = (
        [Path(configured)]
        if configured
        else sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome"))
    )
    assert paths and paths[-1].is_file(), "Chromium fuer echte Browserabnahme bereitstellen"
    return str(paths[-1])


async def login(page, lab, username):
    await page.goto(lab.base + "/login")
    await page.locator('[name="username"]').fill(username)
    await page.locator('[name="password"]').fill(lab.password)
    await page.locator('button[type="submit"]').click()
    await page.wait_for_url(lambda url: urlsplit(url).path != "/login")


async def test_09_parallel_browser_editing_and_reconnect(lab):
    from playwright.async_api import async_playwright

    iid = lab.incidents["A"]
    path = f"/ws/incident/{iid}"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=browser_path(), headless=True)
        contexts = [await browser.new_context() for _ in range(2)]
        try:
            pages = [await context.new_page() for context in contexts]
            for index, page in enumerate(pages):
                await login(page, lab, lab.users["Aone" if index == 0 else "Atwo"])
                before = len(lab.connections(path))
                await page.goto(lab.base + f"/einsatz/{iid}")
                await eventually(lambda before=before: len(lab.connections(path)) > before)
                if index == 0:
                    first_owner = lab.connections(path)[-1]["worker"]
                elif lab.workers > 1:
                    for _ in range(20):
                        if lab.connections(path)[-1]["worker"] != first_owner:
                            break
                        before = len(lab.connections(path))
                        await page.reload()
                        await eventually(lambda before=before: len(lab.connections(path)) > before)
                    assert lab.connections(path)[-1]["worker"] != first_owner

            async def card(page, title, kind="meldung"):
                return await page.evaluate(
                    """async ({id,title,kind}) => {
                  const csrf = document.cookie.split('; ').find(v=>v.startsWith('ec_csrf=')).split('=')[1];
                  const response = await fetch('/einsatz/'+id+'/'+kind, {method:'POST',
                    headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRF-Token':decodeURIComponent(csrf)},
                    body:new URLSearchParams({title,detail:'Paralleltest',status:'meldung'})});
                  return response.status;
                }""",
                    {"id": iid, "title": title, "kind": kind},
                )

            titles = ["Phase6-" + uuid4().hex for _ in range(2)]
            assert await asyncio.gather(*(card(page, title) for page, title in zip(pages, titles, strict=True))) == [
                200,
                200,
            ]
            task_titles = ["Task-" + uuid4().hex for _ in range(2)]
            assert await asyncio.gather(
                *(card(page, title, "aufgabe") for page, title in zip(pages, task_titles, strict=True))
            ) == [200, 200]
            for page in pages:
                for title in titles + task_titles:
                    await page.get_by_text(title, exact=True).wait_for(timeout=30000)
                    assert await page.get_by_text(title, exact=True).count() == 1
            lab.record(
                "parallel_browsers",
                incident_id=iid,
                browser_contexts=2,
                websocket_worker=first_owner,
                second_websocket_worker=lab.connections(path)[-1]["worker"],
                messages=2,
                tasks=2,
                lost_updates=0,
            )
            await contexts[1].set_offline(True)
            await pages[1].evaluate("window.Alpine.$data(document.querySelector('.board-wrap'))._ws.close()")
            await pages[1].wait_for_function(
                "window.Alpine.$data(document.querySelector('.board-wrap'))._ws.readyState !== 1"
            )
            offline_title = "Reconnect-" + uuid4().hex
            assert await card(pages[0], offline_title) == 200
            await contexts[1].set_offline(False)
            await pages[1].get_by_text(offline_title, exact=True).wait_for(timeout=45000)
            assert await pages[1].get_by_text(offline_title, exact=True).count() == 1
            await pages[1].screenshot(path=str(lab.directory / "browser-reconnected.png"), full_page=True)
            lab.record("browser_reconnect", incident_id=iid, missed_message_recovered=True)
        finally:
            for context in contexts:
                await context.close()
            await browser.close()


async def test_11_tenant_isolation_and_token_revocation(lab):
    from app.services.gateway_presence import utc

    first, second = Peer(lab, lab.gateway("sms", "A")), Peer(lab, lab.gateway("sms", "B"))
    second.hold_received = True
    async with first, second:
        response, _ = await lab.request(
            "sms",
            {"org_id": lab.orgs["B"], "jobs": [{"key": uuid4().hex, "phone": "+43000000999", "text": lab.sms_text}]},
        )
        jid = response["job_ids"][0]
        await eventually(lambda: jid in second.wires)
        await first.send(
            {"type": "sms.result", **second.wires[jid], "ok": True, "provider_accepted": True, "outcome": "sent"}
        )
        await asyncio.sleep(0.3)
        assert lab.row("SmsDispatchJob", jid).status == "dispatched"
        assert not first.ledger.submissions[jid]
        await second.send({"type": "sms.ack", **second.wires[jid]})
        await eventually(lambda: lab.row("SmsDispatchJob", jid).status == "sent")
        async with httpx.AsyncClient(base_url=lab.base) as client:
            headers = {"Authorization": "Bearer " + lab.monitor_tokens["A"]}
            data = (await client.get("/health/dienste", params={"org_id": lab.orgs["B"]}, headers=headers)).json()
            assert all(
                not (entry["kind"] == "sms" and entry["id"] == second.gateway["id"])
                for entry in data["runtime"]["gateways"]
            )
            await client.get("/login")
            response = await client.post(
                "/login",
                data={"username": lab.users["Aone"], "password": lab.password, "_csrf": client.cookies.get("ec_csrf")},
            )
            assert response.status_code in (302, 303)
            assert (await client.get(f"/einsatz/{lab.incidents['B']}")).status_code in (403, 404)
            cookies = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
            lab.secrets.extend(client.cookies.values())
            with pytest.raises(InvalidStatus) as rejected:
                async with connect(
                    lab.base.replace("http://", "ws://") + f"/ws/incident/{lab.incidents['B']}",
                    additional_headers={"Cookie": cookies},
                ):
                    pass
            assert rejected.value.response.status_code == 403
            with lab.db() as db:
                db.get(lab.m.SmsGatewayToken, first.gateway["id"]).revoked_at = utc()
                db.commit()
            await eventually(lambda: lab.row("SmsGatewayToken", first.gateway["id"]).connection_id is None, seconds=20)
            status = (await client.get("/health/dienste", headers=headers)).json()["runtime"]
            assert status["sms_connected"] == 0
            assert all(secret not in json.dumps(status) for secret in lab.secrets)
        lab.record(
            "tenant_isolation",
            foreign_ack_rejected=True,
            foreign_http_rejected=True,
            foreign_websocket_rejected=True,
            monitoring_scoped=True,
            revoked_gateway_offline=True,
        )


async def test_12_leader_failover_and_gateway_expiry(lab):
    from app.services.gateway_presence import online

    leader = next(row for row in lab.worker_rows() if row.is_leader)
    peer = Peer(lab, lab.gateway("linux"), owner=leader.worker_id)
    async with peer:
        killed_at = time.monotonic()
        os.kill(int(leader.worker_id.split(":")[1]), signal.SIGKILL)
        await peer.close()
        await eventually(
            lambda: lab.ready_workers() and all(w.worker_id != leader.worker_id for w in lab.worker_rows()), seconds=60
        )

        def new_leader():
            leaders = [w for w in lab.worker_rows() if w.is_leader]
            return (
                leaders[0]
                if len(leaders) == 1 and leaders[0].loops.get("alarm_outbox_loop", {}).get("last_success_at")
                else None
            )

        replacement = await eventually(new_leader)
        assert replacement.worker_id != leader.worker_id
        failover_seconds = time.monotonic() - killed_at
        await eventually(lambda: not online(lab.row("Gateway", peer.gateway["id"])), seconds=105)
        stale_seconds = time.monotonic() - killed_at
        assert stale_seconds < 110
        assert all(not row.loops for row in lab.worker_rows() if not row.is_leader)
        lab.record(
            "leader_failover",
            old_worker=leader.worker_id,
            new_worker=replacement.worker_id,
            observed_replacement_seconds=round(failover_seconds, 2),
            gateway_offline_after_seconds=round(stale_seconds, 2),
            gateway_ttl_seconds=90,
        )


def test_13_job_accounting_and_diagnostic_logs(lab):
    with lab.db() as db:
        sms = db.query(lab.m.SmsDispatchJob).all()
        prints = db.query(lab.m.PrintJob).all()
        assert sms and prints
        assert all(job.status in ("sent", "failed", "uncertain") for job in sms)
        assert all(job.status in ("done", "failed", "uncertain", "canceled") for job in prints)
        assert any(job.status == "uncertain" for job in sms)
        assert any(job.status == "uncertain" for job in prints)
        states = {
            "sms": {state: sum(job.status == state for job in sms) for state in ("sent", "failed", "uncertain")},
            "print": {state: sum(job.status == state for job in prints) for state in ("done", "failed", "uncertain")},
        }
    logs = (lab.directory / "gunicorn.log").read_text()
    assert not any(value in logs for value in lab.secrets if value), "Sensitive Testdaten im Diagnoselog"
    lab.record("job_accounting", states=states, unfinished_jobs=0, secret_leaks=0)
