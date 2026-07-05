"""Tests fuer den Grossschadenslage-Sonderalarm (SMS + Teams) bei Ausrufung einer neuen
Lage — unabhaengig von der stichwortbezogenen Einsatzinfo. Muster: test_sms_einsatzinfo.py
(SMS-Seite) und test_teams_alarm.py (Teams-Seite)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests-mindestens-32-zeichen!")
os.environ.setdefault("DEBUG", "true")
os.environ["DATABASE_URL"] = "sqlite:///./test.db"


# ── dispatch_gsl_alarm (SMS) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_gsl_alarm_skips_when_disabled():
    from app.services import sms_dispatch_service as svc

    org_settings = MagicMock()
    org_settings.gsl_alarm_enabled = False

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_dispatch_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_dispatch_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.send_bulk", new_callable=AsyncMock) as mock_send:
        await svc.dispatch_gsl_alarm(org_id=1, lage_name="Lage Test", is_exercise=False)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_gsl_alarm_skips_no_gateway():
    from app.services import sms_dispatch_service as svc

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=False), \
         patch("app.services.sms_dispatch_service.send_bulk", new_callable=AsyncMock) as mock_send, \
         patch("app.services.sms_dispatch_service.SessionLocal") as mock_session:
        await svc.dispatch_gsl_alarm(org_id=1, lage_name="Lage Test", is_exercise=False)
        mock_send.assert_not_called()
        mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_gsl_alarm_skips_exercise_when_not_configured():
    from app.services import sms_dispatch_service as svc

    org_settings = MagicMock()
    org_settings.gsl_alarm_enabled = True
    org_settings.einsatzinfo_sms_send_exercise = False

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_dispatch_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_dispatch_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.send_bulk", new_callable=AsyncMock) as mock_send:
        await svc.dispatch_gsl_alarm(org_id=1, lage_name="Lage Test", is_exercise=True)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_gsl_alarm_sends_default_text_and_logs():
    from app.services import sms_dispatch_service as svc

    org_settings = MagicMock()
    org_settings.gsl_alarm_enabled = True
    org_settings.gsl_alarm_text = None

    member = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings

    sent_texts: list[str] = []

    async def fake_send_bulk(org_id, jobs):
        for _, text in jobs:
            sent_texts.append(text)
        return len(jobs), len(jobs)

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_dispatch_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_dispatch_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.collect_einsatzinfo_recipients",
               return_value={"+4366099999": member}), \
         patch("app.services.sms_dispatch_service.send_bulk", side_effect=fake_send_bulk), \
         patch("app.services.sms_dispatch_service.write_audit"), \
         patch("app.services.sms_dispatch_service.SmsLog"):
        await svc.dispatch_gsl_alarm(org_id=1, lage_name="Lage Test", is_exercise=False)

    assert len(sent_texts) == 1
    assert "Geraetehaus" in sent_texts[0]


@pytest.mark.asyncio
async def test_dispatch_gsl_alarm_custom_text_with_lage_placeholder():
    from app.services import sms_dispatch_service as svc

    org_settings = MagicMock()
    org_settings.gsl_alarm_enabled = True
    org_settings.gsl_alarm_text = "Achtung: {lage} - alle einruecken!"

    member = MagicMock()
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = org_settings

    sent_texts: list[str] = []

    async def fake_send_bulk(org_id, jobs):
        for _, text in jobs:
            sent_texts.append(text)
        return len(jobs), len(jobs)

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=True), \
         patch("app.services.sms_dispatch_service.SessionLocal", return_value=mock_db), \
         patch("app.services.sms_dispatch_service.set_tenant_context"), \
         patch("app.services.sms_dispatch_service.collect_einsatzinfo_recipients",
               return_value={"+4366099999": member}), \
         patch("app.services.sms_dispatch_service.send_bulk", side_effect=fake_send_bulk), \
         patch("app.services.sms_dispatch_service.write_audit"), \
         patch("app.services.sms_dispatch_service.SmsLog"):
        await svc.dispatch_gsl_alarm(org_id=1, lage_name="Waldbrand Bregenzerwald", is_exercise=True)

    assert sent_texts == ["[UEBUNG] Achtung: Waldbrand Bregenzerwald - alle einruecken!"]


# ── post_gsl_alarm_card (Teams) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_gsl_alarm_card_skips_when_teams_disabled():
    from app.services import teams_alarm_service as svc

    cfg = MagicMock(enabled=False)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = cfg

    with patch("app.services.teams_alarm_service.SessionLocal", return_value=mock_db), \
         patch("app.services.teams_alarm_service._post_payload", new_callable=AsyncMock) as mock_post:
        await svc.post_gsl_alarm_card(1, 99, "Lage Test", False, base_url="https://example.com")
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_post_gsl_alarm_card_skips_when_gsl_alarm_disabled():
    from app.services import teams_alarm_service as svc

    cfg = MagicMock(enabled=True, send_exercise=False, webhook_url_alarm="https://outlook.office.com/x")
    org_settings = MagicMock(gsl_alarm_enabled=False)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.side_effect = [cfg, org_settings]

    with patch("app.services.teams_alarm_service.SessionLocal", return_value=mock_db), \
         patch("app.services.teams_alarm_service._post_payload", new_callable=AsyncMock) as mock_post:
        await svc.post_gsl_alarm_card(1, 99, "Lage Test", False, base_url="https://example.com")
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_post_gsl_alarm_card_skips_exercise_when_not_configured():
    from app.services import teams_alarm_service as svc

    cfg = MagicMock(enabled=True, send_exercise=False)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = cfg

    with patch("app.services.teams_alarm_service.SessionLocal", return_value=mock_db), \
         patch("app.services.teams_alarm_service._post_payload", new_callable=AsyncMock) as mock_post:
        await svc.post_gsl_alarm_card(1, 99, "Lage Test", True, base_url="https://example.com")
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_post_gsl_alarm_card_posts_payload_with_lage_link():
    from app.services import teams_alarm_service as svc

    cfg = MagicMock(enabled=True, send_exercise=False,
                     webhook_url_alarm="https://outlook.office.com/webhook/alarm")
    org_settings = MagicMock(gsl_alarm_enabled=True, gsl_alarm_text=None)
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.side_effect = [cfg, org_settings]

    captured = {}

    async def fake_post(webhook_url, payload, *, log_label):
        captured["webhook_url"] = webhook_url
        captured["payload"] = payload
        return True

    with patch("app.services.teams_alarm_service.SessionLocal", return_value=mock_db), \
         patch("app.services.teams_alarm_service._post_payload", side_effect=fake_post):
        await svc.post_gsl_alarm_card(1, 99, "Lage Test", False, base_url="https://example.com")

    assert captured["webhook_url"] == "https://outlook.office.com/webhook/alarm"
    content = captured["payload"]["attachments"][0]["content"]
    actions = content["actions"]
    assert actions[0]["url"] == "https://example.com/lage/99"


# ── notify_gsl_created (Orchestrierung) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_gsl_created_uses_background_tasks_when_given():
    from app.services.gsl_notify import notify_gsl_created

    lage = MagicMock(id=5, org_id=1, is_exercise=False)
    lage.name = "Lage X"
    bg = MagicMock()

    await notify_gsl_created(lage, base_url="https://example.com", background_tasks=bg)

    assert bg.add_task.call_count == 2


@pytest.mark.asyncio
async def test_notify_gsl_created_awaits_directly_without_background_tasks():
    from app.services.gsl_notify import notify_gsl_created

    lage = MagicMock(id=5, org_id=1, is_exercise=False)
    lage.name = "Lage X"

    with patch("app.services.sms_dispatch_service.dispatch_gsl_alarm",
               new_callable=AsyncMock) as mock_sms, \
         patch("app.services.teams_alarm_service.post_gsl_alarm_card",
               new_callable=AsyncMock) as mock_teams:
        await notify_gsl_created(lage, base_url="https://example.com", background_tasks=None)

    mock_sms.assert_awaited_once_with(1, "Lage X", False, None)
    mock_teams.assert_awaited_once_with(1, 5, "Lage X", False, base_url="https://example.com")
