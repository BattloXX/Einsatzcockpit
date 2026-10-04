"""Archiv-Detail/PDF: Fahrzeuge einer Partner-Org duerfen die Seite nicht abstuerzen lassen."""
from app.core.tenant import set_tenant_context
from app.models.incident import IncidentColumn, IncidentVehicle
from app.models.master import FireDept, VehicleMaster
from app.services.incident_service import create_incident
from tests.conftest import TestingSession
from tests.test_wordpress_report_archive import _login, _make_user_and_incident


def test_archiv_detail_mit_fahrzeug_fremder_org(client, setup_db):
    username, _ = _make_user_and_incident("archiv_fremd", "admin")
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        partner = FireDept(slug="partner-ff", name="Partner FF", short_code="PFF")
        db.add(partner)
        db.flush()
        incident, _ = create_incident(db, "T1", is_exercise=True, primary_org_id=1)
        column = db.query(IncidentColumn).filter(IncidentColumn.incident_id == incident.id).first()
        vehicle = VehicleMaster(dept_id=partner.id, code="TLF", name="TLF Partner", type="TLF")
        db.add(vehicle)
        db.flush()
        db.add(IncidentVehicle(incident_id=incident.id, column_id=column.id, vehicle_master_id=vehicle.id))
        db.commit()
        incident_id = incident.id
    finally:
        db.close()
    _login(client, username)
    response = client.get(f"/archiv/{incident_id}")
    assert response.status_code == 200
    assert "Partner FF" in response.text
