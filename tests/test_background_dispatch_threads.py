"""L2: Session-Lebensdauer im Thread, Transport auf dem aufrufenden Loop."""
from __future__ import annotations

import asyncio
import importlib
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app import db as datenbank
from app.core.tenant import set_tenant_context
from tests.conftest import TestingSession

_MODULE = (
    "alarm_outbox", "api_message_dispatch_loop", "mailing_dispatch_loop",
    "road_closure_notification_loop", "task_reminder", "gk_zugang_service",
    "resend_inbound_service", "mail_graph_poller", "mail_imap_poller",
    "mail_weiterleitung_loop", "mail_zuordnung_loop", "weather_alert_loop",
)


@contextmanager
def db_threads(monkeypatch):
    aufrufe = []
    loop_thread = threading.get_ident()

    def pruefen(*args):
        aufrufe.append(threading.get_ident())
        assert threading.get_ident() != loop_thread

    def session():
        pruefen()
        db = TestingSession()
        for name in ("do_orm_execute", "before_flush", "before_commit"):
            event.listen(db, name, pruefen)
        schliessen = db.close

        def close():
            pruefen()
            schliessen()

        db.close = close
        return db

    with monkeypatch.context() as patch:
        patch.setattr(datenbank, "SessionLocal", session)
        for name in _MODULE:
            modul = importlib.import_module(f"app.services.{name}")
            if hasattr(modul, "SessionLocal"):
                patch.setattr(modul, "SessionLocal", session)
        yield
    assert aufrufe


@pytest.mark.parametrize(("modul", "funktion", "args"), [
    ("alarm_outbox", "_run_claimed", (-1, 1)),
    ("alarm_outbox", "process_incident_alarm", (-1,)),
    ("alarm_outbox", "run_alarm_outbox_once", ()),
    ("api_message_dispatch_loop", "dispatch_once", ()),
    ("mailing_dispatch_loop", "dispatch_once", ()),
    ("road_closure_notification_loop", "_process", (-1, datetime(2026, 11, 6))),
    ("road_closure_notification_loop", "process_due", ()),
    ("task_reminder", "_check_katplan_wiedervorlagen", ()),
    ("task_reminder", "_check_pflegeauftrag_erinnerungen", ()),
    ("mail_graph_poller", "_save_one", (-1, {}, b"", False)),
    ("mail_imap_poller", "_save_one", (-1, 1, {"uid": 1, "received": None})),
    ("mail_weiterleitung_loop", "run_mail_weiterleitung_once", (-1,)),
    ("mail_zuordnung_loop", "_process_claimed_mail", (-1, datetime(2026, 11, 6))),
    ("mail_zuordnung_loop", "run_mail_zuordnung_once", ()),
    ("weather_alert_loop", "_process_org", (-1, "")),
])
async def test_db_ausserhalb_event_loop(monkeypatch, modul, funktion, args):
    service = importlib.import_module(f"app.services.{modul}")
    if modul == "task_reminder":
        monkeypatch.setattr(service, "_letzter_katplan_check", None)
        monkeypatch.setattr(service, "_letzter_pflegeauftrag_check", None)
    with db_threads(monkeypatch):
        tasks = await getattr(service, funktion)(*args)
        if modul == "alarm_outbox" and tasks:
            await asyncio.gather(*tasks)


@pytest.mark.parametrize("modul", ["mail_graph_poller", "mail_imap_poller"])
async def test_poller_fehler_und_limit_im_thread(monkeypatch, modul):
    service = importlib.import_module(f"app.services.{modul}")
    from app.config import settings

    monkeypatch.setattr(settings, "MAIL_GRAPH_POLL_ENABLED", True)
    monkeypatch.setattr(settings, "O365_MAIL_ENABLED", True)
    with db_threads(monkeypatch):
        assert await service.abrufen_fuer_org(-1) == 0
        assert not await service.im_db_thread(service._abruf_erlaubt_sync, -1)
        await service.im_db_thread(service._speichere_abruf_fehler_sync, -1, "Testfehler")


@pytest.mark.parametrize(("modul", "funktion"), [
    ("gk_zugang_service", "gk_versand_aufraeum_loop"),
    ("resend_inbound_service", "resend_inbound_retention_loop"),
])
async def test_retention_iteration_im_thread(monkeypatch, modul, funktion):
    service = importlib.import_module(f"app.services.{modul}")

    async def stop(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop)
    with db_threads(monkeypatch), pytest.raises(asyncio.CancelledError):
        await getattr(service, funktion)()


async def test_mailing_claim_und_ergebnis_im_thread_transport_auf_loop(monkeypatch):
    from app.models.mailing import MailingConfig, MailingQueueItem
    from app.services import mailing_dispatch_loop as service
    from tests.mailing_phase2_helpers import campaign

    loop = asyncio.get_running_loop()
    with TestingSession() as db:
        set_tenant_context(db, None)
        c, _ = campaign(db, status="queued")
        c.total_count = 1
        item = MailingQueueItem(org_id=1, campaign_id=c.id, email="thread@example.invalid", status="queued")
        cfg = db.query(MailingConfig).filter_by(org_id=1).first() or MailingConfig(org_id=1)
        cfg.enabled, cfg.resend_api_key_enc, cfg.from_addr = True, "test", "from@example.invalid"
        db.add_all([item, cfg])
        db.commit()
        item_id = item.id
    gesendet = []

    async def send(*args):
        assert asyncio.get_running_loop() is loop
        # Der Claim ist bereits committed, bevor der Transport beginnt.
        with TestingSession() as db:
            set_tenant_context(db, None)
            assert db.get(MailingQueueItem, item_id).status == "sending"
        gesendet.append(1)
        return "thread-resend"

    monkeypatch.setattr(service, "send_via_resend", send)
    monkeypatch.setattr(service, "mailing_api_key", lambda cfg: "test-key")
    with db_threads(monkeypatch):
        await service.dispatch_once()
    with TestingSession() as db:
        set_tenant_context(db, None)
        item = db.get(MailingQueueItem, item_id)
        assert (item.status, item.resend_message_id, item.attempt_count) == ("sent", "thread-resend", 1), item.error_message
    assert gesendet


async def test_erinnerung_versand_session_im_thread_smtp_auf_loop(monkeypatch):
    from email.message import EmailMessage

    from app.services import mail_service, task_reminder

    loop = asyncio.get_running_loop()
    gesendet = []

    async def send(*args):
        assert asyncio.get_running_loop() is loop
        gesendet.append(1)

    monkeypatch.setattr(mail_service, "_send", send)
    monkeypatch.setattr(mail_service, "_org_resend_cfg", lambda *a: None)
    monkeypatch.setattr(mail_service, "get_resend_cfg", lambda *a: None)
    monkeypatch.setattr("app.config.settings.O365_MAIL_ENABLED", False)
    with db_threads(monkeypatch):
        await task_reminder.im_db_thread(task_reminder._katplan_mail_sync, {
            "org_id": 1, "msg": EmailMessage(), "smtp_cfg": {},
        })
        await task_reminder.im_db_thread(task_reminder._pflegeauftrag_mail_sync, {
            "org_id": 1, "to": "thread@example.invalid", "kontakt_name": "Kontakt",
            "objekt_name": "Objekt", "raw_token": "token", "auftrag_text": "Test",
            "gueltig_bis": datetime.now(UTC).replace(tzinfo=None),
        })
    assert len(gesendet) == 2


def test_alarm_pool_unabhaengig_vom_hintergrund_pool():
    """Belegte Hintergrund-Threads (LIS, Routen) duerfen Alarmkanaele nicht verzoegern."""
    import asyncio
    import threading
    import time

    from app.services import loop_thread_io

    freigabe = threading.Event()

    def blockiert():
        freigabe.wait(5)

    def alarmkanal():
        time.sleep(0.2)
        return threading.current_thread().name

    async def ablauf():
        belegt = [asyncio.create_task(loop_thread_io.im_db_thread(blockiert)) for _ in range(4)]
        await asyncio.sleep(0.05)
        start = time.monotonic()
        namen = await asyncio.gather(*(loop_thread_io.im_db_thread(alarmkanal, pool=loop_thread_io.ALARM_POOL)
                                       for _ in range(4)))
        dauer = time.monotonic() - start
        freigabe.set()
        await asyncio.gather(*belegt)
        return namen, dauer

    namen, dauer = asyncio.run(ablauf())
    assert all(name.startswith("alarm-db") for name in namen)
    assert dauer < 0.6, "Alarmkanaele liefen nicht parallel"
