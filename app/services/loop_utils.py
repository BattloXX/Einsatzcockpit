"""Gemeinsame Helfer für Background-Loops (Audit B2/C4).

Alle Loops laufen als asyncio-Tasks auf demselben Event-Loop wie die
HTTP-Requests und WebSockets. Synchrone DB-Arbeit gehört deshalb in
``asyncio.to_thread(...)`` — sonst friert jede Query den ganzen Worker ein.

``iteration_watch`` warnt, wenn eine Iteration länger dauert als ihr
Intervall: Frühwarnung dafür, dass ein Loop nicht mehr hinterherkommt
(z. B. langsame DB, hängender HTTP-Call).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def iteration_watch(logger: logging.Logger, name: str, interval_s: float) -> Iterator[None]:
    """Misst eine Loop-Iteration und warnt bei Überschreitung des Intervalls."""
    start = time.monotonic()
    try:
        yield
    finally:
        dauer = time.monotonic() - start
        if dauer > interval_s:
            logger.warning(
                "%s: Iteration dauerte %.1f s (Intervall %.0f s) — Loop kommt nicht hinterher",
                name, dauer, interval_s,
            )
