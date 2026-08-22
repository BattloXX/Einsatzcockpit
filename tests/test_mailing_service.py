from app.models.mailing import MailingQueueItem,MailingRecipientListEntry
from app.services.mailing_service import queue_campaign,retry_failed_items
from tests.mailing_phase2_helpers import campaign,db_session
def test_failed_campaign_cannot_create_duplicate_queue_batch():
    db=db_session(); c,lst=campaign(db); db.add(MailingRecipientListEntry(org_id=1,list_id=lst.id,email="one@example.at")); db.flush(); queue_campaign(db,c); assert len(c.queue_items)==1; c.status="failed"; c.queue_items[0].status="failed"; original=c.queue_items[0].id; queue_campaign(db,c); assert db.query(MailingQueueItem).filter_by(campaign_id=c.id).count()==1; assert retry_failed_items(db,c)==1 and c.queue_items[0].id==original and c.queue_items[0].status=="queued"; db.rollback(); db.close()
def test_campaign_override_applies_to_new_queue_items():
    db=db_session(); c,lst=campaign(db); c.max_attempts_override=7; db.add(MailingRecipientListEntry(org_id=1,list_id=lst.id,email="one@example.at")); db.flush(); queue_campaign(db,c); assert c.queue_items[0].max_attempts==7; db.rollback(); db.close()
