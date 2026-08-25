"""Tests fuer den exklusiven FCM-Alarm-Channel bei neuen Einsaetzen."""
import asyncio
import logging
from time import perf_counter
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks

from app.models.incident import Incident
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
