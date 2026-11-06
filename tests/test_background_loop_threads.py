"""DB-Ausfuehrung der L1-Loops und Loop-Bindung ihrer Async-I/O."""
from __future__ import annotations

import asyncio
import importlib
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, text

from app import db as datenbank
from app.core.tenant import set_tenant_context
from tests.conftest import TestingSession


@contextmanager
def _db_threads(monkeypatch):
    treffer = []

    def pruefen(*args):
        treffer.append(threading.current_thread() is threading.main_thread())

    def session():
        pruefen()
        db = TestingSession()
        event.listen(db, "do_orm_execute", pruefen)
        event.listen(db, "before_flush", pruefen)
        event.listen(db, "after_commit", pruefen)
        return db

    with monkeypatch.context() as patch:
        patch.setattr(datenbank, "SessionLocal", session)
        for modul in ("breathing_notify", "incident_route_loop"):
            patch.setattr(importlib.import_module(f"app.services.{modul}"), "SessionLocal", session)
        yield treffer
    assert treffer, "Kein DB-Aufruf erfasst"
    assert not any(treffer), "Session/Query/Flush/Commit lief auf dem Hauptthread"


@pytest.mark.parametrize(("modul", "funktion", "args"), [
    ("lis.lis_loop", "_run_all_orgs", ()),
    ("lis.lis_loop", "_sync_one_org", (-1, -1)),
    ("dibos.dibos_loop", "_run_all_orgs", ()),
    ("dibos.dibos_loop", "_has_missing_active_incidents", (1, set())),
    ("dibos.dibos_loop", "_check_org", (-1, -1)),
    ("abfluss_poll_loop", "_poll_all_orgs", ()),
    ("dienst_monitor_loop", "_run_all_orgs", ()),
    ("dienst_monitor_loop", "_process_org", (1,)),
    ("incident_route_loop", "_mark_unexpected_error", (-1,)),
    ("incident_route_loop", "process_route", (-1,)),
    ("incident_route_loop", "run_incident_route_once", ()),
    ("breathing_notify", "liefere_faellige", ()),
])
async def test_loop_db_ausserhalb_hauptthread(monkeypatch, modul, funktion, args):
    with _db_threads(monkeypatch):
        await getattr(importlib.import_module(f"app.services.{modul}"), funktion)(*args)


async def test_watchdog_db_im_thread_broadcast_auf_loop(monkeypatch):
    from app.services import breathing_service
    from app.services.broadcast import manager

    hauptloop = asyncio.get_running_loop()
    broadcasts = []
    scans = []
    original_sleep = asyncio.sleep
    runden = 0

    def sammeln(db):
        scans.append(threading.current_thread() is threading.main_thread())
        db.execute(text("SELECT 1"))
        return [(123, 456, "notfall")]

    async def broadcast(*args):
        assert asyncio.get_running_loop() is hauptloop
        broadcasts.append(args)

    async def sleep(delay):
        nonlocal runden
        runden += 1
        if runden == 2:
            raise asyncio.CancelledError
        await original_sleep(0)

    monkeypatch.setattr(breathing_service, "_collect_new_breathing_warnings", sammeln)
    monkeypatch.setattr(manager, "broadcast", broadcast)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with _db_threads(monkeypatch), pytest.raises(asyncio.CancelledError):
        await breathing_service._breathing_watchdog_loop()
    assert scans == [False]
    assert broadcasts == [(123, {"type": "troop_warning", "troop_id": 456, "kind": "notfall"})]


@pytest.mark.parametrize("neuanlage", [False, True])
async def test_lis_transaktion_im_thread_http_auf_urspruenglichem_loop(monkeypatch, neuanlage):
    from app.core.crypto import encrypt_secret
    from app.models.lis import OrgLisConfig
    from app.services.lis import lis_loop, lis_sync

    hauptloop = asyncio.get_running_loop()
    aufrufe = []

    class Client:
        session_context_ready = False
        root_org_map = None

        def __setattr__(self, name, wert):
            assert asyncio.get_running_loop() is hauptloop
            object.__setattr__(self, name, wert)

        async def select_operation(self, *args, **kwargs):
            assert asyncio.get_running_loop() is hauptloop
            aufrufe.append("select")

        async def get_root_organizations(self):
            assert asyncio.get_running_loop() is hauptloop
            aufrufe.append("root")
            return []

        async def get_operations_in_range(self, *args, **kwargs):
            assert asyncio.get_running_loop() is hauptloop
            aufrufe.append("operations")
            return [{"Id": "thread-neuanlage", "Number": "f-thread-neuanlage", "Description": "Thread-Test",
                     "Type": {"Code": "t4"}, "BeginTime": "2026-11-06T12:00:00"}] if neuanlage else []

        async def get_tasks(self, *args):
            assert asyncio.get_running_loop() is hauptloop
            aufrufe.append("tasks")
            return []

        async def get_operation_units(self, *args):
            assert asyncio.get_running_loop() is hauptloop
            aufrufe.append("units")
            return []

        async def get_documents_by_operation_id(self, *args):
            assert asyncio.get_running_loop() is hauptloop
            aufrufe.append("documents")
            return []

    with TestingSession() as db:
        set_tenant_context(db, None)
        konfiguration = OrgLisConfig(
            org_id=1, enabled=True, base_url="https://lis.invalid", site="LIS", username="thread-test",
            password_enc=encrypt_secret("secret"), organization_id="org-guid", poll_interval_seconds=10,
            last_backfill_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(konfiguration)
        db.commit()
        config_id = konfiguration.id
    client = Client()

    async def lade_client(*args):
        return client

    monkeypatch.setattr(lis_loop, "_get_client", lade_client)
    verarbeitet, gedruckt = [], []

    async def alarm(incident_id):
        assert asyncio.get_running_loop() is hauptloop
        verarbeitet.append(incident_id)

    async def matching(incident_id):
        assert asyncio.get_running_loop() is hauptloop

    async def broadcast(*args):
        assert asyncio.get_running_loop() is hauptloop

    def druck(incident_id):
        assert asyncio.get_running_loop() is hauptloop
        gedruckt.append(incident_id)

    monkeypatch.setattr("app.services.alarm_outbox.process_incident_alarm", alarm)
    monkeypatch.setattr("app.services.objekt_matching_service.match_incident_background", matching)
    monkeypatch.setattr("app.services.broadcast.broadcast_org", broadcast)
    monkeypatch.setattr(lis_sync, "_starte_autoprint", druck)
    try:
        with _db_threads(monkeypatch):
            await lis_loop._sync_one_org(1, config_id)
        assert aufrufe == ["select", "root", "operations"] + (
            ["select", "tasks", "units", "documents"] if neuanlage else []
        )
        assert verarbeitet == gedruckt
        if neuanlage:
            from app.models.incident import IncidentAlarmJob
            assert len(verarbeitet) == 1
            with TestingSession() as db:
                set_tenant_context(db, None)
                jobs = db.query(IncidentAlarmJob).filter_by(incident_id=verarbeitet[0]).all()
                assert {job.channel for job in jobs} == {"mail", "push", "sms", "teams"}
    finally:
        with TestingSession() as db:
            set_tenant_context(db, None)
            db.delete(db.get(OrgLisConfig, config_id))
            db.commit()


async def test_thread_abbruch_schliesst_transaktion_vor_rueckkehr():
    from app.services.loop_thread_io import auf_event_loop, im_db_thread, thread_ablauf

    gestartet = asyncio.Event()
    geschlossen = threading.Event()
    hauptloop = asyncio.get_running_loop()

    async def http():
        assert asyncio.get_running_loop() is hauptloop
        gestartet.set()
        await asyncio.Future()

    async def ablauf():
        try:
            await auf_event_loop(http)
        finally:
            geschlossen.set()

    task = asyncio.create_task(im_db_thread(lambda: thread_ablauf(ablauf())))
    await asyncio.wait_for(gestartet.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert geschlossen.is_set()


@pytest.mark.parametrize("zustand", ["down", "ok"])
@pytest.mark.parametrize("kanal", ["sms", "mail", "teams"])
async def test_dienst_monitor_claim_versand_rollback_im_thread(monkeypatch, zustand, kanal):
    from datetime import timedelta
    from uuid import uuid4

    from app.models.dienst_monitor import DienstMonitorLog, DienstStatus
    from app.models.master import FireDept, OrgSettings
    from app.services import dienst_monitor_loop, dienst_monitor_service, sms_service

    now = datetime.now(UTC).replace(tzinfo=None)
    hauptloop = asyncio.get_running_loop()
    gesendet = []
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=f"thread-monitor-{uuid4().hex}", name="Thread-Monitor")
        db.add(org)
        db.flush()
        org_id = org.id
        db.add(OrgSettings(org_id=org_id, dienst_monitor_enabled=True, dienst_monitor_karenz_min=0,
                           dienst_monitor_sms="+43123" if kanal == "sms" else None,
                           dienst_monitor_mail="test@example.invalid" if kanal == "mail" else None,
                           dienst_monitor_teams_webhook_url="https://teams.invalid" if kanal == "teams" else None))
        row = DienstStatus(org_id=org_id, key="print_gateway", state="down", fail_cycles=1,
                           down_since=now - timedelta(minutes=10), since=now - timedelta(minutes=10),
                           outage_notified_at=now - timedelta(minutes=5) if zustand == "ok" else None)
        db.add(row)
        db.commit()
        row_id = row.id

    def pruefen(*args):
        assert threading.current_thread() is not threading.main_thread()
        return [dienst_monitor_service.DienstCheck("print_gateway", zustand, "Test", True)]

    async def sms(*args, **kwargs):
        assert asyncio.get_running_loop() is hauptloop
        gesendet.append(args)
        return sms_service.SmsDeliveryResult(success=False)

    async def mail(*args):
        assert asyncio.get_running_loop() is hauptloop
        gesendet.append(args)
        raise RuntimeError("Testfehler")

    async def teams(*args):
        assert asyncio.get_running_loop() is hauptloop
        gesendet.append(args)
        return False

    monkeypatch.setattr("app.services.mail_service._send", mail)
    monkeypatch.setattr("app.services.teams_service.post_teams_karte", teams)
    monkeypatch.setattr(dienst_monitor_service, "pruefe_dienste", pruefen)
    monkeypatch.setattr(sms_service, "send_sms", sms)
    with _db_threads(monkeypatch):
        await dienst_monitor_loop._process_org(org_id)
    assert len(gesendet) == 1
    with TestingSession() as db:
        set_tenant_context(db, None)
        row = db.get(DienstStatus, row_id)
        # Fehlgeschlagene Stoerung wieder freigeben; fehlgeschlagene Entwarnung
        # darf den alten Ausfallmarker dagegen nicht wiederherstellen.
        assert row.outage_notified_at is None
        assert row.last_repeat_at is None
        assert db.query(DienstMonitorLog).filter_by(org_id=org_id, status="fehler").count() == 1
        einstellung = db.query(OrgSettings).filter_by(org_id=org_id).one()
        einstellung.dienst_monitor_enabled = False
        db.commit()


async def test_dibos_konfiguration_lookup_probe_im_thread_http_auf_loop(monkeypatch):
    from app.core.crypto import encrypt_secret
    from app.models.dibos import OrgDibosConfig
    from app.services.dibos import dibos_loop

    hauptloop = asyncio.get_running_loop()

    class Client:
        async def get_current_events(self):
            assert asyncio.get_running_loop() is hauptloop
            return []

        async def get_public_events(self):
            assert asyncio.get_running_loop() is hauptloop
            return []

    async def client(*args):
        return Client()

    with TestingSession() as db:
        set_tenant_context(db, None)
        konfiguration = OrgDibosConfig(
            org_id=1, enabled=True, enrich_incidents=True, auto_trace_on_event=False,
            base_url="https://dibos.invalid", gateway_user="gw", gateway_password_enc=encrypt_secret("pw"),
            service_user="service", service_password_enc=encrypt_secret("pw"),
        )
        db.add(konfiguration)
        db.commit()
        config_id = konfiguration.id
    monkeypatch.setattr(dibos_loop, "_get_client", client)
    try:
        with _db_threads(monkeypatch):
            await dibos_loop._check_org(1, config_id)
    finally:
        with TestingSession() as db:
            set_tenant_context(db, None)
            db.delete(db.get(OrgDibosConfig, config_id))
            db.commit()
        dibos_loop._previous_event_numbers.clear()
        dibos_loop._last_public_events_at.clear()
        dibos_loop._last_enrich_fingerprint.clear()


async def test_route_db_im_thread_provider_und_broadcast_auf_loop(monkeypatch):
    from uuid import uuid4

    from app.models.incident import Incident
    from app.models.master import FireDept, OrgSettings
    from app.models.road_closure import IncidentRoute
    from app.services import einsatz_routing, incident_route_loop

    hauptloop = asyncio.get_running_loop()
    broadcasts = []
    http = []

    class Provider:
        name = "thread-test"
        supports_avoid_polygons = True

        async def calculate_route(self, *args, **kwargs):
            assert asyncio.get_running_loop() is hauptloop
            http.append(args)
            return einsatz_routing.RouteResponse(einsatz_routing.RouteResult(
                {"type": "LineString", "coordinates": [[9.73, 47.47], [9.76, 47.47]]},
                1000, 100, [],
            ), [], "thread-test", True)

    async def notify(*args):
        assert asyncio.get_running_loop() is hauptloop
        broadcasts.append(args)

    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=f"thread-route-{uuid4().hex}", name="Thread-Route")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, routing_start_lat=47.47, routing_start_lng=9.73))
        incident = Incident(primary_org_id=org.id, status="active", lat=47.47, lng=9.76)
        db.add(incident)
        db.flush()
        route = IncidentRoute(incident_id=incident.id, org_id=org.id, status="running")
        db.add(route)
        db.commit()
        route_id, incident_id, org_id = route.id, incident.id, org.id
    monkeypatch.setattr(einsatz_routing, "get_provider", Provider)
    monkeypatch.setattr(incident_route_loop, "notify_route_updated", notify)
    with _db_threads(monkeypatch):
        await incident_route_loop.process_route(route_id)
    assert http == [((47.47, 9.73), (47.47, 9.76))]
    assert broadcasts == [(incident_id, org_id)]


async def test_atemschutz_claim_push_ergebnis_im_thread_sms_auf_loop(monkeypatch):
    from datetime import timedelta
    from uuid import uuid4

    from app.models.breathing import BreathingTroop
    from app.models.incident import Incident
    from app.models.master import FireDept, OrgSettings
    from app.models.user import User
    from app.services import breathing_notify

    now = datetime.now(UTC).replace(tzinfo=None)
    hauptloop = asyncio.get_running_loop()
    push_threads, sms_loops = [], []
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=f"thread-asuew-{uuid4().hex}", name="Thread-ASUEW")
        db.add(org)
        db.flush()
        user = User(username=f"thread-asuew-{uuid4().hex}", display_name="Leitung", org_id=org.id,
                    active=True, phone="+43123")
        db.add(user)
        db.flush()
        db.add(OrgSettings(org_id=org.id, asuew_eskalation_sms=True))
        incident = Incident(primary_org_id=org.id, incident_leader_user_id=user.id, status="active", is_exercise=False)
        db.add(incident)
        db.flush()
        troop = BreathingTroop(incident_id=incident.id, name="Trupp", status="im_einsatz",
                              ueberwacher_user_id=user.id, entry_at=now)
        db.add(troop)
        db.flush()
        breathing_notify.sync_warnungen(db, incident, troop, {"withdraw"}, now)
        db.commit()

    def push(*args):
        push_threads.append(threading.current_thread() is threading.main_thread())
        return True

    async def sms(*args):
        sms_loops.append(asyncio.get_running_loop())
        return True

    with _db_threads(monkeypatch):
        assert await breathing_notify.liefere_faellige(now + timedelta(seconds=60), push, sms) == 3
    assert push_threads == [False, False]
    assert sms_loops == [hauptloop]


def test_im_db_thread_nutzt_eigenen_pool_und_kontext():
    """Hintergrund-Ablaeufe duerfen den asyncio-Standardpool (#898) nicht belegen."""
    import asyncio
    import threading

    from app.services import loop_thread_io

    async def ablauf():
        haupt_loop = asyncio.get_running_loop()

        def im_thread():
            return threading.current_thread().name, loop_thread_io._io_loop.get() is haupt_loop

        return await loop_thread_io.im_db_thread(im_thread)

    name, kontext_ok = asyncio.run(ablauf())
    assert name.startswith("hintergrund-db")
    assert kontext_ok
