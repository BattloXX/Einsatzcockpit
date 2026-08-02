from datetime import UTC, datetime, timedelta

from app.models.master import AIRequestLog
from app.services import ai_log_retention
from tests.conftest import TestingSession


def test_purge_old_ai_requests_keeps_newer(monkeypatch):
    monkeypatch.setattr(ai_log_retention, "SessionLocal", TestingSession)
    db = TestingSession()
    db.query(AIRequestLog).delete()
    now = datetime.now(UTC)
    common = dict(feature="test", model="model", success=True)
    db.add(AIRequestLog(**common, created_at=now - timedelta(days=61)))
    db.add(AIRequestLog(**common, created_at=now - timedelta(days=59)))
    db.commit()
    db.close()

    assert ai_log_retention.purge_old_ai_requests(retention_days=60, chunk_size=1) == 1

    db = TestingSession()
    rows = db.query(AIRequestLog).all()
    assert len(rows) == 1
    db.close()
