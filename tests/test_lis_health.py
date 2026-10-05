# ruff: noqa: E702
from datetime import UTC, datetime, timedelta

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.lis import OrgLisConfig
from app.services.lis import lis_health
from tests.conftest import TestingSession


@pytest.fixture(autouse=True)
def _reset_lis_health_state():
    lis_health._last_ok.clear()
    yield
    lis_health._last_ok.clear()


def test_lis_health_requires_config_and_link():
    db = TestingSession()
    set_tenant_context(db, 1)
    try:
        incident = Incident(primary_org_id=1, alarm_type_code="T1", status="active")
        db.add(incident)
        db.flush()
        assert lis_health.lis_delivers_for(db, 1, incident) is False
        config = OrgLisConfig(
            org_id=1,
            enabled=True,
            base_url="https://lis",
            organization_id="1",
            username="u",
            password_enc="encrypted",
            poll_interval_seconds=5,
        )
        db.add(config)
        incident.lis_operation_id = "op-1"
        db.flush()
        lis_health.mark_lis_ok(1)
        assert lis_health.lis_delivers_for(db, 1, incident) is True
        lis_health._last_ok[1] = datetime.now(UTC) - timedelta(seconds=121)
        assert lis_health.lis_delivers_for(db, 1, incident) is False
        incident.lis_operation_id = None
        assert lis_health.lis_delivers_for(db, 1, incident) is False
    finally:
        db.rollback()
        db.close()


def test_lis_health_start_grace(monkeypatch):
    db = TestingSession()
    set_tenant_context(db, 1)
    try:
        config = OrgLisConfig(
            org_id=1,
            enabled=True,
            base_url="https://lis",
            organization_id="1",
            username="u",
            password_enc="encrypted",
            poll_interval_seconds=5,
        )
        incident = Incident(primary_org_id=1, alarm_type_code="T1", status="active", lis_operation_id="op-2")
        db.add_all([config, incident])
        db.flush()
        monkeypatch.setattr(lis_health, "_started_at", datetime.now(UTC))
        lis_health._last_ok.pop(1, None)
        assert lis_health.lis_delivers_for(db, 1, incident) is True
    finally:
        db.rollback()
        db.close()
