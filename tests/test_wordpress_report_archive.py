from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.user import AuditLog, Role, User, UserRole
from app.services.wordpress_report_service import WordPressReportResult
from tests.conftest import TestingSession

ORG_ID = 1


def _make_user_and_incident(username: str, role_code: str) -> tuple[str, int]:
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        role = db.query(Role).filter(Role.code == role_code).first()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            active=True,
            org_id=ORG_ID,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        incident = Incident(
            primary_org_id=ORG_ID,
            alarm_type_code="T1",
            status="closed",
            reason="Testeinsatz",
        )
        db.add(incident)
        db.commit()
        return username, incident.id
    finally:
        db.close()


def _login(client, username: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        "/login",
        data={"username": username, "password": "Test1234!", "_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def _post(client, incident_id: int):
    return client.post(
        f"/archiv/{incident_id}/webseiten-bericht",
        data={"_csrf": client.cookies.get("ec_csrf")},
    )


def test_wordpress_report_route_rejects_readonly(client, setup_db):
    username, incident_id = _make_user_and_incident("wp_archive_readonly", "readonly")
    _login(client, username)
    response = _post(client, incident_id)
    assert response.status_code == 403


def test_wordpress_report_route_recorder_double_submit_is_idempotent(
    client, setup_db, monkeypatch
):
    username, incident_id = _make_user_and_incident("wp_archive_recorder", "recorder")
    calls = []

    async def _fake_post(db, incident):
        calls.append(incident.id)
        if incident.wp_report_post_id is not None:
            return WordPressReportResult(
                True, incident.wp_report_post_id, incident.wp_report_edit_url, None, True
            )
        incident.wp_report_post_id = 812
        incident.wp_report_edit_url = "https://website.test/wp-admin/post.php?post=812&action=edit"
        db.commit()
        return WordPressReportResult(
            True, incident.wp_report_post_id, incident.wp_report_edit_url, None, False
        )

    monkeypatch.setattr(
        "app.services.wordpress_report_service.post_incident_report", _fake_post
    )
    _login(client, username)

    first = _post(client, incident_id)
    second = _post(client, incident_id)

    assert first.status_code == 200
    assert "Entwurf wurde auf der Website angelegt" in first.text
    assert second.status_code == 200
    assert "Bereits erstellt" in second.text
    assert calls == [incident_id, incident_id]

    db = TestingSession()
    set_tenant_context(db, None)
    try:
        audit_count = db.query(AuditLog).filter(
            AuditLog.action == "wordpress_report_created",
            AuditLog.incident_id == incident_id,
        ).count()
        assert audit_count == 1
    finally:
        db.close()
