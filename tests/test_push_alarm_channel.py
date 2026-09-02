"""Tests fuer den exklusiven FCM-Alarm-Channel bei neuen Einsaetzen."""
import asyncio
import logging
import sys
from time import perf_counter
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.user import FcmDeliveryLog, FcmToken, PushLog, User
from app.services import push_service
from app.services.incident_notify import notify_incident_created


def _incident() -> Incident:
    return Incident(
        id=42,
        alarm_type_code="B2",
        address_city="Testort",
        is_exercise=True,
    )


@pytest.mark.parametrize("mit_background_tasks", [False, True])
@pytest.mark.asyncio
async def test_incident_channels_run_concurrently(monkeypatch, mit_background_tasks):
    gestartet: list[str] = []

    async def langsam(name, *args, **kwargs):
        gestartet.append(name)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(
        "app.services.sms_dispatch_service.dispatch_einsatzinfo",
        lambda *args, **kwargs: langsam("sms"),
    )
    monkeypatch.setattr(
        "app.services.incident_notify._send_incident_push",
        lambda *args, **kwargs: langsam("push"),
    )
    monkeypatch.setattr(
        "app.services.teams_alarm_service.post_incident_card",
        lambda *args, **kwargs: langsam("teams"),
    )
    background_tasks = BackgroundTasks() if mit_background_tasks else None

    start = perf_counter()
    await notify_incident_created(
        Mock(), _incident(), org_id=1, base_url="https://example.test",
        background_tasks=background_tasks,
    )
    if background_tasks is not None:
        await background_tasks()
    dauer = perf_counter() - start

    assert set(gestartet) == {"sms", "push", "teams"}
    assert dauer < 0.11


@pytest.mark.asyncio
async def test_incident_channel_error_does_not_stop_others(monkeypatch, caplog):
    beendet: list[str] = []

    async def sms_fehler(*args, **kwargs):
        raise RuntimeError("kaputt")

    async def erfolgreich(name, *args, **kwargs):
        beendet.append(name)

    monkeypatch.setattr(
        "app.services.sms_dispatch_service.dispatch_einsatzinfo", sms_fehler,
    )
    monkeypatch.setattr(
        "app.services.incident_notify._send_incident_push",
        lambda *args, **kwargs: erfolgreich("push"),
    )
    monkeypatch.setattr(
        "app.services.teams_alarm_service.post_incident_card",
        lambda *args, **kwargs: erfolgreich("teams"),
    )

    with caplog.at_level(logging.ERROR, logger="einsatzleiter.incident_notify"):
        await notify_incident_created(
            Mock(), _incident(), org_id=1, base_url="https://example.test",
            background_tasks=None,
        )

    assert set(beendet) == {"push", "teams"}
    assert "Einsatzinfo-SMS fehlgeschlagen (Einsatz 42)" in caplog.text


@pytest.mark.asyncio
async def test_new_incident_uses_alarm_channel(monkeypatch):
    sent = {}

    def fake_notify_org(*args, **kwargs):
        sent.update(kwargs)
        return 1

    monkeypatch.setattr(push_service, "notify_org", fake_notify_org)
    monkeypatch.setattr(
        "app.services.sms_dispatch_service.dispatch_einsatzinfo",
        Mock(),
    )
    monkeypatch.setattr(
        "app.services.teams_alarm_service.post_incident_card",
        Mock(),
    )
    incident = _incident()

    await notify_incident_created(
        Mock(), incident, org_id=1, background_tasks=None,
    )

    assert sent["channel_id"] == "einsatz_alarm"


def test_notify_user_does_not_use_alarm_channel(monkeypatch):
    forwarded = {}

    monkeypatch.setattr(push_service, "_push_cfg", lambda db: {"enabled": False})

    def fake_notify_fcm_users(db, user_ids, title, body, url, cfg, channel_id=None):
        forwarded["channel_id"] = channel_id
        return 1

    monkeypatch.setattr(push_service, "_notify_fcm_users", fake_notify_fcm_users)

    count = push_service.notify_user(Mock(), 7, "Auftrag", "Neue Nachricht")

    assert count == 1
    assert forwarded["channel_id"] is None


def _fake_messaging(monkeypatch, send):
    firebase_admin = ModuleType("firebase_admin")
    messaging = ModuleType("firebase_admin.messaging")
    messaging.Notification = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.AndroidNotification = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.AndroidConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.Message = lambda **kwargs: SimpleNamespace(**kwargs)
    messaging.send = send
    for name in (
        "UnregisteredError", "SenderIdMismatchError", "QuotaExceededError",
        "ThirdPartyAuthError",
    ):
        setattr(messaging, name, type(name, (Exception,), {}))
    firebase_admin.messaging = messaging
    monkeypatch.setitem(sys.modules, "firebase_admin", firebase_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.messaging", messaging)
    monkeypatch.setattr(push_service, "_get_fcm_app", lambda _cfg=None: object())


def _fcm_rows(suffix: str):
    db = SessionLocal()
    set_tenant_context(db, None)
    user = User(username=f"fcm-dual-{suffix}", display_name="FCM Dual", org_id=1, active=True)
    db.add(user)
    db.flush()
    token = FcmToken(user_id=user.id, token=f"fcm-dual-token-{suffix}")
    push_log = PushLog(title="Alarm", body="Test", source="einsatz_alarm", org_id=1)
    db.add_all([token, push_log])
    db.commit()
    return db, user, token, push_log


def test_alarm_sends_wake_and_display_with_one_delivery(monkeypatch):
    sent = []
    _fake_messaging(monkeypatch, sent.append)
    db, user, _token, push_log = _fcm_rows("messages")
    try:
        count = push_service._notify_fcm_users(
            db, {user.id}, "Alarm", "Test", "/einsatz/42", {},
            channel_id="einsatz_alarm", push_log_id=push_log.id,
        )
        db.commit()

        assert count == 1
        assert len(sent) == 2
        wake = next(message for message in sent if not hasattr(message, "notification"))
        display = next(message for message in sent if hasattr(message, "notification"))
        assert wake.data["silent"] == "1"
        assert "silent" not in display.data
        assert wake.data["delivery_id"] == display.data["delivery_id"]
        assert display.android.notification.channel_id == "einsatz_alarm"
        deliveries = db.query(FcmDeliveryLog).filter_by(push_log_id=push_log.id).all()
        assert len(deliveries) == 1
        assert deliveries[0].success is True
    finally:
        db.close()


def test_delivery_is_committed_before_first_fcm_send(monkeypatch):
    observed_delivery_ids = []

    def assert_committed(message):
        delivery_id = int(message.data["delivery_id"])
        other_db = SessionLocal()
        try:
            assert other_db.get(FcmDeliveryLog, delivery_id) is not None
            observed_delivery_ids.append(delivery_id)
        finally:
            other_db.close()

    _fake_messaging(monkeypatch, assert_committed)
    db, user, _token, push_log = _fcm_rows("commit")
    try:
        push_service._notify_fcm_users(
            db, {user.id}, "Alarm", "Test", None, {},
            channel_id="einsatz_alarm", push_log_id=push_log.id,
        )
        assert len(observed_delivery_ids) == 2
        assert len(set(observed_delivery_ids)) == 1
    finally:
        db.close()


def test_notify_vehicle_committet_die_transaktion_des_aufrufers_nicht(monkeypatch):
    """notify_vehicle laeuft mitten in incident_service-Transaktionen.

    Ein Commit der Delivery-Zeilen wuerde dort halbfertige Auftrags-/Meldungs-
    Zuweisungen festschreiben (Regression aus dem Zwei-Nachrichten-Umbau).
    """
    from app.core.security import hash_api_key
    from app.models.master import VehicleMaster
    from app.models.user import DeviceToken

    _fake_messaging(monkeypatch, lambda _message: None)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username="fcm-veh", display_name="FCM Fahrzeug", org_id=1, active=True)
        vehicle = VehicleMaster(dept_id=1, code="TLF-T", name="TLF Test", type="Test")
        db.add_all([user, vehicle])
        db.flush()
        db.add_all([
            FcmToken(user_id=user.id, token="fcm-vehicle-token"),
            DeviceToken(
                user_id=user.id,
                token_hash=hash_api_key("fcm-vehicle-device"),
                label="Fahrzeug-Tablet",
                vehicle_master_id=vehicle.id,
            ),
        ])
        db.commit()

        # Ab hier simuliert die noch offene Aenderung den Aufrufer-Kontext.
        user.display_name = "Noch nicht committet"
        db.flush()
        commit_spy = Mock(side_effect=AssertionError("notify_vehicle darf nicht committen"))
        monkeypatch.setattr(db, "commit", commit_spy)

        push_service.notify_vehicle(db, vehicle.id, "Auftrag", "Test")

        commit_spy.assert_not_called()
    finally:
        db.rollback()
        db.close()
