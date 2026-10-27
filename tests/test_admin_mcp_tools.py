"""Lesende Diagnose: Schema, Fehler, Isolation, Maskierung und Begrenzungen."""
import asyncio
import importlib.util
import json
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from app.admin_mcp.context import admin_session as SessionLocal
from app.admin_mcp.masking import mask_arguments, mask_text
from app.admin_mcp.registry import READ_TOOLS
from app.models.dienst_monitor import DienstStatus
from app.models.master import FireDept
from app.models.user import AuditLog
from app.services import system_diagnose as diagnose
from tests import test_admin_mcp_oauth as oauth
from tests.test_admin_mcp_oauth import admin_call, tokens

admin_env = oauth.admin_env


def payload(response):
    assert response.status_code == 200, response.text
    body = response.text
    if body.startswith('event:'):
        body = next(line[6:] for line in body.splitlines() if line.startswith("data: "))
    result = json.loads(body)["result"]
    assert not result.get("isError"), result
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def call(client, token, name, arguments=None):
    return payload(admin_call(client, token, "tools/call", {"name": name, "arguments": arguments or {}}))


def test_all_seven_schemas_audit_and_no_org_contents(client, admin_env, monkeypatch):
    from app import log_buffer
    sentinel = "GEHEIMER-EINSATZ-KONTAKT-MITGLIED-OBJEKT"
    with SessionLocal() as db:
        for i in range(2):
            org = FireDept(slug=uuid4().hex, name=sentinel + str(i))
            db.add(org)
            db.flush()
            db.add(DienstStatus(org_id=org.id, key="alarm_seriell", state="ok", last_error=sentinel))
        db.commit()
    monkeypatch.setattr(log_buffer, "get_entries", lambda *args: [
        {"level": "ERROR", "msg": sentinel + " password=SECRETPASSWORD alice@example.com +436641234567"},
        {"level": "ERROR", "msg": sentinel + " password=SECRETPASSWORD alice@example.com +436641234567"},
    ])
    values = tokens(client, admin_env)
    correlations = set()
    for name in READ_TOOLS:
        answer = call(client, values["access_token"], name)
        assert set(answer) == {"zeitpunkt", "status", "modul", "schweregrad", "evidenz",
                               "ursachenhypothese", "empfehlung", "korrelations_id"}
        assert answer["status"] in ("ok", "warn", "fail", "unbekannt")
        assert datetime.fromisoformat(answer["zeitpunkt"]).utcoffset().total_seconds() == 0
        UUID(answer["korrelations_id"])
        correlations.add(answer["korrelations_id"])
        forbidden = (sentinel, "SECRETPASSWORD", "alice@example.com", "+436641234567")
        assert not any(v in json.dumps(answer) for v in forbidden)
    assert len(correlations) == 7
    answer = call(client, values["access_token"], "admin_dienste_pruefen")
    assert answer["evidenz"]["dienste"]["alarm_seriell"]["ok"] >= 2
    logs = call(client, values["access_token"], "admin_logs_analysieren", {"suchtext": sentinel, "limit": 200})
    assert logs["evidenz"]["gruppen"][0]["anzahl"] == 2
    with SessionLocal() as db:
        rows = db.query(AuditLog).filter(AuditLog.user_id == admin_env["id"], AuditLog.action == "admin_mcp.tool").all()
        assert correlations <= {json.loads(r.payload_json)["korrelations_id"] for r in rows}
        audits = " ".join(r.payload_json for r in rows)
        assert sentinel not in audits and values["access_token"] not in audits
        assert '"suchtext": "[MASKIERT]"' in audits
        assert "evidenz" not in audits


def test_redis_down_db_failure_and_empty_data(client, admin_env, monkeypatch):
    values = tokens(client, admin_env)
    monkeypatch.setattr(diagnose.ws_bus, "status", lambda: {"configured": True, "connected": False,
                                                        "error": "password=secret IP 192.168.2.3"})
    redis = call(client, values["access_token"], "admin_redis_pruefen")
    assert redis["status"] == "fail" and "secret" not in json.dumps(redis)

    class BrokenDB:
        def get_bind(self):
            raise OperationalError("secret", {}, Exception("private@example.com"))
    assert diagnose.db_status(BrokenDB())["status"] == "fail"
    assert "private@example.com" not in json.dumps(diagnose.db_status(BrokenDB()), default=str)
    monkeypatch.setattr(diagnose, "db_status", lambda db: (_ for _ in ()).throw(RuntimeError("secret")))
    result = call(client, values["access_token"], "admin_datenbank_pruefen")
    assert result["status"] == "fail" and "secret" not in json.dumps(result)


@pytest.mark.parametrize("value", [
    "Authorization: Bearer abcSECRET", "Cookie: session=SECRET; other=PRIVATE", "token=SECRET",
    "password: SECRET", "api_key=SECRET", "mail alice@example.org", "tel +43 664 1234567",
    "IPv4 192.168.10.24", "IPv6 2001:db8::1234", "refresh_token='SECRET'", "totp=123456",
    "opaque abcdefghijklmnopqrstuvwxyz0123456789",
])
def test_masking(value):
    result = mask_text(value)
    assert "[MASKIERT]" in result and result != value
    for secret in ("SECRET", "alice@example.org", "+43 664 1234567", "192.168.10.24", "2001:db8::1234"):
        assert secret not in result


def test_mask_arguments():
    assert mask_arguments({"suchtext": "Name des Mitglieds", "password": "secret", "limit": 200, "level": "ERROR"}) == {
        "suchtext": "[MASKIERT]", "password": "[MASKIERT]", "limit": 200, "level": "ERROR"}


def test_limits_timeout_size_and_parameters(client, admin_env, monkeypatch):
    import app.admin_mcp.server as server
    values = tokens(client, admin_env)
    for limit in (0, 201):
        answer = call(client, values["access_token"], "admin_logs_analysieren", {"limit": limit})
        assert answer["status"] == "unbekannt"
    monkeypatch.setattr(server, "MAX_RESPONSE_BYTES", 1024)
    monkeypatch.setattr(server, "collect", lambda *a, **kw: {"status": "ok", "evidence": {"big": "x" * 2048}})
    answer = call(client, values["access_token"], "admin_redis_pruefen")
    assert answer["evidenz"] == {"fehler": "Antwortgroesse begrenzt"}
    original = asyncio.to_thread
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(server.asyncio, "to_thread", slow)
    monkeypatch.setattr(server, "TOOL_TIMEOUT", 0.01)
    assert call(client, values["access_token"], "admin_redis_pruefen")["evidenz"] == {"fehler": "Zeitlimit erreicht"}
    monkeypatch.setattr(server.asyncio, "to_thread", original)
    monkeypatch.setattr(server, "TOOL_TIMEOUT", 20)
    monkeypatch.setattr(server._tool_limiter, "hit", lambda *args: False)
    assert call(client, values["access_token"], "admin_redis_pruefen")["evidenz"] == {"fehler": "Rate-Limit erreicht"}


def test_migration_0326_upgrade_downgrade(tmp_path):
    spec = importlib.util.spec_from_file_location("migration0326", "alembic/versions/0326_admin_mcp.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0325"
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user (id BIGINT PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = inspect(connection)
        for kind in ("client", "code", "token"):
            columns = {c["name"] for c in inspector.get_columns("admin_mcp_oauth_" + kind)}
            assert "org_id" not in columns
            if kind != "client":
                assert {"session_epoch", "mfa_at"} <= columns
        assert "family_expires_at" in columns
        migration.downgrade()
        assert inspect(connection).get_table_names() == ["user"]
    engine.dispose()


def test_admin_oauth_never_in_org_export():
    from app.services.org_export_service import EXCLUDE_TABLES, scope_rules
    tables = {"admin_mcp_oauth_client", "admin_mcp_oauth_code", "admin_mcp_oauth_token"}
    assert tables <= EXCLUDE_TABLES
    assert not tables & set(scope_rules())
