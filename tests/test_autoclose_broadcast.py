import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.services import autoclose


def test_auto_closed_incident_is_broadcast(setup_db, monkeypatch):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"autoclose-broadcast-{uuid4().hex}",
            name="Autoclose Broadcast",
            color="#ff0000",
            bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        db.add(OrgSettings(
            org_id=org.id,
            autoclose_enabled=True,
            autoclose_grace_minutes=30,
        ))
        incident = Incident(
            primary_org_id=org.id,
            alarm_type_code="T1",
            status="active",
            started_at=(datetime.now(UTC) - timedelta(hours=49)).replace(tzinfo=None),
            autoclose_warn_sent_at=(
                datetime.now(UTC) - timedelta(minutes=35)
            ).replace(tzinfo=None),
        )
        db.add(incident)
        db.commit()

        to_warn, closed = autoclose._check_incidents_sync(db)
        calls = []

        async def fake_broadcast(incident_id, payload):
            calls.append((incident_id, payload))

        monkeypatch.setattr(autoclose.manager, "broadcast", fake_broadcast)
        asyncio.run(autoclose._broadcast_events(to_warn, closed))

        assert (incident.id, org.id) in closed
        assert (incident.id, {"type": "incident_closed"}) in calls
    finally:
        db.close()
