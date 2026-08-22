from app.models.mailing import MailingCampaignRecipientList,MailingRecipientList,MailingRecipientListEntry
from app.services.mailing_recipients import resolve_recipient_list_multi
from tests.mailing_phase2_helpers import campaign,db_session
def test_multi_list_union_deduplicates_email_case_insensitively():
    db=db_session(); c,l1=campaign(db); l2=MailingRecipientList(org_id=1,name="second",kind="static"); db.add(l2); db.flush(); db.add(MailingCampaignRecipientList(campaign_id=c.id,recipient_list_id=l2.id)); db.add_all([MailingRecipientListEntry(org_id=1,list_id=l1.id,email="Same@Example.at"),MailingRecipientListEntry(org_id=1,list_id=l2.id,email="same@example.at"),MailingRecipientListEntry(org_id=1,list_id=l2.id,email="other@example.at")]); db.flush(); assert {x["email"] for x in resolve_recipient_list_multi(db,c)}=={"same@example.at","other@example.at"}; db.rollback(); db.close()
