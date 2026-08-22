import asyncio, logging
from datetime import UTC, datetime
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.mailing import MailingCampaign
from app.services.mailing_service import queue_campaign
logger=logging.getLogger("einsatzleiter.mailing.schedule"); INTERVAL_SECONDS=60
def materialize_due_campaigns(db=None):
    owns=db is None
    if owns: db=SessionLocal(); set_tenant_context(db,None)
    try:
        now=datetime.now(UTC).replace(tzinfo=None)
        rows=db.query(MailingCampaign).execution_options(include_all_tenants=True).filter(MailingCampaign.status=="scheduled",MailingCampaign.scheduled_at<=now).all()
        for row in rows: row.status="draft"; queue_campaign(db,row)
        db.commit(); return len(rows)
    finally:
        if owns: db.close()
async def mailing_schedule_loop():
    while True:
        try: materialize_due_campaigns()
        except asyncio.CancelledError: raise
        except Exception: logger.exception("Mailing-Zeitplanung fehlgeschlagen")
        await asyncio.sleep(INTERVAL_SECONDS)
