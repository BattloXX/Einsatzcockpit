import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.mailing import MailingConfig, MailingQueueItem, MailingTemplate
from app.models.master import MemberTag, Qualification
from app.routers.ui_mailing import campaign_test_send, template_test_send
from app.services.mailing_service import summarize_filter_json
from tests.mailing_phase2_helpers import campaign, db_session


def test_summarize_filter_json_resolves_qualification_and_tag_names():
    db = db_session()
    tag = MemberTag(org_id=1, name="Nord")
    qualification = Qualification(code="AGT-UI", label="Atemschutzgeräteträger")
    db.add_all([tag, qualification])
    db.flush()
    result = summarize_filter_json(
        json.dumps({"active": True, "qualification_codes": [qualification.code], "tag_ids": [tag.id]}),
        db,
        1,
    )
    assert result == "Aktiv + Qualifikation: Atemschutzgeräteträger + Tag: Nord"
    db.rollback()
    db.close()


def test_summarize_filter_json_handles_invalid_or_empty_data():
    assert summarize_filter_json(None) == "Keine Filter"
    assert summarize_filter_json("not-json") == "Keine Filter"


def test_campaign_test_send_uses_current_user_without_queue_item(monkeypatch):
    db = db_session()
    item, _ = campaign(db)
    config = db.query(MailingConfig).filter(MailingConfig.org_id == 1).first()
    if config is None:
        config = MailingConfig(org_id=1)
        db.add(config)
    config.enabled = True
    config.from_addr = "sender@example.at"
    db.flush()
    sent = []

    async def fake_send(message, api_key, from_addr):
        sent.append((message, api_key, from_addr))

    monkeypatch.setattr("app.services.mailing_service.mailing_api_key", lambda cfg: "secret")
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", fake_send)
    user = SimpleNamespace(id=1, org_id=1, email="admin@example.at", display_name="Max Muster", org=SimpleNamespace(name="Feuerwehr Test"))
    response = asyncio.run(campaign_test_send(item.id, db=db, user=user, _g=None))
    assert response.status_code == 303
    assert sent[0][0]["To"] == "admin@example.at"
    assert sent[0][0]["Subject"].startswith("[TEST] ")
    assert db.query(MailingQueueItem).filter(MailingQueueItem.campaign_id == item.id).count() == 0
    db.rollback()
    db.close()


def test_template_test_send_uses_current_user_and_template_content(monkeypatch):
    db = db_session()
    item = MailingTemplate(
        org_id=1,
        name="Test",
        subject="Hallo {{ vorname }}",
        body_html="<p>{{ empfaenger_name }}</p>",
        body_text="Text für {{ email }}",
    )
    config = db.query(MailingConfig).filter(MailingConfig.org_id == 1).first() or MailingConfig(
        org_id=1
    )
    config.enabled = True
    config.from_addr = "sender@example.at"
    config.reply_to_default = "reply@example.at"
    db.add_all([item, config])
    db.flush()
    sent = []

    async def fake_send(message, api_key, from_addr):
        sent.append((message, api_key, from_addr))

    monkeypatch.setattr("app.services.mailing_service.mailing_api_key", lambda cfg: "secret")
    monkeypatch.setattr("app.services.resend_mail_service.send_via_resend", fake_send)
    user = SimpleNamespace(
        id=1,
        org_id=1,
        email="admin@example.at",
        display_name="Max Muster",
        org=SimpleNamespace(name="Feuerwehr Test"),
    )
    response = asyncio.run(template_test_send(item.id, db=db, user=user, _g=None))
    message, api_key, from_addr = sent[0]
    assert response.status_code == 303
    assert response.headers["location"] == f"/mailing/templates/{item.id}/edit?test_sent=1"
    assert message["To"] == "admin@example.at"
    assert message["Subject"] == "[TEST] Hallo Max"
    assert message["Reply-To"] == "reply@example.at"
    assert "Max Muster" in message.get_body(preferencelist=("html",)).get_content()
    assert api_key == "secret"
    assert from_addr == "sender@example.at"
    db.rollback()
    db.close()


def test_template_test_send_rejects_user_without_email():
    db = db_session()
    item = MailingTemplate(org_id=1, name="Test", subject="Test", body_html="<p>Test</p>")
    db.add(item)
    db.flush()
    user = SimpleNamespace(
        id=1,
        org_id=1,
        email="",
        display_name="Max Muster",
        org=SimpleNamespace(name="Feuerwehr Test"),
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(template_test_send(item.id, db=db, user=user, _g=None))
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Für den aktuellen Benutzer ist keine E-Mail-Adresse hinterlegt."
    db.rollback()
    db.close()


def test_template_test_send_returns_404_for_unknown_template():
    db = db_session()
    user = SimpleNamespace(
        id=1,
        org_id=1,
        email="admin@example.at",
        display_name="Max Muster",
        org=SimpleNamespace(name="Feuerwehr Test"),
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(template_test_send(999_999, db=db, user=user, _g=None))
    assert exc_info.value.status_code == 404
    db.rollback()
    db.close()
