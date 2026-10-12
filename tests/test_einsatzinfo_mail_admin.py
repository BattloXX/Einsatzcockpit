"""Admin-Oberflaeche fuer Einsatzinfo-Mails."""
from __future__ import annotations

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.einsatzinfo_mail import MailEinsatzinfoRecipient
from app.models.master import AlarmType, FireDept, Member, OrgSettings
from app.models.sms import SmsGroup
from app.models.user import Role, User, UserRole


def _login(client, username: str) -> str:
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf})
    return client.cookies.get("ec_csrf")


def _setup(suffix: str, role_code: str = "admin") -> tuple[int, int, int, str]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"mail-ui-{suffix}", name=f"Mail UI {suffix}", color="#123456", bos="FW")
        db.add(org); db.flush()
        user = User(username=f"mail-ui-{suffix}", password_hash=hash_password("Test1234!"), display_name="Admin", email=f"{suffix}@example.test", org_id=org.id, active=True)
        db.add(user); db.flush()
        role = db.query(Role).filter(Role.code == role_code).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        member = Member(org_id=org.id, firstname="Mail", lastname=suffix, email=f"member-{suffix}@example.test", active=True)
        db.add(OrgSettings(org_id=org.id))
        group = SmsGroup(org_id=org.id, name=f"Gruppe {suffix}")
        alarm = AlarmType(org_id=org.id, code="B2", category="B", label="Brand")
        db.add_all([member, group, alarm]); db.commit()
        return org.id, member.id, group.id, user.username
    finally:
        db.close()


def test_page_and_role_guard(client, setup_db):
    _, _, _, username = _setup("page")
    _login(client, username)
    assert client.get("/admin/einsatzinfo-mail").status_code == 200
    assert "Einsatzinfo-Mail" in client.get("/admin/einsatzinfo-mail").text
    _, _, _, non_admin = _setup("member", "readonly")
    client.cookies.clear()
    _login(client, non_admin)
    assert client.get("/admin/einsatzinfo-mail", follow_redirects=False).status_code in {302, 303, 403}


def test_settings_basis_invalid_and_preview(client, setup_db):
    org_id, member_id, group_id, username = _setup("basis")
    csrf = _login(client, username)
    response = client.post("/admin/einsatzinfo-mail/einstellungen", data={"_csrf": csrf, "enabled": "true", "send_exercise": "true", "subject_template": "Alarm {stichwort}", "template": "Text {adresse}"})
    assert response.status_code == 200
    response = client.post("/admin/einsatzinfo-mail/basis-verteiler", data={"_csrf": csrf, "group_id": str(group_id), "member_id": str(member_id), "externe_emails": "Extern@example.test; extern@example.test\nzwei@example.test"})
    assert response.status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).one()
        assert settings.einsatzinfo_mail_enabled and settings.einsatzinfo_mail_subject_template == "Alarm {stichwort}"
        rows = db.query(MailEinsatzinfoRecipient).filter(MailEinsatzinfoRecipient.org_id == org_id).all()
        assert len(rows) == 4
    finally:
        db.close()
    response = client.post("/admin/einsatzinfo-mail/basis-verteiler", data={"_csrf": csrf, "externe_emails": "ungültig"}, follow_redirects=False)
    assert "fehler=" in response.headers["location"]
    assert client.post("/admin/einsatzinfo-mail/vorschau", data={"_csrf": csrf, "subject_template": "Alarm {stichwort}", "template": "Text {stichwort}"}).text.find("B2") >= 0


def test_stichwort_foreign_ids_and_testmail(client, setup_db, monkeypatch):
    org_id, member_id, group_id, username = _setup("stichwort")
    other_org, other_member, other_group, _ = _setup("foreign")
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        alarm = db.query(AlarmType).filter(AlarmType.org_id == org_id).one()
        foreign_alarm = db.query(AlarmType).filter(AlarmType.org_id == other_org).one()
        alarm_id, foreign_alarm_id = alarm.id, foreign_alarm.id
    finally:
        db.close()
    csrf = _login(client, username)
    response = client.post(f"/admin/einsatzinfo-mail/stichwort/{alarm_id}", data={"_csrf": csrf, "group_id": [str(group_id), str(other_group)], "member_id": [str(member_id), str(other_member)], "subject_template_override": "S {stichwort}", "template_override": "T"})
    assert response.status_code == 200
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        alarm = db.get(AlarmType, alarm_id)
        rows = db.query(MailEinsatzinfoRecipient).filter(
            MailEinsatzinfoRecipient.org_id == org_id,
            MailEinsatzinfoRecipient.alarm_type_id == alarm_id,
        ).all()
        assert alarm.einsatzinfo_mail_subject_template == "S {stichwort}"
        assert {row.group_id for row in rows if row.group_id} == {group_id}
        assert {row.member_id for row in rows if row.member_id} == {member_id}
    finally:
        db.close()
    assert client.post(f"/admin/einsatzinfo-mail/stichwort/{foreign_alarm_id}", data={"_csrf": csrf}, follow_redirects=False).status_code == 404
    sent = []
    async def deliver(_db, _org_id, message, _smtp_cfg=None): sent.append(message)
    monkeypatch.setattr("app.services.mail_service.deliver", deliver)
    assert client.post("/admin/einsatzinfo-mail/testmail", data={"_csrf": csrf}).status_code == 200
    assert sent and sent[0]["To"] == "stichwort@example.test" and "[TEST]" in sent[0]["Subject"]
