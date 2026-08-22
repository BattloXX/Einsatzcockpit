from uuid import uuid4
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.mailing import MailingCampaign, MailingCampaignRecipientList, MailingRecipientList, MailingTemplate

def db_session():
    db=SessionLocal(); set_tenant_context(db,None); return db
def campaign(db,org_id=1,status="draft"):
    suffix=uuid4().hex
    tpl=MailingTemplate(org_id=org_id,name="T"+suffix,subject="Hallo",body_html='<body><a href="https://example.at/a">Link</a></body>',body_text="Hallo")
    lst=MailingRecipientList(org_id=org_id,name="L"+suffix,kind="static")
    db.add_all([tpl,lst]); db.flush()
    c=MailingCampaign(org_id=org_id,template_id=tpl.id,recipient_list_id=None,status=status)
    db.add(c); db.flush(); db.add(MailingCampaignRecipientList(campaign_id=c.id,recipient_list_id=lst.id)); db.flush()
    return c,lst
