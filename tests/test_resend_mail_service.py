"""Resend-Payload und HTTP-Versand."""
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.resend_mail_service import (
    ResendMailError,
    _mark_test_system_from_addr,
    _resend_payload,
    send_via_resend,
)


def _msg(html: bool = False) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "falsch@example.at"
    msg["To"] = "a@example.at, b@example.at"
    msg["Subject"] = "Test"
    msg.set_content("Textinhalt")
    if html:
        msg.add_alternative("<p>HTML-Inhalt</p>", subtype="html")
    return msg


def _client(response):
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=client)
    cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return cls, client


def test_resend_payload_contains_both_bodies_and_explicit_from():
    payload = _resend_payload(_msg(html=True), "org@verified.example")
    assert payload["from"] == "org@verified.example"
    assert payload["to"] == ["a@example.at", "b@example.at"]
    assert "Textinhalt" in payload["text"]
    assert "HTML-Inhalt" in payload["html"]


async def test_send_via_resend_success():
    response = MagicMock(status_code=200)
    cls, client = _client(response)
    with patch("httpx.AsyncClient", cls):
        await send_via_resend(_msg(), "re_key", "org@verified.example")
    call = client.post.call_args
    assert call.args[0] == "https://api.resend.com/emails"
    assert call.kwargs["headers"]["Authorization"] == "Bearer re_key"
    assert call.kwargs["json"]["from"] == "org@verified.example"


async def test_send_via_resend_http_error():
    response = MagicMock(status_code=422, text="Fehler")
    response.json.return_value = {"message": "Domain nicht verifiziert"}
    cls, _ = _client(response)
    with patch("httpx.AsyncClient", cls), pytest.raises(ResendMailError, match="Domain nicht verifiziert"):
        await send_via_resend(_msg(), "re_key", "org@invalid.example")


@pytest.mark.parametrize("api_key,from_addr", [("", "a@example.at"), ("key", "")])
async def test_send_via_resend_incomplete_config(api_key, from_addr):
    with pytest.raises(ResendMailError, match="unvollstaendig"):
        await send_via_resend(_msg(), api_key, from_addr)


def test_mark_test_system_from_addr_noop_when_not_test_system(monkeypatch):
    monkeypatch.setattr(settings, "TEST_SYSTEM", False)
    assert _mark_test_system_from_addr("noreply@einsatzcockpit.com") == "noreply@einsatzcockpit.com"


def test_mark_test_system_from_addr_prefixes_local_part_and_sets_display_name(monkeypatch):
    monkeypatch.setattr(settings, "TEST_SYSTEM", True)
    result = _mark_test_system_from_addr("feuerwehr.wolfurt@einsatzcockpit.com")
    assert result == "Einsatzcockpit (Testsystem) <test-feuerwehr.wolfurt@einsatzcockpit.com>"


async def test_send_via_resend_marks_from_addr_on_test_system(monkeypatch):
    monkeypatch.setattr(settings, "TEST_SYSTEM", True)
    response = MagicMock(status_code=200)
    cls, client = _client(response)
    with patch("httpx.AsyncClient", cls):
        await send_via_resend(_msg(), "re_key", "org@verified.example")
    call = client.post.call_args
    assert call.kwargs["json"]["from"] == "Einsatzcockpit (Testsystem) <test-org@verified.example>"
