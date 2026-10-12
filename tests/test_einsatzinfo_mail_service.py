"""Tests fuer den automatischen Einsatzinfo-Mailversand."""
from __future__ import annotations

from email.utils import getaddresses

import pytest


def _mail_settings(db):
    """Liefert die Mail-Einstellungen der Test-Organisation und legt sie bei Bedarf an."""
    from app.models.master import OrgSettings

    settings = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    if settings is None:
        settings = OrgSettings(org_id=1)
        db.add(settings)
        db.flush()
    return settings


def _new_org(db, slug):
    """Legt eine isolierte Test-Organisation mit Mail-Einstellungen an."""
    from app.models.master import FireDept, OrgSettings

    org = FireDept(slug=slug, name=slug, is_home_org=False)
    db.add(org)
    db.flush()
    settings = OrgSettings(org_id=org.id)
    db.add(settings)
    db.flush()
    return org, settings


def test_collect_recipients_expands_groups_and_deduplicates(setup_db):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.einsatzinfo_mail import MailEinsatzinfoRecipient
    from app.models.master import AlarmType, Member
    from app.models.sms import SmsGroup, SmsGroupMember
    from app.services.einsatzinfo_mail_service import collect_einsatzinfo_mail_recipients

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org, _settings = _new_org(db, "mail-collect")
        alarm = AlarmType(org_id=org.id, code="MAILCOLLECT", label="Mail Collect")
        active = Member(org_id=org.id, firstname="Aktiv", lastname="Test", email="Duplicate@Example.test")
        inactive = Member(org_id=org.id, firstname="Inaktiv", lastname="Test", email="inactive@example.test", active=False)
        no_mail = Member(org_id=org.id, firstname="Leer", lastname="Test", email=None)
        group = SmsGroup(org_id=org.id, name="Mailtestgruppe")
        db.add_all([alarm, active, inactive, no_mail, group])
        db.flush()
        db.add_all([
            SmsGroupMember(sms_group_id=group.id, member_id=active.id),
            SmsGroupMember(sms_group_id=group.id, member_id=inactive.id),
            SmsGroupMember(sms_group_id=group.id, member_id=no_mail.id),
            MailEinsatzinfoRecipient(org_id=org.id, group_id=group.id),
            MailEinsatzinfoRecipient(org_id=org.id, member_id=active.id),
            MailEinsatzinfoRecipient(org_id=org.id, email="duplicate@example.test"),
            MailEinsatzinfoRecipient(org_id=org.id, email="extern@example.test"),
            MailEinsatzinfoRecipient(org_id=org.id, alarm_type_id=alarm.id, email="nur-code@example.test"),
        ])
        db.commit()

        result = collect_einsatzinfo_mail_recipients(db, org.id, alarm.id)
        assert set(result) == {"duplicate@example.test", "extern@example.test", "nur-code@example.test"}
        assert result["duplicate@example.test"][0].id == active.id
        assert result["extern@example.test"] == (None, None)
        assert "inactive@example.test" not in result

        other = collect_einsatzinfo_mail_recipients(db, org.id, None)
        assert "nur-code@example.test" not in other
    finally:
        db.close()


def test_template_priority_exercise_prefix_and_empty_labels():
    from app.services.einsatzinfo_mail_service import _render_mail_template
    from app.services.sms_dispatch_service import render_template

    context = {"stichwort": "B2", "adresse": "Hauptstrasse 1", "leitstellennummer": ""}
    subject = render_template("Stichwort {stichwort}", context)
    assert subject == "Stichwort B2"
    body = _render_mail_template("Org\nLeitstellennummer: {leitstellennummer}\n\n\nKarte: {kartenlink}", context)
    assert body == "Org"
    assert "[ÜBUNG] " + subject == "[ÜBUNG] Stichwort B2"


@pytest.mark.asyncio
async def test_dispatch_prefers_alarm_template_and_prefixes_exercise(setup_db, monkeypatch):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.einsatzinfo_mail import MailEinsatzinfoRecipient
    from app.models.incident import Incident
    from app.models.master import AlarmType
    from app.services import einsatzinfo_mail_service as service

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org, settings = _new_org(db, "mail-template-priority")
        settings.einsatzinfo_mail_enabled = True
        settings.einsatzinfo_mail_send_exercise = True
        settings.einsatzinfo_mail_subject_template = "Org {stichwort}"
        settings.einsatzinfo_mail_template = "Org\nKarte: {kartenlink}"
        alarm = AlarmType(
            org_id=org.id, code="MAILTPL", label="Mail Vorlage",
            einsatzinfo_mail_subject_template="Alarm {stichwort}",
            einsatzinfo_mail_template="Alarm\nLeitstellennummer: {leitstellennummer}",
        )
        db.add(alarm)
        db.flush()
        db.add(MailEinsatzinfoRecipient(org_id=org.id, email="template@example.test"))
        incident = Incident(primary_org_id=org.id, alarm_type_code=alarm.code, is_exercise=True)
        db.add(incident)
        db.commit()
        incident_id = incident.id
        org_id = org.id
    finally:
        db.close()

    sent = []

    async def fake_deliver(_db, _org_id, message):
        sent.append(message)

    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_args: True)
    monkeypatch.setattr("app.services.mail_service.deliver", fake_deliver)
    await service.dispatch_einsatzinfo_mail(org_id, incident_id)
    assert sent[0]["Subject"] == "[ÜBUNG] Alarm MAILTPL"
    assert sent[0].get_body(preferencelist=("plain",)).get_content().strip() == "Alarm"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "exercise", "available", "expected"),
    [
        (False, False, True, "deaktiviert"),
        (True, True, True, "Uebung unterdrueckt"),
        (True, False, False, "kein Mailversand konfiguriert"),
    ],
)
async def test_dispatch_gates(setup_db, monkeypatch, enabled, exercise, available, expected):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.models.master import OrgSettings
    from app.services import einsatzinfo_mail_service as service

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org, settings = _new_org(db, f"mail-gate-{enabled}-{exercise}-{available}")
        settings.einsatzinfo_mail_enabled = enabled
        settings.einsatzinfo_mail_send_exercise = False
        incident = Incident(primary_org_id=org.id, alarm_type_code="MAILGATE", is_exercise=exercise)
        db.add(incident)
        db.commit()
        incident_id = incident.id
        org_id = org.id
    finally:
        db.close()

    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_args: available)
    result = await service.dispatch_einsatzinfo_mail(org_id, incident_id)
    assert result.reason == expected


@pytest.mark.asyncio
async def test_dispatch_without_recipients(setup_db, monkeypatch):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.incident import Incident
    from app.models.master import OrgSettings
    from app.services import einsatzinfo_mail_service as service

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org, settings = _new_org(db, "mail-no-recipients")
        settings.einsatzinfo_mail_enabled = True
        incident = Incident(primary_org_id=org.id, alarm_type_code="MAILNONE")
        db.add(incident)
        db.commit()
        incident_id = incident.id
        org_id = org.id
    finally:
        db.close()
    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_args: True)
    assert (await service.dispatch_einsatzinfo_mail(org_id, incident_id)).reason == "keine Empfaenger konfiguriert"


@pytest.mark.asyncio
async def test_chunking_and_resume(setup_db, monkeypatch):
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.einsatzinfo_mail import MailEinsatzinfoLog, MailEinsatzinfoRecipient
    from app.models.incident import Incident
    from app.models.master import OrgSettings
    from app.services import einsatzinfo_mail_service as service

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org, settings = _new_org(db, "mail-chunk")
        settings.einsatzinfo_mail_enabled = True
        incident = Incident(primary_org_id=org.id, alarm_type_code="MAILCHUNK")
        db.add(incident)
        db.flush()
        db.add_all(MailEinsatzinfoRecipient(org_id=org.id, email=f"chunk-{number}@example.test") for number in range(120))
        db.commit()
        incident_id = incident.id
        org_id = org.id
    finally:
        db.close()

    calls = []
    attempts = {"count": 0}

    async def failing_deliver(_db, _org_id, message):
        attempts["count"] += 1
        calls.append(message)
        if attempts["count"] == 2:
            raise RuntimeError("zweiter Block fehlgeschlagen")

    monkeypatch.setattr("app.services.mail_service.mail_available", lambda *_args: True)
    monkeypatch.setattr(
        "app.services.mail_service.versandweg",
        lambda *_args: {"absender": "Leitstelle <sender@example.test>"},
    )
    monkeypatch.setattr("app.services.mail_service.deliver", failing_deliver)
    with pytest.raises(RuntimeError, match="zweiter Block"):
        await service.dispatch_einsatzinfo_mail(org_id, incident_id)
    assert len(calls) == 2
    assert all(message["To"] == "sender@example.test" for message in calls)
    assert all(len(getaddresses([message.get("Bcc", "")])) <= 50 for message in calls)

    async def successful_deliver(_db, _org_id, message):
        calls.append(message)

    monkeypatch.setattr("app.services.mail_service.deliver", successful_deliver)
    result = await service.dispatch_einsatzinfo_mail(org_id, incident_id)
    assert result.sent and result.recipient_count == 120
    assert len(calls) == 4  # Der erste erfolgreiche Block wird nicht wiederholt.
    assert (await service.dispatch_einsatzinfo_mail(org_id, incident_id)).handed_off

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        log = db.query(MailEinsatzinfoLog).filter(MailEinsatzinfoLog.incident_id == incident_id).one()
        assert log.success_count == 120
        assert log.completed_at is not None
    finally:
        db.close()
