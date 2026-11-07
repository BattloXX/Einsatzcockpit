"""S6: echte Ed25519-Signaturen, Artefakte und Update-Policy ueber HTTP."""
import base64
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDeviceEvent, InfoscreenRelease, InfoscreenReleaseUploadToken
from app.models.master import OrgSettings
from app.services import infoscreen_agent_events as events
from app.services import infoscreen_agent_releases as service
from app.services import infoscreen_agent_service as devices
from tests.conftest import TestingSession as SessionLocal
from tests.test_infoscreen_agent import create_device
from tests.test_systemstatus_nav import _login, _make_org_admin

BASE = "/api/v1/infoscreen-agent"
SCRIPT = b"#!/bin/bash\nmain() { echo 'Installation'; }\nmain\n"


def make_deb(version="1.0", arch="amd64", package="ec-infoscreen-agent"):
    control = f"Package: {package}\nVersion: {version}\nArchitecture: {arch}\n".encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo("./control")
        info.size = len(control)
        archive.addfile(info, io.BytesIO(control))
    result = b"!<arch>\n"
    for name, content in (("debian-binary", b"2.0\n"), ("control.tar.gz", stream.getvalue()), ("data.tar.gz", b"data")):
        header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(content):<10}`\n".encode()
        assert len(header) == 60
        result += header + content + (b"\n" if len(content) % 2 else b"")
    return result


@pytest.fixture
def signing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://agent.example")
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {"test": pem})
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_INSTALLER_ENABLED", True)
    with SessionLocal() as db:
        # Releases sind global: alle Tests muessen ihren eigenen Ausgangszustand herstellen.
        set_tenant_context(db, None)
        for row in db.query(InfoscreenRelease).all():
            db.delete(row)
        token = service.create_upload_token(db)
        db.commit()
    return key, token


def upload(client, signing, *, version="1.0", arch="amd64", channel="stable", min_version="0",
           changes=None, package=None, script_signature=None, bearer=None):
    key, token = signing
    content = package if package is not None else make_deb(version, arch)
    data = {"version": version, "arch": arch, "channel": channel, "min_version": min_version,
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), "key_id": "test", "signature": "x"}
    data.update(changes or {})
    if data.get("size", 0) > 0 and data.get("size", 0) <= service.MAX_PACKAGE:
        # Auch absichtlich falsche Hash-/Versionsmetadaten korrekt signieren.
        try:
            data["signature"] = base64.b64encode(key.sign(service.canonical_manifest(service.ReleaseMetadata(**data)))).decode()
        except ValueError:
            pass
    if changes and "signature" in changes:
        data["signature"] = changes["signature"]
    signature = script_signature or base64.b64encode(key.sign(SCRIPT)).decode()
    return client.post(BASE + "/releases", headers={"Authorization": "Bearer " + (bearer or token)},
                       data={"metadata": json.dumps(data), "signature_install_sh": signature},
                       files={"package": ("../../evil.deb", content), "install_sh": ("install.sh", SCRIPT)})


def release(client, signing, **kwargs):
    response = upload(client, signing, **kwargs)
    assert response.status_code == 200, response.text
    row_id = response.json()["id"]
    with SessionLocal() as db:
        row = db.get(InfoscreenRelease, row_id)
        row.rollout_percent = 100
        db.commit()
    return row_id


def paired(client, org_id=1, channel="stable"):
    device_id, code = create_device(org_id)
    response = client.post(BASE + "/pair", json={"code": code, "name": "Halle", "channel": channel})
    assert response.status_code == 200, response.text
    return device_id, {"Authorization": "Bearer " + response.json()["device_token"]}


def manifest(client, headers, current="0", arch="amd64"):
    return client.get(BASE + "/update/manifest", params={"arch": arch, "current": current}, headers=headers)


def test_upload_canonical_storage_and_first_download(client, signing, tmp_path):
    response = upload(client, signing)
    assert response.status_code == 200, response.text
    row_id = response.json()["id"]
    device_id, code = create_device()
    headers = {"Authorization": "Bearer " + code}
    assert client.get(BASE + "/download/latest?arch=amd64", headers=headers).status_code == 404
    with SessionLocal() as db:
        row = db.get(InfoscreenRelease, row_id)
        assert row.rollout_percent == 0 and not row.blocked
        assert service.canonical_manifest(row) == (
            b"ec-infoscreen-agent\nversion=1.0\nchannel=stable\narch=amd64\nsha256=" + row.sha256.encode()
            + f"\nsize={row.size}\nmin_version=0".encode())
        row.rollout_percent = 10
        db.commit()
    response = client.get(BASE + "/download/latest?arch=amd64", headers=headers, follow_redirects=False)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    signing[0].public_key().verify(base64.b64decode(data["signature"]),
                                   service.canonical_manifest(service.ReleaseMetadata(**{k: v for k, v in data.items()
                                                                                        if k != "download_url"})))
    package = client.get(data["download_url"], headers={**headers, "Range": "bytes=0-2"}, follow_redirects=False)
    assert package.status_code == 200 and "location" not in package.headers
    assert package.headers["content-type"] == "application/vnd.debian.binary"
    assert len(package.content) == data["size"] == int(package.headers["content-length"])
    assert hashlib.sha256(package.content).hexdigest() == data["sha256"]
    with SessionLocal() as db:
        assert devices.peek_pairing_code(db, code).id == device_id
    for file in tmp_path.iterdir():
        assert file.stat().st_mode & 0o777 == 0o600 and not file.name.startswith("evil")
    assert upload(client, signing).status_code == 409
    assert not list(tmp_path.glob("*.tmp"))
    assert client.post(BASE + "/pair", json={"code": code}).status_code == 200
    assert client.get(data["download_url"], headers=headers).status_code == 401


@pytest.mark.parametrize("changes,package,script_signature,expected", [
    ({"signature": "invalid"}, None, None, "signature_invalid"),
    ({"signature": "ungueltig-ü"}, None, None, "signature_invalid"),
    ({"sha256": "0" * 64}, None, None, "hash_mismatch"),
    ({"size": 1}, None, None, "size_mismatch"),
    ({"size": service.MAX_PACKAGE + 1}, None, None, "metadata_invalid"),
    ({"version": "bad\nvalue"}, None, None, "metadata_invalid"),
    ({"arch": "i386"}, None, None, "metadata_invalid"),
    ({"size": True}, None, None, "metadata_invalid"),
    ({}, make_deb(package="other"), None, "package_mismatch"),
    ({}, make_deb(arch="arm64"), None, "package_mismatch"),
    ({}, make_deb(version="2.0"), None, "package_mismatch"),
    ({}, b"not an ar", None, "package_mismatch"),
    ({}, None, "invalid", "signature_invalid"),
])
def test_bad_uploads(client, signing, changes, package, script_signature, expected):
    response = upload(client, signing, changes=changes, package=package, script_signature=script_signature)
    assert response.status_code == 422 and response.json()["detail"] == expected, response.text


def test_scoped_token_revoked_empty_keys_and_no_secrets(client, signing, monkeypatch, caplog):
    _, token = signing
    for wrong in ("bad", "ecia_token"):
        assert upload(client, signing, bearer=wrong).status_code == 401
    with SessionLocal() as db:
        stored = db.query(InfoscreenReleaseUploadToken).filter_by(token_hash=hash_api_key(token)).one()
        assert stored.token_hash != token
        stored.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        db.commit()
    assert upload(client, signing).status_code == 401
    with SessionLocal() as db:
        stored = db.query(InfoscreenReleaseUploadToken).filter_by(token_hash=hash_api_key(token)).one()
        stored.revoked_at = None
        db.commit()
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {})
    response = upload(client, signing)
    assert response.status_code == 422 and response.json()["detail"] == "release_key_unknown"
    assert token not in caplog.text and "ecia_token" not in caplog.text


def test_package_size_limit_before_body(client, signing):
    response = client.post(BASE + "/releases", content=b"x", headers={
        "Authorization": "Bearer " + signing[1], "Content-Length": str(515 * 1024 * 1024)})
    assert response.status_code == 413


def test_manifest_matrix_and_intermediate_release(client, signing):
    stable = release(client, signing, version="1.0")
    beta = release(client, signing, version="3.0", channel="beta")
    release(client, signing, version="4.0", arch="arm64")
    release(client, signing, version="2.0", min_version="1.0")
    device_id, headers = paired(client)
    assert manifest(client, headers).json()["manifest"]["version"] == "1.0"
    assert manifest(client, headers, "1.0").json()["manifest"]["version"] == "2.0"
    assert manifest(client, headers, "2.0").json()["manifest"] is None
    assert manifest(client, headers, "1.0", "arm64").json()["manifest"]["arch"] == "arm64"
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device = db.get(InfoscreenDevice, device_id)
        device.update_pinned_version = "1.0"
        db.commit()
    assert manifest(client, headers).json()["manifest"]["version"] == "1.0"
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device = db.get(InfoscreenDevice, device_id)
        device.update_locked = True
        db.commit()
    data = manifest(client, headers).json()
    assert data["locked"] and data["manifest"] is None and not data["update_available"]
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device = db.get(InfoscreenDevice, device_id)
        device.update_locked = False
        device.update_pinned_version = None
        device.channel = "beta"
        db.commit()
    assert manifest(client, headers).json()["manifest"]["version"] == "3.0"
    assert client.get(f"{BASE}/download/artifact/{stable}", headers=headers).status_code == 404
    assert client.get(f"{BASE}/download/artifact/{beta}", headers=headers).status_code == 200
    with SessionLocal() as db:
        row = db.get(InfoscreenRelease, beta)
        row.blocked = True
        db.commit()
    assert manifest(client, headers).json()["manifest"] is None
    assert client.get(f"{BASE}/download/artifact/{beta}", headers=headers).status_code == 404


def test_canary_stable_bucket(client, signing):
    row_id = release(client, signing)
    device_id, headers = paired(client)
    bucket = service.rollout_bucket(device_id, "1.0")
    assert bucket == service.rollout_bucket(device_id, "1.0") and 0 <= bucket < 100
    with SessionLocal() as db:
        row = db.get(InfoscreenRelease, row_id)
        row.rollout_percent = bucket
        db.commit()
    for _ in range(2):
        assert not manifest(client, headers).json()["update_available"]
    with SessionLocal() as db:
        row = db.get(InfoscreenRelease, row_id)
        row.rollout_percent = bucket + 1
        db.commit()
    assert manifest(client, headers).json()["update_available"]


@pytest.mark.parametrize("activity,expected", [
    (SimpleNamespace(gsl=True, incident=False, letztes_ende=None), False),
    (SimpleNamespace(gsl=False, incident=True, letztes_ende=None), False),
    (SimpleNamespace(gsl=False, incident=False, letztes_ende=datetime(2026, 11, 7, 1, 55)), False),
    (SimpleNamespace(gsl=False, incident=False, letztes_ende=datetime(2026, 11, 7, 1, 50)), True),
    (SimpleNamespace(gsl=False, incident=False, letztes_ende=None), True),
])
def test_activity_window_and_critical_policy(client, signing, monkeypatch, activity, expected):
    device_id, _ = paired(client)
    monkeypatch.setattr(service, "lade_aktivitaet", lambda *a: activity)
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device = db.get(InfoscreenDevice, device_id)
        data = service.update_policy(db, device, datetime(2026, 11, 7, 2, 0))
        assert data["update_allowed"] is expected
        assert data["maintenance_window"] == {"start": "02:00", "end": "04:30", "timezone": "Europe/Vienna"}
        assert not data["critical_allowed"] and data["check_interval_h"] == 6
        policy = db.query(OrgSettings).filter_by(org_id=1).first()
        if policy is None:
            policy = OrgSettings(org_id=1)
            db.add(policy)
        policy.infoscreen_update_start, policy.infoscreen_update_end = "23:00", "01:00"
        policy.infoscreen_critical_allowed = True
        db.flush()
        data = service.update_policy(db, device, datetime(2026, 11, 7, 12, 0))
        assert data["critical_allowed"] and data["maintenance_window"]["start"] == "23:00"
        db.rollback()


def test_pair_download_expiry_replay_and_throttle(client, signing, caplog):
    release(client, signing)
    device_id, code = create_device()
    headers = {"Authorization": "Bearer " + code}
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        db.get(InfoscreenDevice, device_id).pairing_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    assert client.get(BASE + "/download/latest?arch=amd64", headers=headers).status_code == 401
    for _ in range(9):
        assert client.get(BASE + "/download/latest?arch=amd64", headers=headers).status_code == 401
    assert client.get(BASE + "/download/latest?arch=amd64", headers=headers).status_code == 429
    assert code not in caplog.text


@pytest.mark.parametrize("status", ["installing", "ok", "failed", "rolled_back"])
def test_update_result_and_unpair(client, signing, status):
    device_id, headers = paired(client)
    response = client.post(BASE + "/update/result", headers=headers,
                           json={"version": "1.0", "status": status, "error_code": "health_failed" if status == "failed" else None})
    assert response.status_code == 200 and response.json() == {} and response.headers["cache-control"] == "no-store"
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        device = db.get(InfoscreenDevice, device_id)
        assert device.update_status == status
        if status == "ok":
            assert device.agent_version == "1.0"
        event = db.query(InfoscreenDeviceEvent).filter_by(device_id=device_id, typ="update_result").one()
        assert set(event.payload_json) == {"status", "error_code"}
    for patch in ({"error_code": "password secret"}, {"status": "unknown"}, {"version": "bad\nvalue"}):
        assert client.post(BASE + "/update/result", headers=headers,
                           json={"version": "1.0", "status": "ok", "error_code": None, **patch}).status_code == 422
    assert client.post(BASE + "/unpair", headers=headers).status_code == 200
    assert client.post(BASE + "/unpair", headers=headers).status_code == 401
    assert manifest(client, headers).status_code == 401


def test_installer_banner_fingerprint_and_no_cookies(client, signing, monkeypatch):
    from app.services.infoscreen_agent_installer_cmd import installer_command
    assert client.get("/infoscreen-agent/install.sh").status_code == 404
    with SessionLocal() as db:
        assert not service.installer_ready(db)
    row_id = release(client, signing)
    response = client.get("/infoscreen-agent/install.sh", headers={"Cookie": "session=invalid"})
    assert response.status_code == 200 and response.content == SCRIPT
    assert response.headers["content-type"].startswith("text/x-shellscript")
    assert response.headers["cache-control"] == "public, max-age=60" and "set-cookie" not in response.headers
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_KEY_FINGERPRINT", "")
    expected = service.fingerprint(signing[0].public_key())
    assert service.release_fingerprint() == expected and expected in installer_command("ABCDEFGH")
    with SessionLocal() as db:
        assert service.installer_ready(db)
        row = db.get(InfoscreenRelease, row_id)
        row.blocked = True
        db.flush()
        assert not service.installer_ready(db)
        db.rollback()
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_INSTALLER_ENABLED", False)
    assert client.get("/infoscreen-agent/install.sh").status_code == 404
    with SessionLocal() as db:
        assert not service.installer_ready(db)


def login(client, role):
    client.cookies.clear()
    name = "s6-" + uuid4().hex[:12]
    org_id = _make_org_admin(name, name, role)
    _login(client, name, "Test1234!")
    return org_id


def post(client, path, data=None):
    return client.post(path, data={"_csrf": client.cookies.get("ec_csrf"), **(data or {})},
                       headers={"HX-Request": "true"})


def test_admin_roles_csrf_tokens_and_release_event(client, signing, monkeypatch):
    row_id = release(client, signing)
    device_id, headers = paired(client)
    login(client, "org_admin")
    assert client.get("/admin/agent-releases").status_code == 403
    assert post(client, "/admin/agent-releases/tokens/create").status_code == 403
    login(client, "system_admin")
    assert client.get("/admin/agent-releases").status_code == 200
    assert client.post("/admin/agent-releases/tokens/create").status_code == 403
    response = post(client, "/admin/agent-releases/tokens/create")
    assert response.status_code == 200 and "eciru_" in response.text
    assert "eciru_" not in client.get("/admin/agent-releases").text
    publish = AsyncMock()
    monkeypatch.setattr(events, "publish_update_available", publish)
    response = post(client, f"/admin/agent-releases/{row_id}/policy",
                    {"rollout_percent": "10", "blocked": "1", "critical": "1"})
    assert response.status_code == 200
    assert any(call.args == (1, device_id) for call in publish.await_args_list)
    assert manifest(client, headers).json()["manifest"] is None


@pytest.mark.parametrize("left,right,result", [("1.0~rc1", "1.0", -1), ("1.0-2", "1.0-10", -1),
    ("2:1.0", "1:9.0", 1), ("1.01", "1.1", 0), ("1.0", "1.0-0", 0), ("1.0+a", "1.0", 1)])
def test_debian_version_order(left, right, result):
    assert service.version_compare(left, right) == result


def test_org_policy_device_pin_check_and_cross_org(client, signing, monkeypatch):
    org_id = login(client, "org_admin")
    device_id, headers = paired(client, org_id)
    foreign_id, _ = create_device(1)
    publish = AsyncMock()
    monkeypatch.setattr(events, "publish_update_available", publish)
    base = "/admin/infoscreen-agent"
    assert client.post(base + f"/geraete/{device_id}/update-check").status_code == 403
    assert post(client, base + f"/geraete/{foreign_id}/update-check").status_code == 404
    response = post(client, base + f"/geraete/{device_id}/update", {"channel": "beta", "pin": "2.0", "locked": "1"})
    assert response.status_code == 200 and 'value="2.0"' in response.text
    publish.assert_awaited_with(org_id, device_id)
    assert post(client, base + f"/geraete/{device_id}/update-check").status_code == 200
    assert post(client, base + "/update-policy", {"start": "25:00", "end": "02:00"}).status_code == 422
    assert post(client, base + "/update-policy", {"start": "23:00", "end": "01:00", "critical_allowed": "1"}).status_code == 200
    data = manifest(client, headers).json()
    assert data["locked"] and data["pinned_version"] == "2.0" and data["critical_allowed"]
    assert data["maintenance_window"]["start"] == "23:00"


def test_update_available_ws_uses_existing_channel(client, signing):
    from starlette.websockets import WebSocketDisconnect
    device_id, headers = paired(client)
    with client.websocket_connect("/ws/infoscreen-agent", headers=headers) as socket:
        assert socket.receive_json() == {"type": "ping"}
        from app.core.async_bridge import run_async
        run_async(events.publish_update_available, 1, device_id)
        assert socket.receive_json() == {"type": "update_available"}
        assert client.post(BASE + "/unpair", headers=headers).status_code == 200
        with pytest.raises(WebSocketDisconnect) as exc:
            socket.receive_json()
        assert exc.value.code == 4401


def test_upload_token_revocation_in_ui(client, signing):
    login(client, "system_admin")
    with SessionLocal() as db:
        row_id = db.query(InfoscreenReleaseUploadToken.id).filter_by(token_hash=hash_api_key(signing[1])).scalar()
    assert post(client, f"/admin/agent-releases/tokens/{row_id}/revoke").status_code == 200
    assert upload(client, signing).status_code == 401


def test_release_rollback_removes_files(client, signing, tmp_path):
    key, _ = signing
    content = make_deb()
    metadata = service.ReleaseMetadata(version="1.0", channel="stable", arch="amd64", sha256=hashlib.sha256(content).hexdigest(),
        size=len(content), min_version="0", key_id="test", signature="x")
    metadata.signature = base64.b64encode(key.sign(service.canonical_manifest(metadata))).decode()
    with SessionLocal() as db:
        service.store_release(db, metadata, io.BytesIO(content), SCRIPT, base64.b64encode(key.sign(SCRIPT)).decode())
        assert len(list(tmp_path.iterdir())) == 2
        db.rollback()
    assert not list(tmp_path.iterdir())


def test_critical_unsigned_and_download_of_intermediate_without_heartbeat(client, signing):
    row_id = release(client, signing, version="2.0", min_version="1.0")
    _, headers = paired(client)
    with SessionLocal() as db:
        row = db.get(InfoscreenRelease, row_id)
        before = service.canonical_manifest(row)
        row.critical = True
        assert service.canonical_manifest(row) == before
        db.commit()
    response = manifest(client, headers, "1.0")
    data = response.json()
    assert set(data) == {"update_available", "manifest", "update_allowed", "locked", "pinned_version",
                         "maintenance_window", "critical_allowed", "check_interval_h"}
    assert set(data["manifest"]) == {"version", "channel", "arch", "sha256", "size", "min_version", "key_id",
                                     "signature", "download_url", "critical"}
    assert data["manifest"]["critical"] and not data["critical_allowed"]
    assert "current=1.0" in data["manifest"]["download_url"]
    assert client.get(data["manifest"]["download_url"], headers=headers).status_code == 200
    assert manifest(client, headers, "0").json()["manifest"] is None


@pytest.mark.parametrize("error_code", ["download_failed", "hash_mismatch", "signature_invalid", "arch_mismatch",
    "package_mismatch", "downgrade_blocked", "install_failed", "health_failed", "rollback_failed", "power_loss_recovered"])
def test_all_update_error_codes(client, error_code):
    _, headers = paired(client)
    assert client.post(BASE + "/update/result", headers=headers,
        json={"version": "1.0", "status": "failed", "error_code": error_code}).status_code == 200


def test_key_rotation_and_tampered_installer(client, signing, monkeypatch):
    row_id = release(client, signing)
    other = Ed25519PrivateKey.generate()
    pem = other.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setattr(settings, "INFOSCREEN_AGENT_RELEASE_PUBKEYS", {
        **settings.INFOSCREEN_AGENT_RELEASE_PUBKEYS, "next": pem})
    signed = base64.b64encode(other.sign(SCRIPT)).decode()
    service.verify_signature("next", signed, SCRIPT)
    with pytest.raises(ValueError, match="signature_invalid"):
        service.verify_signature("test", signed, SCRIPT)
    with SessionLocal() as db:
        row = db.get(InfoscreenRelease, row_id)
        service.artifact_path(row.installer_path).write_bytes(SCRIPT + b"tampered")
    assert client.get("/infoscreen-agent/install.sh").status_code == 404
    with SessionLocal() as db:
        assert not service.installer_ready(db)


def test_pair_name_channel_and_banner_auto_enable(client, signing):
    release(client, signing)
    login(client, "org_admin")
    response = post(client, "/admin/infoscreen-agent/geraete/neu", {"name": "Dialog"})
    assert response.status_code == 200 and "Installer noch nicht freigeschaltet" not in response.text
    _, code = create_device()
    for patch in ({"name": "x" * 101}, {"name": "x\n"}, {"name": "   "}, {"channel": "other"}):
        assert client.post(BASE + "/pair", json={"code": code, **patch}).status_code == 400
    data = client.post(BASE + "/pair", json={"code": code, "name": "Halle neu", "channel": "beta"}).json()
    with SessionLocal() as db:
        set_tenant_context(db, 1)
        row = db.get(InfoscreenDevice, data["device_id"])
        assert row.name == "Halle neu" and row.channel == "beta"


@pytest.mark.parametrize("name", ["control.tar.gz", "control.tar.xz", "control.tar.bz2", "control.tar.zst"])
def test_corrupt_control_compression_rejected(name):
    with pytest.raises(ValueError, match="package_mismatch"):
        service._control_bytes(name, b"corrupt archive")


@pytest.mark.parametrize("origin", ["http://agent.example", "https://agent.example/path", "https://user:pass@agent.example",
    "https://agent.example?query=1", "https://agent.example#fragment", "https://agent.example:invalid"])
def test_installer_command_requires_https_origin(monkeypatch, origin):
    from app.services.infoscreen_agent_installer_cmd import installer_command
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", origin)
    with pytest.raises(ValueError):
        installer_command("ABCDEFGH")
