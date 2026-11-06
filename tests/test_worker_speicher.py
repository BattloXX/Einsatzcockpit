"""Import-Speicher und Diagnose bei Gunicorn-Timeouts absichern."""

import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_annotation_import_ohne_weasyprint(tmp_path):
    ergebnis = subprocess.run(
        [sys.executable, "-c", "import sys; import app.services.objekt_seite_annotation; "
         "assert not any(n == 'weasyprint' or n.startswith('weasyprint.') for n in sys.modules)"],
        cwd=REPO, env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'import.db'}"},
        capture_output=True, text=True, timeout=60,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr


@pytest.mark.parametrize("log_fehler,dump_fehler", [(False, False), (True, False), (False, True), (True, True)])
def test_worker_abort_exception_sicher(monkeypatch, log_fehler, dump_fehler):
    monkeypatch.setenv("ECP_GUNICORN_WORKERS", "1")
    konf = runpy.run_path(str(REPO / "deploy/proposals/multi-worker/gunicorn.conf.py"))
    assert callable(konf["worker_abort"])
    critical = Mock(side_effect=RuntimeError("Log nicht verfuegbar") if log_fehler else None)
    dump = Mock(side_effect=OSError("stderr nicht verfuegbar") if dump_fehler else None)
    monkeypatch.setattr(konf["faulthandler"], "dump_traceback", dump)
    konf["worker_abort"](SimpleNamespace(pid=123, log=SimpleNamespace(critical=critical)))
    critical.assert_called_once_with("WORKER TIMEOUT: Thread-Stacks fuer PID %s", 123)
    dump.assert_called_once_with(file=sys.stderr, all_threads=True)


@pytest.mark.parametrize("vorhanden", [False, True])
def test_worker_tmp_dir_nur_wenn_vorhanden(monkeypatch, vorhanden):
    monkeypatch.setenv("ECP_GUNICORN_WORKERS", "1")
    monkeypatch.setattr(os.path, "isdir", lambda pfad: vorhanden if pfad == "/dev/shm" else False)
    konf = runpy.run_path(str(REPO / "deploy/proposals/multi-worker/gunicorn.conf.py"))
    assert konf.get("worker_tmp_dir") == ("/dev/shm" if vorhanden else None)


def test_worker_abort_schreibt_thread_stacks(monkeypatch, capfd):
    monkeypatch.setenv("ECP_GUNICORN_WORKERS", "1")
    konf = runpy.run_path(str(REPO / "deploy/proposals/multi-worker/gunicorn.conf.py"))
    critical = Mock()
    konf["worker_abort"](SimpleNamespace(pid=123, log=SimpleNamespace(critical=critical)))
    ausgabe = capfd.readouterr().err
    assert "Current thread" in ausgabe
    assert "gunicorn.conf.py" in ausgabe
    assert "test_worker_abort_schreibt_thread_stacks" in ausgabe
