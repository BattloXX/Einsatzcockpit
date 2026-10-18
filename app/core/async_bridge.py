"""Brücke für asynchrone Aufrufe aus synchronen FastAPI-Routen."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

import anyio


def run_async[T](fn: Callable[..., Awaitable[T]], /, *args: object, **kwargs: object) -> T:
    """Führt eine Coroutine-Funktion aus einer synchronen Route aus und wartet auf ihr Ergebnis.

    Synchrone FastAPI-Routen laufen im anyio-Worker-Thread. Von dort wird die
    Coroutine auf der Event-Loop des Requests ausgeführt. Das ist auch für
    Request-Methoden wie ``request.form()`` und ``request.json()`` nutzbar,
    etwa mit ``run_async(request.form)``.

    Außerhalb eines Requests verwendet die Funktion ``asyncio.run()``, sofern
    im aktuellen Thread keine Event-Loop läuft. Innerhalb einer laufenden Loop
    muss die Coroutine stattdessen direkt mit ``await`` aufgerufen werden.
    """
    coroutine = functools.partial(fn, *args, **kwargs)
    try:
        return anyio.from_thread.run(cast(Callable[[], Coroutine[Any, Any, T]], coroutine))
    except RuntimeError as error:
        if "AnyIO worker thread" not in str(error):
            raise

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        async def await_coroutine() -> T:
            return await coroutine()

        return asyncio.run(await_coroutine())
    raise RuntimeError(
        "run_async kann innerhalb einer laufenden Event-Loop nicht verwendet werden; "
        "der Aufrufer muss die Coroutine mit await aufrufen."
    )
