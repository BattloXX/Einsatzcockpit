"""Tests für /admin/push-nachrichten: der Push-Status der nativen Android-App
(Capacitor-WebView, kein ServiceWorker/PushManager — Push läuft dort über FCM,
siehe native-bridge.js::_registerFcmToken()) muss serverseitig korrekt anhand
von FcmToken erkannt werden, statt fälschlich "nicht unterstützt" anzuzeigen.

Ausserdem: die Abonnenten-Zählung/-Auswahl muss BEIDE Sendewege (Web-Push UND
FCM, siehe push_service.py) berücksichtigen, sonst unterschätzt sie die
tatsächliche Reichweite und schließt native-App-Nutzer aus der Empfänger-Liste aus.
"""
import re

from fastapi.testclient import TestClient

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.user import DeviceToken, FcmToken, PushSubscription, Role, User, UserRole

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


def _setup_admin(username: str) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Push Test-Admin", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "admin").id))
        db.commit()
        return user.id
    finally:
        db.close()


def test_native_app_without_fcm_token_shows_inactive_not_unsupported():
    """Kein Web-Push-Selbstcheck (der würde immer 'nicht unterstützt' zeigen) —
    stattdessen serverseitiger FCM-Status, hier: noch keine Registrierung."""
    _setup_admin("push_native_no_fcm")
    client = TestClient(app)
    _login(client, "push_native_no_fcm", "Test1234!")

    r = client.get("/admin/push-nachrichten", params={"native": "1"})
    assert r.status_code == 200
    assert "Push wird von diesem Browser nicht unterstützt" not in r.text
    assert "Noch keine FCM-Registrierung" in r.text


def test_native_app_with_fcm_token_shows_active():
    _setup_admin("push_native_with_fcm")
    client = TestClient(app)
    _login(client, "push_native_with_fcm", "Test1234!")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = db.query(User).filter(User.username == "push_native_with_fcm").first()
        db.add(FcmToken(user_id=user.id, token="fcm-token-abc", platform="android"))
        db.commit()
    finally:
        db.close()

    r = client.get("/admin/push-nachrichten", params={"native": "1"})
    assert r.status_code == 200
    assert "Push wird von diesem Browser nicht unterstützt" not in r.text
    assert "über die native App (FCM) aktiviert" in r.text


def test_native_app_fcm_status_is_scoped_to_current_device():
    user_id = _setup_admin("push_native_device_scope")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        registered = DeviceToken(
            user_id=user_id,
            token_hash=hash_api_key("push-device-registered"),
            label="Registriertes Gerät",
        )
        inactive = DeviceToken(
            user_id=user_id,
            token_hash=hash_api_key("push-device-inactive"),
            label="Inaktives Gerät",
        )
        db.add_all([registered, inactive])
        db.flush()
        db.add(FcmToken(
            user_id=user_id,
            device_token_id=registered.id,
            token="fcm-token-device-scoped",
            platform="android",
        ))
        db.commit()
        registered_id = registered.id
        inactive_id = inactive.id
    finally:
        db.close()

    client = TestClient(app)
    client.cookies.set(
        "session", sign_session(user_id, device=True, device_token_id=registered_id)
    )
    active = client.get("/admin/push-nachrichten", params={"native": "1"})
    assert active.status_code == 200
    assert "über die native App (FCM) aktiviert" in active.text

    client.cookies.set(
        "session", sign_session(user_id, device=True, device_token_id=inactive_id)
    )
    inactive_response = client.get("/admin/push-nachrichten", params={"native": "1"})
    assert inactive_response.status_code == 200
    assert "Noch keine FCM-Registrierung" in inactive_response.text


def test_non_native_browser_keeps_client_side_status_check():
    """Ohne native-App-Erkennung bleibt der bisherige Browser-Selbstcheck
    (JS prüft serviceWorker/PushManager) unverändert aktiv."""
    _setup_admin("push_browser_user")
    client = TestClient(app)
    _login(client, "push_browser_user", "Test1234!")

    r = client.get("/admin/push-nachrichten")
    assert r.status_code == 200
    assert "Prüfe Status" in r.text
    assert "Noch keine FCM-Registrierung" not in r.text


def _sub_count(html: str) -> int:
    m = re.search(r'badge-pill--gray"[^>]*>\s*(\d+)\s*Geräte?', html)
    assert m, "Abonnenten-Zähler nicht gefunden"
    return int(m.group(1))


def test_subscriber_count_includes_fcm_tokens():
    """Zählt vorher/nachher (DELTA), statt eine absolute Zahl anzunehmen: die
    "admin"-Rolle erfüllt has_role(user, "system_admin") ebenfalls (siehe
    ROLES/has_role in app/core/permissions.py — "admin" ist laut Kommentar dort
    ein "backward-compat alias for org_admin", has_role() nimmt "admin"/
    "org_admin" aber pauschal in JEDE Rollenprüfung mit auf), wodurch is_sysadmin
    in push_notifications_page für jeden Nutzer, der die Seite überhaupt
    erreichen kann, True wird und serverseitig NICHT auf die eigene Org gefiltert
    wird — ein vorbestehendes, von dieser Änderung unabhängiges Verhalten. Ein
    Delta ist daher robust unabhängig davon, was andere Orgs/Tests bereits an
    Abonnements angelegt haben."""
    from app.models.master import FireDept
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug="push-count-test-org", name="Push-Count-Test-Org",
                       color="#336699", bos="Feuerwehr")
        db.add(org)
        db.flush()
        org_id = org.id
        admin_user = User(username="push_count_user", password_hash=hash_password("Test1234!"),
                          display_name="Push Test-Admin", org_id=org_id, active=True)
        db.add(admin_user)
        db.flush()
        db.add(UserRole(user_id=admin_user.id, role_id=_rolle(db, "admin").id))
        db.commit()
        admin_user_id = admin_user.id
    finally:
        db.close()

    client = TestClient(app)
    _login(client, "push_count_user", "Test1234!")

    before = _sub_count(client.get("/admin/push-nachrichten").text)

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fcm_only_user = User(username="push_fcm_only_member", password_hash=hash_password("Test1234!"),
                             display_name="Nur FCM", org_id=org_id, active=True)
        db.add(fcm_only_user)
        db.flush()
        db.add(FcmToken(user_id=fcm_only_user.id, token="fcm-token-count", platform="android"))
        db.add(PushSubscription(user_id=admin_user_id, endpoint="https://push.example/ep1",
                                p256dh="p256dh-key", auth="auth-key"))
        db.commit()
    finally:
        db.close()

    r = client.get("/admin/push-nachrichten")
    after = _sub_count(r.text)
    assert after - before == 2  # 1 Web-Push + 1 FCM
    # FCM-only-Nutzer muss in der "Einzelner Benutzer"-Auswahl auftauchen
    assert "Nur FCM" in r.text
