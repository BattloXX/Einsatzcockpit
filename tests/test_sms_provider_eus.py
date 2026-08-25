"""Tests fuer EUS-SMS-Adapter und providerneutralen Dispatcher."""
from __future__ import annotations

import asyncio
import json
import os
import time
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

os.environ.setdefault("SECRET_KEY", "test-secret-key-fuer-tests-mindestens-32-zeichen!")
os.environ.setdefault("DEBUG", "true")
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from app.services import eus_sms_service
from app.services.eus_sms_service import (
    EusConfig,
    EusSmsError,
    invalidate_token_cache,
    normalize_base_url,
    send_via_eus,
)
from app.services.sms_service import SmsContext, resolve_sms_config, send_sms, sms_available

_REAL_ASYNC_CLIENT = httpx.AsyncClient
_TEST_ORG_ID = 1  # FF Wolfurt (seeded, siehe test_pin_login.py)


def _set_org_sms_config(**kwargs) -> None:
    """Legt fuer _TEST_ORG_ID eine OrgSmsConfig-Zeile an/aktualisiert sie."""
    from datetime import UTC, datetime

    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_sms import OrgSmsConfig

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        cfg = db.query(OrgSmsConfig).filter(OrgSmsConfig.org_id == _TEST_ORG_ID).first()
        if cfg is None:
            cfg = OrgSmsConfig(
                org_id=_TEST_ORG_ID, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            )
            db.add(cfg)
        for key, value in kwargs.items():
            setattr(cfg, key, value)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def org_sms_config():
    """Stellt sicher, dass _TEST_ORG_ID nach jedem Test wieder ohne OrgSmsConfig-Zeile
    dasteht (Default-Verhalten: nur Gateway) -- unabhaengig davon, was der Test setzt."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.org_sms import OrgSmsConfig

    yield
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSmsConfig).filter(OrgSmsConfig.org_id == _TEST_ORG_ID).delete()
        db.commit()
    finally:
        db.close()


def _cfg(org_id: int = 1, auth_mode: str = "oauth") -> EusConfig:
    return EusConfig(org_id, "https://eus.example.at", auth_mode, "client", "secret", 15)


@pytest.mark.parametrize("value", ["", "file://host", "ftp://host", "https://host/pfad"])
def test_normalize_base_url_rejects_invalid(value):
    with pytest.raises(EusSmsError):
        normalize_base_url(value)


def test_normalize_base_url_strips_slash():
    assert normalize_base_url("https://host/") == "https://host"


@pytest.mark.asyncio
async def test_send_sms_eus_success_without_gateway(monkeypatch):
    eus = AsyncMock()
    gateway = AsyncMock()
    monkeypatch.setattr("app.services.sms_service.send_via_eus", eus)
    monkeypatch.setattr("app.routers.ws.dispatch_sms", gateway)
    ctx = SmsContext(1, ["eus"], _cfg())
    result = await send_sms(1, "+436601234", "Text", ctx=ctx)
    assert result
    assert result.provider == "eus"
    assert result.gateway_token_id is None
    gateway.assert_not_awaited()
    assert ctx.providers_used == {"eus"}


@pytest.mark.asyncio
async def test_send_sms_without_fallback_does_not_call_gateway(monkeypatch):
    eus = AsyncMock(side_effect=EusSmsError("HTTP 500"))
    gateway = AsyncMock()
    monkeypatch.setattr("app.services.sms_service.send_via_eus", eus)
    monkeypatch.setattr("app.routers.ws.dispatch_sms", gateway)
    assert not await send_sms(1, "+436601234", "Text", ctx=SmsContext(1, ["eus"], _cfg()))
    gateway.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_sms_eus_falls_back_to_gateway(monkeypatch):
    monkeypatch.setattr(
        "app.services.sms_service.send_via_eus",
        AsyncMock(side_effect=EusSmsError("HTTP 500")),
    )
    gateway = AsyncMock(return_value={"ok": True, "gateway_token_id": 17})
    monkeypatch.setattr("app.routers.ws.dispatch_sms", gateway)
    ctx = SmsContext(1, ["eus", "gateway"], _cfg())
    result = await send_sms(1, "+436601234", "Text", ctx=ctx)
    assert result
    assert result.provider == "gateway"
    assert result.gateway_token_id == 17
    gateway.assert_awaited_once()
    assert ctx.providers_used == {"eus", "gateway"}


class _Client:
    transport: httpx.MockTransport

    def __init__(self, *args, **kwargs):
        self.client = _REAL_ASYNC_CLIENT(transport=self.transport)
        self.is_closed = False

    async def post(self, *args, **kwargs):
        return await self.client.post(*args, **kwargs)

    async def aclose(self):
        self.is_closed = True
        await self.client.aclose()


@pytest_asyncio.fixture(autouse=True)
async def reset_eus_http_client():
    alter_client = eus_sms_service._http_client
    eus_sms_service._http_client = None
    yield
    client = eus_sms_service._http_client
    eus_sms_service._http_client = alter_client
    if client is not None and not client.is_closed:
        await client.aclose()


@pytest.mark.asyncio
async def test_oauth_token_cache_and_exact_payload(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path == "/oauthapi/login":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(200)

    _Client.transport = httpx.MockTransport(handler)
    monkeypatch.setattr(eus_sms_service.httpx, "AsyncClient", _Client)
    invalidate_token_cache(91)
    cfg = _cfg(91)
    await send_via_eus(cfg, "+431", "Hallo")
    await send_via_eus(cfg, "+432", "Welt")
    assert sum(r.url.path == "/oauthapi/login" for r in requests) == 1
    sends = [r for r in requests if r.url.path == "/messageapi/send"]
    assert json.loads(sends[0].content) == {"body": "Hallo", "recipients": "+431"}


@pytest.mark.asyncio
async def test_expired_token_and_invalidation_request_new_token(monkeypatch):
    count = 0

    def handler(request: httpx.Request):
        nonlocal count
        if request.url.path == "/oauthapi/login":
            count += 1
            return httpx.Response(200, json={"access_token": str(count), "expires_in": 3600})
        return httpx.Response(200)

    _Client.transport = httpx.MockTransport(handler)
    monkeypatch.setattr(eus_sms_service.httpx, "AsyncClient", _Client)
    cfg = _cfg(92)
    eus_sms_service._token_cache[92] = ("old", time.time() - 1)
    await send_via_eus(cfg, "+431", "A")
    invalidate_token_cache(92)
    await send_via_eus(cfg, "+431", "B")
    assert count == 2


@pytest.mark.asyncio
async def test_basic_auth_skips_login(monkeypatch):
    paths: list[str] = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200)

    _Client.transport = httpx.MockTransport(handler)
    monkeypatch.setattr(eus_sms_service.httpx, "AsyncClient", _Client)
    await send_via_eus(_cfg(93, "basic"), "+431", "A")
    assert paths == ["/messageapi/send"]


@pytest.mark.asyncio
async def test_concurrent_oauth_sends_acquire_only_one_token(monkeypatch):
    login_count = 0

    async def handler(request: httpx.Request):
        nonlocal login_count
        if request.url.path == "/oauthapi/login":
            login_count += 1
            await asyncio.sleep(0.02)
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(200)

    _Client.transport = httpx.MockTransport(handler)
    monkeypatch.setattr(eus_sms_service.httpx, "AsyncClient", _Client)
    invalidate_token_cache(94)

    await asyncio.gather(*(send_via_eus(_cfg(94), f"+43{i}", "A") for i in range(8)))

    assert login_count == 1


# ── resolve_sms_config / sms_available ───────────────────────────────────────

def test_resolve_sms_config_no_row_defaults_to_gateway(org_sms_config):
    """Bestandsorgs ohne OrgSmsConfig-Zeile verhalten sich unveraendert (nur Gateway)."""
    ctx = resolve_sms_config(_TEST_ORG_ID)
    assert ctx.chain == ["gateway"]
    assert ctx.eus is None


def test_resolve_sms_config_eus_primary_no_fallback(org_sms_config):
    from app.core.crypto import encrypt_secret

    _set_org_sms_config(
        primary_provider="eus", fallback_provider=None, eus_enabled=True,
        eus_base_url="https://eus.example.at", eus_auth_mode="oauth",
        eus_client_id="client", eus_client_secret_enc=encrypt_secret("secret"),
    )
    ctx = resolve_sms_config(_TEST_ORG_ID)
    assert ctx.chain == ["eus"]
    assert ctx.eus is not None
    assert ctx.eus.base_url == "https://eus.example.at"


def test_resolve_sms_config_eus_disabled_falls_back_to_gateway(org_sms_config):
    from app.core.crypto import encrypt_secret

    _set_org_sms_config(
        primary_provider="eus", eus_enabled=False,
        eus_base_url="https://eus.example.at",
        eus_client_id="client", eus_client_secret_enc=encrypt_secret("secret"),
    )
    ctx = resolve_sms_config(_TEST_ORG_ID)
    assert ctx.chain == ["gateway"]
    assert ctx.eus is None


def test_resolve_sms_config_eus_incomplete_falls_back_to_gateway(org_sms_config):
    """Kein Client Secret hinterlegt -> is_eus_fully_configured ist False."""
    _set_org_sms_config(
        primary_provider="eus", eus_enabled=True,
        eus_base_url="https://eus.example.at", eus_client_id="client",
        eus_client_secret_enc=None,
    )
    ctx = resolve_sms_config(_TEST_ORG_ID)
    assert ctx.chain == ["gateway"]
    assert ctx.eus is None


def test_resolve_sms_config_kill_switch(org_sms_config, monkeypatch):
    from app.config import settings
    from app.core.crypto import encrypt_secret

    _set_org_sms_config(
        primary_provider="eus", eus_enabled=True,
        eus_base_url="https://eus.example.at",
        eus_client_id="client", eus_client_secret_enc=encrypt_secret("secret"),
    )
    monkeypatch.setattr(settings, "EUS_SMS_ENABLED", False)
    ctx = resolve_sms_config(_TEST_ORG_ID)
    assert ctx.chain == ["gateway"]
    assert ctx.eus is None


def test_resolve_sms_config_fallback_equal_primary_deduplicated(org_sms_config):
    from app.core.crypto import encrypt_secret

    _set_org_sms_config(
        primary_provider="eus", fallback_provider="eus", eus_enabled=True,
        eus_base_url="https://eus.example.at",
        eus_client_id="client", eus_client_secret_enc=encrypt_secret("secret"),
    )
    ctx = resolve_sms_config(_TEST_ORG_ID)
    assert ctx.chain == ["eus"]


def test_sms_available_true_for_eus_without_gateway(org_sms_config, monkeypatch):
    from app.core.crypto import encrypt_secret

    _set_org_sms_config(
        primary_provider="eus", eus_enabled=True,
        eus_base_url="https://eus.example.at",
        eus_client_id="client", eus_client_secret_enc=encrypt_secret("secret"),
    )
    monkeypatch.setattr("app.routers.ws.is_sms_gateway_connected", lambda org_id: False)
    assert sms_available(_TEST_ORG_ID) is True
