"""Benutzerkennungen sind auch zwischen Organisationen eindeutig."""
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.db import get_db
from app.main import app
from app.models.master import FireDept
from app.models.user import User
from app.services import benutzer_service
from tests.conftest import TestingSession
from tests.test_benutzer_mitglied import _login, _user


@pytest.mark.parametrize("field", ["username", "email"])
def test_create_rejects_identifier_from_other_org_without_server_error(client, field):
    suffix = uuid4().hex
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=f"create-{suffix}", name="Benutzeranlage")
        other_org = FireDept(slug=f"existing-{suffix}", name="Andere Organisation")
        db.add_all([org, other_org])
        db.flush()
        actor = _user(db, f"create-admin-{suffix}", org.id, "admin")
        existing = _user(db, f"existing-user-{suffix}", other_org.id, "readonly")
        existing.email = f"existing-{suffix}@example.test"
        db.commit()
        org_id, actor_username = org.id, actor.username
        existing_id, existing_username, existing_email = existing.id, existing.username, existing.email

    assert _login(client, actor_username).status_code == 302

    # Die allgemeine Client-Fixture verwendet Systemkontext. Hier wie in einer
    # echten Anfrage eines Organisationsadmins nach Organisation filtern.
    def scoped_db():
        with TestingSession() as db:
            set_tenant_context(db, org_id)
            yield db

    original_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = scoped_db
    try:
        data = {
            "username": f"new-user-{suffix}",
            "display_name": "Neu",
            "email": f"new-{suffix}@example.test",
            "_csrf": client.cookies.get("ec_csrf"),
        }
        data[field] = existing_username if field == "username" else existing_email
        with TestingSession() as db:
            set_tenant_context(db, org_id)
            with pytest.raises(ValueError, match="bereits vergeben"):
                benutzer_service.create_user(
                    db, org_id=org_id, username=data["username"],
                    display_name=data["display_name"], email=data["email"],
                )
        response = client.post("/admin/benutzer/neu", data=data, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/admin/benutzer?error={field}_exists"
        page = client.get(response.headers["location"])
        assert page.status_code == 200
        message = "bereits vergeben" if field == "username" else "bereits einem anderen Benutzer"
        assert message in page.text
        assert existing_username not in page.text
    finally:
        app.dependency_overrides[get_db] = original_override

    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(User).filter(User.org_id == org_id).count() == 1
        existing = db.get(User, existing_id)
        assert existing.username == existing_username and existing.email == existing_email
