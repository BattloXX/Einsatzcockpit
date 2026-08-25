from datetime import datetime
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.tenant import set_tenant_context
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, Fahrtzweck
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.major_incident import IncidentSite, MajorIncident
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
async def test_payload_includes_and_deduplicates_fahrtenbuch_vehicles(monkeypatch):
    _MockAsyncClient.calls = []
    _MockAsyncClient.status_code = 201
    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    db = _session()
    try:
        _configure(db)
        incident = _incident(db, city="Wolfurt")
        board_vehicle = incident.vehicles[0].vehicle_master
        extra_vehicle = VehicleMaster(
            dept_id=ORG_ID, code=f"WP{incident.id}EXTRA", name="TLF", type="TLF",
            lis_reference_id=f"lis-extra-{incident.id}",
        )
        purpose = Fahrtzweck(
            org_id=ORG_ID, name=f"WP Test {incident.id}",
            kategorie=FahrtKategorie.einsatz,
        )
        db.add_all([extra_vehicle, purpose])
        db.flush()
        for vehicle in (board_vehicle, extra_vehicle):
            db.add(Fahrt(
                org_id=ORG_ID, zeitpunkt=datetime(2026, 8, 9, 12, 35),
                fahrzeug_id=vehicle.id, maschinist_name="Max Muster",
                zweck_id=purpose.id, fahrttyp=FahrtKategorie.einsatz,
                incident_id=incident.id,
            ))
        db.flush()

        result = await post_incident_report(db, incident)

        assert result.success is True
        assert _MockAsyncClient.calls[0]["json"]["fahrzeuge"] == [
            f"lis-{incident.id}", f"lis-extra-{incident.id}",
        ]
    finally:
        db.rollback()
        db.close()


@pytest.mark.asyncio
async def test_title_skips_internal_incident_number_without_lis_number(monkeypatch):
    """Die interne laufende Nummer darf nicht als Titel-Fallback erscheinen."""
    _MockAsyncClient.calls = []
    _MockAsyncClient.status_code = 201
    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    db = _session()
    try:
        _configure(db)
        alarm_type = db.query(AlarmType).filter_by(org_id=ORG_ID, code="T4").first()
        alarm_type.label = ""
        incident = _incident(db, city="Wolfurt", reason=None)
        incident.lis_operation_number = None
        incident.nummer = "999"

        result = await post_incident_report(db, incident)

        assert result.success is True
        assert _MockAsyncClient.calls[0]["json"]["title"] == f"Einsatz {incident.id}"
    finally:
        db.rollback()
        db.close()


@pytest.mark.asyncio
async def test_title_uses_lis_operation_number_as_numeric_fallback(monkeypatch):
    _MockAsyncClient.calls = []
    _MockAsyncClient.status_code = 201
    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    db = _session()
    try:
        _configure(db)
        alarm_type = db.query(AlarmType).filter_by(org_id=ORG_ID, code="T4").first()
        alarm_type.label = ""
        incident = _incident(db, city="Wolfurt", reason=None)
        incident.lis_operation_number = "f26001234"
        incident.nummer = "999"

        result = await post_incident_report(db, incident)

        assert result.success is True
        assert _MockAsyncClient.calls[0]["json"]["title"] == "Einsatz Nr. f26001234"
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
async def test_major_incident_only_posts_one_report_for_the_whole_lage(monkeypatch):
    """Zwei Einsatzstellen derselben Großschadenslage dürfen nur EINEN
    WordPress-Bericht erzeugen: die zweite Einsatzstelle übernimmt den Post der
    ersten, statt einen zweiten HTTP-Aufruf auszulösen."""
    _MockAsyncClient.calls = []
    _MockAsyncClient.status_code = 201
    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    db = _session()
    try:
        _configure(db)
        org = db.get(FireDept, ORG_ID)
        org.timezone = "Europe/Vienna"
        lage = MajorIncident(org_id=ORG_ID, name="Sturmschäden Gemeindegebiet")
        db.add(lage)
        db.flush()

        first = _incident(db, city="Wolfurt", reason="Baum auf Fahrbahn")
        second = _incident(db, city="Wolfurt", reason="Dach abgedeckt")
        db.add_all([
            IncidentSite(major_incident_id=lage.id, org_id=ORG_ID, bezeichnung="Stelle 1", incident_id=first.id),
            IncidentSite(major_incident_id=lage.id, org_id=ORG_ID, bezeichnung="Stelle 2", incident_id=second.id),
        ])
        db.flush()

        result_first = await post_incident_report(db, first)
        assert result_first.success is True
        assert result_first.already_existed is False
        assert len(_MockAsyncClient.calls) == 1
        assert _MockAsyncClient.calls[0]["json"]["title"] == "Sturmschäden Gemeindegebiet"
        assert first.wp_report_post_id == 4711

        class _ForbiddenClient:
            def __init__(self, *args, **kwargs):
                raise AssertionError("HTTP must not be called for the second Einsatzstelle")

        monkeypatch.setattr(httpx, "AsyncClient", _ForbiddenClient)

        result_second = await post_incident_report(db, second)
        assert result_second.success is True
        assert result_second.already_existed is True
        assert result_second.post_id == 4711
        assert second.wp_report_post_id == 4711
        assert second.wp_report_edit_url == first.wp_report_edit_url
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_json",
    [
        {"post_id": 4711},
        {"post_id": 4711, "edit_url": None},
        {"post_id": 4711, "edit_url": "   "},
    ],
    ids=["missing", "null", "empty"],
)
async def test_invalid_edit_url_leaves_incident_retryable(monkeypatch, response_json):
    _MockAsyncClient.calls = []
    _MockAsyncClient.status_code = 201
    monkeypatch.setattr(_MockAsyncClient, "response_json", response_json)
    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)
    db = _session()
    try:
        _configure(db)
        incident = _incident(db, city="Wolfurt")

        result = await post_incident_report(db, incident)

        assert result.success is False
        assert incident.wp_report_post_id is None
        assert incident.wp_report_edit_url is None
    finally:
        db.rollback()
        db.close()
