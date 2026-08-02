"""Tests fuer den exklusiven FCM-Alarm-Channel bei neuen Einsaetzen."""
from unittest.mock import Mock

import pytest

from app.models.incident import Incident
from app.services.incident_notify import notify_incident_created
from app.services import push_service


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
    incident = Incident(
        id=42,
        alarm_type_code="B2",
        address_city="Testort",
        is_exercise=True,
    )

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
