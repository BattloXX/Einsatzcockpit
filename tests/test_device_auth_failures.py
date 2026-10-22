"""SEC-01/SEC-02: ungueltige Geraetezugaenge werden erkannt, aggregiert und dem Client gemeldet."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from app.core.security import hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import DeviceAuthFailure, DeviceToken, User
from app.services import device_auth_failure_service as dat

ORG_ID = 1


def _geraet(*, revoked=False, user_active=True, label="SEC01"):
    roh = secrets.token_urlsafe(24)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        u = User(username=f"sec01_{secrets.token_hex(4)}", password_hash=hash_password("x"),
                 display_name="Geraet", org_id=ORG_ID, active=user_active, is_device=True)
        db.add(u)
        db.flush()
        dt = DeviceToken(label=label, token_hash=hash_api_key(roh), user_id=u.id,
                         revoked_at=datetime.now(UTC).replace(tzinfo=None) if revoked else None)
        db.add(dt)
        db.commit()
        return roh, dt.id
    finally:
        db.close()


def _zeilen(prefix):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(DeviceAuthFailure).filter(DeviceAuthFailure.token_hash_prefix == prefix).all()
    finally:
        db.close()


def _duty(client, roh, **headers):
    client.cookies.clear()
    return client.get("/api/v1/device/duty-state", headers={"Authorization": f"Bearer {roh}", **headers})


def test_widerrufenes_token_wird_erkannt_und_gemeldet(client):
    dat.reset_state()
    roh, dt_id = _geraet(revoked=True)
    r = _duty(client, roh, **{"X-ECP-App-Version": "1.2.3", "X-ECP-Platform": "android"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Nicht eingeloggt"}  # Body unveraendert (App-Kompatibilitaet)
    assert r.headers[dat.AUTH_ERROR_HEADER] == "device_revoked"
    zeilen = _zeilen(hash_api_key(roh)[:10])
    assert len(zeilen) == 1
    z = zeilen[0]
    assert (z.reason, z.channel, z.count, z.device_token_id) == ("revoked", "bearer", 1, dt_id)
    assert z.app_version == "1.2.3" and z.platform == "android"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.get(DeviceToken, dt_id)
        assert dt.fail_count == 1 and dt.last_failed_at is not None
    finally:
        db.close()


def test_unbekanntes_token_hat_eigenen_code_und_keine_geraetezuordnung(client):
    dat.reset_state()
    roh = secrets.token_urlsafe(24)
    r = _duty(client, roh)
    assert r.status_code == 401 and r.headers[dat.AUTH_ERROR_HEADER] == "device_unknown"
    (z,) = _zeilen(hash_api_key(roh)[:10])
    assert z.reason == "unknown" and z.device_token_id is None


def test_gesperrter_benutzer_hat_eigenen_code(client):
    dat.reset_state()
    roh, _ = _geraet(user_active=False)
    r = _duty(client, roh)
    assert r.status_code == 401 and r.headers[dat.AUTH_ERROR_HEADER] == "user_inactive"


def test_wiederholte_versuche_werden_zusammengefasst(client):
    dat.reset_state()
    roh, dt_id = _geraet(revoked=True)
    for _ in range(25):
        assert _duty(client, roh).status_code == 401
    zeilen = _zeilen(hash_api_key(roh)[:10])
    assert len(zeilen) == 1 and zeilen[0].count == 1  # erster Versuch sofort, Rest gesammelt
    assert dat.flush_pending() == 1
    (z,) = _zeilen(hash_api_key(roh)[:10])
    assert z.count == 25


def test_kein_volltoken_in_datenbank_und_log(client, caplog):
    dat.reset_state()
    roh, _ = _geraet(revoked=True)
    with caplog.at_level(logging.DEBUG):
        _duty(client, roh)
    assert roh not in caplog.text
    (z,) = _zeilen(hash_api_key(roh)[:10])
    assert roh not in " ".join(str(v) for v in (z.token_hash_prefix, z.user_agent, z.ip))
    assert len(z.token_hash_prefix) == 10


def test_geraet_login_protokolliert_widerrufenes_token(client):
    dat.reset_state()
    roh, _ = _geraet(revoked=True)
    client.cookies.clear()
    r = client.get(f"/geraet-login?token={roh}", follow_redirects=False)
    assert r.status_code == 302 and "device_invalid" in r.headers["location"]
    (z,) = _zeilen(hash_api_key(roh)[:10])
    assert (z.channel, z.reason) == ("device_login", "revoked")


def test_gueltiges_token_erzeugt_keinen_fehlversuch(client):
    dat.reset_state()
    roh, dt_id = _geraet()
    r = _duty(client, roh, **{"X-ECP-Platform": "android", "X-ECP-OS-Version": "14", "X-ECP-Device-Model": "Tab A9"})
    assert r.status_code == 200
    assert _zeilen(hash_api_key(roh)[:10]) == []
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dt = db.get(DeviceToken, dt_id)
        assert (dt.platform, dt.os_version, dt.device_model) == ("android", "14", "Tab A9")
    finally:
        db.close()


def test_widerrufene_geraete_session_wird_protokolliert(client):
    from app.core.security import sign_session

    dat.reset_state()
    roh, dt_id = _geraet(revoked=True)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user_id = db.get(DeviceToken, dt_id).user_id
    finally:
        db.close()
    client.cookies.clear()
    client.cookies.set("session", sign_session(user_id, device=True, device_token_id=dt_id))
    client.get("/api/v1/device/duty-state")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        z = db.query(DeviceAuthFailure).filter_by(device_token_id=dt_id, channel="session").first()
        assert z is not None and z.reason == "session_revoked"
    finally:
        db.close()


# ── SEC-04: Admin-Uebersicht ────────────────────────────────────────────────

def _admin(username, org_id):
    from app.models.user import Role, UserRole

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rolle = db.query(Role).filter(Role.code == "admin").first() or Role(code="admin", name="admin")
        db.add(rolle)
        db.flush()
        u = User(username=username, password_hash=hash_password("Test1234!"), display_name="Admin",
                 org_id=org_id, active=True)
        db.add(u)
        db.flush()
        db.add(UserRole(user_id=u.id, role_id=rolle.id))
        db.commit()
    finally:
        db.close()


def _login(client, username):
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf},
                       follow_redirects=False)


def test_admin_uebersicht_zeigt_fehlversuche_und_filter(client):
    dat.reset_state()
    label = f"SEC04-{secrets.token_hex(3)}"
    roh, dt_id = _geraet(revoked=True, label=label)
    _duty(client, roh)
    _admin("sec04_admin", ORG_ID)
    _login(client, "sec04_admin")
    seite = client.get("/admin/geraete-login")
    assert seite.status_code == 200
    assert "Fehlgeschlagene Geräte-Zugriffe" in seite.text and "Zugang widerrufen" in seite.text
    auffaellig = client.get("/admin/geraete-login?filter=auffaellig")
    assert label in auffaellig.text.split("Fehlgeschlagene Geräte-Zugriffe")[0]
    aktiv = client.get("/admin/geraete-login?filter=aktiv")
    assert label not in aktiv.text.split("Fehlgeschlagene Geräte-Zugriffe")[0]
    detail = client.get(f"/admin/geraete-login/{dt_id}")
    assert detail.status_code == 200 and "Fehlgeschlagene Zugriffe" in detail.text


def test_fremde_org_sieht_keine_fehlversuche(client):
    from app.models.master import FireDept

    dat.reset_state()
    roh, dt_id = _geraet(revoked=True)
    _duty(client, roh)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"sec04-{secrets.token_hex(3)}", name="Fremde Org", color="#111111", bos="Feuerwehr")
        db.add(org)
        db.commit()
        fremd_id = org.id
    finally:
        db.close()
    _admin("sec04_fremd", fremd_id)
    _login(client, "sec04_fremd")
    seite = client.get("/admin/geraete-login")
    assert "Zugang widerrufen" not in seite.text
    assert client.get(f"/admin/geraete-login/{dt_id}").status_code == 404


# ── SEC-02: Web-Layer der App stoppt bei endgueltig ungueltigem Zugang ──────

def test_native_bridge_stoppt_bei_ungueltigem_zugang():
    from pathlib import Path

    js = Path("app/static/js/native-bridge.js").read_text(encoding="utf-8")
    for code in ("device_revoked", "device_unknown", "user_inactive"):
        assert code in js
    assert "X-ECP-Auth-Error" in js
    assert "Der Gerätezugang ist abgelaufen oder wurde deaktiviert. Bitte das Gerät erneut verbinden." in js
    assert "Erneut verbinden" in js and "/geraet-login-pin" in js
    # nur 401 mit Code ist endgueltig; Netzwerkfehler und 5xx bleiben temporaer
    assert "resp.status === 401" in js


def test_login_zeigt_meldung_bei_device_invalid(client):
    client.cookies.clear()
    r = client.get("/login?error=device_invalid")
    assert "Gerätezugang ist abgelaufen oder wurde deaktiviert" in r.text
