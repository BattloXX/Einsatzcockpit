"""Evaluation ohne eigene DB-Fixture oder echten Embedding-Provider."""

import csv
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.suche.vertrag import SuchErgebnis, Treffer
from scripts import suche_eval as ev


def frage(**werte) -> ev.Frage:
    return ev.Frage.model_validate({"nr": 1, "gruppe": "objekt", "erwartete_quelle": "objekt",
                                    "frage": "Geheimer Fragetext?", **werte})


def treffer(quelle="objekt", titel="Geheimer Treffertitel", grund=None) -> Treffer:
    return Treffer(quelle=quelle, quell_id="1", titel=titel, url="/test", grund=grund or ["volltext"])


@pytest.mark.parametrize("label", ["WERK STRASSE", ["Unpassend", "WERK STRASSE"]])
def test_streng_casefold_teilstring_und_erster_rang(label):
    wert = ev.bewerte(frage(soll_titel=label),
                      [treffer(), treffer(titel="Plan Werk Straße 12"), treffer(titel="Werk Straße")], 10)
    assert wert.streng
    assert wert.rang == 2
    assert wert.reziproker_rang == .5


def test_streng_keine_quellenersatzbewertung_und_top_n():
    f = frage(soll_titel="Solltitel")
    assert ev.bewerte(f, [treffer()], 10).rang is None
    assert ev.bewerte(f, [treffer(), treffer(titel="Solltitel")], 1).rang is None
    # Strenge Bewertung bezieht sich allein auf den Titel, unabhaengig vom Quellentyp.
    assert ev.bewerte(f, [treffer(quelle="katplan_seite", titel="Solltitel")], 10).rang == 1


@pytest.mark.parametrize(("erwartet", "geliefert", "rang"), [
    ("objekt", "objekt", 1), ("objekt", "objekt_dokument_seite", 1),
    ("katplan_element", "katplan_seite", 1), ("katplan_element", "katplan_element", 1),
    ("strassensperre", "strassensperre", 1), ("wasserstelle", "wasserstelle", 1),
    ("einsatz", "einsatz", 1), ("kontakt", "kontakt", 1),
    ("kontakt", "objekt", None), ("einsatz", "einsatz_meldung", None),
    ("katplan_seite", "katplan_element", None),
])
def test_schwach_quellen_und_alias(erwartet, geliefert, rang):
    wert = ev.bewerte(frage(erwartete_quelle=erwartet), [treffer(quelle=geliefert)], 10)
    assert not wert.streng
    assert wert.rang == rang


def test_kein_treffer_und_mrr():
    wert = ev.bewerte(frage(), [], 10)
    assert wert.rang is None
    assert wert.reziproker_rang == 0
    assert wert.trefferzahl == 0
    assert ev.bewerte(frage(), [treffer(quelle="kontakt"), treffer()], 10).reziproker_rang == .5


def test_aggregation_gruppen_gesamt_anteile_quantile():
    werte = [ev.Bewertung(1, "objekt", True, 2, 2, 10),
             ev.Bewertung(2, "objekt", False, None, 0, 20, True),
             ev.Bewertung(21, "hochwasserplan", True, 1, 1, 100)]
    a = ev.aggregiere(werte)
    assert set(a) == {"objekt", "hochwasserplan", "gesamt"}
    assert a["objekt"] == ev.Aggregat(2, .5, .25, .5, .5, 10, 20, 1)
    assert a["hochwasserplan"].mrr == 1
    assert a["gesamt"].fragen == 3
    assert a["gesamt"].recall == pytest.approx(2 / 3)
    assert a["gesamt"].mrr == .5
    assert a["gesamt"].streng == pytest.approx(2 / 3)
    assert a["gesamt"].schwach == pytest.approx(1 / 3)
    assert a["gesamt"].p50_ms == 20
    assert a["gesamt"].p95_ms == 100
    assert ev.aggregiere([]) == {}


def test_jsonl_korpus_100_eindeutige_nummern_und_gruppen():
    pfad = Path(__file__).resolve().parents[1] / "scripts/data/suche_fragen.jsonl"
    fragen = ev.lade_fragen(pfad)
    assert len(pfad.read_text(encoding="utf-8").splitlines()) == len(fragen) == 100
    assert len({f.nr for f in fragen}) == 100
    assert {f.nr for f in fragen} == set(range(1, 101))
    for f in fragen:
        erwartet = ("objekt" if f.nr <= 20 else "hochwasserplan" if f.nr <= 40 else
                    "gasplan" if f.nr <= 60 else "sperre" if f.nr <= 70 else
                    "wasserstelle" if f.nr <= 80 else "einsatz" if f.nr <= 90 else "kontakt")
        assert f.gruppe == erwartet


@pytest.mark.parametrize("inhalt", ["", "Geheimer Fragetext", "{}", '{"frage":"Geheim"}',
                                   json.dumps(frage().model_dump()) + "\n" + json.dumps(frage().model_dump()),
                                   json.dumps(frage().model_dump() | {"soll_titel": ""}),
                                   json.dumps(frage().model_dump() | {"soll_titel": []})])
def test_jsonl_fehler_ohne_inhalte(tmp_path, inhalt):
    pfad = tmp_path / "fragen.jsonl"
    pfad.write_text(inhalt, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ev.lade_fragen(pfad)
    assert "Geheim" not in str(exc.value)


def test_standardausgabe_nur_zahlen_und_gruppen():
    ausgabe = io.StringIO()
    f = frage(soll_titel="Geheimer Treffertitel")
    ev.schreibe_ausgabe({"hybrid": [ev.bewerte(f, [treffer()], 10),
                                   ev.bewerte(frage(nr=2), [], 10)]}, 10, ausgabe)
    text = ausgabe.getvalue()
    assert f.frage not in text
    assert "Geheimer Treffertitel" not in text
    assert "Recall@10;MRR" in text
    assert "hybrid;gesamt;2;0.5000;0.5000" in text
    assert "hybrid;ohne_Treffer;2\n" in text


def test_review_top5_escaping_und_leere_ergebnisse():
    ausgabe = io.StringIO()
    f = frage(frage='Frage; mit "Zitat"\nzweizeilig')
    ev.schreibe_review(ausgabe, [("hybrid", f, [treffer(titel=f"Titel;{i}") for i in range(7)]),
                                ("semantisch", f, [])])
    zeilen = list(csv.DictReader(io.StringIO(ausgabe.getvalue()), delimiter=";"))
    assert len(zeilen) == 6
    assert zeilen[0]["frage"] == f.frage
    assert zeilen[0]["titel"] == "Titel;0"
    assert zeilen[4]["rang"] == "5"
    assert zeilen[0]["grund"] == "volltext"
    assert zeilen[0]["distanz"] == ""
    assert zeilen[5]["modus"] == "semantisch"
    assert zeilen[5]["rang"] == ""


@pytest.fixture
def modus_umgebung(monkeypatch):
    monkeypatch.setattr(ev, "kontakte_modul_aktiv", lambda *a: False)
    monkeypatch.setattr(ev.vektor, "vektor_faehig", lambda db: True)
    provider = Mock()
    monkeypatch.setattr(ev.embedding, "EmbeddingProvider", lambda: provider)
    return Mock(spec=Session), User(id=42, org_id=1), provider


@pytest.mark.parametrize("modus", ev.MODI)
def test_modi_flag_rangfolge_filter_und_wiederherstellung(monkeypatch, modus_umgebung, modus):
    db, user, provider = modus_umgebung
    monkeypatch.setattr(ev.settings, "SEARCH_SEMANTIC_ENABLED", False)
    kandidaten = [treffer(grund=["volltext"]), treffer(titel="Soll", grund=["volltext", "semantisch"]),
                  treffer(titel="Dritter", grund=["semantisch"])]

    def suche(*a, **kw):
        assert ev.settings.SEARCH_SEMANTIC_ENABLED == (modus != "volltext")
        assert kw["kontakt_zugriff"] == "keiner"
        return SuchErgebnis(treffer=kandidaten, modi=["semantisch"])

    monkeypatch.setattr(ev.abfrage, "suche", suche)
    werte, review = ev.evaluiere_modus(db, user, [frage(soll_titel="Soll")], modus, 10)
    assert not ev.settings.SEARCH_SEMANTIC_ENABLED
    assert werte[0].rang == (1 if modus == "semantisch" else 2)
    assert list(review[0][2]) == (kandidaten[1:] if modus == "semantisch" else kandidaten)
    assert provider.embed_anfrage.call_count == (0 if modus == "volltext" else 1)


@pytest.mark.parametrize("ausfall", ["db", "provider", "stufe", "breaker", "index"])
def test_semantikausfall_bricht_modus_ab(monkeypatch, modus_umgebung, ausfall):
    db, user, provider = modus_umgebung
    monkeypatch.setattr(ev.settings, "SEARCH_SEMANTIC_ENABLED", False)
    if ausfall == "db":
        monkeypatch.setattr(ev.vektor, "vektor_faehig", lambda db: False)
    if ausfall == "provider":
        provider.embed_anfrage.side_effect = ev.embedding.EmbeddingFehler("Geheime Details")
    ergebnis = SuchErgebnis(modi=["semantisch"], hinweise=["Semantik nicht verfügbar"])
    if ausfall in ("breaker", "index"):
        ergebnis = SuchErgebnis()
    monkeypatch.setattr(ev.abfrage, "suche", lambda *a, **kw: ergebnis)
    with pytest.raises(ev.ModusFehler) as exc:
        ev.evaluiere_modus(db, user, [frage()], "hybrid", 10)
    assert "Geheim" not in str(exc.value)
    assert not ev.settings.SEARCH_SEMANTIC_ENABLED


def test_cli_fortsetzung_nach_modusabbruch_und_distanz(monkeypatch, tmp_path, capsys):
    pfad = tmp_path / "fragen.jsonl"
    pfad.write_text(json.dumps(frage().model_dump()), encoding="utf-8")
    review = tmp_path / "review.csv"
    db = MagicMock(spec=Session)
    db.__enter__.return_value = db
    db.scalar.return_value = User(id=42, org_id=1)
    monkeypatch.setattr(ev, "SessionLocal", lambda: db)
    monkeypatch.setattr(ev, "set_tenant_context", lambda *a: None)
    monkeypatch.setattr(ev.logging, "disable", lambda *a: None)
    vorher = ev.settings.SEARCH_SEMANTIC_MAX_DISTANZ

    def modus_lauf(db, user, fragen, modus, limit):
        assert ev.settings.SEARCH_SEMANTIC_MAX_DISTANZ == .4
        if modus == "hybrid":
            raise ev.ModusFehler("Provider nicht erreichbar")
        return [ev.bewerte(fragen[0], [], limit)], [(modus, fragen[0], [])]

    monkeypatch.setattr(ev, "evaluiere_modus", modus_lauf)
    monkeypatch.setattr(ev.sys, "argv", ["suche_eval.py", "--org", "1", "--user", "42", "--fragen", str(pfad),
                                       "--modi", "hybrid,volltext", "--max-distanz", ".4",
                                       "--review-csv", str(review)])
    assert ev.main() == 1
    assert ev.settings.SEARCH_SEMANTIC_MAX_DISTANZ == vorher
    out, err = capsys.readouterr()
    assert "volltext;gesamt;1" in out
    assert "hybrid: abgebrochen: Provider nicht erreichbar" in err
    assert "Geheimer" not in out + err
    assert "Geheimer Fragetext?" in review.read_text(encoding="utf-8")
