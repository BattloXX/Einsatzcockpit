"""Datenmodell-Regressionen fuer Einsatzinfo-Mail."""
from __future__ import annotations

from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.models.einsatzinfo_mail import (
    MailEinsatzinfoLog,
    MailEinsatzinfoLogRecipient,
    MailEinsatzinfoRecipient,
)
from app.models.master import AlarmType, FireDept, Member, OrgSettings
from app.models.sms import SmsGroup
from app.services.exercise_guard import darf_extern
from tests.conftest import TestingSession


def _org(db) -> FireDept:
    suffix = uuid4().hex[:10]
    org = FireDept(slug=f"mail-modell-{suffix}", name="Mail-Modell")
    db.add(org)
    db.flush()
    return org


def test_org_settings_und_stichwort_vorlagen_roundtrip(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        settings = OrgSettings(
            org_id=org.id,
            einsatzinfo_mail_enabled=True,
            einsatzinfo_mail_send_exercise=True,
            einsatzinfo_mail_subject_template="Einsatz {stichwort}",
            einsatzinfo_mail_template="Adresse: {adresse}",
        )
        alarm_type = AlarmType(
            org_id=org.id,
            code="M1",
            einsatzinfo_mail_subject_template="Override {stichwort}",
            einsatzinfo_mail_template="Override {meldung}",
        )
        db.add_all([settings, alarm_type])
        db.flush()
        db.expire_all()

        assert db.get(OrgSettings, settings.id).einsatzinfo_mail_enabled is True
        assert db.get(OrgSettings, settings.id).einsatzinfo_mail_subject_template == "Einsatz {stichwort}"
        assert db.get(AlarmType, alarm_type.id).einsatzinfo_mail_template == "Override {meldung}"
    finally:
        db.rollback()
        db.close()


def test_empfaenger_und_log_empfaenger_werden_kaskadiert_geloescht(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        member = Member(org_id=org.id, firstname="Mail", lastname="Mitglied")
        group = SmsGroup(org_id=org.id, name="Mail-Gruppe")
        db.add_all([member, group])
        db.flush()
        recipients = [
            MailEinsatzinfoRecipient(org_id=org.id, group_id=group.id),
            MailEinsatzinfoRecipient(org_id=org.id, member_id=member.id),
            MailEinsatzinfoRecipient(org_id=org.id, email="extern@example.test"),
        ]
        log = MailEinsatzinfoLog(org_id=org.id, subject="Einsatz", body="Details")
        log.recipients = [
            MailEinsatzinfoLogRecipient(email="eins@example.test"),
            MailEinsatzinfoLogRecipient(member_id=member.id, email="zwei@example.test"),
        ]
        db.add_all([*recipients, log])
        db.flush()
        log_id = log.id

        assert recipients[0].is_global is True
        assert db.query(MailEinsatzinfoLogRecipient).filter_by(log_id=log_id).count() == 2
        db.delete(log)
        db.flush()
        assert db.query(MailEinsatzinfoLogRecipient).filter_by(log_id=log_id).count() == 0
    finally:
        db.rollback()
        db.close()


def test_exercise_guard_mail_folgt_org_schalter(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        org = _org(db)
        settings = OrgSettings(org_id=org.id, einsatzinfo_mail_send_exercise=False)
        db.add(settings)
        db.flush()

        assert darf_extern("mail", is_exercise=True, org_id=org.id, db=db, planspiel_run_id=None) is False
        settings.einsatzinfo_mail_send_exercise = True
        assert darf_extern("mail", is_exercise=True, org_id=org.id, db=db, planspiel_run_id=None) is True
        assert darf_extern("mail", is_exercise=False, org_id=org.id, db=db, planspiel_run_id=None) is True
    finally:
        db.rollback()
        db.close()
