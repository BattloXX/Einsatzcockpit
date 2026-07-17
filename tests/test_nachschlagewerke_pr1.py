"""Nachschlagewerke PR 1: Gefahrgut-Suche (UN + Stoffname) + ERI-Eintrag."""
from app.services import gefahrgut_service as gg


def test_suche_leer_gibt_leere_liste():
    assert gg.suche("") == []
    assert gg.suche("   ") == []
    assert gg.suche(None) == []


def test_suche_un_exakt():
    treffer = gg.suche("1203")
    assert treffer, "UN 1203 (Benzin) sollte im Seed sein"
    assert treffer[0]["un_vierstellig"] == "1203"
    assert "Benzin" in (treffer[0]["stoffname"] or "")
    # Deep-Links (ERICard/BAM) sind angehaengt
    assert treffer[0]["links"]


def test_suche_un_mit_praefix_und_fuehrenden_nullen():
    # "UN 1203" und "01203" muessen denselben Treffer liefern
    a = gg.suche("UN 1203")
    b = gg.suche("01203")
    assert a and b
    assert a[0]["un_vierstellig"] == "1203"
    assert b[0]["un_vierstellig"] == "1203"


def test_suche_un_praefix_mehrere():
    # "12" als Praefix trifft mehrere UN-Nummern (1202, 1203, 1223, 1219 ...)
    treffer = gg.suche("12")
    uns = {t["un_vierstellig"] for t in treffer}
    assert "1203" in uns
    assert len(uns) >= 2


def test_suche_stoffname_substring_case_insensitiv():
    treffer = gg.suche("benzin")
    assert any("Benzin" in (t["stoffname"] or "") for t in treffer)


def test_suche_stoffname_umlaut_tolerant():
    # "oel" muss "Heizöl" / "Dieselkraftstoff / Heizöl" finden
    treffer = gg.suche("heizoel")
    assert any("Heizöl" in (t["stoffname"] or "") for t in treffer)


def test_suche_limit():
    treffer = gg.suche("1", limit=3)
    assert len(treffer) <= 3


def test_eintrag_un_gefunden_und_nicht():
    e = gg.eintrag_un("1203")
    assert e is not None
    assert e["un_vierstellig"] == "1203"
    assert e["links"]
    assert gg.eintrag_un("9999999") is None


def test_norm_name_helper():
    assert gg._norm_name("Ätzend") == "aetzend"
    assert gg._norm_name("STRASSE") == gg._norm_name("Straße")


def test_csv_pfad_faellt_auf_seed_zurueck():
    # Ohne gesyncte Datei muss der gebuendelte Seed aktiv sein.
    assert gg._csv_pfad() == gg._SEED_CSV_PFAD
