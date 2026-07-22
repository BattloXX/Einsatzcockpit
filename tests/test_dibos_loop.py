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


# ── Anreicherung unabhängig von auto_trace_on_event (Speicherlast-Reduktion) ──

def test_check_org_enriches_without_starting_trace_when_only_enrich_enabled(monkeypatch):
    """enrich_incidents=True, auto_trace_on_event=False: die Org bekommt trotzdem
    eine Anreicherung aus dem leichten Poll — OHNE dass jemals ein Trace (und
    damit Rohdaten-Dateien auf Platte) gestartet wird."""
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=False, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()

    events = [{"eventNumber": "f1"}]
    monkeypatch.setattr(dibos_client, "DibosClient", lambda *a, **kw: _FakeClient(events))
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: False)

    started = []

    async def fake_start(org_id, duration_minutes=120):
        started.append(org_id)
        return "run-id"

    monkeypatch.setattr(dibos_capture, "start_trace_for_org", fake_start)

    enrich_calls = []

    async def fake_enrich_and_broadcast(org_id, raw_events):
        enrich_calls.append((org_id, raw_events))

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert enrich_calls == [(ORG_ID, events)]
    assert started == []  # kein Trace gestartet


def test_check_org_skips_enrichment_when_events_empty(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=False, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()

    monkeypatch.setattr(dibos_client, "DibosClient", lambda *a, **kw: _FakeClient([]))
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: False)

    enrich_calls = []

    async def fake_enrich_and_broadcast(org_id, raw_events):
        enrich_calls.append((org_id, raw_events))

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert enrich_calls == []


def test_check_org_skips_entirely_when_neither_capability_enabled(monkeypatch):
    """Weder Voll-Tracing noch Anreicherung aktiviert: kein API-Aufruf nötig."""
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=False, enrich_incidents=False)
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


def test_check_org_does_both_when_both_enabled(monkeypatch):
    """Beide Schalter aktiv: Anreicherung läuft UND der Trace wird gestartet."""
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=True, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()

    events = [{"eventNumber": "f1"}]
    monkeypatch.setattr(dibos_client, "DibosClient", lambda *a, **kw: _FakeClient(events))
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: False)

    started = []

    async def fake_start(org_id, duration_minutes=120):
        started.append(org_id)
        return "run-id"

    monkeypatch.setattr(dibos_capture, "start_trace_for_org", fake_start)

    enrich_calls = []

    async def fake_enrich_and_broadcast(org_id, raw_events):
        enrich_calls.append((org_id, raw_events))

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert enrich_calls == [(ORG_ID, events)]
    assert started == [ORG_ID]


def test_check_org_running_trace_skips_lightweight_poll_even_with_enrich_enabled(monkeypatch):
    """Läuft bereits ein Trace, überlässt der leichte Loop ihm die Anreicherung —
    kein doppeltes GetCurrentEvents."""
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=True, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()

    client_built = []
    monkeypatch.setattr(
        dibos_client, "DibosClient",
        lambda *a, **kw: client_built.append(True) or _FakeClient([{"eventNumber": "f1"}]),
    )
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: True)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert client_built == []


def test_run_all_orgs_includes_enrich_only_config(monkeypatch):
    """_lade_paare() darf eine Org NICHT mehr ausschließen, nur weil
    auto_trace_on_event=False ist, solange enrich_incidents=True gesetzt ist."""
    db = _session()
    try:
        cfg = _make_config(db, enabled=True, auto_trace_on_event=False, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()

    checked = []

    async def fake_check_org(org_id, cfg_id):
        checked.append((org_id, cfg_id))

    monkeypatch.setattr(dibos_loop, "_check_org", fake_check_org)

    asyncio.run(dibos_loop._run_all_orgs())

    assert checked == [(ORG_ID, config_id)]
