"""Manuelles Wiedereröffnen sperrt weitere automatische LIS-/DIBOS-Abschlüsse."""
import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.services.dibos import dibos_enrich
from app.services.incident_service import reopen_incident
from app.services.lis import lis_sync
from tests.conftest import TestingSession

ORG_ID = 1


def _session():
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _auto_closed_incident(db, *, operation_id: str, operation_number: str | None = None) -> Incident:
    incident = Incident(
        primary_org_id=ORG_ID,
        alarm_type_code="T1",
        status="closed",
        started_at=datetime(2026, 1, 5, 10, tzinfo=UTC),
        closed_at=datetime(2026, 1, 5, 11, tzinfo=UTC),
        closed_via_lis_auto=True,
        lis_operation_id=operation_id,
        lis_operation_number=operation_number,
    )
    db.add(incident)
    db.flush()
    return incident


def _dibos_closed_event(number: str) -> dict:
    return {
        "eventNumber": number,
        "lev3": "F_WOLFU",
        "tycod": "t1",
        "tycodDescription": "Test",
        "created": "2026-01-05T10:00:00",
        "dispatched": "2026-01-05T10:01:00",
        "closed": "2026-01-05T11:00:00",
        "callerList": [],
        "targetList": [],
        "comments": [],
        "personResponseList": [],
        "unitList": [],
        "locationStreet": "Teststrasse",
        "locationStreetNo": "1",
        "locationCity": "Wolfurt",
    }


def test_manual_reopen_of_auto_closed_incident_sets_lock():
    db = _session()
    try:
        incident = _auto_closed_incident(db, operation_id="manual-reopen-lock")

        reopen_incident(db, incident, user_id=123)

        assert incident.status == "active"
        assert incident.lis_auto_close_locked is True
        assert incident.closed_via_lis_auto is False
    finally:
        db.rollback()
        db.close()


def test_manual_reopen_prevents_lis_auto_close():
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = _auto_closed_incident(db, operation_id="manual-reopen-lis-lock")
        reopen_incident(db, incident, user_id=123)

        asyncio.run(lis_sync._close_incidents_missing_from_lis(db, org, set()))

        assert incident.status == "active"
        assert incident.lis_auto_close_locked is True
    finally:
        db.rollback()
        db.close()


def test_manual_reopen_prevents_dibos_public_event_auto_close():
    number = f"manual-reopen-dibos-{uuid.uuid4().hex}"
    db = _session()
    incident = _auto_closed_incident(
        db, operation_id=f"dibos-{number}", operation_number=number,
    )
    reopen_incident(db, incident, user_id=123)
    incident_id = incident.id
    db.commit()
    db.close()

    dibos_enrich.enrich_events_for_org(
        ORG_ID, [], raw_public_events=[_dibos_closed_event(number)],
    )

    check = _session()
    try:
        incident = check.get(Incident, incident_id)
        assert incident.status == "active"
        assert incident.lis_auto_close_locked is True
    finally:
        check.close()


def test_reopen_route_sets_auto_close_lock(client):
    username = f"reopen-lock-admin-{uuid.uuid4().hex}"
    db = _session()
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Admin",
            org_id=ORG_ID,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="admin").one().id))
        incident = _auto_closed_incident(db, operation_id=f"route-{uuid.uuid4().hex}")
        incident_id = incident.id
        db.commit()
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    assert client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    ).status_code in {302, 303}
    response = client.post(
        f"/einsatz/{incident_id}/wiedereroeffnen",
        data={"_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    check = _session()
    try:
        incident = check.get(Incident, incident_id)
        assert incident.status == "active"
        assert incident.lis_auto_close_locked is True
    finally:
        check.close()


def test_lis_auto_close_still_closes_unlocked_active_incident():
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = Incident(
            primary_org_id=ORG_ID,
            alarm_type_code="T1",
            status="active",
            started_at=datetime(2026, 1, 5, 10, tzinfo=UTC),
            lis_operation_id="unlocked-lis-auto-close",
            lis_auto_close_locked=False,
        )
        db.add(incident)
        db.commit()

        asyncio.run(lis_sync._close_incidents_missing_from_lis(db, org, set()))

        assert incident.status == "closed"
        assert incident.closed_via_lis_auto is True
    finally:
        db.rollback()
        db.close()
