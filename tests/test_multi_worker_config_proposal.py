import runpy
from pathlib import Path

import pytest

CONFIG = Path("deploy/proposals/multi-worker/gunicorn.conf.py")


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
