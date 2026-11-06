"""Async-I/O auf dem aufrufenden Loop aus einer thread-eigenen DB-Transaktion.

Verzahnte Bestandsablaeufe behalten ihre Session bis zum letzten Commit im
DB-Thread. Nur I/O-Aufrufe mit einfachen Nutzdaten werden zurueckgereicht.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import threading
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

_io_loop: ContextVar[asyncio.AbstractEventLoop | None] = ContextVar("loop_thread_io", default=None)


async def auf_event_loop[T](funktion: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
    loop = _io_loop.get()
    if loop is None or loop is asyncio.get_running_loop():
        return await funktion(*args, **kwargs)

    async def aufrufen() -> T:
        return await funktion(*args, **kwargs)

    zukunft = asyncio.run_coroutine_threadsafe(aufrufen(), loop)
    try:
        return await asyncio.wrap_future(zukunft)
    except asyncio.CancelledError:
        zukunft.cancel()
        raise


@dataclass
class _ThreadAuftrag:
    sperre: threading.Lock = field(default_factory=threading.Lock)
    abgebrochen: bool = False
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task | None = None

    def abbrechen(self) -> None:
        with self.sperre:
            self.abgebrochen = True
            if self.loop is not None and self.task is not None:
                self.loop.call_soon_threadsafe(self.task.cancel)


_auftrag: ContextVar[_ThreadAuftrag | None] = ContextVar("loop_db_auftrag", default=None)


def thread_ablauf[T](koroutine: Coroutine[Any, Any, T]) -> T:
    """Verzahnten Ablauf im DB-Thread ausfuehren, Abbruch bis zum Session-Ende begleiten."""
    async def ausfuehren() -> T:
        auftrag = _auftrag.get()
        if auftrag is not None:
            with auftrag.sperre:
                auftrag.loop = asyncio.get_running_loop()
                auftrag.task = asyncio.current_task()
                if auftrag.abgebrochen:
                    koroutine.close()
                    raise asyncio.CancelledError
        try:
            return await koroutine
        finally:
            if auftrag is not None:
                with auftrag.sperre:
                    auftrag.loop = None
                    auftrag.task = None

    return asyncio.run(ausfuehren())


# Eigener Pool: Hintergrund-Ablaeufe halten ihren Thread ueber HTTP-Wartezeiten
# (LIS, Routing, SMS). Im asyncio-Standardpool (min(32, CPUs + 4) = 6 Threads auf
# vm1) wuerden sie die Session-Pruefung jeder Anfrage (#898) aushungern.
_HINTERGRUND_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hintergrund-db")


async def _im_pool[T](funktion: Callable[..., T], *args: Any) -> T:
    kontext = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _HINTERGRUND_POOL, functools.partial(kontext.run, funktion, *args))


async def im_db_thread[T](funktion: Callable[..., T], *args: Any) -> T:
    token = _io_loop.set(asyncio.get_running_loop())
    auftrag = _ThreadAuftrag()
    auftrag_token = _auftrag.set(auftrag)
    arbeit = asyncio.create_task(_im_pool(funktion, *args))
    try:
        return await asyncio.shield(arbeit)
    except asyncio.CancelledError:
        # to_thread allein laesst den Thread weiterlaufen. Erst dessen Coroutine
        # abbrechen und auf Session-Cleanup warten, dann den Hauptloop freigeben.
        auftrag.abbrechen()
        await asyncio.gather(arbeit, return_exceptions=True)
        raise
    finally:
        _auftrag.reset(auftrag_token)
        _io_loop.reset(token)


# Nur auf dem urspruenglichen Loop lesen/schreiben. Der DB-Thread kennt lediglich
# den Schluessel und erhaelt einfache Attributwerte bzw. HTTP-Nutzdaten.
_clients: dict[str, Any] = {}


@contextmanager
def loop_client_schluessel() -> Iterator[str]:
    schluessel = uuid4().hex
    try:
        yield schluessel
    finally:
        _clients.pop(schluessel, None)


def registriere_loop_client(schluessel: str, client: Any) -> None:
    _clients[schluessel] = client


def _auf_event_loop_sync[T](funktion: Callable[..., T], *args: Any) -> T:
    loop = _io_loop.get()
    if loop is None:
        return funktion(*args)
    try:
        aktueller_loop = asyncio.get_running_loop()
    except RuntimeError:
        aktueller_loop = None
    if loop is aktueller_loop:
        return funktion(*args)
    ergebnis: Future[T] = Future()

    def aufrufen() -> None:
        try:
            ergebnis.set_result(funktion(*args))
        except BaseException as exc:
            ergebnis.set_exception(exc)

    loop.call_soon_threadsafe(aufrufen)
    return ergebnis.result()


def _lies_client_attribut(schluessel: str, name: str) -> tuple[bool, Any]:
    wert = getattr(_clients[schluessel], name)
    ist_async = inspect.iscoroutinefunction(wert)
    return ist_async, None if ist_async else wert


def _setze_client_attribut(schluessel: str, name: str, wert: Any) -> None:
    setattr(_clients[schluessel], name, wert)


async def _rufe_client_auf(schluessel: str, name: str, args: tuple, kwargs: dict) -> Any:
    return await getattr(_clients[schluessel], name)(*args, **kwargs)


class LoopClientBruecke:
    """Client und dessen Zustand ausschliesslich auf dem urspruenglichen Loop benutzen."""

    def __init__(self, schluessel: str):
        object.__setattr__(self, "_schluessel", schluessel)

    def __getattr__(self, name: str) -> Any:
        ist_async, wert = _auf_event_loop_sync(_lies_client_attribut, self._schluessel, name)
        if ist_async:
            async def aufrufen(*args: Any, **kwargs: Any) -> Any:
                return await auf_event_loop(_rufe_client_auf, self._schluessel, name, args, kwargs)
            return aufrufen
        return wert

    def __setattr__(self, name: str, wert: Any) -> None:
        _auf_event_loop_sync(_setze_client_attribut, self._schluessel, name, wert)
