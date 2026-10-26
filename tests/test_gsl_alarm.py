"""Tests fuer den Grossschadenslage-Sonderalarm (SMS + Teams) bei Ausrufung einer neuen
Lage — unabhaengig von der stichwortbezogenen Einsatzinfo. Muster: test_sms_einsatzinfo.py
(SMS-Seite) und test_teams_alarm.py (Teams-Seite)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests-mindestens-32-zeichen!")
os.environ.setdefault("DEBUG", "true")
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
         patch("app.services.sms_dispatch_service.send_bulk_detailed", new_callable=AsyncMock) as mock_send:
        await svc.dispatch_gsl_alarm(org_id=1, lage_name="Lage Test", is_exercise=False)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_gsl_alarm_skips_no_gateway():
    from app.services import sms_dispatch_service as svc

    with patch("app.routers.ws.is_sms_gateway_connected", return_value=False), \
         patch("app.services.sms_dispatch_service.send_bulk_detailed", new_callable=AsyncMock) as mock_send, \
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
         patch("app.services.sms_dispatch_service.send_bulk_detailed", new_callable=AsyncMock) as mock_send:
        await svc.dispatch_gsl_alarm(org_id=1, lage_name="Lage Test", is_exercise=True)
        mock_send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("custom", "exercise", "expected"), [
    (None, False, "Geraetehaus"),
    ("Achtung: {lage} - alle einruecken!", True, "[UEBUNG] Achtung: Waldbrand Bregenzerwald - alle einruecken!"),
])
async def test_gsl_alarm_persists_and_confirms_native_sms(monkeypatch, sms_outbox_state, custom, exercise, expected):
    from app.models.master import Member, OrgSettings
    from app.models.sms import SmsLog
    from app.services import sms_dispatch_service as svc
    from app.services.sms_outbox import database
    from tests.gateway_helpers import CaptureSocket, bind, cleanup
    c, socket = bind(socket=CaptureSocket(reply=True))
    with database() as db:
        cfg = db.query(OrgSettings).filter_by(org_id=1).first()
        if cfg is None:
            cfg = OrgSettings(org_id=1)
            db.add(cfg)
        previous = bool(cfg.gsl_alarm_enabled), cfg.gsl_alarm_text
        cfg.gsl_alarm_enabled, cfg.gsl_alarm_text = True, custom
        member = Member(org_id=1, firstname="SMS", lastname="Test", phone="+4366099999", active=True)
        db.add(member)
        db.commit()
        db.refresh(member)
        db.expunge(member)
    monkeypatch.setattr(svc, "collect_einsatzinfo_recipients", lambda *args, **kwargs: {member.phone: member})
    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *args, **kwargs: True)
    try:
        await svc.dispatch_gsl_alarm(1, "Waldbrand Bregenzerwald", exercise)
        assert len(socket.sent) == 1 and expected in socket.sent[0]["text"]
        with database() as db:
            log = db.query(SmsLog).filter_by(org_id=1, source="gsl_alarm").order_by(SmsLog.id.desc()).first()
            assert log.success_count == 1 and log.completed_at is not None
    finally:
        cleanup(c)
        with database() as db:
            cfg = db.query(OrgSettings).filter_by(org_id=1).one()
            cfg.gsl_alarm_enabled, cfg.gsl_alarm_text = previous
            db.commit()


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
    lage.planspiel_run_id = None
    bg = MagicMock()

    await notify_gsl_created(lage, base_url="https://example.com", background_tasks=bg)

    assert bg.add_task.call_count == 2


@pytest.mark.asyncio
async def test_notify_gsl_created_awaits_directly_without_background_tasks():
    from app.services.gsl_notify import notify_gsl_created

    lage = MagicMock(id=5, org_id=1, is_exercise=False)
    lage.name = "Lage X"
    lage.planspiel_run_id = None

    with patch("app.services.sms_dispatch_service.dispatch_gsl_alarm",
               new_callable=AsyncMock) as mock_sms, \
         patch("app.services.teams_alarm_service.post_gsl_alarm_card",
               new_callable=AsyncMock) as mock_teams:
        await notify_gsl_created(lage, base_url="https://example.com", background_tasks=None)

    mock_sms.assert_awaited_once_with(1, "Lage X", False, None, planspiel_run_id=None)
    mock_teams.assert_awaited_once_with(
        1, 5, "Lage X", False, base_url="https://example.com", planspiel_run_id=None,
    )
