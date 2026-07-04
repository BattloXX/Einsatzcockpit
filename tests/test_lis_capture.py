"""Tests für lis_capture.py: Datei-Aufzeichnung, Ein-Zyklus-Polling, Retention.

Kein echter LIS-Netzwerkzugriff nötig — verwendet einen Fake-Client, der wie
LisClient asynchrone Methoden bereitstellt und den on_exchange-Hook manuell
aufruft (spiegelt das Verhalten von LisClient._post).
"""
import asyncio
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from app.services.lis.lis_capture import (
    CAPTURE_RETENTION_DAYS,
    ExchangeRecorder,
    _bundle_capture_into_zip,
    _capture_once,
    _current_run_id,
    capture_run_dir,
    capture_traffic,
    purge_old_captures,
)


class _FakeLisClient:
    """Simuliert LisClient: ruft on_exchange wie der echte Client nach jedem
    (fingierten) SOAP-Austausch auf, liefert aber vordefinierte Daten."""

    def __init__(self, operations, on_exchange=None):
        self._operations = operations
        self.on_exchange = on_exchange
        self.calls: list[str] = []

    def _fire(self, action: str) -> None:
        if self.on_exchange:
            self.on_exchange(
                "http://fake.example/OperationService.svc",
                f"http://services.intergraph.com/Emea/Pr/2011/03/OperationService/{action}",
                b"<fake-request/>",
                f"<fake-response op='{action}'/>".encode("utf-8"),
            )

    async def get_operations_in_range(self, organization_id, operation_filter="ActiveParticipation", count=50, start_index=0):
        self.calls.append("get_operations_in_range")
        self._fire("GetOperationsInRange")
        return self._operations

    async def get_tasks(self, operation_id):
        self.calls.append(f"get_tasks:{operation_id}")
        self._fire("GetTasks")
        return []

    async def get_operation_units(self, organization_id, operation_id):
        self.calls.append(f"get_operation_units:{operation_id}")
        self._fire("GetOperationUnits")
        return []

    async def get_documents_by_operation_id(self, operation_id, maximum_distance=100):
        self.calls.append(f"get_documents_by_operation_id:{operation_id}")
        self._fire("GetDocumentsByOperationId")
        return []


# ── ExchangeRecorder: reines Datei-Schreiben ─────────────────────────────────

def test_exchange_recorder_writes_request_and_response_files(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-1")
    recorder.record("http://x/svc", ".../GetTasks", b"<req/>", b"<resp/>")

    files = sorted(p.name for p in tmp_path.iterdir())
    assert any(f.endswith("_request.xml") for f in files)
    assert any(f.endswith("_response.bin") for f in files)
    assert len(recorder.exchanges) == 1
    assert recorder.exchanges[0]["operation"] == "GetTasks"
    assert recorder.exchanges[0]["response_bytes"] == len(b"<resp/>")
    assert recorder.exchanges[0]["truncated"] is False


def test_exchange_recorder_write_summary_content(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=42, run_id="run-2")
    recorder.record("http://x/svc", ".../GetTasks", b"<req/>", b"<resp/>")
    summary_path = recorder.write_summary(duration_minutes=5, finished=True)

    data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert data["org_id"] == 42
    assert data["run_id"] == "run-2"
    assert data["finished"] is True
    assert data["exchange_count"] == 1


def test_exchange_recorder_truncates_large_responses(tmp_path, monkeypatch):
    import app.services.lis.lis_capture as mod
    monkeypatch.setattr(mod, "_MAX_CAPTURED_BYTES", 10)

    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-3")
    big_response = b"x" * 100
    recorder.record("http://x/svc", ".../DownloadDocument", b"<req/>", big_response)

    assert recorder.exchanges[0]["truncated"] is True
    resp_file = tmp_path / recorder.exchanges[0]["response_file"]
    assert resp_file.stat().st_size == 10


# ── _capture_once: ein Poll-Zyklus, deterministisch (keine Zeitabhängigkeit) ──

def test_capture_once_records_all_endpoints_for_active_operation(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-4")
    client = _FakeLisClient([{"Id": "op-1"}], on_exchange=recorder.record)

    asyncio.run(_capture_once(client, recorder, organization_id="org-guid"))

    # 1x GetOperationsInRange + je 1x Tasks/Units/Documents für die eine Operation
    assert len(recorder.exchanges) == 4
    assert client.calls == [
        "get_operations_in_range", "get_tasks:op-1",
        "get_operation_units:op-1", "get_documents_by_operation_id:op-1",
    ]


def test_capture_once_no_operations_only_records_list_call(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-5")
    client = _FakeLisClient([], on_exchange=recorder.record)

    asyncio.run(_capture_once(client, recorder, organization_id="org-guid"))

    assert len(recorder.exchanges) == 1
    assert client.calls == ["get_operations_in_range"]


def test_capture_once_skips_operations_without_id(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-6")
    client = _FakeLisClient([{"Number": "f1"}], on_exchange=recorder.record)  # kein "Id"

    asyncio.run(_capture_once(client, recorder, organization_id="org-guid"))

    assert len(recorder.exchanges) == 1  # nur GetOperationsInRange, kein Sub-Call
    assert client.calls == ["get_operations_in_range"]


# ── Retention: alte Läufe löschen, laufende nie ──────────────────────────────

def test_purge_old_captures_deletes_expired_runs(tmp_path, monkeypatch):
    import app.services.lis.lis_capture as mod
    monkeypatch.setattr(mod, "CAPTURE_ROOT", tmp_path)

    old_run_id = (datetime.now(UTC) - timedelta(days=CAPTURE_RETENTION_DAYS + 1)).strftime("%Y%m%dT%H%M%SZ")
    fresh_run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    old_dir = tmp_path / "1" / old_run_id
    fresh_dir = tmp_path / "1" / fresh_run_id
    old_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    (old_dir / "summary.json").write_text("{}", encoding="utf-8")
    (fresh_dir / "summary.json").write_text("{}", encoding="utf-8")

    deleted = purge_old_captures()

    assert deleted == 1
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_purge_old_captures_never_deletes_running_capture(tmp_path, monkeypatch):
    import app.services.lis.lis_capture as mod
    monkeypatch.setattr(mod, "CAPTURE_ROOT", tmp_path)

    old_run_id = (datetime.now(UTC) - timedelta(days=CAPTURE_RETENTION_DAYS + 1)).strftime("%Y%m%dT%H%M%SZ")
    org_id = 99
    run_dir = tmp_path / str(org_id) / old_run_id
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")

    # Simuliert eine laufende Aufzeichnung für diese Org/run_id
    monkeypatch.setattr(mod, "_active_run_ids", {org_id: old_run_id})

    class _AlwaysRunningTask:
        def done(self):
            return False

    monkeypatch.setattr(mod, "_active_captures", {org_id: _AlwaysRunningTask()})

    deleted = purge_old_captures()

    assert deleted == 0
    assert run_dir.exists()


def test_capture_run_dir_path_structure(tmp_path, monkeypatch):
    import app.services.lis.lis_capture as mod
    monkeypatch.setattr(mod, "CAPTURE_ROOT", tmp_path)
    d = capture_run_dir(7, "run-xyz")
    assert d == tmp_path / "7" / "run-xyz"


# ── ZIP-Bündelung beim Beenden (Zeit abgelaufen ODER "Abbrechen" geklickt) ───

def test_bundle_capture_into_zip_creates_single_zip_and_removes_raw_files(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-bundle")
    recorder.record("http://x/svc", ".../GetTasks", b"<req/>", b"<resp/>")
    recorder.write_summary(duration_minutes=5, finished=True)

    zip_path = _bundle_capture_into_zip(tmp_path, "run-bundle")

    assert zip_path == tmp_path / "run-bundle.zip"
    assert zip_path.is_file()
    assert (tmp_path / "summary.json").is_file()  # bleibt lose liegen (Admin-UI-Liste)
    remaining_raw = [
        p for p in tmp_path.iterdir() if p.is_file() and p.suffix != ".zip" and p.name != "summary.json"
    ]
    assert remaining_raw == []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert any(n.endswith("_request.xml") for n in names)
    assert any(n.endswith("_response.bin") for n in names)
    assert "summary.json" in names


def test_bundle_capture_into_zip_noop_when_nothing_recorded(tmp_path):
    assert _bundle_capture_into_zip(tmp_path, "run-empty") is None
    assert not (tmp_path / "run-empty.zip").exists()


def test_capture_traffic_bundles_into_zip_when_time_runs_out(tmp_path):
    """Läuft die volle Dauer natürlich ab (duration_minutes=0 → sofort fertig):
    die Rohdateien müssen trotzdem zu einem ZIP zusammengefasst werden."""
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-finish")
    client = _FakeLisClient([{"Id": "op-1"}], on_exchange=recorder.record)

    out_dir = asyncio.run(
        capture_traffic(client, recorder, "org-guid", duration_minutes=0, poll_interval_seconds=1)
    )

    zip_path = out_dir / "run-finish.zip"
    assert zip_path.is_file()
    data = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert data["finished"] is True
    remaining_raw = [
        p for p in out_dir.iterdir() if p.is_file() and p.suffix != ".zip" and p.name != "summary.json"
    ]
    assert remaining_raw == []


def test_capture_traffic_bundles_into_zip_on_cancel(tmp_path):
    """Wird die Aufzeichnung mittendrin per "Abbrechen" abgebrochen (task.cancel(),
    wie im /admin/lis/capture/{run_id}/cancel-Endpoint), müssen die bis dahin
    aufgezeichneten Rohdateien trotzdem zu einem ZIP zusammengefasst werden."""
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-cancel")
    cycle_done = asyncio.Event()

    class _SignallingClient(_FakeLisClient):
        async def get_documents_by_operation_id(self, operation_id, maximum_distance=100):
            result = await super().get_documents_by_operation_id(operation_id, maximum_distance)
            cycle_done.set()
            return result

    client = _SignallingClient([{"Id": "op-1"}], on_exchange=recorder.record)

    async def _run():
        task = asyncio.create_task(
            capture_traffic(client, recorder, "org-guid", duration_minutes=120, poll_interval_seconds=60)
        )
        await cycle_done.wait()  # erster Poll-Zyklus ist durch, Task hängt im Sleep bis zum nächsten
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())

    zip_path = tmp_path / "run-cancel.zip"
    assert zip_path.is_file()
    data = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert data["finished"] is False
    remaining_raw = [
        p for p in tmp_path.iterdir() if p.is_file() and p.suffix != ".zip" and p.name != "summary.json"
    ]
    assert remaining_raw == []
