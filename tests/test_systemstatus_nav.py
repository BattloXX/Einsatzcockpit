"""Regressionstests fuer den Systemstatus-Eintrag in der Admin-Navigation."""

from datetime import UTC, datetime, timedelta

from app.core.security import hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.dienst_monitor import DienstMonitorToken, DienstStatus
from app.models.master import FireDept
from app.models.user import Role, SmsGatewayToken, User, UserRole


def _login(client, username: str, password: str):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post(
        "/login",
        data={"username": username, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def _make_org_admin(username: str, org_slug: str, role_code: str = "admin") -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=org_slug, name=f"Org {org_slug}", color="#112233", bos="Feuerwehr")
        db.add(org)
        db.flush()
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name="Admin",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).first()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return org.id
    finally:
        db.close()


def test_org_admin_sieht_systemstatus_nav_eintrag(client, setup_db):
    _make_org_admin("systemstatus_org_admin", "systemstatus-org")
    _login(client, "systemstatus_org_admin", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert 'href="/admin/systemstatus"' in response.text
    assert "Systemstatus" in response.text


def test_system_admin_sieht_systemstatus_nav_eintrag_genau_einmal(client, setup_db):
    _make_org_admin("systemstatus_system_admin", "systemstatus-system", "system_admin")
    _login(client, "systemstatus_system_admin", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert response.text.count('href="/admin/systemstatus"') == 1


def test_systemstatus_zeigt_korrekte_dienstlabels(client, setup_db):
    _make_org_admin("systemstatus_labels", "systemstatus-labels")
    _login(client, "systemstatus_labels", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert "SMS-Gateway" in response.text
    assert "Alarm DIBOS" in response.text
    assert "Sms Gateway" not in response.text
    assert "Alarm Dibos" not in response.text


def test_systemstatus_zeigt_nicht_eingerichtete_dienste(client, setup_db):
    _make_org_admin("systemstatus_fresh", "systemstatus-fresh")
    _login(client, "systemstatus_fresh", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert "Nicht eingerichtet" in response.text


def test_systemstatus_zeigt_uptime_referenz_ohne_token(client, setup_db):
    _make_org_admin("systemstatus_reference", "systemstatus-reference")
    _login(client, "systemstatus_reference", "Test1234!")

    response = client.get("/admin/systemstatus")

    assert response.status_code == 200
    assert "Erreichbarkeit (App + Datenbank)" in response.text
    assert "health</code></td><td>nein" in response.text
    assert "health/dienste" in response.text
    assert "&lt;TOKEN&gt;" in response.text


def test_systemstatus_zeigt_fertige_urls_nach_token_anlage(client, setup_db):
    _make_org_admin("systemstatus_token", "systemstatus-token")
    _login(client, "systemstatus_token", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    response = client.post(
        "/admin/systemstatus/token/neu",
        data={"_csrf": csrf, "label": "Uptime Kuma"},
    )

    assert response.status_code == 200
    assert "health</code> <span>ohne Token" in response.text
    assert "health/dienste?token=" in response.text
    for key in ("print_gateway", "sms_gateway", "alarm_seriell", "alarm_dibos"):
        assert f"health/dienst/{key}?token=" in response.text


def test_systemstatus_zeigt_bestaetigten_ausfall_ohne_benachrichtigung(client, setup_db, monkeypatch):
    org_id = _make_org_admin("systemstatus_down", "systemstatus-down")
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add_all(
            [
                SmsGatewayToken(
                    org_id=org_id,
                    label="Veralteter Heartbeat",
                    token_hash="systemstatus-down-sms-token",
                    last_heartbeat_at=now - timedelta(minutes=30),
                ),
                DienstStatus(
                    org_id=org_id,
                    key="sms_gateway",
                    state="down",
                    down_since=now - timedelta(minutes=60),
                    outage_notified_at=None,
                ),
            ]
        )
        db.commit()
        monkeypatch.setattr("app.routers.ws.connected_gateway_token_ids", lambda _org_id: set())
        _login(client, "systemstatus_down", "Test1234!")

        response = client.get("/admin/systemstatus")

        assert response.status_code == 200
        assert '<span class="badge badge--closed">Ausfall</span>' in response.text
        assert "Keine Benachrichtigung verschickt" in response.text
    finally:
        db.query(DienstStatus).filter(
            DienstStatus.org_id == org_id, DienstStatus.key == "sms_gateway"
        ).delete()
        db.query(SmsGatewayToken).filter(
            SmsGatewayToken.org_id == org_id,
            SmsGatewayToken.token_hash == "systemstatus-down-sms-token",
        ).delete()
        db.commit()
        db.close()


def test_ui_und_uptime_api_zeigen_denselben_ausfall(client, setup_db, monkeypatch):
    """Kachel und Health-Endpunkt duerfen nicht auseinanderlaufen.

    Regression: die Kachel entschied frueher ueber ``outage_notified_at``, der Endpunkt
    ueber ``bestaetigt_down``. Eine Org ohne Empfaenger sah damit alles gruen, waehrend
    Uptime Kuma bereits 503 bekam. Beide Pfade nutzen jetzt ``dienst_zustand``.
    """
    org_id = _make_org_admin("gleichlauf_admin", "gleichlauf-org")
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add_all(
            [
                SmsGatewayToken(
                    org_id=org_id,
                    label="Veralteter Heartbeat",
                    token_hash="gleichlauf-sms-token",
                    last_heartbeat_at=now - timedelta(minutes=30),
                ),
                DienstStatus(
                    org_id=org_id,
                    key="sms_gateway",
                    state="down",
                    down_since=now - timedelta(minutes=60),
                    outage_notified_at=None,
                ),
                DienstMonitorToken(
                    org_id=org_id, label="Uptime Kuma", token_hash=hash_api_key("gleichlauf-uptime-token")
                ),
            ]
        )
        db.commit()
        monkeypatch.setattr("app.routers.ws.connected_gateway_token_ids", lambda _org_id: set())

        gesamt = client.get("/health/dienste?token=gleichlauf-uptime-token")
        assert gesamt.status_code == 503
        assert gesamt.json()["gesamt"] == "down"
        sms = next(d for d in gesamt.json()["dienste"] if d["key"] == "sms_gateway")
        assert sms["status"] == "down"

        einzeln = client.get("/health/dienst/sms_gateway?token=gleichlauf-uptime-token")
        assert einzeln.status_code == 503
        assert einzeln.json()["status"] == "down"

        _login(client, "gleichlauf_admin", "Test1234!")
        seite = client.get("/admin/systemstatus")
        assert '<span class="badge badge--closed">Ausfall</span>' in seite.text
    finally:
        db.query(DienstStatus).filter(DienstStatus.org_id == org_id, DienstStatus.key == "sms_gateway").delete()
        db.query(SmsGatewayToken).filter(
            SmsGatewayToken.org_id == org_id, SmsGatewayToken.token_hash == "gleichlauf-sms-token"
        ).delete()
        db.query(DienstMonitorToken).filter(
            DienstMonitorToken.org_id == org_id,
            DienstMonitorToken.token_hash == hash_api_key("gleichlauf-uptime-token"),
        ).delete()
        db.commit()
        db.close()


def test_ui_und_uptime_api_zeigen_sms_teilstoerung(client, setup_db, monkeypatch):
    org_id = _make_org_admin("teilausfall_admin", "teilausfall-org")
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    set_tenant_context(db, None)
    sms_hashes = ["teilausfall-sms-ok", "teilausfall-sms-down"]
    uptime_hash = hash_api_key("teilausfall-uptime-token")
    try:
        db.add_all(
            [
                SmsGatewayToken(
                    org_id=org_id, label="Android-Wache", token_hash=sms_hashes[0], last_heartbeat_at=now
                ),
                SmsGatewayToken(
                    org_id=org_id,
                    label="Android-Nord",
                    token_hash=sms_hashes[1],
                    last_heartbeat_at=now - timedelta(minutes=30),
                ),
                DienstStatus(
                    org_id=org_id,
                    key="sms_gateway",
                    state="down",
                    down_since=now - timedelta(minutes=60),
                ),
                DienstMonitorToken(org_id=org_id, label="Uptime Kuma", token_hash=uptime_hash),
            ]
        )
        db.commit()
        monkeypatch.setattr("app.routers.ws.connected_gateway_token_ids", lambda _org_id: set())

        gesamt = client.get("/health/dienste?token=teilausfall-uptime-token")
        assert gesamt.status_code == 207
        assert gesamt.json()["gesamt"] == "teilweise"
        sms = next(d for d in gesamt.json()["dienste"] if d["key"] == "sms_gateway")
        assert sms["status"] == "teilweise"
        assert sms["anzahl"] == {"gesamt": 2, "ok": 1, "down": 1, "unbekannt": 0}
        assert {t["name"] for t in sms["teile"]} == {"Android-Wache", "Android-Nord"}

        einzeln = client.get("/health/dienst/sms_gateway?token=teilausfall-uptime-token")
        assert einzeln.status_code == 207
        assert einzeln.json()["status"] == "teilweise"

        _login(client, "teilausfall_admin", "Test1234!")
        seite = client.get("/admin/systemstatus")
        assert '<span class="badge badge--warn">Teilstörung</span>' in seite.text
        assert "Android-Wache" in seite.text
        assert "Android-Nord" in seite.text
    finally:
        db.query(DienstStatus).filter(
            DienstStatus.org_id == org_id, DienstStatus.key == "sms_gateway"
        ).delete()
        db.query(SmsGatewayToken).filter(
            SmsGatewayToken.org_id == org_id, SmsGatewayToken.token_hash.in_(sms_hashes)
        ).delete()
        db.query(DienstMonitorToken).filter(
            DienstMonitorToken.org_id == org_id, DienstMonitorToken.token_hash == uptime_hash
        ).delete()
        db.commit()
        db.close()
