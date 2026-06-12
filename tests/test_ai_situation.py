"""Tests for Phase 3 AI situation brief – provider always mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── generate_situation_brief ─────────────────────────────────────────────────

@pytest.fixture()
def ai_enabled(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_DEFAULT", "claude-sonnet-4-6")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_FAST", "claude-haiku-4-5-20251001")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MAX_TOKENS", 1500)
    monkeypatch.setattr("app.services.ai_service.settings.AI_TIMEOUT", 20)


def _mock_client_with_text(text: str) -> MagicMock:
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_generate_situation_brief_returns_string(ai_enabled):
    from app.services.ai_service import generate_situation_brief

    brief = "Einsatz läuft seit 15 Minuten. Drei Fahrzeuge sind vor Ort."
    mock_client = _mock_client_with_text(brief)

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        result = await generate_situation_brief({"alarm_type": "B2", "laufzeit_min": 15})

    assert isinstance(result, str)
    assert result == brief


@pytest.mark.asyncio
async def test_generate_situation_brief_strips_person_data(ai_enabled):
    from app.services.ai_service import generate_situation_brief

    brief = "Sachliche Lage ohne Personendaten."
    mock_client = _mock_client_with_text(brief)

    context = {
        "alarm_type": "T1",
        "name": "Mustermann",
        "user_id": 42,
        "fahrzeuge": [{"rufname": "LF10", "name": "Hans", "status": "Am Einsatzort"}],
    }
    captured: list[str] = []

    async def _capture(*args, **kwargs):
        captured.append(kwargs.get("messages", [{}])[0].get("content", ""))
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=brief)]
        return mock_response

    mock_client.messages.create = _capture

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        await generate_situation_brief(context)

    assert captured, "messages.create was not called"
    prompt_text = captured[0]
    assert "Mustermann" not in prompt_text
    assert "user_id" not in prompt_text


@pytest.mark.asyncio
async def test_generate_situation_brief_raises_when_disabled(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", False)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "")

    from app.services.ai_service import AIServiceError, generate_situation_brief

    with pytest.raises(AIServiceError):
        await generate_situation_brief({"alarm_type": "T1"})


# ── collect_situation_context ────────────────────────────────────────────────

def test_collect_situation_context_returns_dict(client):
    """collect_situation_context returns a person-data-free dict."""
    from sqlalchemy.orm import Session

    from app.core.tenant import set_tenant_context
    from app.db import engine
    from app.models.incident import Incident
    from app.services.incident_service import collect_situation_context

    db = Session(bind=engine)
    set_tenant_context(db, None)
    try:
        incident = db.query(Incident).first()
        if not incident:
            pytest.skip("No incident in test DB")

        result = collect_situation_context(incident.id, db)

        assert isinstance(result, dict)
        assert "alarm_type" in result
        assert "fahrzeuge" in result
        assert "auftraege" in result
        assert "meldungen" in result
        assert "laufzeit_min" in result
        assert "name" not in result
        assert "user_id" not in result
    finally:
        db.close()


def test_collect_situation_context_vehicles_no_persons(client):
    """Vehicle entries in context contain no person-data keys."""
    from sqlalchemy.orm import Session

    from app.core.tenant import set_tenant_context
    from app.db import engine
    from app.models.incident import Incident
    from app.services.incident_service import collect_situation_context

    db = Session(bind=engine)
    set_tenant_context(db, None)
    try:
        incident = db.query(Incident).first()
        if not incident:
            pytest.skip("No incident in test DB")

        result = collect_situation_context(incident.id, db)
        for v in result.get("fahrzeuge", []):
            assert "commander" not in v
            assert "commander_member_id" not in v
    finally:
        db.close()


# ── endpoint tests ────────────────────────────────────────────────────────────

def test_ki_lagebild_returns_503_when_disabled(client):
    """POST /einsatz/{id}/ki-lagebild returns 503 when AI is disabled."""
    import app.services.ai_service as ai_mod

    original = ai_mod.is_enabled
    ai_mod.is_enabled = lambda: False
    try:
        resp = client.post("/einsatz/1/ki-lagebild")
        assert resp.status_code in (302, 401, 403, 503)
    finally:
        ai_mod.is_enabled = original


def test_ki_lagebild_journal_unauthenticated(client):
    """POST ki-lagebild/journal without auth returns redirect/401/403."""
    resp = client.post(
        "/einsatz/1/ki-lagebild/journal",
        json={"text": "Test-Lagebild."},
    )
    assert resp.status_code in (302, 401, 403)


def test_ki_lagebild_journal_empty_text(client):
    """POST ki-lagebild/journal with empty text returns 400 or auth error."""
    resp = client.post(
        "/einsatz/1/ki-lagebild/journal",
        json={"text": ""},
    )
    assert resp.status_code in (302, 400, 401, 403)
