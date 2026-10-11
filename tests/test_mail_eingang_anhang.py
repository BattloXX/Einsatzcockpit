"""Tests für lokale und Resend-basierte Anhänge des Posteingangs."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Message, MessageMedia
from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang, OrgMailEingangEreignis
from app.services import mail_eingang_anhang as anhang
from app.services.resend_inbound_service import loesche_alte_eingaenge
from tests.test_mail_zuordnung_integration import _einsatz, _lauf, _png, _settings
from tests.test_resend_posteingang import _login, _org


def _row(org_id: int) -> OrgMailEingang:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = OrgMailEingang(
            org_id=org_id, quelle="imap", extern_id=f"imap-{datetime.now().timestamp()}", absender="a@b.at",
            empfaenger="[]", betreff="Test", empfangen_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def test_speichere_und_lade_lokalen_anhang(setup_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    org_id, _ = _org()
    row = _row(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.merge(row)
        metadata = anhang.speichere_lokal(db, row, [{
            "filename": "../../etc/passwd", "content_type": "text/plain", "data": b"inhalt",
        }])
        db.commit()
        assert row.anhang_speicher == "lokal"
        assert metadata == [{
            "id": "l1", "filename": "_.._etc_passwd", "content_type": "text/plain", "size": 6,
            "lokal": True,
        }]
        assert (tmp_path / "mail_eingang" / str(org_id) / str(row.id) / "1").read_bytes() == b"inhalt"
        assert asyncio.run(anhang.lade_anhang(db, row, "l1")) == (b"inhalt", "_.._etc_passwd", "text/plain")
        assert asyncio.run(anhang.lade_anhang(db, row, "l1/../x")) is None
        assert asyncio.run(anhang.lade_anhang(db, row, "../1")) is None
    finally:
        db.close()


def test_zu_grosse_anhaenge_und_summe_werden_markiert(setup_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(anhang, "MAX_ATTACHMENT_BYTES", 3)
    monkeypatch.setattr(anhang, "MAX_MAIL_BYTES", 5)
    org_id, _ = _org()
    row = _row(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.merge(row)
        data = anhang.speichere_lokal(db, row, [
            {"filename": "gross.pdf", "content_type": "application/pdf", "data": b"1234"},
            {"filename": "eins.txt", "content_type": "text/plain", "data": b"123"},
            {"filename": "zwei.txt", "content_type": "text/plain", "data": b"123"},
        ])
        db.commit()
        assert data[0]["zu_gross"] is True and data[2]["zu_gross"] is True
        assert asyncio.run(anhang.lade_anhang(db, row, "l1")) is None
        events = db.query(OrgMailEingangEreignis).filter_by(eingang_id=row.id, typ="anhang_zu_gross").all()
        assert len(events) == 2
    finally:
        db.close()


def test_resend_anhang_wird_ueber_abstraktion_geladen(setup_db, monkeypatch):
    org_id, _ = _org()
    row = _row(org_id)
    row.quelle, row.anhang_speicher = "resend", None

    async def fake_metadata(*_args):
        return {"filename": "plan.pdf", "content_type": "application/pdf", "download_url": "https://files.resend.com/x"}

    class Response:
        content = b"%PDF"
        is_success = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_bytes(self):
            yield self.content

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url):
            return Response()

        def stream(self, _method, _url):
            return Response()

    monkeypatch.setattr(anhang, "resend_api_key_fuer_org", lambda *_args: "key")
    monkeypatch.setattr(anhang, "get_resend_attachment", fake_metadata)
    monkeypatch.setattr(anhang.httpx, "AsyncClient", Client)
    db = SessionLocal()
    try:
        assert asyncio.run(anhang.lade_anhang(db, row, "att_1")) == (b"%PDF", "plan.pdf", "application/pdf")
    finally:
        db.close()


def test_lokaler_download_ist_auf_den_mandanten_begrenzt(setup_db, client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    org_a, admin_a = _org()
    org_b, admin_b = _org()
    row = _row(org_a)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.merge(row)
        anhang.speichere_lokal(db, row, [{"filename": "a.txt", "content_type": "text/plain", "data": b"A"}])
        db.commit()
        entry_id = row.id
    finally:
        db.close()
    _login(client, admin_b)
    assert client.get(f"/admin/mail/eingang/{entry_id}/anhang/l1").status_code == 404
    client.cookies.clear()
    _login(client, admin_a)
    response = client.get(f"/admin/mail/eingang/{entry_id}/anhang/l1")
    assert response.status_code == 200 and response.content == b"A"
    assert response.headers["cache-control"] == "no-store"


def test_zuordnung_uebernimmt_lokales_pdf_und_png(setup_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    org_id, _ = _org()
    incident_id = _einsatz(org_id)
    _settings(org_id)
    row = _row(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.merge(row)
        row.absender = "lls@example.at"
        row.abgerufen_at = datetime.now(UTC).replace(tzinfo=None)
        anhang.speichere_lokal(db, row, [
            {"filename": "bild.png", "content_type": "image/png", "data": _png()},
            {"filename": "plan.pdf", "content_type": "application/pdf", "data": b"%PDF-1.4\n%%EOF"},
        ])
        db.commit()
        entry_id = row.id
    finally:
        db.close()
    assert asyncio.run(_lauf(entry_id)) is True
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        message = db.query(Message).filter_by(incident_id=incident_id).one()
        kinds = sorted(media.kind for media in db.query(MessageMedia).filter_by(message_id=message.id))
        assert kinds == ["image", "pdf"]
    finally:
        db.close()


def test_aufbewahrung_loescht_lokales_verzeichnis(setup_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    org_id, _ = _org()
    row = _row(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.merge(row)
        anhang.speichere_lokal(db, row, [{"filename": "a.txt", "content_type": "text/plain", "data": b"A"}])
        db.add(OrgSettings(org_id=org_id, mail_eingang_aufbewahrung_tage=1))
        row.empfangen_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
        db.commit()
        directory = anhang._verzeichnis(row)
        assert directory.exists()
        assert loesche_alte_eingaenge(db) == 1
        assert not directory.exists()
    finally:
        db.close()
