import pytest
from app.models.mailing import MailingQueueItem, MailingSuppressionEntry
from app.services.mailing_dispatch_loop import dispatch_once
from tests.mailing_phase2_helpers import campaign, db_session

@pytest.mark.asyncio
async def test_dispatch_skips_suppressed_without_attempt(monkeypatch):
    db=db_session(); c,_=campaign(db,status="queued"); c.total_count=1
    item=MailingQueueItem(org_id=1,campaign_id=c.id,email="blocked@example.at",status="queued")
    db.add_all([item,MailingSuppressionEntry(org_id=1,email="blocked@example.at",reason="manual")]); db.commit()
    async def forbidden(*args,**kwargs): raise AssertionError("must not send")
    monkeypatch.setattr("app.services.mailing_dispatch_loop.send_via_resend",forbidden)
    await dispatch_once(db); assert item.status=="suppressed" and item.attempt_count==0
    assert c.suppressed_count==1 and c.status=="sent"; db.close()
