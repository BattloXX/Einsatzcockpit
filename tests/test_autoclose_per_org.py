"""PR 8 – Per-Org Auto-Close-Konfiguration.

Tests für:
- _global_cfg: liest SystemSettings mit Hardcoded-Fallback
- _org_cfg: NULL fällt auf globale Defaults zurück
- _check_incidents_sync: per-Org-Logik (enabled/disabled, after_hours, grace_minutes)
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.services.autoclose import _check_incidents_sync, _global_cfg, _org_cfg

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    set_tenant_context(session, None)  # System-Modus für Autoclose-Tests
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def org(db):
    o = FireDept(slug="ac-test", name="AC Test Org", color="#ff0000", bos="Feuerwehr")
    db.add(o)
    db.flush()
    return o


# ── _global_cfg ──────────────────────────────────────────────────────────────

def test_global_cfg_hardcoded_defaults(db):
    cfg = _global_cfg(db)
    assert cfg["enabled"] is True
    assert cfg["after_hours"] == 48
    assert cfg["grace_minutes"] == 60


def test_global_cfg_reads_system_settings(db):
    db.add(SystemSettings(key="incident_autoclose_enabled", value="false"))
    db.add(SystemSettings(key="incident_autoclose_after_hours", value="24"))
    db.add(SystemSettings(key="incident_autoclose_grace_minutes", value="30"))
    db.flush()
    cfg = _global_cfg(db)
    assert cfg["enabled"] is False
    assert cfg["after_hours"] == 24
    assert cfg["grace_minutes"] == 30


# ── _org_cfg ─────────────────────────────────────────────────────────────────

def test_org_cfg_none_returns_global(db):
    global_cfg = {"enabled": True, "after_hours": 48, "grace_minutes": 60}
    assert _org_cfg(None, global_cfg) == global_cfg


def test_org_cfg_null_fields_fall_back(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_after_hours=12)
    db.add(org_s)
    db.flush()
    global_cfg = {"enabled": True, "after_hours": 48, "grace_minutes": 60}
    cfg = _org_cfg(org_s, global_cfg)
    assert cfg["enabled"] is True       # inherited from global
    assert cfg["after_hours"] == 12     # org override
    assert cfg["grace_minutes"] == 60   # inherited from global


def test_org_cfg_disabled_override(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_enabled=False)
    db.add(org_s)
    db.flush()
    global_cfg = {"enabled": True, "after_hours": 48, "grace_minutes": 60}
    cfg = _org_cfg(org_s, global_cfg)
    assert cfg["enabled"] is False


def test_org_cfg_all_overrides(db, org):
    org_s = OrgSettings(org_id=org.id,
                        autoclose_enabled=True,
                        autoclose_after_hours=6,
                        autoclose_grace_minutes=15)
    db.add(org_s)
    db.flush()
    global_cfg = {"enabled": False, "after_hours": 48, "grace_minutes": 60}
    cfg = _org_cfg(org_s, global_cfg)
    assert cfg == {"enabled": True, "after_hours": 6, "grace_minutes": 15}


# ── _check_incidents_sync ─────────────────────────────────────────────────────

def test_check_skips_disabled_org(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_enabled=False)
    db.add(org_s)
    db.flush()

    inc = Incident(
        primary_org_id=org.id,
        alarm_type_code="T1",
        status="active",
        started_at=(datetime.now(UTC) - timedelta(hours=49)).replace(tzinfo=None),
    )
    db.add(inc)
    db.flush()

    to_warn = _check_incidents_sync(db)
    assert len(to_warn) == 0


def test_check_warns_after_default_threshold(db, org):
    # No OrgSettings → global defaults (48 h, 60 min)
    inc = Incident(
        primary_org_id=org.id,
        alarm_type_code="T1",
        status="active",
        started_at=(datetime.now(UTC) - timedelta(hours=49)).replace(tzinfo=None),
    )
    db.add(inc)
    db.flush()

    to_warn = _check_incidents_sync(db)
    assert len(to_warn) == 1
    assert to_warn[0][0] == inc.id
    assert to_warn[0][1] == 60  # default grace_minutes


def test_check_uses_org_after_hours_override(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_after_hours=10)
    db.add(org_s)
    db.flush()

    # 9 h old → should NOT warn (threshold is 10 h)
    inc = Incident(
        primary_org_id=org.id,
        alarm_type_code="T1",
        status="active",
        started_at=(datetime.now(UTC) - timedelta(hours=9)).replace(tzinfo=None),
    )
    db.add(inc)
    db.flush()

    to_warn = _check_incidents_sync(db)
    assert len(to_warn) == 0


def test_check_uses_org_grace_minutes_in_warning(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_grace_minutes=120)
    db.add(org_s)
    db.flush()

    inc = Incident(
        primary_org_id=org.id,
        alarm_type_code="T1",
        status="active",
        started_at=(datetime.now(UTC) - timedelta(hours=49)).replace(tzinfo=None),
    )
    db.add(inc)
    db.flush()

    to_warn = _check_incidents_sync(db)
    assert len(to_warn) == 1
    assert to_warn[0][1] == 120  # org-specific grace_minutes in warning payload


def test_check_closes_after_grace_period(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_grace_minutes=30)
    db.add(org_s)
    db.flush()

    warn_ts = (datetime.now(UTC) - timedelta(minutes=35)).replace(tzinfo=None)
    inc = Incident(
        primary_org_id=org.id,
        alarm_type_code="T1",
        status="active",
        started_at=(datetime.now(UTC) - timedelta(hours=49)).replace(tzinfo=None),
        autoclose_warn_sent_at=warn_ts,
    )
    db.add(inc)
    db.flush()

    with patch("app.services.autoclose.close_incident") as mock_close:
        def _fake_close(db, inc, user_id=None):
            inc.status = "closed"
        mock_close.side_effect = _fake_close
        _check_incidents_sync(db)
        assert mock_close.called

    db.refresh(inc)
    assert inc.status == "closed"


def test_check_no_close_within_grace_period(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_grace_minutes=60)
    db.add(org_s)
    db.flush()

    # warn sent only 10 minutes ago – still within 60-min grace
    warn_ts = (datetime.now(UTC) - timedelta(minutes=10)).replace(tzinfo=None)
    inc = Incident(
        primary_org_id=org.id,
        alarm_type_code="T1",
        status="active",
        started_at=(datetime.now(UTC) - timedelta(hours=49)).replace(tzinfo=None),
        autoclose_warn_sent_at=warn_ts,
    )
    db.add(inc)
    db.flush()

    with patch("app.services.autoclose.close_incident") as mock_close:
        _check_incidents_sync(db)
        mock_close.assert_not_called()

    db.refresh(inc)
    assert inc.status == "active"


def test_org_settings_cached_across_incidents(db, org):
    org_s = OrgSettings(org_id=org.id, autoclose_enabled=False)
    db.add(org_s)
    db.flush()

    for i in range(3):
        db.add(Incident(
            primary_org_id=org.id,
            alarm_type_code="T1",
            status="active",
            started_at=(datetime.now(UTC) - timedelta(hours=49)).replace(tzinfo=None),
        ))
    db.flush()

    # All three incidents are in the disabled org → no warnings
    to_warn = _check_incidents_sync(db)
    assert len(to_warn) == 0
