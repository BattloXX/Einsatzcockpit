"""Alle Middleware-Queries muessen ausserhalb des Event-Loops laufen."""
import threading
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine

import app.main as main
from app.core.fehlerlog import korrelations_id
from app.core.multi_account import add_account, load_accounts, sign_accounts
from app.core.security import hash_api_key, sign_native_link_token, sign_session, unsign_session
from app.core.tenant import set_tenant_context
from app.middleware.csrf import CSRFMiddleware
from app.models.incident import Incident, IncidentToken
from app.models.major_incident import LageToken, MajorIncident
from app.models.master import FireDept, OrgSettings
from app.models.user import DeviceToken, Role, User, UserRole
from app.services import broadcast, mfa_service
from tests.conftest import TestingSession


@pytest.fixture
def middleware_env(setup_db, monkeypatch):
    suffix = uuid4().hex
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug="loop-" + suffix, name="Loop Org")
        db.add(org)
        db.flush()
        role = db.query(Role).filter_by(code="recorder").one()
        users = [User(username=f"loop-{suffix}-{i}", display_name=f"Loop {i}", org_id=org.id)
                 for i in range(2)]
        db.add_all(users)
        db.flush()
        user_ids = [user.id for user in users]
        org_id = org.id
        db.add(UserRole(user_id=user_ids[0], role_id=role.id))
        db.add(OrgSettings(org_id=org.id, mfa_policy="aus"))
        db.commit()
    monkeypatch.setattr(main, "SessionLocal", TestingSession)
    mfa_service.clear_policy_cache()
    # Echte Produktions-Middleware, aber eine async Route ohne eigene DB-Zugriffe:
    # damit bleibt jeder gemessene SQL-Zugriff eindeutig der Middleware zuordenbar.
    test_app = FastAPI()
    test_app.middleware("http")(main.session_middleware)
    test_app.add_middleware(CSRFMiddleware)

    @test_app.get("/")
    async def page(request: Request):
        user = request.state.user
        return {
            "user_id": user.id if user else None,
            "org": user.org.name if user and user.org else None,
            "roles": [role.code for role in user.roles] if user else [],
            "detached": inspect(getattr(user, "_user", user)).detached if user else None,
            "accounts": request.state.accounts,
            "device": request.state.is_device,
            "device_token_id": request.state.device_token_id,
            "qr_incident_id": request.state.qr_incident_id,
            "qr_lage_id": request.state.qr_lage_id,
            "display_name": request.state.display_name,
            "auth_error": getattr(request.state, "auth_error", None),
        }

    queries = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        queries.append((threading.get_ident(), statement, korrelations_id.get(),
                        broadcast._request_client_id.get()))

    with TestClient(test_app) as client:
        # Das Portal fuehrt den Hook im selben Loop aus wie die HTTP-Middleware,
        # auch wenn das MFA-Gate die Route gar nicht erreicht.
        async def loop_thread():
            return threading.get_ident()

        loop_id = client.portal.call(loop_thread)
        event.listen(Engine, "before_cursor_execute", before_cursor_execute)
        try:
            yield client, user_ids, org_id, queries, loop_id
        finally:
            event.remove(Engine, "before_cursor_execute", before_cursor_execute)
            mfa_service.clear_policy_cache()


def set_cookie(client, name, value):
    client.cookies.set(name, value, domain="testserver.local", path="/")


def request_without_loop_sql(env, path="/", **kwargs):
    client, _, _, queries, loop_id = env
    queries.clear()
    response = client.get(path, follow_redirects=False,
                          headers={"X-Request-ID": "loop-test", "X-EC-Client": "loop-client", **kwargs})
    assert queries, "Der Test muss tatsaechlich DB-Zugriffe ausloesen"
    assert all(query[0] != loop_id for query in queries), queries
    assert all(query[2:] == ("loop-test", "loop-client") for query in queries), queries
    return response


def test_user_session_queries_and_refresh_outside_event_loop(middleware_env):
    client, users, _, _, _ = middleware_env
    set_cookie(client, "session", sign_session(users[0], remember=True))
    response = request_without_loop_sql(middleware_env)
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == users[0] and data["detached"]
    assert data["org"] == "Loop Org" and data["roles"] == ["recorder"]
    assert unsign_session(client.cookies.get("session"))[6] is True
    assert load_accounts(client.cookies.get("ec_accounts"))[0]["u"] == users[0]


@pytest.mark.parametrize("legacy", [False, True])
def test_device_session_queries_outside_event_loop(middleware_env, legacy):
    client, users, _, _, _ = middleware_env
    with TestingSession() as db:
        token = DeviceToken(user_id=users[0], label="Loop", token_hash=hash_api_key(uuid4().hex))
        db.add(token)
        db.flush()
        token_id = token.id
        db.commit()
    set_cookie(client, "session", sign_session(users[0], device=True,
                                             device_token_id=None if legacy else token_id))
    response = request_without_loop_sql(middleware_env)
    assert response.status_code == 200
    assert response.json()["user_id"] == users[0]
    assert response.json()["device"] is True
    assert response.json()["device_token_id"] == (None if legacy else token_id)
    assert "session=" not in response.headers.get("set-cookie", "")


def test_revoked_device_logging_outside_event_loop(middleware_env):
    client, users, _, _, _ = middleware_env
    with TestingSession() as db:
        token = DeviceToken(user_id=users[0], label="Revoked", token_hash=hash_api_key(uuid4().hex),
                            revoked_at=datetime.now(UTC))
        db.add(token)
        db.flush()
        token_id = token.id
        db.commit()
    set_cookie(client, "session", sign_session(users[0], device=True, device_token_id=token_id))
    response = request_without_loop_sql(middleware_env)
    assert response.json()["user_id"] is None
    assert response.json()["auth_error"] == "device_revoked"


def test_two_accounts_queries_outside_event_loop(middleware_env):
    client, users, _, _, _ = middleware_env
    set_cookie(client, "session", sign_session(users[0]))
    accounts = add_account(add_account([], users[0], False), users[1], True)
    set_cookie(client, "ec_accounts", sign_accounts(accounts))
    response = request_without_loop_sql(middleware_env)
    data = response.json()
    assert [account["user_id"] for account in data["accounts"]] == users
    assert data["accounts"][0]["is_active_account"] is True
    assert data["accounts"][1]["is_active_account"] is False
    assert {account["u"] for account in load_accounts(client.cookies.get("ec_accounts"))} == set(users)


def test_native_link_queries_outside_event_loop(middleware_env):
    client, users, _, _, _ = middleware_env
    response = request_without_loop_sql(middleware_env, "/?nt=" + sign_native_link_token("/", users[0]))
    assert response.status_code == 200 and response.json()["user_id"] == users[0]
    assert response.json()["detached"]
    assert not client.cookies.get("session")


@pytest.mark.parametrize("headers,status", [({}, 303), ({"Accept": "application/json"}, 403),
                                           ({"HX-Request": "true"}, 403)])
def test_mfa_gate_queries_outside_event_loop(middleware_env, headers, status):
    client, users, org_id, _, _ = middleware_env
    with TestingSession() as db:
        config = db.query(OrgSettings).filter_by(org_id=org_id).one()
        config.mfa_policy = "alle"
        db.commit()
    set_cookie(client, "session", sign_session(users[0]))
    for _ in range(2):  # Cache-Miss und Cache-Hit pruefen.
        response = request_without_loop_sql(middleware_env, **headers)
        assert response.status_code == status
        if status == 303:
            assert response.headers["location"] == "/mfa/verify?next=%2F"
        else:
            assert response.json()["code"] == "mfa_required"
            assert response.headers["HX-Redirect"] == "/mfa/verify"
        assert "session=" not in response.headers.get("set-cookie", "")


@pytest.mark.parametrize("kind", ["incident", "lage"])
def test_qr_queries_outside_event_loop(middleware_env, kind):
    client, users, org_id, _, _ = middleware_env
    with TestingSession() as db:
        set_tenant_context(db, None)
        if kind == "incident":
            row = Incident(primary_org_id=org_id)
            db.add(row)
            db.flush()
            db.add(IncidentToken(incident_id=row.id, issued_by_user_id=users[0], token_hash=uuid4().hex))
            cookie = sign_session(users[0], qr=True, incident_id=row.id, display_name="QR Name")
        else:
            row = MajorIncident(org_id=org_id, name="Loop Lage")
            db.add(row)
            db.flush()
            db.add(LageToken(lage_id=row.id, issued_by_user_id=users[0], token_hash=uuid4().hex))
            cookie = sign_session(users[0], qr=True, lage_id=row.id, display_name="QR Name")
        row_id = row.id
        db.commit()
    set_cookie(client, "session", cookie)
    response = request_without_loop_sql(middleware_env)
    assert response.status_code == 200
    assert response.json()["user_id"] == users[0]
    assert response.json()["qr_incident_id" if kind == "incident" else "qr_lage_id"] == row_id
    assert response.json()["roles"] == ["recorder"]
    assert response.json()["display_name"] == "QR Name"
    assert "session=" not in response.headers.get("set-cookie", "")


def test_fcm_upsert_queries_outside_event_loop(middleware_env):
    client, users, _, _, _ = middleware_env
    set_cookie(client, "session", sign_session(users[0]))
    response = request_without_loop_sql(middleware_env, "/?fcm_token=loop-fcm&fcm_platform=android")
    assert response.status_code == 200
    assert response.json()["roles"] == ["recorder"] and response.json()["detached"]
    assert any("INSERT INTO fcm_token" in query[1] for query in middleware_env[3])


@pytest.mark.parametrize("path,cookie", [("/", False), ("/", True), ("/static/test", True),
                                         ("/.well-known/test", True), ("/sw.js", True),
                                         ("/favicon.ico", True)])
def test_anonymous_invalid_and_skip_paths_without_thread_hop(middleware_env, monkeypatch, path, cookie):
    client, users, _, queries, _ = middleware_env
    if cookie:
        set_cookie(client, "session", "invalid" if path == "/" else sign_session(users[0]))

    async def unexpected_thread_hop(*args, **kwargs):
        pytest.fail("Anonym und Skip-Pfade brauchen keinen Thread-Hop")

    monkeypatch.setattr(main.asyncio, "to_thread", unexpected_thread_hop)
    queries.clear()
    response = client.get(path)
    assert response.status_code == (200 if path == "/" else 404)
    assert not queries
