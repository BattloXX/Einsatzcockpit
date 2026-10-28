"""Zentrale Fehlererkennung, Diagnose und begrenzte DB-Wiederholungen."""

from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError

from app.services import db_transactions as transaktionen


def konflikt(code):
    return OperationalError("SELECT private_data", {"secret": "private"}, Exception(code, "private details"))


@pytest.fixture(autouse=True)
def keine_wartezeit(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr(transaktionen.time, "sleep", sleep)
    return sleep


@pytest.mark.parametrize("code", [1020, 1205, 1213])
def test_bekannte_codes_rollback_vor_wiederholung(code, keine_wartezeit):
    db = Mock()
    ablauf = Mock()
    db.rollback.side_effect = ablauf.rollback
    funktion = Mock(side_effect=[konflikt(code), "fertig"])
    ablauf.attach_mock(funktion, "funktion")
    assert transaktionen.mit_wiederholung(db, funktion) == "fertig"
    assert [aufruf[0] for aufruf in ablauf.mock_calls] == ["funktion", "rollback", "funktion"]
    db.commit.assert_not_called()
    keine_wartezeit.assert_called_once()
    assert 0.01 <= keine_wartezeit.call_args.args[0] <= 0.019


@pytest.mark.parametrize("versuche", [1, 2, 3, 5])
def test_versuchsgrenze_und_originalfehler(versuche, keine_wartezeit):
    db = Mock()
    fehler = konflikt(1020)
    funktion = Mock(side_effect=fehler)
    with pytest.raises(OperationalError) as gefangen:
        transaktionen.mit_wiederholung(db, funktion, versuche=versuche)
    assert gefangen.value is fehler
    assert funktion.call_count == db.rollback.call_count == versuche
    assert keine_wartezeit.call_count == versuche - 1
    for index, aufruf in enumerate(keine_wartezeit.call_args_list, start=1):
        assert 0.01 * index <= aufruf.args[0] <= 0.01 * index + 0.009


@pytest.mark.parametrize("fehler", [
    konflikt(2006), konflikt(1064), konflikt("1020"),
    OperationalError("SELECT", {}, Exception()), ValueError("ungueltig"),
])
def test_andere_fehler_ohne_wiederholung(fehler, keine_wartezeit):
    db = Mock()
    funktion = Mock(side_effect=fehler)
    vorher = transaktionen.retry_statistik()
    with pytest.raises(type(fehler)) as gefangen:
        transaktionen.mit_wiederholung(db, funktion)
    assert gefangen.value is fehler
    funktion.assert_called_once()
    db.rollback.assert_not_called()
    keine_wartezeit.assert_not_called()
    assert transaktionen.retry_statistik() == vorher
    assert not transaktionen.ist_wiederholbar(fehler)


@pytest.mark.parametrize("code", [1205, 1213])
def test_engere_codeauswahl_wird_beibehalten(code, keine_wartezeit):
    db = Mock()
    funktion = Mock(side_effect=konflikt(code))
    with pytest.raises(OperationalError):
        transaktionen.mit_wiederholung(db, funktion, codes=(1020,))
    funktion.assert_called_once()
    db.rollback.assert_not_called()
    keine_wartezeit.assert_not_called()


def test_erfolg_ohne_wiederholung_oder_commit(keine_wartezeit):
    db = Mock()
    funktion = Mock(return_value="fertig")
    assert transaktionen.mit_wiederholung(db, funktion) == "fertig"
    funktion.assert_called_once()
    db.rollback.assert_not_called()
    db.commit.assert_not_called()
    keine_wartezeit.assert_not_called()


@pytest.mark.parametrize("versuche", [0, -1])
def test_ungueltige_versuchsgrenze(versuche):
    db, funktion = Mock(), Mock()
    with pytest.raises(ValueError):
        transaktionen.mit_wiederholung(db, funktion, versuche=versuche)
    funktion.assert_not_called()
    db.rollback.assert_not_called()


def test_zaehler_pro_code_auch_beim_letzten_versuch(caplog):
    vorher = transaktionen.retry_statistik()
    for code in (1020, 1205, 1213):
        with pytest.raises(OperationalError):
            transaktionen.mit_wiederholung(Mock(), Mock(side_effect=konflikt(code)), versuche=2)
    nachher = transaktionen.retry_statistik()
    for code in (1020, 1205, 1213):
        assert nachher.get(code, 0) == vorher.get(code, 0) + 2
    assert len(caplog.records) == 6
    assert all(record.levelname == "WARNING" for record in caplog.records)
    assert "private" not in caplog.text
    nachher.clear()
    assert transaktionen.retry_statistik()


def test_erkennung_zaehlt_konflikt_auch_ohne_retryfreigabe():
    vorher = transaktionen.retry_statistik().get(1205, 0)
    assert not transaktionen.ist_wiederholbar(konflikt(1205), codes=(1020,))
    assert transaktionen.retry_statistik().get(1205, 0) == vorher + 1
