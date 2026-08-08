"""Aufbewahrung des SMS-Versandprotokolls."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept
from app.models.org_sms import OrgSmsConfig
from app.models.sms import SmsLog, SmsLogRecipient
from app.services import sms_log_retention


@pytest.fixture
def retention_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(sms_log_retention, "SessionLocal", session_factory)
    yield session_factory
    # FK-Erzwingung fuer den Teardown deaktivieren: die volle App-Metadata enthaelt
    # zirkulaere FKs (siehe conftest.py-Warnung), die drop_all() bei aktivem
    # PRAGMA foreign_keys sonst hart abbrechen laesst.
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
    Base.metadata.drop_all(engine)


def _org(session, slug: str, days: int, max_count: int) -> FireDept:
    org = FireDept(slug=slug, name=slug, color="#123456", bos="Feuerwehr")
    session.add(org)
    session.flush()
    session.add(OrgSmsConfig(
        org_id=org.id, log_retention_days=days, log_retention_max_count=max_count
    ))
    return org


def _log(session, org_id: int, sent_at: datetime) -> SmsLog:
    log = SmsLog(
        org_id=org_id, sent_at=sent_at, source="manual", text="Test",
        recipient_count=1, success_count=1,
    )
    session.add(log)
    session.flush()
    return log


def _session(factory):
    db = factory()
    set_tenant_context(db, None)
    return db


def test_age_based_purge(retention_db):
    now = datetime(2026, 8, 8, 12, 0)
    db = _session(retention_db)
    org = _org(db, "sms-age", 30, 0)
    old = _log(db, org.id, now - timedelta(days=31))
    fresh = _log(db, org.id, now - timedelta(days=29))
    db.commit()
    old_id, fresh_id, org_id = old.id, fresh.id, org.id
    db.close()

    assert sms_log_retention.purge_sms_logs_for_org(org_id, now=now) == 1
    db = _session(retention_db)
    assert db.get(SmsLog, old_id) is None
    assert db.get(SmsLog, fresh_id) is not None
    db.close()


def test_count_based_purge_keeps_newest(retention_db):
    now = datetime(2026, 8, 8, 12, 0)
    db = _session(retention_db)
    org = _org(db, "sms-count", 0, 2)
    logs = [_log(db, org.id, now - timedelta(minutes=i)) for i in range(4)]
    db.commit()
    kept_ids = {logs[0].id, logs[1].id}
    org_id = org.id
    db.close()

    assert sms_log_retention.purge_sms_logs_for_org(org_id, now=now) == 2
    db = _session(retention_db)
    assert {row.id for row in db.query(SmsLog).filter(SmsLog.org_id == org_id)} == kept_ids
    db.close()


def test_both_disabled_is_noop(retention_db):
    db = _session(retention_db)
    org = _org(db, "sms-disabled", 0, 0)
    log = _log(db, org.id, datetime(2000, 1, 1))
    db.commit()
    org_id, log_id = org.id, log.id
    db.close()

    assert sms_log_retention.purge_sms_logs_for_org(org_id) == 0
    db = _session(retention_db)
    assert db.get(SmsLog, log_id) is not None
    db.close()


def test_org_isolation(retention_db):
    now = datetime(2026, 8, 8, 12, 0)
    db = _session(retention_db)
    org_a = _org(db, "sms-org-a", 1, 0)
    org_b = _org(db, "sms-org-b", 1, 0)
    log_a = _log(db, org_a.id, now - timedelta(days=2))
    log_b = _log(db, org_b.id, now - timedelta(days=2))
    db.commit()
    ids = org_a.id, log_a.id, log_b.id
    db.close()

    assert sms_log_retention.purge_sms_logs_for_org(ids[0], now=now) == 1
    db = _session(retention_db)
    assert db.get(SmsLog, ids[1]) is None
    assert db.get(SmsLog, ids[2]) is not None
    db.close()


def test_recipient_rows_cascade_with_log(retention_db):
    now = datetime(2026, 8, 8, 12, 0)
    db = _session(retention_db)
    org = _org(db, "sms-cascade", 1, 0)
    log = _log(db, org.id, now - timedelta(days=2))
    recipient = SmsLogRecipient(
        sms_log_id=log.id, member_id=None, phone_number="+436601234567",
        name=None, success=True, sent_at=now - timedelta(days=2),
    )
    db.add(recipient)
    db.commit()
    org_id, recipient_id = org.id, recipient.id
    db.close()

    assert sms_log_retention.purge_sms_logs_for_org(org_id, now=now) == 1
    db = _session(retention_db)
    assert db.get(SmsLogRecipient, recipient_id) is None
    db.close()
