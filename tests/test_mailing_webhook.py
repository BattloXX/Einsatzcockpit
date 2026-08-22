import base64, hashlib, hmac, json, time
from app.models.mailing import MailingQueueItem, MailingSuppressionEntry
from app.services.mailing_webhook_service import process_resend_webhook_event, verify_resend_webhook_signature
from tests.mailing_phase2_helpers import campaign, db_session

def _signature(secret, body, ident="evt_1", timestamp=None):
    timestamp=str(timestamp or int(time.time())); raw=base64.b64encode(secret).decode()
    value=base64.b64encode(hmac.new(secret,f"{ident}.{timestamp}.{body.decode()}".encode(),hashlib.sha256).digest()).decode()
    return "whsec_"+raw,timestamp,"v1,"+value

def test_signature_valid_invalid_and_expired():
    body=b'{"type":"email.delivered"}'; secret,stamp,sig=_signature(b"secret",body)
    assert verify_resend_webhook_signature(secret,body,"evt_1",stamp,sig)
    assert not verify_resend_webhook_signature(secret,body+b" ","evt_1",stamp,sig)
    _,old,sig=_signature(b"secret",body,timestamp=int(time.time())-1000)
    assert not verify_resend_webhook_signature(secret,body,"evt_1",old,sig)

def test_hard_bounce_suppresses_and_event_is_idempotent():
    db=db_session(); c,_=campaign(db,status="sent"); item=MailingQueueItem(org_id=1,campaign_id=c.id,email="BOUNCE@example.at",status="sent",resend_message_id="mail_1"); db.add(item); db.commit()
    payload={"type":"email.bounced","data":{"email_id":"mail_1","bounce":{"type":"Permanent"}}}
    first=process_resend_webhook_event(db,1,"email.bounced",payload,"evt_bounce"); second=process_resend_webhook_event(db,1,"email.bounced",payload,"evt_bounce")
    assert first.id==second.id and item.status=="bounced"
    assert db.query(MailingSuppressionEntry).filter_by(email="bounce@example.at",reason="hard_bounce").count()==1
    db.close()
