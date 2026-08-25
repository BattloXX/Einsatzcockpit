"""EUS Message Send API: Authentifizierung und SMS-Versand."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger("einsatzleiter.sms.eus")


class EusSmsError(RuntimeError):
    """EUS-Token-Erwerb oder SMS-Versand ist fehlgeschlagen."""


@dataclass(frozen=True)
class EusConfig:
    org_id: int
    base_url: str
    auth_mode: str
    client_id: str
    client_secret: str
    timeout: int


_token_cache: dict[int, tuple[str, float]] = {}
_token_locks: dict[int, asyncio.Lock] = {}
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        return _http_client
    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient()
        return _http_client


def invalidate_token_cache(org_id: int) -> None:
    _token_cache.pop(org_id, None)


def normalize_base_url(raw: str | None) -> str:
    value = (raw or "").strip().rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise EusSmsError("Ungueltige EUS-Serveradresse")
    return value


async def _acquire_token(
    org_id: int, base_url: str, client_id: str, client_secret: str,
    timeout: int, force: bool = False,
) -> str:
    now = time.time()
    vorheriger_cache = _token_cache.get(org_id)
    if not force:
        if vorheriger_cache and vorheriger_cache[1] - now > settings.EUS_SMS_TOKEN_MARGIN_S:
            return vorheriger_cache[0]

    token_lock = _token_locks.setdefault(org_id, asyncio.Lock())
    async with token_lock:
        now = time.time()
        cached = _token_cache.get(org_id)
        if cached and cached[1] - now > settings.EUS_SMS_TOKEN_MARGIN_S:
            if not force or cached != vorheriger_cache:
                return cached[0]

        client = await _get_http_client()
        response = await client.post(
            f"{base_url}/oauthapi/login",
            data={"grant_type": "client_credentials", "client_id": client_id,
                  "client_secret": client_secret},
            timeout=timeout,
        )
        if not 200 <= response.status_code < 300:
            raise EusSmsError(f"HTTP {response.status_code}: {response.text[:400]}")
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise EusSmsError("Token-Antwort ohne access_token")
        _token_cache[org_id] = (str(token), now + float(data.get("expires_in", 3600)))
        return str(token)


async def send_via_eus(cfg: EusConfig, to: str, text: str) -> None:
    base_url = normalize_base_url(cfg.base_url)
    timeout = cfg.timeout or settings.EUS_SMS_HTTP_TIMEOUT
    payload = {"body": text, "recipients": to}

    async def attempt(token: str | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        client = await _get_http_client()
        if cfg.auth_mode == "basic":
            return await client.post(
                f"{base_url}/messageapi/send",
                auth=httpx.BasicAuth(cfg.client_id, cfg.client_secret),
                json=payload,
                timeout=timeout,
            )
        return await client.post(
            f"{base_url}/messageapi/send", headers=headers, json=payload, timeout=timeout
        )

    if cfg.auth_mode == "oauth":
        token = await _acquire_token(
            cfg.org_id, base_url, cfg.client_id, cfg.client_secret, timeout
        )
        response = await attempt(token)
        if response.status_code in (401, 403):
            token = await _acquire_token(
                cfg.org_id, base_url, cfg.client_id, cfg.client_secret, timeout, force=True
            )
            response = await attempt(token)
    elif cfg.auth_mode == "basic":
        response = await attempt()
    else:
        raise EusSmsError("Ungueltiger EUS-Authentifizierungsmodus")

    if not 200 <= response.status_code < 300:
        from app.services.sms_service import _mask
        logger.error("EUS-SMS an %s fehlgeschlagen (Org %s): HTTP %s",
                     _mask(to), cfg.org_id, response.status_code)
        raise EusSmsError(f"HTTP {response.status_code}: {response.text[:400]}")
