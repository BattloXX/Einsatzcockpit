"""Service-Tests für die lagebezogene Personalpflege."""

from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import LageEinheit, LageEinheitPerson, LageJournalEntry, MajorIncident
from app.models.master import FireDept, Member
from app.services import ressource_pflege_service as service
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _daten(db):
    suffix = uuid4().hex[:8]
    org = FireDept(slug=f"pflege-{suffix}", name="Pflege", color="#123456", bos="Feuerwehr")
    fremd = FireDept(slug=f"pflege-fremd-{suffix}", name="Fremd", color="#654321", bos="Feuerwehr")
    db.add_all([org, fremd])
    db.flush()
    lage = MajorIncident(name="Personal", org_id=org.id)
    andere_lage = MajorIncident(name="Andere Lage", org_id=org.id)
    db.add_all([lage, andere_lage])
    db.flush()
    a = LageEinheit(lage_id=lage.id, label="RLF A")
    b = LageEinheit(lage_id=lage.id, label="TLF B")
    c = LageEinheit(lage_id=andere_lage.id, label="Andere")
    member = Member(org_id=org.id, firstname="Anna", lastname="Aktiv", active=True)
    member2 = Member(org_id=org.id, firstname="Berta", lastname="Zwei", active=True)
    fremdes_member = Member(org_id=fremd.id, firstname="Fremd", lastname="Person", active=True)
    db.add_all([a, b, c, member, member2, fremdes_member])
    db.flush()
    return lage, andere_lage, a, b, c, member, member2, fremdes_member


def _liste(db, lage, einheit):
    service.modus_wechseln(db, lage, einheit, "liste", user_id=1, author_name="EL")


def test_summen_setzen_validierung_noop_und_verstaerkung():
    with _session() as db:
        lage, _, a, _, _, _, _, _ = _daten(db)
        result = service.personal_setzen(
            db, lage, a, gesamt=4, fuehrung=1, agt=2, sanitaeter=0, user_id=1, author_name="EL"
        )
        assert result["alt"]["gesamt"] == 0 and result["neu"]["gesamt"] == 4
        assert (
            service.personal_setzen(
                db, lage, a, gesamt=4, fuehrung=1, agt=2, sanitaeter=0, user_id=1, author_name="EL"
            )["neu"]["gesamt"]
            == 4
        )
        db.flush()
        assert db.query(LageJournalEntry).filter_by(einheit_id=a.id, ereignis_typ="personal").count() == 1
        with pytest.raises(ValueError, match="Teilsummen"):
            service.personal_setzen(db, lage, a, gesamt=1, agt=2, user_id=1, author_name="EL")
        service.verstaerken(db, lage, a, anzahl=2, user_id=1, author_name="EL")
        assert a.staerke_gesamt == 6


def test_liste_mitglied_frei_doppelzuordnung_fremde_org_und_entfernen():
    with _session() as db:
        lage, _, a, b, _, member, _, fremdes_member = _daten(db)
        _liste(db, lage, a)
        person = service.person_hinzufuegen(
            db, lage, a, member_id=member.id, funktion="agt", user_id=1, author_name="EL"
        )
        frei = service.person_hinzufuegen(
            db, lage, a, name="Freie Person", funktion="sanitaeter", qualifikationen="SAN", user_id=1, author_name="EL"
        )
        assert person.herkunft == "stamm" and frei.aktiv_key is None and a.staerke_gesamt == 2
        _liste(db, lage, b)
        with pytest.raises(ValueError, match="RLF A"):
            service.person_hinzufuegen(
                db, lage, b, member_id=member.id, funktion="mannschaft", user_id=1, author_name="EL"
            )
        with pytest.raises(ValueError, match="Organisation"):
            service.person_hinzufuegen(
                db, lage, b, member_id=fremdes_member.id, funktion="mannschaft", user_id=1, author_name="EL"
            )
        service.person_entfernen(db, lage, a, person.id, user_id=1, author_name="EL")
        service.person_hinzufuegen(db, lage, b, member_id=member.id, funktion="mannschaft", user_id=1, author_name="EL")
        assert b.staerke_gesamt == 1


def test_modus_verstaerkung_abloesung_und_umbuchung_liste():
    with _session() as db:
        lage, _, a, b, _, member, member2, _ = _daten(db)
        _liste(db, lage, a)
        _liste(db, lage, b)
        service.verstaerken(
            db, lage, a, personen=[{"name": "Vera", "funktion": "fuehrung"}], user_id=1, author_name="EL"
        )
        old = service.person_hinzufuegen(db, lage, a, member_id=member.id, funktion="agt", user_id=1, author_name="EL")
        replacement = service.abloesen(db, lage, a, old.id, member_id=member2.id, user_id=1, author_name="EL")
        assert replacement.abloesung_von_id == old.id and old.bis_at is not None
        service.umbuchen(db, lage, a, b, person_ids=[replacement.id], user_id=1, author_name="EL")
        active = db.query(LageEinheitPerson).filter_by(lage_id=lage.id, member_id=member2.id, bis_at=None).all()
        assert len(active) == 1 and active[0].einheit_id == b.id and active[0].herkunft == "umbuchung"
        assert (
            db.query(LageJournalEntry)
            .filter_by(ereignis_typ="personal")
            .filter(LageJournalEntry.einheit_id.in_([a.id, b.id]))
            .count()
            >= 2
        )


def test_umbuchung_summe_ist_atom_ar_und_lagen_modus_pruefung():
    with _session() as db:
        lage, andere_lage, a, b, c, _, _, _ = _daten(db)
        service.personal_setzen(db, lage, a, gesamt=4, user_id=1, author_name="EL")
        service.umbuchen(db, lage, a, b, anzahl=2, user_id=1, author_name="EL")
        assert (a.staerke_gesamt, b.staerke_gesamt) == (2, 2)
        with pytest.raises(ValueError, match="Nicht genügend"):
            service.umbuchen(db, lage, a, b, anzahl=3, user_id=1, author_name="EL")
        assert (a.staerke_gesamt, b.staerke_gesamt) == (2, 2)
        with pytest.raises(ValueError, match="Lage"):
            service.umbuchen(db, lage, a, c, anzahl=1, user_id=1, author_name="EL")
        _liste(db, lage, b)
        with pytest.raises(ValueError, match="unterschiedlichen"):
            service.umbuchen(db, lage, a, b, anzahl=1, user_id=1, author_name="EL")
        assert andere_lage.id != lage.id


def test_kraefte_summen_zaehlen_verbandskinder_nur_einmal():
    with _session() as db:
        lage, _, a, b, _, _, _, _ = _daten(db)
        verband = LageEinheit(lage_id=lage.id, label="Verband", resource_type="verband", staerke_gesamt=99)
        db.add(verband)
        db.flush()
        a.verband_id, a.staerke_gesamt, a.staerke_agt = verband.id, 3, 1
        b.staerke_gesamt, b.staerke_sanitaeter = 2, 1
        assert service.kraefte_summen(db, lage) == {"gesamt": 5, "fuehrung": 0, "agt": 1, "sanitaeter": 1}
