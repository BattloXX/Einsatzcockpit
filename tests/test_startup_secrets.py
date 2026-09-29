"""Regressionstests PR11 (SEC-7, SEC-10): validate_startup_secrets() erzwingt
COOKIE_SECURE + FERNET_KEY in Produktion; Login-Timing-Seitenkanal geglättet."""
from unittest.mock import patch

from app.config import settings, validate_startup_secrets
from app.routers.auth import _get_dummy_password_hash


def test_validate_startup_secrets_flags_missing_fernet_key():
    with patch.object(settings, "FERNET_KEY", ""):
        errors = validate_startup_secrets()
    assert any("FERNET_KEY" in e for e in errors)


def test_validate_startup_secrets_ok_with_fernet_key():
    with patch.object(settings, "FERNET_KEY", "some-fernet-key"), \
         patch.object(settings, "COOKIE_SECURE", True):
        errors = validate_startup_secrets()
    assert not any("FERNET_KEY" in e for e in errors)


def test_validate_startup_secrets_flags_missing_cookie_secure():
    with patch.object(settings, "COOKIE_SECURE", False):
        errors = validate_startup_secrets()
    assert any("COOKIE_SECURE" in e for e in errors)


def test_dummy_password_hash_is_valid_bcrypt_and_cached():
    from app.core.security import verify_password
    h1 = _get_dummy_password_hash()
    h2 = _get_dummy_password_hash()
    assert h1 == h2, "Dummy-Hash sollte gecacht sein (nicht bei jedem Login neu bcrypt-hashen)"
    assert h1.startswith("$2b$")
    # Beliebiges Passwort darf nicht gegen den Dummy-Hash matchen.
    assert verify_password("irgendein-passwort", h1) is False


def test_login_nonexistent_user_still_runs_bcrypt_compare(client, setup_db):
    """SEC-10: auch bei nicht existierendem User muss ein bcrypt-Vergleich laufen
    (Timing-Angleichung gegen Enumeration)."""
    with patch("app.services.auth_service.verify_password", wraps=__import__(
        "app.core.security", fromlist=["verify_password"]
    ).verify_password) as mock_verify:
        client.get("/login")
        csrf = client.cookies.get("ec_csrf")
        r = client.post(
            "/login",
            data={"username": "does-not-exist-xyz", "password": "whatever", "_csrf": csrf},
            follow_redirects=False,
        )
        assert r.status_code == 401
        assert mock_verify.called, "verify_password() wurde fuer nicht existierenden User nicht aufgerufen (SEC-10-Regression)"
