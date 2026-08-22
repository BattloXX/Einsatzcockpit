from app.core.security import sign_mailing_track_token
from app.models.mailing import MailingLinkClick, MailingQueueItem
from tests.mailing_phase2_helpers import campaign, db_session
def test_tracking_pixel_and_click_update_aggregates(client):
    db=db_session(); c,_=campaign(db); item=MailingQueueItem(org_id=1,campaign_id=c.id,email="track@example.at"); db.add(item); db.commit(); iid=item.id; cid=c.id; token=sign_mailing_track_token(iid,1); db.close()
    assert client.get(f"/mailing/t/{token}.png").status_code==200
    r=client.get(f"/mailing/c/{token}?u=https%3A%2F%2Fexample.at",follow_redirects=False); assert r.status_code==302
    db=db_session(); item=db.get(MailingQueueItem,iid); assert item.open_count==1 and item.click_count==1; assert item.campaign.open_count==1 and item.campaign.click_count==1; assert db.query(MailingLinkClick).filter_by(queue_item_id=iid).count()==1; db.close()
def test_invalid_pixel_does_not_reveal_validity(client): assert client.get('/mailing/t/invalid.png').status_code==200
