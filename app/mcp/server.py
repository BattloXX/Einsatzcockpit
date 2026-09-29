"""DB-gestuetzter OAuth-Provider und Streamable-HTTP-MCP-Server."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl

from app.config import settings
from app.core.security import hash_api_key
from app.db import SessionLocal
from app.mcp.context import MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.mcp.tools import whoami as _whoami  # noqa: F401 - registriert Beispiel-Tool
from app.models.mcp import MCPOAuthClient, MCPOAuthCode, MCPOAuthToken

ACCESS_LIFETIME = timedelta(hours=1)
REFRESH_LIFETIME = timedelta(days=30)
CODE_LIFETIME = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _raw() -> str:
    return secrets.token_urlsafe(32)


class EinsatzcockpitOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        db = SessionLocal()
        try:
            row = db.query(MCPOAuthClient).filter(MCPOAuthClient.client_id == client_id).first()
            return OAuthClientInformationFull.model_validate_json(row.metadata_json) if row else None
        finally:
            db.close()

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # DCR ist absichtlich auf Public Clients begrenzt. Das SDK mintet bei
        # fehlendem Auth-Method-Feld sonst client_secret_post; dieses Geheimnis
        # wird hier weder gespeichert noch nachträglich zu "none" umgedeutet.
        if client_info.token_endpoint_auth_method != "none":
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=(
                    "Nur öffentliche PKCE-Clients mit token_endpoint_auth_method=none werden unterstützt."
                ),
            )
        db = SessionLocal()
        try:
            db.add(
                MCPOAuthClient(
                    client_id=client_info.client_id,
                    client_secret_hash=None,
                    metadata_json=client_info.model_dump_json(),
                )
            )
            db.commit()
        finally:
            db.close()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Speichert nur einen Hash des kurzlebigen Login-Vorgangs."""
        flow = _raw()
        db = SessionLocal()
        try:
            db.add(
                MCPOAuthCode(
                    code_hash=hash_api_key(flow),
                    client_id=client.client_id,
                    redirect_uri=str(params.redirect_uri),
                    code_challenge=params.code_challenge,
                    scopes=" ".join(params.scopes or []),
                    state=params.state,
                    resource=params.resource,
                    expires_at=_now() + CODE_LIFETIME,
                )
            )
            db.commit()
        finally:
            db.close()
        return "/mcp/anmelden?" + urlencode({"vorgang": flow})

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        db = SessionLocal()
        try:
            row = db.query(MCPOAuthCode).filter(MCPOAuthCode.code_hash == hash_api_key(authorization_code)).first()
            if not row or not row.user_id or row.consumed_at or row.expires_at < _now():
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=row.scopes.split(),
                expires_at=row.expires_at.replace(tzinfo=UTC).timestamp(),
                client_id=row.client_id,
                code_challenge=row.code_challenge,
                redirect_uri=cast(AnyUrl, row.redirect_uri),
                redirect_uri_provided_explicitly=True,
                resource=row.resource,
                subject=str(row.user_id),
            )
        finally:
            db.close()

    def _issue(
        self,
        db,
        client_id: str,
        user_id: int,
        org_id: int,
        scopes: list[str],
        resource: str | None,
        family: str | None = None,
    ) -> OAuthToken:
        access, refresh, family = _raw(), _raw(), family or _raw()
        now = _now()
        for raw, typ, expiry in (
            (access, "access", now + ACCESS_LIFETIME),
            (refresh, "refresh", now + REFRESH_LIFETIME),
        ):
            db.add(
                MCPOAuthToken(
                    token_hash=hash_api_key(raw),
                    token_type=typ,
                    client_id=client_id,
                    user_id=user_id,
                    org_id=org_id,
                    scopes=" ".join(scopes),
                    expires_at=expiry,
                    family_id=family,
                )
            )
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        db = SessionLocal()
        try:
            row = db.query(MCPOAuthCode).filter(MCPOAuthCode.code_hash == hash_api_key(authorization_code.code)).first()
            if not row or row.consumed_at or not row.user_id or not row.org_id:
                raise ValueError("ungueltiger Code")
            row.consumed_at = _now()
            token = self._issue(
                db, client.client_id, row.user_id, row.org_id, authorization_code.scopes, authorization_code.resource
            )
            db.commit()
            return token
        finally:
            db.close()

    async def load_refresh_token(self, client, token: str) -> RefreshToken | None:
        db = SessionLocal()
        try:
            row = (
                db.query(MCPOAuthToken)
                .filter(MCPOAuthToken.token_hash == hash_api_key(token), MCPOAuthToken.token_type == "refresh")
                .first()
            )
            if not row or row.revoked_at or row.expires_at < _now():
                return None
            return RefreshToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes.split(),
                expires_at=int(row.expires_at.replace(tzinfo=UTC).timestamp()),
                resource=settings.effective_public_base_url.rstrip("/") + "/mcp",
                subject=str(row.user_id),
            )
        finally:
            db.close()

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        db = SessionLocal()
        try:
            row = (
                db.query(MCPOAuthToken)
                .filter(
                    MCPOAuthToken.token_hash == hash_api_key(refresh_token.token), MCPOAuthToken.token_type == "refresh"
                )
                .first()
            )
            if not row or row.revoked_at or row.expires_at < _now():
                raise ValueError("ungueltiger Refresh-Token")
            now = _now()
            row.revoked_at = now
            for access in db.query(MCPOAuthToken).filter(
                MCPOAuthToken.family_id == row.family_id,
                MCPOAuthToken.token_type == "access",
                MCPOAuthToken.revoked_at.is_(None),
            ):
                access.revoked_at = now
            result = self._issue(
                db, client.client_id, row.user_id, row.org_id, scopes, refresh_token.resource, row.family_id
            )
            db.commit()
            return result
        finally:
            db.close()

    async def load_access_token(self, token: str) -> AccessToken | None:
        db = SessionLocal()
        try:
            row = (
                db.query(MCPOAuthToken)
                .filter(MCPOAuthToken.token_hash == hash_api_key(token), MCPOAuthToken.token_type == "access")
                .first()
            )
            if not row or row.revoked_at or row.expires_at < _now():
                return None
            row.last_used_at = _now()
            db.commit()
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes.split(),
                expires_at=int(row.expires_at.replace(tzinfo=UTC).timestamp()),
                resource=settings.effective_public_base_url.rstrip("/") + "/mcp",
                subject=str(row.user_id),
                claims={"org_id": row.org_id},
            )
        finally:
            db.close()

    async def revoke_token(self, token) -> None:
        db = SessionLocal()
        try:
            row = db.query(MCPOAuthToken).filter(MCPOAuthToken.token_hash == hash_api_key(token.token)).first()
            if row:
                for related in db.query(MCPOAuthToken).filter(MCPOAuthToken.family_id == row.family_id):
                    related.revoked_at = _now()
                db.commit()
        finally:
            db.close()


provider = EinsatzcockpitOAuthProvider()
_base = settings.effective_public_base_url.rstrip("/")
server = MCPServer(
    "Einsatzcockpit",
    auth_server_provider=provider,
    auth=AuthSettings(
    issuer_url=cast(AnyHttpUrl, _base),
    resource_server_url=cast(AnyHttpUrl, _base + "/mcp"),
        validate_token_resource=True,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["mcp"], default_scopes=["mcp"]
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=["mcp"],
    ),
)


@server.tool(name="mcp_whoami", description="Zeigt den aktuell verbundenen Einsatzcockpit-Benutzer.")
async def whoami(ctx: Context) -> dict[str, object]:
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    if not token or not token.subject or not token.claims:
        raise MCPPermissionError("Nicht angemeldet.")
    db = SessionLocal()
    try:
        definition = TOOLS["mcp_whoami"]
        live_context = load_live_context(db, int(token.subject), int(token.claims["org_id"]), definition.required_roles)
        if definition.module_check and not definition.module_check(live_context.org_id, db):
            raise MCPPermissionError("Das für dieses Werkzeug nötige Modul ist nicht aktiviert.")
        return await definition.handler(live_context)
    finally:
        db.close()


def application():
    return server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True)
