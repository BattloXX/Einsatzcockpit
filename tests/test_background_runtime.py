import asyncio
import logging
import os
import subprocess
import sys
import threading

import pytest

from app.services import background_runtime as runtime
from app.services.leader_lock import LeaderLock
from app.services.loop_utils import iteration_watch


def test_lock_errors_fail_closed_and_recover(tmp_path, monkeypatch, caplog):
    calls = []
    worker = runtime.BackgroundRuntime(str(tmp_path / "leader"), lambda: calls.append(1) or [])
    acquire = worker.lock.try_acquire
    with monkeypatch.context() as patch:
        def denied():
            raise PermissionError("SECRET_TOKEN_MUST_NOT_APPEAR")
        patch.setattr(worker.lock, "try_acquire", denied)
        worker.try_leadership()
        worker.try_leadership()
        assert not worker.is_leader and not calls
        assert worker.leader_error == "PermissionError"
        assert "SECRET_TOKEN" not in caplog.text
    assert worker.lock.try_acquire == acquire
    worker.try_leadership()
    assert worker.is_leader and calls == [1]
    worker.release()


def test_lock_released_after_sigkill_and_not_inherited_by_fork(tmp_path):
    path = str(tmp_path / "leader")
    parent = LeaderLock(path)
    assert parent.try_acquire()
    pid = os.fork()
    if pid == 0:
        # Ein Kind darf das Lock des Elternprozesses nicht als eigenes ansehen.
        acquired = parent.try_acquire()
        os._exit(1 if acquired else 0)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    parent.release()
    code = ("import sys,time; from app.services.leader_lock import LeaderLock; "
            "lock=LeaderLock(sys.argv[1]); assert lock.try_acquire(); "
            "print('ready',flush=True); time.sleep(30)")
    process = subprocess.Popen([sys.executable, "-u", "-c", code, path], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        follower = LeaderLock(path)
        assert not follower.try_acquire()
        process.kill()
        process.wait(timeout=5)
        assert follower.try_acquire()
        follower.release()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


async def test_follower_takes_over_once_and_keeps_loop_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "TAKEOVER_S", .01)
    calls = []
    success = asyncio.Event()
    path = str(tmp_path / "leader")
    def loops():
        calls.append(1)
        async def tick():
            while True:
                with iteration_watch(logging.getLogger("test"), "proof", .01):
                    await asyncio.sleep(.001)
                if len(calls) == 2:
                    success.set()
                await asyncio.sleep(.01)
        return [asyncio.create_task(tick())]
    first = runtime.BackgroundRuntime(path, loops, worker_id="test-first")
    second = runtime.BackgroundRuntime(path, loops, worker_id="test-second")
    try:
        await first.start()
        await second.start()
        assert first.is_leader and not second.is_leader and calls == [1]
        await first.stop_tasks()
        first.release()
        await asyncio.wait_for(success.wait(), timeout=2)
        assert calls == [1, 1]
        second.write_heartbeat()
        from app.models.worker_runtime import WorkerRuntime
        from app.services.sms_outbox import database
        with database() as db:
            row = db.get(WorkerRuntime, "test-second")
            assert row.is_leader and row.loops["proof"]["last_success_at"]
    finally:
        await first.stop_tasks()
        await second.stop_tasks()
        first.release()
        second.release()
        from app.models.worker_runtime import WorkerRuntime
        from app.services.sms_outbox import database
        with database() as db:
            db.query(WorkerRuntime).filter(WorkerRuntime.worker_id.in_(("test-first", "test-second"))).delete()
            db.commit()


def test_shutdown_holds_leader_until_thread_work_finishes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main
    from app.config import settings

    entered, close, working, canceled, finish = [threading.Event() for _ in range(5)]
    failures = []
    path = str(tmp_path / "leader")
    monkeypatch.setattr(settings, "TESTING", False)
    monkeypatch.setattr(settings, "LEADER_LOCK_PATH", path)
    def work():
        working.set()
        assert finish.wait(10)
    async def loop():
        try:
            await asyncio.to_thread(work)
        finally:
            canceled.set()
    monkeypatch.setattr(main, "_start_background_loops", lambda: [asyncio.create_task(loop())])
    def serve():
        try:
            with TestClient(main.app):
                entered.set()
                assert close.wait(10)
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=serve)
    follower = LeaderLock(path)
    thread.start()
    try:
        assert entered.wait(5) and working.wait(5)
        close.set()
        assert canceled.wait(5)
        assert not follower.try_acquire(), "to_thread arbeitet trotz Task-Cancel noch"
        finish.set()
        thread.join(timeout=10)
        assert not thread.is_alive() and not failures
        assert follower.try_acquire()
    finally:
        close.set()
        finish.set()
        thread.join(timeout=10)
        follower.release()


def test_iteration_failure_does_not_advance_success():
    data = {}
    token = runtime._loops.set(data)
    try:
        with iteration_watch(logging.getLogger("test"), "proof", 1):
            pass
        success = data["proof"]["last_success_at"]
        with pytest.raises(RuntimeError), iteration_watch(logging.getLogger("test"), "proof", 1):
            raise RuntimeError("no payload")
        assert data["proof"]["state"] == "failed"
        assert data["proof"]["last_success_at"] == success
    finally:
        runtime._loops.reset(token)


async def test_detached_leader_work_is_drained(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()
    async def child():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    async def parent():
        runtime.create_leader_task(child())
    worker = runtime.BackgroundRuntime(str(tmp_path / "leader"), lambda: [asyncio.create_task(parent())])
    worker.try_leadership()
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert worker.children
        await worker.stop_tasks()
        assert stopped.is_set() and not worker.children
    finally:
        await worker.stop_tasks()
        worker.release()


async def test_failed_root_loop_restarts_under_same_lock(monkeypatch, caplog):
    monkeypatch.setattr(runtime, "LOOP_RESTART_S", .01)
    resumed = asyncio.Event()
    calls = []
    async def loop():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("SECRET_JOB_PAYLOAD")
        resumed.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(runtime.supervised_loop(loop))
    try:
        await asyncio.wait_for(resumed.wait(), timeout=2)
        assert calls == [1, 1]
        assert "SECRET_JOB_PAYLOAD" not in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
