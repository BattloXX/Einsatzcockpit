"""Unit tests for ai_service – provider always mocked, no network calls."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import ai_service
from app.services.ai_service import AIServiceError, _strip_persons, is_enabled

# ── is_enabled ───────────────────────────────────────────────────────────────

def test_is_enabled_false_when_flag_off(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", False)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")
    assert not is_enabled()


def test_is_enabled_false_when_no_key(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "")
    assert not is_enabled()


def test_is_enabled_true(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")
    assert is_enabled()


# ── _strip_persons ────────────────────────────────────────────────────────────

def test_strip_persons_removes_top_level_keys():
    data = {
        "incident_id": 1,
        "title": "Brandeinsatz",
        "name": "Max Mustermann",
        "user_id": 42,
        "address": "Hauptstraße 1",
    }
    result = _strip_persons(data)
    assert "name" not in result
    assert "user_id" not in result
    assert result["incident_id"] == 1
    assert result["title"] == "Brandeinsatz"
    assert result["address"] == "Hauptstraße 1"


def test_strip_persons_recursive_list():
    data = {
        "tasks": [
            {"title": "Türe öffnen", "created_by_user_id": 5, "status": "open"},
            {"title": "Sicherung", "user": {"email": "a@b.at"}, "status": "done"},
        ]
    }
    result = _strip_persons(data)
    assert "created_by_user_id" not in result["tasks"][0]
    assert "user" not in result["tasks"][1]
    assert result["tasks"][0]["title"] == "Türe öffnen"
    assert result["tasks"][1]["status"] == "done"


def test_strip_persons_nested_dict():
    data = {"vehicle": {"id": 3, "commander": {"id": 7, "name": "Franz"}, "callsign": "TLF-1"}}
    result = _strip_persons(data)
    assert "commander" not in result["vehicle"]
    assert result["vehicle"]["callsign"] == "TLF-1"
    assert result["vehicle"]["id"] == 3


def test_strip_persons_preserves_non_person_data():
    data = {"alarm_type": "B2", "reason": "Küchenbrand", "vehicles": ["TLF", "RLF"]}
    result = _strip_persons(data)
    assert result == data


def test_strip_persons_empty():
    assert _strip_persons({}) == {}


def test_strip_persons_does_not_mutate_input():
    data = {"name": "Test", "title": "Einsatz"}
    original = dict(data)
    _strip_persons(data)
    assert data == original


# ── complete ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def ai_enabled(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_DEFAULT", "claude-sonnet-4-6")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MODEL_FAST", "claude-haiku-4-5-20251001")
    monkeypatch.setattr("app.services.ai_service.settings.AI_MAX_TOKENS", 1500)
    monkeypatch.setattr("app.services.ai_service.settings.AI_TIMEOUT", 20)


def _make_mock_client(text: str) -> MagicMock:
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_complete_returns_text(ai_enabled):
    mock_client = _make_mock_client("Einsatzverlauf: Brand in Halle 3.")

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        result = await ai_service.complete("System-Prompt", "User-Prompt")

    assert result == "Einsatzverlauf: Brand in Halle 3."


@pytest.mark.asyncio
async def test_complete_uses_default_model(ai_enabled):
    mock_client = _make_mock_client("ok")

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        await ai_service.complete("sys", "user", fast=False)

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_complete_uses_fast_model(ai_enabled):
    mock_client = _make_mock_client("schnell")

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        await ai_service.complete("sys", "user", fast=True)

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_complete_respects_custom_max_tokens(ai_enabled):
    mock_client = _make_mock_client("ok")

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        await ai_service.complete("sys", "user", max_tokens=500)

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == 500


@pytest.mark.asyncio
async def test_complete_raises_when_disabled(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", False)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "")

    with pytest.raises(AIServiceError):
        await ai_service.complete("sys", "user")


@pytest.mark.asyncio
async def test_complete_raises_when_no_key(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_ENABLED", True)
    monkeypatch.setattr("app.services.ai_service.settings.ANTHROPIC_API_KEY", "")

    with pytest.raises(AIServiceError):
        await ai_service.complete("sys", "user")


@pytest.mark.asyncio
async def test_complete_timeout_raises_ai_service_error(ai_enabled):
    with patch("app.services.ai_service.asyncio.wait_for", side_effect=asyncio.TimeoutError):
        with pytest.raises(AIServiceError, match="Timeout"):
            await ai_service.complete("sys", "user")


@pytest.mark.asyncio
async def test_complete_api_error_raises_ai_service_error(ai_enabled):
    import httpx
    from anthropic import APIConnectionError

    mock_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        side_effect=APIConnectionError(request=mock_request)
    )

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        with pytest.raises(AIServiceError):
            await ai_service.complete("sys", "user")


@pytest.mark.asyncio
async def test_complete_empty_response_raises_ai_service_error(ai_enabled):
    mock_response = MagicMock()
    mock_response.content = []
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("app.services.ai_service.AsyncAnthropic", return_value=mock_client):
        with pytest.raises(AIServiceError, match="leer"):
            await ai_service.complete("sys", "user")
