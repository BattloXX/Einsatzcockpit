"""Eigene MariaDB/Redis-Container und echter Gunicorn, keinerlei Produktivzugriffe."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait(predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("Abnahmebedingung nicht rechtzeitig erfuellt")


async def eventually(predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = await asyncio.to_thread(predicate)
        if result:
            return result
        await asyncio.sleep(0.1)
    raise AssertionError("Abnahmebedingung nicht rechtzeitig erfuellt")


class Lab:
    def __init__(self, workers, directory, patch):
        self.workers = workers
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prefix = "ecp-phase6-test-" + uuid4().hex[:10]
        self.containers = []
        self.process = None
        self.patch = patch
        self.results = []
        self.secrets = []
        self.key = secrets.token_urlsafe(40)
        self.password = "Phase6-" + secrets.token_urlsafe(20) + "!"
        self.sms_text = "SMS_CONTENT_MUST_NOT_BE_LOGGED_" + uuid4().hex
        self.secrets.extend((self.key, self.password, self.sms_text))

    def docker(self, *args):
        return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=40).stdout.strip()

    def start(self):
        db_port, redis_port, app_port = free_port(), free_port(), free_port()
        self.db_container, self.redis_container = self.prefix + "-db", self.prefix + "-redis"
        database = "ecp_phase6_" + self.prefix.rsplit("-", 1)[-1]
        self.database_url = f"mysql+pymysql://root@127.0.0.1:{db_port}/{database}"
        self.redis_url = f"redis://127.0.0.1:{redis_port}/0"
        self.base = f"http://127.0.0.1:{app_port}"
        self.docker(
            "run",
            "--detach",
            "--name",
            self.db_container,
            "--env",
            "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1",
            "--env",
            f"MARIADB_DATABASE={database}",
            "--publish",
            f"127.0.0.1:{db_port}:3306",
            "mariadb:12.3",
        )
        self.containers.append(self.db_container)
        self.docker(
            "run",
            "--detach",
            "--name",
            self.redis_container,
            "--publish",
            f"127.0.0.1:{redis_port}:6379",
            "redis:7-alpine",
        )
        self.containers.append(self.redis_container)
        engine = create_engine(self.database_url, connect_args={"connect_timeout": 1})

        def database_ready():
            try:
                with engine.connect() as connection:
                    return connection.exec_driver_sql("SELECT 1").scalar() == 1
            except Exception:
                return False

        wait(database_ready, seconds=60)
        engine.dispose()
        runtime_directory = self.directory / "run"
        runtime_directory.mkdir(mode=0o700, exist_ok=True)
        values = {
            "XDG_RUNTIME_DIR": str(runtime_directory),
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "TESTING": "false",
            "ECP_ACCEPTANCE_TEST": "1",
            "ECP_ACCEPTANCE_KEY": self.key,
            "ECP_ACCEPTANCE_ARTIFACTS": str(self.directory),
            "ECP_GUNICORN_WORKERS": str(self.workers),
            "ECP_GUNICORN_BIND": f"127.0.0.1:{app_port}",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DEBUG": "true",
            "COOKIE_SECURE": "false",
            "PUBLIC_BASE_URL": self.base,
            "APP_BASE_URL": self.base,
            "LEADER_LOCK_PATH": str(self.directory / "leader.lock"),
            "WEATHER_DATABASE_URL": "",
            "NACHSCHLAGEWERK_SYNC_ENABLED": "false",
            "MAIL_IMAP_POLL_ENABLED": "false",
            "MAIL_GRAPH_POLL_ENABLED": "false",
            "LIS_ENABLED": "false",
            "DIBOS_TRACE_ENABLED": "false",
            "SEARCH_ENABLED": "false",
            "SEARCH_INDEX_WORKER_ENABLED": "false",
            "MEDIA_STORAGE_DIR": str(self.directory / "media"),
            "BACKUP_DIR": str(self.directory / "backup"),
        }
        self.secrets.append(values["SECRET_KEY"])
        for name, value in values.items():
            self.patch.setenv(name, value)
        with (self.directory / "migrations.log").open("w") as output:
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=ROOT,
                check=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=180,
            )
        self.seed()
        self.log = (self.directory / "gunicorn.log").open("w")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "--config",
                "deploy/proposals/multi-worker/gunicorn.conf.py",
                "e2e.multi_worker.acceptance_app:app",
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        wait(lambda: self.ready_workers(), seconds=60)
        with self.db() as db:
            self.version = db.connection().exec_driver_sql("SELECT VERSION()").scalar()

    def seed(self):
        from app.core import security
        from app.db import SessionLocal
        from app.models.dienst_monitor import DienstMonitorToken
        from app.models.gateway import Gateway, GatewayMessage, Printer, PrinterAgentRoute, PrintJob
        from app.models.incident import Incident, IncidentAlarmJob
        from app.models.master import FireDept, Member, OrgSettings
        from app.models.sms import SmsDispatchAttempt, SmsDispatchJob, SmsEinsatzinfoRecipient
        from app.models.user import Role, SmsGatewayToken, User, UserRole
        from app.models.worker_runtime import WorkerRuntime
        from app.seed_data import seed

        self.Session = SessionLocal
        self.m = SimpleNamespace(
            **{
                cls.__name__: cls
                for cls in (
                    Gateway,
                    GatewayMessage,
                    PrintJob,
                    Printer,
                    PrinterAgentRoute,
                    Incident,
                    IncidentAlarmJob,
                    SmsDispatchAttempt,
                    SmsDispatchJob,
                    SmsGatewayToken,
                    WorkerRuntime,
                    DienstMonitorToken,
                )
            }
        )
        security.BCRYPT_ROUNDS = 4
        self.orgs, self.users, self.monitor_tokens = {}, {}, {}
        with self.db() as db:
            seed(db)
            for name in ("A", "B"):
                org = FireDept(slug="phase6-" + name.lower(), name="Phase 6 " + name)
                db.add(org)
                db.flush()
                self.orgs[name] = org.id
                db.add(OrgSettings(org_id=org.id, einsatzinfo_sms_enabled=True, dienst_monitor_enabled=False))
                member = Member(
                    org_id=org.id, firstname="Abnahme", lastname=name, phone="+4300000000" + str(org.id), active=True
                )
                db.add(member)
                db.flush()
                db.add(SmsEinsatzinfoRecipient(org_id=org.id, member_id=member.id))
                token = secrets.token_urlsafe(30)
                self.secrets.append(token)
                self.monitor_tokens[name] = token
                db.add(DienstMonitorToken(org_id=org.id, label="Phase 6", token_hash=security.hash_api_key(token)))
                for suffix in ("one", "two") if name == "A" else ("one",):
                    username = f"phase6-{name.lower()}-{suffix}"
                    user = User(
                        username=username,
                        password_hash=security.hash_password(self.password),
                        display_name=username,
                        org_id=org.id,
                        active=True,
                    )
                    db.add(user)
                    db.flush()
                    for code in ("incident_leader", "admin"):
                        role = db.query(Role).filter(Role.code == code).one()
                        db.add(UserRole(user_id=user.id, role_id=role.id))
                    self.users[name + suffix] = username
            db.commit()
        self.incidents = {name: self.incident(name) for name in self.orgs}

    @contextmanager
    def db(self):
        from app.core.tenant import set_tenant_context

        with self.Session() as db:
            set_tenant_context(db, None)
            yield db

    def incident(self, org="A"):
        from app.services.incident_service import create_incident

        with self.db() as db:
            incident, _ = create_incident(
                db, "B2", primary_org_id=self.orgs[org], address_city="Abnahme", report_text=self.sms_text
            )
            db.commit()
            self.secrets.extend(value for value in (incident.alarm_token, incident.auto_geojson_token) if value)
            return incident.id

    def gateway(self, kind, org="A", *, printer=None, priority=10):
        from app.core.security import hash_api_key

        token = secrets.token_urlsafe(32)
        self.secrets.append(token)
        with self.db() as db:
            row = (
                self.m.SmsGatewayToken(org_id=self.orgs[org], token_hash=hash_api_key(token), label="Phase 6 SMS")
                if kind == "sms"
                else self.m.Gateway(
                    org_id=self.orgs[org], kind=kind, name="Phase 6 " + kind, device_token_hash=hash_api_key(token)
                )
            )
            db.add(row)
            db.flush()
            route_id = None
            if kind != "sms":
                if printer is None:
                    target = self.m.Printer(
                        org_id=self.orgs[org],
                        gateway_id=row.id,
                        name="Abnahme Drucker",
                        uri="agent://phase6" if kind == "windows_agent" else "ipp://phase6/print",
                        aktiv=True,
                    )
                    db.add(target)
                    db.flush()
                    printer = target.id
                if kind == "windows_agent":
                    route = self.m.PrinterAgentRoute(
                        org_id=self.orgs[org],
                        printer_id=printer,
                        gateway_id=row.id,
                        priority=priority,
                        local_queue="Phase 6",
                        render_mode="driver",
                    )
                    db.add(route)
                    db.flush()
                    route_id = route.id
            db.commit()
            return {
                "kind": kind,
                "org": org,
                "org_id": self.orgs[org],
                "id": row.id,
                "token": token,
                "printer_id": printer,
                "route_id": route_id,
            }

    def print_job(self, gateway):
        with self.db() as db:
            job = self.m.PrintJob(
                org_id=gateway["org_id"],
                gateway_id=gateway["id"],
                printer_id=gateway["printer_id"],
                incident_id=self.incidents[gateway["org"]],
                document_type="einsatzinfo",
                idempotency_key=uuid4().hex,
                delivery="lease" if gateway["kind"] == "windows_agent" else "push",
            )
            db.add(job)
            db.commit()
            return job.id

    def row(self, model, ident):
        with self.db() as db:
            row = db.get(getattr(self.m, model), ident)
            db.expunge(row)
            return row

    def worker_rows(self):
        from app.services.gateway_presence import utc

        with self.db() as db:
            return db.query(self.m.WorkerRuntime).filter(self.m.WorkerRuntime.expires_at > utc()).all()

    def ready_workers(self):
        rows = self.worker_rows()
        return len(rows) == self.workers and all(row.redis.get("connected") for row in rows)

    def other_worker(self, owner):
        return next((row.worker_id for row in self.worker_rows() if row.worker_id != owner), owner)

    async def request(self, kind, data, *, worker=None):
        headers = {"X-ECP-Acceptance-Key": self.key}
        if worker:
            headers["X-ECP-Target-Worker"] = worker
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            async with httpx.AsyncClient(base_url=self.base, timeout=30) as client:
                response = (
                    await client.get("/api/v1/__acceptance/runtime", headers=headers)
                    if kind == "runtime"
                    else await client.post("/api/v1/__acceptance/trigger", headers=headers, json={"kind": kind, **data})
                )
            if response.status_code == 409:
                continue
            assert response.status_code == 200, f"Testausloeser HTTP {response.status_code}"
            return response.json(), response.headers["X-ECP-Acceptance-Worker"]
        raise AssertionError("Zielworker nicht erreicht")

    def routes(self):
        records = []
        for path in self.directory.glob("routes-*.jsonl"):
            records.extend(json.loads(line) for line in path.read_text().splitlines())
        return records

    def connections(self, path):
        records = []
        for file in self.directory.glob("connections-*.jsonl"):
            records.extend(json.loads(line) for line in file.read_text().splitlines())
        return sorted((record for record in records if record["path"] == path), key=lambda record: record["at"])

    def record(self, case, **metadata):
        self.results.append({"case": case, "status": "passed", **metadata})
        self.save()

    def save(self):
        (self.directory / "results.json").write_text(
            json.dumps(
                {
                    "workers": self.workers,
                    "mariadb": getattr(self, "version", None),
                    "test_peer_only": True,
                    "hardware_accepted": False,
                    "cases": self.results,
                },
                indent=2,
            )
        )

    def close(self):
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)
        if hasattr(self, "log"):
            self.log.close()
        self.save()
        for name in reversed(self.containers):
            assert name.startswith(self.prefix)
            self.docker("rm", "--force", "--volumes", name)
