"""S8: Offline-HTTP, echte Signaturen und explizite Vertrauensanker."""
import asyncio
import base64
import hashlib
import io
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState
from uvicorn.protocols.utils import ClientDisconnected

from app.config import Settings, settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.infoscreen_agent import InfoscreenRelease, InfoscreenReleaseKey
from app.models.master import SystemSettings
from app.models.user import AuditLog
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_github_import as github
from app.services import infoscreen_agent_releases as releases
from tests.test_infoscreen_agent_releases import SCRIPT, login, make_deb, post

BASE = "/admin/agent-releases"
ASSETS = "https://github.com/example/agent/releases/download/v1/"


@pytest.fixture
def source(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {"release": pem})
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_KEY_FINGERPRINT", "")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_GITHUB_TOKEN", "")
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_INSTALLER_ENABLED", False)
    with SessionLocal() as db:
        for row in db.query(InfoscreenReleaseKey).all() + db.query(InfoscreenRelease).all():
            db.delete(row)
        for cache in db.query(SystemSettings).filter(SystemSettings.key.startswith(github._cache_key())).all():
            db.delete(cache)
        db.commit()
    yield key, pem
    with SessionLocal() as db:
        for row in db.query(InfoscreenReleaseKey).all() + db.query(InfoscreenRelease).all():
            db.delete(row)
        for cache in db.query(SystemSettings).filter(SystemSettings.key.startswith(github._cache_key())).all():
            db.delete(cache)
        db.commit()


def fixture_release(key, version="1.0", channel="stable"):
    package = make_deb(version)
    metadata = releases.ReleaseMetadata(version=version, channel=channel, arch="amd64", sha256=hashlib.sha256(package).hexdigest(),
        size=len(package), min_version="0", key_id="release", signature="x")
    metadata.signature = base64.b64encode(key.sign(releases.canonical_manifest(metadata))).decode()
    name = f"ec-infoscreen-agent_{version}_amd64.deb"
    artifact = metadata.model_dump(exclude={"version", "channel", "key_id"})
    artifact["package"] = name
    doc = {"schema": 1, "version": version, "channel": channel, "key_id": "release", "artifacts": [artifact],
           "install_sh": {"file": "install.sh", "signature": base64.b64encode(key.sign(SCRIPT)).decode()}, "key_file": "release-key.pem"}
    files = {"release.json": json.dumps(doc).encode(), name: package, "install.sh": SCRIPT}
    row = {"tag_name": version, "prerelease": channel == "beta", "assets": [
        {"name": name, "browser_download_url": ASSETS + name} for name in files]}
    return row, files, doc


def http_fixture(rows, files, *, mutate=None, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request)
        if mutate:
            response = mutate(request)
            if response is not None:
                return response
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=rows, headers={"ETag": '"v1"'})
        return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[-1]])
    return httpx.Client(transport=httpx.MockTransport(handler))


def run_import(http):
    with http, SessionLocal() as db:
        return github.import_releases(db, http)


@pytest.mark.parametrize("version,channel", [("1.0", "stable"), ("2026.11.08~beta.1", "beta")])
def test_success_duplicate_etag(source, version, channel):
    row, files, _ = fixture_release(source[0], version, channel)
    assert run_import(http_fixture([row], files))[0]["status"] == "importiert"
    calls = []
    def unchanged(req):
        if req.url.host == "api.github.com":
            assert req.headers["if-none-match"] == '"v1"'
            return httpx.Response(304)
    assert run_import(http_fixture([], files, mutate=unchanged, calls=calls))[0]["status"] == "uebersprungen"
    assert not any(req.url.path.endswith(".deb") for req in calls)
    with SessionLocal() as db:
        release = db.query(InfoscreenRelease).one()
        assert release.rollout_percent == 0 and not release.blocked and release.channel == channel
        assert releases.read_installer(release, db) == SCRIPT
        assert github.sync_status(db)["last_sync"]


@pytest.mark.parametrize("change,code", [
    ("signature", "signature_invalid"), ("hash", "hash_mismatch"), ("script", "signature_invalid"),
    ("large", "size_limit"), ("schema", "release_json_invalid"), ("invalid", "release_json_invalid"),
    ("extra", "release_json_invalid"), ("bool", "release_json_invalid"), ("name", "release_json_invalid"),
    ("channel", "release_json_invalid"), ("unknown_key", "release_json_invalid"),
])
def test_invalid_release_does_not_stop_next(source, tmp_path, change, code):
    row, files, doc = fixture_release(source[0])
    if change == "signature":
        doc["artifacts"][0]["signature"] = base64.b64encode(b"x" * 64).decode()
    elif change == "hash":
        files["ec-infoscreen-agent_1.0_amd64.deb"] = b"x" * len(files["ec-infoscreen-agent_1.0_amd64.deb"])
    elif change == "script":
        files["install.sh"] += b"changed"
    elif change == "large":
        doc["artifacts"][0]["size"] = releases.MAX_PACKAGE + 1
    elif change == "schema":
        doc["schema"] = 2
    elif change == "extra":
        doc["unexpected"] = True
    elif change == "bool":
        doc["schema"] = True
    elif change == "name":
        doc["artifacts"][0]["package"] = "../../evil.deb"
    elif change == "channel":
        row["prerelease"] = True
    elif change == "unknown_key":
        doc["key_id"] = "unknown"
    files["release.json"] = b"broken{" if change == "invalid" else json.dumps(doc).encode()
    good, good_files, _ = fixture_release(source[0], "2.0")
    # Pro Release eine eigene Asset-URL fuer getrennte Metadaten.
    for asset in good["assets"]:
        asset["browser_download_url"] = asset["browser_download_url"].replace("/v1/", "/v2/")
    def next_release(request):
        if "/v2/" in request.url.path:
            return httpx.Response(200, content=good_files[request.url.path.rsplit("/", 1)[-1]])
    results = run_import(http_fixture([row, good], files, mutate=next_release))
    assert [r["status"] for r in results] == ["abgelehnt", "importiert"]
    assert results[0]["code"] == code
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limit(source, status):
    assert run_import(http_fixture([], {}, mutate=lambda req: httpx.Response(status)))[0]["code"] == "rate_limited"


def test_missing_pin_no_http(source, monkeypatch):
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {})
    def no_http(req):
        pytest.fail("Ohne Pin kein Netz")
    assert run_import(httpx.Client(transport=httpx.MockTransport(no_http)))[0]["code"] == "key_not_pinned"


@pytest.mark.parametrize("url", ["http://github.com/key", "https://evil.example/key", "https://github.com.evil.example/key",
    "https://github.com:444/key", "https://user@github.com/key"])
def test_url_allowlist(source, url):
    calls = []
    with http_fixture([], {}, calls=calls) as http, io.BytesIO() as out:
        with pytest.raises(github.ImportFailure, match="host_not_allowed"):
            github.download(http, url, out, 100)
    assert not calls


@pytest.mark.parametrize("host", ["evil.example", "release-assets.githubusercontent.com"])
def test_redirect_every_hop_and_token(source, monkeypatch, host):
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_GITHUB_TOKEN", "TOP_SECRET")
    calls = []
    def redirect(req):
        if req.url.host == "api.github.com":
            assert req.headers["authorization"] == "Bearer TOP_SECRET"
            return httpx.Response(302, headers={"Location": "https://github.com/second"})
        assert "authorization" not in req.headers
        if req.url.host == "github.com":
            return httpx.Response(307, headers={"Location": f"https://{host}/asset"})
        return httpx.Response(200, content=b"ok")
    with http_fixture([], {}, mutate=redirect, calls=calls) as http, io.BytesIO() as out:
        if host == "evil.example":
            with pytest.raises(github.ImportFailure, match="host_not_allowed"):
                github.download(http, "https://api.github.com/first", out, 100)
            assert len(calls) == 2
        else:
            github.download(http, "https://api.github.com/first", out, 100)
            assert out.read() == b"ok" and len(calls) == 3


def test_size_streaming_and_redirect_limit(source):
    for response in [httpx.Response(200, headers={"Content-Length": "1001"}), httpx.Response(200, content=b"x" * 1001),
                     httpx.Response(302, headers={"Location": "https://github.com/again"})]:
        with http_fixture([], {}, mutate=lambda req: response) as http, io.BytesIO() as out:
            with pytest.raises(github.ImportFailure, match="redirect_limit" if response.status_code == 302 else "size_limit"):
                github.download(http, "https://github.com/start", out, 1000)


def confirm_data(pem, **changes):
    return {"pem": pem, "key_id": "release", "quelle": "repo",
            "fingerprint": releases.fingerprint(releases.parse_public_key(pem)), **changes}


def test_key_ui_confirmation_rotation_audit_and_import(source, client, monkeypatch):
    login(client, "system_admin")
    monkeypatch.setattr(github, "repository_key", lambda db: source[1])
    response = post(client, BASE + "/keys/preview", {"quelle": "repo"})
    assert response.status_code == 200 and confirm_data(source[1])["fingerprint"] in response.text
    with SessionLocal() as db:
        assert db.query(InfoscreenReleaseKey).count() == 0
    for fp in ["", "wrong", confirm_data(source[1])["fingerprint"] + " "]:
        assert post(client, BASE + "/keys/confirm", confirm_data(source[1], fingerprint=fp)).status_code == 422
    assert post(client, BASE + "/keys/confirm", confirm_data(source[1])).status_code == 200
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(github, "repository_key", lambda db: other)
    assert post(client, BASE + "/keys/preview", {"quelle": "repo"}).status_code == 200
    assert post(client, BASE + "/keys/confirm", confirm_data(other)).status_code == 422
    with SessionLocal() as db:
        row = db.query(InfoscreenReleaseKey).one()
        assert row.aktiv and row.pem == source[1] and row.quelle == "repo" and row.bestaetigt_von
        key_id = row.id
    assert post(client, f"{BASE}/keys/{key_id}/status", {"aktiv": "0"}).status_code == 200
    assert post(client, BASE + "/keys/confirm", confirm_data(other, quelle="manuell")).status_code == 200
    assert post(client, f"{BASE}/keys/{key_id}/status", {"aktiv": "1"}).status_code == 422
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(InfoscreenReleaseKey).count() == 2
        assert db.query(AuditLog).filter_by(action="infoscreen_agent.key_deactivated", entity_id=key_id).count() == 1


def test_key_routes_rights_csrf(source, client):
    paths = ["/keys/preview", "/keys/confirm", "/keys/1/status", "/github/import"]
    login(client, "org_admin")
    for path in paths:
        assert post(client, BASE + path, confirm_data(source[1])).status_code == 403
    login(client, "system_admin")
    for path in paths:
        assert client.post(BASE + path, data=confirm_data(source[1])).status_code == 403
    response = post(client, BASE + "/keys/preview", {"pem": "-----INVALID-----", "quelle": "manuell"})
    assert response.status_code == 200 and "release_key_invalid" in response.text
    response = post(client, BASE + "/keys/preview", {"pem": source[1], "quelle": "manuell", "key_id": "release"})
    assert response.status_code == 200 and "Schluessel bestaetigen" in response.text


def test_public_keys_and_fingerprint_precedence(source, client, monkeypatch):
    login(client, "system_admin")
    dbkey = Ed25519PrivateKey.generate()
    pem = dbkey.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    fp = releases.fingerprint(dbkey.public_key())
    assert post(client, BASE + "/keys/confirm", confirm_data(pem)).status_code == 200
    with SessionLocal() as db:
        # Konflikt: Setting gewinnt; ueberschatteter DB-Key wird kein Installer-Pin.
        assert releases.fingerprint(releases.public_keys(db)["release"]) == confirm_data(source[1])["fingerprint"]
        assert releases.release_fingerprint(db) == confirm_data(source[1])["fingerprint"]
        monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {"legacy": source[1]})
        assert len(releases.public_keys(db)) == 2
        assert releases.release_fingerprint(db) == fp
        monkeypatch.setattr(settings, "INFOSCREEN_AGENT_KEY_FINGERPRINT", "explicit")
        assert releases.release_fingerprint(db) == "explicit"
        monkeypatch.setattr(settings, "INFOSCREEN_AGENT_KEY_FINGERPRINT", "")
        db.query(InfoscreenReleaseKey).one().aktiv = False
        db.flush()
        assert set(releases.public_keys(db)) == {"legacy"}
        assert releases.release_fingerprint(db) == confirm_data(source[1])["fingerprint"]
        db.rollback()


def test_db_key_enables_installer_and_banner(source, client, monkeypatch):
    login(client, "system_admin")
    assert post(client, BASE + "/keys/confirm", confirm_data(source[1])).status_code == 200
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {})
    row, files, _ = fixture_release(source[0])
    assert run_import(http_fixture([row], files))[0]["status"] == "importiert"
    with SessionLocal() as db:
        assert not releases.installer_ready(db)
        db.query(InfoscreenRelease).one().rollout_percent = 10
        db.commit()
        assert releases.installer_ready(db)
    assert client.get("/infoscreen-agent/install.sh").content == SCRIPT
    with SessionLocal() as db:
        db.query(InfoscreenReleaseKey).one().aktiv = False
        db.commit()
        assert not releases.installer_ready(db)
    assert client.get("/infoscreen-agent/install.sh").status_code == 404


def test_repo_key_preview_download_never_pins(source):
    row = {"assets": [{"name": "release-key.pem", "browser_download_url": ASSETS + "release-key.pem"}]}
    with http_fixture([row], {"release-key.pem": source[1].encode()}) as http, SessionLocal() as db:
        assert github.repository_key(db, http) == source[1]
        assert not db.query(InfoscreenReleaseKey).count()


@pytest.mark.parametrize("repo", ["http://evil/agent", "owner/name/path", "../repo", "owner/repo?token=x", "owner/repo#x"])
def test_repo_validation(repo):
    with pytest.raises(ValueError):
        Settings(INFOSCREEN_AGENT_GITHUB_REPO=repo)


@pytest.mark.asyncio
async def test_ws_disconnect_during_pause_and_close_race(monkeypatch):
    socket = AsyncMock()
    socket.receive_text.side_effect = WebSocketDisconnect()
    monkeypatch.setattr(events, "PING_S", 60)
    with pytest.raises(WebSocketDisconnect):
        await asyncio.wait_for(events._wait_next_ping(socket), timeout=.5)
    monkeypatch.setattr(events, "_valid", lambda *args: False)
    socket.application_state = WebSocketState.CONNECTED
    for error in [ClientDisconnected(), WebSocketDisconnect()]:
        socket.close.side_effect = error
        await events._deliver_one((1, 1, "session"), socket, {})


@pytest.mark.asyncio
async def test_sync_disabled_and_leader_lock(source, tmp_path, monkeypatch):
    from app.services.background_runtime import BackgroundRuntime, supervised_loop
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_GITHUB_SYNC_ENABLED", False)
    work = AsyncMock(return_value=github.SYNC_S)
    monkeypatch.setattr(github, "im_db_thread", work)
    await github.infoscreen_agent_github_sync_loop()
    work.assert_not_called()
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_GITHUB_SYNC_ENABLED", True)
    def start():
        return [asyncio.create_task(supervised_loop(github.infoscreen_agent_github_sync_loop))]
    leader = BackgroundRuntime(str(tmp_path / "leader.lock"), start)
    follower = BackgroundRuntime(str(tmp_path / "leader.lock"), start)
    try:
        leader.try_leadership()
        follower.try_leadership()
        await asyncio.sleep(.02)
        assert leader.is_leader and not follower.is_leader
        work.assert_awaited_once_with(github.sync_once)
        assert leader.loops["infoscreen_agent_github_sync_loop"]["last_success_at"]
    finally:
        for task in leader.tasks:
            task.cancel()
        await asyncio.gather(*leader.tasks, return_exceptions=True)
        leader.release()
        follower.release()


def test_ui_import_htmx_keeps_keys_and_manual_policy(source, client, monkeypatch):
    login(client, "system_admin")
    row, files, _ = fixture_release(source[0])
    actual = github.import_releases
    # Reale Pruefkette mit MockTransport, auch beim Route-Aufruf.
    def offline(db):
        with http_fixture([row], files) as http:
            return actual(db, http)
    monkeypatch.setattr(github, "import_releases", offline)
    wake = AsyncMock()
    monkeypatch.setattr(events, "publish_update_available", wake)
    response = post(client, BASE + "/github/import")
    assert response.status_code == 200 and 'id="agent-releases"' in response.text
    assert "importiert" in response.text and "Letzter Abgleich" in response.text
    assert "<html" not in response.text
    with SessionLocal() as db:
        assert not db.query(InfoscreenReleaseKey).count()
        assert db.query(InfoscreenRelease).one().rollout_percent == 0
    wake.assert_not_called()
    assert "uebersprungen" in post(client, BASE + "/github/import").text


def test_key_id_not_pinned_and_bad_pem_formats(source, monkeypatch):
    row, files, _ = fixture_release(source[0])
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {"legacy": source[1]})
    assert run_import(http_fixture([row], files))[0]["code"] == "key_not_pinned"
    for pem in (source[1] + "\nmalicious data", source[1] + source[1], source[1].replace("PUBLIC KEY", "PRIVATE KEY")):
        with pytest.raises(ValueError, match="release_key_invalid"):
            releases.parse_public_key(pem)


def test_sync_remembers_last_run_on_leader_change(source, monkeypatch):
    row, files, _ = fixture_release(source[0])
    assert run_import(http_fixture([row], files))[0]["status"] == "importiert"
    def no_import(db):
        pytest.fail("Abgleich innerhalb von 24 h wiederholt")
    monkeypatch.setattr(github, "import_releases", no_import)
    assert 0 < github.sync_once() <= github.SYNC_S


@pytest.mark.parametrize("value", ["1-", "1.0:abc", "1:bad", "1.0-bad_revision"])
def test_debian_version_invalid(value):
    with pytest.raises(ValueError, match="version_invalid"):
        releases.valid_version(value)


def test_repository_key_size_and_format(source):
    row = {"assets": [{"name": "release-key.pem", "browser_download_url": ASSETS + "release-key.pem"}]}
    for pem, code in [(b"x" * (github.MAX_KEY + 1), "size_limit"), (b"bad PEM", "release_key_invalid")]:
        with http_fixture([row], {"release-key.pem": pem}) as http, SessionLocal() as db:
            with pytest.raises(github.ImportFailure, match=code):
                github.repository_key(db, http)


def test_multiple_arches_rollback_together(source, tmp_path):
    row, files, document = fixture_release(source[0])
    package = make_deb("1.0", "arm64")
    metadata = releases.ReleaseMetadata(version="1.0", channel="stable", arch="arm64", key_id="release", min_version="0",
        size=len(package), sha256=hashlib.sha256(package).hexdigest(), signature=base64.b64encode(b"x" * 64).decode())
    name = "ec-infoscreen-agent_1.0_arm64.deb"
    document["artifacts"].append({**metadata.model_dump(exclude={"version", "channel", "key_id"}), "package": name})
    row["assets"].append({"name": name, "browser_download_url": ASSETS + name})
    files[name] = package
    files["release.json"] = json.dumps(document).encode()
    assert run_import(http_fixture([row], files))[0]["code"] == "signature_invalid"
    with SessionLocal() as db:
        assert not db.query(InfoscreenRelease).count()
    assert not list(tmp_path.iterdir())


def test_etag_cache_fits_mariadb_text_and_shrinks(source):
    from datetime import UTC, datetime
    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        large = {"pages": {"metadata": "x" * 160000}, "last_sync": now.isoformat()}
        github._save_status(db, large, now)
        db.commit()
        assert github.sync_status(db) == large
        cache_rows = db.query(SystemSettings).filter(SystemSettings.key.startswith(github._cache_key())).all()
        assert len(cache_rows) > 2 and all(len(row.value.encode()) < 65536 for row in cache_rows)
        small = {"pages": {}}
        github._save_status(db, small, now)
        db.commit()
        assert github.sync_status(db) == small
        assert db.query(SystemSettings).filter(SystemSettings.key.startswith(github._cache_key())).count() == 2
