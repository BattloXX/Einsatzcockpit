"""Tests für die PyJWT-basierte Entra-ID-id_token-Validierung."""
from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import sso_service

TENANT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "test-client-id"
NONCE = "test-nonce"
KID = "test-key-id"


def _jwks_for(key: rsa.RSAPrivateKey, kid: str) -> dict:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.pop("alg", None)  # Entra-JWKS liefern kein "alg"
    jwk.update({"kid": kid, "use": "sig"})
    return {"keys": [jwk]}


def _token(
    key: rsa.RSAPrivateKey,
    *,
    kid: str = KID,
    audience: str = CLIENT_ID,
    issuer: str | None = None,
    nonce: str = NONCE,
    tenant_id: str = TENANT_ID,
    expires_at: int | None = None,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "aud": audience,
            "iss": issuer or sso_service._authority(TENANT_ID),
            "nonce": nonce,
            "tid": tenant_id,
            "exp": expires_at if expires_at is not None else now + 300,
        },
        key,
        algorithm="RS256",
        headers={"kid": kid},
    )


@pytest.fixture
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _mock_jwks(monkeypatch: pytest.MonkeyPatch, jwks: dict) -> None:
    async def get_jwks(tenant_id: str, authority_base: str | None = None) -> dict:
        assert tenant_id == TENANT_ID
        return jwks

    monkeypatch.setattr(sso_service, "_get_jwks", get_jwks)


async def test_validate_id_token_returns_valid_claims(monkeypatch, rsa_key):
    _mock_jwks(monkeypatch, _jwks_for(rsa_key, KID))
    token = _token(rsa_key)

    claims = await sso_service.validate_id_token(
        id_token=token, tenant_id=TENANT_ID, client_id=CLIENT_ID, nonce=NONCE,
    )

    assert claims["tid"] == TENANT_ID
    assert claims["nonce"] == NONCE


@pytest.mark.parametrize(
    ("token_kwargs", "message"),
    [
        ({"expires_at": int(time.time()) - 61}, "id_token abgelaufen"),
        ({"audience": "wrong-client"}, "id_token ungültig"),
        ({"issuer": "https://issuer.invalid/v2.0"}, "id_token ungültig"),
    ],
)
async def test_validate_id_token_rejects_invalid_standard_claims(monkeypatch, rsa_key, token_kwargs, message):
    _mock_jwks(monkeypatch, _jwks_for(rsa_key, KID))

    with pytest.raises(sso_service.SsoError, match=message) as error:
        await sso_service.validate_id_token(
            id_token=_token(rsa_key, **token_kwargs),
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            nonce=NONCE,
        )

    assert error.value.code == "idtoken_invalid"


async def test_validate_id_token_rejects_wrong_signature(monkeypatch, rsa_key):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _mock_jwks(monkeypatch, _jwks_for(rsa_key, KID))

    with pytest.raises(sso_service.SsoError, match="id_token ungültig"):
        await sso_service.validate_id_token(
            id_token=_token(other_key), tenant_id=TENANT_ID, client_id=CLIENT_ID, nonce=NONCE,
        )


async def test_validate_id_token_refetches_jwks_for_unknown_kid(monkeypatch, rsa_key):
    old_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    refreshed_jwks = _jwks_for(rsa_key, KID)
    fetch_calls = 0

    async def get_jwks(tenant_id: str, authority_base: str | None = None) -> dict:
        return _jwks_for(old_key, "old-key-id")

    async def fetch_jwks(tenant_id: str, authority_base: str | None = None) -> dict:
        nonlocal fetch_calls
        fetch_calls += 1
        return refreshed_jwks

    monkeypatch.setattr(sso_service, "_get_jwks", get_jwks)
    monkeypatch.setattr(sso_service, "_fetch_jwks", fetch_jwks)

    claims = await sso_service.validate_id_token(
        id_token=_token(rsa_key), tenant_id=TENANT_ID, client_id=CLIENT_ID, nonce=NONCE,
    )

    assert claims["tid"] == TENANT_ID
    assert fetch_calls == 1


@pytest.mark.parametrize(
    ("token_kwargs", "code", "message"),
    [
        ({"nonce": "wrong-nonce"}, "idtoken_invalid", "Nonce stimmt nicht überein"),
        ({"tenant_id": "22222222-2222-2222-2222-222222222222"}, "tenant_mismatch", "Tenant-ID im Token"),
    ],
)
async def test_validate_id_token_checks_nonce_and_tid(monkeypatch, rsa_key, token_kwargs, code, message):
    _mock_jwks(monkeypatch, _jwks_for(rsa_key, KID))

    with pytest.raises(sso_service.SsoError, match=message) as error:
        await sso_service.validate_id_token(
            id_token=_token(rsa_key, **token_kwargs),
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            nonce=NONCE,
        )

    assert error.value.code == code
