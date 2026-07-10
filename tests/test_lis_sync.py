"""Tests für lis_sync._get_or_link_incident: verbinden statt duplizieren, in
beiden Reihenfolgen (LIS zuerst / API zuerst) — sowie _sync_vehicle_status für
Fahrzeugstatus (S4/S5), Fahrzeugposition (LocationX/LocationY) und
_close_incidents_missing_from_lis (Auto-Close, wenn eine Operation in LIS
nicht mehr aktiv ist)."""
import asyncio
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentColumn, IncidentVehicle, Message, Task
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
        is_exercise=False,
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


def test_lis_first_creates_incident_as_exercise_when_flagged():
    """LIS-first-Neuanlage mit is_exercise=True (Testeinsatz-Fall) muss den Einsatz
    auch als Übungseinsatz in Einsatzcockpit anlegen, nicht als echten Einsatz."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)

        incident, created = lis_sync._get_or_link_incident(
            db, org, _parsed(lis_operation_id="lis-op-uebung", is_exercise=True),
        )

        assert created is True
        assert incident.is_exercise is True
    finally:
        db.rollback()
        db.close()


def test_parse_operation_extracts_real_schema_including_exercise_flag(setup_db):
    """End-to-End mit dem echten Schema aus Capture 2026-07-04 (Testeinsatz LIS,
    Type.Code='t_t3' mit Präfix, Type.Type='Schulungseinsatz...') — nach dem Fix von
    _result_list() liegen diese Felder direkt (flach) im op-dict, nicht mehr unter
    einem verschachtelten 'Operation'-Key."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        raw_op = {
            "Id": "f93e6e1c-cded-4aa7-aa52-3ab66da8d40e",
            "Number": "t_t30007",
            "Name": "Testeinsatz LIS",
            "Description": "Testeinsatz LIS",
            "BeginTime": "2026-07-04T07:44:30",
            "Address": {"Street": "Flotzbachstraße", "Housenumber": "27a", "Community": "Wolfurt"},
            "Type": {"Code": "t_t3", "Type": "Schulungseinsatz (ohne RFL) - Feuerwehr"},
        }

        parsed = lis_sync._parse_operation(raw_op, org)
        incident, created = lis_sync._get_or_link_incident(db, org, parsed)

        assert created is True
        assert incident.alarm_type_code == "T3"
        assert incident.is_exercise is True
        assert incident.address_street == "Flotzbachstraße"
    finally:
        db.rollback()
        db.close()


def test_parse_operation_uses_delivered_coordinates_directly(setup_db):
    """Liefert die LIS-Operation eigene Koordinaten (Operation.LocationX/Y, echtes Schema
    aus dem Mitschnitt), werden sie direkt nach WGS84 übernommen und am Einsatz gesetzt —
    ohne Adressvalidierung/Geocoding."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        raw_op = {
            "Id": "op-mit-koordinaten",
            "Number": "t_f140010",
            "Description": "BMA Ausgelöst",
            "BeginTime": "2026-07-05T21:22:40",
            "Address": {"Street": "KESSELSTRAßE", "Housenumber": "42", "Community": "WOLFURT"},
            "Type": {"Code": "t_f14", "Type": "Schulungseinsatz (ohne RFL) - Feuerwehr"},
            "LocationX": "105669",
            "LocationY": "257241",
        }

        parsed = lis_sync._parse_operation(raw_op, org)
        assert parsed["lat"] is not None and parsed["lng"] is not None
        # Plausibel für Wolfurt/Vorarlberg (siehe lis_geo-Plausibilitätsgrenzen)
        assert 47.4 < parsed["lat"] < 47.5
        assert 9.7 < parsed["lng"] < 9.8

        incident, created = lis_sync._get_or_link_incident(db, org, parsed)
        assert created is True
        assert incident.lat == parsed["lat"]
        assert incident.lng == parsed["lng"]
    finally:
        db.rollback()
        db.close()


def test_parse_operation_marks_ended_operation_as_closed(setup_db):
    """EndTime gesetzt → is_closed=True (Steuersignal gegen Alarmierung bei Backfill
    historischer/bereits beendeter Operationen). Ohne EndTime → is_closed=False."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        base = {
            "Id": "op-ended",
            "Number": "t_f140011",
            "Description": "BMA Ausgelöst",
            "BeginTime": "2026-07-05T21:22:40",
            "Address": {"Street": "Kesselstraße", "Housenumber": "42", "Community": "Wolfurt"},
            "Type": {"Code": "t_f14", "Type": "Feuer"},
        }
        aktiv = lis_sync._parse_operation(base, org)
        assert aktiv["is_closed"] is False

        beendet = lis_sync._parse_operation({**base, "EndTime": "2026-07-05T21:33:50"}, org)
        assert beendet["is_closed"] is True
        assert beendet["ended_at"] is not None

        # .NET-Default-Datum zählt nicht als beendet
        default = lis_sync._parse_operation({**base, "EndTime": "0001-01-01T00:00:00"}, org)
        assert default["is_closed"] is False
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


# ── Meldungen (JOURNAL) vs. Aufträge (TASK) ─────────────────────────────────

def _journal_task(task_id="lis-task-journal", description="Info an alle") -> dict:
    return {
        "Id": task_id, "Number": "M0001", "Description": description, "CreatedBy": "LIS",
        "Type": {"Type": "JOURNAL", "Label": "Meldung"},
    }


def _auftrag_task(task_id="lis-task-auftrag", description="An Stab zuteilen",
                   deadline="2026-07-04T18:00:00") -> dict:
    return {
        "Id": task_id, "Number": "A0001", "Description": description, "CreatedBy": "LIS",
        "DeadlineTime": deadline,
        "Type": {"Type": "TASK", "Label": "Auftrag"},
    }


def test_is_lis_auftrag_covers_all_auftrag_subtypes():
    """Der vollständige Task.Type-Katalog dieser LIS-Installation (GetTaskTypes,
    2026-07-04) hat drei Auftrags-Untertypen (TASK/DEFAULTTASK/SIMPLETASK) — alle
    drei müssen als Auftrag erkannt werden, alles andere nicht."""
    from app.services.lis.lis_mapping import is_lis_auftrag

    assert is_lis_auftrag("TASK") is True
    assert is_lis_auftrag("DEFAULTTASK") is True
    assert is_lis_auftrag("SIMPLETASK") is True
    assert is_lis_auftrag("JOURNAL") is False
    assert is_lis_auftrag("UNITSTATUSHISTORY") is False
    assert is_lis_auftrag("DISPATCHSYSTEM") is False
    assert is_lis_auftrag("PROTOCOL") is False
    assert is_lis_auftrag("INFORMATION") is False
    assert is_lis_auftrag(None) is False


def test_sync_messages_only_imports_journal_not_task():
    """Ein echter LIS-Auftrag (Type.Type=='TASK') darf NICHT als Message landen —
    das war vor der Trennung Meldungen/Aufträge der Fall (siehe _sync_tasks)."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, _ = lis_sync._get_or_link_incident(
            db, org, _parsed(lis_operation_id="lis-op-split-1", reason="Split-Test 1", street="Split-Straße 1"),
        )

        lis_sync._sync_messages(db, org, incident, [_journal_task(), _auftrag_task()])

        # Filter auf lis_task_id != None: create_incident() kann zusätzlich
        # org-eigene Standard-Meldungsvorlagen seeden (seed_service.py) — hier
        # interessieren nur die aus LIS-Tasks synchronisierten Meldungen.
        messages = (
            db.query(Message)
            .filter(Message.incident_id == incident.id, Message.lis_task_id.isnot(None))
            .all()
        )
        assert len(messages) == 1
        assert messages[0].lis_task_id == "lis-task-journal"
        assert messages[0].title == "LIS: M0001"
    finally:
        db.rollback()
        db.close()


def test_sync_tasks_only_imports_task_not_journal_and_sets_deadline():
    """Ein Auftrag (Type.Type=='TASK') landet im Task-Modell (Aufträge-Board), mit
    DeadlineTime als due_at — eine Meldung (JOURNAL) wird hier nicht importiert."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, _ = lis_sync._get_or_link_incident(
            db, org, _parsed(lis_operation_id="lis-op-split-2", reason="Split-Test 2", street="Split-Straße 2"),
        )

        lis_sync._sync_tasks(db, org, incident, [_journal_task(), _auftrag_task()])

        tasks = (
            db.query(Task)
            .filter(Task.incident_id == incident.id, Task.lis_task_id.isnot(None))
            .all()
        )
        assert len(tasks) == 1
        task = tasks[0]
        assert task.lis_task_id == "lis-task-auftrag"
        assert task.source == "lis"
        assert task.due_at is not None
        assert task.title == "LIS: A0001"
    finally:
        db.rollback()
        db.close()


def test_sync_tasks_is_idempotent_and_updates_deadline():
    """Wiederholtes Polling desselben Auftrags darf keinen zweiten Task anlegen,
    aktualisiert aber eine geänderte Frist."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, _ = lis_sync._get_or_link_incident(
            db, org, _parsed(lis_operation_id="lis-op-split-3", reason="Split-Test 3", street="Split-Straße 3"),
        )

        lis_sync._sync_tasks(db, org, incident, [_auftrag_task(deadline="2026-07-04T18:00:00")])
        lis_sync._sync_tasks(db, org, incident, [_auftrag_task(deadline="2026-07-04T19:30:00")])

        tasks = (
            db.query(Task)
            .filter(Task.incident_id == incident.id, Task.lis_task_id.isnot(None))
            .all()
        )
        assert len(tasks) == 1
        expected = lis_sync._parse_operation_datetime("2026-07-04T19:30:00", org)
        assert tasks[0].due_at == expected
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


# ── Auto-Close: LIS-Operation nicht mehr aktiv → Einsatz schließen ─────────

def test_close_incidents_missing_from_lis_closes_stale_incident():
    """Ein über LIS verknüpfter, noch aktiver Einsatz wird geschlossen, sobald
    seine Operation nicht mehr im aktuellen ActiveParticipation-Ergebnis ist."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, _ = lis_sync._get_or_link_incident(
            db, org, _parsed(lis_operation_id="lis-op-wird-geschlossen"),
        )
        db.commit()

        asyncio.run(lis_sync._close_incidents_missing_from_lis(db, org, set()))
        db.commit()

        db.refresh(incident)
        assert incident.status == "closed"
        assert incident.closed_at is not None
    finally:
        db.rollback()
        db.close()


def test_close_incidents_missing_from_lis_keeps_still_active_incident():
    """Taucht die Operation weiterhin im aktiven Ergebnis auf, bleibt der
    Einsatz unangetastet."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, _ = lis_sync._get_or_link_incident(
            db, org, _parsed(lis_operation_id="lis-op-bleibt-aktiv"),
        )
        db.commit()

        asyncio.run(
            lis_sync._close_incidents_missing_from_lis(db, org, {"lis-op-bleibt-aktiv"}),
        )
        db.commit()

        db.refresh(incident)
        assert incident.status == "active"
    finally:
        db.rollback()
        db.close()


def test_close_incidents_missing_from_lis_ignores_incidents_without_lis_link():
    """Einsätze ohne lis_operation_id (rein manuell/API angelegt) dürfen durch
    den LIS-Auto-Close niemals geschlossen werden."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        manual_incident = Incident(
            primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
            reason="Verkehrsunfall", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
        )
        db.add(manual_incident)
        db.commit()

        asyncio.run(lis_sync._close_incidents_missing_from_lis(db, org, set()))
        db.commit()

        db.refresh(manual_incident)
        assert manual_incident.status == "active"
    finally:
        db.rollback()
        db.close()


# ── sync_operation: SMS+Push muessen bei neu angelegtem Einsatz feuern ──────

class _FakeLisClientNoTasks:
    """Minimaler Fake fuer sync_operation() – keine Aufgaben/Einheiten/Dokumente,
    nur der Neuanlage-Pfad interessiert hier."""

    async def select_operation(self, organization_id, operation_id=None):
        pass

    async def get_tasks(self, operation_id):
        return []

    async def get_operation_units(self, organization_id, operation_id):
        return []

    async def get_documents_by_operation_id(self, operation_id):
        return []


def test_sync_operation_new_incident_triggers_notify(monkeypatch):
    """Bisher loeste der LIS-Sync bei automatischer Neuanlage weder SMS noch Push
    aus (kein Request-Kontext -> kein BackgroundTasks). sync_operation() muss jetzt
    fuer einen neu angelegten Einsatz notify_incident_created() aufrufen."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        from app.models.lis import OrgLisConfig
        config = OrgLisConfig(org_id=ORG_ID, organization_id="org-guid")

        calls = []

        async def fake_notify(db_arg, incident, *, org_id, background_tasks=None, **kw):
            calls.append((incident.id, org_id, background_tasks))

        monkeypatch.setattr(
            "app.services.incident_notify.notify_incident_created", fake_notify,
        )

        raw_op = {
            "Id": "lis-op-notify-test",
            "Number": "f900001",
            "Name": "Verkehrsunfall",
            "Description": "Verkehrsunfall",
            "BeginTime": "2026-07-04T10:00:00",
            "Address": {"Street": "Teststrasse", "Housenumber": "1", "Community": "Wolfurt"},
            "Type": {"Code": "t4", "Type": "Verkehrsunfall"},
        }

        asyncio.run(
            lis_sync.sync_operation(db, org, config, _FakeLisClientNoTasks(), raw_op)
        )

        assert len(calls) == 1
        incident_id, org_id, background_tasks = calls[0]
        assert org_id == ORG_ID
        assert background_tasks is None
        incident = db.get(Incident, incident_id)
        assert incident.lis_operation_id == "lis-op-notify-test"
    finally:
        db.rollback()
        db.close()


# ── sync_operation: Diagnose-Opt-in startet Rohdaten-Aufzeichnung automatisch ──

def _notify_raw_op(op_id: str) -> dict:
    return {
        "Id": op_id,
        "Number": "f900001",
        "Name": "Verkehrsunfall",
        "Description": "Verkehrsunfall",
        "BeginTime": "2026-07-04T10:00:00",
        "Address": {"Street": "Teststrasse", "Housenumber": "1", "Community": "Wolfurt"},
        "Type": {"Code": "t4", "Type": "Verkehrsunfall"},
    }


def test_sync_operation_auto_starts_capture_when_enabled(monkeypatch):
    """OrgLisConfig.auto_capture_on_new_operation=True: sync_operation() muss bei
    Neuanlage automatisch eine 120-Minuten-Aufzeichnung starten (Diagnose-Opt-in,
    system_admin, siehe ui_lis.py)."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        from app.models.lis import OrgLisConfig
        config = OrgLisConfig(
            org_id=ORG_ID, organization_id="org-guid", auto_capture_on_new_operation=True,
        )

        async def fake_notify(*a, **kw):
            pass

        monkeypatch.setattr("app.services.incident_notify.notify_incident_created", fake_notify)

        calls = []

        async def fake_start_capture(org_id, duration_minutes=120):
            calls.append((org_id, duration_minutes))
            return "run-id-test"

        monkeypatch.setattr(
            "app.services.lis.lis_capture.start_capture_for_org", fake_start_capture,
        )

        asyncio.run(
            lis_sync.sync_operation(db, org, config, _FakeLisClientNoTasks(),
                                    _notify_raw_op("lis-op-autocap-on"))
        )

        assert calls == [(ORG_ID, 120)]
    finally:
        db.rollback()
        db.close()


def test_sync_operation_does_not_start_capture_when_disabled(monkeypatch):
    """Default (auto_capture_on_new_operation=False): keine automatische Aufzeichnung —
    das Diagnose-Feature ist bewusst Opt-in."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        from app.models.lis import OrgLisConfig
        config = OrgLisConfig(org_id=ORG_ID, organization_id="org-guid")  # Default: False

        async def fake_notify(*a, **kw):
            pass

        monkeypatch.setattr("app.services.incident_notify.notify_incident_created", fake_notify)

        calls = []

        async def fake_start_capture(org_id, duration_minutes=120):
            calls.append((org_id, duration_minutes))
            return "run-id-test"

        monkeypatch.setattr(
            "app.services.lis.lis_capture.start_capture_for_org", fake_start_capture,
        )

        asyncio.run(
            lis_sync.sync_operation(db, org, config, _FakeLisClientNoTasks(),
                                    _notify_raw_op("lis-op-autocap-off"))
        )

        assert calls == []
    finally:
        db.rollback()
        db.close()


def test_sync_operation_auto_capture_failure_does_not_break_incident_creation(monkeypatch):
    """start_capture_for_org() wirft ValueError, wenn bereits eine Aufzeichnung fuer
    die Org laeuft (siehe lis_capture.py) — das darf die Einsatz-Anlage nie stoeren."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        from app.models.lis import OrgLisConfig
        config = OrgLisConfig(
            org_id=ORG_ID, organization_id="org-guid", auto_capture_on_new_operation=True,
        )

        async def fake_notify(*a, **kw):
            pass

        monkeypatch.setattr("app.services.incident_notify.notify_incident_created", fake_notify)

        async def failing_start_capture(org_id, duration_minutes=120):
            raise ValueError("Für diese Organisation läuft bereits eine Aufzeichnung.")

        monkeypatch.setattr(
            "app.services.lis.lis_capture.start_capture_for_org", failing_start_capture,
        )

        asyncio.run(
            lis_sync.sync_operation(db, org, config, _FakeLisClientNoTasks(),
                                    _notify_raw_op("lis-op-autocap-fail"))
        )

        incident = db.query(Incident).filter(Incident.lis_operation_id == "lis-op-autocap-fail").first()
        assert incident is not None
    finally:
        db.rollback()
        db.close()


class _FakeLisClientRecordingSelectOperation(_FakeLisClientNoTasks):
    """Zeichnet select_operation()-Aufrufe auf (Experiment 2, 2026-07-05): sync_operation()
    muss select_operation() mit der konkreten operationId aufrufen, BEVOR get_tasks()
    aufgerufen wird — siehe select_operation()-Docstring in lis_client.py."""

    def __init__(self):
        self.calls = []

    async def select_operation(self, organization_id, operation_id=None):
        self.calls.append(("select_operation", organization_id, operation_id))

    async def get_tasks(self, operation_id):
        self.calls.append(("get_tasks", operation_id))
        return []


def test_sync_operation_selects_operation_before_get_tasks(monkeypatch):
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        from app.models.lis import OrgLisConfig
        config = OrgLisConfig(org_id=ORG_ID, organization_id="org-guid")

        async def fake_notify(*a, **kw):
            pass

        monkeypatch.setattr(
            "app.services.incident_notify.notify_incident_created", fake_notify,
        )

        raw_op = {
            "Id": "lis-op-select-test",
            "Number": "f900003",
            "Name": "Verkehrsunfall",
            "BeginTime": "2026-07-04T10:00:00",
            "Address": {"Street": "Bundesstraße", "Housenumber": "1", "Community": "Wolfurt"},
            "Type": {"Code": "t4", "Type": "Verkehrsunfall"},
        }

        client = _FakeLisClientRecordingSelectOperation()
        asyncio.run(lis_sync.sync_operation(db, org, config, client, raw_op))

        assert client.calls == [
            ("select_operation", "org-guid", "lis-op-select-test"),
            ("get_tasks", "lis-op-select-test"),
        ]
    finally:
        db.rollback()
        db.close()


def test_sync_operation_linked_incident_does_not_trigger_notify(monkeypatch):
    """Wird eine LIS-Operation nur mit einem bereits bestehenden Einsatz verknuepft
    (nicht neu angelegt), darf keine erneute Benachrichtigung ausgeloest werden."""
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        from app.models.lis import OrgLisConfig
        config = OrgLisConfig(org_id=ORG_ID, organization_id="org-guid")

        calls = []

        async def fake_notify(*a, **kw):
            calls.append(1)

        monkeypatch.setattr(
            "app.services.incident_notify.notify_incident_created", fake_notify,
        )

        existing = Incident(
            primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
            reason="Verkehrsunfall", address_street="Bundesstraße", address_no="1",
            address_city="Wolfurt", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
            lis_operation_id="lis-op-already-linked",
        )
        db.add(existing)
        db.flush()

        raw_op = {
            "Id": "lis-op-already-linked",
            "Number": "f900002",
            "Name": "Verkehrsunfall",
            "BeginTime": "2026-07-04T10:00:00",
            "Address": {"Street": "Bundesstraße", "Housenumber": "1", "Community": "Wolfurt"},
            "Type": {"Code": "t4", "Type": "Verkehrsunfall"},
        }

        asyncio.run(
            lis_sync.sync_operation(db, org, config, _FakeLisClientNoTasks(), raw_op)
        )

        assert calls == []
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

        # Delta statt Absolutwert: die Tabelle ist session-weit geteilt (setup_db
        # räumt erst am Ende der gesamten Testsuite auf), andere Tests können
        # bereits eigene VehiclePosition-Zeilen angelegt haben.
        count_before = db.query(VehiclePosition).count()
        lis_sync._sync_vehicle_status(db, org, incident, [person_unit])

        assert db.query(VehiclePosition).count() == count_before
    finally:
        db.rollback()
        db.close()


# ── Dokumenten-Sync: keine rohe GUID im sichtbaren Titel ─────────────────────

class _FakeDocClient:
    def __init__(self, docs):
        self._docs = docs

    async def get_documents_by_operation_id(self, operation_id):
        return self._docs

    async def download_document(self, doc_id, entity=None):
        return b"%PDF-1.4 fake bytes"


def test_sync_documents_never_uses_guid_as_title(monkeypatch):
    """Ein LIS-Dokument OHNE Name (z. B. ein Bild) darf NICHT die rohe Dokument-GUID
    als Kartentitel bekommen ('Dokument: <guid>.pdf'), sondern einen lesbaren Namen."""
    async def _fake_store(upload, message, user, db, org_id=None):
        return None

    monkeypatch.setattr(
        "app.services.media_service.store_upload_for_message", _fake_store,
    )

    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        incident, _ = lis_sync._get_or_link_incident(
            db, org, _parsed(lis_operation_id="lis-op-doc-1", reason="Doc-Test", street="Doc-Straße 1"),
        )
        guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        docs = [
            {"Id": "c9cbd9be-ba21-4c51-a314-6791f8fb5680",
             "Name": "BMA_Datenblatt_1384", "FileExtension": ".pdf"},
            {"Id": guid, "Name": None, "DocumentType": None, "FileExtension": ".jpg"},
        ]
        client = _FakeDocClient(docs)
        changed = asyncio.run(lis_sync._sync_documents(db, org, incident, client, "lis-op-doc-1"))
        assert changed is True

        titles = [
            m.title for m in db.query(Message)
            .filter(Message.incident_id == incident.id, Message.title.like("Dokument:%"))
            .all()
        ]
        assert len(titles) == 2
        # Keine rohe GUID in irgendeinem Titel
        assert all(guid not in t for t in titles)
        assert "Dokument: BMA_Datenblatt_1384" in titles
        # Der namenlose Treffer bekommt einen lesbaren Fallback
        assert any(t.startswith("Dokument: LIS-Dokument") for t in titles)
    finally:
        db.rollback()
        db.close()
