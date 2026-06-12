"""PR 9 – Org-Isolations-Testmatrix.

Prüft, dass Org A keine Daten von Org B sieht und umgekehrt.

Coverage:
- TenantScoped-Modelle (Member, AlarmType): automatischer WHERE org_id-Filter via Session-Event
- Incident-Listing: _visible_incidents_q filtert korrekt auf primäre + kollaborative Orgs
- can_access_incident: Zugangsprüfung auf Einsatz-Ebene
- IncidentOrg (Kollaboration): kollaborative Orgs sehen freigegebene Einsätze
"""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"

from app.core.permissions import can_access_incident
from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentOrg
from app.models.master import AlarmType, FireDept, Member



TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture()
def db():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    set_tenant_context(session, None)  # System-Modus: kein Filter (explizit gesetzt)
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def two_orgs(db):
    org_a = FireDept(slug="iso-a", name="Iso Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="iso-b", name="Iso Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()
    return org_a, org_b


# ── TenantScoped: AlarmType ───────────────────────────────────────────────────

def test_alarm_type_scoped_to_org(db, two_orgs):
    org_a, org_b = two_orgs
    db.add(AlarmType(org_id=org_a.id, code="F1", label="Brand"))
    db.add(AlarmType(org_id=org_b.id, code="F2", label="Brand B"))
    db.flush()

    # No tenant context → sees all
    all_types = db.query(AlarmType).all()
    assert len(all_types) == 2

    # Org A context → only org A's alarm types
    set_tenant_context(db, org_a.id)
    org_a_types = db.query(AlarmType).all()
    assert len(org_a_types) == 1
    assert org_a_types[0].org_id == org_a.id

    # Org B context → only org B's alarm types
    set_tenant_context(db, org_b.id)
    org_b_types = db.query(AlarmType).all()
    assert len(org_b_types) == 1
    assert org_b_types[0].org_id == org_b.id

    # Cleanup tenant context
    set_tenant_context(db, None)


# ── TenantScoped: Member ──────────────────────────────────────────────────────

def test_member_scoped_to_org(db, two_orgs):
    org_a, org_b = two_orgs
    db.add(Member(org_id=org_a.id, lastname="Müller", firstname="Hans"))
    db.add(Member(org_id=org_a.id, lastname="Schmid", firstname="Anna"))
    db.add(Member(org_id=org_b.id, lastname="Huber", firstname="Max"))
    db.flush()

    set_tenant_context(db, org_a.id)
    members = db.query(Member).all()
    assert len(members) == 2
    assert all(m.org_id == org_a.id for m in members)

    set_tenant_context(db, org_b.id)
    members = db.query(Member).all()
    assert len(members) == 1
    assert members[0].org_id == org_b.id

    set_tenant_context(db, None)


def test_member_get_by_id_crosses_tenant_boundary(db, two_orgs):
    """db.get() bypasses TenantScoped filter — this is a known limitation.
    Routers must check org access explicitly when using db.get() on TenantScoped models.
    """
    org_a, org_b = two_orgs
    m_b = Member(org_id=org_b.id, lastname="Other", firstname="Org")
    db.add(m_b)
    db.flush()

    # With org A context, db.get() still returns org B's member (identity map bypass)
    set_tenant_context(db, org_a.id)
    # Note: db.get() may return the member despite tenant filter.
    # This is why routers must verify org_id manually on .get() results.
    fetched = db.get(Member, m_b.id)
    # Document the behavior: get() bypasses the filter
    if fetched is not None:
        # This is the known gap: routers should check fetched.org_id == user.org_id
        assert fetched.org_id == org_b.id  # confirms it's cross-org

    set_tenant_context(db, None)


# ── Incident: can_access_incident ─────────────────────────────────────────────

class _FakeUser:
    def __init__(self, org_id, roles=("incident_leader",)):
        self.org_id = org_id
        self.roles = [type("R", (), {"code": r})() for r in roles]
        self.is_system_admin = "system_admin" in roles


def _fake_incident(primary_org_id, collab_org_ids=()):
    """SimpleNamespace-Stub für can_access_incident-Tests (kein SA-Instrumentation nötig)."""
    from types import SimpleNamespace
    collab = [SimpleNamespace(org_id=oid) for oid in collab_org_ids]
    return SimpleNamespace(primary_org_id=primary_org_id, collaborating_orgs=collab)


def test_can_access_own_incident(two_orgs):
    org_a, org_b = two_orgs
    inc = _fake_incident(org_a.id)
    user = _FakeUser(org_a.id)
    assert can_access_incident(user, inc) is True


def test_cannot_access_other_org_incident(two_orgs):
    org_a, org_b = two_orgs
    inc = _fake_incident(org_b.id)
    user = _FakeUser(org_a.id)
    assert can_access_incident(user, inc) is False


def test_system_admin_can_access_any_incident(two_orgs):
    org_a, org_b = two_orgs
    inc = _fake_incident(org_b.id)
    user = _FakeUser(org_a.id, roles=("system_admin",))
    assert can_access_incident(user, inc) is True


def test_collaborating_org_can_access_incident(db, two_orgs):
    org_a, org_b = two_orgs
    inc = Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active")
    db.add(inc)
    db.flush()
    db.add(IncidentOrg(incident_id=inc.id, org_id=org_b.id, role="collaborator"))
    db.flush()
    db.refresh(inc, ["collaborating_orgs"])

    user = _FakeUser(org_b.id)
    assert can_access_incident(user, inc) is True


# ── _visible_incidents_q ──────────────────────────────────────────────────────

def test_visible_incidents_filters_by_primary_org(db, two_orgs):
    from app.routers.ui_incident import _visible_incidents_q
    org_a, org_b = two_orgs

    inc_a = Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active")
    inc_b = Incident(primary_org_id=org_b.id, alarm_type_code="T2", status="active")
    db.add_all([inc_a, inc_b])
    db.flush()

    user = _FakeUser(org_a.id)
    results = _visible_incidents_q(db, user).all()
    assert len(results) == 1
    assert results[0].primary_org_id == org_a.id


def test_visible_incidents_includes_collaborating(db, two_orgs):
    from app.routers.ui_incident import _visible_incidents_q
    org_a, org_b = two_orgs

    inc_a = Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active")
    db.add(inc_a)
    db.flush()

    # Org B is invited and accepted
    db.add(IncidentOrg(incident_id=inc_a.id, org_id=org_b.id, role="collaborator"))
    db.flush()

    user = _FakeUser(org_b.id)
    results = _visible_incidents_q(db, user).all()
    assert len(results) == 1
    assert results[0].id == inc_a.id


def test_visible_incidents_no_cross_org_leakage(db, two_orgs):
    from app.routers.ui_incident import _visible_incidents_q
    org_a, org_b = two_orgs

    # Two incidents, one per org, no collaboration
    db.add(Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active"))
    db.add(Incident(primary_org_id=org_b.id, alarm_type_code="T2", status="active"))
    db.flush()

    user_a = _FakeUser(org_a.id)
    user_b = _FakeUser(org_b.id)

    results_a = _visible_incidents_q(db, user_a).all()
    results_b = _visible_incidents_q(db, user_b).all()

    assert len(results_a) == 1
    assert len(results_b) == 1
    assert results_a[0].primary_org_id == org_a.id
    assert results_b[0].primary_org_id == org_b.id


def test_visible_incidents_system_admin_sees_all(db, two_orgs):
    from app.routers.ui_incident import _visible_incidents_q
    org_a, org_b = two_orgs

    db.add(Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active"))
    db.add(Incident(primary_org_id=org_b.id, alarm_type_code="T2", status="active"))
    db.flush()

    admin = _FakeUser(org_a.id, roles=("system_admin",))
    results = _visible_incidents_q(db, admin).all()
    assert len(results) == 2


def test_visible_incidents_no_org_user_sees_nothing(db, two_orgs):
    from app.routers.ui_incident import _visible_incidents_q
    org_a, _ = two_orgs

    db.add(Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active"))
    db.flush()

    user_no_org = _FakeUser(None)
    results = _visible_incidents_q(db, user_no_org).all()
    assert len(results) == 0
