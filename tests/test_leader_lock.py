from app.services.leader_lock import LeaderLock


def test_leader_lock_is_exclusive_and_releasable(tmp_path):
    path = str(tmp_path / "leader.lock")
    first = LeaderLock(path)
    second = LeaderLock(path)
    try:
        assert first.try_acquire() is True
        assert second.try_acquire() is False
        first.release()
        assert second.try_acquire() is True
    finally:
        first.release()
        second.release()


def _lifespan_calls(monkeypatch, tmp_path, *, hold_lock: bool) -> tuple[int, bool]:
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.config import settings

    path = str(tmp_path / "lifespan.lock")
    monkeypatch.setattr(settings, "LEADER_LOCK_PATH", path)
    calls: list[int] = []
    monkeypatch.setattr(main_module, "_start_background_loops", lambda: calls.append(1) or [])
    holder = LeaderLock(path)
    if hold_lock:
        assert holder.try_acquire() is True
    try:
        with TestClient(main_module.app):
            pass
    finally:
        holder.release()
    after = LeaderLock(path)
    try:
        released = after.try_acquire()
    finally:
        after.release()
    return len(calls), released


def test_lifespan_starts_loops_only_with_leader_lock(setup_db, monkeypatch, tmp_path):
    assert _lifespan_calls(monkeypatch, tmp_path, hold_lock=False) == (1, True)


def test_lifespan_skips_loops_when_other_worker_holds_lock(setup_db, monkeypatch, tmp_path):
    assert _lifespan_calls(monkeypatch, tmp_path, hold_lock=True) == (0, True)
