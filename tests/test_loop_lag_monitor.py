"""Schwellwert, Ruhe im Normalbetrieb und Abschalten des Loop-Lag-Monitors."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app import main
from app.config import Settings


async def test_monitor_only_warns_above_threshold_and_recovers(monkeypatch, caplog):
    monkeypatch.setattr(main.settings, "LOOP_LAG_WARN_MS", 1000)
    # Ohne Verzug, exakt auf der Schwelle, darueber, wieder normal.
    times = iter([0, 1, 1, 3, 3, 5.5, 5.5, 6.5, 6.5])
    clock = SimpleNamespace(time=lambda: next(times))
    sleeps = []

    async def sleep(seconds):
        assert seconds == 1.0
        if len(sleeps) == 4:
            raise asyncio.CancelledError
        sleeps.append(seconds)

    monkeypatch.setattr(main, "asyncio", SimpleNamespace(get_running_loop=lambda: clock, sleep=sleep))
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.loop_lag"):
        with pytest.raises(asyncio.CancelledError):
            await main._loop_lag_monitor()
    records = [record for record in caplog.records if record.name == "einsatzleiter.loop_lag"]
    assert len(records) == 1
    assert "1500 ms" in records[0].message


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
