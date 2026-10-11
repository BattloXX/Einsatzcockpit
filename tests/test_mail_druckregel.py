"""M7: Eingangsmail-Druckregel erzeugt nur sichere, idempotente Jobs."""

import io
from datetime import datetime
from uuid import uuid4

from pypdf import PdfWriter

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import (
    DOC_MAIL_EINGANG,
    DOCUMENT_TYPE_LABELS,
    RULE_DOCUMENT_LABELS,
    TRIGGER_LABELS,
    TRIGGER_MAIL_EINGANG,
    Gateway,
    Printer,
    PrintJob,
    PrintRule,
)
from app.models.org_mail import OrgMailEingang
from app.services import print_artifact_service
from app.services.print_dispatcher import _filter_matches, _jobs_for_rule


def test_mail_rule_creates_one_job_per_printer_and_is_idempotent(setup_db):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = 880000 + uuid4().int % 10000
        gateway = Gateway(org_id=org, name="mail-gw", device_token_hash=hash_api_key(uuid4().hex))
        db.add(gateway)
        db.flush()
        printer = Printer(org_id=org, gateway_id=gateway.id, name="mail-p", uri="ipp://mail", aktiv=True)
        db.add(printer)
        db.flush()
        rule = PrintRule(org_id=org, name="mail-rule", aktiv=True, trigger=TRIGGER_MAIL_EINGANG,
                         documents=[DOC_MAIL_EINGANG], printer_ids=[printer.id])
        db.add(rule)
        db.flush()
        context = {"mail_eingang_id": 42, "mail_assigned": True, "mail_sender_approved": True}
        assert len(_jobs_for_rule(db, gateway, rule, context)) == 1
        assert _jobs_for_rule(db, gateway, rule, context) == []
        db.commit()
        jobs = db.query(PrintJob).filter_by(org_id=org, document_type=DOC_MAIL_EINGANG).all()
        assert len(jobs) == 1 and jobs[0].artifact_ref == "42" and jobs[0].printer_id == printer.id
    finally:
        db.close()


def test_mail_rule_filters_reject_unassigned_or_unapproved():
    class Rule:
        trigger = TRIGGER_MAIL_EINGANG
        filters = {"mail_nur_zugeordnet": True, "mail_nur_freigegeben": True}
        id = 1

    assert not _filter_matches(Rule(), {"mail_assigned": False, "mail_sender_approved": True})
    assert not _filter_matches(Rule(), {"mail_assigned": True, "mail_sender_approved": False})
    assert _filter_matches(Rule(), {"mail_assigned": True, "mail_sender_approved": True})


def test_mail_renderer_uses_plain_text_and_lists_nonprintable_attachment(monkeypatch):
    row = OrgMailEingang(
        id=42, org_id=9, quelle="resend", extern_id="resend", absender="sender@example.at", betreff="Lage",
        empfangen_at=datetime.now(), html_body="<b>Nur Text</b>",
        anhang_json='[{"id":"a", "filename":"daten.zip", "content_type":"application/zip", "size":12}]',
    )
    job = PrintJob(org_id=9, document_type=DOC_MAIL_EINGANG, artifact_ref="42", options={"mail_attachments": False})
    captured = []
    def fake_pdf(source, base_url):
        captured.append(source)
        out = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(out)
        return out.getvalue()

    class Db:
        def get(self, model, key):
            return row if model is OrgMailEingang else None

    monkeypatch.setattr(print_artifact_service, "_html_to_pdf", fake_pdf)
    print_artifact_service._render_mail_eingang(Db(), job, "")
    assert "sender@example.at" in captured[0] and "daten.zip" in captured[0]
    assert "&lt;b&gt;" not in captured[0] and "Nur Text" in captured[0]


def test_mail_trigger_is_exposed_to_rule_admin():
    assert TRIGGER_LABELS[TRIGGER_MAIL_EINGANG] == "E-Mail eingegangen"
    assert RULE_DOCUMENT_LABELS[DOC_MAIL_EINGANG] == "E-Mail eingegangen"
    assert DOCUMENT_TYPE_LABELS[DOC_MAIL_EINGANG] == "E-Mail eingegangen"


def test_kein_autodruck_wenn_abruf_fehlschlaegt(setup_db, monkeypatch):
    import asyncio
    from datetime import UTC, datetime

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_mail import OrgMailEingang
    from app.services import print_dispatcher
    from app.services import resend_inbound_service as inbound
    from tests.test_resend_posteingang import _org

    aufrufe = []

    async def spy(eingang_id):
        aufrufe.append(eingang_id)

    monkeypatch.setattr(print_dispatcher, "autoprint_mail_eingang_background", spy)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            class R:
                is_success = False

            return R()

    monkeypatch.setattr(inbound.httpx, "AsyncClient", lambda **kw: Client())
    org_id, _ = _org()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = OrgMailEingang(org_id=org_id, quelle="resend", extern_id="mail_np", absender="a", empfaenger="[]", betreff="b",
                             empfangen_at=datetime.now(UTC).replace(tzinfo=None), status="neu")
        db.add(row)
        db.commit()
        asyncio.run(inbound.fetch_inbound_message(db, org_id, row.id))
        assert row.status == "abruf_fehler" and aufrufe == []
    finally:
        db.close()
