"""Regression: Beim Verschieben (Drag&Drop) eines mit einer Einheit verbundenen
Auftrags/einer Meldung innerhalb einer Spalte darf die Verbindung zur Einheit
(vehicle_id) NICHT verloren gehen. Nur wenn die Karte bewusst aus der Fahrzeug-Zone
heraus gezogen wird (detach_vehicle=True), wird die Zuordnung gelöst.
"""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentColumn, IncidentVehicle, Message, Task
from app.models.master import FireDept, VehicleMaster
from app.services.incident_service import move_card


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    set_tenant_context(session, None)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def scene(db):
    org = FireDept(slug="mc-test", name="MC Test", color="#ff0000", bos="Feuerwehr")
    db.add(org)
    db.flush()
    inc = Incident(primary_org_id=org.id, alarm_type_code="T1")
    db.add(inc)
    db.flush()
    tasks_col = IncidentColumn(incident_id=inc.id, code="tasks", title="Aufträge", column_kind="tasks")
    active_col = IncidentColumn(incident_id=inc.id, code="active", title="Im Einsatz", column_kind="vehicles")
    db.add_all([tasks_col, active_col])
    db.flush()
    vm = VehicleMaster(dept_id=org.id, code="RLF", name="RLF-A")
    db.add(vm)
    db.flush()
    vehicle = IncidentVehicle(incident_id=inc.id, column_id=active_col.id,
                              vehicle_master_id=vm.id, display_order=0)
    db.add(vehicle)
    db.flush()
    return {"db": db, "inc": inc, "tasks_col": tasks_col, "vehicle": vehicle}


def test_reorder_task_in_column_keeps_vehicle_link(scene):
    """Ein mit einer Einheit verbundener Auftrag (vehicle_id + column_id gesetzt) wird
    innerhalb der Spalte umsortiert → vehicle_id bleibt erhalten."""
    db, inc, col, vehicle = scene["db"], scene["inc"], scene["tasks_col"], scene["vehicle"]
    task = Task(incident_id=inc.id, column_id=col.id, vehicle_id=vehicle.id,
                title="Auftrag an Einheit", display_order=0)
    db.add(task)
    db.flush()

    move_card(db, inc.id, "task", task.id, column_id=col.id, position=2)

    db.refresh(task)
    assert task.vehicle_id == vehicle.id  # Verbindung erhalten
    assert task.column_id == col.id
    assert task.display_order == 2


def test_drag_task_off_vehicle_detaches(scene):
    """Wird der Auftrag bewusst aus der Fahrzeug-Zone auf eine Spalte gezogen
    (detach_vehicle=True), wird die Einheiten-Zuordnung gelöst."""
    db, inc, col, vehicle = scene["db"], scene["inc"], scene["tasks_col"], scene["vehicle"]
    task = Task(incident_id=inc.id, column_id=col.id, vehicle_id=vehicle.id,
                title="Auftrag an Einheit", display_order=0)
    db.add(task)
    db.flush()

    move_card(db, inc.id, "task", task.id, column_id=col.id, position=0, detach_vehicle=True)

    db.refresh(task)
    assert task.vehicle_id is None  # bewusst gelöst
    assert task.column_id == col.id


def test_drop_task_on_vehicle_assigns(scene):
    """Drop auf eine Fahrzeug-Zone verbindet den Auftrag mit der Einheit."""
    db, inc, col, vehicle = scene["db"], scene["inc"], scene["tasks_col"], scene["vehicle"]
    task = Task(incident_id=inc.id, column_id=col.id, title="Freier Auftrag", display_order=0)
    db.add(task)
    db.flush()

    move_card(db, inc.id, "task", task.id, vehicle_id=vehicle.id, position=0)

    db.refresh(task)
    assert task.vehicle_id == vehicle.id


def test_reorder_message_in_column_keeps_vehicle_link(scene):
    """Analog für Meldungen: Umsortieren in der Spalte erhält die Einheiten-Zuordnung."""
    db, inc, col, vehicle = scene["db"], scene["inc"], scene["tasks_col"], scene["vehicle"]
    msg = Message(incident_id=inc.id, column_id=col.id, vehicle_id=vehicle.id,
                  title="Meldung an Einheit", display_order=0)
    db.add(msg)
    db.flush()

    move_card(db, inc.id, "message", msg.id, column_id=col.id, position=1)

    db.refresh(msg)
    assert msg.vehicle_id == vehicle.id
