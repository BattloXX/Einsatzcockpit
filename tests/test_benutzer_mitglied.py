"""Regressionen fuer die optionale Benutzer-Mitglied-Verknuepfung."""
from __future__ import annotations

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, Member
from app.models.user import Role, User, UserRole


def _session():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _login(client, username: str):
    client.cookies.clear()
    client.get("/login")
    return client.post("/login", data={
        "username": username,
        "password": "Test1234!",
        "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)


def _role(db, code: str):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, label=code)
        db.add(role)
        db.flush()
    return role


def _user(db, username: str, org_id: int, *roles: str):
    user = User(
        username=username,
        display_name=username,
        password_hash=hash_password("Test1234!"),
        org_id=org_id,
        active=True,
    )
    db.add(user)
    db.flush()
    for code in roles:
        db.add(UserRole(user_id=user.id, role_id=_role(db, code).id))
    return user


def _member(db, org_id: int, lastname: str, firstname: str):
    member = Member(org_id=org_id, lastname=lastname, firstname=firstname, active=True)
    db.add(member)
    db.flush()
    return member


def _setup(db, prefix: str):
    org = db.query(FireDept).first()
    assert org is not None
    other_org = FireDept(slug=f"member-link-{prefix}", name=f"Andere Mitgliedsorg {prefix}")
    db.add(other_org)
    db.flush()
    actor = _user(db, f"member_link_admin_{prefix}", org.id, "admin")
    target = _user(db, f"member_link_target_{prefix}", org.id, "readonly")
    member = _member(db, org.id, "Muster", "Mia")
    other_member = _member(db, other_org.id, "Fremd", "Franz")
    db.commit()
    return actor.username, target.id, member.id, other_member.id, org.id


def _edit(client, user_id: int, member_id: str):
    return client.post(f"/admin/benutzer/{user_id}/edit", data={
        "display_name": "Zielbenutzer",
        "member_id": member_id,
        "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)


def test_member_can_be_linked_and_removed_via_edit(client):
    db = _session()
    try:
        actor, target_id, member_id, _, _ = _setup(db, "edit")
    finally:
        db.close()

    assert _login(client, actor).status_code == 302
    assert _edit(client, target_id, str(member_id)).status_code == 303
    assert _edit(client, target_id, "").status_code == 303

    db = _session()
    try:
        target = db.get(User, target_id)
        assert target is not None and target.member_id is None
    finally:
        db.close()


def test_member_from_other_org_and_duplicate_link_are_rejected(client):
    db = _session()
    try:
        actor, target_id, member_id, other_member_id, org_id = _setup(db, "rejected")
        second_target = _user(db, "member_link_second_rejected", org_id, "readonly")
        db.commit()
        second_target_id = second_target.id
    finally:
        db.close()

    _login(client, actor)
    assert _edit(client, target_id, str(other_member_id)).headers["location"] == "/admin/benutzer?error=member_invalid"
    assert _edit(client, target_id, str(member_id)).status_code == 303
    assert _edit(client, second_target_id, str(member_id)).headers["location"] == "/admin/benutzer?error=member_taken"


def test_create_user_with_member_and_member_delete_clears_link(client):
    db = _session()
    try:
        actor, _, member_id, _, org_id = _setup(db, "create")
    finally:
        db.close()

    _login(client, actor)
    response = client.post("/admin/benutzer/neu", data={
        "username": "member_link_created",
        "display_name": "Angelegt",
        "password": "Test1234!",
        "member_id": str(member_id),
        "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code == 200

    db = _session()
    try:
        created = db.query(User).filter(User.username == "member_link_created").first()
        assert created is not None and created.member_id == member_id and created.org_id == org_id
    finally:
        db.close()

    response = client.post("/admin/mitglieder/bulk-delete", data={
        "member_ids": str(member_id),
        "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code == 303

    db = _session()
    try:
        created = db.query(User).filter(User.username == "member_link_created").first()
        assert created is not None and created.member_id is None
    finally:
        db.close()


def test_user_list_shows_member_name(client):
    db = _session()
    try:
        actor, target_id, member_id, _, _ = _setup(db, "list")
        target = db.get(User, target_id)
        assert target is not None
        target.member_id = member_id
        db.commit()
    finally:
        db.close()

    _login(client, actor)
    response = client.get("/admin/benutzer")
    assert response.status_code == 200
    assert "Muster Mia" in response.text
