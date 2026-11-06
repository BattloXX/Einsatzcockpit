"""Dienstschalter, LIS-Proben und sofortige Anzeige ueber alle Ausgabekanaele."""
import asyncio
import importlib.util
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.dibos import OrgDibosConfig
from app.models.dienst_monitor import ORG_DIENST_KEYS, DienstMonitorToken, DienstStatus
from app.models.lis import OrgLisConfig
from app.models.master import FireDept, OrgSettings
from app.services.dienst_monitor_service import claim_meldung, entscheide, pruefe_dienste
from app.services.system_diagnose import service_summary


@pytest.fixture
def monitor_orgs():
    with SessionLocal() as db:
        set_tenant_context(db, None)
        orgs = [FireDept(slug=uuid4().hex, name="Monitor-Test") for _ in range(2)]
        db.add_all(orgs)
        db.flush()
        for org in orgs:
            db.add(OrgSettings(org_id=org.id))
        db.commit()
        try:
            yield db, [org.id for org in orgs]
        finally:
            for model in (DienstMonitorToken, DienstStatus, OrgLisConfig, OrgDibosConfig, OrgSettings):
                for row in db.query(model).filter(model.org_id.in_([org.id for org in orgs])).all():
                    db.delete(row)
            for org in orgs:
                db.delete(org)
            db.commit()


def test_migration_0353_daten_und_default():
    spec = importlib.util.spec_from_file_location("schalter", "alembic/versions/0353_dienst_monitor_ausgeschaltet.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0353", "0352")
    column = OrgSettings.__table__.c.dienst_monitor_ausgeschaltet
    assert column.nullable and column.type.length == 255
    assert column.default.arg == "alarm_lis" and column.server_default.arg == "alarm_lis"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE org_settings (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO org_settings VALUES (1), (2)"))
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.execute(sa.text("SELECT dienst_monitor_ausgeschaltet FROM org_settings")).scalars().all() == ["alarm_lis"] * 2
        conn.execute(sa.text("INSERT INTO org_settings (id) VALUES (3)"))
        assert conn.execute(sa.text("SELECT dienst_monitor_ausgeschaltet FROM org_settings WHERE id=3")).scalar() == "alarm_lis"
        migration.downgrade()
        assert [c["name"] for c in sa.inspect(conn).get_columns("org_settings")] == ["id"]
    engine.dispose()


@pytest.mark.parametrize("key", ["alarm_lis", "alarm_dibos"])
def test_interface_zustaende_schalter_und_tenant(monitor_orgs, key):
    db, (org_a, org_b) = monitor_orgs
    now = datetime.now(UTC).replace(tzinfo=None)
    def check(org_id):
        return next(c for c in pruefe_dienste(db, org_id, now) if c.key == key)
    if key == "alarm_lis":
        assert check(org_a).detail == "Ueberwachung ausgeschaltet."
    for org_id in (org_a, org_b):
        db.query(OrgSettings).filter_by(org_id=org_id).one().dienst_monitor_ausgeschaltet = ""
    assert check(org_a).state == "nicht_konfiguriert"
    if key == "alarm_lis":
        cfg = OrgLisConfig(org_id=org_a, enabled=True, base_url="https://lis.invalid", username="u",
                           password_enc="cipher", organization_id="guid", poll_interval_seconds=60)
    else:
        cfg = OrgDibosConfig(org_id=org_a, enabled=True, base_url="https://dibos.invalid",
                             gateway_user="g", gateway_password_enc="cipher", service_user="s",
                             service_password_enc="cipher", create_incidents=True, poll_interval_seconds=60)
    db.add(cfg)
    db.commit()
    assert check(org_a).state == "unknown"
    status = DienstStatus(org_id=org_a, key=key, state="down", last_probe_at=now, last_probe_ok=True)
    db.add(status)
    db.commit()
    assert check(org_a).state == "ok"
    cfg_b = type(cfg)(org_id=org_b, **{
        c.name: getattr(cfg, c.name) for c in cfg.__table__.columns if c.name not in ("id", "org_id")
    })
    db.add(cfg_b)
    db.add(DienstStatus(org_id=org_b, key=key, state="down", last_probe_at=now, last_probe_ok=False))
    db.commit()
    status.last_probe_at = now - timedelta(seconds=361)
    db.commit()
    assert check(org_a).state == "down"
    status.last_probe_at, status.last_probe_ok = now, False
    status.last_probe_error = "privater Fehler"
    db.commit()
    assert check(org_a).state == "down"
    settings_a = db.query(OrgSettings).filter_by(org_id=org_a).one()
    settings_a.dienst_monitor_ausgeschaltet = key
    db.commit()
    off = check(org_a)
    assert (off.state, off.detail, off.relevant, off.teile) == ("nicht_konfiguriert", "Ueberwachung ausgeschaltet.", False, ())
    assert check(org_b).state == "down"
    assert check(org_b).relevant
    status.down_since = status.outage_notified_at = status.last_repeat_at = now - timedelta(hours=2)
    status.fail_cycles = 20
    db.commit()
    assert not claim_meldung(db, status, org_a, "stoerung", now, 60)
    assert not claim_meldung(db, status, org_a, "entwarnung", now, 60)
    assert entscheide(off, status, 5, 60, now).art is None
    assert status.down_since is status.outage_notified_at is status.last_repeat_at is None
    assert status.fail_cycles == status.ok_cycles == 0
    settings_a.dienst_monitor_ausgeschaltet = ""
    db.commit()
    assert entscheide(check(org_a), status, 5, 60, now).art is None
    assert status.down_since == now
    cfg.enabled = False
    db.commit()
    assert check(org_a).state == "nicht_konfiguriert"


def test_api_und_diagnose_ignorieren_alten_ausfall(client, monitor_orgs):
    db, (org_a, org_b) = monitor_orgs
    now = datetime.now(UTC).replace(tzinfo=None)
    for org_id in (org_a, org_b):
        db.query(OrgSettings).filter_by(org_id=org_id).one().dienst_monitor_ausgeschaltet = "alarm_dibos,alarm_lis"
        db.add(DienstStatus(org_id=org_id, key="alarm_dibos", state="down", down_since=now - timedelta(hours=1),
                           last_error="GEHEIM"))
    db.add(DienstMonitorToken(org_id=org_a, label="Test", token_hash=hash_api_key("schalter-test")))
    db.commit()
    for url in ("/health/dienst/alarm_dibos", "/health/dienste/alarm_lis"):
        response = client.get(url, headers={"Authorization": "Bearer schalter-test"})
        assert response.status_code == 200
        assert response.json()["status"] == "nicht_konfiguriert"
        assert not response.json()["ueberwacht"]
        assert "GEHEIM" not in response.text
    response = client.get("/health/dienste?token=schalter-test")
    checks = {c["key"]: c for c in response.json()["dienste"]}
    assert checks["alarm_dibos"]["status"] == "nicht_konfiguriert"
    assert checks["alarm_dibos"]["seit"] is None
    assert response.json()["ausgeschaltet"] == 2
    evidence = service_summary(db)["evidence"]["dienste"]
    assert evidence["alarm_dibos"]["ausgeschaltet"] == 2
    assert evidence["alarm_dibos"]["down"] == 0
    assert "GEHEIM" not in str(evidence)


def test_ui_speichert_whitelist_csrf_und_zeigt_auswahl(client):
    from tests.test_systemstatus_nav import _login, _make_org_admin
    org_id = _make_org_admin("schalter-admin", "schalter-admin")
    _login(client, "schalter-admin", "Test1234!")
    response = client.get("/admin/systemstatus")
    assert 'name="_csrf"' in response.text
    assert 'value="alarm_lis" >' in response.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        now = datetime.now(UTC).replace(tzinfo=None)
        db.add(DienstStatus(org_id=org_id, key="alarm_dibos", state="down", down_since=now,
                           outage_notified_at=now, last_repeat_at=now, fail_cycles=5))
        db.commit()
    data = {"enabled": "on", "ueberwachte_dienste": [k for k in ORG_DIENST_KEYS if k != "alarm_dibos"] + ["worker_runtime", "unbekannt"]}
    assert client.post("/admin/systemstatus/empfaenger", data=data).status_code == 403
    data["_csrf"] = client.cookies.get("ec_csrf")
    response = client.post("/admin/systemstatus/empfaenger", data=data)
    assert response.status_code == 200
    assert 'value="alarm_lis" checked' in response.text
    assert "Ueberwachung ausgeschaltet" in response.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(OrgSettings).filter_by(org_id=org_id).one().dienst_monitor_ausgeschaltet == "alarm_dibos"
        status = db.query(DienstStatus).filter_by(org_id=org_id, key="alarm_dibos").one()
        assert status.down_since is status.outage_notified_at is status.last_repeat_at is None
        assert status.fail_cycles == 0
        from app.models.user import AuditLog
        import json
        audit = db.query(AuditLog).filter_by(org_id=org_id, action="admin.dienst_monitor.empfaenger_geaendert").order_by(AuditLog.id.desc()).first()
        assert json.loads(audit.payload_json)["geaenderte_dienste"] == ["alarm_dibos", "alarm_lis"]
    # Ohne ausgewaehlte Checkboxen werden alle sechs Org-Dienste ausgeschaltet.
    response = client.post("/admin/systemstatus/empfaenger", data={"_csrf": data["_csrf"]})
    assert response.status_code == 200
    with SessionLocal() as db:
        assert set(db.query(OrgSettings).filter_by(org_id=org_id).one().dienst_monitor_ausgeschaltet.split(",")) == set(ORG_DIENST_KEYS)


@pytest.mark.parametrize("fehler", [False, True])
def test_lis_poll_schreibt_probe_im_thread(monkeypatch, fehler):
    from app.services.lis import lis_sync
    from app.services.lis.lis_client import LisClientError
    hauptthread = threading.get_ident()
    aufrufe = []
    def probe(*args):
        assert threading.get_ident() != hauptthread
        aufrufe.append(args)
    class Client:
        session_context_ready = True
        root_org_map = {}
        async def get_operations_in_range(self, *args, **kwargs):
            if fehler:
                raise LisClientError("Fehler")
            return []
    async def nichts(*args, **kwargs):
        return None
    monkeypatch.setattr("app.services.dienst_monitor_service.record_probe", probe)
    # Nach dem Poll folgende Verarbeitung ist fuer die Probe irrelevant.
    monkeypatch.setattr(lis_sync, "_close_incidents_missing_from_lis", nichts)
    monkeypatch.setattr(lis_sync, "_maybe_backfill", nichts, raising=False)
    org = SimpleNamespace(id=1)
    config = SimpleNamespace(organization_id="guid", last_backfill_at=datetime.now(UTC))
    if fehler:
        asyncio.run(lis_sync._sync_organization_with_client(None, org, config, Client()))
    else:
        # Rest des Sync benoetigt eine DB-Session; vorhandene Test-DB nutzen.
        with SessionLocal() as db:
            asyncio.run(lis_sync._sync_organization_with_client(db, org, config, Client()))
    assert aufrufe[0][:3] == (1, "alarm_lis", not fehler)


def test_loop_schliesst_ausfall_ohne_versand(monitor_orgs, monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.dienst_monitor_loop import _process_org_mit_db
    from app.services.dienst_monitor_service import DienstCheck
    db, (org_id, _) = monitor_orgs
    settings = db.query(OrgSettings).filter_by(org_id=org_id).one()
    settings.dienst_monitor_ausgeschaltet = "alarm_dibos"
    settings.dienst_monitor_mail = "test@example.invalid"
    settings.dienst_monitor_sms = "+43123"
    settings.dienst_monitor_teams_webhook_url = "https://teams.invalid"
    now = datetime.now(UTC).replace(tzinfo=None)
    status = DienstStatus(org_id=org_id, key="alarm_dibos", state="down", fail_cycles=10,
                         down_since=now - timedelta(hours=2), outage_notified_at=now - timedelta(hours=2),
                         last_repeat_at=now - timedelta(hours=2))
    db.add(status)
    db.commit()
    monkeypatch.setattr("app.services.dienst_monitor_service.pruefe_dienste", lambda *args: [
        DienstCheck("alarm_dibos", "nicht_konfiguriert", "Ueberwachung ausgeschaltet.", False),
    ])
    versand = AsyncMock()
    monkeypatch.setattr("app.services.dienst_monitor_dispatch.dispatch_meldung", versand)
    asyncio.run(_process_org_mit_db(db, org_id))
    db.refresh(status)
    assert status.outage_notified_at is status.down_since is status.last_repeat_at is None
    assert status.fail_cycles == status.ok_cycles == 0
    versand.assert_not_awaited()


def test_lis_probe_persistiert_erfolg_und_fehler(monitor_orgs):
    from app.services.dienst_monitor_service import record_probe

    db, (org_id, _) = monitor_orgs
    record_probe(org_id, "alarm_lis", True)
    db.expire_all()
    status = db.query(DienstStatus).filter_by(org_id=org_id, key="alarm_lis").one()
    assert status.last_probe_ok is True and status.last_probe_at is not None
    record_probe(org_id, "alarm_lis", False, "LIS-Poll fehlgeschlagen.")
    db.expire_all()
    assert status.last_probe_ok is False
    assert status.last_probe_error == "LIS-Poll fehlgeschlagen."
