"""Gemeinsame Helfer für Background-Loops (Audit B2/C4).

Alle Loops laufen als asyncio-Tasks auf demselben Event-Loop wie die
HTTP-Requests und WebSockets. Synchrone DB-Arbeit gehört deshalb in
``asyncio.to_thread(...)`` — sonst friert jede Query den ganzen Worker ein.

``iteration_watch`` warnt, wenn eine Iteration länger dauert als ihr
Intervall: Frühwarnung dafür, dass ein Loop nicht mehr hinterherkommt
(z. B. langsame DB, hängender HTTP-Call).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Any


def start_loop_task(
    task: asyncio.Task | None, factory: Callable[[], Coroutine[Any, Any, None]],
) -> asyncio.Task:
    """Behält nur einen laufenden Task des aktuellen Loops; fremde Tasks verwerfen."""
    if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
        return asyncio.create_task(factory())
    return task


async def stop_loop_task(task: asyncio.Task | None) -> None:
    """Cancel und Await sind nur für Tasks des aktuellen Loops zulässig."""
    if task is None or task.get_loop() is not asyncio.get_running_loop():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@contextmanager
def iteration_watch(logger: logging.Logger, name: str, interval_s: float) -> Iterator[None]:
    """Misst eine Loop-Iteration und warnt bei Überschreitung des Intervalls."""
    start = time.monotonic()
    from app.services.background_runtime import record_iteration

    record_iteration(name, "running", interval_s=interval_s)
    try:
        yield
    except BaseException:
        record_iteration(name, "failed")
        raise
    else:
        record_iteration(name, "ok")
    finally:
        dauer = time.monotonic() - start
        if dauer > interval_s:
            logger.warning(
                "%s: Iteration dauerte %.1f s (Intervall %.0f s) — Loop kommt nicht hinterher",
                name, dauer, interval_s,
            )
