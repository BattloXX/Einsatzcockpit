"""ActivityKit-Registrierung und Besitzwechsel."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentOrg
from app.models.live_activity import LiveActivityToken
from app.models.user import User
from app.services.live_activity_service import upsert_token
from tests.test_device_api_fcm_token import _authenticate, _make_device_user

URL = "/api/v1/device/live-activity-token"


def test_start_rotation_transfer_delete(client, setup_db):
    uid, did = _make_device_user("la_owner")
    other, other_device = _make_device_user("la_other")
    _authenticate(client, uid, did)
    for token in ("ABC123", "abc456"):
        assert client.post(URL, json={"kind": "start", "token": token}).json() == {"ok": True}
    with SessionLocal() as db:
        assert db.query(LiveActivityToken).count() == 1
        row = db.query(LiveActivityToken).one()
        assert (row.user_id, row.device_token_id) == (uid, did)
    client.cookies.clear()
    response = client.post(URL, json={"kind": "start", "token": "abc456"},
                           headers={"Authorization": "Bearer raw-la_other"})
    assert response.status_code == 200
    _authenticate(client, uid, did)
    assert client.request("DELETE", URL, json={"token": "abc456"}).json() == {"ok": True}
    with SessionLocal() as db:
        assert db.query(LiveActivityToken).one().user_id == other
    _authenticate(client, other, other_device)
    assert client.request("DELETE", URL, json={"token": "abc456"}).status_code == 200
    with SessionLocal() as db:
        assert db.query(LiveActivityToken).count() == 0


@pytest.mark.parametrize("body", [
    {}, {"kind": "unknown", "token": "ab"}, {"kind": "activity", "token": "ab"},
    *[{"kind": "start", "token": t} for t in ("", "z1", " a1", "a" * 513, 123, None)],
    {"kind": "activity", "token": "ab", "incident_id": True},
    {"kind": "activity", "token": "ab", "incident_id": "1"}, [],
])
def test_validation(client, setup_db, body, request):
    uid, did = _make_device_user(f"la_validation_{request.node.name}")
    _authenticate(client, uid, did)
    assert client.post(URL, json=body).status_code == 400


def test_activity_visibility(client, setup_db):
    uid, did = _make_device_user("la_visible")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = db.get(User, uid)
        user.org_id = 1
        own = Incident(alarm_type_code="B1", primary_org_id=1)
        foreign = Incident(alarm_type_code="B2", primary_org_id=2)
        shared = Incident(alarm_type_code="B3", primary_org_id=2)
        db.add_all([own, foreign, shared])
        db.flush()
        db.add(IncidentOrg(incident_id=shared.id, org_id=1))
        db.commit()
        own_id, foreign_id, shared_id = own.id, foreign.id, shared.id
    _authenticate(client, uid, did)
    for iid in (own_id, shared_id):
        assert client.post(URL, json={"kind": "activity", "token": f"ab{iid:x}", "incident_id": iid}).status_code == 200
    for iid in (foreign_id, 999999):
        assert client.post(URL, json={"kind": "activity", "token": "cd", "incident_id": iid}).status_code == 404
    with SessionLocal() as db:
        assert {r.incident_id for r in db.query(LiveActivityToken)} == {own_id, shared_id}


def test_unauthenticated(client, setup_db):
    assert client.post(URL, json={"kind": "start", "token": "ab"}).status_code == 401
    assert client.request("DELETE", URL, json={"token": "ab"}).status_code == 401


def test_migration(tmp_path):
    spec = importlib.util.spec_from_file_location("migration0341", Path("alembic/versions/0341_live_activity_token.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for name in ("user", "device_token", "incident"):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)')
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            columns = {c["name"]: c for c in sa.inspect(conn).get_columns("live_activity_token")}
            assert isinstance(columns["incident_id"]["type"], sa.Integer)
            conn.exec_driver_sql("INSERT INTO live_activity_token (user_id, kind, token, created_at, updated_at) "
                                 "VALUES (1, 'start', 'ab', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            assert conn.exec_driver_sql("SELECT id FROM live_activity_token").scalar_one() == 1
            module.downgrade()
            assert "live_activity_token" not in sa.inspect(conn).get_table_names()


def test_user_deletion_and_export(setup_db):
    from app.routers.ui_admin import _hard_delete_user
    from app.services.org_export_service import EXCLUDE_TABLES

    uid, _ = _make_device_user("la_delete")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = db.get(User, uid)
        upsert_token(db, user, "start", "abcd", None, None)
        db.commit()
        _hard_delete_user(db, user, 1)
        db.commit()
        assert db.query(LiveActivityToken).filter_by(user_id=uid).count() == 0
    assert "live_activity_token" in EXCLUDE_TABLES
