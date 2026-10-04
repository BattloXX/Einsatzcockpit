"""Tests fuer das Fundament des read-only Einsatz-Feeds."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import Depends

from app.core.dependencies import require_feed_scope
from app.core.security import generate_api_key, hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal, get_db
from app.main import app
from app.models.master import FireDept
from app.models.user import ApiKey, AuditLog, Role, User, UserRole
from app.schemas.feed import FORBIDDEN_FIELDS, FeedEinsatzBasis
from app.services.feed_service import build_feed_einsatz_basis


@app.get("/api/v1/_tests/feed-scope")
def _feed_scope_route(
    api_key: ApiKey = Depends(require_feed_scope("einsatz:read")),
    db=Depends(get_db),
):
    return {"org_id": api_key.org_id, "tenant_org_id": db.info.get("current_org_id")}


def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0253_feed_fundament.py"
    spec = importlib.util.spec_from_file_location("migration_0253", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_migration(conn, function):
    with Operations.context(MigrationContext.configure(conn)):
        function()


def _feed_key(*, scopes="einsatz:read", ip_allowlist=None, expires_at=None, revoked_at=None):
    raw = generate_api_key()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org.is_(True)).first()
        assert org is not None
        key = ApiKey(
            key_hash=hash_api_key(raw), label="Feed-Test", org_id=org.id, scopes=scopes,
            ip_allowlist=ip_allowlist, expires_at=expires_at, revoked_at=revoked_at,
        )
        db.add(key)
        db.commit()
        return raw, key.id, org.id
    finally:
        db.close()


def _login_admin(client, username):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).filter(FireDept.is_home_org.is_(True)).first()
        role = db.query(Role).filter(Role.code == "admin").first()
        assert org is not None and role is not None
        user = User(
            username=username, password_hash=hash_password("Test1234!"),
            display_name="Feed-Admin", org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()
    client.get("/login")
    response = client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert response.status_code == 302


def test_migration_0253_adds_and_removes_feed_columns(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0253.db'}")
    module = _migration()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE api_key (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE incident (id INTEGER PRIMARY KEY, primary_org_id INTEGER, "
            "status VARCHAR(20), started_at DATETIME)"
        )
        _run_migration(conn, module.upgrade)
        _run_migration(conn, module.upgrade)
        assert {"ip_allowlist"}.issubset(
            {column["name"] for column in sa.inspect(conn).get_columns("api_key")}
        )
        assert {"feed_rev"}.issubset(
            {column["name"] for column in sa.inspect(conn).get_columns("incident")}
        )
        assert conn.exec_driver_sql("SELECT feed_rev FROM incident").fetchall() == []
        _run_migration(conn, module.downgrade)
        assert "feed_rev" not in {
            column["name"] for column in sa.inspect(conn).get_columns("incident")
        }


def test_admin_accepts_feed_scopes_and_rejects_invalid_input(client):
    _login_admin(client, "feed_admin_ui")
    csrf = client.cookies.get("ec_csrf")
    ok = client.post("/admin/api-keys/neu", data={
        "_csrf": csrf,
        "label": "Feed",
        "scopes": ["einsatz:read", "einsatz:read:kraefte", "einsatz:read:board"],
        "ip_allowlist": "192.0.2.7/24, 2001:db8::/32",
    })
    assert ok.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        key = db.query(ApiKey).filter(ApiKey.label == "Feed").one()
        assert key.scopes == "einsatz:read,einsatz:read:kraefte,einsatz:read:board"
        assert key.ip_allowlist == "192.0.2.0/24,2001:db8::/32"
    finally:
        db.close()
    bad_scope = client.post("/admin/api-keys/neu", data={
        "_csrf": csrf, "label": "Ungültig", "scopes": "nicht:erlaubt",
    })
    assert bad_scope.status_code == 400
    bad_cidr = client.post("/admin/api-keys/neu", data={
        "_csrf": csrf, "label": "Ungültig", "ip_allowlist": "kein-netz",
    })
    assert bad_cidr.status_code == 400


@pytest.mark.parametrize("scopes, expected", [("", 403), ("einsatz:read", 200)])
def test_require_feed_scope_scope_matrix(client, scopes, expected):
    raw, _, org_id = _feed_key(scopes=scopes)
    response = client.get("/api/v1/_tests/feed-scope", headers={"X-API-Key": raw})
    assert response.status_code == expected
    if expected == 200:
        assert response.json()["org_id"] == org_id
        assert response.json()["tenant_org_id"] == org_id


@pytest.mark.parametrize("state", ["revoked", "expired"])
def test_require_feed_scope_rejects_inactive_keys(client, state):
    now = datetime.now(UTC)
    raw, _, _ = _feed_key(
        revoked_at=now if state == "revoked" else None,
        expires_at=now - timedelta(minutes=1) if state == "expired" else None,
    )
    assert client.get("/api/v1/_tests/feed-scope", headers={"X-API-Key": raw}).status_code == 401


def test_require_feed_scope_denies_non_matching_allowlist_and_audits(client):
    raw, key_id, _ = _feed_key(ip_allowlist="203.0.113.0/24")
    response = client.get("/api/v1/_tests/feed-scope", headers={"X-API-Key": raw})
    assert response.status_code == 403
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        count = db.query(AuditLog).filter(
            AuditLog.action == "api.feed.denied", AuditLog.api_key_id == key_id
        ).count()
        assert count == 1
    finally:
        db.close()


def test_require_feed_scope_accepts_matching_allowlist(client):
    from fastapi.testclient import TestClient

    raw, _, _ = _feed_key(ip_allowlist="203.0.113.0/24")
    with TestClient(app, client=("203.0.113.7", 50000)) as allowed_client:
        response = allowed_client.get("/api/v1/_tests/feed-scope", headers={"X-API-Key": raw})
    assert response.status_code == 200


def test_last_used_at_is_throttled(client):
    raw, key_id, _ = _feed_key()
    assert client.get("/api/v1/_tests/feed-scope", headers={"X-API-Key": raw}).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        first = db.get(ApiKey, key_id).last_used_at
    finally:
        db.close()
    assert client.get("/api/v1/_tests/feed-scope", headers={"X-API-Key": raw}).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ApiKey, key_id).last_used_at == first
    finally:
        db.close()


def test_feed_dto_is_whitelisted_and_uses_utc_z_timestamps():
    now = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
    incident = SimpleNamespace(
        id=7, nummer=12, alarm_type_code="B2", status="active", is_exercise=False,
        started_at=now, closed_at=None, taken_over_at=now, departed_at=None,
        on_scene_at=None, ready_again_at=None, address_street="Hauptstraße", address_no="1",
        address_city="Musterort", lat=47.1, lng=9.7, vehicles=[], objekt_links=[],
        caller_name="Nicht im DTO", report_text="Geheim",
    )
    payload = build_feed_einsatz_basis(incident, SimpleNamespace(timezone="Europe/Vienna"))
    assert not (set(FeedEinsatzBasis.model_fields) & FORBIDDEN_FIELDS)
    dumped = payload.model_dump()
    assert dumped["started_at"].endswith("Z")
    assert dumped["taken_over_at"].endswith("Z")
    assert not (set(dumped) & FORBIDDEN_FIELDS)
