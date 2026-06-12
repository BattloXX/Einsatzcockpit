"""PR 1 – Sichtbarkeits-Testmatrix & Fail-Closed-Listener.

Akzeptanz-Kriterien PR 1:
- Fail-Closed: Ein vorsätzlich ungefilterter Session-Zugriff auf ein
  tenant-pflichtiges Modell ohne gesetzten Kontext löst TenantContextMissing aus.
- Listener erweitert: Incident (primary_org ODER IncidentOrg), VehicleMaster (dept_id),
  User (org_id), AuditLog (org_id) werden korrekt gescoped.
- VehicleMaster-Scoping: Org A sieht nur eigene Fahrzeuge.
- Incident-Scoping: Org B sieht Org-A-Einsatz nur nach IncidentOrg-Einladung.
- User-Scoping: Org A sieht keine User von Org B.
"""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import TenantContextMissing, set_tenant_context
from app.db import Base
from app.models.incident import Incident, IncidentOrg
from app.models.master import FireDept, Member, VehicleMaster
from app.models.user import AuditLog, User

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture()
def vm_db():
    """In-Memory-DB für Sichtbarkeits-Tests."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org_a = FireDept(slug="vm-a", name="VM Org A", color="#f00", bos="Feuerwehr")
    org_b = FireDept(slug="vm-b", name="VM Org B", color="#00f", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    # VehicleMaster je Org
    db.add_all([
        VehicleMaster(dept_id=org_a.id, code="RLF-A", name="Rüstlöschfahrzeug A"),
        VehicleMaster(dept_id=org_a.id, code="TLF-A", name="Tanklöschfahrzeug A"),
        VehicleMaster(dept_id=org_b.id, code="RLF-B", name="Rüstlöschfahrzeug B"),
    ])
    # Incident je Org (kein Collaboration)
    inc_a = Incident(primary_org_id=org_a.id, alarm_type_code="T1", status="active")
    inc_b = Incident(primary_org_id=org_b.id, alarm_type_code="T2", status="active")
    db.add_all([inc_a, inc_b])
    db.flush()
    # IDs vor commit sichern (commit expired-markiert alle Objekte; reload von
    # Incident triggert sonst Fail-Closed da noch kein Context gesetzt ist)
    org_a_id = org_a.id
    org_b_id = org_b.id
    inc_a_id = inc_a.id
    inc_b_id = inc_b.id

    # Org A lädt Org B ein (IncidentOrg für inc_a)
    db.add(IncidentOrg(incident_id=inc_a_id, org_id=org_b_id, role="collaborator"))

    # User je Org (plus system_admin ohne Org)
    db.add_all([
        User(username="user-a", password_hash="x", display_name="User A", org_id=org_a_id),
        User(username="user-b", password_hash="x", display_name="User B", org_id=org_b_id),
        User(username="sysadmin", password_hash="x", display_name="System Admin", org_id=None),
    ])
    # AuditLog: je Org + systemweit (NULL)
    db.add_all([
        AuditLog(action="test.a", org_id=org_a_id),
        AuditLog(action="test.b", org_id=org_b_id),
        AuditLog(action="test.system", org_id=None),
    ])
    db.commit()

    yield db, org_a_id, org_b_id, inc_a_id, inc_b_id

    db.close()
    Base.metadata.drop_all(bind=engine)


# ── Fail-Closed ───────────────────────────────────────────────────────────────

def test_fail_closed_raises_on_missing_context():
    """Queries auf tenant-pflichtige Tabellen ohne gesetzten Kontext werfen TenantContextMissing."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        with pytest.raises(TenantContextMissing):
            db.query(Member).all()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_fail_closed_raises_on_incident_without_context():
    """Incident-Abfrage ohne Kontext wirft TenantContextMissing."""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        with pytest.raises(TenantContextMissing):
            db.query(Incident).all()
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_fail_closed_bypassed_with_include_all_tenants():
    """include_all_tenants=True umgeht den Fail-Closed-Check."""
    from sqlalchemy import select
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        result = db.execute(
            select(Member).execution_options(include_all_tenants=True)
        ).scalars().unique().all()
        assert isinstance(result, list)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_fail_closed_none_context_allows_query(vm_db):
    """Explizit gesetzter None-Kontext (system_admin) löst keinen Fehler aus."""
    db, org_a_id, org_b_id, _, _ = vm_db
    set_tenant_context(db, None)
    result = db.query(Member).all()
    assert isinstance(result, list)


# ── VehicleMaster-Scoping ─────────────────────────────────────────────────────

def test_vehicle_master_scoped_to_org_a(vm_db):
    """Org A sieht nur eigene Fahrzeuge (dept_id-Filter)."""
    db, org_a_id, _, _, _ = vm_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    vehicles = db.query(VehicleMaster).all()

    assert len(vehicles) == 2
    assert all(v.dept_id == org_a_id for v in vehicles)
    codes = {v.code for v in vehicles}
    assert codes == {"RLF-A", "TLF-A"}


def test_vehicle_master_scoped_to_org_b(vm_db):
    """Org B sieht nur eigenes Fahrzeug."""
    db, _, org_b_id, _, _ = vm_db
    set_tenant_context(db, org_b_id)
    db.expire_all()

    vehicles = db.query(VehicleMaster).all()

    assert len(vehicles) == 1
    assert vehicles[0].code == "RLF-B"
    assert vehicles[0].dept_id == org_b_id


def test_vehicle_master_none_context_sees_all(vm_db):
    """System-Admin sieht alle Fahrzeuge."""
    db, _, _, _, _ = vm_db
    set_tenant_context(db, None)
    db.expire_all()

    vehicles = db.query(VehicleMaster).all()
    assert len(vehicles) == 3


# ── Incident-Scoping ──────────────────────────────────────────────────────────

def test_incident_scoped_org_a_only_own(vm_db):
    """Org A sieht nur eigenen Einsatz (kein Org-B-Einsatz)."""
    db, org_a_id, org_b_id, inc_a_id, inc_b_id = vm_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    incidents = db.query(Incident).all()

    ids = {i.id for i in incidents}
    assert inc_a_id in ids
    assert inc_b_id not in ids


def test_incident_scoped_org_b_sees_collab(vm_db):
    """Org B sieht den Org-A-Einsatz wegen IncidentOrg-Einladung + eigenen Einsatz."""
    db, org_a_id, org_b_id, inc_a_id, inc_b_id = vm_db
    set_tenant_context(db, org_b_id)
    db.expire_all()

    incidents = db.query(Incident).all()

    ids = {i.id for i in incidents}
    assert inc_a_id in ids   # via IncidentOrg (Einladung)
    assert inc_b_id in ids   # eigener Einsatz


# ── User-Scoping ──────────────────────────────────────────────────────────────

def test_user_scoped_to_org_a(vm_db):
    """Org A sieht nur Org-A-User; system_admin (org_id=NULL) taucht nicht auf."""
    db, org_a_id, org_b_id, _, _ = vm_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    users = db.query(User).all()

    assert len(users) == 1
    assert users[0].username == "user-a"
    usernames = {u.username for u in users}
    assert "sysadmin" not in usernames
    assert "user-b" not in usernames


def test_user_scoped_to_org_b(vm_db):
    """Org B sieht nur Org-B-User."""
    db, _, org_b_id, _, _ = vm_db
    set_tenant_context(db, org_b_id)
    db.expire_all()

    users = db.query(User).all()

    assert len(users) == 1
    assert users[0].username == "user-b"


def test_user_none_context_sees_all(vm_db):
    """System-Admin sieht alle User einschließlich system_admin (org_id=NULL)."""
    db, _, _, _, _ = vm_db
    set_tenant_context(db, None)
    db.expire_all()

    users = db.query(User).all()
    usernames = {u.username for u in users}
    assert "user-a" in usernames
    assert "user-b" in usernames
    assert "sysadmin" in usernames


# ── AuditLog-Scoping ─────────────────────────────────────────────────────────

def test_audit_log_scoped_to_org_a(vm_db):
    """Org A sieht nur eigene Audit-Einträge; systemweite (NULL) werden ausgeblendet."""
    db, org_a_id, _, _, _ = vm_db
    set_tenant_context(db, org_a_id)
    db.expire_all()

    logs = db.query(AuditLog).all()

    assert len(logs) == 1
    assert logs[0].action == "test.a"
    actions = {l.action for l in logs}
    assert "test.system" not in actions
    assert "test.b" not in actions


def test_audit_log_none_context_sees_all(vm_db):
    """System-Admin sieht alle Audit-Einträge einschließlich systemweiter."""
    db, _, _, _, _ = vm_db
    set_tenant_context(db, None)
    db.expire_all()

    logs = db.query(AuditLog).all()
    actions = {l.action for l in logs}
    assert "test.a" in actions
    assert "test.b" in actions
    assert "test.system" in actions
