"""Admin-Route fuer SMS-Empfaengerdetails und Tenant-Isolation."""
import uuid

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.sms import SmsLog
from app.models.user import Role, User, UserRole


def _login(client, username: str):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_sms_log_details_returns_404_for_other_org(client):
    marker = uuid.uuid4().hex
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_a = FireDept(slug=f"sms-detail-a-{marker}", name="SMS Detail A")
        org_b = FireDept(slug=f"sms-detail-b-{marker}", name="SMS Detail B")
        db.add_all([org_a, org_b])
        db.flush()
        user = User(
            username=f"sms_detail_{marker}", password_hash=hash_password("Test1234!"),
            display_name="SMS Detail Admin", org_id=org_a.id, active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "admin").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        foreign_log = SmsLog(
            org_id=org_b.id, source="manual", text=f"FREMD-{marker}",
            recipient_count=0, success_count=0,
        )
        own_log = SmsLog(
            org_id=org_a.id, source="manual", text="Eigen", recipient_count=0, success_count=0,
        )
        db.add_all([foreign_log, own_log])
        db.commit()
        username, foreign_id, own_id = user.username, foreign_log.id, own_log.id
    finally:
        db.close()

    _login(client, username)
    own_response = client.get(f"/admin/sms-senden/{own_id}/details")
    assert own_response.status_code == 200
    assert "Keine Detaildaten vorhanden" in own_response.text

    cross_org_response = client.get(f"/admin/sms-senden/{foreign_id}/details")
    assert cross_org_response.status_code == 404
    assert f"FREMD-{marker}" not in cross_org_response.text
