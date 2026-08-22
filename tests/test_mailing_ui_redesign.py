import asyncio
import json
from types import SimpleNamespace

from app.models.mailing import MailingConfig, MailingQueueItem
from app.models.master import MemberTag, Qualification
from app.routers.ui_mailing import campaign_test_send
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
