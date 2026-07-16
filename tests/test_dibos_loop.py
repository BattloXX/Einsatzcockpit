"""Tests für dibos_loop.py: der leichte Auto-Erkennungs-Loop startet einen Voll-Trace,
sobald GetCurrentEvents für eine Org nicht mehr leer ist.

Nutzt die echte SQLite-Test-DB (siehe conftest.py) mit Org 1 (Seed-Daten "FF Wolfurt"),
monkeypatcht aber DibosClient/is_trace_running/start_trace_for_org — kein echter
Netzwerkzugriff nötig.
"""
import asyncio

from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.models.dibos import OrgDibosConfig
from app.services.dibos import dibos_capture, dibos_client, dibos_loop
from tests.conftest import TestingSession

ORG_ID = 1


def _session():
    db = TestingSession()
    set_tenant_context(db, None)
    return db


def _make_config(db, **overrides) -> OrgDibosConfig:
    db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == ORG_ID).delete()
    defaults = dict(
        org_id=ORG_ID,
        enabled=True,
        auto_trace_on_event=True,
        auto_trace_duration_minutes=90,
        base_url="https://dibos.example.at/Z_EventHub",
        host="testhost",
        ag="FW",
        gateway_user="gw",
        gateway_password_enc=encrypt_secret("gw-pw"),
        service_user="service.test.all",
        service_password_enc=encrypt_secret("svc-pw"),
    )
    defaults.update(overrides)
    cfg = OrgDibosConfig(**defaults)
    db.add(cfg)
    db.flush()
    db.commit()
    return cfg


class _FakeClient:
    def __init__(self, events, *args, **kwargs):
        self._events = events

    async def get_current_events(self):
        return self._events

    async def aclose(self):
        pass


def test_check_org_starts_trace_when_events_nonempty(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db)
        config_id = cfg.id
    finally:
        db.close()

    started = []

    monkeypatch.setattr(dibos_client, "DibosClient", lambda *a, **kw: _FakeClient([{"eventNumber": "f1"}]))
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: False)

    async def fake_start(org_id, duration_minutes=120):
        started.append((org_id, duration_minutes))
        return "run-id-1"

    monkeypatch.setattr(dibos_capture, "start_trace_for_org", fake_start)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert started == [(ORG_ID, 90)]


def test_check_org_skips_when_events_empty(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db)
        config_id = cfg.id
    finally:
        db.close()

    started = []
    monkeypatch.setattr(dibos_client, "DibosClient", lambda *a, **kw: _FakeClient([]))
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: False)

    async def fake_start(org_id, duration_minutes=120):
        started.append(org_id)
        return "run-id"

    monkeypatch.setattr(dibos_capture, "start_trace_for_org", fake_start)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert started == []


def test_check_org_skips_when_trace_already_running(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db)
        config_id = cfg.id
    finally:
        db.close()

    started = []
    monkeypatch.setattr(dibos_client, "DibosClient", lambda *a, **kw: _FakeClient([{"eventNumber": "f1"}]))
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: True)

    async def fake_start(org_id, duration_minutes=120):
        started.append(org_id)
        return "run-id"

    monkeypatch.setattr(dibos_capture, "start_trace_for_org", fake_start)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert started == []


def test_check_org_skips_when_config_disabled(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db, enabled=False)
        config_id = cfg.id
    finally:
        db.close()

    client_built = []
    monkeypatch.setattr(
        dibos_client, "DibosClient",
        lambda *a, **kw: client_built.append(True) or _FakeClient([{"eventNumber": "f1"}]),
    )

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert client_built == []  # Config deaktiviert -> nie ein Client gebaut


def test_check_org_skips_when_not_fully_configured(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db, gateway_password_enc=None)
        config_id = cfg.id
    finally:
        db.close()

    client_built = []
    monkeypatch.setattr(
        dibos_client, "DibosClient",
        lambda *a, **kw: client_built.append(True) or _FakeClient([{"eventNumber": "f1"}]),
    )

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert client_built == []


def test_run_all_orgs_only_checks_enabled_and_auto_trace_configs(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db, enabled=True, auto_trace_on_event=True)
        config_id = cfg.id
    finally:
        db.close()

    checked = []

    async def fake_check_org(org_id, cfg_id):
        checked.append((org_id, cfg_id))

    monkeypatch.setattr(dibos_loop, "_check_org", fake_check_org)

    asyncio.run(dibos_loop._run_all_orgs())

    assert checked == [(ORG_ID, config_id)]


def test_run_all_orgs_skips_disabled_config(monkeypatch):
    db = _session()
    try:
        _make_config(db, enabled=False, auto_trace_on_event=True)
    finally:
        db.close()

    checked = []

    async def fake_check_org(org_id, cfg_id):
        checked.append((org_id, cfg_id))

    monkeypatch.setattr(dibos_loop, "_check_org", fake_check_org)

    asyncio.run(dibos_loop._run_all_orgs())

    assert checked == []
