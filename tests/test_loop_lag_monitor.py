"""Schwellwert, Ruhe im Normalbetrieb und Abschalten des Loop-Lag-Monitors."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app import main
from app.config import Settings


async def test_monitor_verzug_taskdump_gedrosselt(monkeypatch, caplog):
    monkeypatch.setattr(main.settings, "LOOP_LAG_WARN_MS", 1000)
    uhr = [0.0]
    verzoegerungen = iter([0.1, 1.0, 1.6, 2.0, 61.0])
    pulse = []
    aufgabe = SimpleNamespace(get_name=lambda: "Testaufgabe",
                             get_coro=lambda: SimpleNamespace(__qualname__="test_koroutine"))

    async def schlafen(sekunden):
        assert sekunden == 0.1
        try:
            uhr[0] += next(verzoegerungen)
        except StopIteration:
            raise asyncio.CancelledError from None

    monkeypatch.setattr(main, "asyncio", SimpleNamespace(
        get_running_loop=lambda: SimpleNamespace(time=lambda: uhr[0]),
        sleep=schlafen, all_tasks=lambda: [aufgabe] * 8))
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.loop_lag"):
        with pytest.raises(asyncio.CancelledError):
            await main._loop_lag_monitor(SimpleNamespace(puls=lambda: pulse.append(uhr[0])))
    verzug = [r.message for r in caplog.records if "worker_id=" in r.message]
    dumps = [r.message for r in caplog.records if "aktive Tasks" in r.message]
    assert len(verzug) == 3
    assert "1500 ms" in verzug[0]
    assert len(dumps) == 2
    assert all(dump.count("Testaufgabe test_koroutine") == 5 for dump in dumps)
    assert len(pulse) == 6


async def test_zero_disables_monitor(monkeypatch):
    monkeypatch.setattr(main.settings, "LOOP_LAG_WARN_MS", 0)
    # Deaktiviert: weder Loop noch Timer werden benoetigt.
    monkeypatch.setattr(main, "asyncio", SimpleNamespace())
    await main._loop_lag_monitor()


def test_loop_lag_setting_default_and_env(monkeypatch):
    monkeypatch.delenv("LOOP_LAG_WARN_MS", raising=False)
    assert Settings(_env_file=None).LOOP_LAG_WARN_MS == 1000
    monkeypatch.setenv("LOOP_LAG_WARN_MS", "0")
    assert Settings(_env_file=None).LOOP_LAG_WARN_MS == 0


def test_watchdog_einmal_je_vorfall_mit_stack(monkeypatch, caplog):
    from pathlib import Path

    from app.services import loop_lag_watchdog as modul

    uhr = [0.0]
    watchdog = modul.VerzugsWatchdog(1000, zeitquelle=lambda: uhr[0])
    frame = None
    for nummer in range(8):
        frame = SimpleNamespace(f_code=SimpleNamespace(
            co_filename=str(watchdog.repo / "app/main.py"), co_name=f"funktion_{nummer}"),
            f_lineno=100 + nummer, f_back=frame)
    monkeypatch.setattr(modul.sys, "_current_frames", lambda: {watchdog.hauptthread_id: frame})
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.loop_lag"):
        uhr[0] = 1.0
        watchdog.pruefe()
        assert not caplog.records
        uhr[0] = 1.1
        watchdog.pruefe()
        uhr[0] = 2.0
        watchdog.pruefe()
        assert len(caplog.records) == 1
        watchdog.puls()
        watchdog.pruefe()
        uhr[0] = 3.1
        watchdog.pruefe()
    assert len(caplog.records) == 2
    assert "app/main.py:107 funktion_7" in caplog.text
    assert "funktion_1" not in caplog.text
    assert caplog.records[0].message.count("app/main.py:") == 6
    assert str(Path.cwd()) not in caplog.text


def test_watchdog_thread_stoppt():
    from app.services.loop_lag_watchdog import VerzugsWatchdog

    watchdog = VerzugsWatchdog(1000)
    watchdog.starten()
    assert watchdog.thread.daemon and watchdog.thread.is_alive()
    watchdog.stoppen()
    assert not watchdog.thread.is_alive()
