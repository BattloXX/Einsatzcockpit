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
    # Amtlicher ADR-Name (Grossschreibung wie auf der Kennzeichnung): BENZIN
    assert "benzin" in (treffer[0]["stoffname"] or "").lower()
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
    assert any("benzin" in (t["stoffname"] or "").lower() for t in treffer)


def test_suche_stoffname_umlaut_tolerant():
    # "duesenkraftstoff" muss "DÜSENKRAFTSTOFF" (UN 1863) finden
    treffer = gg.suche("duesenkraftstoff")
    assert any("düsenkraftstoff" in (t["stoffname"] or "").lower() for t in treffer)


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


# ── BAM-Datenservice-Format (TAB-getrennt, S_-Spalten) ────────────────────────

def test_detect_delimiter():
    assert gg._detect_delimiter("a;b;c\n1;2;3") == ";"
    assert gg._detect_delimiter("a\tb\tc\n1\t2\t3") == "\t"


def test_parst_bam_tab_format(monkeypatch, tmp_path):
    """Rohes BAM-Format: TAB, S_-Spalten, mehrere Zeilen je UN, VP-Gruppe != VP-Anweisung."""
    header = "\t".join([
        "S_UNNR", "N_LFDNR", "S_VORSILBE", "S_NAME", "S_SPEZIFIKATION",
        "S_KLASSE", "S_KLASSIFIZIERUNGSCODE", "S_VP_GRUPPE",
        "S_VERPACKUNGSANW1", "S_GEFAHRNR",
    ])
    zeilen = [
        header,
        "1017\t1\t\tCHLOR\t\t2\t2TOC\t\tP200\t265",
        "0004\t1\t\tAMMONIUMPIKRAT\ttrocken\t1\t1.1D\t\tP112b\t",
        "0004\t2\t\tAMMONIUMPIKRAT\tangefeuchtet\t1\t1.1D\t\tP112a\t",
        "1789\t1\t\tCHLORWASSERSTOFFSAEURE\t\t8\tC1\tII\tP001\t80",
    ]
    datei = tmp_path / "bam_gefahrgut.csv"
    datei.write_text("\n".join(zeilen), encoding="utf-8")
    monkeypatch.setattr("app.config.settings.NACHSCHLAGEWERK_DATA_DIR", str(tmp_path))
    gg.invalidate_cache()
    try:
        assert gg._csv_pfad() == datei
        chlor = gg.eintrag_un("1017")
        assert chlor["stoffname"] == "CHLOR"
        assert chlor["gefahrnummer"] == "265"          # S_GEFAHRNR, weit hinten
        assert chlor["verpackungsgruppe"] is None       # leere S_VP_GRUPPE, nicht P200
        # Dedup: erste Spezifikation (trocken) gewinnt, Namenszusammenbau greift
        ap = gg.eintrag_un("0004")
        assert ap["stoffname"] == "AMMONIUMPIKRAT, trocken"
        # S_VP_GRUPPE (II) darf NICHT die Verpackungsanweisung (P001) treffen
        assert gg.eintrag_un("1789")["verpackungsgruppe"] == "II"
    finally:
        gg.invalidate_cache()
