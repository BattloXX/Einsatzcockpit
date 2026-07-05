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


# ---------------------------------------------------------------------------
# Quota-Verwaltung
# ---------------------------------------------------------------------------

_GIB = 1024 ** 3


def test_pct_unlimited_is_none():
    from app.routers.ui_sysadmin import _pct
    assert _pct(1234, None) is None


def test_pct_zero_quota():
    from app.routers.ui_sysadmin import _pct
    assert _pct(0, 0) == 0
    assert _pct(5, 0) == 100


def test_pct_normal_rounding():
    from app.routers.ui_sysadmin import _pct
    assert _pct(50, 100) == 50
    assert _pct(1, 3) == 33  # 33.33 → 33
    assert _pct(150, 100) == 150  # Überschreitung wird nicht gekappt


def test_quota_rows_maps_storage_and_ai():
    from datetime import UTC, datetime

    from app.models.master import FireDept, OrgSettings, OrgStorageUsage
    from app.routers.ui_sysadmin import _quota_rows

    cur_month = datetime.now(UTC).strftime("%Y-%m")
    org = _org(3, "FF Test")
    org.storage_quota_bytes = 10 * _GIB

    usage = SimpleNamespace(org_id=3, used_bytes=5 * _GIB)
    os_row = SimpleNamespace(
        org_id=3,
        ai_monthly_token_quota=1000,
        ai_tokens_used_month=250,
        ai_tokens_month_key=cur_month,
    )

    db = MagicMock()

    def _query(model, *a, **k):
        m = MagicMock()
        if model is FireDept:
            m.filter.return_value = m
            m.order_by.return_value = m
            m.all.return_value = [org]
        elif model is OrgStorageUsage:
            m.all.return_value = [usage]
        elif model is OrgSettings:
            m.all.return_value = [os_row]
        else:
            m.all.return_value = []
        return m

    db.query.side_effect = _query

    rows = _quota_rows(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["storage_used"] == 5 * _GIB
    assert r["storage_quota"] == 10 * _GIB
    assert r["storage_pct"] == 50
    assert r["storage_quota_gb"] == 10.0
    assert r["ai_quota"] == 1000
    assert r["ai_used"] == 250
    assert r["ai_pct"] == 25


def test_quota_rows_ai_used_zero_for_stale_month():
    """Verbrauch aus einem alten Monat wird als 0 gewertet."""
    from app.models.master import FireDept, OrgSettings, OrgStorageUsage
    from app.routers.ui_sysadmin import _quota_rows

    org = _org(4, "FF Alt")
    org.storage_quota_bytes = None
    os_row = SimpleNamespace(
        org_id=4,
        ai_monthly_token_quota=500,
        ai_tokens_used_month=999,
        ai_tokens_month_key="2020-01",  # veraltet
    )

    db = MagicMock()

    def _query(model, *a, **k):
        m = MagicMock()
        if model is FireDept:
            m.filter.return_value = m
            m.order_by.return_value = m
            m.all.return_value = [org]
        elif model is OrgStorageUsage:
            m.all.return_value = []
        elif model is OrgSettings:
            m.all.return_value = [os_row]
        else:
            m.all.return_value = []
        return m

    db.query.side_effect = _query

    rows = _quota_rows(db)
    assert rows[0]["ai_used"] == 0
    assert rows[0]["storage_used"] == 0
    assert rows[0]["storage_pct"] is None  # keine Quota gesetzt


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _save_db(org, os_row):
    db = MagicMock()
    db.get.return_value = org
    q = MagicMock()
    q.filter_by.return_value.first.return_value = os_row
    db.query.return_value = q
    return db


def test_quota_save_converts_gb_and_tokens():
    from app.routers.ui_sysadmin import sysadmin_quota_save

    org = _org(1)
    org.storage_quota_bytes = None
    os_row = SimpleNamespace(org_id=1, ai_monthly_token_quota=None)
    db = _save_db(org, os_row)
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))

    resp = _run(sysadmin_quota_save(
        1, request, storage_quota_gb="5", ai_monthly_token_quota="1.000.000", db=db,
    ))

    assert org.storage_quota_bytes == 5 * _GIB
    assert os_row.ai_monthly_token_quota == 1_000_000
    assert resp.status_code == 303
    db.commit.assert_called_once()


def test_quota_save_empty_means_unlimited():
    from app.routers.ui_sysadmin import sysadmin_quota_save

    org = _org(1)
    org.storage_quota_bytes = 999
    os_row = SimpleNamespace(org_id=1, ai_monthly_token_quota=999)
    db = _save_db(org, os_row)
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))

    _run(sysadmin_quota_save(
        1, request, storage_quota_gb="  ", ai_monthly_token_quota="", db=db,
    ))

    assert org.storage_quota_bytes is None
    assert os_row.ai_monthly_token_quota is None


def test_quota_save_comma_decimal():
    from app.routers.ui_sysadmin import sysadmin_quota_save

    org = _org(1)
    os_row = SimpleNamespace(org_id=1, ai_monthly_token_quota=None)
    db = _save_db(org, os_row)
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))

    _run(sysadmin_quota_save(
        1, request, storage_quota_gb="1,5", ai_monthly_token_quota="", db=db,
    ))

    assert org.storage_quota_bytes == int(round(1.5 * _GIB))


def test_quota_save_invalid_storage_raises():
    from fastapi import HTTPException

    from app.routers.ui_sysadmin import sysadmin_quota_save

    org = _org(1)
    os_row = SimpleNamespace(org_id=1, ai_monthly_token_quota=None)
    db = _save_db(org, os_row)
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)))

    with pytest.raises(HTTPException) as exc:
        _run(sysadmin_quota_save(
            1, request, storage_quota_gb="abc", ai_monthly_token_quota="", db=db,
        ))
    assert exc.value.status_code == 400
