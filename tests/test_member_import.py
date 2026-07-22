"""Tests für den Mitglieder-Excel-Import (app/routers/ui_admin.py::import_members_excel),
insbesondere die neue syBOS-ID-Spalte (verknüpft DIBOS-Personenrückmeldungen mit einem
Mitglied, siehe app/services/dibos/dibos_enrich.py)."""
import io

import openpyxl
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.master import Member
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_admin(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Mitglieder-Import Test-Admin", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "admin").id))
        db.commit()
        return user.id
    finally:
        db.close()


def _xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_sets_sybos_id_on_new_member():
    _setup_admin("mitglieder_import_sybos_new")
    client = TestClient(app)
    _login(client, "mitglieder_import_sybos_new", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    xlsx = _xlsx_bytes(
        ["Vorname", "Zuname", "Telefon", "E-Mail", "syBOS-ID"],
        [["Jesse", "Rohner-ImportTest", "+43 664 1234567", "jesse@example.at", "31359"]],
    )
    r = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("adressliste.xlsx", xlsx,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:300]
    assert "imported=1" in r.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        member = db.query(Member).filter(
            Member.org_id == ORG_ID, Member.firstname == "Jesse", Member.lastname == "Rohner-ImportTest",
        ).first()
        assert member is not None
        assert member.sybos_id == "31359"
    finally:
        db.close()


def test_import_updates_sybos_id_on_existing_member():
    _setup_admin("mitglieder_import_sybos_update")
    client = TestClient(app)
    _login(client, "mitglieder_import_sybos_update", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    # Erster Import ohne syBOS-ID
    xlsx1 = _xlsx_bytes(
        ["Vorname", "Zuname"],
        [["Maria", "Update-ImportTest"]],
    )
    r1 = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("liste1.xlsx", xlsx1,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r1.status_code == 303
    assert "imported=1" in r1.headers["location"]

    # Zweiter Import (gleicher Name) MIT syBOS-ID -> Update, kein Duplikat
    xlsx2 = _xlsx_bytes(
        ["Vorname", "Zuname", "syBOS-ID"],
        [["Maria", "Update-ImportTest", "44642"]],
    )
    r2 = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("liste2.xlsx", xlsx2,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    assert "updated=1" in r2.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        members = db.query(Member).filter(
            Member.org_id == ORG_ID, Member.firstname == "Maria", Member.lastname == "Update-ImportTest",
        ).all()
        assert len(members) == 1  # kein Duplikat durch den zweiten Import
        assert members[0].sybos_id == "44642"
    finally:
        db.close()


def test_import_without_sybos_column_leaves_sybos_id_empty():
    """Bestehendes Verhalten bleibt erhalten: ohne syBOS-ID-Spalte in der Datei
    wird sybos_id einfach nicht gesetzt (kein Fehler, kein Pflichtfeld)."""
    _setup_admin("mitglieder_import_sybos_absent")
    client = TestClient(app)
    _login(client, "mitglieder_import_sybos_absent", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    xlsx = _xlsx_bytes(
        ["Vorname", "Zuname", "Telefon"],
        [["Klaus", "Ohnesybos-ImportTest", "+43 664 999"]],
    )
    r = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("liste.xlsx", xlsx,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "imported=1" in r.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        member = db.query(Member).filter(
            Member.org_id == ORG_ID, Member.firstname == "Klaus", Member.lastname == "Ohnesybos-ImportTest",
        ).first()
        assert member is not None
        assert member.sybos_id is None
    finally:
        db.close()


def test_import_matches_by_sybos_id_when_name_changes():
    """Namensänderung (z.B. Heirat) in einer neu eingespielten Liste: die syBOS-ID
    bleibt stabil und muss den bestehenden Datensatz finden statt einen zweiten
    Namensdatensatz anzulegen."""
    _setup_admin("mitglieder_import_namensaenderung")
    client = TestClient(app)
    _login(client, "mitglieder_import_namensaenderung", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    xlsx1 = _xlsx_bytes(
        ["Vorname", "Zuname", "syBOS-ID"],
        [["Anna", "Vorname-ImportTest", "77001"]],
    )
    r1 = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("liste1.xlsx", xlsx1,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf}, follow_redirects=False,
    )
    assert r1.status_code == 303
    assert "imported=1" in r1.headers["location"]

    # Zweiter Import: gleiche syBOS-ID, aber GEÄNDERTER Nachname
    xlsx2 = _xlsx_bytes(
        ["Vorname", "Zuname", "syBOS-ID"],
        [["Anna", "Nachname-ImportTest", "77001"]],
    )
    r2 = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("liste2.xlsx", xlsx2,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf}, follow_redirects=False,
    )
    assert r2.status_code == 303
    assert "updated=1" in r2.headers["location"]
    assert "imported=0" in r2.headers["location"]  # kein zweiter Datensatz

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        members = db.query(Member).filter(Member.org_id == ORG_ID, Member.sybos_id == "77001").all()
        assert len(members) == 1
        assert members[0].lastname == "Nachname-ImportTest"
        assert members[0].firstname == "Anna"
    finally:
        db.close()


def test_import_matches_by_sybos_id_when_email_changes():
    """E-Mail-Änderung in einer neu eingespielten Liste wird über die syBOS-ID
    demselben Mitglied zugeordnet (statt eines Namensabgleichs)."""
    _setup_admin("mitglieder_import_mailaenderung")
    client = TestClient(app)
    _login(client, "mitglieder_import_mailaenderung", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    xlsx1 = _xlsx_bytes(
        ["Vorname", "Zuname", "E-Mail", "syBOS-ID"],
        [["Bernd", "Mailtest-ImportTest", "alt@example.at", "77002"]],
    )
    r1 = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("liste1.xlsx", xlsx1,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf}, follow_redirects=False,
    )
    assert r1.status_code == 303

    xlsx2 = _xlsx_bytes(
        ["Vorname", "Zuname", "E-Mail", "syBOS-ID"],
        [["Bernd", "Mailtest-ImportTest", "neu@example.at", "77002"]],
    )
    r2 = client.post(
        "/admin/mitglieder/excel-import",
        files={"file": ("liste2.xlsx", xlsx2,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"_csrf": csrf}, follow_redirects=False,
    )
    assert r2.status_code == 303
    assert "updated=1" in r2.headers["location"]

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        members = db.query(Member).filter(Member.org_id == ORG_ID, Member.sybos_id == "77002").all()
        assert len(members) == 1
        assert members[0].email == "neu@example.at"
    finally:
        db.close()


def test_edit_member_route_saves_sybos_id():
    _setup_admin("mitglieder_edit_sybos")
    client = TestClient(app)
    _login(client, "mitglieder_edit_sybos", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        member = Member(org_id=ORG_ID, firstname="Edit", lastname="SybosTest")
        db.add(member)
        db.commit()
        member_id = member.id
    finally:
        db.close()

    r = client.post(
        f"/admin/mitglieder/{member_id}/edit",
        data={"_csrf": csrf, "lastname": "SybosTest", "firstname": "Edit", "sybos_id": "77003"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        refreshed = db.get(Member, member_id)
        assert refreshed.sybos_id == "77003"
    finally:
        db.close()


def test_members_list_page_shows_sybos_id_column():
    _setup_admin("mitglieder_list_sybos")
    client = TestClient(app)
    _login(client, "mitglieder_list_sybos", "Test1234!")

    db = SessionLocal()
    set_tenant_context(db, ORG_ID)
    try:
        db.add(Member(org_id=ORG_ID, firstname="Liste", lastname="SybosAnzeigeTest", sybos_id="77004"))
        db.commit()
    finally:
        db.close()

    r = client.get("/admin/mitglieder")
    assert r.status_code == 200
    assert "syBOS-ID" in r.text
    assert "77004" in r.text
