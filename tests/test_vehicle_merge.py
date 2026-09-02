import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, Fahrtzweck
from app.models.foerderstrecke import FoerderPumpenTyp
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.major_incident import (
    IncidentSite,
    LageEinheit,
    MajorIncident,
    SiteResourceAssignment,
    VehiclePosition,
)
from app.models.master import AlarmDispatchVehicle, AlarmType, FireDept, VehicleMaster
from app.models.teilnahme import Teilnahme
from app.models.user import DeviceToken, Role, User, UserRole
from app.services.vehicle_merge_service import VehicleMergeError, merge_vehicles

ORG_ID = 1


def _login(client, username: str) -> None:
    client.cookies.clear()
    client.get("/login")
    response = client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code in (302, 303)


def _org_admin(db, username: str, org_id: int = ORG_ID) -> User:
    role = db.query(Role).filter(Role.code == "org_admin").first()
    assert role is not None
    user = User(username=username, password_hash=hash_password("Test1234!"),
                display_name="Merge Admin", org_id=org_id, active=True)
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.flush()
    return user


def test_merge_haengt_neun_referenzen_um_und_uebernimmt_stammdaten():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _org_admin(db, "merge_refs_admin")
        alarm = AlarmType(org_id=ORG_ID, code="MRGREF", category="T", label="Merge")
        winner = VehicleMaster(
            dept_id=ORG_ID, code="MRG-REF", name="Gewinner", km_aktuell=10,
            betriebsstunden_aktuell=Decimal("2.0"), qr_token=None, lis_reference_id=None,
        )
        loser = VehicleMaster(
            dept_id=ORG_ID, code="MRG-REF", name="Verlierer", km_aktuell=10735,
            betriebsstunden_aktuell=Decimal("12.5"), qr_token=uuid.uuid4().hex,
            lis_reference_id="LIS-MRG-REF", kennzeichen="B-123",
        )
        db.add_all([alarm, winner, loser])
        db.flush()
        incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="closed")
        major = MajorIncident(org_id=ORG_ID, name="Merge-Lage")
        purpose = Fahrtzweck(org_id=ORG_ID, name="Merge-Fahrt", kategorie=FahrtKategorie.einsatz)
        db.add_all([incident, major, purpose])
        db.flush()
        column = IncidentColumn(incident_id=incident.id, code="active", title="Aktiv",
                                column_kind="vehicles", is_fixed=True, display_order=0)
        site = IncidentSite(major_incident_id=major.id, org_id=ORG_ID, bezeichnung="Stelle")
        db.add_all([column, site])
        db.flush()
        references = [
            IncidentVehicle(incident_id=incident.id, column_id=column.id,
                            vehicle_master_id=loser.id, unit_status="Einsatzbereit"),
            Teilnahme(org_id=ORG_ID, bezug_typ="einsatz", bezug_id=incident.id,
                      fahrzeug_id=loser.id),
            DeviceToken(label="Merge", token_hash=uuid.uuid4().hex, user_id=user.id,
                        vehicle_master_id=loser.id),
            AlarmDispatchVehicle(alarm_type_id=alarm.id, vehicle_master_id=loser.id),
            FoerderPumpenTyp(org_id=ORG_ID, name="Merge-Pumpe", vehicle_id=loser.id),
            Fahrt(org_id=ORG_ID, zeitpunkt=datetime(2026, 1, 1), fahrzeug_id=loser.id,
                  maschinist_name="Test", zweck_id=purpose.id, fahrttyp=FahrtKategorie.einsatz),
            SiteResourceAssignment(incident_site_id=site.id, resource_type="vehicle",
                                   vehicle_id=loser.id),
            LageEinheit(lage_id=major.id, vehicle_id=loser.id, label="Alt"),
            VehiclePosition(incident_id=major.id, org_id=ORG_ID, vehicle_id=loser.id,
                            resource_label="Alt", lat=47, lon=9,
                            recorded_at=datetime(2026, 1, 1)),
        ]
        db.add_all(references)
        db.flush()
        loser_qr_token = loser.qr_token

        merge_vehicles(db, winner, loser, actor_user_id=user.id)
        db.commit()

        assert references[0].vehicle_master_id == winner.id
        assert references[1].fahrzeug_id == winner.id
        assert references[2].vehicle_master_id == winner.id
        assert references[3].vehicle_master_id == winner.id
        assert references[4].vehicle_id == winner.id
        assert references[5].fahrzeug_id == winner.id
        assert references[6].vehicle_id == winner.id
        assert references[7].vehicle_id == winner.id
        assert references[8].vehicle_id == winner.id
        assert references[6].label == winner.display_label
        assert references[7].label == winner.display_label
        assert references[8].resource_label == winner.display_label
        assert winner.km_aktuell == 10735
        assert winner.betriebsstunden_aktuell == Decimal("12.5")
        assert winner.lis_reference_id == "LIS-MRG-REF"
        assert winner.qr_token == loser_qr_token
        assert loser.deleted is True
        assert loser.active is False
    finally:
        db.close()


def test_merge_dedupliziert_ausrueckordnung():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _org_admin(db, "merge_alarm_admin")
        alarm = AlarmType(org_id=ORG_ID, code="MRGALM", category="T", label="Merge")
        winner = VehicleMaster(dept_id=ORG_ID, code="MRG-ALM", name="Gewinner")
        loser = VehicleMaster(dept_id=ORG_ID, code="MRG-ALM", name="Verlierer")
        db.add_all([user, alarm, winner, loser])
        db.flush()
        db.add_all([
            AlarmDispatchVehicle(alarm_type_id=alarm.id, vehicle_master_id=winner.id),
            AlarmDispatchVehicle(alarm_type_id=alarm.id, vehicle_master_id=loser.id),
        ])
        db.flush()
        merge_vehicles(db, winner, loser, actor_user_id=user.id)
        db.flush()
        assert db.query(AlarmDispatchVehicle).filter_by(alarm_type_id=alarm.id).count() == 1
    finally:
        db.rollback()
        db.close()


def test_merge_fahrtenbuch_guard_aendert_nichts():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _org_admin(db, "merge_guard_admin")
        purpose = Fahrtzweck(org_id=ORG_ID, name="Merge-Guard", kategorie=FahrtKategorie.einsatz)
        winner = VehicleMaster(dept_id=ORG_ID, code="MRG-GRD", name="Gewinner", km_aktuell=1)
        loser = VehicleMaster(dept_id=ORG_ID, code="MRG-GRD", name="Verlierer", km_aktuell=99)
        db.add_all([purpose, winner, loser])
        db.flush()
        for vehicle in (winner, loser):
            db.add(Fahrt(org_id=ORG_ID, zeitpunkt=datetime(2026, 2, vehicle.id % 20 + 1),
                         fahrzeug_id=vehicle.id, maschinist_name="Test", zweck_id=purpose.id,
                         fahrttyp=FahrtKategorie.einsatz))
        db.flush()
        with pytest.raises(VehicleMergeError) as exc_info:
            merge_vehicles(db, winner, loser, actor_user_id=user.id)
        assert exc_info.value.code == "fahrtenbuch_conflict"
        assert winner.km_aktuell == 1
        assert loser.deleted is False
    finally:
        db.rollback()
        db.close()


def test_admin_validation_extern_erlaubt_und_tenant_delete_merge_verboten(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        _org_admin(db, "merge_route_admin")
        own = VehicleMaster(dept_id=ORG_ID, code="MRG-VAL", name="Bestehend")
        other_org = FireDept(slug="merge-route-other", name="Andere Wehr", color="#123456")
        db.add_all([own, other_org])
        db.flush()
        foreign = VehicleMaster(dept_id=other_org.id, code="MRG-X", name="Fremd")
        db.add(foreign)
        db.commit()
        own_id, foreign_id = own.id, foreign.id
    finally:
        db.close()

    _login(client, "merge_route_admin")
    csrf = client.cookies.get("ec_csrf")
    duplicate = client.post("/admin/fahrzeuge/neu", data={
        "code": " mrg-val ", "name": "Doppelt", "_csrf": csrf,
    }, follow_redirects=False)
    assert duplicate.status_code == 303
    assert "error=exists" in duplicate.headers["location"]
    external = client.post("/admin/fahrzeuge/neu-extern", data={
        "org_name": "Nachbar", "code": "MRG-VAL", "name": "Extern", "_csrf": csrf,
    }, follow_redirects=False)
    assert external.status_code == 303
    denied_delete = client.post(f"/admin/fahrzeuge/{foreign_id}/loeschen",
                                 data={"_csrf": csrf}, follow_redirects=False)
    assert denied_delete.status_code == 403
    denied_merge = client.post("/admin/fahrzeuge/zusammenfuehren", data={
        "winner_id": own_id, "loser_id": foreign_id, "_csrf": csrf,
    }, follow_redirects=False)
    assert denied_merge.status_code == 403


def test_org_admin_sieht_merge_einstieg_ohne_dubletten_und_extern_ist_keine_dublette(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        _org_admin(db, "merge_visible_admin")
        db.add_all([
            VehicleMaster(dept_id=ORG_ID, code="UNIQUE-MERGE", name="Eigen"),
            VehicleMaster(dept_id=ORG_ID, code="UNIQUE-MERGE", name="Extern",
                          is_external=True, adhoc_org_name="Nachbar"),
        ])
        db.commit()
    finally:
        db.close()
    _login(client, "merge_visible_admin")
    response = client.get("/admin/fahrzeuge")
    assert response.status_code == 200
    assert "Fahrzeuge zusammenführen" in response.text
    assert "UNIQUE-MERGE zusammenführen" not in response.text
