from app.services.mailing_dispatch_loop import BATCH_SIZE, INTERVAL_SECONDS, _message, dispatch_once
import pytest
from datetime import UTC,datetime
from app.models.mailing import MailingConfig,MailingQueueItem
from tests.mailing_phase2_helpers import campaign,db_session


def test_dispatch_defaults():
    assert BATCH_SIZE == 20 and INTERVAL_SECONDS == 15 and callable(dispatch_once)


def test_message_contains_unsubscribe_headers_and_visible_footer(monkeypatch):
    db = db_session()
    c, _ = campaign(db)
    item = MailingQueueItem(org_id=1, campaign_id=c.id, email="send@example.at")
    db.add(item)
    db.commit()
    monkeypatch.setattr("app.services.mailing_dispatch_loop.settings.PUBLIC_BASE_URL", "https://mail.example.at")
    cfg = MailingConfig(org_id=1, from_addr="newsletter@example.at", reply_to_default="reply@example.at")
    msg = _message(item, cfg)
    assert str(msg["List-Unsubscribe"]).startswith("<mailto:reply@example.at?subject=unsubscribe>, <https://mail.example.at/mailing/u/")
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "Jetzt abmelden: https://mail.example.at/mailing/u/" in msg.get_body(preferencelist=("plain",)).get_content()
    assert "Jetzt abmelden</a>" in msg.get_body(preferencelist=("html",)).get_content()
    db.close()

@pytest.mark.asyncio
async def test_dispatch_persists_resend_id_and_sql_counts(monkeypatch):
    db=db_session(); c,_=campaign(db,status="queued"); c.total_count=2
    item=MailingQueueItem(org_id=1,campaign_id=c.id,email="send@example.at",status="queued")
    stale=MailingQueueItem(org_id=1,campaign_id=c.id,email="failed@example.at",status="failed")
    cfg=db.query(MailingConfig).filter_by(org_id=1).first() or MailingConfig(org_id=1); cfg.enabled=True; cfg.resend_api_key_enc="x"; cfg.from_addr="from@example.at"; db.add_all([item,stale,cfg]); db.commit(); iid=item.id
    async def send(*args,**kwargs): return "resend-42"
    monkeypatch.setattr("app.services.mailing_dispatch_loop.send_via_resend",send); monkeypatch.setattr("app.services.mailing_dispatch_loop.mailing_api_key",lambda cfg:"key")
    assert await dispatch_once(db,batch_size=10000)>=1; db.refresh(item); assert item.resend_message_id=="resend-42" and item.status=="sent"; assert c.sent_count==1 and c.failed_count==1 and c.status=="failed"; db.close()

@pytest.mark.asyncio
async def test_backoff_is_capped_at_sixty_minutes(monkeypatch):
    db=db_session(); c,_=campaign(db,status="queued"); c.total_count=1; item=MailingQueueItem(org_id=1,campaign_id=c.id,email="retry@example.at",status="queued",attempt_count=9,max_attempts=20); cfg=db.query(MailingConfig).filter_by(org_id=1).first() or MailingConfig(org_id=1); cfg.enabled=True; cfg.resend_api_key_enc="x"; cfg.from_addr="from@example.at"; db.add_all([item,cfg]); db.commit()
    async def fail(*args,**kwargs): raise RuntimeError("no")
    monkeypatch.setattr("app.services.mailing_dispatch_loop.send_via_resend",fail); monkeypatch.setattr("app.services.mailing_dispatch_loop.mailing_api_key",lambda cfg:"key")
    before=datetime.now(UTC).replace(tzinfo=None); await dispatch_once(db,batch_size=10000); assert 59.9 <= (item.next_attempt_at-before).total_seconds()/60 <= 60.1; db.close()
