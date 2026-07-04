"""Tests für lis_sync._get_or_link_incident: verbinden statt duplizieren, in
beiden Reihenfolgen (LIS zuerst / API zuerst) — sowie _sync_vehicle_status für
Fahrzeugstatus (S4/S5) und Fahrzeugposition (LocationX/LocationY)."""
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.major_incident import IncidentSite, MajorIncident, VehiclePosition
from app.models.master import FireDept, VehicleMaster
from app.services.lis import lis_sync
from tests.conftest import TestingSession

ORG_ID = 1  # FF Wolfurt


def _session() -> "TestingSession":
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _parsed(**overrides) -> dict:
    defaults = dict(
        lis_operation_id="lis-op-abc",
        lis_operation_number="f26005863",
        reason="Verkehrsunfall",
        street="Bundesstraße",
        house_no="1",
        city="Wolfurt",
        report_text="Verkehrsunfall",
        alarm_type_code="T4",
        started_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    return defaults


def _count_incidents(db) -> int:
    return db.query(Incident).filter(Incident.primary_org_id == ORG_ID).count()


def test_api_first_then_lis_links_without_duplicate():
    """API legt den Einsatz zuerst an (external_key gesetzt, keine lis_operation_id).
    Danach liefert das LIS dieselbe Operation — es darf KEIN zweiter Einsatz entstehen,
    stattdessen wird der vorhandene verknüpft."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        api_incident = Incident(
            primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
            reason="Verkehrsunfall", address_street="Bundesstraße", address_no="1",
            address_city="Wolfurt", started_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
            external_key="api-key-123", lis_operation_id=None,
        )
        db.add(api_incident)
        db.flush()
        before_count = _count_incidents(db)

        incident, created = lis_sync._get_or_link_incident(db, org, _parsed())

        assert created is False
        assert incident.id == api_incident.id
        assert incident.lis_operation_id == "lis-op-abc"
        assert incident.lis_operation_number == "f26005863"
        assert incident.external_key == "api-key-123"  # unverändert
        assert _count_incidents(db) == before_count  # keine Dublette
    finally:
        db.rollback()
        db.close()


def test_lis_first_creates_incident_without_external_key():
    """LIS liefert zuerst — es existiert noch kein API-Einsatz. Der Einsatz wird
    selbst angelegt, external_key bleibt leer (die API kann ihn später nachtragen)."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        before_count = _count_incidents(db)

        incident, created = lis_sync._get_or_link_incident(db, org, _parsed(lis_operation_id="lis-op-neu"))

        assert created is True
        assert incident.lis_operation_id == "lis-op-neu"
        assert incident.external_key is None
        assert _count_incidents(db) == before_count + 1
    finally:
        db.rollback()
        db.close()


def test_repeated_lis_sync_is_idempotent_no_duplicate():
    """Wiederholtes Polling derselben LIS-Operation (z.B. jeden 30s) darf nie
    einen zweiten Einsatz anlegen — direkter lis_operation_id-Treffer greift."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        parsed = _parsed(lis_operation_id="lis-op-repeat")

        incident1, created1 = lis_sync._get_or_link_incident(db, org, parsed)
        after_first = _count_incidents(db)
        incident2, created2 = lis_sync._get_or_link_incident(db, org, parsed)
        after_second = _count_incidents(db)

        assert created1 is True
        assert created2 is False
        assert incident1.id == incident2.id
        assert after_first == after_second
    finally:
        db.rollback()
        db.close()


# ── Fahrzeugposition (LocationX/LocationY -> VehiclePosition) ───────────────

def _make_incident_with_vehicle(db, org_id: int, lis_reference_id: str = "rlf_wolfu"):
    """Legt Einsatz + Fahrzeug + Fahrzeugzuweisung an, damit _sync_vehicle_status
    das Fahrzeug per ReferenceId auflösen kann."""
    incident = Incident(
        primary_org_id=org_id, alarm_type_code="T4", status="active",
        reason="Verkehrsunfall", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
    )
    db.add(incident)
    db.flush()

    vehicle_master = VehicleMaster(dept_id=org_id, code="RLF-A", name="Rüstlöschfahrzeug",
                                    lis_reference_id=lis_reference_id)
    db.add(vehicle_master)
    db.flush()

    column = IncidentColumn(incident_id=incident.id, code="dispatched", title="Disponiert",
                             column_kind="vehicles")
    db.add(column)
    db.flush()

    incident_vehicle = IncidentVehicle(
        incident_id=incident.id, column_id=column.id, vehicle_master_id=vehicle_master.id,
    )
    db.add(incident_vehicle)
    db.flush()
    return incident, vehicle_master, incident_vehicle


def _s5_unit(ref_id: str = "rlf_wolfu", location_x="105308", location_y="260817") -> dict:
    return {
        "ReferenceId": ref_id,
        "OperationUnitStatusType": {"Label": "S5 - am Einsatzort"},
        "LocationX": location_x,
        "LocationY": location_y,
    }


def test_sync_vehicle_location_writes_position_without_major_incident():
    """Ohne Großschadenslage wird die Position mit incident_id=None geschrieben
    (analog zu App-Positionen außerhalb einer GSL)."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, vehicle_master, incident_vehicle = _make_incident_with_vehicle(db, ORG_ID)

        lis_sync._sync_vehicle_status(db, org, incident, [_s5_unit()])

        positions = db.query(VehiclePosition).filter(VehiclePosition.vehicle_id == vehicle_master.id).all()
        assert len(positions) == 1
        pos = positions[0]
        assert pos.source == "lis"
        assert pos.incident_id is None
        assert pos.org_id == ORG_ID
        assert 47.4 < pos.lat < 47.6
        assert 9.6 < pos.lon < 9.9
        assert incident_vehicle.unit_status == "Am Einsatzort"
    finally:
        db.rollback()
        db.close()


def test_sync_vehicle_location_uses_major_incident_id_when_linked_via_site():
    """Ist der Einsatz als Einsatzstelle einer Großschadenslage zugeordnet,
    landet die LIS-Position mit derselben Lage-ID wie App-GPS-Positionen —
    damit beide auf der Lagekarte zusammengeführt werden (gleiche Tabelle,
    gleiches Feld, nicht getrennt)."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, vehicle_master, _ = _make_incident_with_vehicle(db, ORG_ID)

        lage = MajorIncident(org_id=ORG_ID, name="Großschadenslage Test")
        db.add(lage)
        db.flush()
        site = IncidentSite(major_incident_id=lage.id, org_id=ORG_ID, bezeichnung="Stelle 1",
                             incident_id=incident.id)
        db.add(site)
        db.flush()

        lis_sync._sync_vehicle_status(db, org, incident, [_s5_unit()])

        pos = db.query(VehiclePosition).filter(VehiclePosition.vehicle_id == vehicle_master.id).first()
        assert pos is not None
        assert pos.incident_id == lage.id
    finally:
        db.rollback()
        db.close()


def test_sync_vehicle_location_skipped_when_coordinates_missing():
    """Ein Statuswechsel ohne Koordinaten (z.B. S4 - zum Einsatzort) darf keine
    Position erzeugen — kein Fallback auf (0, 0) oder Vorwerte."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, vehicle_master, incident_vehicle = _make_incident_with_vehicle(db, ORG_ID)
        unit = {
            "ReferenceId": "rlf_wolfu",
            "OperationUnitStatusType": {"Label": "S4 - zum Einsatzort"},
            "LocationX": None,
            "LocationY": None,
        }

        lis_sync._sync_vehicle_status(db, org, incident, [unit])

        assert db.query(VehiclePosition).filter(VehiclePosition.vehicle_id == vehicle_master.id).count() == 0
        assert incident_vehicle.unit_status == "Einsatz übernommen"
    finally:
        db.rollback()
        db.close()


def test_sync_vehicle_location_ignores_unmapped_reference_id():
    """Einheiten ohne passende lis_reference_id (z.B. Personen wie
    'markus.bereiter') dürfen keine Fahrzeugposition erzeugen — Personen- und
    Fahrzeugkoordinaten bleiben getrennt (siehe Koordinaten-Doku Abschnitt 3.2)."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, vehicle_master, _ = _make_incident_with_vehicle(db, ORG_ID)
        person_unit = _s5_unit(ref_id="andreas.schneider4", location_x="105251", location_y="257303")

        lis_sync._sync_vehicle_status(db, org, incident, [person_unit])

        assert db.query(VehiclePosition).count() == 0
    finally:
        db.rollback()
        db.close()
