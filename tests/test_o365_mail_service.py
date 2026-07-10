"""Office 365 / Microsoft Graph Mailversand: Token-Erwerb (Cache, Fehler),
sendMail-Payload/-Aufruf (Erfolg, 401-Retry, Fehlschlag), tenant_id/client_id-Validierung."""
from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.crypto import encrypt_secret
from app.services.o365_mail_service import (
    O365MailError,
    _acquire_token,
    _graph_payload,
    _token_cache,
    _validate_ids,
    send_via_graph,
)


@pytest.fixture(autouse=True)
def clear_token_cache():
    _token_cache.clear()
    yield
    _token_cache.clear()


def _cfg(**overrides) -> SimpleNamespace:
    defaults = dict(
        org_id=1,
        tenant_id="11111111-1111-1111-1111-111111111111",
        client_id="22222222-2222-2222-2222-222222222222",
        client_secret_enc=encrypt_secret("supersecret"),
        sender_address="einsatz@feuerwehr-beispiel.at",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_response(status_code: int, json_data: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = text
    return resp


def _patched_client(responses: list):
    """httpx.AsyncClient-Mock, der bei jedem .post() die naechste Response aus
    der Liste liefert (in Aufreihenfolge)."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=responses)
    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_cls, mock_client


# ── _validate_ids ──────────────────────────────────────────────────────────────

def test_validate_ids_accepts_guid():
    _validate_ids("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222")


def test_validate_ids_rejects_path_traversal():
    with pytest.raises(O365MailError):
        _validate_ids("../../evil", "client")


def test_validate_ids_rejects_empty():
    with pytest.raises(O365MailError):
        _validate_ids("", "client")


# ── _acquire_token ─────────────────────────────────────────────────────────────

async def test_acquire_token_caches_result():
    token_resp = _mock_response(200, {"access_token": "tok-1", "expires_in": 3600})
    mock_cls, mock_client = _patched_client([token_resp])

    with patch("httpx.AsyncClient", mock_cls):
        t1 = await _acquire_token(org_id=1, tenant_id="tid", client_id="cid", client_secret="sec")
        t2 = await _acquire_token(org_id=1, tenant_id="tid", client_id="cid", client_secret="sec")

    assert t1 == "tok-1"
    assert t2 == "tok-1"
    assert mock_client.post.call_count == 1  # zweiter Aufruf kam aus dem Cache


async def test_acquire_token_force_bypasses_cache():
    responses = [
        _mock_response(200, {"access_token": "tok-1", "expires_in": 3600}),
        _mock_response(200, {"access_token": "tok-2", "expires_in": 3600}),
    ]
    mock_cls, mock_client = _patched_client(responses)

    with patch("httpx.AsyncClient", mock_cls):
        t1 = await _acquire_token(org_id=1, tenant_id="tid", client_id="cid", client_secret="sec")
        t2 = await _acquire_token(
            org_id=1, tenant_id="tid", client_id="cid", client_secret="sec", force=True,
        )

    assert t1 == "tok-1"
    assert t2 == "tok-2"
    assert mock_client.post.call_count == 2


async def test_acquire_token_http_error_raises():
    err_resp = _mock_response(400, {"error": "invalid_client", "error_description": "bad secret"})
    mock_cls, _ = _patched_client([err_resp])

    with patch("httpx.AsyncClient", mock_cls):
        with pytest.raises(O365MailError):
            await _acquire_token(org_id=1, tenant_id="tid", client_id="cid", client_secret="wrong")


# ── _graph_payload ─────────────────────────────────────────────────────────────

def test_graph_payload_prefers_html_and_splits_recipients():
    msg = EmailMessage()
    msg["To"] = "a@example.at, b@example.at"
    msg["Subject"] = "Betreff"
    msg.set_content("Text-Version", subtype="plain")
    msg.add_alternative("<p>HTML-Version</p>", subtype="html")

    payload = _graph_payload(msg)

    assert payload["message"]["subject"] == "Betreff"
    assert payload["message"]["body"]["contentType"] == "HTML"
    assert "HTML-Version" in payload["message"]["body"]["content"]
    assert payload["message"]["toRecipients"] == [
        {"emailAddress": {"address": "a@example.at"}},
        {"emailAddress": {"address": "b@example.at"}},
    ]
    assert "from" not in payload["message"]
    assert "sender" not in payload["message"]


def test_graph_payload_plain_text_only():
    msg = EmailMessage()
    msg["To"] = "a@example.at"
    msg["Subject"] = "Betreff"
    msg.set_content("Nur Text", subtype="plain")

    payload = _graph_payload(msg)

    assert payload["message"]["body"]["contentType"] == "Text"
    assert "Nur Text" in payload["message"]["body"]["content"]


# ── send_via_graph ─────────────────────────────────────────────────────────────

def _msg() -> EmailMessage:
    m = EmailMessage()
    m["To"] = "empfaenger@example.at"
    m["Subject"] = "Test"
    m.set_content("Testinhalt", subtype="plain")
    return m


async def test_send_via_graph_success():
    token_resp = _mock_response(200, {"access_token": "tok-1", "expires_in": 3600})
    send_resp = _mock_response(202)
    mock_cls, mock_client = _patched_client([token_resp, send_resp])

    with patch("httpx.AsyncClient", mock_cls):
        await send_via_graph(_msg(), _cfg())

    # Zweiter Aufruf (sendMail) ging an die richtige URL mit Bearer-Token
    send_call = mock_client.post.call_args_list[1]
    assert send_call.args[0] == "https://graph.microsoft.com/v1.0/users/einsatz@feuerwehr-beispiel.at/sendMail"
    assert send_call.kwargs["headers"]["Authorization"] == "Bearer tok-1"


async def test_send_via_graph_401_forces_refresh_and_retries():
    token_resp_1 = _mock_response(200, {"access_token": "tok-1", "expires_in": 3600})
    send_resp_401 = _mock_response(401, {"error": {"message": "InvalidAuthenticationToken"}})
    token_resp_2 = _mock_response(200, {"access_token": "tok-2", "expires_in": 3600})
    send_resp_202 = _mock_response(202)
    mock_cls, mock_client = _patched_client(
        [token_resp_1, send_resp_401, token_resp_2, send_resp_202],
    )

    with patch("httpx.AsyncClient", mock_cls):
        await send_via_graph(_msg(), _cfg())

    assert mock_client.post.call_count == 4
    last_send_call = mock_client.post.call_args_list[3]
    assert last_send_call.kwargs["headers"]["Authorization"] == "Bearer tok-2"


async def test_send_via_graph_non_2xx_raises():
    token_resp = _mock_response(200, {"access_token": "tok-1", "expires_in": 3600})
    send_resp_500 = _mock_response(500, {"error": {"message": "Internal error"}})
    mock_cls, _ = _patched_client([token_resp, send_resp_500])

    with patch("httpx.AsyncClient", mock_cls):
        with pytest.raises(O365MailError):
            await send_via_graph(_msg(), _cfg())


async def test_send_via_graph_incomplete_config_raises():
    with pytest.raises(O365MailError):
        await send_via_graph(_msg(), _cfg(sender_address=None))
