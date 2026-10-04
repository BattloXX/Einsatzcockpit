"""Tests für dibos_loop.py: der leichte Auto-Erkennungs-Loop startet einen Voll-Trace,
sobald GetCurrentEvents für eine Org nicht mehr leer ist.

Nutzt die echte SQLite-Test-DB (siehe conftest.py) mit Org 1 (Seed-Daten "FF Wolfurt"),
monkeypatcht aber DibosClient/is_trace_running/start_trace_for_org — kein echter
Netzwerkzugriff nötig.
"""
import asyncio

import pytest

from app.core.crypto import encrypt_secret
from app.core.tenant import set_tenant_context
from app.models.dibos import OrgDibosConfig
from app.models.incident import Incident
from app.services.dibos import dibos_capture, dibos_client, dibos_loop
from app.services.incident_service import create_incident
from tests.conftest import TestingSession

ORG_ID = 1


@pytest.fixture(autouse=True)
def _reset_loop_state():
    async def _reset():
        await dibos_loop._close_all_clients()
        dibos_loop._previous_event_numbers.clear()
        dibos_loop._last_public_events_at.clear()
        dibos_loop._last_enrich_fingerprint.clear()

    asyncio.run(_reset())
    yield
    asyncio.run(_reset())


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
    def __init__(self, events, *args, public_events=None, **kwargs):
        self._events = events
        self._public_events = public_events or []

    async def get_current_events(self):
        return self._events

    async def get_current_units(self):
        return []

    async def get_public_events(self):
        return self._public_events

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

    async def fake_enrich_and_broadcast(org_id, raw_events, **kwargs):
        enrich_calls.append((org_id, raw_events))
        return True
        return True

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert enrich_calls == [(ORG_ID, events), (ORG_ID, events)]
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

    async def fake_enrich_and_broadcast(org_id, raw_events, **kwargs):
        enrich_calls.append((org_id, raw_events))

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert enrich_calls == []


def test_check_org_enriches_closed_public_event_when_current_events_empty(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=False, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()

    db = _session()
    try:
        incident, _ = create_incident(db, "T1", primary_org_id=ORG_ID)
        incident.lis_operation_number = "f26007481"
        db.commit()
    finally:
        db.close()

    public_events = [{"eventNumber": "f26007481", "closed": "2026-08-25T11:23:45"}]
    monkeypatch.setattr(
        dibos_client, "DibosClient",
        lambda *a, **kw: _FakeClient([], public_events=public_events),
    )
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: False)
    enrich_calls = []

    async def fake_enrich_and_broadcast(org_id, raw_events, **kwargs):
        enrich_calls.append((org_id, raw_events, kwargs))
        return True

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert enrich_calls == [(ORG_ID, [], {
        "raw_public_events": public_events,
        "raw_units": [],
        "wache_unid": cfg.wache_unid,
        "create_incidents": False,
    })]


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

    async def fake_enrich_and_broadcast(org_id, raw_events, **kwargs):
        enrich_calls.append((org_id, raw_events))

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert enrich_calls == [(ORG_ID, events), (ORG_ID, events)]
    assert started == [ORG_ID]


def test_check_org_polls_and_enriches_while_trace_is_running(monkeypatch):
    """Ein Trace ist Diagnose; der schnelle Poll bleibt für Anlage aktiv."""
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=True, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()

    client_built = []
    enrich_calls = []
    monkeypatch.setattr(
        dibos_client, "DibosClient",
        lambda *a, **kw: client_built.append(True) or _FakeClient([{"eventNumber": "f1"}]),
    )
    monkeypatch.setattr(dibos_capture, "is_trace_running", lambda org_id: True)

    async def fake_enrich_and_broadcast(*args, **kwargs):
        enrich_calls.append(args)
        return True

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich_and_broadcast)

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert client_built == [True]
    assert enrich_calls


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


class _RecordingClient(_FakeClient):
    def __init__(self, events, *args, public_events=None, calls=None, error=False, **kwargs):
        super().__init__(events, *args, public_events=public_events, **kwargs)
        self.calls = calls if calls is not None else []
        self.error = error
        self.closed = False

    async def get_current_events(self):
        self.calls.append("current")
        if self.error:
            from app.services.dibos.dibos_client import DibosClientError
            raise DibosClientError("kaputt")
        return await super().get_current_events()

    async def get_current_units(self):
        self.calls.append("units")
        return await super().get_current_units()

    async def get_public_events(self):
        self.calls.append("public")
        return await super().get_public_events()

    async def aclose(self):
        self.closed = True


def _close_active_incidents():
    db = _session()
    try:
        db.query(Incident).filter(Incident.primary_org_id == ORG_ID, Incident.status == "active").update({
            Incident.status: "closed",
        })
        db.commit()
    finally:
        db.close()


def test_check_org_fast_path_enriches_before_units_and_public(monkeypatch):
    db = _session()
    try:
        config_id = _make_config(db, auto_trace_on_event=False, enrich_incidents=True).id
    finally:
        db.close()
    order = []
    monkeypatch.setattr(
        dibos_client, "DibosClient",
        lambda *args, **kwargs: _RecordingClient([{"eventNumber": "fast-1"}], calls=order),
    )

    async def fake_enrich(*args, **kwargs):
        order.append("enrich")
        return True

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", fake_enrich)
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert order.index("enrich") < order.index("units")
    assert order.index("enrich") < order.index("public")


def test_check_org_fetches_public_events_only_for_missing_active_incident(monkeypatch):
    db = _session()
    try:
        config_id = _make_config(db, auto_trace_on_event=False, enrich_incidents=True).id
    finally:
        db.close()
    _close_active_incidents()
    clients = []
    monkeypatch.setattr(
        dibos_client, "DibosClient",
        lambda *args, **kwargs: clients.append(_RecordingClient([], calls=[])) or clients[-1],
    )

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert "public" not in clients[0].calls


def test_check_org_fetches_missing_active_incident_public_events_then_throttles(monkeypatch):
    db = _session()
    try:
        config_id = _make_config(db, auto_trace_on_event=False, enrich_incidents=True).id
    finally:
        db.close()
    _close_active_incidents()
    db = _session()
    try:
        incident, _ = create_incident(db, "T1", primary_org_id=ORG_ID)
        incident.lis_operation_number = "gone-1"
        db.commit()
    finally:
        db.close()
    current_events = [[{"eventNumber": "gone-1"}], [], [], []]
    client = _RecordingClient([], calls=[])

    async def get_current_events():
        client.calls.append("current")
        return current_events.pop(0)

    client.get_current_events = get_current_events
    monkeypatch.setattr(dibos_client, "DibosClient", lambda *args, **kwargs: client)
    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", lambda *args, **kwargs: asyncio.sleep(0, True))

    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    dibos_loop._last_public_events_at[ORG_ID] = 0
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))

    assert client.calls.count("public") == 2


def test_check_org_caches_successful_fingerprint_and_retries_failed_enrich(monkeypatch):
    db = _session()
    try:
        config_id = _make_config(db, auto_trace_on_event=False, enrich_incidents=True).id
    finally:
        db.close()
    _close_active_incidents()
    client = _RecordingClient([{"eventNumber": "fingerprint-1"}], calls=[])
    monkeypatch.setattr(dibos_client, "DibosClient", lambda *args, **kwargs: client)
    calls = []

    async def successful_enrich(*args, **kwargs):
        calls.append(args[1])
        return True

    import app.services.dibos.dibos_enrich as dibos_enrich
    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", successful_enrich)
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    after_first = len(calls)
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    client._events = [{"eventNumber": "fingerprint-1", "changed": True}]
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    assert len(calls) == after_first + 1

    dibos_loop._last_enrich_fingerprint.clear()
    dibos_loop._previous_event_numbers.clear()
    calls.clear()

    async def failed_enrich(*args, **kwargs):
        calls.append(args[1])
        return False

    monkeypatch.setattr(dibos_enrich, "enrich_and_broadcast", failed_enrich)
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    assert len(calls) >= 3


def test_check_org_reuses_client_rebuilds_on_config_change_and_error(monkeypatch):
    db = _session()
    try:
        cfg = _make_config(db, auto_trace_on_event=False, enrich_incidents=True)
        config_id = cfg.id
    finally:
        db.close()
    built = []

    def build_client(*args, **kwargs):
        client = _RecordingClient([], calls=[])
        built.append(client)
        return client

    monkeypatch.setattr(dibos_client, "DibosClient", build_client)
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    assert len(built) == 1

    db = _session()
    try:
        db.get(OrgDibosConfig, config_id).gateway_password_enc = encrypt_secret("changed")
        db.commit()
    finally:
        db.close()
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    assert len(built) == 2
    assert built[0].closed is True

    built[-1].error = True
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    assert built[-1].closed is True
    asyncio.run(dibos_loop._check_org(ORG_ID, config_id))
    assert len(built) == 3
