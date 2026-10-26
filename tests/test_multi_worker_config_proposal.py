import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

CONFIG = Path("deploy/proposals/multi-worker/gunicorn.conf.py")


@pytest.mark.parametrize(
    "count,pool,overflow,maximum,warns",
    [(4, 10, 30, 151, True), (2, 10, 20, 151, False),
     (3, 8, 15, 151, False), (4, 8, 12, 151, False),
     (4, 10, 30, 180, False), (4, 10, 30, 179, True)],
)
def test_master_warns_only_when_connection_budget_exceeds_limit(monkeypatch, count, pool, overflow, maximum, warns):
    for name, value in {"ECP_GUNICORN_WORKERS": count, "DB_POOL_SIZE": pool,
                        "DB_MAX_OVERFLOW": overflow, "DB_MAX_CONNECTIONS": maximum}.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("REDIS_URL", "redis://test/0")
    server = SimpleNamespace(log=Mock())
    runpy.run_path(str(CONFIG))["on_starting"](server)
    assert server.log.warning.call_count == int(warns)
    if warns:
        assert "DB-Verbindungsbudget UEBERSCHRITTEN" in server.log.warning.call_args.args[0]


def test_master_connection_budget_defaults(monkeypatch):
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_MAX_CONNECTIONS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ECP_GUNICORN_WORKERS", "4")
    monkeypatch.setenv("REDIS_URL", "redis://test/0")
    server = SimpleNamespace(log=Mock())
    runpy.run_path(str(CONFIG))["on_starting"](server)
    assert server.log.warning.call_args.args[1:] == (4, 10, 30, 180, 151)


@pytest.mark.parametrize("count", ["1", "2", "3", "4"])
def test_opt_in_worker_count_and_existing_runtime_parameters(monkeypatch, count):
    monkeypatch.setenv("ECP_GUNICORN_WORKERS", count)
    monkeypatch.setenv("REDIS_URL", "redis://test/0")
    data = runpy.run_path(str(CONFIG))
    assert data["workers"] == int(count)
    assert data["worker_class"] == "app.uvicorn_worker.TolerantUvicornWorker"
    assert data["timeout"] == data["graceful_timeout"] == 120
    assert not data["preload_app"]


def test_proposal_defaults_to_single_worker_without_redis(monkeypatch):
    monkeypatch.delenv("ECP_GUNICORN_WORKERS", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert runpy.run_path(str(CONFIG))["workers"] == 1


@pytest.mark.parametrize("count", ["0", "5", "2.0", "two", "-1", ""])
def test_invalid_count_is_rejected(monkeypatch, count):
    monkeypatch.setenv("ECP_GUNICORN_WORKERS", count)
    with pytest.raises(RuntimeError):
        runpy.run_path(str(CONFIG))


def test_multiple_workers_without_redis_are_rejected(monkeypatch):
    monkeypatch.setenv("ECP_GUNICORN_WORKERS", "2")
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(RuntimeError, match="Redis"):
        runpy.run_path(str(CONFIG))
