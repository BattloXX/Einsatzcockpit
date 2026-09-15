"""Tests fuer die gemeinsame Telefonnummernlogik."""

import pytest

from app.core.telefon import ist_oesterreichische_mobilnummer


@pytest.mark.parametrize("vorwahl", [650, 653, 655, 657, 659, 661, 663, 699])
@pytest.mark.parametrize("format", ["0{vorwahl}123456", "+43{vorwahl}123456", "0043{vorwahl}123456"])
def test_erkennt_oesterreichische_mobilnummern_in_ueblichen_formaten(vorwahl, format):
    assert ist_oesterreichische_mobilnummer(format.format(vorwahl=vorwahl))


@pytest.mark.parametrize(
    "wert",
    ["0654123456", "+43656123456", "0043658123456", "0662123456", "0700123456", "05574123456", "", None],
)
def test_lehnt_nicht_vorgesehene_oder_ungueltige_nummern_ab(wert):
    assert not ist_oesterreichische_mobilnummer(wert)
