"""Tests für die idempotente Gruppenkommandanten-Zuweisung."""
from contextlib import contextmanager

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, LageEinheitLeader, LageJournalEntry, MajorIncident
from app.models.master import FireDept, Member
from app.models.user import Role, User, UserRole
from app.services import resource_service as rs
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
    org = FireDept(slug="gk-org", name="GK Org", color="#ff0000", bos="Feuerwehr")
    fremd = FireDept(slug="gk-fremd", name="Fremd", color="#00ff00", bos="Feuerwehr")
    db.add_all([org, fremd])
    db.flush()
    lage = MajorIncident(name="GK-Test", org_id=org.id)
    db.add(lage)
    db.flush()
    einheit = LageEinheit(lage_id=lage.id, label="TLF", resource_type="fahrzeug", status=rs.STATUS_BEREITGESTELLT)
    member = Member(org_id=org.id, firstname="Maria", lastname="Muster", phone="0664 123 45 67", active=True)
    fremdes_member = Member(org_id=fremd.id, firstname="Fremd", lastname="Mitglied", active=True)
    db.add_all([einheit, member, fremdes_member])
    db.flush()
    return lage, einheit, member, fremdes_member


def test_mitglied_extern_noop_telefon_wechsel_und_entfernen():
    with _session() as db:
        lage, einheit, member, _ = _daten(db)
        result = rs.setze_gruppenkommandant(db, lage, einheit, member_id=member.id)
        assert result.aenderung == "neu"
        leader = result.leader
        assert leader and leader.person_name == "Maria Muster"
        assert leader.phone_e164 == "+436641234567"

        assert rs.setze_gruppenkommandant(db, lage, einheit, member_id=member.id).aenderung == "keine"
        db.flush()
        assert db.query(LageEinheitLeader).filter_by(einheit_id=einheit.id).count() == 1
        assert db.query(LageJournalEntry).filter_by(einheit_id=einheit.id).count() == 1

        changed = rs.setze_gruppenkommandant(db, lage, einheit, member_id=member.id, telefon="+43 650 1112233")
        assert changed.aenderung == "telefon"
        assert changed.leader is leader and leader.phone_version == 2
        assert db.query(LageEinheitLeader).filter_by(einheit_id=einheit.id).count() == 1

        external = rs.setze_gruppenkommandant(db, lage, einheit, person_name="  Extern Frau  ")
        assert external.aenderung == "wechsel"
        assert leader.end_at is not None and leader.ende_grund == "wechsel"
        assert external.leader and external.leader.predecessor_id == leader.id

        rs.entferne_gruppenkommandant(db, lage, einheit, user_id=7, author_name="EL")
        assert einheit.leader_assignment_id is None and einheit.commander_label is None
        assert external.leader.end_at is not None and external.leader.ende_grund == "entfernt"


def test_ungueltige_nummer_fremdes_mitglied_und_stellvertreter():
    with _session() as db:
        lage, einheit, member, fremdes_member = _daten(db)
        with pytest.raises(ValueError, match="Telefonnummer ungueltig"):
            rs.setze_gruppenkommandant(db, lage, einheit, person_name="Extern", telefon="ungültig")
        with pytest.raises(ValueError, match="Mitglied"):
            rs.setze_gruppenkommandant(db, lage, einheit, member_id=fremdes_member.id)

        deputy = rs.setze_stellvertreter(db, lage, einheit, member_id=member.id)
        assert deputy.aenderung == "neu"
        assert deputy.leader and deputy.leader.rolle == "stellvertreter"
        assert einheit.leader_assignment_id is None


def _login(client, username):
    client.get("/login")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_legacy_kommandant_noop_und_wechsel(client, monkeypatch):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug="gk-route", name="GK Route", color="#ff0000", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(
            username="gk-route", password_hash=hash_password("Test1234!"),
            display_name="EL", org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="recorder").one().id))
        lage = MajorIncident(name="Route", org_id=org.id)
        db.add(lage)
        db.flush()
        einheit = LageEinheit(lage_id=lage.id, label="RLF", resource_type="fahrzeug", status=rs.STATUS_BEREITGESTELLT)
        db.add(einheit)
        db.commit()
        lage_id, einheit_id, username = lage.id, einheit.id, user.username
    finally:
        db.close()

    events = []

    async def fake_broadcast(lage_id, event):
        events.append((lage_id, event))

    import app.routers.ui_major_incident as router
    monkeypatch.setattr(router, "broadcast_lage", fake_broadcast)
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    url = f"/lage/{lage_id}/einheiten/{einheit_id}/kommandant"
    for name in ("Anna Beispiel", "Anna Beispiel", "Berta Wechsel"):
        response = client.post(url, data={"_csrf": csrf, "commander_label": name})
        assert response.status_code == 204

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        leaders = db.query(LageEinheitLeader).filter_by(einheit_id=einheit_id).order_by(LageEinheitLeader.id).all()
        assert len(leaders) == 2 and leaders[0].end_at is not None
        assert len(db.query(LageJournalEntry).filter_by(einheit_id=einheit_id).all()) == 2
    finally:
        db.close()
    assert events[-1] == (lage_id, {"type": "ressource:changed", "einheit_id": einheit_id})
