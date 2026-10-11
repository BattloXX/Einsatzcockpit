"""Gezielte UI-Regressionen für den organisationsweiten Mail-Posteingang."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.org_mail import OrgMailEingang, OrgO365MailConfig, OrgSmtpConfig
from app.services.mail_eingang_service import ereignis, speichere_eingang
from tests.test_resend_posteingang import _login, _org


def _mail(org_id, source, external_id, subject, *, status="neu", when=None, fetched=True):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = speichere_eingang(
            db, org_id, quelle=source, extern_id=external_id, absender="sender@example.at",
            betreff=subject, empfaenger=["empfang@example.at"], cc=None, message_id=None,
            empfangen_at=when or datetime.now(UTC), abgerufen=fetched,
        )
        assert row is not None
        row.status = status
        db.commit()
        return row.id
    finally:
        db.close()


def test_menu_badge_and_source_filter(client, setup_db):
    org_id, username = _org()
    _mail(org_id, "resend", "ui-resend", "Resend")
    _mail(org_id, "imap", "ui-imap", "IMAP")
    _mail(org_id, "o365", "ui-o365", "Office")
    _login(client, username)
    settings = client.get("/admin/mail").text
    assert "Posteingang" in settings and ">3</span>" in settings
    page = client.get("/admin/mail/eingang?quelle=imap").text
    assert "IMAP" in page and "Resend</a>" not in page and "Office</a>" not in page


def test_list_results_period_and_channel_status(client, setup_db):
    org_id, username = _org()
    assigned = _mail(org_id, "resend", "ui-assigned", "Zugeordnet", status="zugeordnet")
    waiting = _mail(org_id, "imap", "ui-wait", "Wartend", status="wartend")
    rejected = _mail(org_id, "o365", "ui-rejected", "Abgelehnt", status="posteingang")
    _mail(org_id, "imap", "ui-failed", "Fehler", status="abruf_fehler")
    _mail(org_id, "resend", "ui-old", "UI_ALTMARKER", when=datetime.now(UTC) - timedelta(days=2))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = {row.id: row for row in db.query(OrgMailEingang).filter_by(org_id=org_id).all()}
        rows[assigned].incident_id, rows[assigned].zuordnung = 123, "auto"
        ereignis(db, rows[waiting], "wartend", "wartet auf Einsatz bis 12:34")
        rows[rejected].zuordnung, rows[rejected].zuordnung_grund = "abgelehnt", "Absender nicht freigegeben"
        db.add(OrgSmtpConfig(org_id=org_id, imap_enabled=True, imap_letzter_fehler="IMAP kaputt"))
        db.add(OrgO365MailConfig(org_id=org_id, read_enabled=True, read_postfach="read@example.at"))
        db.commit()
    finally:
        db.close()
    _login(client, username)
    page = client.get("/admin/mail/eingang").text
    assert "In Einsatz #123 übernommen (automatisch)" in page and "/incident/123" in page
    assert "wartet auf Einsatz bis 12:34" in page and "Absender nicht freigegeben" in page
    assert "Abruf fehlgeschlagen" in page and "IMAP kaputt" in page and "read@example.at" in page
    assert "UI_ALTMARKER" not in client.get("/admin/mail/eingang?zeitraum=24h").text


def test_detail_timeline_and_reprocess(client, setup_db, monkeypatch):
    org_id, username = _org()
    imap_id = _mail(org_id, "imap", "ui-reprocess", "Reprocess")
    resend_id = _mail(org_id, "resend", "ui-resend-reprocess", "Resend reprocess")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(OrgMailEingang, imap_id)
        ereignis(db, row, "wartend", "erste Station")
        ereignis(db, row, "zugeordnet", "zweite Station")
        db.commit()
    finally:
        db.close()
    calls = []

    async def assign(db, entry_id):
        calls.append(("assign", entry_id))

    async def fetch(db, oid, entry_id):
        calls.append(("fetch", oid, entry_id))

    monkeypatch.setattr("app.services.mail_zuordnung_service.ordne_zu", assign)
    monkeypatch.setattr("app.services.resend_inbound_service.fetch_inbound_message", fetch)
    _login(client, username)
    detail = client.get(f"/admin/mail/eingang/{imap_id}").text
    assert detail.index("erste Station") < detail.index("zweite Station")
    assert "Wartet auf Einsatz" in detail and "In Einsatz übernommen" in detail
    csrf = client.cookies.get("ec_csrf")
    assert client.post(
        f"/admin/mail/eingang/{imap_id}/erneut-verarbeiten", data={"_csrf": csrf}, follow_redirects=False
    ).status_code == 303
    assert client.post(
        f"/admin/mail/eingang/{resend_id}/erneut-verarbeiten", data={"_csrf": csrf}, follow_redirects=False
    ).status_code == 303
    assert ("assign", imap_id) in calls and ("fetch", org_id, resend_id) in calls


def test_cross_org_mail_detail_and_action_are_hidden(client, setup_db):
    org_a, _ = _org()
    _, admin_b = _org()
    entry_id = _mail(org_a, "imap", "ui-cross-org", "Nur A")
    _login(client, admin_b)
    assert "Nur A" not in client.get("/admin/mail/eingang").text
    assert client.get(f"/admin/mail/eingang/{entry_id}").status_code == 404
    assert client.get(f"/admin/mail/eingang/{entry_id}/anhang/beliebig").status_code == 404
    csrf = client.cookies.get("ec_csrf")
    assert client.post(
        f"/admin/mail/eingang/{entry_id}/archivieren", data={"_csrf": csrf}, follow_redirects=False
    ).status_code == 404


def test_mail_templates_have_no_smart_quotes_in_tags():
    import re
    from pathlib import Path

    names = ["mail_eingang.html", "_mail_eingang_content.html", "mail_eingang_detail.html", "_layout.html"]
    for name in names:
        content = Path("app/templates/admin", name).read_text()
        assert not any(re.search(r"[“”„‘’]", tag) for tag in re.findall(r"<[^>]*>", content))
