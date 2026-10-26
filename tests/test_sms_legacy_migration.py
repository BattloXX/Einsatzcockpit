import hashlib

import pytest

from app.models.sms import SmsLog, SmsLogRecipient
from app.services import sms_outbox as outbox
from app.services.gateway_presence import utc
from app.services.sms_service import SmsContext

pytestmark = pytest.mark.usefixtures("sms_outbox_state")


@pytest.mark.parametrize("existing", ["queued", "dispatched", "sent", "failed", "uncertain"])
def test_legacy_unknown_blocks_retry_but_preserves_known_terminal_job(existing):
    with outbox.database() as db:
        log = SmsLog(org_id=1, source="alarm", sent_at=utc(), text="Legacy alarm", recipient_count=1)
        db.add(log)
        db.flush()
        phone = "+4366000999"
        recipient = SmsLogRecipient(sms_log_id=log.id, phone_number=phone, sent_at=utc(),
                                    provider="sendet", success=False)
        db.add(recipient)
        key = f"alarm:{log.id}:{hashlib.sha256(phone.encode()).hexdigest()[:24]}"
        jid = outbox.enqueue_in(db, 1, key, phone, log.text, ["gateway"])
        job = db.get(outbox.Job, jid)
        job.status = existing
        db.commit()
        outbox.queue_log(db, log, {phone: (None, None)}, SmsContext(1, ["gateway"], None))
        db.commit()
        assert recipient.dispatch_job_id == jid
        assert job.status == (existing if existing in outbox.TERMINAL else "uncertain")
        assert recipient.success == (existing == "sent")
        assert log.completed_at and log.success_count == int(existing == "sent")
        assert outbox.queue_log(db, log, {phone: (None, None)}, SmsContext(1, ["gateway"], None)) == []
