"""Phase 5b: geheime Speicherung, tokenfreie Oberflaeche und lokale GitHub-Tests."""
import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.admin_mcp import code_tools, registry
from app.config import settings
from app.core.crypto import decrypt_secret
from app.core.security import sign_session
from app.models.master import SystemSettings
from app.models.user import AuditLog
from app.services import github_readonly as gh
from app.services import github_token as service
from app.services.diagnose.deploy import check_deploy
from app.services.diagnose.registry import Budget
from tests.test_admin_totp import mfa_env  # noqa: F401

SECRET = "github_seed_never_expose_123456789"
OTHER = "github_seed_replacement_987654321"
STAMP = datetime(2026, 10, 27, 10)
URL = "/admin/system-einstellungen"


@pytest.fixture
def env(mfa_env, monkeypatch):
    db, user, regular, client = mfa_env
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", "")
    monkeypatch.setattr(settings, "GITHUB_REPOSITORY", "BattloXX/Einsatzcockpit")
    monkeypatch.setattr(service, "now", lambda: STAMP)
    monkeypatch.setattr(gh, "_permit", lambda: True)
    client.cookies.set("session", sign_session(user.id), domain="testserver.local", path="/")
    gh._CACHE.clear()
    yield db, user, regular, client
    gh._CACHE.clear()


def post(client, action, **data):
    return client.post(URL + "/github/" + action,
                       data={"_csrf": client.cookies["ec_csrf"], **data}, follow_redirects=False)


def test_save_replace_delete_html_audit_and_editor(env, caplog):
    db, user, _, client = env
    assert post(client, "speichern", token=SECRET).status_code == 303
    db.expire_all()
    encrypted = service.read(db, "enc")
    assert encrypted != SECRET and decrypt_secret(encrypted) == SECRET
    assert service.read(db, "set_by") == str(user.id)
    html = client.get(URL).text
    assert SECRET not in html and encrypted not in html and 'name="k_github_read_token_enc"' not in html
    assert 'type="password" autocomplete="off"' in html
    assert post(client, "speichern", token=OTHER).status_code == 303
    db.expire_all()
    assert decrypt_secret(service.read(db, "enc")) == OTHER
    protected = list(db.scalars(select(SystemSettings.key).where(SystemSettings.key.startswith(service.PREFIX))))
    for key in protected:
        response = client.post(URL, data={"_csrf": client.cookies["ec_csrf"], "new_key": key,
                                          "new_value": SECRET}, follow_redirects=False)
        assert response.status_code == 400
    assert client.post(URL, data={"_csrf": client.cookies["ec_csrf"], "new_key": "GITHUB_READ_TOKEN_ENC",
                                  "new_value": SECRET}, follow_redirects=False).status_code == 400
    client.post(URL, data={"_csrf": client.cookies["ec_csrf"], "k_github_read_token_enc": SECRET})
    db.expire_all()
    assert decrypt_secret(service.read(db, "enc")) == OTHER
    assert post(client, "loeschen").status_code == 400
    assert post(client, "loeschen", confirm="ja").status_code == 303
    db.expire_all()
    assert not service.read(db, "enc")
    audits = list(db.scalars(select(AuditLog).where(AuditLog.action.like("github_token.%"))))
    assert {a.action for a in audits} == {"github_token.set", "github_token.replaced", "github_token.deleted"}
    assert SECRET not in repr([(a.action, a.payload_json) for a in audits]) + caplog.text
    assert OTHER not in client.get(URL).text + caplog.text


def test_priority_cache_rotation_decryption(env, monkeypatch):
    db, user, _, _ = env
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", OTHER)
    assert service.resolve(db) == (OTHER, "Umgebungsvariable", False)
    service.save(db, SECRET, user.id)
    db.flush()
    calls = []
    def handler(request):
        calls.append(request.headers["authorization"])
        return httpx.Response(200, json={"object": {"sha": "abcdef12"}})
    github = gh.GitHubReadonly(httpx.MockTransport(handler), db=db)
    github.head_of_main()
    service.save(db, OTHER, user.id)
    db.flush()
    github.head_of_main()
    assert calls == ["Bearer " + SECRET, "Bearer " + OTHER]
    monkeypatch.setattr(settings, "FERNET_KEY", "broken")
    assert service.resolve(db) == ("", "DB", True)
    assert github.head_of_main()["code"] == "nicht_autorisiert"
    assert service.status(db)["status"] == "ungueltig"


@pytest.mark.parametrize("header,expected", [("2026-11-26 10:00:00 UTC", "2026-11-26T10:00:00"),
                                            ("2026-11-26T12:00:00+02:00", "2026-11-26T10:00:00"),
                                            (None, ""), ("broken", "")])
def test_header_parsing_and_throttle(env, monkeypatch, header, expected):
    db, user, _, _ = env
    service.save(db, SECRET, user.id)
    db.flush()
    service.observe(db, SECRET, 200, header)
    assert service.read(db, "expires_at") == expected
    first = service.read(db, "last_check_at")
    service.observe(db, SECRET, 401, None)
    assert service.read(db, "last_status") == "ok"
    monkeypatch.setattr(service, "now", lambda: STAMP + timedelta(hours=1))
    service.observe(db, SECRET, 200, header)
    assert service.read(db, "last_check_at") == first  # Unveraendert: kein Schreibzugriff.
    service.observe(db, SECRET, 401, None)
    assert service.read(db, "last_status") == "ungueltig"
    assert service.read(db, "last_check_at") != first


@pytest.mark.parametrize("days,state", [(31, "ok"), (30, "laeuft_bald_ab"), (8, "laeuft_bald_ab"),
                                        (7, "laeuft_bald_ab"), (1, "laeuft_bald_ab"), (0, "abgelaufen"),
                                        (-1, "abgelaufen")])
def test_expiry_boundaries(env, days, state):
    db, user, _, _ = env
    service.save(db, SECRET, user.id)
    service.put(db, "expires_at", (STAMP + timedelta(days=days)).isoformat())
    service.put(db, "last_status", "ok")
    db.flush()
    access = service.status(db)
    assert access["status"] == state and access["restlaufzeit_tage"] == days


@pytest.mark.parametrize("value", ["broken", "2026-10-26", "2026-10-27T10:00:00", "2027-12-02"])
def test_manual_expiry_invalid(env, value):
    with pytest.raises(ValueError):
        service.manual_expiry(value)


def test_manual_expiry_header_precedence(env):
    db, user, _, _ = env
    service.save(db, SECRET, user.id, "2026-12-01")
    db.flush()
    service.observe(db, SECRET, 200, None)
    assert service.read(db, "expiry_source") == "manuell"
    assert service.read(db, "expires_at") == "2026-12-01T00:00:00"
    assert service.manual_expiry((STAMP + timedelta(days=400)).isoformat())
    assert service.manual_expiry("") is None


def test_connection_local_transport(env, monkeypatch, caplog):
    db, _, _, client = env
    post(client, "speichern", token=SECRET)
    original = gh.GitHubReadonly
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"object": {"sha": "abcdef12"}},
                              headers={"github-authentication-token-expiration": "2026-11-26 10:00:00 UTC",
                                       "x-ratelimit-remaining": "17"})
    monkeypatch.setattr(gh, "GitHubReadonly", lambda **kwargs: original(httpx.MockTransport(handler), **kwargs))
    response = post(client, "testen")
    page = client.get(response.headers["location"])
    assert "Repository erreichbar: ja" in page.text and "Berechtigungen ausreichend: ja" in page.text
    assert "Rate-Limit-Rest: 17" in page.text and len(calls) >= 3
    assert SECRET not in page.text + caplog.text
    db.expire_all()
    assert service.read(db, "expiry_source") == "github"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "github_token.tested"))


def test_csrf_role_device_and_mfa(env, monkeypatch):
    db, user, regular, client = env
    assert client.post(URL + "/github/speichern", data={"token": SECRET}).status_code == 403
    client.cookies.set("session", sign_session(regular.id), domain="testserver.local", path="/")
    assert post(client, "speichern", token=SECRET).status_code == 403
    client.cookies.set("session", sign_session(user.id), domain="testserver.local", path="/")
    user.is_device = True
    db.commit()
    assert post(client, "speichern", token=SECRET).status_code == 403
    user.is_device = False
    db.add(SystemSettings(key="admin_2fa_enabled", value="true"))
    db.commit()
    # Die globale Middleware verlangt ebenfalls MFA. Route direkt pruefen, um
    # zusaetzlich die eigene Frischepruefung unabhaengig von der Middleware zu testen.
    from types import SimpleNamespace
    from app.routers.ui_admin import github_token_action
    request = SimpleNamespace(state=SimpleNamespace(is_device=False, qr_incident_id=None, qr_lage_id=None,
                                                    mfa_at=None, user=user))
    for action in ("speichern", "loeschen"):
        response = github_token_action(action, request, db)
        assert "github_mfa=1" in response.headers["location"]
    assert not service.read(db, "enc")


@pytest.mark.parametrize("state", ["nicht_gesetzt", "ok", "laeuft_bald_ab", "abgelaufen", "ungueltig",
                                    "keine_berechtigung", "eingeschraenkt"])
def test_mcp_block_and_finding(env, monkeypatch, state):
    db, user, _, _ = env
    if state != "nicht_gesetzt":
        service.save(db, SECRET, user.id)
        service.put(db, "last_status", "ok" if state in ("laeuft_bald_ab", "abgelaufen") else state)
        if state in ("laeuft_bald_ab", "abgelaufen"):
            service.put(db, "expires_at", (STAMP + timedelta(days=7 if state == "laeuft_bald_ab" else -1)).isoformat())
        db.flush()
    monkeypatch.setattr(code_tools, "_collect_code", lambda *a, **kw: {"status": "ok", "evidence": {}})
    for name in ("admin_ci_status", "admin_code_analyse", "admin_codex_auftrag_erstellen"):
        answer = code_tools.collect_code(db, name)
        assert answer["github_zugang"]["status"] == state
        assert SECRET not in json.dumps(answer)
        assert set(("status", "meldung", "restlaufzeit_tage", "hinterlegt_am", "ablauf_am", "quelle", "hilfe")) <= answer["github_zugang"].keys()
    overview = registry.collect(db, "admin_system_uebersicht")
    assert overview["github_zugang"]["status"] == state
    monkeypatch.setattr(settings, "GIT_SHA", "")
    result = check_deploy(db, Budget())
    assert any(f.kern == "github.token" for f in result.findings) == (state in (
        "laeuft_bald_ab", "abgelaufen", "ungueltig", "keine_berechtigung"))
    assert SECRET not in repr(result)


@pytest.mark.parametrize("field", ["is_device", "qr_incident_id", "qr_lage_id"])
def test_device_and_qr_sessions_rejected_for_all_actions(env, field):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.routers.ui_admin import github_token_action
    db, user, _, _ = env
    state = dict(is_device=False, qr_incident_id=None, qr_lage_id=None, mfa_at=None, user=user)
    state[field] = True if field == "is_device" else 1
    request = SimpleNamespace(state=SimpleNamespace(**state))
    for action in ("speichern", "loeschen", "testen"):
        with pytest.raises(HTTPException) as exc:
            github_token_action(action, request, db)
        assert exc.value.status_code == 403


def test_fresh_mfa_and_manual_validation_route(env):
    import time
    db, user, _, client = env
    db.add(SystemSettings(key="admin_2fa_enabled", value="true"))
    db.commit()
    # Bereits MFA-bestaetigte Session, aber nicht frisch genug fuer eine Mutation.
    client.cookies.set("session", sign_session(user.id, mfa_at=int(time.time()) - 400,
                                               session_started_at=int(time.time()) - 600),
                       domain="testserver.local", path="/")
    assert "github_mfa=1" in post(client, "speichern", token=SECRET).headers["location"]
    client.cookies.set("session", sign_session(user.id, mfa_at=int(time.time())),
                       domain="testserver.local", path="/")
    response = post(client, "speichern", token=SECRET, expires_at="broken")
    assert "github_error=1" in response.headers["location"] and SECRET not in response.headers["location"]
    assert "github_message=" in post(client, "speichern", token=SECRET).headers["location"]
    assert post(client, "loeschen", confirm="ja").status_code == 303


@pytest.mark.parametrize("http_status,state", [(401, "ungueltig"), (403, "keine_berechtigung"),
                                              (404, "keine_berechtigung"), (429, "eingeschraenkt")])
def test_real_client_mcp_and_wire_response(env, monkeypatch, http_status, state, caplog):
    from sqlalchemy.orm import sessionmaker
    from app.admin_mcp.server import response
    db, user, _, _ = env
    service.save(db, SECRET, user.id)
    db.commit()
    monkeypatch.setattr(gh, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    original = gh.GitHubReadonly
    def handler(request):
        assert request.headers["Authorization"] == "Bearer " + SECRET
        return httpx.Response(http_status, text=SECRET)
    monkeypatch.setattr(code_tools, "GitHubReadonly", lambda: original(httpx.MockTransport(handler)))
    place = {"datei": "app/services/code_analyse.py", "funktion": "group_logs", "exception_typ": "ValueError"}
    monkeypatch.setattr(code_tools.code, "group_logs", lambda rows: [dict(place)])
    for name in ("admin_ci_status", "admin_code_analyse", "admin_codex_auftrag_erstellen"):
        result = code_tools.collect_code(db, name, **({"fehlerstelle": place} if name.endswith("erstellen") else {}))
        wire = response(name, "test", result)
        assert wire["github_zugang"]["status"] == state
        assert wire["status"] == "eingeschraenkt"
        assert any(v.get("status") == "warn" for v in wire["evidenz"].values() if isinstance(v, dict))
        assert SECRET not in json.dumps(wire) + caplog.text


def test_environment_rotation_drops_stale_metadata(env, monkeypatch):
    db, _, _, _ = env
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", SECRET)
    service.observe(db, SECRET, 200, "2026-11-26 10:00:00 UTC")
    assert service.status(db)["ablauf_am"]
    monkeypatch.setattr(settings, "GITHUB_READ_TOKEN", OTHER)
    status = service.status(db)
    assert status["status"] == "nicht_getestet" and status["ablauf_am"] is None



def test_future_less_than_one_day_is_warning_not_expired(env):
    db, user, _, _ = env
    service.save(db, SECRET, user.id, (STAMP + timedelta(hours=1)).isoformat())
    db.flush()
    access = service.status(db)
    assert access["restlaufzeit_tage"] == 0 and access["status"] == "laeuft_bald_ab"


def test_github_expiry_overrides_manual_and_broken_header_is_ignored(env, monkeypatch):
    db, user, _, _ = env
    service.save(db, SECRET, user.id, "2026-12-01")
    db.flush()
    service.observe(db, SECRET, 200, "2026-11-26 10:00:00 UTC")
    assert service.read(db, "expiry_source") == "github"
    assert service.read(db, "expires_at") == "2026-11-26T10:00:00"
    monkeypatch.setattr(service, "now", lambda: STAMP + timedelta(hours=1))
    service.observe(db, SECRET, 200, "broken")
    assert service.read(db, "expires_at") == "2026-11-26T10:00:00"
    assert service.read(db, "expiry_source") == "github"
    service.observe(db, SECRET, 200, None)
    assert service.read(db, "expires_at") == "" and service.read(db, "expiry_source") == "unbekannt"


def test_diagnose_current_http_error_with_readonly_session(env, monkeypatch):
    from app.services.diagnose.registry import CHECKS, execute_check
    db, user, _, _ = env
    service.save(db, SECRET, user.id)
    db.commit()
    monkeypatch.setattr(settings, "GIT_SHA", "abcdef12")
    monkeypatch.setattr(gh.GitHubReadonly, "compare", lambda *args: gh.unavailable("nicht_autorisiert"))
    result = execute_check(db.get_bind(), CHECKS["deploy.version"])
    assert any(f.kern == "github.token" and f.evidenz == {"status": "ungueltig", "tage": None}
               for f in result.findings)
    assert service.read(db, "last_status") == "nicht_getestet"  # Diagnose bleibt SELECT-only.
