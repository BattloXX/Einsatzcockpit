"""Unit coverage for the safety decisions in mail assignment."""
from __future__ import annotations

from datetime import datetime

from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang
from app.services.mail_zuordnung_service import _allowed, _html_text


def _entry(**values) -> OrgMailEingang:
    defaults = dict(
        org_id=1, resend_email_id="mail_1", absender="leitstelle@example.at", empfaenger="[]",
        betreff="Test", empfangen_at=datetime.now(),
    )
    defaults.update(values)
    return OrgMailEingang(**defaults)


def test_freigegebener_absender_akzeptiert_adresse_und_domain_case_insensitive():
    settings = OrgSettings(org_id=1, mail_eingang_modus="freigegeben", mail_eingang_absender="@example.at")
    assert _allowed(_entry(absender="Leitstelle@EXAMPLE.AT"), settings)[0]
    assert not _allowed(_entry(absender="other@example.org"), settings)[0]


def test_alle_braucht_bestimmte_und_vollstaendige_authentifizierung():
    settings = OrgSettings(org_id=1, mail_eingang_modus="alle")
    assert _allowed(_entry(spf="pass", dkim="pass", dmarc="pass"), settings)[0]
    assert not _allowed(_entry(spf="pass", dkim="pass", dmarc=None), settings)[0]


def test_html_wird_zu_text_ohne_tags_oder_remote_content():
    text = _html_text('<p>Alarm <strong>jetzt</strong></p><img src="https://evil.invalid/a.png">')
    assert text == "Alarm jetzt"
    assert "<" not in text and "evil.invalid" not in text
