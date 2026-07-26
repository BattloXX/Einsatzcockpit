"""Tests für bma_pdf_parser.py: Parsen eines manuell hochgeladenen BMA-
Datenblatt-PDFs (Landeswarnzentrale Vorarlberg) in dieselbe {"anlage":...,
"kontakte":...}-Form wie die Live-Schnittstelle (bma_parser.py).

ECHTES_DATENBLATT ist der 1:1-Textlayer-Extrakt (pypdf) eines echten
Datenblatts ("BMA 1238") — mit einer echten Datei verifiziert, dass pypdf hier
trotz Identity-H-Subset-Font korrekte Unicode-Umlaute liefert (kein OCR
nötig). Reihenfolge/Leerzeichen-Eigenheiten je Zeile (z.B. "Aufschaltung
RFL:Ja" ohne Leerzeichen, "Telefon Beruf: +43" MIT Leerzeichen) sind exakt
wie im echten Extrakt belassen, nicht künstlich vereinheitlicht.
"""
from app.services.bma_import.bma_pdf_parser import parse_datenblatt_text

ECHTES_DATENBLATT = """BMA 1238
Böhler Fenster Wolfurt
1. Angaben zur Brandmeldeanlage
Standort: Böhler Fenster Wolfurt Anlagedatum: 19.02.2020 10:01:04
Straße: Wiesenweg 33 PLZ/Ort: 6922 Wolfurt
Telefon Beruf: +43 5574 74550 Fax Beruf: +43 5574 74550-20
EMail Beruf: servicecenter@boehlerfenster.com
Aufschaltung RFL:Ja - 17.10.2002
Rechnungsadresse:
Firma: Böhler Fenster GmbH EMail:
Zusatz:
Straße: Wiesenweg 23 PLZ/Ort: 6922 Wolfurt
Brandschutzbeauftragte(r)
Name: Davit Stephanyan
Straße: Bildsteinerstraße 7a / 15 PLZ/Ort: 6858 Schwarzach
Telefon Beruf:+43 5574 74550314 Tel.Mobil Privat:+43 660 6888732
EMail Privat: d.stepanyan@armenischer-kv.at Pager: FW Schwarzach 16
2. Alarmierung Feuerwehr
Alarmierung der örtlichen Feuerwehr mit Stichwort F14 (Brandmeldeanlagen):
Feuerwehr: FW - Wolfurt
3. Verständigung
BMA Alarmperson
Name: Andreas Böhler
Straße: Wiesenweg 29a PLZ/Ort: 6922 Wolfurt
Telefon Beruf:+43 5574 74550 Tel.Mobil Privat:+43 664 2642912
EMail Beruf:andreas.boehler@boehlerfenster.com Tel.Mobil Beruf:+43 664 2642912
EMail Privat:ab@feuerwehr.wolfurt.at Pager: FW Wolfurt 31
BMA Alarmperson
Name: Davit Stephanyan
Straße: Bildsteinerstraße 7a / 15 PLZ/Ort: 6858 Schwarzach
Telefon Beruf:+43 5574 74550314 Tel.Mobil Privat:+43 660 6888732
EMail Privat: d.stepanyan@armenischer-kv.at Pager: FW Schwarzach 16

Datenblatt zuletzt aktualisiert: 23.07.2026
Datenblatt zuletzt geprüft:"""


def test_parst_anlage_stammdaten():
    ergebnis = parse_datenblatt_text(ECHTES_DATENBLATT)
    anlage = ergebnis["anlage"]
    assert anlage["extern_id"] == "pdf:1238"
    assert anlage["bma_nummer"] == "1238"
    assert anlage["bezeichnung"] == "Böhler Fenster Wolfurt"
    assert anlage["strasse"] == "Wiesenweg"
    assert anlage["hausnummer"] == "33"
    assert anlage["plz"] == "6922"
    assert anlage["ort"] == "Wolfurt"
    assert anlage["telefon"] == "+43 5574 74550"
    assert anlage["fax"] == "+43 5574 74550-20"
    assert anlage["email"] == "servicecenter@boehlerfenster.com"
    assert anlage["anlagedatum"] == "19.02.2020 10:01:04"


def test_parst_rfl_aufschaltung():
    anlage = parse_datenblatt_text(ECHTES_DATENBLATT)["anlage"]
    assert anlage["is_rfl"] is True
    assert anlage["rfl_aufschaltdatum"] == "17.10.2002"


def test_ohne_rfl_aufschaltung_ist_is_rfl_false():
    text = ECHTES_DATENBLATT.replace("Aufschaltung RFL:Ja - 17.10.2002", "Aufschaltung RFL:Nein")
    anlage = parse_datenblatt_text(text)["anlage"]
    assert anlage["is_rfl"] is False
    assert anlage["rfl_aufschaltdatum"] is None


def test_parst_feuerwehr_stichwort_ohne_zielspalte_aber_sichtbar():
    """Kein Modellfeld dafür (siehe Modul-Docstring) — trotzdem mitgeführt, damit
    im Review nichts stillschweigend verloren geht."""
    anlage = parse_datenblatt_text(ECHTES_DATENBLATT)["anlage"]
    assert anlage["feuerwehr_stichwort"] == "F14"
    assert anlage["feuerwehr"] == "FW - Wolfurt"


def test_parst_rechnungsadresse():
    anlage = parse_datenblatt_text(ECHTES_DATENBLATT)["anlage"]
    assert anlage["rechnungsadresse"] == {
        "name": "Böhler Fenster GmbH", "email": None,
        "strasse": "Wiesenweg 23", "plz": "6922", "ort": "Wolfurt",
    }


def test_ohne_rechnungsadresse_block_ist_rechnungsadresse_none():
    text = "\n".join(
        z for z in ECHTES_DATENBLATT.splitlines()
        if z not in ("Rechnungsadresse:", "Firma: Böhler Fenster GmbH EMail:", "Zusatz:")
        and not (z.startswith("Straße: Wiesenweg 23"))
    )
    anlage = parse_datenblatt_text(text)["anlage"]
    assert anlage["rechnungsadresse"] is None


def test_parst_datenblatt_aktualisiert_am():
    anlage = parse_datenblatt_text(ECHTES_DATENBLATT)["anlage"]
    assert anlage["datenblatt_aktualisiert_am"] == "23.07.2026"


def test_parst_alle_drei_kontakte_mit_rollen():
    kontakte = parse_datenblatt_text(ECHTES_DATENBLATT)["kontakte"]
    assert len(kontakte) == 3
    assert [k["art"] for k in kontakte] == [
        "brandschutzbeauftragter", "bma_alarmperson", "bma_alarmperson",
    ]
    assert {k["name"] for k in kontakte} == {"Davit Stephanyan", "Andreas Böhler"}


def test_kontakt_telefone_und_email():
    kontakte = parse_datenblatt_text(ECHTES_DATENBLATT)["kontakte"]
    andreas = next(k for k in kontakte if k["name"] == "Andreas Böhler")
    assert andreas["email"] == "andreas.boehler@boehlerfenster.com"
    assert andreas["telefone"] == [
        "Telefon beruflich: +43 5574 74550",
        "Mobil beruflich: +43 664 2642912",
        "Mobil privat: +43 664 2642912",
        "Pager: FW Wolfurt 31",
    ]


def test_kontakt_adresse_wird_nicht_uebernommen():
    """Muster bma_parser.py::_parse_kontakt_karte() — Straße/PLZ-Ort der
    Kontaktperson landen in keinem Zielfeld (kein ObjektKontakt-Adressfeld)."""
    kontakte = parse_datenblatt_text(ECHTES_DATENBLATT)["kontakte"]
    for k in kontakte:
        assert "strasse" not in k
        assert "plz" not in k
        assert "ort" not in k


def test_dieselbe_person_in_zwei_rollen_ergibt_zwei_kontakte_mit_eigener_extern_id():
    """Davit Stephanyan tritt als Brandschutzbeauftragter UND (impliziert durch
    Namensgleichheit im echten Datenblatt) potenziell nochmal auf - jede Rolle
    bekommt eine eigene extern_id, damit beide als getrennte ObjektKontakt-
    Zeilen erhalten bleiben (Muster: bma_parser.py)."""
    kontakte = parse_datenblatt_text(ECHTES_DATENBLATT)["kontakte"]
    extern_ids = [k["extern_id"] for k in kontakte]
    assert len(extern_ids) == len(set(extern_ids))


def test_ohne_bma_nummer_wirft_value_error():
    import pytest
    with pytest.raises(ValueError, match="BMA-Nummer"):
        parse_datenblatt_text("Kein gueltiges Datenblatt\nZeile 2")


def test_leerer_text_wirft_value_error():
    import pytest
    with pytest.raises(ValueError, match="Leeres Datenblatt"):
        parse_datenblatt_text("   \n  ")


def test_strasse_ohne_hausnummer_bleibt_als_ganzes_in_strasse():
    text = ECHTES_DATENBLATT.replace("Straße: Wiesenweg 33 PLZ/Ort:", "Straße: Ringstraße PLZ/Ort:")
    anlage = parse_datenblatt_text(text)["anlage"]
    assert anlage["strasse"] == "Ringstraße"
    assert anlage["hausnummer"] is None
