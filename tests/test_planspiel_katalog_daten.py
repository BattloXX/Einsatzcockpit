"""Konsistenztests fuer den statischen Planspiel-Katalog."""

import re

from app.data import lfv_stichworte_v03
from app.data.planspiel_katalog import BAUSTEINE, GSL_WELLEN, ORT_TYPEN, SZENARIEN, TEMPLATE_VARIABLEN

_PLACEHOLDER = re.compile(r"{([a-z_]+)}")
_ADRESS_VARIABLEN = {"adresse", "strasse", "hausnr", "ort", "objekt", "einheit", "meldezeit"}


def test_szenarien_sind_vollstaendig_und_eindeutig():
    assert len(SZENARIEN) == 57
    assert len({szenario.code for szenario in SZENARIEN}) == 57
    f30 = next(szenario for szenario in SZENARIEN if szenario.code == "F30")
    assert not f30.zufall_erlaubt


def test_szenarien_verweisen_auf_gueltige_bausteine_und_ortstypen():
    for szenario in SZENARIEN:
        if szenario.code == "F30":
            assert not szenario.bausteine
        else:
            assert 2 <= len(szenario.bausteine) <= 4
        assert all(code in BAUSTEINE for code in szenario.bausteine)
        assert all(ort_typ in ORT_TYPEN for ort_typ in szenario.ort_typen)


def test_stichworte_und_templates_sind_konsistent():
    codes = {stichwort.code for stichwort in lfv_stichworte_v03.STICHWORTE}
    for szenario in SZENARIEN:
        assert szenario.stichwort.lower() in codes
        _assert_variablen(szenario.alarmtext_template, {}, require_auswahl=False)
        for text in (szenario.text_overrides or {}).values():
            _assert_variablen(text, {}, require_auswahl=False)
    for baustein in BAUSTEINE.values():
        _assert_variablen(baustein.text, baustein.variablen)


def _assert_variablen(
    text: str, auswahlwerte: dict[str, tuple[str, ...]], *, require_auswahl: bool = True
) -> None:
    for variable in _PLACEHOLDER.findall(text):
        assert variable in TEMPLATE_VARIABLEN
        if require_auswahl and variable not in _ADRESS_VARIABLEN:
            assert auswahlwerte.get(variable)


def test_katalog_regeln_und_gsl_wellen():
    nach_code = {szenario.code: szenario for szenario in SZENARIEN}
    assert all(nach_code[code].requires_verified_bma for code in ("B20", "B21"))
    assert all(not nach_code[code].zufall_erlaubt for code in ("B23", "B24", "T25", "F30"))
    assert all(nach_code[code].geografie_bedingung for code in ("B25", "T26"))
    gsl_codes = ("G01", "G02", "G03", "G04", "G05")
    assert all(nach_code[code].kategorie == "gsl" and nach_code[code].gsl_modus for code in gsl_codes)
    assert all(code in nach_code for wellen in GSL_WELLEN.values() for welle in wellen for code in welle.stellen_codes)


def test_lfv_referenz():
    assert len(lfv_stichworte_v03.STICHWORTE) == 19
    assert lfv_stichworte_v03.finde("F14").beschreibung.startswith("Eine Brandmeldeanlage")
