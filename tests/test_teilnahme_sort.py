"""Tests fuer sortierbare Teilnehmerliste (_lade_teilnahmen sort-Parameter)."""
from contextlib import contextmanager

import pytest

from app.core.tenant import set_tenant_context
from app.models.master import Member
from app.models.teilnahme import Teilnahme
from app.routers.ui_termin import _lade_teilnahmen
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, 1)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _make_member(db, firstname, lastname) -> Member:
    m = Member(org_id=1, firstname=firstname, lastname=lastname)
    db.add(m)
    db.flush()
    return m


def _make_teilnahme(db, bezug_id, *, mitglied=None, freitext=None) -> Teilnahme:
    t = Teilnahme(
        org_id=1,
        bezug_typ="einsatz",
        bezug_id=bezug_id,
        mitglied_id=mitglied.id if mitglied else None,
        freitext_name=freitext,
    )
    db.add(t)
    db.flush()
    return t


def test_default_order_is_insertion_order():
    with _session() as db:
        bezug_id = 9001
        m_b = _make_member(db, "Bernd", "Berger")
        m_a = _make_member(db, "Anna", "Aigner")
        _make_teilnahme(db, bezug_id, mitglied=m_b)
        _make_teilnahme(db, bezug_id, mitglied=m_a)

        result = _lade_teilnahmen(db, "einsatz", bezug_id)

        assert [t.mitglied.firstname for t in result] == ["Bernd", "Anna"]
        db.rollback()


def test_sort_by_nachname():
    with _session() as db:
        bezug_id = 9002
        m_b = _make_member(db, "Bernd", "Berger")
        m_a = _make_member(db, "Anna", "Aigner")
        _make_teilnahme(db, bezug_id, mitglied=m_b)
        _make_teilnahme(db, bezug_id, mitglied=m_a)

        result = _lade_teilnahmen(db, "einsatz", bezug_id, sort="nachname")

        assert [t.mitglied.lastname for t in result] == ["Aigner", "Berger"]
        db.rollback()


def test_sort_by_vorname():
    with _session() as db:
        bezug_id = 9003
        m_b = _make_member(db, "Bernd", "Berger")
        m_a = _make_member(db, "Anna", "Aigner")
        _make_teilnahme(db, bezug_id, mitglied=m_b)
        _make_teilnahme(db, bezug_id, mitglied=m_a)

        result = _lade_teilnahmen(db, "einsatz", bezug_id, sort="vorname")

        assert [t.mitglied.firstname for t in result] == ["Anna", "Bernd"]
        db.rollback()


def test_sort_handles_freitext_entries():
    """Gast-Eintraege ohne Mitglied werden anhand freitext_name einsortiert."""
    with _session() as db:
        bezug_id = 9004
        m_b = _make_member(db, "Bernd", "Berger")
        _make_teilnahme(db, bezug_id, mitglied=m_b)
        _make_teilnahme(db, bezug_id, freitext="Anton Gast")

        result = _lade_teilnahmen(db, "einsatz", bezug_id, sort="nachname")

        names = [t.anzeige_name for t in result]
        assert names == ["Anton Gast", "Bernd Berger"]
        db.rollback()


def test_sort_is_case_insensitive():
    with _session() as db:
        bezug_id = 9005
        m_lower = _make_member(db, "anna", "zeller")
        m_upper = _make_member(db, "Bernd", "Aigner")
        _make_teilnahme(db, bezug_id, mitglied=m_lower)
        _make_teilnahme(db, bezug_id, mitglied=m_upper)

        result = _lade_teilnahmen(db, "einsatz", bezug_id, sort="nachname")

        assert [t.mitglied.lastname for t in result] == ["Aigner", "zeller"]
        db.rollback()


def test_invalid_sort_falls_back_to_insertion_order():
    with _session() as db:
        bezug_id = 9006
        m_b = _make_member(db, "Bernd", "Berger")
        m_a = _make_member(db, "Anna", "Aigner")
        _make_teilnahme(db, bezug_id, mitglied=m_b)
        _make_teilnahme(db, bezug_id, mitglied=m_a)

        result = _lade_teilnahmen(db, "einsatz", bezug_id, sort="unbekannt")

        assert [t.mitglied.firstname for t in result] == ["Bernd", "Anna"]
        db.rollback()
