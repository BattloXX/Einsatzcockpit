"""Regressionsnetz fuer Karenz, Claims, Signale und Monitoring-API."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.tenant import set_tenant_context
from app.models.dienst_monitor import DienstStatus
from app.services.dienst_monitor_service import DienstCheck, entscheide


def row(**werte):
    basis = dict(org_id=1, key="print_gateway", state="unknown", fail_cycles=0, ok_cycles=0)
    basis.update(werte)
    return DienstStatus(**basis)


def test_karenz_und_zwei_zyklen():
    now = datetime(2026, 1, 1, 12)
    status = row()
    check = DienstCheck("print_gateway", "down", "aus", True)
    assert entscheide(check, status, 5, 60, now).art is None
    assert entscheide(check, status, 5, 60, now + timedelta(minutes=4)).art is None
    assert entscheide(check, status, 5, 60, now + timedelta(minutes=5)).art == "stoerung"


def test_flapping_vor_karenz_ohne_meldung():
    now = datetime(2026, 1, 1, 12)
    status = row()
    entscheide(DienstCheck("print_gateway", "down", "aus", True), status, 5, 60, now)
    assert entscheide(DienstCheck("print_gateway", "ok", "gut", True), status, 5, 60,
                      now + timedelta(minutes=2)).art is None
    assert status.down_since is None


def test_entwarnung_genau_nach_gemeldeter_stoerung():
    now = datetime(2026, 1, 1, 12)
    status = row(outage_notified_at=now, down_since=now - timedelta(minutes=8))
    check = DienstCheck("print_gateway", "ok", "gut", True)
    assert entscheide(check, status, 5, 60, now).art == "entwarnung"
    status.outage_notified_at = None
    assert entscheide(check, status, 5, 60, now).art is None


def test_wiederholung_erst_nach_cooldown():
    now = datetime(2026, 1, 1, 12)
    status = row(down_since=now - timedelta(hours=2), fail_cycles=2,
                 outage_notified_at=now - timedelta(hours=1), last_repeat_at=now - timedelta(minutes=59))
    check = DienstCheck("print_gateway", "down", "aus", True)
    assert entscheide(check, status, 5, 60, now).art is None
    status.last_repeat_at = now - timedelta(minutes=60)
    assert entscheide(check, status, 5, 60, now).art == "stoerung"


def test_neustart_setzt_down_since_nicht_zurueck():
    now = datetime(2026, 1, 1, 12)
    status = row(down_since=now - timedelta(minutes=6), fail_cycles=1)
    assert entscheide(DienstCheck("print_gateway", "down", "aus", True), status, 5, 60, now).art == "stoerung"
    assert status.down_since == now - timedelta(minutes=6)


def test_aware_now_und_naive_db_werden_normalisiert():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    status = row(down_since=datetime(2026, 1, 1, 11, 50), fail_cycles=1)
    assert entscheide(DienstCheck("print_gateway", "down", "aus", True), status, 5, 60, now).art == "stoerung"


def test_unknown_und_nicht_relevant_veraendern_zaehler_nicht():
    status = row(fail_cycles=3, ok_cycles=2)
    assert entscheide(DienstCheck("sms_gateway", "unknown", "alt", True), status, 5, 60,
                      datetime.now(UTC)).art is None
    assert (status.fail_cycles, status.ok_cycles) == (3, 2)
    assert entscheide(DienstCheck("alarm_dibos", "down", "aus", False), status, 0, 1,
                      datetime.now(UTC)).art is None


def test_atomarer_claim_nur_einmal():
    """Der zweite Worker trifft nach dem ersten Commit das bedingte Praedikat nicht mehr."""
    from app.db import SessionLocal
    from app.services.dienst_monitor_service import claim_meldung
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        status = row(org_id=1, key="claim_test")
        db.add(status); db.commit(); db.refresh(status)
        now = datetime(2026, 1, 1, 12)
        assert claim_meldung(db, status, 1, "stoerung", now, 60)
        assert not claim_meldung(db, status, 1, "stoerung", now, 60)
    finally:
        db.query(DienstStatus).filter(DienstStatus.org_id == 1, DienstStatus.key == "claim_test").delete()
        db.commit(); db.close()


@pytest.mark.asyncio
async def test_totalausfall_rollt_claim_zurueck(monkeypatch):
    from app.db import SessionLocal
    from app.services.dienst_monitor_service import claim_meldung, rollback_claim
    db = SessionLocal(); set_tenant_context(db, None)
    try:
        status = row(org_id=1, key="rollback_test")
        db.add(status); db.commit(); db.refresh(status)
        now = datetime(2026, 1, 1, 12)
        assert claim_meldung(db, status, 1, "stoerung", now, 60)
        rollback_claim(db, status.id, 1, "stoerung", now, None, None)
        db.refresh(status)
        assert status.outage_notified_at is None
        assert claim_meldung(db, status, 1, "stoerung", now + timedelta(seconds=1), 60)
    finally:
        db.query(DienstStatus).filter(DienstStatus.org_id == 1, DienstStatus.key == "rollback_test").delete()
        db.commit(); db.close()


@pytest.mark.asyncio
async def test_sms_gateway_sms_kanal_wird_uebersprungen():
    from app.db import SessionLocal
    from app.models.dienst_monitor import DienstMonitorLog
    from app.services.dienst_monitor_dispatch import dispatch_meldung
    db = SessionLocal(); set_tenant_context(db, None)
    settings = SimpleNamespace(dienst_monitor_mail=None, dienst_monitor_teams_webhook_url=None,
                               dienst_monitor_sms="+43123")
    org = SimpleNamespace(name="Test", timezone="Europe/Vienna")
    status = row(key="sms_gateway", down_since=datetime.now(UTC).replace(tzinfo=None))
    check = DienstCheck("sms_gateway", "down", "aus", True)
    try:
        assert not await dispatch_meldung(db=db, org_id=1, org=org, org_settings=settings,
                                          row=status, check=check, art="stoerung")
        log = db.query(DienstMonitorLog).filter(DienstMonitorLog.key == "sms_gateway").order_by(DienstMonitorLog.id.desc()).first()
        assert log and log.status == "uebersprungen"
    finally:
        db.query(DienstMonitorLog).filter(DienstMonitorLog.org_id == 1,
                                          DienstMonitorLog.key == "sms_gateway").delete()
        db.commit(); db.close()


def test_gateway_aggregation_serial_unknown_und_sms_ohne_heartbeat(monkeypatch):
    from app.db import SessionLocal
    from app.models.gateway import Gateway
    from app.models.user import SmsGatewayToken
    from app.services.dienst_monitor_service import pruefe_dienste
    db = SessionLocal(); set_tenant_context(db, None)
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        frisch = Gateway(org_id=1, name="Frisch", device_token_hash="dm-frisch",
                         last_seen_at=now, serial_connected=True, wut_config={"host": "wt-a"})
        alt = Gateway(org_id=1, name="Alt", standort="Depot", device_token_hash="dm-alt",
                      last_seen_at=now - timedelta(hours=1), serial_connected=False,
                      wut_config={"host": "wt-b"})
        token = SmsGatewayToken(org_id=1, label="Altclient", token_hash="dm-sms-ohne-heartbeat")
        db.add_all([frisch, alt, token]); db.commit()
        monkeypatch.setattr("app.routers.ws.is_sms_gateway_connected", lambda _org_id: False)
        checks = {c.key: c for c in pruefe_dienste(db, 1, now)}
        assert checks["print_gateway"].state == "down"
        assert "Alt" in checks["print_gateway"].detail and "Frisch" not in checks["print_gateway"].detail
        assert checks["alarm_seriell"].state == "unknown"
        assert checks["sms_gateway"].state == "unknown"
    finally:
        db.query(SmsGatewayToken).filter(SmsGatewayToken.token_hash == "dm-sms-ohne-heartbeat").delete()
        db.query(Gateway).filter(Gateway.device_token_hash.in_(["dm-frisch", "dm-alt"])).delete()
        db.commit(); db.close()


def test_monitoring_token_fehler_alle_identisch(client):
    from app.db import SessionLocal
    from app.core.security import hash_api_key
    from app.models.dienst_monitor import DienstMonitorToken
    db = SessionLocal(); set_tenant_context(db, None)
    now = datetime.now(UTC).replace(tzinfo=None)
    tokens = {
        "widerrufen": DienstMonitorToken(org_id=1, label="w", token_hash=hash_api_key("widerrufen"),
                                          revoked_at=now),
        "abgelaufen": DienstMonitorToken(org_id=1, label="a", token_hash=hash_api_key("abgelaufen"),
                                         expires_at=now - timedelta(seconds=1)),
    }
    try:
        db.add_all(tokens.values()); db.commit()
        antworten = [client.get("/health/dienste", headers={"Authorization": f"Bearer {raw}"})
                     for raw in ("ungueltig", "widerrufen", "abgelaufen")]
        assert [r.status_code for r in antworten] == [401, 401, 401]
        assert len({r.text for r in antworten}) == 1
        assert client.get("/health/dienst/unbekannt?token=ungueltig").status_code == 404
    finally:
        db.query(DienstMonitorToken).filter(DienstMonitorToken.token_hash.in_(
            [hash_api_key("widerrufen"), hash_api_key("abgelaufen")])).delete()
        db.commit(); db.close()


def test_bestaetigt_down_unabhaengig_vom_versanderfolg():
    """Uptime meldet 503 auch ohne konfigurierte Empfaenger.

    Regression: der Status darf nicht an ``outage_notified_at`` haengen, sonst bliebe eine
    Org, die nur Uptime Kuma nutzt und keine Benachrichtigungskanaele pflegt, dauerhaft auf
    "ok" -- der Versand scheitert dort ja immer und setzt den Marker nie.
    """
    from app.services.dienst_monitor_service import bestaetigt_down

    now = datetime(2026, 1, 1, 12)
    ohne_ausfall = row(down_since=None, outage_notified_at=None)
    in_karenz = row(down_since=now - timedelta(minutes=3), outage_notified_at=None)
    ueber_karenz = row(down_since=now - timedelta(minutes=7), outage_notified_at=None)

    assert not bestaetigt_down(ohne_ausfall, 5, now)
    assert not bestaetigt_down(in_karenz, 5, now)
    assert bestaetigt_down(ueber_karenz, 5, now)
    assert not bestaetigt_down(None, 5, now)
    # aware/naive gemischt darf nicht crashen
    assert bestaetigt_down(ueber_karenz, 5, now.replace(tzinfo=UTC))
