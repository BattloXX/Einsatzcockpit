"""Tests für österreichische E.164-Normalisierung und sichere Anzeige."""

import pytest

from app.core.telefon import telefon_anzeige, telefon_maske, telefon_zu_e164_at


@pytest.mark.parametrize("wert", ["0664 123 45 67", "+43 664 123 45 67", "0043 664 123 45 67"])
def test_telefon_zu_e164_at_normalisiert_oesterreichische_mobilnummer(wert):
    assert telefon_zu_e164_at(wert) == "+436641234567"


def test_telefon_zu_e164_at_belaesst_ausland():
    assert telefon_zu_e164_at("+49 151 12345678") == "+4915112345678"


@pytest.mark.parametrize("wert", [None, "", "kein Telefon", "+43 12"])
def test_telefon_helfer_lehnen_ungueltige_werte_ab(wert):
    assert telefon_zu_e164_at(wert) is None
    assert telefon_anzeige(wert) == ""
    assert telefon_maske(wert) == ""


def test_telefon_anzeige_und_maske():
    assert telefon_anzeige("+436641234567") == "+43 664 1234567"
    assert telefon_maske("+436641234567") == "+43 664 ***4567"
    assert telefon_anzeige("+4915112345678") == "+49 15112345678"
