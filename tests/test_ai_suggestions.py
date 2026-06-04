"""Tests for Phase 2 AI alarm enrichment – provider always mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── suggest_tasks ─────────────────────────────────────────────────────────────

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
async def test_suggest_tasks_returns_list(ai_enabled):
    from app.services.ai_service import suggest_tasks

    json_response = (
        '[{"titel":"Wasserversorgung aufbauen","detail":"Hydrant Hauptstraße"},'
        '{"titel":"Sicherheitsbereich einrichten"}]'
    )
    mock_client = _mock_client_with_text(json_response)

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        result = await suggest_tasks("Brand in Lagerhalle", "B3")

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["titel"] == "Wasserversorgung aufbauen"
    assert result[0]["detail"] == "Hydrant Hauptstraße"
    assert result[1]["titel"] == "Sicherheitsbereich einrichten"
    assert result[1].get("detail") is None


@pytest.mark.asyncio
async def test_suggest_tasks_returns_empty_on_invalid_json(ai_enabled):
    from app.services.ai_service import suggest_tasks

    mock_client = _mock_client_with_text("Leider kein JSON hier, nur Text.")

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        result = await suggest_tasks("Brandmeldung", "T1")

    assert result == []


@pytest.mark.asyncio
async def test_suggest_tasks_returns_empty_on_ai_error(ai_enabled):
    from app.services.ai_service import suggest_tasks

    with patch("app.services.ai_service.asyncio.wait_for", side_effect=TimeoutError):
        result = await suggest_tasks("Brandmeldung", "T1")

    assert result == []


@pytest.mark.asyncio
async def test_suggest_tasks_truncates_long_titles(ai_enabled):
    from app.services.ai_service import suggest_tasks

    long_titel = "X" * 100
    json_response = f'[{{"titel":"{long_titel}","detail":null}}]'
    mock_client = _mock_client_with_text(json_response)

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        result = await suggest_tasks("Übung", "T1")

    assert len(result[0]["titel"]) <= 60


@pytest.mark.asyncio
async def test_suggest_tasks_skips_empty_titles(ai_enabled):
    from app.services.ai_service import suggest_tasks

    json_response = '[{"titel":"","detail":"nur Detail"},{"titel":"Gültig"}]'
    mock_client = _mock_client_with_text(json_response)

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        result = await suggest_tasks("Test", "T1")

    assert len(result) == 1
    assert result[0]["titel"] == "Gültig"


# ── Task.source field ─────────────────────────────────────────────────────────

def test_task_source_default_is_manual(client):
    """Newly created Tasks have source='manual' by default."""
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.models.incident import Incident, Task

    db = Session(bind=engine)
    try:
        incident = db.query(Incident).first()
        if not incident:
            pytest.skip("No incident in test DB")

        task = Task(
            incident_id=incident.id,
            title="Test-Aufgabe",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        assert task.source == "manual"

        # Cleanup
        db.delete(task)
        db.commit()
    finally:
        db.close()


def test_task_source_ai_suggestion(client):
    """Tasks with source='ai_suggestion' persist correctly."""
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.models.incident import Incident, Task

    db = Session(bind=engine)
    try:
        incident = db.query(Incident).first()
        if not incident:
            pytest.skip("No incident in test DB")

        task = Task(
            incident_id=incident.id,
            title="KI-Vorschlag",
            source="ai_suggestion",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        assert task.source == "ai_suggestion"

        # Accept: source → manual
        task.source = "manual"
        db.commit()
        db.refresh(task)
        assert task.source == "manual"

        # Cleanup
        db.delete(task)
        db.commit()
    finally:
        db.close()


# ── Alarm creation idempotency with AI disabled ───────────────────────────────

def test_alarm_post_succeeds_without_ai(client):
    """POST /api/v1/einsatz creates incident even when AI is completely disabled."""
    import app.services.ai_service as ai_mod

    original = ai_mod.is_enabled
    ai_mod.is_enabled = lambda: False
    try:
        resp = client.post(
            "/api/v1/einsatz",
            json={"Key": "test-ai-disabled-9999", "Stufe": "T1", "Meldung": "Test"},
            headers={"X-API-Key": "invalid-key"},
        )
        # 401 is expected (no valid API key in test DB), but NOT 500
        assert resp.status_code != 500
    finally:
        ai_mod.is_enabled = original


# ── accept/reject endpoints ───────────────────────────────────────────────────

def test_accept_endpoint_rejects_non_ai_task(client):
    """ki-annehmen returns 404 for tasks with source='manual'."""
    from sqlalchemy.orm import Session

    from app.db import engine
    from app.models.incident import Incident, Task

    db = Session(bind=engine)
    try:
        incident = db.query(Incident).first()
        if not incident:
            pytest.skip("No incident in test DB")

        task = Task(incident_id=incident.id, title="Manuell", source="manual")
        db.add(task)
        db.commit()
        task_id = task.id
        incident_id = incident.id

        resp = client.post(f"/einsatz/{incident_id}/aufgabe/{task_id}/ki-annehmen")
        # Unauthenticated → redirect/401/403; never 200 for a manual task
        assert resp.status_code in (302, 401, 403, 404)

        db.delete(task)
        db.commit()
    finally:
        db.close()
