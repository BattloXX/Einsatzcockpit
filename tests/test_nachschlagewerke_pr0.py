"""Nachschlagewerke PR 0: Feature-Flag-Helfer + Router-Registrierung."""
from unittest.mock import MagicMock

from app.services.nachschlagewerk_service import (
    nachschlagewerke_effective_enabled,
    nachschlagewerke_system_enabled,
)


class _Sys:
    def __init__(self, value=None):
        self.key = "nachschlagewerke_module_enabled"
        self.value = value


class _OrgS:
    def __init__(self, enabled=False):
        self.nachschlagewerke_module_enabled = enabled


def _db_with(sys_value, org_enabled):
    """Mock-DB: erster Query-Pfad SystemSettings, zweiter OrgSettings (execution_options)."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys(sys_value)
    db.query.return_value.filter.return_value.execution_options.return_value.first.return_value = _OrgS(org_enabled)
    return db


# ── System-Flag ───────────────────────────────────────────────────────────────

def test_system_flag_missing_returns_false():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert nachschlagewerke_system_enabled(db) is False


def test_system_flag_false_value():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys("false")
    assert nachschlagewerke_system_enabled(db) is False


def test_system_flag_true_value():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _Sys("true")
    assert nachschlagewerke_system_enabled(db) is True


# ── Effektiv = System AND Org ─────────────────────────────────────────────────

def test_effective_false_when_no_org():
    assert nachschlagewerke_effective_enabled(None, MagicMock()) is False


def test_effective_false_when_system_off():
    assert nachschlagewerke_effective_enabled(1, _db_with("false", True)) is False


def test_effective_false_when_system_on_org_off():
    assert nachschlagewerke_effective_enabled(1, _db_with("true", False)) is False


def test_effective_true_when_both_on():
    assert nachschlagewerke_effective_enabled(1, _db_with("true", True)) is True


# ── Router-Registrierung + Guard ──────────────────────────────────────────────

def test_router_registered():
    from app.main import app

    def _paths(routes):
        out = set()
        for r in routes:
            p = getattr(r, "path", None)
            if p:
                out.add(p)
        return out

    assert "/nachschlagewerke/" in _paths(app.routes)


def test_guard_404_when_disabled():
    from fastapi import HTTPException

    from app.routers.ui_nachschlagewerke import require_nachschlagewerke_enabled

    class _Req:
        class state:  # noqa: N801
            nachschlagewerke_enabled = False

    try:
        require_nachschlagewerke_enabled(_Req())
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Guard haette 404 werfen muessen")


def test_guard_passes_when_enabled():
    from app.routers.ui_nachschlagewerke import require_nachschlagewerke_enabled

    class _Req:
        class state:  # noqa: N801
            nachschlagewerke_enabled = True

    # kein Fehler => ok
    assert require_nachschlagewerke_enabled(_Req()) is None
