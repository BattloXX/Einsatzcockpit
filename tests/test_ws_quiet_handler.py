"""Benigne WebSocket-Trennungen (keepalive ping timeout, 1011) sollen nicht als
ERROR im asyncio-Log landen – aber echte Fehler weiterhin schon."""
from __future__ import annotations

import asyncio

from websockets.exceptions import ConnectionClosedError

import app.main as m


async def _run_case(context_builder) -> int:
    """Installiert den Handler, feuert einen Kontext und zählt Default-Handler-Aufrufe."""
    m._install_ws_quiet_exception_handler()
    loop = asyncio.get_running_loop()
    calls = {"n": 0}
    loop.default_exception_handler = lambda ctx: calls.__setitem__("n", calls["n"] + 1)
    loop.call_exception_handler(context_builder(loop))
    return calls["n"]


async def test_connection_closed_in_context_suppressed():
    n = await _run_case(lambda loop: {"message": "shielded", "exception": ConnectionClosedError(None, None)})
    assert n == 0  # gedämpft → Default-Handler NICHT aufgerufen


async def test_connection_closed_on_future_suppressed():
    def build(loop):
        fut = loop.create_future()
        fut.set_exception(ConnectionClosedError(None, None))
        return {"message": "ConnectionClosedError exception in shielded future", "future": fut}
    n = await _run_case(build)
    assert n == 0  # exakt der Prod-Log-Fall (Exception am Future)


async def test_real_error_passes_through():
    def build(loop):
        fut = loop.create_future()
        fut.set_exception(RuntimeError("echter Bug"))
        return {"message": "boom", "future": fut}
    n = await _run_case(build)
    assert n == 1  # echter Fehler → Default-Handler läuft


async def test_real_error_forwarded_to_previously_registered_handler():
    """Regression: war zuvor bereits ein Handler via set_exception_handler registriert
    (2-arg-Signatur (loop, context), z. B. von einer anderen Bibliothek), wurde er
    fälschlich mit nur einem Argument (context) aufgerufen -> TypeError bei echten
    Fehlern, sobald ein solcher Vorgänger-Handler existierte."""
    loop = asyncio.get_running_loop()
    calls: list[tuple] = []

    def prev_handler(loop_, context):
        calls.append((loop_, context))

    loop.set_exception_handler(prev_handler)
    m._install_ws_quiet_exception_handler()

    fut = loop.create_future()
    fut.set_exception(RuntimeError("echter Bug"))
    loop.call_exception_handler({"message": "boom", "future": fut})

    assert len(calls) == 1
    forwarded_loop, forwarded_context = calls[0]
    assert forwarded_loop is loop
    assert forwarded_context.get("future") is fut
