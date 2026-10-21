"""SQL-sichere Tabellennamen und SQLite ohne VECTOR-Unterstuetzung."""

from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.config import settings
from app.services.suche import vektor


@pytest.mark.parametrize("modell, slug", [
    ("embeddinggemma", "embeddinggemma"), ("EmbeddingGemma:latest", "embeddinggemma_latest"),
    ("a/b-c.d", "a_b_c_d"), ("x`; DROP TABLE y; --", "x_drop_table_y"),
])
def test_tabellenname(monkeypatch, modell, slug):
    monkeypatch.setattr(settings, "SEARCH_EMBED_MODEL", modell)
    monkeypatch.setattr(settings, "SEARCH_EMBED_DIM", 768)
    assert vektor.tabellenname() == f"search_vector_{slug}_768"


@pytest.mark.parametrize("modell, dim", [("!!!", 768), ("", 768), ("a" * 64, 768), ("a", 0), ("a", -1)])
def test_tabellenname_ungueltig(monkeypatch, modell, dim):
    monkeypatch.setattr(settings, "SEARCH_EMBED_MODEL", modell)
    monkeypatch.setattr(settings, "SEARCH_EMBED_DIM", dim)
    with pytest.raises(ValueError):
        vektor.tabellenname()


def test_sqlite_noop(monkeypatch):
    engine = sa.create_engine("sqlite://")
    try:
        with Session(engine) as db:
            execute = MagicMock(side_effect=AssertionError("SQLite darf kein Vektor-SQL ausfuehren"))
            monkeypatch.setattr(db, "execute", execute)
            assert not vektor.vektor_faehig(db)
            assert vektor.stelle_tabelle_sicher(db) is None
            assert vektor.speichere(db, 1, 2, [(3, "a" * 64, [1, 0])]) is None
            assert vektor.knn(db, 1, [1, 0], ["objekt"], 5) == []
            assert vektor.lade_hashes(db, 1, 2) == {}
            assert vektor.loesche_dokument(db, 1, 2) is None
            assert vektor.loesche_chunks(db, 1, [3]) is None
            execute.assert_not_called()
    finally:
        engine.dispose()


def test_knn_fallback_und_parameter(monkeypatch):
    monkeypatch.setattr(vektor, "vektor_faehig", lambda _: True)
    db = MagicMock()
    db.execute.return_value.all.side_effect = [[(10, 20, 0.0)], [(10, 20, 0.0), (11, 21, 0.2)]]
    assert vektor.knn(db, 987, [1, 0], ["objekt"], 2) == [(10, 20, 0.0), (11, 21, 0.2)]
    assert db.execute.call_count == 2
    index, exakt = db.execute.call_args_list
    assert "LIMIT :kandidaten)" in str(index.args[0])
    assert index.args[1]["kandidaten"] == 10
    assert "LIMIT :kandidaten" not in str(exakt.args[0])
    assert "IGNORE INDEX (embedding)" in str(exakt.args[0])
    for aufruf in (index, exakt):
        sql = str(aufruf.args[0])
        assert "v.org_id=:org" in sql and "d.org_id=:org" in sql and "c.org_id=:org" in sql
        assert "d.quelle IN" in sql
        assert "987" not in sql and "objekt" not in sql
        assert aufruf.args[1]["org"] == 987


@pytest.mark.parametrize("unterstuetzt", [True, False])
def test_faehigkeitsprobe_eigene_connection_und_cache(monkeypatch, unterstuetzt):
    engine = sa.create_engine("sqlite://")
    monkeypatch.setattr(engine.dialect, "name", "mariadb")
    verbindung = MagicMock()
    if not unterstuetzt:
        verbindung.execute.side_effect = sa.exc.OperationalError("Probe", {}, Exception("kein VECTOR"))
    connect = MagicMock()
    connect.return_value.__enter__.return_value = verbindung
    monkeypatch.setattr(engine, "connect", connect)
    db = MagicMock()
    db.get_bind.return_value = engine
    vektor.setze_faehigkeit_zurueck()
    try:
        assert vektor.vektor_faehig(db) is unterstuetzt
        assert vektor.vektor_faehig(db) is unterstuetzt
        assert connect.call_count == 1
        db.execute.assert_not_called()
        sql = str(verbindung.execute.call_args_list[0].args[0])
        assert "CREATE TEMPORARY TABLE" in sql and "VECTOR(3) NOT NULL" in sql
        if unterstuetzt:
            assert "DROP TEMPORARY TABLE" in str(verbindung.execute.call_args_list[1].args[0])
        vektor.setze_faehigkeit_zurueck()
        assert vektor.vektor_faehig(db) is unterstuetzt
        assert connect.call_count == 2
    finally:
        vektor.setze_faehigkeit_zurueck()
        engine.dispose()


def test_knn_genug_kandidaten_und_leere_auswahl(monkeypatch):
    monkeypatch.setattr(vektor, "vektor_faehig", lambda _: True)
    db = MagicMock()
    db.execute.return_value.all.return_value = [(10, 20, 0.1)]
    assert vektor.knn(db, 1, [1, 0], ["objekt"], 1) == [(10, 20, 0.1)]
    assert db.execute.call_count == 1
    db.execute.reset_mock()
    assert vektor.knn(db, 1, [1, 0], [], 1) == []
    assert vektor.knn(db, 1, [1, 0], ["objekt"], 0) == []
    db.execute.assert_not_called()
