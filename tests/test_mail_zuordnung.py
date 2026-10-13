"""Unit coverage for the safety decisions in mail assignment."""
from __future__ import annotations

from datetime import datetime

from app.models.master import OrgSettings
from app.models.org_mail import OrgMailEingang
from app.services.mail_zuordnung_service import _allowed, _html_text, leitstellennummer_aus_betreff


def _entry(**values) -> OrgMailEingang:
    defaults = dict(
        org_id=1, quelle="resend", extern_id="mail_1", absender="leitstelle@example.at", empfaenger="[]",
        betreff="Test", empfangen_at=datetime.now(),
    )
    defaults.update(values)
    return OrgMailEingang(**defaults)


def test_freigegebener_absender_akzeptiert_adresse_und_domain_case_insensitive():
    settings = OrgSettings(org_id=1, mail_eingang_modus="freigegeben", mail_eingang_absender="@example.at")
    assert _allowed(_entry(absender="Leitstelle@EXAMPLE.AT"), settings)[0]
    assert not _allowed(_entry(absender="other@example.org"), settings)[0]


def test_alle_akzeptiert_mails_unabhaengig_von_der_authentifizierung():
    settings = OrgSettings(org_id=1, mail_eingang_modus="alle")
    assert _allowed(_entry(spf="pass", dkim="pass", dmarc="pass"), settings) == (True, "")
    assert _allowed(_entry(spf="fail", dkim="fail", dmarc="fail"), settings) == (True, "")
    assert _allowed(_entry(), settings) == (True, "")


def test_html_wird_zu_text_ohne_tags_oder_remote_content():
    text = _html_text('<p>Alarm <strong>jetzt</strong></p><img src="https://evil.invalid/a.png">')
    assert text == "Alarm jetzt"
    assert "<" not in text and "evil.invalid" not in text


def test_leitstellennummer_aus_betreff_erkennt_nur_das_erste_token():
    assert leitstellennummer_aus_betreff(
        "f26009038 t1 WOLFURT L41 - SENDERSTRASSE"
    ) == "f26009038"
    assert leitstellennummer_aus_betreff("AW: F26009038 Antwort") == "f26009038"
    assert leitstellennummer_aus_betreff("fu26303655 Übung") == "fu26303655"
    assert leitstellennummer_aus_betreff("Einsatzinfo f26009038") is None
    assert leitstellennummer_aus_betreff("") is None
    assert leitstellennummer_aus_betreff(None) is None


def test_absenderliste_mit_mehreren_zeilen_wird_gespeichert():
    from app.routers.ui_org_mail import _gueltige_absenderliste

    roh = "johannes@battlogg.org\r\nM.Piller@meusburger.com\r\n\r\n@lls.at\n"
    assert _gueltige_absenderliste(roh) == "johannes@battlogg.org\nm.piller@meusburger.com\n@lls.at"
    assert _gueltige_absenderliste("a@example.at\x00\r\nb@example.at") is None
    assert _gueltige_absenderliste("a@example.at\tb@example.at") is None
