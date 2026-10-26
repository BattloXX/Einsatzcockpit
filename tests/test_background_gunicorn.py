"""Opt-in: echte Gunicorn-Worker, SIGKILL, Ersatzprozess und exklusives Failover."""

import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.user import User
from app.seed_data import seed

pytestmark = pytest.mark.skipif(os.environ.get("RUN_WORKER_PROCESS_TESTS") != "1", reason="Opt-in Gunicorn-Prozesstest")


@pytest.mark.parametrize("count", [2, 4])
def test_gunicorn_leader_crash_and_worker_reconnect(tmp_path, count):
    engine = create_engine(f"sqlite:///{tmp_path / 'probe.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        set_tenant_context(db, None)
        seed(db)
        if db.query(User).first() is None:
            db.add(User(username="probe-admin", password_hash="unused-test-login", display_name="Probe", active=True))
            db.commit()
    engine.dispose()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'probe.db'}", "TESTING": "false",
           "REDIS_URL": "", "LEADER_LOCK_PATH": str(tmp_path / "leader.lock"), "ECP_PROBE_DIR": str(tmp_path)}
    log_path = tmp_path / "gunicorn.log"
    with log_path.open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "gunicorn", "tests.worker_probe_app:app",
                                    "--worker-class", "uvicorn.workers.UvicornWorker", "--workers", str(count),
                                    "--bind", f"127.0.0.1:{port}", "--graceful-timeout", "5", "--timeout", "30"],
                                   stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            with httpx.Client(timeout=2) as client:
                def ready(predicate):
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        assert process.poll() is None, log_path.read_text()[-4000:]
                        try:
                            data = client.get(f"http://127.0.0.1:{port}/__phase5_probe").json()
                            if predicate(data):
                                return data
                        except (httpx.HTTPError, ValueError):
                            pass
                        time.sleep(.05)
                    pytest.fail(log_path.read_text()[-4000:])
                before = ready(lambda d: d["count"] == count and len(d["leaders"]) == 1)
                leader_id = before["leaders"][0]
                leader_pid = int(leader_id.split(":")[1])
                os.kill(leader_pid, signal.SIGKILL)
                after = ready(lambda d: d["count"] == count and len(d["leaders"]) == 1
                              and leader_id not in d["workers"])
                assert after["leaders"][0] != leader_id
                deadline = time.monotonic() + 5
                while after["leaders"][0] not in (tmp_path / "runs").read_text() and time.monotonic() < deadline:
                    time.sleep(.02)
                runs = (tmp_path / "runs").read_text().splitlines()
                assert leader_id in runs and after["leaders"][0] in runs
                assert not (tmp_path / "overlap").exists()
        finally:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
