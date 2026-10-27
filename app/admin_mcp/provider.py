"""Eigene OAuth-Persistenz, PKCE und absolut begrenzte Token-Familien."""
import secrets
from datetime import UTC, timedelta
from typing import cast
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from app.admin_mcp.context import REFRESH_LIFETIME, load_live_context
from app.admin_mcp.context import admin_session as SessionLocal
from app.config import settings
from app.core.security import hash_api_key
from app.models.admin_mcp import AdminMCPOAuthClient, AdminMCPOAuthCode, AdminMCPOAuthToken
from app.models.admin_security import utcnow

ACCESS_LIFETIME = timedelta(minutes=15)
RESOURCE = settings.effective_public_base_url.rstrip("/") + "/mcp/admin"


def raw() -> str:
    return secrets.token_urlsafe(32)


def revoke_user(db, user_id: int) -> None:
    for row in db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.user_id == user_id,
                                                AdminMCPOAuthToken.revoked_at.is_(None)):
        row.revoked_at = utcnow()


class AdminOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with SessionLocal() as db:
            row = db.query(AdminMCPOAuthClient).filter(AdminMCPOAuthClient.client_id == client_id).first()
            return OAuthClientInformationFull.model_validate_json(row.metadata_json) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client_info.token_endpoint_auth_method = "none"
        client_info.client_secret = None
        client_info.client_secret_expires_at = None
        with SessionLocal() as db:
            db.add(AdminMCPOAuthClient(client_id=client_info.client_id, client_secret_hash=None,
                                      metadata_json=client_info.model_dump_json()))
            db.commit()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if params.resource != RESOURCE or params.scopes != ["mcp-admin"]:
            raise AuthorizeError("invalid_target", "Ungueltige Resource oder Scope")
        flow = raw()
        with SessionLocal() as db:
            db.add(AdminMCPOAuthCode(code_hash=hash_api_key(flow), client_id=client.client_id,
                redirect_uri=str(params.redirect_uri), code_challenge=params.code_challenge,
                scopes="mcp-admin", state=params.state, resource=RESOURCE,
                expires_at=utcnow() + timedelta(minutes=5)))
            db.commit()
        return "/mcp/admin/anmelden?" + urlencode({"vorgang": flow})

    async def load_authorization_code(self, client, authorization_code: str) -> AuthorizationCode | None:
        with SessionLocal() as db:
            row = db.query(AdminMCPOAuthCode).filter(
                AdminMCPOAuthCode.code_hash == hash_api_key(authorization_code)).first()
            if (not row or not row.user_id or row.client_id != client.client_id
                    or row.consumed_at or row.expires_at <= utcnow() or row.resource != RESOURCE):
                return None
            try:
                load_live_context(db, row)
            except PermissionError:
                return None
            return AuthorizationCode(code=authorization_code, scopes=["mcp-admin"],
                expires_at=row.expires_at.replace(tzinfo=UTC).timestamp(), client_id=row.client_id,
                code_challenge=row.code_challenge, redirect_uri=cast(AnyUrl, row.redirect_uri),
                redirect_uri_provided_explicitly=True, resource=RESOURCE, subject=str(row.user_id))

    def _issue(self, db, row, family: str, family_expires_at) -> OAuthToken:
        access, refresh = raw(), raw()
        for value, kind, expiry in ((access, "access", min(utcnow() + ACCESS_LIFETIME, family_expires_at)),
                                    (refresh, "refresh", family_expires_at)):
            db.add(AdminMCPOAuthToken(token_hash=hash_api_key(value), token_type=kind,
                client_id=row.client_id, user_id=row.user_id, session_epoch=row.session_epoch,
                mfa_at=row.mfa_at, scopes="mcp-admin", family_id=family,
                family_expires_at=family_expires_at, expires_at=expiry))
        return OAuthToken(access_token=access, refresh_token=refresh,
            expires_in=max(0, int(min(ACCESS_LIFETIME, family_expires_at - utcnow()).total_seconds())),
            scope="mcp-admin")

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        with SessionLocal() as db:
            row = db.query(AdminMCPOAuthCode).filter(
                AdminMCPOAuthCode.code_hash == hash_api_key(authorization_code.code)).with_for_update().first()
            if (not row or not row.user_id or row.client_id != client.client_id or row.consumed_at
                    or row.expires_at <= utcnow() or row.resource != RESOURCE):
                raise TokenError("invalid_grant", "Ungueltiger Code")
            try:
                load_live_context(db, row)
            except PermissionError as exc:
                raise TokenError("invalid_grant", "Ungueltiger Code") from exc
            row.consumed_at = utcnow()
            result = self._issue(db, row, raw(), utcnow() + REFRESH_LIFETIME)
            db.commit()
            return result

    def _valid(self, db, row, client_id: str | None = None) -> bool:
        if (not row or row.revoked_at or row.expires_at <= utcnow() or row.family_expires_at <= utcnow()
                or (client_id is not None and row.client_id != client_id)):
            return False
        try:
            load_live_context(db, row)
        except PermissionError:
            return False
        return True

    async def load_refresh_token(self, client, refresh_token: str) -> RefreshToken | None:
        with SessionLocal() as db:
            row = db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.token_hash == hash_api_key(refresh_token),
                                                     AdminMCPOAuthToken.token_type == "refresh").first()
            if row is None or not self._valid(db, row, client.client_id):
                return None
            return RefreshToken(token=refresh_token, client_id=row.client_id, scopes=["mcp-admin"],
                expires_at=int(row.expires_at.replace(tzinfo=UTC).timestamp()), resource=RESOURCE,
                subject=str(row.user_id))

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        with SessionLocal() as db:
            row = db.query(AdminMCPOAuthToken).filter(
                AdminMCPOAuthToken.token_hash == hash_api_key(refresh_token.token),
                AdminMCPOAuthToken.token_type == "refresh").with_for_update().first()
            if row is None or not self._valid(db, row, client.client_id) or scopes != ["mcp-admin"]:
                raise TokenError("invalid_grant", "Ungueltiger Refresh-Token")
            for related in db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.family_id == row.family_id):
                related.revoked_at = utcnow()
            result = self._issue(db, row, row.family_id, row.family_expires_at)
            db.commit()
            return result

    async def load_access_token(self, token: str) -> AccessToken | None:
        with SessionLocal() as db:
            row = db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.token_hash == hash_api_key(token),
                                                     AdminMCPOAuthToken.token_type == "access").first()
            if row is None or not self._valid(db, row):
                return None
            return AccessToken(token=token, client_id=row.client_id, scopes=["mcp-admin"],
                expires_at=int(row.expires_at.replace(tzinfo=UTC).timestamp()), resource=RESOURCE,
                subject=str(row.user_id))

    async def revoke_token(self, token) -> None:
        with SessionLocal() as db:
            row = db.query(AdminMCPOAuthToken).filter(
                AdminMCPOAuthToken.token_hash == hash_api_key(token.token)).first()
            if row:
                for related in db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.family_id == row.family_id):
                    related.revoked_at = utcnow()
                db.commit()


provider = AdminOAuthProvider()
