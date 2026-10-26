"""Anonymisierte Beispiele und Invarianten des funktionalen P3a-Kerns."""

import json
import random
from pathlib import Path

import pytest

from app.services.alarm_parser import (
    STANDARD_REGELSATZ,
    GemeindeInfo,
    ParserKontext,
    Regelsatz,
    leerer_kontext,
    parse_alarm,
    validiere_muster,
)
from app.services.alarm_parser.normalize import adressvergleich, normalisiere, vergleichsform

DATEN = Path(__file__).parent / "data" / "alarm_wut"


@pytest.fixture
def kontext() -> ParserKontext:
    return ParserKontext(gemeinden={
        "wolfurt": GemeindeInfo("Wolfurt", strassen={"fruehlingstrasse": "Frühlingstraße"}),
        "buch": GemeindeInfo("Buch", ortsteile={"koss": "Koss"}),
        "lauterach": GemeindeInfo("Lauterach", objekte={"bad lerchenau": "bad lerchenau"}),
    })


def pruefe_invarianten(raw, result):
    assert result.original_text.encode("utf-8", errors="surrogatepass") == raw.encode("utf-8", errors="surrogatepass")
    json.dumps(result.to_dict())
    rest = vergleichsform(result.rest_text)
    quelle = f" {vergleichsform(raw)} "
    # Gleiche Worte koennen an verschiedenen Positionen vorkommen. Nur bei
    # einmaligem Vorkommen beweist ein Inhaltsvergleich doppelten Verbrauch.
    werte = [getattr(result, name) for name in (
        "leitstellennummer", "stichwort", "gemeinde", "ortsteil", "strasse", "hausnummer",
        "adresszusatz", "lagezusatz", "meldebild", "melder", "melderbereich",
    )]
    for name in ("weitere_codes", "sonderkennzeichen", "zusatzinfo", "bma"):
        werte.extend(getattr(result, name))
    for wert in filter(None, werte):
        normal = vergleichsform(wert)
        if normal and quelle.count(f" {normal} ") == 1:
            assert f" {normal} " not in f" {rest} "
    assert 0 <= result.qualitaet <= 100


@pytest.mark.parametrize("pfad", sorted(DATEN.glob("*.txt")), ids=lambda path: path.stem)
def test_fixtures(pfad, kontext):
    raw = pfad.read_bytes().decode("utf-8", errors="surrogatepass")
    erwartet = json.loads(pfad.with_suffix(".expected.json").read_text())
    result = parse_alarm(raw, kontext)
    assert result.to_dict() == erwartet
    pruefe_invarianten(raw, result)


def test_buch_ohne_ortsteil_katalog(kontext):
    kontext.gemeinden["buch"].ortsteile.clear()
    raw = (DATEN / "anhang_buch.txt").read_text()
    result = parse_alarm(raw, kontext)
    assert result.ortsteil is None
    assert result.lagezusatz == "koss oestlich im wald"
    assert result.rest_text == ""
    pruefe_invarianten(raw, result)


def test_leerer_katalog_kein_raten():
    raw = (DATEN / "anhang_wolfurt.txt").read_text()
    result = parse_alarm(raw, leerer_kontext())
    assert result.gemeinde is None
    assert result.strasse is None
    assert "wolfurt fruehlingstrasse" in result.rest_text
    assert result.status == "teilweise"
    pruefe_invarianten(raw, result)


def test_mehrdeutige_gemeinde(kontext):
    raw = "f26000001 f2 wolfurt buch > brand (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.gemeinde is None
    assert "gemeinde_mehrdeutig" in result.hinweise
    assert result.rest_text == "wolfurt buch"
    assert result.status == "teilweise"
    pruefe_invarianten(raw, result)


def test_unbekanntes_bleibt_erhalten(kontext):
    raw = "f26000001 f2 wolfurt FREMDSTUECK/XYZ > brand (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.rest_text == "FREMDSTUECK/XYZ"
    assert result.status == "teilweise"
    pruefe_invarianten(raw, result)


def test_alias_und_reihenfolge_als_daten(kontext):
    regeln = Regelsatz(version="7", gemeinde_aliase={"wft": "Wolfurt"},
                       strassen_aliase={"blumenweg": "fruehlingstrasse"})
    raw = "f26000001 f2 blumenweg 3 wft > brand (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext, regeln)
    assert result.gemeinde == "Wolfurt"
    assert result.strasse == "Frühlingstraße"
    assert result.hausnummer == "3"
    assert result.parser_version == "7"
    assert result.status == "ok"
    pruefe_invarianten(raw, result)
    regeln = regeln.model_copy(update={"reihenfolge_varianten": [["gemeinde", "strasse"]]})
    result = parse_alarm(raw, kontext, regeln)
    assert result.strasse is None
    assert "blumenweg" in result.rest_text
    assert "reihenfolge_unbekannt" in result.hinweise
    pruefe_invarianten(raw, result)


@pytest.mark.parametrize("datum", ["31.02.2026 12:00", "25.10.2026 02:30", "29.03.2026 02:30"])
def test_ungueltige_oder_mehrdeutige_zeit(datum, kontext):
    raw = f"f26000001 f2 wolfurt > brand ({datum} Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.alarmzeit is None
    assert result.status == "teilweise"
    assert "alarmzeit_ungueltig" in result.hinweise
    pruefe_invarianten(raw, result)


def test_normalisierung():
    raw = "\x00 Äpfel\f\r\n  Frühlingstr.  ß\x7f"
    assert normalisiere(raw) == "Äpfel Frühlingstr. ß"
    assert vergleichsform(raw) == "aepfel fruehlingstr ss"
    assert adressvergleich("Frühlingstr.") == adressvergleich("Fruehlingstrasse")


@pytest.mark.parametrize("muster", ["(a+)+", "(a*){2}", r"(a)\1", r"(?P<x>a)(?P=x)", "a" * 201,
                                   "[", "a+a+", "a{999999}", "a*", "(a|aa)+$"])
def test_unsichere_muster(muster):
    with pytest.raises(ValueError):
        validiere_muster(muster)
    with pytest.raises(ValueError):
        Regelsatz(stichwort_muster=muster)


@pytest.mark.parametrize("muster", [r"f\d{1,2}", r"r\d+", r"_[a-z]+"])
def test_gueltige_muster(muster):
    assert validiere_muster(muster) == muster


def test_regelsatz_json_roundtrip():
    assert Regelsatz.model_validate_json(STANDARD_REGELSATZ.model_dump_json()) == STANDARD_REGELSATZ


def test_parser_wirft_nie(kontext):
    zufall = random.Random(260026)
    alphabet = "abcft0123456789 _>,().:/\n\r\f\x00\x01ÿä\udcff"
    for _ in range(500):
        raw = "".join(zufall.choices(alphabet, k=zufall.randrange(250)))
        result = parse_alarm(raw, kontext)
        assert "parser_fehler" not in result.hinweise
        pruefe_invarianten(raw, result)


def test_doppelte_stichworte_nicht_raten(kontext):
    raw = "f26000001 f2 f3 wolfurt > brand (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.stichwort is None
    assert result.status == "unklar"
    assert result.rest_text == "f2 f3"
    assert "stichwort_mehrdeutig" in result.hinweise
    pruefe_invarianten(raw, result)


def test_doppelte_zeit_nicht_raten(kontext):
    raw = "f26000001 f2 wolfurt > brand (01.01.2026 12:00 Uhr) (01.01.2026 13:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.alarmzeit is None
    assert result.meldebild == "brand"
    assert result.rest_text == "(01.01.2026 12:00 Uhr) (01.01.2026 13:00 Uhr)"
    assert "alarmzeit_mehrdeutig" in result.hinweise
    pruefe_invarianten(raw, result)


def test_mehrdeutige_strasse(kontext):
    kontext.gemeinden["wolfurt"].strassen["fruehlingstr."] = "Andere Straße"
    raw = (DATEN / "normalalarm.txt").read_text()
    result = parse_alarm(raw, kontext)
    assert result.strasse is None
    assert result.hausnummer is None
    assert result.rest_text == "Frühlingstraße 7"
    assert "strasse_mehrdeutig" in result.hinweise
    pruefe_invarianten(raw, result)


def test_strassen_abkuerzung_und_steuerzeichen(kontext):
    raw = "\x00f26000001\r\nf2\twolfurt Frühlingstr. 7 >\fbrand (01.01.2026 12:00 Uhr)\x00"
    result = parse_alarm(raw, kontext)
    assert result.strasse == "Frühlingstraße"
    assert result.hausnummer == "7"
    assert result.status == "ok"
    assert result.rest_text == ""
    pruefe_invarianten(raw, result)


def test_org_zeitzone(kontext):
    kontext.zeitzone = "UTC"
    raw = (DATEN / "normalalarm.txt").read_text()
    result = parse_alarm(raw, kontext)
    assert result.alarmzeit.tzinfo is None
    assert result.to_dict()["alarmzeit"] == "2026-01-01T12:00:00"
    pruefe_invarianten(raw, result)


def test_bma_ohne_meldebild(kontext):
    raw = "f26000001 f14 wolfurt fruehlingstrasse 7 BMA 1234 Melder 2/3 Melderbereich 7 (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.bma == ["1234"]
    assert result.melder == "2/3"
    assert result.melderbereich == "7"
    assert result.rest_text == ""
    assert result.status == "ok"
    pruefe_invarianten(raw, result)


def test_melder_mehrdeutig(kontext):
    raw = "f26000001 f14 wolfurt > BMA 1234 Melder 2 Melder 3 (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.melder is None
    assert result.rest_text == "Melder 2 Melder 3"
    assert result.status == "teilweise"
    pruefe_invarianten(raw, result)


def test_unbekannte_stufe_verwendet_lis_fallback(kontext):
    raw = "f26000001 f99 wolfurt > brand (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.stichwort == "f99"
    assert result.alarm_type_code == "T1"
    pruefe_invarianten(raw, result)


def test_keine_datenbank_im_parser(monkeypatch, kontext):
    def verboten(*args, **kwargs):
        pytest.fail("Parser darf keine Datenbankverbindung oeffnen")

    monkeypatch.setattr("sqlalchemy.engine.Engine.connect", verboten)
    raw = (DATEN / "anhang_lauterach.txt").read_text()
    result = parse_alarm(raw, kontext)
    assert result.status == "ok"
    pruefe_invarianten(raw, result)


def test_teure_muster_werden_abgebrochen():
    # Keine verschachtelten Gruppen, dennoch kombinatorisches Backtracking.
    with pytest.raises(ValueError, match="50 ms"):
        validiere_muster("a{1,999}" * 12 + "b")


def test_strasse_ohne_gemeinde_mehrdeutig(kontext):
    kontext.gemeinden["buch"].strassen["fruehlingstrasse"] = "Frühlingstraße"
    raw = "f26000001 f2 fruehlingstrasse 7 > brand (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.strasse is None
    assert result.rest_text == "fruehlingstrasse 7"
    assert "strasse_mehrdeutig" in result.hinweise
    pruefe_invarianten(raw, result)


def test_eindeutige_strasse_ohne_gemeinde(kontext):
    raw = "f26000001 f2 fruehlingstrasse 7 > brand (01.01.2026 12:00 Uhr)"
    result = parse_alarm(raw, kontext)
    assert result.gemeinde is None
    assert result.strasse == "Frühlingstraße"
    assert result.hausnummer == "7"
    assert result.status == "ok"
    pruefe_invarianten(raw, result)


def test_parser_veraendert_keine_eingabedaten(kontext):
    from dataclasses import asdict

    vorher = asdict(kontext)
    regeln = STANDARD_REGELSATZ.model_dump()
    raw = (DATEN / "anhang_wolfurt.txt").read_text()
    eins = parse_alarm(raw, kontext)
    zwei = parse_alarm(raw, kontext)
    assert eins == zwei
    assert asdict(kontext) == vorher
    assert STANDARD_REGELSATZ.model_dump() == regeln
    pruefe_invarianten(raw, eins)
    pruefe_invarianten(raw, zwei)
