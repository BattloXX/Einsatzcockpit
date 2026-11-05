"""Begrenzte Sitzungsbereinigung im Watchdog ohne unnoetige Commits."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.breathing import BreathingGeraetSitzung
from app.models.incident import Incident
from app.services import breathing_service as svc


def test_cleanup_batches_cadence_and_no_empty_commit(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    monkeypatch.setattr(svc, "_now", lambda: now)
    monkeypatch.setattr(svc, "_watchdog_last_sitzung_cleanup", None)
    monkeypatch.setattr(svc, "breathing_effective_enabled", lambda org_id, db: False)
    with Session(engine, autoflush=False) as db:
        set_tenant_context(db, None)
        db.add_all([Incident(id=1, status="active"), Incident(id=2, status="closed")])
        db.flush()

        def session(**values):
            data = dict(incident_id=1, user_id=1, platform="android", session_key=uuid4().hex,
                        gestartet_at=now - timedelta(hours=25), letzter_heartbeat_at=now)
            data.update(values)
            row = BreathingGeraetSitzung(**data)
            db.add(row)
            return row

        expired = [session() for _ in range(201)]
        old = [session(beendet_at=now - timedelta(days=31), ende_grund="beendet") for _ in range(201)]
        inactive = session(incident_id=2, gestartet_at=now)
        fresh = session(gestartet_at=now)
        recent = session(beendet_at=now - timedelta(days=29), ende_grund="beendet")
        db.commit()
        commits = []
        event.listen(db, "after_commit", lambda db: commits.append(True))
        svc._collect_new_breathing_warnings(db)
        assert len(commits) == 1
        assert sum(row.beendet_at == now and row.ende_grund == "abgelaufen" for row in expired) == 200
        assert expired[-1].beendet_at is None
        assert db.query(BreathingGeraetSitzung).filter(BreathingGeraetSitzung.id.in_([r.id for r in old])).count() == 1
        now += timedelta(minutes=9, seconds=59)
        svc._collect_new_breathing_warnings(db)
        assert len(commits) == 1
        assert expired[-1].beendet_at is None
        now += timedelta(seconds=1)
        svc._collect_new_breathing_warnings(db)
        assert len(commits) == 2
        assert expired[-1].beendet_at == now and inactive.ende_grund == "abgelaufen"
        assert fresh.beendet_at is None and recent.beendet_at is not None
        assert db.query(BreathingGeraetSitzung).count() == 204
        now += timedelta(minutes=10)
        svc._collect_new_breathing_warnings(db)
        assert len(commits) == 2
    engine.dispose()
