from datetime import datetime
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.master import AlarmType, FireDept, VehicleMaster
from app.models.wordpress_report import WordPressReportConfig
from app.services.wordpress_report_service import post_incident_report
from tests.conftest import TestingSession

ORG_ID = 1


def _session():
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


class _MockAsyncClient:
    calls: list[dict] = []
    status_code = 201
    response_json = {"post_id": 4711, "edit_url": "https://website.test/wp-admin/post.php?post=4711&action=edit"}

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        self.calls.append({"url": url, "json": json, "timeout": self.timeout})
        return httpx.Response(
            self.status_code,
            json=self.response_json,
            request=httpx.Request("POST", url),
        )


def _configure(db):
    existing = db.query(WordPressReportConfig).filter_by(org_id=ORG_ID).first()
    if existing:
        db.delete(existing)
        db.flush()
    db.add(WordPressReportConfig(
        org_id=ORG_ID,
        enabled=True,
        webhook_url="https://website.test/fw-einsatzbericht-api.php",
        webhook_token="secret-token",
    ))


def _incident(db, *, city: str, reason: str | None = "Zimmerbrand") -> Incident:
    incident = Incident(
        primary_org_id=ORG_ID,
        alarm_type_code="T4",
        status="closed",
        reason=reason,
        address_street="Hauptstrasse",
        address_no="17",
        address_city=city,
        started_at=datetime(2026, 8, 9, 12, 30),
        closed_at=datetime(2026, 8, 9, 13, 10),
    )
    db.add(incident)
    db.flush()
    column = IncidentColumn(incident_id=incident.id, code="active", title="Einheiten")
    db.add(column)
    db.flush()
    with_ref = VehicleMaster(
        dept_id=ORG_ID, code=f"WP{incident.id}A", name="RLF", type="RLF",
        lis_reference_id=f"lis-{incident.id}",
    )
    without_ref = VehicleMaster(
        dept_id=ORG_ID, code=f"WP{incident.id}B", name="KDO", type="KDO",
        lis_reference_id=None,
    )
    db.add_all([with_ref, without_ref])
    db.flush()
    db.add_all([
        IncidentVehicle(incident_id=incident.id, column_id=column.id, vehicle_master_id=with_ref.id),
        IncidentVehicle(incident_id=incident.id, column_id=column.id, vehicle_master_id=without_ref.id),
    ])
    db.flush()
    db.refresh(incident)
    return incident


@pytest.mark.asyncio
async def test_missing_or_disabled_config_does_not_call_httpx(monkeypatch):
    class _ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("HTTP must not be called")

    monkeypatch.setattr(httpx, "AsyncClient", _ForbiddenClient)
    db = _session()
    try:
        cfg = db.query(WordPressReportConfig).filter_by(org_id=ORG_ID).first()
        if cfg:
            db.delete(cfg)
            db.flush()
        incident = _incident(db, city="Wolfurt")
        result = await post_incident_report(db, incident)
        assert result.success is False
        assert "nicht konfiguriert" in result.error
    finally:
        db.rollback()
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("incident_city", "org_city", "expected_location"),
    [(" Wolfurt ", "wolfurt", "Hauptstrasse"), ("Dornbirn", "Wolfurt", "Dornbirn Hauptstrasse")],
)
async def test_success_payload_innerorts_and_ausserorts(
    monkeypatch, incident_city, org_city, expected_location
):
    _MockAsyncClient.calls = []
    _MockAsyncClient.status_code = 201
    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    db = _session()
    try:
        _configure(db)
        org = db.get(FireDept, ORG_ID)
        org.city = org_city
        org.timezone = "Europe/Vienna"
        alarm_type = db.query(AlarmType).filter_by(org_id=ORG_ID, code="T4").first()
        alarm_type.wp_einsatzart = "Technischer Einsatz"
        incident = _incident(db, city=incident_city)
        commit = MagicMock(wraps=db.commit)
        db.commit = commit

        result = await post_incident_report(db, incident)

        assert result.success is True
        assert commit.call_count == 1
        payload = _MockAsyncClient.calls[0]["json"]
        assert _MockAsyncClient.calls[0]["url"].endswith("?token=secret-token")
        assert payload["title"] == "Zimmerbrand"
        assert payload["alarmzeit"] == "2026-08-09T14:30"
        assert payload["einsatzende"] == "2026-08-09T15:10"
        assert payload["dauer_min"] == 40
        assert payload["einsatzort"] == expected_location
        assert payload["einsatzart"] == "Technischer Einsatz"
        assert payload["fahrzeuge"] == [f"lis-{incident.id}"]
        assert incident.wp_report_post_id == 4711
        assert incident.wp_report_edit_url.startswith("https://website.test/wp-admin/")
    finally:
        db.rollback()
        db.close()


@pytest.mark.asyncio
async def test_existing_post_is_first_guard_and_never_calls_httpx(monkeypatch):
    class _ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("HTTP must not be called")

    monkeypatch.setattr(httpx, "AsyncClient", _ForbiddenClient)
    db = _session()
    try:
        incident = _incident(db, city="Wolfurt")
        incident.wp_report_post_id = 99
        incident.wp_report_edit_url = "https://website.test/edit/99"
        result = await post_incident_report(db, incident)
        assert result.success is True
        assert result.already_existed is True
        assert result.post_id == 99
    finally:
        db.rollback()
        db.close()


@pytest.mark.asyncio
async def test_non_2xx_leaves_incident_retryable(monkeypatch):
    _MockAsyncClient.calls = []
    _MockAsyncClient.status_code = 500
    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    db = _session()
    try:
        _configure(db)
        incident = _incident(db, city="Wolfurt")
        result = await post_incident_report(db, incident)
        assert result.success is False
        assert incident.wp_report_post_id is None
    finally:
        db.rollback()
        db.close()
