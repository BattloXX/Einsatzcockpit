"""Tests für die Einsatz-Board-Anforderungen (2026-07-01):

- Neue Karten werden ganz oben in der Lane eingereiht (prepend_card, _ordered_col_items)
- Abschnittsleiter je Lane (Qualifikation EL/GK) — Modellfelder + Kandidaten-Query
"""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"

from app.core.tenant import set_tenant_context
from app.core.templating import _ordered_col_items
from app.db import Base
from app.models.incident import Incident, IncidentColumn, Message, RescuedPerson, Task
from app.models.master import FireDept, Member, MemberQualification, Qualification
from app.services.incident_service import list_section_leader_candidates, prepend_card

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    set_tenant_context(session, None)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def org(db):
    o = FireDept(slug="board-test", name="Board Test Org", color="#ff0000", bos="Feuerwehr")
    db.add(o)
    db.flush()
    return o


@pytest.fixture()
def incident(db, org):
    inc = Incident(primary_org_id=org.id, alarm_type_code="T1")
    db.add(inc)
    db.flush()
    return inc


# ── prepend_card: neue Karten ganz oben ──────────────────────────────────────

def test_prepend_card_without_existing_card_order(db, incident):
    """Erste prepend_card()-Aufrufe ohne bisheriges card_order bauen die Default-
    Reihenfolge aus der DB auf und stellen das neue Item ganz vorne ein."""
    col = IncidentColumn(incident_id=incident.id, code="tasks", title="Aufträge", column_kind="tasks")
    db.add(col)
    db.flush()

    t1 = Task(incident_id=incident.id, column_id=col.id, title="Alter Auftrag", display_order=0)
    db.add(t1)
    db.flush()

    t2 = Task(incident_id=incident.id, column_id=col.id, title="Neuer Auftrag", display_order=0)
    db.add(t2)
    db.flush()

    prepend_card(db, col.id, "task", t2.id)

    import json
    order = json.loads(col.card_order)
    assert order[0] == {"kind": "task", "id": t2.id}
    assert {"kind": "task", "id": t1.id} in order


def test_prepend_card_with_existing_card_order(db, incident):
    """Ein weiteres neues Item wird bei bereits vorhandenem card_order ebenfalls
    an Position 0 eingefügt (nicht angehängt)."""
    col = IncidentColumn(incident_id=incident.id, code="messages", title="Meldungen", column_kind="messages")
    db.add(col)
    db.flush()

    m1 = Message(incident_id=incident.id, column_id=col.id, title="Erste Meldung")
    db.add(m1)
    db.flush()
    prepend_card(db, col.id, "message", m1.id)

    m2 = Message(incident_id=incident.id, column_id=col.id, title="Zweite Meldung")
    db.add(m2)
    db.flush()
    prepend_card(db, col.id, "message", m2.id)

    import json
    order = json.loads(col.card_order)
    assert order[0] == {"kind": "message", "id": m2.id}
    assert order[1] == {"kind": "message", "id": m1.id}


def test_ordered_col_items_prepends_items_missing_from_card_order(db, incident):
    """Items, die noch nicht in card_order stehen (z. B. weil prepend_card aus
    irgendeinem Grund nicht aufgerufen wurde), muessen trotzdem ganz oben
    erscheinen statt ans Ende angehaengt zu werden."""
    col = IncidentColumn(incident_id=incident.id, code="tasks", title="Aufträge", column_kind="tasks")
    db.add(col)
    db.flush()

    t_old = Task(incident_id=incident.id, column_id=col.id, title="Alt")
    db.add(t_old)
    db.flush()

    import json
    col.card_order = json.dumps([{"kind": "task", "id": t_old.id}])

    t_new = Task(incident_id=incident.id, column_id=col.id, title="Neu (fehlt in card_order)")
    db.add(t_new)
    db.flush()

    result = _ordered_col_items(col, [], [t_old, t_new], [], [])
    kinds_ids = [(k, o.id) for k, o in result]
    assert kinds_ids[0] == ("task", t_new.id)
    assert kinds_ids[1] == ("task", t_old.id)


def test_prepend_card_no_column_id_is_noop(db):
    """prepend_card mit column_id=None darf nicht crashen (z. B. Person ohne Spalte)."""
    prepend_card(db, None, "person", 1)  # muss einfach nur nicht crashen


# ── Abschnittsleiter (Qualifikation EL/GK) ───────────────────────────────────

@pytest.fixture()
def qualifications(db):
    el = Qualification(code="EL", label="Einsatzleiter", is_einsatzleiter=True)
    gk = Qualification(code="GK", label="Gruppenkommandant", is_gruppenkommandant=True)
    db.add_all([el, gk])
    db.flush()
    return el, gk


def test_list_section_leader_candidates_union_of_el_and_gk(db, org, qualifications):
    el, gk = qualifications
    m_el = Member(org_id=org.id, lastname="Elini", firstname="Els")
    m_gk = Member(org_id=org.id, lastname="Gruber", firstname="Gustl")
    m_none = Member(org_id=org.id, lastname="Niemand", firstname="Nina")
    db.add_all([m_el, m_gk, m_none])
    db.flush()
    db.add(MemberQualification(member_id=m_el.id, qualification_id=el.id))
    db.add(MemberQualification(member_id=m_gk.id, qualification_id=gk.id))
    db.flush()

    candidates = list_section_leader_candidates(db, [org.id])
    candidate_ids = {c.id for c in candidates}

    assert m_el.id in candidate_ids
    assert m_gk.id in candidate_ids
    assert m_none.id not in candidate_ids


def test_incident_column_section_leader_member_relationship(db, incident, org):
    m = Member(org_id=org.id, lastname="Chef", firstname="Ab")
    db.add(m)
    db.flush()

    col = IncidentColumn(
        incident_id=incident.id, code="custom1", title="Abschnitt Nord",
        column_kind="vehicles", section_leader_member_id=m.id,
    )
    db.add(col)
    db.flush()
    db.refresh(col)

    assert col.section_leader is not None
    assert col.section_leader.id == m.id
    assert col.section_leader_name is None


def test_incident_column_section_leader_freitext(db, incident):
    col = IncidentColumn(
        incident_id=incident.id, code="custom2", title="Abschnitt Süd",
        column_kind="vehicles", section_leader_name="Externer Einsatzleiter",
    )
    db.add(col)
    db.flush()

    assert col.section_leader is None
    assert col.section_leader_name == "Externer Einsatzleiter"


# ── RescuedPerson: vehicle-Relationship (für Karte/PDF/Journal) ──────────────

def test_rescued_person_vehicle_relationship(db, incident, org):
    from app.models.incident import IncidentVehicle
    from app.models.master import VehicleMaster

    vm = VehicleMaster(dept_id=org.id, code="KDOF", name="Kommandofahrzeug")
    db.add(vm)
    db.flush()
    col = IncidentColumn(incident_id=incident.id, code="dispatched", title="Disponiert", column_kind="vehicles")
    db.add(col)
    db.flush()
    veh = IncidentVehicle(incident_id=incident.id, column_id=col.id, vehicle_master_id=vm.id)
    db.add(veh)
    db.flush()

    person = RescuedPerson(incident_id=incident.id, name="Max Muster", vehicle_id=veh.id, status="versorgt")
    db.add(person)
    db.flush()
    db.refresh(person)

    assert person.vehicle is not None
    assert person.vehicle.vehicle_master.code == "KDOF"
