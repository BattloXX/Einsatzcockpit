"""Tests für die Teams-Alarmierung: Karteninhalt (teams_card.py) und Dispatch-Entscheidung
Bot vs. Webhook-Fallback (teams_alarm_service.py) — Webhook-Versand mit gemocktem httpx,
kein echter Netzwerkzugriff."""
from datetime import UTC, datetime

import httpx
import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.master import AlarmType, FireDept
from app.models.teams_bot import TeamsAlarmConfig, TeamsChannelBinding
from app.services import teams_alarm_service
from app.services.teams_card import build_incident_message_card
from tests.conftest import TestingSession

ORG_ID = 1


def _session():
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _incident(**overrides) -> Incident:
    defaults = dict(
        primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
        reason="Verkehrsunfall", address_street="Bundesstraße", address_no="1",
        address_city="Wolfurt", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
        lat=47.47, lng=9.73, alarm_token="tok_abc123",
    )
    defaults.update(overrides)
    return Incident(**defaults)


def _cfg(**overrides) -> TeamsAlarmConfig:
    defaults = dict(
        org_id=ORG_ID, enabled=True, send_exercise=False,
        include_map=True, include_gmaps_link=True, include_qr_link=True,
        include_board_link=True,
    )
    defaults.update(overrides)
    return TeamsAlarmConfig(**defaults)


# ── build_incident_message_card ──────────────────────────────────────────────
# Adaptive Card (in "attachments" gewrappt) — siehe teams_card.py-Docstring: der
# Workflows-Webhook (Nachfolger der alten Office-365-Connectors) verwirft Bild/Buttons
# im alten MessageCard-Schema, daher Umstieg auf Adaptive Card.

def _adaptive_content(card: dict) -> dict:
    return card["attachments"][0]["content"]


def _body_texts(card: dict) -> str:
    return "\n".join(b.get("text", "") for b in _adaptive_content(card)["body"])


def _images(card: dict) -> list[dict]:
    return [b for b in _adaptive_content(card)["body"] if b.get("type") == "Image"]


def test_build_incident_message_card_includes_all_bausteine_by_default():
    incident = _incident()
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    assert card["type"] == "message"
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    text = _body_texts(card)
    assert "Bundesstraße 1" in text
    assert "Verkehrsunfall" in text
    images = _images(card)
    assert images[0]["url"] == "https://example.com/api/v1/teams/map/tok_abc123.png"

    action_titles = [a["title"] for a in _adaptive_content(card)["actions"]]
    assert any("Google Maps" in t for t in action_titles)
    assert any("Alarmübersicht" in t for t in action_titles)


def test_build_incident_message_card_respects_include_toggles():
    incident = _incident()
    cfg = _cfg(include_map=False, include_gmaps_link=False, include_qr_link=False,
               include_board_link=False)
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    assert not _images(card)
    assert "actions" not in _adaptive_content(card)


def test_build_incident_message_card_includes_board_link():
    incident = _incident(id=185)
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    actions = _adaptive_content(card)["actions"]
    board_actions = [a for a in actions if "Einsatz-Board" in a["title"]]
    assert board_actions == [{
        "type": "Action.OpenUrl",
        "title": "🖥 Einsatz-Board öffnen",
        "url": "https://example.com/einsatz/185",
    }]


def test_build_incident_message_card_omits_board_link_when_disabled():
    incident = _incident(id=185)
    cfg = _cfg(include_board_link=False)
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    action_titles = [a["title"] for a in _adaptive_content(card).get("actions", [])]
    assert not any("Einsatz-Board" in t for t in action_titles)


def test_build_incident_message_card_marks_exercise():
    incident = _incident(is_exercise=True)
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")
    assert "[ÜBUNG]" in _body_texts(card)


def test_build_incident_message_card_no_map_without_coords():
    incident = _incident(lat=None, lng=None)
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")
    assert not _images(card)
    # Google-Maps-Button darf ohne Koordinaten nicht auftauchen
    action_titles = [a["title"] for a in _adaptive_content(card).get("actions", [])]
    assert not any("Google Maps" in t for t in action_titles)


# ── post_incident_card: Dispatch-Entscheidung ────────────────────────────────

def test_post_incident_card_noop_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook",
                         lambda *a, **kw: calls.append(1))

    db = _session()
    try:
        cfg = _cfg(enabled=False, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_skips_exercise_when_not_configured(monkeypatch):
    calls = []

    async def fake_webhook(*a, **kw):
        calls.append(1)
        return True
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, send_exercise=False, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        incident = _incident(is_exercise=True)
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_uses_webhook_when_no_bot_binding(monkeypatch):
    calls = []

    async def fake_webhook(webhook_url, incident, cfg, *, base_url, org):
        calls.append(webhook_url)
        return True
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/alarm")
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == ["https://outlook.office.com/webhook/alarm"]
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_prefers_bot_when_binding_exists(monkeypatch):
    bot_calls = []
    webhook_calls = []

    async def fake_bot(*a, **kw):
        bot_calls.append(1)

    async def fake_webhook(*a, **kw):
        webhook_calls.append(1)
        return True

    monkeypatch.setattr("app.services.teams_bot_service.post_incident_card_via_bot", fake_bot)
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, bot_enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/alarm")
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()
        db.add(TeamsChannelBinding(
            org_id=ORG_ID, target="alarm", service_url="https://smba.example/",
            conversation_id="19:abc@thread.tacv2", captured_at=datetime.now(UTC),
        ))
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert bot_calls == [1]
        assert webhook_calls == []  # Bot bevorzugt, Webhook nicht zusaetzlich aufgerufen
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_skips_alarm_type_with_teams_alarm_disabled(monkeypatch):
    calls = []

    async def fake_webhook(*a, **kw):
        calls.append(1)
        return True
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        # T4 ist bereits durch seed_data.py fuer ORG_ID angelegt — hier nur den Flag umschalten
        # statt eine zweite Zeile (org_id, code) anzulegen (UNIQUE-Constraint).
        alarm_type = db.query(AlarmType).filter(
            AlarmType.org_id == ORG_ID, AlarmType.code == "T4",
        ).first()
        alarm_type.teams_alarm_enabled = False
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_refreshes_stale_coords_before_building_card(monkeypatch):
    """Regression (2026-07-05): ein paralleler Background-Task (eigene DB-Session, z.B.
    Geocoding) kann lat/lng NACH dem Laden dieses `incident`-Objekts persistieren. Ohne
    db.refresh() sieht build_incident_message_card() hier noch die alten (None-)Werte aus
    der Identity-Map — Kartenbild/Google-Maps-Button würden fälschlich fehlen."""
    captured = {}

    async def fake_webhook(webhook_url, incident, cfg, *, base_url, org):
        captured["lat"] = incident.lat
        captured["lng"] = incident.lng
        return True
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        incident = _incident(lat=None, lng=None)
        db.add(incident)
        # Ein zweiter, parallel laufender Prozess (eigene Session, eigene DB-Verbindung)
        # muss die Zeile erst SEHEN können — dafuer braucht es einen echten Commit statt
        # nur flush(); darum wird unten explizit statt per rollback() aufgeraeumt.
        db.commit()

        # Simuliert einen zweiten, parallel laufenden Prozess (eigene Session), der
        # zwischenzeitlich die Koordinaten setzt — wie _geocode_incident() in api_v1.py.
        other = TestingSession()
        set_tenant_context(other, ORG_ID)
        other_incident = other.get(Incident, incident.id)
        other_incident.lat = 47.488847
        other_incident.lng = 9.741011
        other.commit()
        other.close()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert captured["lat"] == 47.488847
        assert captured["lng"] == 9.741011
    finally:
        db.rollback()
        db.query(Incident).filter(Incident.id == incident.id).delete()
        db.query(TeamsAlarmConfig).filter(TeamsAlarmConfig.org_id == ORG_ID).delete()
        db.commit()
        db.close()


def test_post_incident_card_noop_when_no_target_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook",
                         lambda *a, **kw: calls.append(1))

    db = _session()
    try:
        cfg = _cfg(enabled=True)  # keine Webhook-URLs gesetzt
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


# ── _post_via_webhook: echte HTTP-Ebene mit gemocktem httpx ──────────────────

def test_post_via_webhook_posts_message_card(monkeypatch):
    captured = {}

    class _MockAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)

    incident = _incident()
    cfg = _cfg()

    import asyncio
    ok = asyncio.run(teams_alarm_service._post_via_webhook(
        "https://outlook.office.com/webhook/x", incident, cfg,
        base_url="https://example.com", org=None,
    ))
    assert ok is True
    assert captured["url"] == "https://outlook.office.com/webhook/x"
    assert captured["json"]["type"] == "message"
    assert captured["json"]["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_post_via_webhook_rejects_non_https_url():
    incident = _incident()
    cfg = _cfg()

    import asyncio
    ok = asyncio.run(teams_alarm_service._post_via_webhook(
        "http://insecure.example/webhook", incident, cfg,
        base_url="https://example.com", org=None,
    ))
    assert ok is False
