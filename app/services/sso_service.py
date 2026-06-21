"""OIDC/Entra-ID-Service: PKCE, Discovery, Token-Tausch, id_token-Validierung, Gruppen."""
from __future__ import annotations

import secrets
import time
from base64 import urlsafe_b64encode
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import ExpiredSignatureError, JWTError, jwt

from app.config import settings


class SsoError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── authority_base Whitelist (F-01: SSRF-Schutz) ─────────────────────────────

ALLOWED_AUTHORITY_BASES: frozenset[str] = frozenset({
    "https://login.microsoftonline.com",        # Public Cloud
    "https://login.microsoftonline.us",          # US Government
    "https://login.partner.microsoftonline.cn",  # China
    "https://login.microsoftonline.de",          # Germany (legacy)
})


def validate_authority_base(value: str | None) -> str | None:
    """Gibt bereinigte authority_base zurück oder None (= Public Cloud Default).
    Wirft ValueError bei unerlaubtem Wert."""
    if not value or not value.strip():
        return None
    stripped = value.strip().rstrip("/")
    if stripped not in ALLOWED_AUTHORITY_BASES:
        raise ValueError(
            f"authority_base nicht erlaubt: '{stripped}'. "
            f"Erlaubt: {sorted(ALLOWED_AUTHORITY_BASES)}"
        )
    return stripped


# ── JWKS-Cache (in-memory, keyed by tenant_id + authority_base) ──────────────
# F-06: Cache-Key enthält authority_base, damit Änderungen sofort wirken.

_jwks_cache: dict[str, tuple[dict, float]] = {}


def _cache_key(tenant_id: str, authority_base: str | None) -> str:
    return f"{tenant_id}|{authority_base or ''}"


def _authority(tenant_id: str, authority_base: str | None = None) -> str:
    base = (authority_base or settings.MS_LOGIN_BASE_URL).rstrip("/")
    return f"{base}/{tenant_id}/v2.0"


async def _get_jwks(tenant_id: str, authority_base: str | None = None) -> dict:
    """Lädt JWKS für einen Tenant, cached für SSO_JWKS_CACHE_TTL Sekunden."""
    key = _cache_key(tenant_id, authority_base)
    cached = _jwks_cache.get(key)
    if cached and (time.time() - cached[1]) < settings.SSO_JWKS_CACHE_TTL:
        return cached[0]
    return await _fetch_jwks(tenant_id, authority_base)


async def _fetch_jwks(tenant_id: str, authority_base: str | None = None) -> dict:
    authority = _authority(tenant_id, authority_base)
    async with httpx.AsyncClient(timeout=settings.SSO_HTTP_TIMEOUT) as client:
        disco = await client.get(f"{authority}/.well-known/openid-configuration")
        disco.raise_for_status()
        jwks_uri = disco.json()["jwks_uri"]
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()
    _jwks_cache[_cache_key(tenant_id, authority_base)] = (jwks, time.time())
    return jwks


# ── PKCE + State/Nonce ────────────────────────────────────────────────────────

def generate_pkce() -> tuple[str, str]:
    """Gibt (code_verifier, code_challenge_S256) zurück."""
    verifier = secrets.token_urlsafe(64)
    digest = sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_urlsafe(24)


def generate_nonce() -> str:
    return secrets.token_urlsafe(24)


# ── Authorize-URL ─────────────────────────────────────────────────────────────

def build_authorize_url(
    *,
    tenant_id: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    authority_base: str | None = None,
    login_hint: str | None = None,
) -> str:
    base = (authority_base or settings.MS_LOGIN_BASE_URL).rstrip("/")
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": settings.SSO_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{base}/{tenant_id}/oauth2/v2.0/authorize?{urlencode(params)}"


# ── Token-Tausch ──────────────────────────────────────────────────────────────

async def exchange_code(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    authority_base: str | None = None,
) -> dict[str, Any]:
    """Tauscht Authorization Code gegen Token-Antwort (access_token + id_token)."""
    base = (authority_base or settings.MS_LOGIN_BASE_URL).rstrip("/")
    token_url = f"{base}/{tenant_id}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=settings.SSO_HTTP_TIMEOUT) as client:
        resp = await client.post(token_url, data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": code_verifier,
            "scope": settings.SSO_SCOPES,
        })
    if resp.status_code != 200:
        raise SsoError("token_exchange_failed",
                        f"Token-Tausch fehlgeschlagen: HTTP {resp.status_code}")
    return resp.json()


# ── id_token-Validierung ──────────────────────────────────────────────────────

async def validate_id_token(
    *,
    id_token: str,
    tenant_id: str,
    client_id: str,
    nonce: str,
    authority_base: str | None = None,
) -> dict[str, Any]:
    """Validiert id_token: Signatur, aud, iss, exp, nbf, nonce, tid."""
    jwks = await _get_jwks(tenant_id, authority_base)

    try:
        header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise SsoError("idtoken_invalid", f"id_token Header unlesbar: {exc}") from exc

    # kid-Miss → JWKS neu laden (Key-Rollover), Cache für diesen Tenant invalidieren
    kid = header.get("kid")
    key_ids = {k.get("kid") for k in jwks.get("keys", [])}
    if kid and kid not in key_ids:
        _jwks_cache.pop(_cache_key(tenant_id, authority_base), None)
        jwks = await _fetch_jwks(tenant_id, authority_base)

    issuer = f"{_authority(tenant_id, authority_base)}"

    try:
        claims = jwt.decode(
            id_token,
            jwks,
            algorithms=["RS256"],
            audience=client_id,
            issuer=issuer,
            options={"leeway": 60},
        )
    except ExpiredSignatureError as exc:
        raise SsoError("idtoken_invalid", "id_token abgelaufen") from exc
    except JWTError as exc:
        raise SsoError("idtoken_invalid", f"id_token ungültig: {exc}") from exc

    if claims.get("nonce") != nonce:
        raise SsoError("idtoken_invalid", "Nonce stimmt nicht überein")

    if claims.get("tid") != tenant_id:
        raise SsoError("tenant_mismatch",
                        f"Tenant-ID im Token ({claims.get('tid')}) entspricht nicht der Konfiguration")

    return claims


# ── Gruppen ermitteln ─────────────────────────────────────────────────────────

async def get_groups(claims: dict[str, Any], access_token: str) -> set[str]:
    """Gibt Menge von Entra-Gruppen-Object-IDs zurück (direkt aus Claims oder Graph-Fallback)."""
    if "groups" in claims:
        return set(claims["groups"])

    # Overage: _claim_names enthält 'groups'
    if "_claim_names" in claims and "groups" in claims.get("_claim_names", {}):
        return await _get_groups_via_graph(access_token)

    return set()


async def _get_groups_via_graph(access_token: str) -> set[str]:
    """Ruft Gruppen über Microsoft Graph ab (bei Overage)."""
    async with httpx.AsyncClient(timeout=settings.SSO_HTTP_TIMEOUT) as client:
        resp = await client.post(
            "https://graph.microsoft.com/v1.0/me/getMemberObjects",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"securityEnabledOnly": True},
        )
    if resp.status_code != 200:
        raise SsoError("graph_failed",
                        f"Gruppen per Graph nicht abrufbar: HTTP {resp.status_code}")
    data = resp.json()
    return set(data.get("value", []))


# ── Rollen-Mapping ────────────────────────────────────────────────────────────

def map_groups_to_roles(
    groups: set[str],
    config,  # OrgSsoConfig
    system_admin_role_id: int | None,
) -> set[int] | None:
    """
    Gibt gemappte role_ids zurück oder None wenn Deny.
    system_admin wird nie über SSO vergeben.
    """
    mapped: set[int] = set()
    for gm in config.group_mappings:
        if gm.entra_group_id in groups:
            mapped.add(gm.role_id)

    if system_admin_role_id is not None:
        mapped.discard(system_admin_role_id)

    if not mapped:
        if config.deny_if_no_group:
            return None
        if config.default_role_id:
            mapped = {config.default_role_id}
            if system_admin_role_id is not None:
                mapped.discard(system_admin_role_id)
        else:
            return None

    return mapped
