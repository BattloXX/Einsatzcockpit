from app.models.mailing import MailingQueueItem
from app.services.mailing_service import retry_failed_items
from tests.mailing_phase2_helpers import campaign,db_session
def test_retry_resets_failed_rows_in_place_without_duplicates():
    db=db_session(); c,_=campaign(db,status="failed"); a=MailingQueueItem(org_id=1,campaign_id=c.id,email="a@x.at",status="failed",attempt_count=3,error_message="boom"); b=MailingQueueItem(org_id=1,campaign_id=c.id,email="b@x.at",status="sent"); db.add_all([a,b]); db.flush(); ids={a.id,b.id}; assert retry_failed_items(db,c)==1; assert {x.id for x in c.queue_items}==ids and a.status=="queued" and a.attempt_count==0 and b.status=="sent"; db.rollback(); db.close()
