"""Alle MCP-Werkzeuge serialisieren Zeitpunkte mit UTC-Offset."""
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import log_buffer
from app.admin_mcp.registry import ADMIN_TOOLS, REPAIR_TOOLS, collect
from app.admin_mcp.server import response
from app.admin_mcp.zeit import iso_utc, timestamps
from tests import test_admin_mcp_oauth as oauth
from tests.test_admin_mcp_tools import call

admin_env = oauth.admin_env


def assert_dates(value):
    if isinstance(value, dict):
        for child in value.values():
            assert_dates(child)
    elif isinstance(value, list):
        for child in value:
            assert_dates(child)
    elif isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:", value):
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, value
        assert value.endswith("Z"), value


@pytest.mark.parametrize("name", list(ADMIN_TOOLS | REPAIR_TOOLS))
def test_every_response_boundary(name):
    stamp = datetime(2026, 10, 25, 2, 30)
    result = response(name, "fixture", {"status": "ok", "evidence": {
        "db": stamp, "nested": [{"api": "2026-10-25T02:30:00+02:00", "legacy": stamp.isoformat()}],
    }, "github_zugang": {"ablauf_am": stamp.isoformat()}})
    assert_dates(result)
    assert result["evidenz"]["nested"][0]["api"] == "2026-10-25T00:30:00Z"


def test_utc_helper():
    stamp = datetime(2026, 11, 2, 12)
    assert iso_utc(stamp) == "2026-11-02T12:00:00Z"
    assert iso_utc(stamp.replace(tzinfo=UTC)) == iso_utc(stamp)
    assert iso_utc(stamp.replace(tzinfo=UTC).astimezone(UTC)) == iso_utc(stamp)
    assert iso_utc("unknown") is None
    assert timestamps({"version": "2026.11.02.1", "date": "2026-11-02"}) == {
        "version": "2026.11.02.1", "date": "2026-11-02"}


def test_existing_read_tool_fixtures(client, admin_env, monkeypatch):
    from app.admin_mcp.registry import READ_TOOLS
    monkeypatch.setattr(log_buffer, "get_entries", lambda *a: [{
        "level": "ERROR", "msg": "request failed 42", "ts_iso": "2026-11-02T12:00:00Z"}])
    token = oauth.tokens(client, admin_env)["access_token"]
    for name in READ_TOOLS:
        answer = call(client, token, name, {"geraet_id": 2147483647} if name == "admin_geraet_details" else {})
        assert_dates(answer)


def test_seventeen_value_signatures_collapse_without_raw_text(monkeypatch):
    start = datetime(2026, 11, 2, tzinfo=UTC)
    rows = [{"level": "ERROR", "msg": (
        f"request failed job {i} id={uuid4()} hex={i + 10000000:08x} "
        f"'Privatperson {i}' 192.168.1.{i} person{i}@example.org https://example.org/jobs/{i} /jobs/{i}"),
        "ts_iso": (start + timedelta(seconds=i)).isoformat(),
        "pathname": "app/services/code_analyse.py", "funcName": "group_logs", "lineno": 125,
    } for i in range(17)]
    monkeypatch.setattr(log_buffer, "get_entries", lambda *a: rows)
    result = collect(None, "admin_logs_analysieren", level="ERROR")
    groups = result["evidence"]["gruppen"]
    assert len(groups) == 1 and groups[0]["anzahl"] == 17
    assert groups[0]["beispiel_ort"]["zeile"] == 125
    assert groups[0]["erstes_auftreten"] == "2026-11-02T00:00:00Z"
    assert groups[0]["letztes_auftreten"] == "2026-11-02T00:00:16Z"
    template = groups[0]["template"]
    assert all(placeholder in template for placeholder in ("<n>", "<id>", "<str>", "<ip>", "<email>", "<url>", "<path>"))
    assert len(template) <= 160
    assert not any(raw in json.dumps(result) for raw in ("Privatperson", "example.org", "192.168", "/jobs/"))
    assert_dates(result)
