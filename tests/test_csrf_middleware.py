"""CSRF-Middleware: Header-first-Streaming (Audit B5) + Fallback-Verhalten.

Der X-CSRF-Token-Header wird VOR der Body-Pufferung geprüft: stimmt er, wird
der Body ungepuffert an den Handler durchgestreamt. Nur klassische Formulare
ohne Header fallen auf das _csrf-Formfeld (mit Body-Puffer + Replay) zurück.

POSTs auf nicht existierende Pfade laufen trotzdem durch die Middleware —
404 statt 403 beweist also "CSRF-Prüfung bestanden" ohne Auth-Aufbau.
"""
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)
PROBE = "/csrf-probe-nicht-vorhanden"


def _csrf_cookie(client) -> str:
    client.get("/login")
    token = client.cookies.get("ec_csrf")
    assert token
    return token


def _make_user(username: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        if db.query(User).filter(User.username == username).first():
            return
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="CSRF-Tester", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "admin").first()
        if role:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def test_header_token_gueltig_streamt_durch(client):
    token = _csrf_cookie(client)
    r = client.post(PROBE, headers={"X-CSRF-Token": token}, data={"x": "1"})
    assert r.status_code == 404  # CSRF bestanden, Route existiert nur nicht


def test_header_token_falsch_403(client):
    _csrf_cookie(client)
    r = client.post(PROBE, headers={"X-CSRF-Token": "falsches-token"}, data={"x": "1"})
    assert r.status_code == 403


def test_ohne_token_403(client):
    _csrf_cookie(client)
    r = client.post(PROBE, data={"x": "1"})
    assert r.status_code == 403


def test_formfeld_fallback_funktioniert_weiter(client):
    token = _csrf_cookie(client)
    r = client.post(PROBE, data={"x": "1", "_csrf": token})
    assert r.status_code == 404


def test_falscher_header_gewinnt_ueber_gueltiges_formfeld(client):
    # Header hat Vorrang: ungültiger Header → 403, auch wenn das Formfeld stimmt
    # (identisch zum Verhalten vor dem Umbau).
    token = _csrf_cookie(client)
    r = client.post(PROBE, headers={"X-CSRF-Token": "falsch"},
                    data={"x": "1", "_csrf": token})
    assert r.status_code == 403


def test_multipart_mit_header_ohne_formfeld(client):
    # Multipart-Upload nur mit Header-Token (Streaming-Pfad): kein 403.
    token = _csrf_cookie(client)
    r = client.post(PROBE, headers={"X-CSRF-Token": token},
                    files={"file": ("test.txt", b"x" * 4096, "text/plain")})
    assert r.status_code == 404


def test_multipart_formfeld_fallback(client):
    # Multipart ohne Header: _csrf-Feld im Body wird weiterhin gefunden (Puffer-Pfad).
    token = _csrf_cookie(client)
    r = client.post(PROBE, files={"file": ("test.txt", b"inhalt", "text/plain")},
                    data={"_csrf": token})
    assert r.status_code == 404


def test_body_kommt_im_streaming_pfad_intakt_an(client):
    # Login NUR mit Header-Token (kein _csrf-Feld): Der Handler muss das
    # Formular aus dem durchgestreamten Body parsen können — 302-Redirect
    # beweist, dass Benutzername/Passwort vollständig ankamen.
    _make_user("csrf_stream_user")
    token = _csrf_cookie(client)
    r = client.post("/login",
                    headers={"X-CSRF-Token": token},
                    data={"username": "csrf_stream_user", "password": "Test1234!"},
                    follow_redirects=False)
    assert r.status_code == 302
