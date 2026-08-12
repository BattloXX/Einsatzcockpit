import io
import uuid
from datetime import datetime

from openpyxl import Workbook

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
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
from app.models.uas import UASEinsatz
from app.models.user import DeviceToken, Role, User, UserRole
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
    "Leitstellen Nummer", "Erst-Alarmierung", "Übernommen", "Ausfahrt", "Am Einsatzort",
    "Wieder einsatzbereit", "Ende", "Einsatzstichwort tatsächlich", "Alarmtext",
    "Einsatzablauf", "Bemerkung", "Koordinaten N", "Koordinaten E",
]


def test_format_erkennung_a_und_b():
    parsed_a = parse_einsatz_excel(_xlsx(FORMAT_A_HEADERS, [
        ["f1", "Brand", "07.01.2026 17:50:00", "07.01.2026 18:50:00",
         "RLF-A", "Wolfurt RLF", None, "Hauptstraße", "1", "WOLFURT"],
    ]))
    parsed_b = parse_einsatz_excel(_xlsx(FORMAT_B_HEADERS, [
        ["f1", "07.01.2026 17:50:00", None, None, None, None,
         "07.01.2026 18:50:00", "f3", "Alarm", "Ablauf", None, 47.4, 9.7],
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
            "f2601", "07.01.2026 17:50:00", None, None, None, None,
            "07.01.2026 19:00:00", "f3", "Detailalarm", "Neuer Ablauf", None,
            47.4768, 9.7465,
        ]])
        detail_result = import_einsaetze(db, parse_einsatz_excel(detail_raw), org.id, user.id)
        db.refresh(incidents[0])
        assert detail_result.updated == 1 and detail_result.imported == 0
        assert incidents[0].alarm_type_code == "F3"
        assert incidents[0].lat == 47.4768
        assert incidents[0].reason == "Brand"  # bestehender Wert wird nicht überschrieben
    finally:
        db.close()


def test_format_b_importiert_alle_statuszeiten():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "statuszeiten")
        user = _user(db, org.id, "org_admin")
        db.commit()
        raw = _xlsx(FORMAT_B_HEADERS, [[
            "f-status", "07.01.2026 08:43:00", "07.01.2026 08:44:00",
            "07.01.2026 08:47:00", "07.01.2026 08:49:00", "07.01.2026 08:59:00",
            "07.01.2026 09:01:00", "T1", "Alarm", "Ablauf", None, 47.4, 9.7,
        ]])

        result = import_einsaetze(db, parse_einsatz_excel(raw), org.id, user.id)
        incident = db.query(Incident).filter_by(
            primary_org_id=org.id, lis_operation_number="f-status"
        ).one()

        assert result.imported == 1
        assert incident.started_at == datetime(2026, 1, 7, 7, 43)
        assert incident.taken_over_at == datetime(2026, 1, 7, 7, 44)
        assert incident.departed_at == datetime(2026, 1, 7, 7, 47)
        assert incident.on_scene_at == datetime(2026, 1, 7, 7, 49)
        assert incident.ready_again_at == datetime(2026, 1, 7, 7, 59)
        assert incident.closed_at == datetime(2026, 1, 7, 8, 1)
    finally:
        db.close()


def test_reimport_korrigiert_alle_statuszeiten():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "reimport")
        user = _user(db, org.id, "org_admin")
        db.commit()
        first_raw = _xlsx(FORMAT_B_HEADERS, [[
            "f-reimport", "07.01.2026 08:00:00", "07.01.2026 08:01:00",
            "07.01.2026 08:02:00", "07.01.2026 08:03:00", "07.01.2026 08:04:00",
            "07.01.2026 08:05:00", "T1", "Alarm", "Ablauf", None, 47.4, 9.7,
        ]])
        import_einsaetze(db, parse_einsatz_excel(first_raw), org.id, user.id)
        incident = db.query(Incident).filter_by(
            primary_org_id=org.id, lis_operation_number="f-reimport"
        ).one()
        second_raw = _xlsx(FORMAT_B_HEADERS, [[
            "f-reimport", "07.01.2026 09:00:00", "07.01.2026 09:01:00",
            "07.01.2026 09:02:00", "07.01.2026 09:03:00", "07.01.2026 09:04:00",
            "07.01.2026 09:05:00", "T1", "Alarm", "Ablauf", None, 47.4, 9.7,
        ]])
        result = import_einsaetze(db, parse_einsatz_excel(second_raw), org.id, user.id)
        db.refresh(incident)

        assert result.updated == 1
        assert incident.started_at == datetime(2026, 1, 7, 8, 0)
        assert incident.taken_over_at == datetime(2026, 1, 7, 8, 1)
        assert incident.departed_at == datetime(2026, 1, 7, 8, 2)
        assert incident.on_scene_at == datetime(2026, 1, 7, 8, 3)
        assert incident.ready_again_at == datetime(2026, 1, 7, 8, 4)
        assert incident.closed_at == datetime(2026, 1, 7, 8, 5)
    finally:
        db.close()


def test_reimport_ueberschreibt_manuell_geaenderte_adresse_nicht():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "reimport-adresse")
        user = _user(db, org.id, "org_admin")
        db.commit()
        headers = FORMAT_B_HEADERS + ["Straße/Objekt"]
        raw = _xlsx(headers, [[
            "f-adresse", "07.01.2026 08:00:00", None, None, None, None,
            "07.01.2026 08:05:00", "T1", "Alarm", "Ablauf", None, 47.4, 9.7,
            "Importstraße",
        ]])
        parsed = parse_einsatz_excel(raw)
        import_einsaetze(db, parsed, org.id, user.id)
        incident = db.query(Incident).filter_by(
            primary_org_id=org.id, lis_operation_number="f-adresse"
        ).one()
        incident.address_street = "Manuell korrigiert"
        db.commit()

        result = import_einsaetze(db, parsed, org.id, user.id)
        db.refresh(incident)

        assert result.unchanged == 1
        assert incident.address_street == "Manuell korrigiert"
    finally:
        db.close()


def test_format_a_nutzt_einsatzbereit_auch_als_ende():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "format-a-ende")
        user = _user(db, org.id, "org_admin")
        db.commit()
        raw = _xlsx(FORMAT_A_HEADERS, [[
            "f-format-a", "Brand", "07.01.2026 17:50:00", "07.01.2026 18:50:00",
            None, None, None, "Hauptstraße", "1", "WOLFURT",
        ]])

        import_einsaetze(db, parse_einsatz_excel(raw), org.id, user.id)
        incident = db.query(Incident).filter_by(
            primary_org_id=org.id, lis_operation_number="f-format-a"
        ).one()

        expected = datetime(2026, 1, 7, 17, 50)
        assert incident.ready_again_at == expected
        assert incident.closed_at == expected
    finally:
        db.close()


def test_import_nutzt_org_zeitzone_statt_fixem_wien_fallback():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "timezone")
        org.timezone = "Europe/London"
        user = _user(db, org.id, "org_admin")
        db.commit()
        raw = _xlsx(FORMAT_B_HEADERS, [[
            "f-timezone", "07.07.2026 08:43:00", None, None, None, None,
            "07.07.2026 09:01:00", "T1", "Alarm", "Ablauf", None, 47.4, 9.7,
        ]])

        import_einsaetze(db, parse_einsatz_excel(raw), org.id, user.id)
        incident = db.query(Incident).filter_by(
            primary_org_id=org.id, lis_operation_number="f-timezone"
        ).one()

        assert incident.started_at == datetime(2026, 7, 7, 7, 43)
        assert incident.closed_at == datetime(2026, 7, 7, 8, 1)
    finally:
        db.close()


def test_reimport_ersetzt_adhoc_fahrzeug_in_allen_fk_tabellen():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db, "adhoc-fks")
        user = _user(db, org.id, "org_admin")
        alarm = AlarmType(org_id=org.id, code="T1", category="T", label="Technisch")
        adhoc = VehicleMaster(
            dept_id=org.id, code="RLF-A", name="RLF", active=True, is_adhoc=True
        )
        db.add_all([alarm, adhoc])
        db.flush()
        adhoc_id = adhoc.id

        incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="closed")
        db.add(incident)
        db.flush()
        column = IncidentColumn(
            incident_id=incident.id, code="active", title="Aktiv", column_kind="vehicles",
            is_fixed=True, display_order=0,
        )
        major = MajorIncident(org_id=org.id, name="Lage")
        purpose = Fahrtzweck(
            org_id=org.id, name="Einsatz", kategorie=FahrtKategorie.einsatz
        )
        db.add_all([column, major, purpose])
        db.flush()
        site = IncidentSite(
            major_incident_id=major.id, org_id=org.id, bezeichnung="Stelle"
        )
        db.add(site)
        db.flush()
        references = [
            IncidentVehicle(
                incident_id=incident.id, column_id=column.id, vehicle_master_id=adhoc.id,
                unit_status="Einsatzbereit",
            ),
            Teilnahme(
                org_id=org.id, bezug_typ="einsatz", bezug_id=incident.id,
                fahrzeug_id=adhoc.id,
            ),
            DeviceToken(
                label="Test", token_hash=uuid.uuid4().hex, user_id=user.id,
                vehicle_master_id=adhoc.id,
            ),
            AlarmDispatchVehicle(alarm_type_id=alarm.id, vehicle_master_id=adhoc.id),
            FoerderPumpenTyp(org_id=org.id, name="Pumpe", vehicle_id=adhoc.id),
            Fahrt(
                org_id=org.id, zeitpunkt=datetime(2026, 1, 1), fahrzeug_id=adhoc.id,
                maschinist_name="Test", zweck_id=purpose.id, fahrttyp=FahrtKategorie.einsatz,
            ),
            SiteResourceAssignment(
                incident_site_id=site.id, resource_type="vehicle", vehicle_id=adhoc.id,
            ),
            LageEinheit(lage_id=major.id, vehicle_id=adhoc.id, label="RLF"),
            VehiclePosition(
                incident_id=major.id, org_id=org.id, vehicle_id=adhoc.id,
                lat=47.0, lon=9.0, recorded_at=datetime(2026, 1, 1),
            ),
        ]
        db.add_all(references)
        db.commit()

        real = VehicleMaster(
            dept_id=org.id, code="RLF-A", name="RLF neu", active=True, is_adhoc=False
        )
        db.add(real)
        db.commit()
        real_id = real.id
        raw = _xlsx(FORMAT_A_HEADERS, [[
            "f-dedup", "Brand", "07.01.2026 17:50:00", "07.01.2026 18:50:00",
            "RLF-A", "RLF", None, "Hauptstraße", "1", "WOLFURT",
        ]])

        result = import_einsaetze(db, parse_einsatz_excel(raw), org.id, user.id)

        assert result.row_errors == 0
        assert db.get(VehicleMaster, adhoc_id) is None
        for reference in references:
            db.refresh(reference)
        assert references[0].vehicle_master_id == real_id
        assert references[1].fahrzeug_id == real_id
        assert references[2].vehicle_master_id == real_id
        assert references[3].vehicle_master_id == real_id
        assert references[4].vehicle_id == real_id
        assert references[5].fahrzeug_id == real_id
        assert references[6].vehicle_id == real_id
        assert references[7].vehicle_id == real_id
        assert references[8].vehicle_id == real_id
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
