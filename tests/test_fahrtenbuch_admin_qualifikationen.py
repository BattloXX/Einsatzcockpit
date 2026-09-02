from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, Member, MemberQualification, Qualification
from app.models.user import Role, User, UserRole


def _login(client, username: str) -> str:
    client.cookies.clear()
    client.get("/login")
    response = client.post("/login", data={
        "username": username,
        "password": "Test1234!",
        "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code in (302, 303)
    return client.cookies.get("ec_csrf")


def _fahrtenbuch_admin(db, username: str, org_id: int) -> User:
    role = db.query(Role).filter(Role.code == "fahrtenbuch_admin").one()
    user = User(
        username=username, password_hash=hash_password("Test1234!"),
        display_name="Fahrtenbuch Admin", org_id=org_id, active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def test_fahrtenbuch_admin_darf_quali_crud_und_eigene_zuweisung_aber_keine_member_mutation(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = _fahrtenbuch_admin(db, "fb_quali_admin", 1)
        member = Member(org_id=1, lastname="Quali", firstname="Eigen", active=True)
        db.add(member)
        db.commit()
        member_id = member.id
        assert user.id
    finally:
        db.close()

    csrf = _login(client, "fb_quali_admin")
    members_page = client.get("/admin/mitglieder")
    assert members_page.status_code == 200
    assert "Qualifikationen" in members_page.text
    assert "newMemberModal" not in members_page.text
    assert "excelImportModal" not in members_page.text
    assert client.get("/admin/qualifikationen").status_code == 200

    created = client.post("/admin/qualifikationen/neu", data={
        "code": "FBQ-CRUD", "label": "Fahrtenbuch Qualifikation",
        "maschinist_stufe": "3", "_csrf": csrf,
    }, follow_redirects=False)
    assert created.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        qualification = db.query(Qualification).filter_by(code="FBQ-CRUD").one()
        qualification_id = qualification.id
    finally:
        db.close()

    edited = client.post(f"/admin/qualifikationen/{qualification_id}/edit", data={
        "code": "FBQ-CRUD", "label": "Geändert", "maschinist_stufe": "2", "_csrf": csrf,
    }, follow_redirects=False)
    assert edited.status_code == 303
    assigned = client.post(f"/admin/mitglieder/{member_id}/quali", data={
        "qualification_codes": "FBQ-CRUD", "_csrf": csrf,
    }, follow_redirects=False)
    assert assigned.status_code == 303

    denied_create = client.post("/admin/mitglieder/neu", data={
        "lastname": "Verboten", "firstname": "Neu", "_csrf": csrf,
    }, follow_redirects=False)
    assert denied_create.status_code == 403
    denied_edit = client.post(f"/admin/mitglieder/{member_id}/edit", data={
        "lastname": "Verboten", "firstname": "Edit", "_csrf": csrf,
    }, follow_redirects=False)
    assert denied_edit.status_code == 403
    assert client.get("/admin/mitglieder/excel-import", follow_redirects=False).status_code == 403

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(MemberQualification).filter_by(
            member_id=member_id, qualification_id=qualification_id,
        ).count() == 1
        assignment = db.query(MemberQualification).filter_by(
            member_id=member_id, qualification_id=qualification_id,
        ).one()
        db.delete(assignment)
        db.commit()
    finally:
        db.close()
    deleted = client.post(f"/admin/qualifikationen/{qualification_id}/loeschen", data={
        "_csrf": csrf,
    }, follow_redirects=False)
    assert deleted.status_code == 303


def test_fahrtenbuch_admin_darf_fremdes_mitglied_weder_qualifizieren_noch_bearbeiten(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        _fahrtenbuch_admin(db, "fb_quali_cross", 1)
        other = FireDept(slug="fb-quali-other", name="FB Quali Fremd", color="#123456")
        db.add(other)
        db.flush()
        member = Member(org_id=other.id, lastname="Fremd", firstname="Mitglied", active=True)
        db.add(member)
        db.commit()
        member_id = member.id
    finally:
        db.close()
    csrf = _login(client, "fb_quali_cross")
    assert client.post(f"/admin/mitglieder/{member_id}/quali", data={
        "qualification_codes": "M1", "_csrf": csrf,
    }, follow_redirects=False).status_code == 403
    assert client.post(f"/admin/mitglieder/{member_id}/edit", data={
        "lastname": "Fremd", "firstname": "Geändert", "_csrf": csrf,
    }, follow_redirects=False).status_code == 403
