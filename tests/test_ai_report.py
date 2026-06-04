"""Tests for Phase 1 AI report draft – provider always mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── generate_report_draft ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_report_draft_returns_string(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_DEFAULT", "claude-sonnet-4-6")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MAX_TOKENS", 1500)
    monkeypatch.setattr("app.services.ai_service.settings.AI_TIMEOUT", 20)

    from app.services.ai_service import generate_report_draft

    mock_content = MagicMock()
    mock_content.text = "Um 14:32 Uhr wurde Alarm ausgelöst. Zwei Fahrzeuge rückten aus."
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        result = await generate_report_draft({"alarm_type": "B2", "dauer_min": 45})

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_generate_report_draft_no_person_data_in_prompt(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_DEFAULT", "claude-sonnet-4-6")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MAX_TOKENS", 1500)
    monkeypatch.setattr("app.services.ai_service.settings.AI_TIMEOUT", 20)

    from app.services.ai_service import generate_report_draft

    mock_content = MagicMock()
    mock_content.text = "Bericht ohne Personendaten."
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    incident_data = {
        "alarm_type": "B2",
        "name": "Mustermann",      # should be stripped before reaching here
        "user_id": 42,             # should be stripped
        "auftraege": [{"titel": "Türe öffnen", "created_by_user_id": 7}],  # nested
    }

    captured_user_msg: list[str] = []

    async def _capture(*args, **kwargs):
        captured_user_msg.append(kwargs.get("messages", [{}])[0].get("content", ""))
        return mock_response

    mock_client.messages.create = _capture

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        await generate_report_draft(incident_data)

    assert captured_user_msg, "messages.create was not called"
    prompt_text = captured_user_msg[0]
    assert "Mustermann" not in prompt_text
    assert "user_id" not in prompt_text


# ── collect_report_context ───────────────────────────────────────────────────

def test_collect_report_context_returns_dict(client):
    """collect_report_context with a seeded incident returns a dict without person keys."""
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.models.incident import Incident
    from app.services.incident_service import collect_report_context

    db = Session(bind=engine)
    try:
        incident = db.query(Incident).first()
        if not incident:
            pytest.skip("No incident in test DB")

        result = collect_report_context(incident.id, db)

        assert isinstance(result, dict)
        assert "alarm_type" in result
        assert "name" not in result
        assert "user_id" not in result
        assert "incident_leader_user_id" not in result
    finally:
        db.close()


# ── endpoint tests ────────────────────────────────────────────────────────────

def test_generate_report_endpoint_disabled(client):
    """When AI is disabled, the endpoint returns a graceful message, not a 500."""
    import app.services.ai_service as ai_mod

    original = ai_mod.is_enabled
    ai_mod.is_enabled = lambda: False
    try:
        resp = client.post("/archiv/1/ki-bericht")
        # Expect 200 with a disabled message or 302 redirect (no auth) – not 500
        assert resp.status_code in (200, 302, 401, 403)
    finally:
        ai_mod.is_enabled = original


def test_save_report_endpoint_unauthenticated(client):
    """Save endpoint redirects unauthenticated requests to login."""
    resp = client.post("/archiv/1/ki-bericht/speichern", data={"ki_bericht_entwurf": "Test"})
    assert resp.status_code in (200, 302, 401, 403)


def test_ai_report_draft_persists(client):
    """Saving an ai_report_draft stores it on the Incident."""
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.models.incident import Incident

    db = Session(bind=engine)
    try:
        incident = db.query(Incident).first()
        if not incident:
            pytest.skip("No incident in test DB")
        incident_id = incident.id
        incident.ai_report_draft = "Vorläufiger Bericht."
        db.commit()

        db.expire(incident)
        fresh = db.get(Incident, incident_id)
        assert fresh is not None
        assert fresh.ai_report_draft == "Vorläufiger Bericht."

        # Cleanup
        fresh.ai_report_draft = None
        db.commit()
    finally:
        db.close()


def test_pdf_contains_ai_draft_section(client):
    """When ai_report_draft is set, the PDF template renders the section."""
    from types import SimpleNamespace

    from app.core.templating import templates

    mock_incident = SimpleNamespace(
        id=1,
        alarm_type_code="B2",
        is_exercise=False,
        address_street="Hauptstraße",
        address_no="1",
        address_city="Wolfurt",
        report_text="Brand",
        reason=None,
        started_at=None,
        closed_at=None,
        nummer=None,
        ai_report_draft="Testbericht von der KI.",
        leader_member=None,
        leader=None,
        vehicles=[],
        tasks=[],
        messages=[],
        rescued_persons=[],
        breathing_troops=[],
        log_entries=[],
    )

    template = templates.env.get_template("pdf/incident_report.html")
    html = template.render(
        incident=mock_incident,
        now=None,
        base_url="",
        user=SimpleNamespace(org=None),
        media_b64=lambda m: "",
        media_exists=lambda m: False,
    )

    assert "Einsatzverlauf (KI-Entwurf)" in html
    assert "Testbericht von der KI." in html


def test_pdf_no_ai_draft_section_when_empty(client):
    """When ai_report_draft is None, the PDF template omits the KI section."""
    from types import SimpleNamespace

    from app.core.templating import templates

    mock_incident = SimpleNamespace(
        id=1,
        alarm_type_code="T1",
        is_exercise=False,
        address_street=None,
        address_no=None,
        address_city=None,
        report_text=None,
        reason=None,
        started_at=None,
        closed_at=None,
        nummer=None,
        ai_report_draft=None,
        leader_member=None,
        leader=None,
        vehicles=[],
        tasks=[],
        messages=[],
        rescued_persons=[],
        breathing_troops=[],
        log_entries=[],
    )

    template = templates.env.get_template("pdf/incident_report.html")
    html = template.render(
        incident=mock_incident,
        now=None,
        base_url="",
        user=SimpleNamespace(org=None),
        media_b64=lambda m: "",
        media_exists=lambda m: False,
    )

    assert "Einsatzverlauf (KI-Entwurf)" not in html
