import pytest
from app.models.mailing import MailingConfig, MailingQueueItem
from app.services.mailing_dispatch_loop import dispatch_once
from app.services.resend_mail_service import ResendMailError
from tests.mailing_phase2_helpers import campaign, db_session

@pytest.mark.asyncio
async def test_resend_failure_uses_smtp(monkeypatch):
    db=db_session(); c,_=campaign(db,status="queued"); c.total_count=1; item=MailingQueueItem(org_id=1,campaign_id=c.id,email="smtp@example.at",status="queued")
    cfg=db.query(MailingConfig).filter_by(org_id=1).first() or MailingConfig(org_id=1)
    cfg.enabled=True; cfg.resend_api_key_enc="x"; cfg.from_addr="from@example.at"; db.add_all([item,cfg]); db.commit(); sent=[]
    async def fail(*a,**k): raise ResendMailError("offline")
    async def smtp(msg,cfg): sent.append(msg["To"])
    monkeypatch.setattr("app.services.mailing_dispatch_loop.send_via_resend",fail); monkeypatch.setattr("app.services.mailing_dispatch_loop.mailing_api_key",lambda c:"key")
    monkeypatch.setattr("app.services.mailing_dispatch_loop._org_smtp_cfg",lambda *a:{"host":"smtp","from_addr":"fallback@example.at"}); monkeypatch.setattr("app.services.mailing_dispatch_loop._send",smtp)
    await dispatch_once(db); assert item.status=="sent" and item.resend_message_id is None and "SMTP-Fallback" in item.error_message and "smtp@example.at" in sent; db.close()
