"""Nur fuer den lokalen Gunicorn-Prozesstest; keine produktiven Poller starten."""

import asyncio
import fcntl
import logging
import os
from pathlib import Path

import app.main as main
from app.services import background_runtime, gateway_presence
from app.services.loop_utils import iteration_watch
from app.services.sms_outbox import database
from app.services.worker_monitor import snapshot

background_runtime.HEARTBEAT_S = .1
background_runtime.TAKEOVER_S = .1
background_runtime.WORKER_TTL_S = 1


async def proof_loop():
    directory = Path(os.environ["ECP_PROBE_DIR"])
    fd = os.open(directory / "work.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            with iteration_watch(logging.getLogger("proof"), "proof_loop", .05):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    (directory / "overlap").write_text("Two leaders executed concurrently")
                    raise
                try:
                    with (directory / "runs").open("a") as stream:
                        stream.write(gateway_presence.worker_id() + "\n")
                    await asyncio.sleep(.01)
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            await asyncio.sleep(.02)
    finally:
        os.close(fd)


def start_loops():
    return [asyncio.create_task(background_runtime.supervised_loop(proof_loop), name="proof_loop")]


main._start_background_loops = start_loops
app = main.app


@app.get("/__phase5_probe")
def probe():
    with database() as db:
        data = snapshot(db, 1)
        return {"count": data["worker_count"], "leaders": data["leader_workers"],
                "workers": [w["worker_id"] for w in data["workers"]]}
