"""Tests für die neue Ausrückordnung ohne "Disponiert"-Spalte:
- Ohne LIS-Anbindung landen Fahrzeuge aus der Ausrückordnung direkt in "active".
- Mit aktiver LIS-Anbindung legt die Ausrückordnung keine Fahrzeuge automatisch an —
  sie erscheinen erst mit LIS-Status S4 (_sync_vehicle_status) oder manuell.
- LIS-Auto-Close: Wiedereröffnen + Sperre gegen erneuten Auto-Close (lis_auto_close_locked).
"""
import asyncio
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.lis import OrgLisConfig
from app.models.master import AlarmType, FireDept, VehicleMaster
from app.services import incident_service
from app.services.lis import lis_sync
from tests.conftest import TestingSession

ORG_ID = 1  # FF Wolfurt


def _session() -> "TestingSession":
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _make_incident_with_active_column(db, org_id: int = ORG_ID) -> Incident:
    incident = Incident(
        primary_org_id=org_id, alarm_type_code="T4", status="active",
        reason="Verkehrsunfall", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
    )
    db.add(incident)
    db.flush()
    db.add(IncidentColumn(
        incident_id=incident.id, code="active", title="Tatsächlich im Einsatz", column_kind="vehicles",
        is_fixed=True,
    ))
    db.flush()
    db.refresh(incident, ["columns"])
    return incident


def _make_wolfurt_vehicle(db, code: str = "RLF-DISP") -> VehicleMaster:
    vm = VehicleMaster(dept_id=ORG_ID, code=code, name="Rüstlöschfahrzeug", active=True)
    db.add(vm)
    db.flush()
    return vm


def test_populate_vehicles_without_lis_places_directly_in_active():
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        assert org.slug == "wolfurt"
        incident = _make_incident_with_active_column(db)
        vm = _make_wolfurt_vehicle(db)
        alarm = AlarmType(org_id=ORG_ID, code="DISPTEST1", label="Test", default_first_train_only=False)
        db.add(alarm)
        db.flush()

        incident_service._populate_vehicles(db, incident, alarm)

        vehicles = db.query(IncidentVehicle).filter(IncidentVehicle.incident_id == incident.id).all()
        assert any(v.vehicle_master_id == vm.id for v in vehicles)
        active_col = next(c for c in incident.columns if c.code == "active")
        placed = next(v for v in vehicles if v.vehicle_master_id == vm.id)
        assert placed.column_id == active_col.id
    finally:
        db.rollback()
        db.close()


def test_populate_vehicles_with_lis_enabled_skips_auto_creation():
    db = _session()
    try:
        incident = _make_incident_with_active_column(db)
        _make_wolfurt_vehicle(db, code="RLF-DISP2")
        alarm = AlarmType(org_id=ORG_ID, code="DISPTEST2", label="Test", default_first_train_only=False)
        db.add(alarm)
        db.add(OrgLisConfig(org_id=ORG_ID, enabled=True))
        db.flush()

        incident_service._populate_vehicles(db, incident, alarm)

        vehicles = db.query(IncidentVehicle).filter(IncidentVehicle.incident_id == incident.id).all()
        assert vehicles == []
    finally:
        db.rollback()
        db.close()


def _s4_unit(ref_id: str, operation_unit_id: str = "ou-guid-1") -> dict:
    return {
        "Id": operation_unit_id,
        "ReferenceId": ref_id,
        "OperationUnitStatusType": {"Label": "S4 - zum Einsatzort"},
        "UnitType": {"Type": "Vehicle"},
    }


def test_sync_vehicle_status_creates_vehicle_on_first_s4():
    """Mit aktiver LIS-Anbindung existiert noch kein IncidentVehicle — der erste
    S4-Status muss das Fahrzeug direkt in "active" neu anlegen."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = _make_incident_with_active_column(db)
        vm = VehicleMaster(dept_id=ORG_ID, code="RLF-B", name="Rüstlöschfahrzeug",
                            lis_reference_id="rlf_neu", active=True)
        db.add(vm)
        db.flush()

        lis_sync._sync_vehicle_status(db, org, incident, [_s4_unit("rlf_neu")])

        iv = (
            db.query(IncidentVehicle)
            .filter(IncidentVehicle.incident_id == incident.id, IncidentVehicle.vehicle_master_id == vm.id)
            .first()
        )
        assert iv is not None
        assert iv.unit_status == "Einsatz übernommen"
        assert iv.lis_operation_unit_id == "ou-guid-1"
        active_col = next(c for c in incident.columns if c.code == "active")
        assert iv.column_id == active_col.id
    finally:
        db.rollback()
        db.close()


def test_sync_vehicle_status_without_status_does_not_create_vehicle():
    """Ein Fahrzeug ohne S4/S5-Status (z.B. nur alarmiert/S1) darf noch nicht im
    Board auftauchen, solange kein IncidentVehicle existiert."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = _make_incident_with_active_column(db)
        db.add(VehicleMaster(dept_id=ORG_ID, code="RLF-C", name="Rüstlöschfahrzeug",
                              lis_reference_id="rlf_kein_status", active=True))
        db.flush()

        unit = {"Id": "ou-2", "ReferenceId": "rlf_kein_status",
                "OperationUnitStatusType": {"Label": "S1 - Einsatzbereit"},
                "UnitType": {"Type": "Vehicle"}}
        lis_sync._sync_vehicle_status(db, org, incident, [unit])

        assert db.query(IncidentVehicle).filter(IncidentVehicle.incident_id == incident.id).count() == 0
    finally:
        db.rollback()
        db.close()


# ── LIS-Auto-Close: Wiedereröffnen + Sperre ─────────────────────────────────

def test_get_or_link_incident_reopens_auto_closed_and_locks():
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = Incident(
            primary_org_id=ORG_ID, alarm_type_code="T4", status="closed",
            reason="Verkehrsunfall", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
            closed_at=datetime(2026, 7, 4, 11, 0, tzinfo=UTC),
            closed_via_lis_auto=True, lis_auto_close_locked=False,
            lis_operation_id="lis-op-reopen-test",
        )
        db.add(incident)
        db.flush()

        parsed = {
            "lis_operation_id": "lis-op-reopen-test", "lis_operation_number": "f1",
            "reason": "Verkehrsunfall", "street": None, "city": None,
            "alarm_type_code": "T4", "started_at": None, "is_exercise": False,
            "report_text": None,
        }
        result, created = lis_sync._get_or_link_incident(db, org, parsed)

        assert created is False
        assert result.id == incident.id
        assert result.status == "active"
        assert result.closed_at is None
        assert result.lis_auto_close_locked is True
    finally:
        db.rollback()
        db.close()


def test_get_or_link_incident_leaves_manually_closed_incident_alone():
    """Ein manuell (nicht per LIS-Auto-Close) geschlossener Einsatz darf NICHT
    automatisch wiedereröffnet werden, nur weil die Operation wieder aktiv ist."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = Incident(
            primary_org_id=ORG_ID, alarm_type_code="T4", status="closed",
            reason="Verkehrsunfall", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
            closed_at=datetime(2026, 7, 4, 11, 0, tzinfo=UTC),
            closed_via_lis_auto=False,
            lis_operation_id="lis-op-manual-close-test",
        )
        db.add(incident)
        db.flush()

        parsed = {
            "lis_operation_id": "lis-op-manual-close-test", "lis_operation_number": "f1",
            "reason": "Verkehrsunfall", "street": None, "city": None,
            "alarm_type_code": "T4", "started_at": None, "is_exercise": False,
            "report_text": None,
        }
        result, created = lis_sync._get_or_link_incident(db, org, parsed)

        assert created is False
        assert result.status == "closed"
    finally:
        db.rollback()
        db.close()


def test_close_incidents_missing_from_lis_skips_locked_incident():
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = Incident(
            primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
            reason="Verkehrsunfall", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
            lis_operation_id="lis-op-locked-test", lis_auto_close_locked=True,
        )
        db.add(incident)
        db.commit()

        asyncio.run(lis_sync._close_incidents_missing_from_lis(db, org, set()))
        db.commit()

        db.refresh(incident)
        assert incident.status == "active"
    finally:
        db.rollback()
        db.close()


def test_close_incidents_missing_from_lis_marks_closed_via_lis_auto():
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident = Incident(
            primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
            reason="Verkehrsunfall", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
            lis_operation_id="lis-op-autoclose-flag-test",
        )
        db.add(incident)
        db.commit()

        asyncio.run(lis_sync._close_incidents_missing_from_lis(db, org, set()))
        db.commit()

        db.refresh(incident)
        assert incident.status == "closed"
        assert incident.closed_via_lis_auto is True
    finally:
        db.rollback()
        db.close()
