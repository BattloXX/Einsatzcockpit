"""Ein Leader pro Host; DB-Heartbeats sind Diagnose, keine unsichere Leader-Lease."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.worker_runtime import WorkerRuntime
from app.services import gateway_presence, ws_bus
from app.services.leader_lock import LeaderLock

logger = logging.getLogger("einsatzleiter.background")
HEARTBEAT_S = 5
WORKER_TTL_S = 20
TAKEOVER_S = 2
LOOP_RESTART_S = 5
_loops: ContextVar[dict | None] = ContextVar("leader_loop_metrics", default=None)
_children: ContextVar[set[asyncio.Task] | None] = ContextVar("leader_children", default=None)


def create_leader_task(coro, *, name: str | None = None) -> asyncio.Task:
    """Entkoppelte Folgearbeit bleibt Teil des Leader-Shutdowns; HTTP bleibt unveraendert."""
    task = asyncio.create_task(coro, name=name)
    tasks = _children.get()
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return task


def record_iteration(name: str, state: str, *, interval_s: float | None = None) -> None:
    metrics = _loops.get()
    if metrics is None:
        return
    now = gateway_presence.utc().isoformat()
    row = metrics.setdefault(name, {})
    row["state"] = state
    if interval_s is not None:
        row["interval_s"] = interval_s
    if state == "running":
        row["last_started_at"] = now
    elif state == "ok":
        row["last_success_at"] = now
    elif state == "failed":
        row["last_failure_at"] = now


async def supervised_loop(factory: Callable) -> None:
    """Unerwartete Taskfehler unter demselben Lock neu starten; deaktivierte Loops ruhen."""
    name = factory.__name__
    while True:
        try:
            record_iteration(name, "active")
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record_iteration(name, "failed")
            logger.error("Hintergrund-Loop fehlgeschlagen (loop=%s, error=%s)", name, type(exc).__name__)
            await asyncio.sleep(LOOP_RESTART_S)
        else:
            record_iteration(name, "stopped")
            return


class BackgroundRuntime:
    def __init__(self, path: str, start_loops: Callable, *, worker_id: str | None = None):
        self.worker_id = worker_id or gateway_presence.worker_id()
        self.lock = LeaderLock(path)
        self.start_loops = start_loops
        self.started_at = gateway_presence.utc()
        self.leader_since: datetime | None = None
        self.leader_error: str | None = None
        self.loops: dict = {}
        self.tasks: list[asyncio.Task] = []
        self.children: set[asyncio.Task] = set()
        self._watcher: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._last_log_error: str | None = None
        self._next_cleanup = self.started_at

    @property
    def is_leader(self) -> bool:
        return self.leader_since is not None

    def try_leadership(self) -> None:
        if self.is_leader:
            return
        try:
            acquired = self.lock.try_acquire()
            self.leader_error = None
            self._last_log_error = None
            if not acquired:
                return
        except OSError as exc:
            self.leader_error = type(exc).__name__
            if self._last_log_error != self.leader_error:
                logger.error("Leader-Lock nicht verfuegbar; Loops bleiben aus (error=%s)", self.leader_error)
                self._last_log_error = self.leader_error
            return
        self.leader_error = None
        self.leader_since = gateway_presence.utc()
        token = _loops.set(self.loops)
        child_token = _children.set(self.children)
        try:
            self.tasks = self.start_loops()
        except Exception as exc:
            # Bei unbekanntem Teilstart den Lock behalten, keine zweite Gruppe starten.
            self.leader_error = "loop_start_failed"
            logger.error("Leader-Loops konnten nicht starten (error=%s)", type(exc).__name__)
        finally:
            _loops.reset(token)
            _children.reset(child_token)
        logger.info("Leader uebernommen (worker=%s)", self.worker_id)

    async def start(self) -> None:
        self.try_leadership()
        await self.heartbeat()
        self._watcher = asyncio.create_task(self._watch(), name="leader-election")
        self._heartbeat = asyncio.create_task(self._pulse(), name="worker-heartbeat")

    async def _watch(self):
        while True:
            await asyncio.sleep(TAKEOVER_S)
            was_leader = self.is_leader
            self.try_leadership()
            if self.is_leader and not was_leader:
                await self.heartbeat()

    def write_heartbeat(self, *, stopped: bool = False) -> None:
        now = gateway_presence.utc()
        if not stopped:
            for task in self.tasks:
                if task.cancelled():
                    self.loops.setdefault(task.get_name(), {}).update(
                        state="failed", last_failure_at=now.isoformat())
        with SessionLocal() as db:
            set_tenant_context(db, None)
            row = db.get(WorkerRuntime, self.worker_id)
            if row is None:
                row = WorkerRuntime(worker_id=self.worker_id, hostname=socket.gethostname()[:64],
                                    pid=os.getpid(), started_at=self.started_at)
                db.add(row)
            row.heartbeat_at = now
            row.expires_at = now if stopped else now + timedelta(seconds=WORKER_TTL_S)
            row.is_leader = self.is_leader and not stopped
            row.leader_since = self.leader_since
            row.leader_error = self.leader_error
            row.redis = ws_bus.status()
            row.loops = {name: dict(values) for name, values in self.loops.items()}
            db.commit()

    async def heartbeat(self, *, stopped: bool = False) -> None:
        try:
            await asyncio.to_thread(self.write_heartbeat, stopped=stopped)
        except Exception as exc:
            logger.warning("Worker-Heartbeat nicht gespeichert (error=%s)", type(exc).__name__)

    async def _pulse(self):
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await self.heartbeat()
            now = gateway_presence.utc()
            if self.is_leader and now >= self._next_cleanup:
                self._next_cleanup = now + timedelta(hours=1)
                try:
                    await asyncio.to_thread(self.cleanup)
                except Exception as exc:
                    logger.warning("Alte Worker-Diagnose nicht bereinigt (error=%s)", type(exc).__name__)

    @staticmethod
    def cleanup():
        # Getrennte Transaktion, ausschliesslich beim Leader: konkurrierende
        # DELETE-Range-Locks duerfen keine Worker-Heartbeats zurueckrollen.
        with SessionLocal() as db:
            set_tenant_context(db, None)
            db.query(WorkerRuntime).filter(
                WorkerRuntime.expires_at < gateway_presence.utc() - timedelta(days=1)
            ).delete()
            db.commit()

    async def stop_tasks(self):
        tasks = [*self.tasks, *[t for t in (self._watcher, self._heartbeat) if t is not None]]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        while self.children:
            children = list(self.children)
            for task in children:
                task.cancel()
            await asyncio.gather(*children, return_exceptions=True)

    def release(self):
        # Erst nach Task-Ende UND Drain des Default-Executors durch lifespan aufrufen.
        self.lock.release()
        self.leader_since = None
