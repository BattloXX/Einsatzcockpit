"""Native-App-Datei-Handoff (/api/v1/device/native-link).

Android-WebView hat keinen eingebauten PDF-Viewer - PDFs muessen deshalb an
einen Capacitor-Custom-Tab uebergeben werden (@capacitor/browser), der die
Session-Cookies der App-WebView NICHT teilt. Diese Tests decken das
kurzlebige, pfadgebundene ?nt=-Token ab (app/core/security.py::
sign_native_link_token, app/main.py::session_middleware).
"""
from app.core.security import hash_password, sign_native_link_token, unsign_native_link_token
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


def _login(client, username, password):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_user(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Native-Link Test-User", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "admin").id))
        db.commit()
        return user.id
    finally:
        db.close()


def test_unsign_native_link_token_roundtrip():
    token = sign_native_link_token("/objekte/5/objektblatt.pdf", 42)
    result = unsign_native_link_token(token)
    assert result == ("/objekte/5/objektblatt.pdf", 42)


def test_unsign_native_link_token_rejects_garbage():
    assert unsign_native_link_token("not-a-real-token") is None


def test_create_native_link_requires_login():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    client.cookies.clear()
    r = client.post("/api/v1/device/native-link", json={"path": "/objekte/1/objektblatt.pdf"})
    assert r.status_code == 401


def test_create_native_link_rejects_absolute_url():
    _setup_user("native_link_absurl")
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "native_link_absurl", "Test1234!")
    r = client.post("/api/v1/device/native-link", json={"path": "https://evil.example/phish"})
    assert r.status_code == 400


def test_create_native_link_rejects_protocol_relative_url():
    _setup_user("native_link_protorel")
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "native_link_protorel", "Test1234!")
    r = client.post("/api/v1/device/native-link", json={"path": "//evil.example/phish"})
    assert r.status_code == 400


def test_native_link_token_authenticates_without_cookie():
    """Der Custom Tab hat kein Session-Cookie - das ?nt=-Token allein muss den
    Request fuer GENAU den angeforderten Pfad authentifizieren."""
    from fastapi.testclient import TestClient
    from app.main import app
    _setup_user("native_link_authcheck")

    client = TestClient(app)
    _login(client, "native_link_authcheck", "Test1234!")
    r = client.post("/api/v1/device/native-link", json={"path": "/admin/push-nachrichten"})
    assert r.status_code == 200
    url = r.json()["url"]
    assert "nt=" in url
    path_and_query = url.split("://", 1)[1].split("/", 1)[1]

    # Ohne Cookie, nur mit dem Token aus der URL:
    anon_client = TestClient(app)
    anon_client.cookies.clear()
    r2 = anon_client.get("/" + path_and_query)
    assert r2.status_code == 200
    assert "Push-Nachrichten" in r2.text


def test_native_link_token_rejected_for_different_path():
    """Ein Token fuer Pfad A darf Pfad B nicht authentifizieren (kein genereller
    Session-Ersatz, sondern exakt pfadgebunden)."""
    from fastapi.testclient import TestClient
    from app.main import app
    _setup_user("native_link_wrongpath")

    client = TestClient(app)
    _login(client, "native_link_wrongpath", "Test1234!")
    r = client.post("/api/v1/device/native-link", json={"path": "/admin/push-nachrichten"})
    token = r.json()["url"].split("nt=")[1]

    anon_client = TestClient(app)
    anon_client.cookies.clear()
    r2 = anon_client.get(f"/admin/?nt={token}", follow_redirects=False)
    # Falscher Pfad -> Token greift nicht -> als anonym behandelt (Redirect zum Login)
    assert r2.status_code == 302
    assert r2.headers.get("location") == "/login"
