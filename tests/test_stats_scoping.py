"""PR 2 – Statistik-Aggregate-Queries Org-Scoping.

Stellt sicher, dass _apply_org_scope() die COUNT-Queries korrekt filtert,
da with_loader_criteria bei Aggregat-Queries nicht greift (Kategorie C).
"""
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

import pytest

# BigInteger → INTEGER für SQLite
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentOrg
from app.models.master import FireDept
from app.routers.ui_stats import _apply_org_scope

TEST_DB_URL = "sqlite:///:memory:"


class _FakeRole:
    def __init__(self, code):
        self.code = code


class _FakeUser:
    def __init__(self, org_id, roles=()):
        self.org_id = org_id
        self.roles = [_FakeRole(r) for r in roles]


@pytest.fixture()
def stats_db():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)

    org_a = FireDept(slug="st-a", name="Stats Org A", color="#f00", bos="Feuerwehr")
    org_b = FireDept(slug="st-b", name="Stats Org B", color="#00f", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    org_a_id = org_a.id
    org_b_id = org_b.id

    # Org A: 2 Einsätze, 1 Übung
    db.add(Incident(primary_org_id=org_a_id, alarm_type_code="B1", status="closed", is_exercise=False))
    db.add(Incident(primary_org_id=org_a_id, alarm_type_code="B2", status="closed", is_exercise=False))
    db.add(Incident(primary_org_id=org_a_id, alarm_type_code="T1", status="closed", is_exercise=True))
    db.flush()

    # Org B: 1 Einsatz
    inc_b = Incident(primary_org_id=org_b_id, alarm_type_code="T2", status="active", is_exercise=False)
    db.add(inc_b)
    db.flush()
    inc_b_id = inc_b.id

    # Org A lädt Org B ein (collaboration)
    db.add(IncidentOrg(incident_id=inc_b_id, org_id=org_a_id, role="collaborator"))
    db.commit()

    yield db, org_a_id, org_b_id
    db.close()
    Base.metadata.drop_all(bind=engine)


# ── Basis-Scoping ─────────────────────────────────────────────────────────────

def test_org_a_count_excludes_org_b(stats_db):
    """Org A sieht nur eigene Einsätze + eingeladene, nicht org_b-eigene ohne Einladung."""
    db, org_a_id, org_b_id = stats_db
    user = _FakeUser(org_a_id)
    q = db.query(Incident).filter(Incident.is_exercise == False)  # noqa: E712
    result = _apply_org_scope(q, user, db).all()
    # Org A hat 2 eigene + 1 collab (inc_b) = 3
    assert len(result) == 3
    for inc in result:
        assert inc.primary_org_id == org_a_id or any(
            io.org_id == org_a_id for io in (inc.collaborating_orgs or [])
        )


def test_org_b_count_own_only(stats_db):
    """Org B sieht nur den eigenen Einsatz (keine Einladung zu Org-A-Einsätzen)."""
    db, org_a_id, org_b_id = stats_db
    user = _FakeUser(org_b_id)
    q = db.query(Incident).filter(Incident.is_exercise == False)  # noqa: E712
    result = _apply_org_scope(q, user, db).all()
    assert len(result) == 1
    assert result[0].primary_org_id == org_b_id


def test_system_admin_sees_all(stats_db):
    """system_admin sieht alle Einsätze (kein Org-Filter)."""
    db, _, _ = stats_db
    user = _FakeUser(None, roles=("system_admin",))
    q = db.query(Incident).filter(Incident.is_exercise == False)  # noqa: E712
    result = _apply_org_scope(q, user, db).all()
    assert len(result) == 3  # A1, A2, B


def test_no_org_user_sees_nothing(stats_db):
    """User ohne Org (kein system_admin) sieht keine Einsätze."""
    db, _, _ = stats_db
    user = _FakeUser(None)
    q = db.query(Incident)
    result = _apply_org_scope(q, user, db).all()
    assert len(result) == 0


def test_exercise_filter_respected(stats_db):
    """Übungen werden korrekt mitgezählt wenn is_exercise=True gefiltert wird."""
    db, org_a_id, _ = stats_db
    user = _FakeUser(org_a_id)
    q = db.query(Incident).filter(Incident.is_exercise == True)  # noqa: E712
    result = _apply_org_scope(q, user, db).all()
    assert len(result) == 1
    assert result[0].alarm_type_code == "T1"
