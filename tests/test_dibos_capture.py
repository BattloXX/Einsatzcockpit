"""Tests für dibos_capture.py: Datei-Aufzeichnung, Live-Snapshot, Ein-Zyklus-Polling,
Retention.

Kein echter DIBOS-Netzwerkzugriff nötig — verwendet einen Fake-Client, der wie
DibosClient asynchrone Methoden bereitstellt und den on_exchange-Hook manuell
aufruft (spiegelt das Verhalten von DibosClient._post).
"""
import asyncio
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from app.services.dibos.dibos_capture import (
    TRACE_RETENTION_DAYS,
    ExchangeRecorder,
    _bundle_trace_into_zip,
    _capture_once,
    capture_traffic,
    purge_old_traces,
    trace_run_dir,
)

_SAMPLE_EVENT = {
    "eventNumber": "f26006249", "ag": "FW", "tycodDescription": "geringer technischer Einsatz",
    "eventComment": "Testkommentar", "created": "2026-07-15T18:00:01",
    "dispatched": "2026-07-15T18:01:13", "closed": None,
    "callerList": [{"callerName": "Muster Max", "callerNumber": "0664123456"}],
    "targetList": [{"target": "19101", "targetType": "ALARMTEXT"}],
}


class _FakeDibosClient:
    """Simuliert DibosClient: ruft on_exchange wie der echte Client nach jedem
    (fingierten) HTTP-Austausch mit der Operation als Namen auf."""

    def __init__(self, events=None, on_exchange=None, fail_ops=()):
        self._events = events if events is not None else []
        self.on_exchange = on_exchange
        self.calls: list[str] = []
        self._fail_ops = set(fail_ops)
        self.closed = False

    def _fire(self, operation: str, payload) -> None:
        if self.on_exchange:
            self.on_exchange(
                f"http://fake.example/{operation}",
                operation,
                b"<fake-request/>",
                json.dumps(payload).encode("utf-8"),
            )

    async def _maybe_fail(self, operation: str):
        if operation in self._fail_ops:
            from app.services.dibos.dibos_client import DibosClientError
            raise DibosClientError(f"{operation} fehlgeschlagen (Test)")

    async def get_current_events(self):
        self.calls.append("get_current_events")
        await self._maybe_fail("GetCurrentEvents")
        self._fire("GetCurrentEvents", self._events)
        return self._events

    async def get_public_events(self, qty=15):
        self.calls.append("get_public_events")
        await self._maybe_fail("GetPublicEvents")
        self._fire("GetPublicEvents", [])
        return []

    async def get_current_units(self):
        self.calls.append("get_current_units")
        await self._maybe_fail("GetCurrentUnits")
        self._fire("GetCurrentUnits", [])
        return []

    async def get_current_radios(self):
        self.calls.append("get_current_radios")
        await self._maybe_fail("GetCurrentRadios")
        self._fire("GetCurrentRadios", [])
        return []

    async def get_elvis_notification(self):
        self.calls.append("get_elvis_notification")
        await self._maybe_fail("GetElvisNotification")
        self._fire("GetElvisNotification", [])
        return []

    async def aclose(self):
        self.closed = True


# ── ExchangeRecorder: Datei-Schreiben + Live-Snapshot ───────────────────────

def test_exchange_recorder_writes_request_and_response_files(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-1")
    recorder.record("http://x/svc", "GetCurrentUnits", b"<req/>", b"[]")

    files = sorted(p.name for p in tmp_path.iterdir())
    assert any(f.endswith("_request.xml") for f in files)
    assert any(f.endswith("_response.json") for f in files)
    assert len(recorder.exchanges) == 1
    assert recorder.exchanges[0]["operation"] == "GetCurrentUnits"
    assert recorder.exchanges[0]["truncated"] is False


def test_exchange_recorder_write_summary_content(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=42, run_id="run-2")
    recorder.record("http://x/svc", "GetCurrentEvents", b"<req/>", b"[]")
    summary_path = recorder.write_summary(duration_minutes=5, finished=True)

    data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert data["org_id"] == 42
    assert data["run_id"] == "run-2"
    assert data["finished"] is True
    assert data["exchange_count"] == 1


def test_exchange_recorder_truncates_large_responses_and_skips_live_snapshot(tmp_path, monkeypatch):
    import app.services.dibos.dibos_capture as mod
    monkeypatch.setattr(mod, "_MAX_CAPTURED_BYTES", 10)

    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-3")
    big_response = json.dumps([_SAMPLE_EVENT] * 5).encode("utf-8")
    recorder.record("http://x/svc", "GetCurrentEvents", b"<req/>", big_response)

    assert recorder.exchanges[0]["truncated"] is True
    resp_file = tmp_path / recorder.exchanges[0]["response_file"]
    assert resp_file.stat().st_size == 10
    assert "events" not in recorder.latest  # zu groß -> nicht in den Live-Snapshot übernommen


def test_exchange_recorder_updates_live_snapshot_for_known_operations(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-4")
    recorder.record("http://x/svc", "GetCurrentEvents", b"<req/>", json.dumps([_SAMPLE_EVENT]).encode("utf-8"))

    assert recorder.latest["updated_at"] is not None
    assert recorder.latest["events"][0]["eventNumber"] == "f26006249"


def test_exchange_recorder_ignores_unknown_operation_for_live_snapshot(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-5")
    recorder.record("http://x/svc", "GetElvisNotification", b"<req/>", b"[]")

    assert recorder.latest == {"updated_at": None}


def test_exchange_recorder_write_latest_content(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-6")
    recorder.record("http://x/svc", "GetCurrentUnits", b"<req/>", b'[{"unid": "tlf1_wolfu"}]')
    latest_path = recorder.write_latest()

    data = json.loads(latest_path.read_text(encoding="utf-8"))
    assert data["units"][0]["unid"] == "tlf1_wolfu"


# ── _capture_once: ein Poll-Zyklus über alle fünf Endpunkte ─────────────────

def test_capture_once_calls_all_five_endpoints_and_writes_latest(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-7")
    client = _FakeDibosClient([_SAMPLE_EVENT], on_exchange=recorder.record)

    asyncio.run(_capture_once(client, recorder))

    assert client.calls == [
        "get_current_events", "get_public_events", "get_current_units",
        "get_current_radios", "get_elvis_notification",
    ]
    assert len(recorder.exchanges) == 5
    assert (tmp_path / "latest.json").is_file()


def test_capture_once_continues_after_one_endpoint_fails(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-8")
    client = _FakeDibosClient([], on_exchange=recorder.record, fail_ops={"GetCurrentUnits"})

    asyncio.run(_capture_once(client, recorder))

    # Alle fünf Endpunkte wurden versucht, auch nach dem Fehlschlag von GetCurrentUnits
    assert client.calls == [
        "get_current_events", "get_public_events", "get_current_units",
        "get_current_radios", "get_elvis_notification",
    ]
    # Nur 4 Exchanges, weil GetCurrentUnits fehlgeschlagen ist (kein on_exchange-Aufruf)
    assert len(recorder.exchanges) == 4


# ── Retention: alte Läufe löschen, laufende nie ─────────────────────────────

def test_purge_old_traces_deletes_expired_runs(tmp_path, monkeypatch):
    import app.services.dibos.dibos_capture as mod
    monkeypatch.setattr(mod, "TRACE_ROOT", tmp_path)

    old_run_id = (datetime.now(UTC) - timedelta(days=TRACE_RETENTION_DAYS + 1)).strftime("%Y%m%dT%H%M%SZ")
    fresh_run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    old_dir = tmp_path / "1" / old_run_id
    fresh_dir = tmp_path / "1" / fresh_run_id
    old_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    (old_dir / "summary.json").write_text("{}", encoding="utf-8")
    (fresh_dir / "summary.json").write_text("{}", encoding="utf-8")

    deleted = purge_old_traces()

    assert deleted == 1
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_purge_old_traces_never_deletes_running_trace(tmp_path, monkeypatch):
    import app.services.dibos.dibos_capture as mod
    monkeypatch.setattr(mod, "TRACE_ROOT", tmp_path)

    old_run_id = (datetime.now(UTC) - timedelta(days=TRACE_RETENTION_DAYS + 1)).strftime("%Y%m%dT%H%M%SZ")
    org_id = 99
    run_dir = tmp_path / str(org_id) / old_run_id
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(mod, "_active_run_ids", {org_id: old_run_id})

    class _AlwaysRunningTask:
        def done(self):
            return False

    monkeypatch.setattr(mod, "_active_traces", {org_id: _AlwaysRunningTask()})

    deleted = purge_old_traces()

    assert deleted == 0
    assert run_dir.exists()


def test_trace_run_dir_path_structure(tmp_path, monkeypatch):
    import app.services.dibos.dibos_capture as mod
    monkeypatch.setattr(mod, "TRACE_ROOT", tmp_path)
    d = trace_run_dir(7, "run-xyz")
    assert d == tmp_path / "7" / "run-xyz"


# ── ZIP-Bündelung beim Beenden (Zeit abgelaufen ODER "Abbrechen" geklickt) ──

def test_bundle_trace_into_zip_creates_single_zip_and_keeps_summary_and_latest(tmp_path):
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-bundle")
    recorder.record("http://x/svc", "GetCurrentEvents", b"<req/>", b"[]")
    recorder.write_summary(duration_minutes=5, finished=True)
    recorder.write_latest()

    zip_path = _bundle_trace_into_zip(tmp_path, "run-bundle")

    assert zip_path == tmp_path / "run-bundle.zip"
    assert zip_path.is_file()
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "latest.json").is_file()  # bleibt lose liegen (Admin-Live-Ansicht)
    remaining_raw = [
        p for p in tmp_path.iterdir()
        if p.is_file() and p.suffix != ".zip" and p.name not in ("summary.json", "latest.json")
    ]
    assert remaining_raw == []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert any(n.endswith("_request.xml") for n in names)
    assert any(n.endswith("_response.json") for n in names)
    assert "summary.json" not in names  # bleibt unkomprimiert liegen, nicht im ZIP


def test_bundle_trace_into_zip_noop_when_nothing_recorded(tmp_path):
    assert _bundle_trace_into_zip(tmp_path, "run-empty") is None
    assert not (tmp_path / "run-empty.zip").exists()


def test_capture_traffic_bundles_into_zip_when_time_runs_out_and_closes_client(tmp_path):
    """Läuft die volle Dauer natürlich ab (duration_minutes=0 -> sofort fertig):
    die Rohdateien müssen trotzdem zu einem ZIP zusammengefasst und der Client
    geschlossen werden (Cookie-Jar-Client, anders als LisClient nicht pro Request)."""
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-finish")
    client = _FakeDibosClient([_SAMPLE_EVENT], on_exchange=recorder.record)

    out_dir = asyncio.run(capture_traffic(client, recorder, duration_minutes=0, poll_interval_seconds=1))

    zip_path = out_dir / "run-finish.zip"
    assert zip_path.is_file()
    assert client.closed is True
    data = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert data["finished"] is True
    assert (out_dir / "latest.json").is_file()


def test_capture_traffic_bundles_into_zip_on_cancel(tmp_path):
    """Wird die Aufzeichnung mittendrin per "Abbrechen" abgebrochen (task.cancel(),
    wie im /admin/dibos/trace/{run_id}/cancel-Endpoint), müssen die bis dahin
    aufgezeichneten Rohdateien trotzdem zu einem ZIP zusammengefasst werden."""
    recorder = ExchangeRecorder(tmp_path, org_id=1, run_id="run-cancel")
    cycle_done = asyncio.Event()

    class _SignallingClient(_FakeDibosClient):
        async def get_elvis_notification(self):
            result = await super().get_elvis_notification()
            cycle_done.set()
            return result

    client = _SignallingClient([_SAMPLE_EVENT], on_exchange=recorder.record)

    async def _run():
        task = asyncio.create_task(
            capture_traffic(client, recorder, duration_minutes=120, poll_interval_seconds=60)
        )
        await cycle_done.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())

    zip_path = tmp_path / "run-cancel.zip"
    assert zip_path.is_file()
    assert client.closed is True
    data = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert data["finished"] is False
