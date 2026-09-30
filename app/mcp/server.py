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
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl

from app.config import settings
from app.core.security import hash_api_key
from app.db import SessionLocal
from app.mcp.context import MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.mcp.tools import fahrtenbuch as _fahrtenbuch  # noqa: F401 - registriert Fahrtenbuch-Tools
from app.mcp.tools import objekt as _objekt  # noqa: F401 - registriert Objekt-Tools
from app.mcp.tools import objekt_dokumente as _objekt_dokumente  # noqa: F401 - registriert Dokument-Tools
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
        # DCR liefert ausschliesslich oeffentliche PKCE-Clients. Das SDK mintet bei
        # fehlendem oder client_secret_*-Verfahren ein Geheimnis; das wird hier weder
        # gespeichert noch ausgegeben: der Client wird als "none" registriert (die
        # Antwort spiegelt das), Sicherheit kommt aus PKCE S256 + Login + Redirect-URI.
        client_info.token_endpoint_auth_method = "none"
        client_info.client_secret = None
        client_info.client_secret_expires_at = None
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


def _live_context_for_tool(tool_name: str):
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    if not token or not token.subject or not token.claims:
        raise MCPPermissionError("Nicht angemeldet.")
    definition = TOOLS[tool_name]
    db = SessionLocal()
    try:
        context = load_live_context(db, int(token.subject), int(token.claims["org_id"]), definition.required_roles)
        if definition.module_check and not definition.module_check(context.org_id, db):
            raise MCPPermissionError("Das Modul für dieses Werkzeug ist für diese Organisation nicht aktiviert.")
        return definition, context
    except Exception:
        db.close()
        raise


async def _call_registered_tool(tool_name: str, **arguments) -> dict[str, object]:
    definition, context = _live_context_for_tool(tool_name)
    try:
        return await definition.handler(context, **arguments)
    finally:
        context.db.close()


class EinsatzcockpitMCPServer(MCPServer):
    async def list_tools(self):
        """Blendet Werkzeuge aus, deren Live-Rechte oder Modul fehlen."""
        tools = await super().list_tools()
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if not token or not token.subject or not token.claims:
            return []
        db = SessionLocal()
        try:
            visible = set()
            for name, definition in TOOLS.items():
                try:
                    context = load_live_context(
                        db, int(token.subject), int(token.claims["org_id"]), definition.required_roles
                    )
                    if not definition.module_check or definition.module_check(context.org_id, db):
                        visible.add(name)
                except MCPPermissionError:
                    continue
            return [tool for tool in tools if tool.name in visible]
        finally:
            db.close()


_base = settings.effective_public_base_url.rstrip("/")
server = EinsatzcockpitMCPServer(
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
    return await _call_registered_tool("mcp_whoami")


@server.tool(
    name="fahrtenbuch_stammdaten",
    description="Liest sichere Fahrtenbuch-Stammdaten der eigenen Organisation.",
)
async def fahrtenbuch_stammdaten(ctx: Context) -> dict[str, object]:
    return await _call_registered_tool("fahrtenbuch_stammdaten")


@server.tool(name="fahrtenbuch_fahrten", description="Listet Fahrten der eigenen Organisation.")
async def fahrtenbuch_fahrten(
    von: str = "",
    bis: str = "",
    fahrzeug_id: int = 0,
    kategorie: str = "",
    zweck_id: int = 0,
    status: str = "aktiv",
    fahrer: str = "",
    nur_statistikrelevant: bool = False,
    limit: int = 50,
    seite: int = 1,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "fahrtenbuch_fahrten",
        von=von,
        bis=bis,
        fahrzeug_id=fahrzeug_id,
        kategorie=kategorie,
        zweck_id=zweck_id,
        status=status,
        fahrer=fahrer,
        nur_statistikrelevant=nur_statistikrelevant,
        limit=limit,
        seite=seite,
    )


@server.tool(name="fahrtenbuch_fahrt", description="Liest eine Fahrt samt Korrekturkette.")
async def fahrtenbuch_fahrt(fahrt_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("fahrtenbuch_fahrt", fahrt_id=fahrt_id)


@server.tool(name="fahrtenbuch_auswertung", description="Wertet aktive, statistikrelevante Fahrten aus.")
async def fahrtenbuch_auswertung(
    von: str = "",
    bis: str = "",
    gruppierung: str = "fahrzeug",
    fahrzeug_id: int = 0,
    kategorie: str = "",
    zweck_id: int = 0,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "fahrtenbuch_auswertung",
        von=von,
        bis=bis,
        gruppierung=gruppierung,
        fahrzeug_id=fahrzeug_id,
        kategorie=kategorie,
        zweck_id=zweck_id,
    )


@server.tool(name="objekt_kataloge", description="Liest gueltige Objekt-Katalogwerte.")
async def objekt_kataloge(ctx: Context) -> dict[str, object]:
    return await _call_registered_tool("objekt_kataloge")


@server.tool(name="objekt_suchen", description="Sucht Objekte der eigenen Organisation.")
async def objekt_suchen(
    q: str = "", status: str = "", limit: int = 25, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool("objekt_suchen", q=q, status=status, limit=limit)


@server.tool(name="objekt_lesen", description="Liest ein Objekt ohne Kontakt-Klartextdaten.")
async def objekt_lesen(objekt_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("objekt_lesen", objekt_id=objekt_id)


@server.tool(name="kontakt_suchen", description="Sucht zentrale Kontakte ohne Telefon oder E-Mail.")
async def kontakt_suchen(q: str = "", limit: int = 25, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("kontakt_suchen", q=q, limit=limit)


@server.tool(name="objekt_duplikate_pruefen", description="Prueft moegliche Objekt-Dubletten.")
async def objekt_duplikate_pruefen(
    name: str = "",
    strasse: str = "",
    hausnummer: str = "",
    plz: str = "",
    ort: str = "",
    bma_nummer: str = "",
    rfl_nummer: str = "",
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "objekt_duplikate_pruefen",
        name=name,
        strasse=strasse,
        hausnummer=hausnummer,
        plz=plz,
        ort=ort,
        bma_nummer=bma_nummer,
        rfl_nummer=rfl_nummer,
    )


@server.tool(name="kontakt_duplikate_pruefen", description="Prueft moegliche Kontakt-Dubletten.")
async def kontakt_duplikate_pruefen(
    anzeigename: str,
    organisation: str = "",
    email: str = "",
    telefone: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_duplikate_pruefen", anzeigename=anzeigename, organisation=organisation, email=email, telefone=telefone
    )


@server.tool(name="objekt_anlegen", description="Legt ausschliesslich einen Objekt-Entwurf an.")
async def objekt_anlegen(
    stammdaten: dict,
    bma: dict | None = None,
    gefahren: list[dict] | None = None,
    merkmale: list[dict] | None = None,
    zusatzadressen: list[dict] | None = None,
    kontakte: list[dict] | None = None,
    duplikat_bestaetigt: bool = False,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "objekt_anlegen",
        stammdaten=stammdaten,
        bma=bma,
        gefahren=gefahren,
        merkmale=merkmale,
        zusatzadressen=zusatzadressen,
        kontakte=kontakte,
        duplikat_bestaetigt=duplikat_bestaetigt,
    )


@server.tool(
    name="objekt_aktualisieren",
    description="Aktualisiert einen Objektentwurf oder eine Arbeitskopie ohne Freigabe.",
)
async def objekt_aktualisieren(
    objekt_id: int,
    stammdaten: dict | None = None,
    bma: dict | None = None,
    gefahren_hinzufuegen: list[dict] | None = None,
    gefahren_entfernen: list[int | dict] | None = None,
    merkmale_hinzufuegen: list[dict] | None = None,
    merkmale_entfernen: list[int | dict] | None = None,
    zusatzadressen_hinzufuegen: list[dict] | None = None,
    zusatzadressen_entfernen: list[int | dict] | None = None,
    kontakte_hinzufuegen: list[dict] | None = None,
    kontakte_entfernen: list[int | dict] | None = None,
    duplikat_bestaetigt: bool = False,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "objekt_aktualisieren",
        objekt_id=objekt_id,
        stammdaten=stammdaten,
        bma=bma,
        gefahren_hinzufuegen=gefahren_hinzufuegen,
        gefahren_entfernen=gefahren_entfernen,
        merkmale_hinzufuegen=merkmale_hinzufuegen,
        merkmale_entfernen=merkmale_entfernen,
        zusatzadressen_hinzufuegen=zusatzadressen_hinzufuegen,
        zusatzadressen_entfernen=zusatzadressen_entfernen,
        kontakte_hinzufuegen=kontakte_hinzufuegen,
        kontakte_entfernen=kontakte_entfernen,
        duplikat_bestaetigt=duplikat_bestaetigt,
    )


@server.tool(name="objekt_dokument_uebergeben", description="Uebergibt ein fertig analysiertes PDF an ein Objekt.")
async def objekt_dokument_uebergeben(
    objekt_id: int,
    dateiname: str,
    inhalt_base64: str,
    seiten: list[dict],
    ersetzt_dokument_id: int | None = None,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "objekt_dokument_uebergeben",
        objekt_id=objekt_id,
        dateiname=dateiname,
        inhalt_base64=inhalt_base64,
        seiten=seiten,
        ersetzt_dokument_id=ersetzt_dokument_id,
    )


@server.tool(name="objekt_dokumente_auflisten", description="Listet Dokumente und Seiten eines Objekts.")
async def objekt_dokumente_auflisten(objekt_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("objekt_dokumente_auflisten", objekt_id=objekt_id)


@server.tool(
    name="objekt_dokument_seiten_klassifizieren", description="Korrigiert die Klassifizierung von Dokumentseiten."
)
async def objekt_dokument_seiten_klassifizieren(
    dokument_id: int,
    seiten: list[dict],
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool("objekt_dokument_seiten_klassifizieren", dokument_id=dokument_id, seiten=seiten)


def _metadata_route_mit_public_clients(routes: list) -> None:
    """Ergaenzt "none" in den Auth-Methoden der Authorization-Server-Metadaten.

    Das SDK bewirbt fest nur client_secret_*; Clients, die sich an den Metadaten
    orientieren, wuerden sich sonst als vertrauliche Clients registrieren wollen.
    """
    from mcp.server.auth.handlers.metadata import MetadataHandler
    from mcp.server.auth.routes import build_metadata, cors_middleware
    from starlette.routing import Route

    metadata = build_metadata(
        cast(AnyHttpUrl, _base),
        None,
        ClientRegistrationOptions(enabled=True, valid_scopes=["mcp"], default_scopes=["mcp"]),
        RevocationOptions(enabled=True),
    )
    metadata.token_endpoint_auth_methods_supported = ["none"]
    metadata.revocation_endpoint_auth_methods_supported = ["none"]
    for index, route in enumerate(routes):
        if getattr(route, "path", None) == "/.well-known/oauth-authorization-server":
            routes[index] = Route(
                "/.well-known/oauth-authorization-server",
                endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            )


def application():
    # Das SDK aktiviert den DNS-Rebinding-Schutz automatisch nur fuer localhost und
    # wuerde jeden anderen Host-Header (z. B. hinter nginx) mit 421 abweisen. Der
    # Schutz zielt auf unauthentifizierte lokale Server; dieser Endpunkt ist oeffentlich
    # und verlangt ein Bearer-Token mit Live-Rechtepruefung, daher bewusst aus.
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    _metadata_route_mit_public_clients(app.routes)
    return app
