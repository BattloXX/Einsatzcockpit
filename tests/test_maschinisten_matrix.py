from datetime import date, datetime
from io import BytesIO

import openpyxl

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, FahrtStatus, Fahrtzweck
from app.models.master import FireDept, Member, MemberQualification, Qualification, VehicleMaster
from app.models.user import Role, User, UserRole
from app.services.excel_export_service import exportiere_maschinisten_matrix
from app.services.maschinisten_matrix_service import berechne_maschinisten_matrix


def _login(client, username: str) -> None:
    client.cookies.clear()
    client.get("/login")
    response = client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code in (302, 303)


def test_matrix_split_grenzen_qualifikationen_filter_xlsx_und_cross_org():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, 1)
        assert org
        other = FireDept(slug="matrix-other", name="Matrix Fremd", color="#123456")
        q1 = db.query(Qualification).filter_by(code="M1").one()
        q2 = db.query(Qualification).filter_by(code="M2").one()
        active = Member(org_id=org.id, lastname="Alpha", firstname="Anna", active=True)
        expired = Member(org_id=org.id, lastname="Beta", firstname="Berta", active=True)
        unqualified = Member(org_id=org.id, lastname="Zulu", firstname="Zeno", active=True)
        vehicle = VehicleMaster(dept_id=org.id, code="STEIGER-MX", name="Steiger", zweiter_maschinist_pflicht=True)
        purpose = Fahrtzweck(org_id=org.id, name="Matrix", kategorie=FahrtKategorie.uebung)
        db.add_all([other, active, expired, unqualified, vehicle, purpose])
        db.flush()
        foreign_member = Member(org_id=other.id, lastname="Fremd", firstname="Fritz", active=True)
        foreign_vehicle = VehicleMaster(dept_id=other.id, code="FREMD-MX", name="Fremd")
        db.add_all([foreign_member, foreign_vehicle])
        db.flush()
        db.add_all([
            MemberQualification(member_id=active.id, qualification_id=q1.id),
            MemberQualification(member_id=expired.id, qualification_id=q2.id, valid_until=date(2020, 1, 1)),
            Fahrt(org_id=org.id, zeitpunkt=datetime(2026, 12, 31, 22, 30), fahrzeug_id=vehicle.id,
                  maschinist_member_id=unqualified.id, maschinist_name="Zeno Zulu",
                  maschinist2_member_id=active.id, maschinist2_name="Anna Alpha",
                  zweck_id=purpose.id, fahrttyp=FahrtKategorie.uebung),
            Fahrt(org_id=org.id, zeitpunkt=datetime(2026, 6, 1), fahrzeug_id=vehicle.id,
                  maschinist_name="Storniert", zweck_id=purpose.id, fahrttyp=FahrtKategorie.einsatz,
                  status=FahrtStatus.storniert),
            Fahrt(org_id=org.id, zeitpunkt=datetime(2026, 6, 2), fahrzeug_id=vehicle.id,
                  maschinist_name="Irrelevant", zweck_id=purpose.id, fahrttyp=FahrtKategorie.einsatz,
                  nicht_statistikrelevant=True),
        ])
        db.commit()
        matrix = berechne_maschinisten_matrix(db, org.id, 2026)
        assert [c["rolle"] for c in matrix["spalten"] if c["gruppe"] == "STEIGER-MX"] == ["ma", "korb"]
        by_name = {r["name"]: r for r in matrix["zeilen"]}
        assert by_name["Alpha Anna"]["stufe"] == 1
        assert by_name["Zulu Zeno"]["stufe"] is None
        assert "Beta Berta" not in by_name
        assert "Fremd Fritz" not in by_name
        assert "Storniert" not in by_name and "Irrelevant" not in by_name
        assert by_name["Alpha Anna"]["zellen"][f"{vehicle.id}:korb"]["uebung"] == 1
        assert by_name["Zulu Zeno"]["zellen"][f"{vehicle.id}:ma"]["uebung"] == 1
        assert berechne_maschinisten_matrix(db, org.id, 2027)["zeilen"][0]["name"] == "Alpha Anna"
        wb = openpyxl.load_workbook(BytesIO(exportiere_maschinisten_matrix(matrix, org)))
        ws = wb.active
        assert ws.freeze_panes == "C3"
        assert ws.page_setup.orientation == "landscape"
        assert str(ws.page_setup.paperSize) == str(ws.PAPERSIZE_A3)
    finally:
        db.rollback()
        db.close()


def test_matrix_druckroute_liefert_pdf(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == "org_admin").one()
        user = User(username="matrix_pdf_admin", password_hash=hash_password("Test1234!"),
                    display_name="Matrix PDF", org_id=1, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()
    _login(client, "matrix_pdf_admin")
    response = client.get(
        "/druck/dokument.pdf?document_type=maschinisten_matrix&artifact_ref=jahr%3D2026"
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_stats_ohne_org_bleibt_leer_und_xlsx_liefert_404(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == "system_admin").one()
        user = User(username="matrix_no_org", password_hash=hash_password("Test1234!"),
                    display_name="Matrix ohne Org", org_id=None, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()
    _login(client, "matrix_no_org")
    page = client.get("/statistik/fahrtenbuch")
    assert page.status_code == 200
    assert "Maschinisten-Matrix" in page.text
    assert client.get("/statistik/fahrtenbuch/maschinisten.xlsx?jahr=2026").status_code == 404


def test_matrix_jahresformular_behaelt_filter_und_jahresliste_nutzt_org_zeitzone(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == "org_admin").one()
        user = User(username="matrix_year_filters", password_hash=hash_password("Test1234!"),
                    display_name="Matrix Filter", org_id=1, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        vehicle = VehicleMaster(dept_id=1, code="TZ-YEAR-MX", name="Zeitzone")
        purpose = Fahrtzweck(org_id=1, name="Zeitzonenjahr", kategorie=FahrtKategorie.uebung)
        db.add_all([vehicle, purpose])
        db.flush()
        db.add(Fahrt(
            org_id=1, zeitpunkt=datetime(2026, 12, 31, 23, 30), fahrzeug_id=vehicle.id,
            maschinist_name="Jahreswechsel", zweck_id=purpose.id, fahrttyp=FahrtKategorie.uebung,
        ))
        db.commit()
    finally:
        db.close()
    _login(client, "matrix_year_filters")
    response = client.get(
        "/statistik/fahrtenbuch?von=2026-02-01&bis=2026-03-01&fahrzeug_id=7"
        "&fahrttyp=uebung&zweck_id=9&gruppierung=maschinist&matrix_jahr=2027"
    )
    assert response.status_code == 200
    for expected in (
        'name="von" value="2026-02-01"', 'name="bis" value="2026-03-01"',
        'name="fahrzeug_id" value="7"', 'name="fahrttyp" value="uebung"',
        'name="zweck_id" value="9"', 'name="gruppierung" value="maschinist"',
    ):
        assert expected in response.text
    assert '<option value="2027" selected>2027</option>' in response.text
