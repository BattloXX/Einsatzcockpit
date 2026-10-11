from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage

from app.models.org_mail import OrgMailEingangEreignis
from app.db import SessionLocal
from app.services.mail_eingang_service import (
    normalisiere_absender,
    normalisiere_mime,
    parse_authentication_results,
    speichere_eingang,
)


def test_speichere_dedupliziert_je_quelle(setup_db):
    db = SessionLocal()
    try:
        first = speichere_eingang(db, 1, quelle="resend", extern_id="same", absender="Erika <A@B.at>", betreff="T", empfaenger=[], cc=None, message_id=None, empfangen_at=datetime.now())
        assert first and first.absender == "a@b.at"
        assert speichere_eingang(db, 1, quelle="resend", extern_id="same", absender="a@b.at", betreff="T", empfaenger=[], cc=None, message_id=None, empfangen_at=datetime.now()) is None
        assert speichere_eingang(db, 1, quelle="imap", extern_id="same", absender="a@b.at", betreff="T", empfaenger=[], cc=None, message_id=None, empfangen_at=datetime.now())
        assert db.query(OrgMailEingangEreignis).filter_by(eingang_id=first.id, typ="empfangen").count() == 1
    finally:
        db.rollback()
        db.close()


def test_normalisiere_absender_und_auth_header():
    assert normalisiere_absender("Erika <A@B.at>") == "a@b.at"
    assert parse_authentication_results(["mx; spf=pass dkim=fail dmarc=neutral", "spf=fail dkim=pass dmarc=pass"]) == ("pass", "fail", "neutral")
    assert parse_authentication_results([]) == (None, None, None)


def test_normalisiere_mime_multipart_und_kaputte_mail():
    message = EmailMessage()
    message["From"] = "Erika <A@B.at>"
    message["To"] = "Z <z@example.at>"
    message["Authentication-Results"] = "mx; spf=pass dkim=pass dmarc=pass"
    message.set_content("Text")
    message.add_alternative("<p>HTML</p>", subtype="html")
    message.add_attachment(b"pdf", maintype="application", subtype="pdf", filename="a.pdf")
    message.add_attachment(b"img", maintype="image", subtype="png", filename="inline.png", disposition="inline")
    rfc822 = EmailMessage()
    rfc822.set_content("weitergeleitet")
    message.add_attachment(rfc822)
    parsed = normalisiere_mime(message.as_bytes())
    assert parsed["absender"] == "a@b.at" and parsed["text"] == "Text\n" and parsed["html"] == "<p>HTML</p>\n"
    assert len(parsed["anhaenge"]) == 3
    assert normalisiere_mime(b"broken headers\n\nbody")["betreff"] == ""
