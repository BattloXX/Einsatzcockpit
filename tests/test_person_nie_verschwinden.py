"""Regression tests for the rescued-person board visibility invariant."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.core.tenant import set_tenant_context
from app.core.templating import templates
from app.db import Base
from app.models.incident import Incident, IncidentColumn, IncidentVehicle, RescuedPerson
from app.models.master import FireDept, VehicleMaster
from app.routers import ui_incident
from app.services.incident_service import heal_orphaned_persons, move_card


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


@pytest.fixture()
def scene():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    set_tenant_context(db, None)
    org = FireDept(slug="person-visible", name="Person Visible", color="#f00", bos="Feuerwehr")
    db.add(org)
    db.flush()
    incident = Incident(primary_org_id=org.id, alarm_type_code="T1")
    other = Incident(primary_org_id=org.id, alarm_type_code="T2")
    db.add_all([incident, other])
    db.flush()
    rescued = IncidentColumn(incident_id=incident.id, code="rescued", title="Personen", column_kind="rescued")
    rescued_two = IncidentColumn(incident_id=incident.id, code="rescued-2", title="Personen 2", column_kind="rescued")
    other_rescued = IncidentColumn(incident_id=other.id, code="rescued", title="Personen", column_kind="rescued")
    db.add_all([rescued, rescued_two, other_rescued])
    master = VehicleMaster(dept_id=org.id, code="P1", name="Person 1")
    db.add(master)
    db.flush()
    vehicle = IncidentVehicle(incident_id=incident.id, column_id=rescued.id, vehicle_master_id=master.id)
    removed = IncidentVehicle(incident_id=incident.id, column_id=rescued.id, vehicle_master_id=master.id)
    other_vehicle = IncidentVehicle(incident_id=other.id, column_id=other_rescued.id, vehicle_master_id=master.id)
    db.add_all([vehicle, removed, other_vehicle])
    db.flush()
    removed.removed_at = vehicle.created_at
    yield SimpleNamespace(
        db=db, incident=incident, other=other, rescued=rescued, rescued_two=rescued_two,
        vehicle=vehicle, removed=removed, other_vehicle=other_vehicle,
    )
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.mark.parametrize("status", ["gefunden", "versorgt", "abtransportiert", "verstorben"])
def test_vehicle_card_renders_every_person_status(scene, status):
    person = RescuedPerson(
        incident_id=scene.incident.id, column_id=scene.rescued.id,
        vehicle_id=scene.vehicle.id, status=status, name=status,
    )
    scene.db.add(person)
    scene.db.flush()

    html = templates.env.get_template("incident/_vehicle_card.html").render(
        incident=scene.incident, vehicle=scene.vehicle, can_edit=False,
    )

    assert f'data-kind="person"' in html
    assert status in html


@pytest.mark.parametrize("vehicle_id", ["other_vehicle", "removed", 999999])
def test_invalid_person_vehicle_move_keeps_person_unchanged(scene, vehicle_id):
    person = RescuedPerson(incident_id=scene.incident.id, column_id=scene.rescued.id, name="Bleibt")
    scene.db.add(person)
    scene.db.flush()

    assert not move_card(
        scene.db, scene.incident.id, "person", person.id,
        vehicle_id=getattr(scene, vehicle_id).id if isinstance(vehicle_id, str) else vehicle_id,
    )
    scene.db.refresh(person)
    assert (person.column_id, person.vehicle_id) == (scene.rescued.id, None)


def test_person_move_between_vehicle_and_rescued_column_is_visible_once(scene):
    person = RescuedPerson(incident_id=scene.incident.id, column_id=scene.rescued.id, name="Einmal")
    scene.db.add(person)
    scene.db.flush()

    assert move_card(scene.db, scene.incident.id, "person", person.id, vehicle_id=scene.vehicle.id)
    assert move_card(scene.db, scene.incident.id, "person", person.id, column_id=scene.rescued_two.id)
    scene.db.refresh(person)
    assert (person.column_id, person.vehicle_id) == (scene.rescued_two.id, None)


def test_heal_orphaned_persons_repairs_only_current_incident(scene):
    orphan = RescuedPerson(
        incident_id=scene.incident.id, column_id=scene.rescued_two.id,
        vehicle_id=scene.removed.id, name="Heilen",
    )
    untouched = RescuedPerson(
        incident_id=scene.other.id, column_id=scene.other_vehicle.column_id,
        vehicle_id=scene.other_vehicle.id, name="Andere Lage",
    )
    scene.db.add_all([orphan, untouched])
    scene.db.flush()

    assert heal_orphaned_persons(scene.db, scene.incident.id) == 1
    scene.db.refresh(orphan)
    scene.db.refresh(untouched)
    assert (orphan.vehicle_id, orphan.column_id) == (None, scene.rescued.id)
    assert untouched.vehicle_id == scene.other_vehicle.id


def test_person_modal_keeps_removed_current_vehicle_selectable(scene):
    person = RescuedPerson(
        incident_id=scene.incident.id, column_id=scene.rescued.id,
        vehicle_id=scene.removed.id, name="Bleibt zugeordnet",
    )
    scene.db.add(person)
    scene.db.flush()

    html = templates.env.get_template("incident/_person_modal.html").render(
        incident=scene.incident, person=person, can_edit=True,
    )

    assert f'value="{scene.removed.id}" selected' in html
    assert "(entfernt)" in html


def test_person_move_endpoint_returns_conflict_for_invalid_vehicle(scene, monkeypatch):
    person = RescuedPerson(incident_id=scene.incident.id, column_id=scene.rescued.id, name="Konflikt")
    scene.db.add(person)
    scene.db.flush()

    async def no_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr(ui_incident.manager, "broadcast", no_broadcast)
    request = Request({"type": "http", "method": "POST", "path": "/"})
    request.state.user = SimpleNamespace(id=1, org_id=scene.incident.primary_org_id)
    response = asyncio.run(ui_incident.move_card_endpoint(
        scene.incident.id, request, BackgroundTasks(), "person", person.id,
        vehicle_id=scene.other_vehicle.id, db=scene.db, _=None,
    ))

    assert response.status_code == 409


def test_sortable_resyncs_after_failed_move():
    source = ("app/static/js/sortable-glue.js")
    text = open(source, encoding="utf-8").read()
    assert "if (!response.ok)" in text
    assert "Verschieben fehlgeschlagen" in text
    assert "/inhalt" in text
    assert "EinsatzBoard" not in text
