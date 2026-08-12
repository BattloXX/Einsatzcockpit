import io
import uuid

from openpyxl import Workbook

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentVehicle
from app.models.master import AlarmType, FireDept, VehicleMaster
from app.models.uas import UASEinsatz
from app.models.user import Role, User, UserRole
from app.services.einsatz_import_service import import_einsaetze, parse_einsatz_excel
from tests.conftest import TestingSession


def _xlsx(headers, rows) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _org(db, prefix="import"):
    suffix = uuid.uuid4().hex[:10]
    org = FireDept(slug=f"{prefix}-{suffix}", name=f"{prefix} {suffix}")
    db.add(org)
    db.flush()
    return org


def _user(db, org_id, role_code):
    username = f"einsatz_import_{role_code}_{uuid.uuid4().hex[:8]}"
    user = User(username=username, password_hash=hash_password("Test1234!"),
                display_name=username, org_id=org_id, active=True)
    db.add(user)
    db.flush()
    role = db.query(Role).filter_by(code=role_code).one()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.flush()
    return user


FORMAT_A_HEADERS = [
    "Leitstellen Nr.", "Kategorie", "Erst-Alarmierung", "Wieder einsatzbereit",
    "Fahrzeug", "Funkrufname", "Objekt", "Straße/Objekt", "Hausnummer", "Einsatzort",
]
FORMAT_B_HEADERS = [
    "Leitstellen Nummer", "Erst-Alarmierung", "Ende", "Einsatzstichwort tatsächlich",
    "Alarmtext", "Einsatzablauf", "Bemerkung", "Koordinaten N", "Koordinaten E",
]


def test_format_erkennung_a_und_b():
    parsed_a = parse_einsatz_excel(_xlsx(FORMAT_A_HEADERS, [
        ["f1", "Brand", "07.01.2026 17:50:00", "07.01.2026 18:50:00",
         "RLF-A", "Wolfurt RLF", None, "Hauptstraße", "1", "WOLFURT"],
    ]))
    parsed_b = parse_einsatz_excel(_xlsx(FORMAT_B_HEADERS, [
        ["f1", "07.01.2026 17:50:00", "07.01.2026 18:50:00", "f3",
         "Alarm", "Ablauf", None, 47.4, 9.7],
    ]))
    assert parsed_a.format == "A"
    assert parsed_b.format == "B"
    assert parsed_b.groups["f1"][0]["erst-alarmierung"] == "07.01.2026 17:50:00"


def test_import_dedup_fahrzeug_matching_und_adhoc():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        user = _user(db, org.id, "org_admin")
        db.add(AlarmType(org_id=org.id, code="F3", category="F", label="Brand"))
        known = VehicleMaster(dept_id=org.id, code="RLF-A", name="RLF", active=True)
        db.add(known)
        db.commit()

        raw = _xlsx(FORMAT_A_HEADERS, [
            ["f2601", "Brand", "07.01.2026 17:50:00", "07.01.2026 18:50:00",
             "RLF-A", "RLF", None, "Hauptstraße", "1", "WOLFURT"],
            ["f2601", "Brand", "07.01.2026 17:50:00", "07.01.2026 18:50:00",
             "DLK-A", "DLK", None, "Hauptstraße", "1", "WOLFURT"],
        ])
        first = import_einsaetze(db, parse_einsatz_excel(raw), org.id, user.id)
        second = import_einsaetze(db, parse_einsatz_excel(raw), org.id, user.id)

        incidents = db.query(Incident).filter_by(primary_org_id=org.id,
                                                 lis_operation_number="f2601").all()
        assert len(incidents) == 1
        assert first.imported == 1 and first.vehicles_attached == 2 and first.vehicles_adhoc == 1
        assert second.imported == 0 and second.vehicles_attached == 0
        assert db.query(IncidentVehicle).filter_by(incident_id=incidents[0].id).count() == 2
        adhoc = db.query(VehicleMaster).filter_by(dept_id=org.id, code="DLK-A").one()
        assert adhoc.is_adhoc is True

        detail_raw = _xlsx(FORMAT_B_HEADERS, [[
            "f2601", "07.01.2026 17:50:00", "07.01.2026 19:00:00", "f3",
            "Detailalarm", "Neuer Ablauf", None, 47.4768, 9.7465,
        ]])
        detail_result = import_einsaetze(db, parse_einsatz_excel(detail_raw), org.id, user.id)
        db.refresh(incidents[0])
        assert detail_result.updated == 1 and detail_result.imported == 0
        assert incidents[0].alarm_type_code == "F3"
        assert incidents[0].lat == 47.4768
        assert incidents[0].reason == "Brand"  # bestehender Wert wird nicht überschrieben
    finally:
        db.close()


def _login(client, username):
    client.get("/login")
    response = client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")
    }, follow_redirects=False)
    assert response.status_code in (302, 303)


def test_import_page_nur_adminrollen(client):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "access")
        users = {role: _user(db, org.id, role).username
                 for role in ("readonly", "admin", "org_admin", "system_admin")}
        db.commit()
    finally:
        db.close()
    for role, username in users.items():
        _login(client, username)
        response = client.get("/admin/einsatz-import")
        assert response.status_code == (403 if role == "readonly" else 200)
        client.cookies.clear()


def test_delete_org_scope_systemadmin_und_uas_cleanup(client):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        own_org, foreign_org = _org(db, "delete-own"), _org(db, "delete-foreign")
        admin = _user(db, own_org.id, "org_admin")
        sysadmin = _user(db, None, "system_admin")
        own = Incident(primary_org_id=own_org.id, alarm_type_code="T1", status="closed")
        foreign = Incident(primary_org_id=foreign_org.id, alarm_type_code="T1", status="closed")
        db.add_all([own, foreign])
        db.flush()
        own_id, foreign_id = own.id, foreign.id
        db.add(UASEinsatz(org_id=own_org.id, incident_id=own.id))
        db.commit()
        admin_name, sysadmin_name = admin.username, sysadmin.username
    finally:
        db.close()

    _login(client, admin_name)
    denied = client.post(f"/archiv/{foreign_id}/loeschen",
                         data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert denied.status_code == 403
    deleted = client.post(f"/archiv/{own_id}/loeschen",
                          data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert deleted.status_code == 303

    client.cookies.clear()
    _login(client, sysadmin_name)
    deleted_foreign = client.post(f"/archiv/{foreign_id}/loeschen",
                                  data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False)
    assert deleted_foreign.status_code == 303
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        assert db.get(Incident, own_id) is None
        assert db.query(UASEinsatz).filter_by(incident_id=own_id).count() == 0
        assert db.get(Incident, foreign_id) is None
    finally:
        db.close()
