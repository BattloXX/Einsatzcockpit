"""Tests fuer Freigaben von Strassensperren an Nachbar-Organisationen."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.invitation import OrgPartner
from app.models.master import FireDept, OrgSettings
from app.models.road_closure import IncidentRoute, RoadClosure, RoadClosureChange, RoadClosureShare
from app.models.user import AuditLog
from app.services import road_closure_service as service
from tests.test_strassensperren_ui import _login, _payload, _setup_user


def _org(db, name):
    org = FireDept(slug=f"nachbar-{uuid4().hex}", name=name, timezone="Europe/Vienna")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, strassensperren_modul_aktiv=True))
    return org


def _closure(org_id):
    return RoadClosure(
        org_id=org_id,
        title="Nachbar-Sperre",
        street="Hauptstraße",
        valid_from=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
        restriction_type="closed",
        geometry_geojson=json.dumps({"type": "Point", "coordinates": [9.75, 47.47]}),
        geometry_status="ok",
    )


def test_set_shares_prueft_partner_auditiert_und_markiert_entfernte_route(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a, org_b, org_c = _org(db, "Nachbar A"), _org(db, "Nachbar B"), _org(db, "Nachbar C")
        db.add(OrgPartner(org_id=org_a.id, partner_org_id=org_b.id))
        closure = _closure(org_a.id)
        db.add(closure)
        db.flush()

        added, removed = service.set_shares(db, closure, [org_a.id, org_b.id], None)
        assert added == [org_b.id] and removed == []
        assert service.shared_org_ids(db, closure) == [org_b.id]
        db.flush()
        change = (
            db.query(RoadClosureChange)
            .execution_options(include_all_tenants=True)
            .filter_by(road_closure_id=closure.id, action="shared")
            .one()
        )
        assert json.loads(change.after_json) == [org_b.id]
        audit = db.query(AuditLog).filter_by(action="road_closure.shared", entity_id=closure.id).one()
        assert json.loads(audit.payload_json)["hinzugefuegt"] == [org_b.id]

        with pytest.raises(ValueError, match="Partner-Organisationen"):
            service.set_shares(db, closure, [org_c.id], None)
        assert service.shared_org_ids(db, closure) == [org_b.id]

        incident = Incident(primary_org_id=org_b.id, alarm_type_code="T1", status="active")
        db.add(incident)
        db.flush()
        route = IncidentRoute(
            org_id=org_b.id,
            incident_id=incident.id,
            route_geojson=json.dumps({"type": "LineString", "coordinates": [[9.74, 47.47], [9.76, 47.47]]}),
            dest_lat=47.47,
            dest_lng=9.75,
        )
        db.add(route)
        db.flush()
        assert service.set_shares(db, closure, [], None) == ([], [org_b.id])
        assert route.stale is True
    finally:
        db.rollback()
        db.close()


def test_ui_freigabe_an_nachbarorganisation(client):
    owner = _setup_user("objekt_verwalter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b, org_c = _org(db, "Sichtbare Nachbarwehr"), _org(db, "Unsichtbare Organisation")
        db.add(OrgPartner(org_id=owner.org_id, partner_org_id=org_b.id))
        db.commit()
        org_b_id, org_c_id = org_b.id, org_c.id
    finally:
        db.close()
    user_b, user_c = _setup_user("objekt_verwalter", org_id=org_b_id), _setup_user("objekt_verwalter", org_id=org_c_id)

    _login(client, owner)
    titel = f"Nachbar-Sperre {org_b_id}"
    form = _payload(client, title=titel, freigabe_org_ids=[str(org_b_id)])
    response = client.post("/strassensperren/neu", data=form, follow_redirects=False)
    assert response.status_code == 303
    closure_id = int(response.headers["location"].rsplit("/", 1)[1])
    edit_form = client.get(f"/strassensperren/{closure_id}/bearbeiten").text
    assert "Sichtbare Nachbarwehr" in edit_form and "Unsichtbare Organisation" not in edit_form

    _login(client, user_b)
    assert titel in client.get("/strassensperren?status=all&scope=shared").text
    assert titel not in client.get("/strassensperren?status=all&scope=own").text
    detail = client.get(f"/strassensperren/{closure_id}")
    assert detail.status_code == 200 and "Besitzer-Organisation" in detail.text and "Angelegt" not in detail.text
    assert client.get(f"/strassensperren/{closure_id}/bearbeiten").status_code == 404

    _login(client, user_c)
    assert client.get(f"/strassensperren/{closure_id}").status_code == 404

    _login(client, owner)
    response = client.post(
        f"/strassensperren/{closure_id}/bearbeiten",
        data=_payload(client, title=titel, version="2"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    _login(client, user_b)
    assert client.get(f"/strassensperren/{closure_id}").status_code == 404
