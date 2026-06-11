"""PR 11 – System-Admin-Konsole.

Tests für _org_stats():
- leere DB → leere Liste
- eine Org ohne Einsätze → korrekte Nullwerte
- eine Org mit aktiven und abgeschlossenen Einsätzen
- mehrere Orgs → keine Kreuz-Vermischung der Zähler
- gelöschte Org erscheint trotzdem in der Liste
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Hilfsfunktionen für Fake-DB-Objekte
# ---------------------------------------------------------------------------

def _org(id: int, name: str = "Test Org", is_active: bool = True, deleted_at=None):
    return SimpleNamespace(
        id=id,
        name=name,
        slug=f"org-{id}",
        color="#ff0000",
        short_code=None,
        is_home_org=False,
        is_active=is_active,
        deleted_at=deleted_at,
    )


def _query_result(rows: list[tuple]) -> MagicMock:
    """Fake query result that supports .filter().group_by().all()"""
    items = [SimpleNamespace(**dict(zip(("primary_org_id", "cnt"), r) if len(r) == 2 else zip(("primary_org_id", "last"), r))) for r in rows]
    mock = MagicMock()
    mock.filter.return_value = mock
    mock.group_by.return_value = mock
    mock.all.return_value = items
    return mock


def _make_db(orgs, active_inc=(), total_inc=(), last_inc=(), users=(), members=(), api_keys=()):
    """Build a mock SQLAlchemy Session that returns preset data for _org_stats."""
    db = MagicMock()

    # Track how many times query() is called to return correct data in sequence
    call_seq = [
        orgs,         # db.query(FireDept) → order_by → all
        active_inc,   # Incident.primary_org_id + count, active
        total_inc,    # Incident.primary_org_id + count, all
        last_inc,     # Incident.primary_org_id + max(started_at)
        users,        # User.org_id + count
        members,      # Member.org_id + count
        api_keys,     # ApiKey.org_id + count
    ]
    idx = {"i": 0}

    def _query(*args, **kwargs):
        m = MagicMock()
        seq_data = call_seq[idx["i"]] if idx["i"] < len(call_seq) else []
        idx["i"] += 1

        # First call: FireDept list
        if idx["i"] == 1:
            m.order_by.return_value.all.return_value = seq_data
        else:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = seq_data
        return m

    db.query.side_effect = _query
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_org_stats_empty():
    from app.routers.ui_sysadmin import _org_stats

    db = MagicMock()
    m = MagicMock()
    m.order_by.return_value.all.return_value = []
    db.query.return_value = m

    result = _org_stats(db)
    assert result == []


def test_org_stats_one_org_no_incidents():
    from app.routers.ui_sysadmin import _org_stats

    org = _org(1, "FF Wolfurt")
    row = SimpleNamespace(primary_org_id=None, cnt=0)

    db = MagicMock()
    call_num = {"n": 0}

    def _query(*args, **kwargs):
        call_num["n"] += 1
        m = MagicMock()
        if call_num["n"] == 1:
            # FireDept
            m.order_by.return_value.all.return_value = [org]
        else:
            # All aggregates: return empty
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = []
        return m

    db.query.side_effect = _query

    result = _org_stats(db)
    assert len(result) == 1
    assert result[0]["org"] is org
    assert result[0]["active_incidents"] == 0
    assert result[0]["total_incidents"] == 0
    assert result[0]["last_incident_at"] is None
    assert result[0]["users"] == 0
    assert result[0]["members"] == 0
    assert result[0]["api_keys"] == 0


def test_org_stats_counts_mapped_correctly():
    from app.routers.ui_sysadmin import _org_stats

    org = _org(7, "FF Bregenz")
    ts = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    active_row = SimpleNamespace(primary_org_id=7, cnt=3)
    total_row = SimpleNamespace(primary_org_id=7, cnt=10)
    last_row = SimpleNamespace(primary_org_id=7, last=ts)
    user_row = SimpleNamespace(org_id=7, cnt=5)
    member_row = SimpleNamespace(org_id=7, cnt=42)
    apikey_row = SimpleNamespace(org_id=7, cnt=2)

    db = MagicMock()
    call_num = {"n": 0}

    def _query(*args, **kwargs):
        call_num["n"] += 1
        m = MagicMock()
        if call_num["n"] == 1:
            m.order_by.return_value.all.return_value = [org]
        elif call_num["n"] == 2:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = [active_row]
        elif call_num["n"] == 3:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = [total_row]
        elif call_num["n"] == 4:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = [last_row]
        elif call_num["n"] == 5:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = [user_row]
        elif call_num["n"] == 6:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = [member_row]
        else:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = [apikey_row]
        return m

    db.query.side_effect = _query

    result = _org_stats(db)
    assert len(result) == 1
    r = result[0]
    assert r["active_incidents"] == 3
    assert r["total_incidents"] == 10
    assert r["last_incident_at"] == ts
    assert r["users"] == 5
    assert r["members"] == 42
    assert r["api_keys"] == 2


def test_org_stats_no_cross_contamination():
    """Counts for org A must not appear in org B's row."""
    from app.routers.ui_sysadmin import _org_stats

    org_a = _org(1, "Org A")
    org_b = _org(2, "Org B")

    db = MagicMock()
    call_num = {"n": 0}

    def _query(*args, **kwargs):
        call_num["n"] += 1
        m = MagicMock()
        if call_num["n"] == 1:
            m.order_by.return_value.all.return_value = [org_a, org_b]
        elif call_num["n"] == 2:
            # active incidents: only org A has 2
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = [SimpleNamespace(primary_org_id=1, cnt=2)]
        else:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = []
        return m

    db.query.side_effect = _query

    result = _org_stats(db)
    assert len(result) == 2
    rows_by_id = {r["org"].id: r for r in result}
    assert rows_by_id[1]["active_incidents"] == 2
    assert rows_by_id[2]["active_incidents"] == 0


def test_org_stats_deleted_org_included():
    """Soft-deleted orgs must still appear in the table."""
    from app.routers.ui_sysadmin import _org_stats

    org = _org(99, "Gelöschte Org", deleted_at=datetime(2026, 1, 1))

    db = MagicMock()
    call_num = {"n": 0}

    def _query(*args, **kwargs):
        call_num["n"] += 1
        m = MagicMock()
        if call_num["n"] == 1:
            m.order_by.return_value.all.return_value = [org]
        else:
            m.filter.return_value = m
            m.group_by.return_value = m
            m.all.return_value = []
        return m

    db.query.side_effect = _query

    result = _org_stats(db)
    assert len(result) == 1
    assert result[0]["org"].deleted_at is not None
