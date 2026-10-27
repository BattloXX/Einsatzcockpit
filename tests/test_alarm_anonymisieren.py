"""Wiederverwendbare Maskierung von Alarmtexten."""

import pytest

from app.services.alarm_parser.anonymisieren import anonymisiere


@pytest.mark.parametrize("text, erwartet", [
    ("Telefon +43 664 1234567", "Telefon [Telefon]"),
    ("Telefon 0664/1234567", "Telefon [Telefon]"),
    ("Tel 0043 (664) 1234567", "Tel [Telefon]"),
    ("Mail max.mustermann@example.at", "Mail [E-Mail]"),
    ("Notrufer Max Mustermann > Brand", "Notrufer [Name] > Brand"),
    ("Melder: Eva Musterfrau\nAnrufer Franz Maier", "Melder: [Name]\nAnrufer [Name]"),
    ("AT61 1904 3002 3457 3201", "[IBAN]"),
    ("f26008809 f2 > notrufer nicht mehr vorort", "f26008809 f2 > notrufer nicht mehr vorort"),
    ("notrufer Max Mustermann", "notrufer [Name]"),
    ("notrufer max mustermann", "notrufer [Name]"),
    ("ANRUFER MAX MUSTERMANN", "ANRUFER [Name]"),
    ("Melder 12/3, Hausnummer 11", "Melder 12/3, Hausnummer 11"),
])
def test_anonymisierung(text, erwartet):
    assert anonymisiere(text) == erwartet
    assert anonymisiere(erwartet) == erwartet


def test_mehrere_daten_bleiben_maskiert():
    text = "Notrufer Max Mustermann +43 664 1234567 max@example.at > Rauch"
    result = anonymisiere(text)
    assert result == "Notrufer [Name] [Telefon] [E-Mail] > Rauch"
