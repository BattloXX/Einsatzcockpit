from datetime import UTC,datetime,timedelta
from app.models.mailing import MailingRecipientListEntry, MailingQueueItem
from app.services.mailing_schedule_loop import materialize_due_campaigns
from tests.mailing_phase2_helpers import campaign,db_session
def test_only_due_scheduled_campaign_is_materialized():
    db=db_session(); due,l1=campaign(db,status="scheduled"); future,l2=campaign(db,status="scheduled"); db.add_all([MailingRecipientListEntry(org_id=1,list_id=l1.id,email="due@example.at"),MailingRecipientListEntry(org_id=1,list_id=l2.id,email="future@example.at")]); due.scheduled_at=datetime.now(UTC).replace(tzinfo=None)-timedelta(minutes=1); future.scheduled_at=datetime.now(UTC).replace(tzinfo=None)+timedelta(hours=1); db.commit(); assert materialize_due_campaigns(db)==1; assert db.query(MailingQueueItem).filter_by(campaign_id=due.id).count()==1; assert future.status=="scheduled"; db.close()
