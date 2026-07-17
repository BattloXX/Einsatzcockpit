"""WS-Broadcast parallel mit Timeout (Audit B6) + Slow-Query-Logging (B7)."""
import asyncio
import logging

import pytest

from app.services import broadcast as bc


class _FakeWS:
    """Nachbau eines WebSockets: sammelt Sendungen, kann hängen oder werfen."""

    def __init__(self, *, hang: bool = False, fail: bool = False):
        self.hang = hang
        self.fail = fail
        self.received: list[str] = []

    async def send_text(self, payload: str) -> None:
        if self.hang:
            await asyncio.sleep(60)
        if self.fail:
            raise RuntimeError("kaputt")
        self.received.append(payload)


@pytest.fixture
def fresh_manager(monkeypatch):
    mgr = bc.ConnectionManager()
    # Timeout kurz, damit der Hänger-Test nicht real 2 s wartet
    monkeypatch.setattr(bc, "_SEND_TIMEOUT_S", 0.1)
    return mgr


def test_haengender_client_blockiert_andere_nicht(fresh_manager):
    """Ein hängender Socket darf die Zustellung an die übrigen nicht aufhalten
    und wird nach dem Timeout aus dem Kanal entfernt."""
    ok1, haenger, ok2 = _FakeWS(), _FakeWS(hang=True), _FakeWS()
    fresh_manager._connections[1] = {ok1, haenger, ok2}

    async def run():
        start = asyncio.get_event_loop().time()
        await fresh_manager._deliver_local(1, {"type": "test"})
        return asyncio.get_event_loop().time() - start

    dauer = asyncio.run(run())
    assert ok1.received and ok2.received
    assert dauer < 1.0  # parallel + Timeout statt 60 s seriell warten
    assert haenger not in fresh_manager._connections[1]
    assert {ok1, ok2} <= fresh_manager._connections[1]


def test_kaputter_client_wird_entfernt(fresh_manager):
    ok, kaputt = _FakeWS(), _FakeWS(fail=True)
    fresh_manager._connections[7] = {ok, kaputt}
    asyncio.run(fresh_manager._deliver_local(7, {"type": "x"}))
    assert ok.received
    assert kaputt not in fresh_manager._connections[7]


def test_deliver_all_erreicht_alle_kanaele(fresh_manager):
    a, b = _FakeWS(), _FakeWS()
    fresh_manager._connections[1] = {a}
    fresh_manager._connections[2] = {b}
    asyncio.run(fresh_manager._deliver_all_local({"type": "global"}))
    assert a.received and b.received


def test_slow_query_logging_warnt_ueber_schwelle(caplog):
    import time as time_mod

    from sqlalchemy import create_engine, event, text

    from app.db import register_slow_query_logging

    engine = create_engine("sqlite:///:memory:")
    register_slow_query_logging(engine, threshold_ms=10)

    # sqlite-Funktion, die eine langsame Query simuliert
    @event.listens_for(engine, "connect")
    def _add_sleep(dbapi_conn, rec):
        dbapi_conn.create_function("py_sleep", 1, lambda s: time_mod.sleep(s) or 1)

    with caplog.at_level(logging.WARNING, logger="einsatzleiter.slow_query"):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))                 # schnell → kein Log
            conn.execute(text("SELECT py_sleep(0.05)"))    # ~50 ms → Warnung

    meldungen = [r.message for r in caplog.records]
    assert any("Langsame Query" in m and "py_sleep" in m for m in meldungen)
    assert not any("SELECT 1" == m[-8:] for m in meldungen)


def test_slow_query_logging_schwelle_null_deaktiviert(caplog):
    from sqlalchemy import create_engine, text

    from app.db import register_slow_query_logging

    engine = create_engine("sqlite:///:memory:")
    register_slow_query_logging(engine, threshold_ms=0)
    with caplog.at_level(logging.WARNING, logger="einsatzleiter.slow_query"):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    assert not caplog.records
