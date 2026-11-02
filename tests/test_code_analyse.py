"""Pfade und technische Evidenz verlassen niemals den lokalen App-Baum."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import code_analyse as code
from app.services.diagnose.deploy import check_deploy
from app.services.diagnose.registry import Budget, CHECKS

SECRET = "seed_custom_secret_xyz123"
TRACE = 'File "app/services/code_analyse.py", line 18, in group_logs\nValueError: '


def test_grouping_times_masking_and_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", SECRET)
    data = [
        {"ts": ts, "msg": TRACE + SECRET + " alice@example.org +43 664 1234567 192.168.1.2 Cookie: private=sentinel"}
        for ts in ("2026-10-27 11:00:00", "2026-10-27 09:00:00")]
    result = code.group_logs(data)
    assert len(result) == 1
    assert result[0]["anzahl"] == 2
    assert result[0]["funktion"] == "group_logs" and result[0]["exception_typ"] == "ValueError"
    assert result[0]["erstes_auftreten"] < result[0]["letztes_auftreten"]
    assert not any(v in json.dumps(result) for v in (SECRET, "alice@example.org", "+43", "192.168", "sentinel"))
    assert code.extract_places("app.services.code_analyse.group_logs")[0]["funktion"] == "group_logs"
    assert code.extract_places("app/services/code_analyse.py")[0]["datei"] == "app/services/code_analyse.py"
    assert code.extract_places(TRACE.replace("group_logs", "alice_private"))[0]["funktion"] == "unbekannt"


@pytest.mark.parametrize("raw", ["../app/config.py", "app/../config.py", "/etc/passwd", "/evil/app/config.py",
    "app/no_such_file.py", "app/config.py.bak", r"C:\srv\app\config.py", "app/services/../../config.py"])
def test_invalid_paths(raw):
    assert code.local_path(raw) is None
    assert code.extract_places(f'File "{raw}", line 1, in main') == []
    assert code.extract_places(raw) == []


def test_absolute_local_stack_frame_and_symlinks(tmp_path, monkeypatch):
    assert code.extract_places(f'File "{code.APP_ROOT}/config.py", line 1, in <module>')
    app = tmp_path / "app"
    app.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("secret = 42")
    (app / "secret.py").symlink_to(outside)
    (app / "nested").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(code, "APP_ROOT", app)
    assert code.local_path("app/secret.py") is None
    assert code.local_path("app/nested/secret.py") is None


def test_finding_static_mapping():
    now = datetime(2026, 10, 27)
    finding = SimpleNamespace(check_id="gateway.jobs", evidenz={}, occurrences=4, first_seen_at=now, last_seen_at=now)
    groups = code.finding_groups(finding)
    assert "app/services/alarm_outbox.py" in [g["datei"] for g in groups]
    assert all(g["anzahl"] == 4 for g in groups)


@pytest.mark.parametrize("sha", ["", "unknown"])
def test_deploy_unknown(sha, monkeypatch):
    monkeypatch.setattr(settings, "GIT_SHA", sha)
    result = check_deploy(None, Budget())
    assert result.findings[0].schweregrad == "warn"
    assert CHECKS["deploy.version"].kosten == "leicht"


def test_deploy_behind_and_down(monkeypatch):
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    budget = Budget()
    budget.runtime["deploy"] = {"status": "eingeschraenkt", "daten": {"commits_hinter_main": 3}}
    result = check_deploy(None, budget)
    assert result.findings[0].evidenz == {"commits_hinter_main": 3}
    budget.runtime["deploy"] = {"status": "eingeschraenkt", "code": "timeout"}
    result = check_deploy(None, budget)
    assert not result.findings and not result.vollstaendig and "unbekannt" in result.hinweis
    budget.runtime["deploy"] = {"status": "ok", "daten": {"commits_hinter_main": 0}}
    assert not check_deploy(None, budget).findings


def test_finding_traceback_nested_evidence():
    now = datetime(2026, 10, 27)
    finding = SimpleNamespace(check_id="no_mapping", evidenz={"nested": [TRACE + SECRET]},
                             occurrences=7, first_seen_at=now, last_seen_at=now)
    groups = code.finding_groups(finding)
    assert groups[0]["funktion"] == "group_logs"
    assert SECRET not in json.dumps(groups)


def test_recursive_frames_count_one_log_event():
    rows = [{"msg": TRACE + TRACE.replace("line 18", "line 19"), "ts": "2026-10-27 10:00:00"}]
    result = code.group_logs(rows)
    assert len(result) == 1 and result[0]["anzahl"] == 1


def test_structured_log_record_and_builtin_exception_only():
    import logging
    from app.log_buffer import _MemoryLogHandler

    handler = _MemoryLogHandler()
    record = logging.LogRecord("app.services.code_analyse", logging.ERROR,
                               str(code.APP_ROOT / "services/code_analyse.py"), 123,
                               "private source text", (), None, "group_logs")
    try:
        raise ValueError("private caught exception")
    except ValueError:
        import sys
        record.exc_info = sys.exc_info()
    handler.emit(record)
    entry = handler.get_entries()[0]
    assert {"ts", "level", "name", "msg", "pathname", "funcName", "lineno", "ts_iso", "exception_typ"} <= entry.keys()
    assert entry["pathname"] == "app/services/code_analyse.py"
    group = code.group_logs([entry])[0]
    assert (group["funktion"], group["zeile"], group["exception_typ"]) == ("group_logs", 123, "ValueError")
    assert datetime.fromisoformat(group["erstes_auftreten"]).tzinfo is not None
    entry["exception_typ"] = "PrivateCustomerError"
    entry["msg"] = "private"
    assert code.group_logs([entry])[0]["exception_typ"] == "unbekannt"
    assert "private" not in json.dumps(code.group_logs([entry]))


@pytest.mark.parametrize("days,severity", [(0, "warn"), (14, "warn"), (15, "info"), (30, "info"), (31, None)])
def test_github_expiry_reminder(monkeypatch, days, severity):
    from datetime import timedelta
    from app.services import github_token
    now = datetime(2026, 11, 2, 12)
    monkeypatch.setattr(github_token, "now", lambda: now)
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", "fixture-token")
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    values = {"expires_at": (now + timedelta(days=days, seconds=1)).isoformat(), "last_status": "ok"}
    monkeypatch.setattr(github_token, "read", lambda db, key: values.get(key, ""))
    budget = Budget()
    budget.runtime["deploy"] = {"status": "ok", "daten": {"commits_hinter_main": 0}}
    findings = [f for f in check_deploy(None, budget).findings if f.kern == "github_token_laeuft_ab"]
    if severity is None:
        assert not findings
    else:
        finding = findings[0]
        assert finding.schweregrad == severity
        assert datetime.fromisoformat(finding.evidenz["ablauf_am"]).tzinfo is not None
        assert all(word in finding.empfehlung for word in ("Contents", "Actions", "Systemeinstellungen"))


def test_first_last_seen_compare_fractional_seconds_chronologically():
    rows = [{"msg": TRACE, "ts_iso": stamp} for stamp in (
        "2026-11-02T12:00:00.100Z", "2026-11-02T12:00:00Z")]
    result = code.group_logs(rows)[0]
    assert result["erstes_auftreten"] == "2026-11-02T12:00:00Z"
    assert result["letztes_auftreten"] == "2026-11-02T12:00:00.100000Z"
