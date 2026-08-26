"""Tests fuer die zentrale Telefonnummern-Normalisierung."""
import pytest

from app.core.telefon import telefon_kompakt, telefon_normalisiert
from app.core.templating import templates


@pytest.mark.parametrize(
    ("wert", "erwartet"),
    [
        ("", ""),
        (None, ""),
        ("+43 (664) 111-22/33", "+436641112233"),
        ("0043 664 111", "0043664111"),
        ("0664 111 222", "0664111222"),
    ],
)
def test_telefon_kompakt(wert, erwartet):
    assert telefon_kompakt(wert) == erwartet


@pytest.mark.parametrize(
    ("wert", "erwartet"),
    [
        ("", ""),
        (None, ""),
        ("+43 (664) 111-22/33", "+436641112233"),
        ("0043 664 111", "+43664111"),
        ("+43 664 111", "+43664111"),
        ("0664 111 222", "0664111222"),
    ],
)
def test_telefon_normalisiert(wert, erwartet):
    assert telefon_normalisiert(wert) == erwartet


def test_jinja_filter_tel_ist_registriert_und_wird_angewendet():
    assert templates.env.filters["tel"] is telefon_kompakt
    template = templates.env.from_string("{{ nummer|tel }}")
    assert template.render(nummer="+43 (664) 111-22/33") == "+436641112233"
