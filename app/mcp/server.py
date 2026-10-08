"""DB-gestuetzter OAuth-Provider und Streamable-HTTP-MCP-Server."""

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
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
from mcp_types import ContentBlock, ImageContent, TextContent
from pydantic import AnyHttpUrl, AnyUrl

from app.config import settings
from app.core.security import hash_api_key
from app.db import SessionLocal
from app.mcp.context import MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.mcp.tools import fahrtenbuch as _fahrtenbuch  # noqa: F401 - registriert Fahrtenbuch-Tools
from app.mcp.tools import kontakt as _kontakt  # noqa: F401 - registriert Kontakt-Tools
from app.mcp.tools import objekt as _objekt  # noqa: F401 - registriert Objekt-Tools
from app.mcp.tools import objekt_dokumente as _objekt_dokumente  # noqa: F401 - registriert Dokument-Tools
from app.mcp.tools import organisation as _organisation  # noqa: F401 - registriert Organisations-Tool
from app.mcp.tools import strassensperren as _strassensperren  # noqa: F401 - registriert Strassensperren-Tools
from app.mcp.tools import wasserstelle as _wasserstelle  # noqa: F401 - registriert Wasserstellen-Tools
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


logger = logging.getLogger(__name__)
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
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        definition, context = _live_context_for_tool(tool_name)
    except MCPPermissionError as exc:
        raise ToolError(str(exc)) from exc
    try:
        return await definition.handler(context, **arguments)
    except (ValueError, MCPPermissionError) as exc:
        # Nur fachliche Meldungen an den Client geben; das SDK maskiert sonst jede Ausnahme.
        raise ToolError(str(exc)) from exc
    except Exception:
        logger.exception("MCP-Tool %s ist fehlgeschlagen", tool_name)
        raise
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
    name="organisation_lesen",
    description="Liest Stammdaten und Logo der eigenen Organisation (z. B. fuer Briefkoepfe oder Berichte).",
)
async def organisation_lesen(logo_als_bild: bool = True, ctx: Context | None = None) -> list[ContentBlock]:
    ergebnis = await _call_registered_tool("organisation_lesen", logo_als_bild=logo_als_bild)
    logo = ergebnis["logo"]
    assert isinstance(logo, dict)
    inhalt_base64 = logo.pop("inhalt_base64", None)
    inhalt: list[ContentBlock] = [
        TextContent(type="text", text=json.dumps(ergebnis, ensure_ascii=False, indent=2))
    ]
    if inhalt_base64 is not None:
        assert isinstance(inhalt_base64, str)
        mime = logo["mime"]
        assert isinstance(mime, str)
        inhalt.append(ImageContent(type="image", data=inhalt_base64, mime_type=mime))
    return inhalt


@server.tool(
    name="wasserstellen_suchen",
    description=(
        "Sucht Wasserstellen der eigenen Organisation. Typ-Codes: ueberflur, unterflur, saugstelle, "
        "loeschteich, loeschbehaelter, brunnen, relais, sonstige. Status: bereit, wartung, defekt "
        "(defekt = deaktiviert und nicht auf der Einsatzkarte). Löschen per MCP ist nicht möglich."
    ),
)
async def wasserstellen_suchen(
    q: str = "", typ: str = "", status: str = "", nur_aktive: bool = False,
    lat: float | None = None, lng: float | None = None, radius_m: int | None = None,
    limit: int = 50, seite: int = 1, ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "wasserstellen_suchen", q=q, typ=typ, status=status, nur_aktive=nur_aktive, lat=lat, lng=lng,
        radius_m=radius_m, limit=limit, seite=seite,
    )


@server.tool(
    name="wasserstelle_lesen",
    description=(
        "Liest eine Wasserstelle. Typ-Codes: ueberflur, unterflur, saugstelle, loeschteich, "
        "loeschbehaelter, brunnen, relais, sonstige; Status: bereit, wartung, defekt (deaktiviert). "
        "Löschen per MCP ist nicht möglich."
    ),
)
async def wasserstelle_lesen(wasserstelle_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("wasserstelle_lesen", wasserstelle_id=wasserstelle_id)


@server.tool(
    name="wasserstelle_anlegen",
    description=(
        "Legt eine Wasserstelle an und prüft Dubletten. Typ-Codes: ueberflur, unterflur, saugstelle, "
        "loeschteich, loeschbehaelter, brunnen, relais, sonstige. Status: bereit, wartung, defekt "
        "(deaktiviert). Löschen per MCP ist nicht möglich."
    ),
)
async def wasserstelle_anlegen(
    bezeichnung: str, typ: str, lat: float | None, lng: float | None, hinweis: str = "",
    ergiebigkeit_l_min: int | None = None, status: str = "bereit", duplikat_bestaetigt: bool = False,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "wasserstelle_anlegen", bezeichnung=bezeichnung, typ=typ, lat=lat, lng=lng, hinweis=hinweis,
        ergiebigkeit_l_min=ergiebigkeit_l_min, status=status, duplikat_bestaetigt=duplikat_bestaetigt,
    )


@server.tool(
    name="wasserstelle_aktualisieren",
    description=(
        "Aktualisiert nur felder mit erlaubten Schlüsseln: bezeichnung, typ, lat, lng, hinweis, "
        "ergiebigkeit_l_min, status. Typ-Codes: ueberflur, unterflur, saugstelle, loeschteich, "
        "loeschbehaelter, brunnen, relais, sonstige. Status: bereit, wartung, defekt (deaktiviert). "
        "Löschen per MCP ist nicht möglich."
    ),
)
async def wasserstelle_aktualisieren(
    wasserstelle_id: int, felder: dict, ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool("wasserstelle_aktualisieren", wasserstelle_id=wasserstelle_id, felder=felder)


@server.tool(
    name="wasserstelle_deaktivieren",
    description=(
        "Setzt Status defekt (deaktiviert, nicht auf der Einsatzkarte). Typ-Codes: ueberflur, unterflur, "
        "saugstelle, loeschteich, loeschbehaelter, brunnen, relais, sonstige; Status: bereit, wartung, defekt. "
        "Löschen per MCP ist nicht möglich."
    ),
)
async def wasserstelle_deaktivieren(
    wasserstelle_id: int, grund: str = "", ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool("wasserstelle_deaktivieren", wasserstelle_id=wasserstelle_id, grund=grund)


RestrictionTypeLiteral = Literal["closed", "partial", "construction", "one_way", "weight_limit", "height_limit", "width_limit", "residents_only", "difficult_passage", "other", ""]  # noqa: E501
RestrictionTypeValueLiteral = Literal["closed", "partial", "construction", "one_way", "weight_limit", "height_limit", "width_limit", "residents_only", "difficult_passage", "other"]  # noqa: E501
PriorityLiteral = Literal["low", "normal", "high", "critical"]
DirectionLiteral = Literal["both", "forward", "backward", ""]
ClosureStatusLiteral = Literal["current", "active", "planned", "expired", "cancelled", "all"]
GeometryFilterLiteral = Literal["", "pruefen", "ok", "fehlt"]


@server.tool(name="strassensperren_liste", description="Listet sichtbare Straßensperren. Einschränkungstypen: closed (Vollsperre), partial (Teilsperre), construction (Baustelle), one_way (Einbahn), weight_limit (Gewicht), height_limit (Höhe), width_limit (Breite), residents_only (Anrainer), difficult_passage (erschwert), other (sonstige).")  # noqa: E501
async def strassensperren_liste(status: ClosureStatusLiteral = "current", von: str = "", bis: str = "", strasse: str = "", restriction_type: RestrictionTypeLiteral = "", nur_eigene: bool = False, limit: int = 50, geometrie: GeometryFilterLiteral = "", ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperren_liste", status=status, von=von, bis=bis, strasse=strasse, restriction_type=restriction_type, nur_eigene=nur_eigene, limit=limit, geometrie=geometrie)  # noqa: E501


@server.tool(name="strassensperre_lesen", description="Liest alle Details einer sichtbaren Straßensperre.")
async def strassensperre_lesen(road_closure_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("strassensperre_lesen", road_closure_id=road_closure_id)


@server.tool(name="strassensperre_dokument_upload_vorbereiten", description="Bereitet einen kurzlebigen PDF-Upload für eine Verordnung vor; mit road_closure_id zum Anhängen, ohne für strassensperre_entwurf_aus_pdf.")  # noqa: E501
async def strassensperre_dokument_upload_vorbereiten(road_closure_id: int | None = None, dateiname: str = "verordnung.pdf", groesse_bytes: int | None = None, ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperre_dokument_upload_vorbereiten", road_closure_id=road_closure_id, dateiname=dateiname, groesse_bytes=groesse_bytes)  # noqa: E501


@server.tool(name="strassensperre_dokument_uebergeben", description="Hängt eine PDF-Verordnung an eine eigene Straßensperre an (inhalt_base64 oder upload_id) und liefert Textauszug und Felder-Entwurf.")  # noqa: E501
async def strassensperre_dokument_uebergeben(road_closure_id: int, dateiname: str, inhalt_base64: str | None = None, upload_id: str | None = None, ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperre_dokument_uebergeben", road_closure_id=road_closure_id, dateiname=dateiname, inhalt_base64=inhalt_base64, upload_id=upload_id)  # noqa: E501


@server.tool(name="strassensperre_entwurf_aus_pdf", description="Liest eine PDF-Verordnung ohne Speichern: Textauszug, Felder-Entwurf, Geometrievorschlag, ähnliche Sperren. Danach strassensperre_anlegen und strassensperre_dokument_uebergeben.")  # noqa: E501
async def strassensperre_entwurf_aus_pdf(inhalt_base64: str | None = None, upload_id: str | None = None, city: str = "", ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperre_entwurf_aus_pdf", inhalt_base64=inhalt_base64, upload_id=upload_id, city=city)  # noqa: E501


@server.tool(
    name="strassensperren_kataloge",
    description="Liefert erlaubte Werte, Labels und Aliase für Straßensperren.",
)
async def strassensperren_kataloge(ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("strassensperren_kataloge")


@server.tool(
    name="strassensperre_anlegen",
    description=(
        "Legt eine Sperre an oder schlägt bei Änderungen eine bestehende Sperre zum Aktualisieren oder Ersetzen vor."
    ),
)
async def strassensperre_anlegen(
    title: str,
    valid_from: str,
    restriction_type: RestrictionTypeValueLiteral,
    street: str = "",
    from_text: str = "",
    to_text: str = "",
    valid_until: str = "",
    description: str = "",
    direction: DirectionLiteral = "",
    priority: PriorityLiteral = "normal",
    max_weight_t: float | None = None,
    max_height_m: float | None = None,
    max_width_m: float | None = None,
    max_length_m: float | None = None,
    source: str = "",
    source_url: str = "",
    city: str = "",
    reference_number: str = "",
    exceptions: str = "",
    authority: str = "",
    geometry_geojson: dict | str | None = None,
    visible_for_org_ids: list[int] | None = None,
    duplikat_bestaetigt: bool = False,
    als_neu_bestaetigt: bool = False,
    ersetzt_road_closure_id: int | None = None,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "strassensperre_anlegen",
        title=title,
        valid_from=valid_from,
        restriction_type=restriction_type,
        street=street,
        from_text=from_text,
        to_text=to_text,
        valid_until=valid_until,
        description=description,
        direction=direction,
        priority=priority,
        max_weight_t=max_weight_t,
        max_height_m=max_height_m,
        max_width_m=max_width_m,
        max_length_m=max_length_m,
        source=source,
        source_url=source_url,
        city=city,
        reference_number=reference_number,
        exceptions=exceptions,
        authority=authority,
        geometry_geojson=geometry_geojson,
        visible_for_org_ids=visible_for_org_ids,
        duplikat_bestaetigt=duplikat_bestaetigt,
        als_neu_bestaetigt=als_neu_bestaetigt,
        ersetzt_road_closure_id=ersetzt_road_closure_id,
    )


@server.tool(
    name="strassensperre_aktualisieren",
    description="Aktualisiert eine eigene Sperre; geometry_geojson setzt geometry_status auf ok. Kein MCP-Löschen.",
)
async def strassensperre_aktualisieren(
    road_closure_id: int,
    felder: dict,
    version: int | None = None,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "strassensperre_aktualisieren",
        road_closure_id=road_closure_id,
        felder=felder,
        version=version,
    )


@server.tool(
    name="strassensperre_geometrie_ermitteln", description="Ermittelt einen OSM-Abschnitt zur Prüfung und Übernahme."
)
async def strassensperre_geometrie_ermitteln(road_closure_id: int | None = None, street: str = "", von: str = "", bis: str = "", city: str = "", uebernehmen: bool = False, ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperre_geometrie_ermitteln", road_closure_id=road_closure_id, street=street, von=von, bis=bis, city=city, uebernehmen=uebernehmen)  # noqa: E501


@server.tool(
    name="strassensperre_geometrie_bestaetigen", description="Bestätigt eine Geometrie für die Umfahrungsberechnung."
)
async def strassensperre_geometrie_bestaetigen(road_closure_id: int, geometry_geojson: dict | str | None = None, version: int | None = None, ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperre_geometrie_bestaetigen", road_closure_id=road_closure_id, geometry_geojson=geometry_geojson, version=version)  # noqa: E501


@server.tool(
    name="strassensperre_deaktivieren",
    description="Deaktiviert eine eigene Sperre. Eine harte Löschung per MCP ist nicht möglich.",
)
async def strassensperre_deaktivieren(
    road_closure_id: int, grund: str, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool("strassensperre_deaktivieren", road_closure_id=road_closure_id, grund=grund)


@server.tool(
    name="strassensperre_reaktivieren",
    description="Reaktiviert eine eigene Sperre. Eine harte Löschung per MCP ist nicht möglich.",
)
async def strassensperre_reaktivieren(
    road_closure_id: int, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool("strassensperre_reaktivieren", road_closure_id=road_closure_id)


@server.tool(name="strassensperren_suchen", description="Sucht sichtbare Straßensperren nach Worten in Titel, Straße, Beschreibung und Abschnitt; status wie bei strassensperren_liste.")  # noqa: E501
async def strassensperren_suchen(suchtext: str, status: str = "all", von: str = "", bis: str = "", limit: int = 20, ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperren_suchen", suchtext=suchtext, status=status, von=von, bis=bis, limit=limit)  # noqa: E501


@server.tool(name="strassensperren_im_gebiet", description="Findet sichtbare Sperren im Radius; radius_m 50 bis 20000, status: current, active, planned, expired, cancelled oder all.")  # noqa: E501
async def strassensperren_im_gebiet(lat: float, lng: float, radius_m: int = 2000, status: str = "current", ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperren_im_gebiet", lat=lat, lng=lng, radius_m=radius_m, status=status)


@server.tool(name="einsatz_strassensperren", description="Liest die gespeicherten Sperren einer sichtbaren Einsatz-Anfahrt.")  # noqa: E501
async def einsatz_strassensperren(incident_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("einsatz_strassensperren", incident_id=incident_id)


@server.tool(name="einsatz_anfahrtsroute_pruefen", description="Prüft die gespeicherte Einsatzroute oder berechnet live mit incident_id oder lat und lng; Fahrzeugprofile werden noch nicht berücksichtigt.")  # noqa: E501
async def einsatz_anfahrtsroute_pruefen(incident_id: int | None = None, lat: float | None = None, lng: float | None = None, vehicle_id: int | None = None, ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("einsatz_anfahrtsroute_pruefen", incident_id=incident_id, lat=lat, lng=lng, vehicle_id=vehicle_id)  # noqa: E501


@server.tool(name="strassensperren_entlang_route", description="Berechnet sichtbare Sperren entlang einer freien Start-Ziel-Route; Fahrzeugprofile werden noch nicht berücksichtigt.")  # noqa: E501
async def strassensperren_entlang_route(start_lat: float, start_lng: float, ziel_lat: float, ziel_lng: float, vehicle_id: int | None = None, ctx: Context | None = None) -> dict[str, object]:  # noqa: E501
    return await _call_registered_tool("strassensperren_entlang_route", start_lat=start_lat, start_lng=start_lng, ziel_lat=ziel_lat, ziel_lng=ziel_lng, vehicle_id=vehicle_id)  # noqa: E501


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


@server.tool(
    name="objekt_lesen",
    description=(
        "Liefert Stammdaten inkl. informationen (allgemeiner Hinweistext) und anfahrtsweg, BMA, Gefahren mit "
        "Details, Merkmale mit Hinweis, Zusatzadressen, Wohnanlage und Kontakt-Zuordnungen ohne Kontakt-Klartext."
    ),
)
async def objekt_lesen(
    objekt_id: int, arbeitskopie: bool = False, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool("objekt_lesen", objekt_id=objekt_id, arbeitskopie=arbeitskopie)


@server.tool(name="kontakt_suchen", description="Sucht zentrale Kontakte der eigenen Organisation.")
async def kontakt_suchen(
    q: str = "",
    typ: str = "all",
    kategorie: int | None = None,
    limit: int = 25,
    seite: int = 1,
    organisation_id: int | None = None,
    funktion: str = "",
    quelle: str = "",
    externe_id: str = "",
    aktiv: bool | None = None,
    aktualisiert_seit: str = "",
    vollstaendig: bool = False,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_suchen", q=q, typ=typ, kategorie=kategorie, limit=limit, seite=seite,
        organisation_id=organisation_id, funktion=funktion, quelle=quelle, externe_id=externe_id,
        aktiv=aktiv, aktualisiert_seit=aktualisiert_seit, vollstaendig=vollstaendig,
    )


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


@server.tool(name="kontakt_duplikate_pruefen", description="Prüft mögliche Kontakt-Dubletten.")
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


@server.tool(name="kontakt_lesen", description="Liest einen zentralen Kontakt mit seinen Objektzuordnungen.")
async def kontakt_lesen(kontakt_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("kontakt_lesen", kontakt_id=kontakt_id)


@server.tool(name="kontakt_kategorien", description="Listet die Kontaktkategorien der eigenen Organisation.")
async def kontakt_kategorien(ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("kontakt_kategorien")


@server.tool(
    name="kontakt_anlegen",
    description=(
        "Legt einen zentralen Kontakt an: felder={typ: person|stelle, anzeigename oder vorname+nachname, "
        "organisation?, funktion?, email?, erreichbarkeit?, notizen?}; telefone=[{nummer, label?, sort?, "
        "bevorzugt?, sms_eignung?}]. Moegliche Dubletten oder ungueltige Eingaben werden als ToolError gemeldet; "
        "mit duplikat_bestaetigt=true eine bekannte Dublette trotzdem anlegen."
    ),
)
async def kontakt_anlegen(
    felder: dict,
    telefone: list[dict] | None = None,
    kategorien: list[str] | None = None,
    duplikat_bestaetigt: bool = False,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_anlegen",
        felder=felder,
        telefone=telefone,
        kategorien=kategorien,
        duplikat_bestaetigt=duplikat_bestaetigt,
    )


@server.tool(name="kontakt_aktualisieren", description="Aktualisiert einen zentralen Kontakt mit Versionsschutz.")
async def kontakt_aktualisieren(
    kontakt_id: int,
    version: int,
    felder: dict | None = None,
    telefone: list[dict] | None = None,
    kategorien: list[str] | None = None,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_aktualisieren",
        kontakt_id=kontakt_id,
        version=version,
        felder=felder,
        telefone=telefone,
        kategorien=kategorien,
    )


@server.tool(name="kontakt_archivieren", description="Archiviert einen zentralen Kontakt.")
async def kontakt_archivieren(
    kontakt_id: int, bestaetigt: bool = False, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool("kontakt_archivieren", kontakt_id=kontakt_id, bestaetigt=bestaetigt)


@server.tool(name="kontakt_zusammenfuehren", description="Führt zwei zentrale Kontakte zusammen.")
async def kontakt_zusammenfuehren(
    quelle_id: int,
    ziel_id: int,
    feldwahl: dict[str, str] | None = None,
    bestaetigt: bool = False,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_zusammenfuehren",
        quelle_id=quelle_id,
        ziel_id=ziel_id,
        feldwahl=feldwahl,
        bestaetigt=bestaetigt,
    )


@server.tool(name="kontakt_organisation_suchen", description="Sucht Organisationen und Ansprechpartner.")
async def kontakt_organisation_suchen(q: str = "", limit: int = 25, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("kontakt_organisation_suchen", q=q, limit=limit)


@server.tool(name="kontakt_organisation_upsert", description="Legt eine Organisation an oder aktualisiert sie.")
async def kontakt_organisation_upsert(organisation: dict, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("kontakt_organisation_upsert", organisation=organisation)


@server.tool(name="kontakt_funktion_zuordnen", description="Ordnet einen Kontakt einer Organisationsfunktion zu.")
async def kontakt_funktion_zuordnen(
    kontakt_id: int, organisation: dict, funktion: dict, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_funktion_zuordnen", kontakt_id=kontakt_id, organisation=organisation, funktion=funktion
    )


@server.tool(name="kontakt_bulk_upsert", description="Validiert oder führt strukturierte Kontakt-Massenänderungen aus.")
async def kontakt_bulk_upsert(
    kontakte: list[dict],
    modus: str = "merge",
    dry_run: bool = True,
    idempotency_key: str = "",
    bestaetigt: bool = False,
    quelle: str = "MCP",
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_bulk_upsert",
        kontakte=kontakte,
        modus=modus,
        dry_run=dry_run,
        idempotency_key=idempotency_key,
        bestaetigt=bestaetigt,
        quelle=quelle,
    )


@server.tool(name="kontakt_import_vorschau", description="Erstellt eine schreibfreie Kontaktimport-Vorschau.")
async def kontakt_import_vorschau(
    quelle: str,
    kontakte: list[dict],
    quellendatum: str = "",
    quellendokument: str = "",
    importmodus: str = "merge",
    organisationen: list[dict] | None = None,
    optionen: dict | None = None,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_import_vorschau",
        quelle=quelle,
        kontakte=kontakte,
        quellendatum=quellendatum,
        quellendokument=quellendokument,
        importmodus=importmodus,
        organisationen=organisationen,
        optionen=optionen,
    )


@server.tool(name="kontakt_import_ausfuehren", description="Führt eine bestätigte Kontaktimport-Vorschau aus.")
async def kontakt_import_ausfuehren(
    preview_id: int,
    bestaetigte_konfliktentscheidungen: dict | None = None,
    idempotency_key: str = "",
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "kontakt_import_ausfuehren",
        preview_id=preview_id,
        bestaetigte_konfliktentscheidungen=bestaetigte_konfliktentscheidungen,
        idempotency_key=idempotency_key,
    )


@server.tool(name="kontakt_import_status", description="Liest den Status eines Kontaktimport-Batches.")
async def kontakt_import_status(batch_id: int, ctx: Context | None = None) -> dict[str, object]:
    return await _call_registered_tool("kontakt_import_status", batch_id=batch_id)


@server.tool(
    name="objekt_anlegen",
    description=(
        "Legt ausschliesslich einen Objekt-Entwurf an. Stammdaten: name, vulgoname, kategorie_id, strasse, "
        "hausnummer, plz, ort, lat, lng, informationen (allgemeiner Hinweistext), anfahrtsweg, revision_datum "
        "(YYYY-MM-DD). merkmale=[{merkmal_id, hinweis?}]; gefahren=[{gefahr_id, un_nummer?, stoffname?, "
        "gefahrklasse?, gefahrnummer?, detail?}]; bma: Felder wie objekt_lesen.bma; wohnanlage={vorhanden?, "
        "wohneinheiten, geschosse, stiegen, hausverwaltung_kontakt_id, hinweise}. Kontakte: "
        "kontakte=[{art, kontakt_id} oder "
        "{art, neu:{anzeigename|vorname+nachname, organisation, funktion, email, telefone:[{nummer,label}]}} "
        "oder flach {art, vorname, nachname, telefon, mobil, email}]; art aus objekt_kataloge (Kontaktarten). "
        "Moegliche Kontakt-Dubletten oder ungueltige Kontaktfelder werden als ToolError gemeldet; mit "
        "duplikat_bestaetigt=true bekannte Dubletten trotzdem anlegen."
    ),
)
async def objekt_anlegen(
    stammdaten: dict,
    bma: dict | None = None,
    gefahren: list[dict] | None = None,
    merkmale: list[dict] | None = None,
    zusatzadressen: list[dict] | None = None,
    kontakte: list[dict] | None = None,
    wohnanlage: dict | None = None,
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
        wohnanlage=wohnanlage,
        duplikat_bestaetigt=duplikat_bestaetigt,
    )


@server.tool(
    name="objekt_aktualisieren",
    description=(
        "Aktualisiert einen Objektentwurf oder eine Arbeitskopie ohne Freigabe; objekt_id darf die Basis- oder "
        "Arbeitskopie-ID sein. Stammdaten: name, vulgoname, kategorie_id, strasse, hausnummer, plz, ort, lat, "
        "lng, informationen (allgemeiner Hinweistext), anfahrtsweg, revision_datum (YYYY-MM-DD). "
        "merkmale_hinzufuegen=[{merkmal_id, hinweis?}], merkmale_aendern=[{id, hinweis}]; "
        "gefahren_hinzufuegen=[{gefahr_id, un_nummer?, stoffname?, gefahrklasse?, gefahrnummer?, detail?}], "
        "gefahren_aendern=[{id, un_nummer?, stoffname?, gefahrklasse?, gefahrnummer?, detail?, links?:[{label,url}]}]. "
        "bma: Felder wie objekt_lesen.bma, vorhanden=false entfernt sie; wohnanlage={vorhanden?, wohneinheiten, "
        "geschosse, stiegen, hausverwaltung_kontakt_id, hinweise}. kontakte_hinzufuegen: wie "
        "kontakte bei objekt_anlegen ({art, kontakt_id}, {art, neu:{anzeigename|vorname+nachname, organisation, "
        "funktion, email, telefone:[{nummer,label}]}}, oder flach {art, vorname, nachname, telefon, mobil, email}). "
        "kontakte_entfernen: [zuordnung_id] oder [{kontakt_id, art?}] (IDs aus objekt_lesen, bei Arbeitskopie mit "
        "arbeitskopie=true; Basis-Zuordnungs-IDs werden aufgeloest). kontakte_aendern: "
        "[{zuordnung_id, art?, sort?, erreichbarkeit?}]. Moegliche "
        "Kontakt-Dubletten oder ungueltige Kontaktfelder werden als ToolError gemeldet; mit "
        "duplikat_bestaetigt=true bekannte Dubletten trotzdem anlegen. Benoetigt das Kontakte-Modul."
    ),
)
async def objekt_aktualisieren(
    objekt_id: int,
    stammdaten: dict | None = None,
    bma: dict | None = None,
    gefahren_hinzufuegen: list[dict] | None = None,
    gefahren_entfernen: list[int | dict] | None = None,
    gefahren_aendern: list[dict] | None = None,
    merkmale_hinzufuegen: list[dict] | None = None,
    merkmale_entfernen: list[int | dict] | None = None,
    merkmale_aendern: list[dict] | None = None,
    zusatzadressen_hinzufuegen: list[dict] | None = None,
    zusatzadressen_entfernen: list[int | dict] | None = None,
    kontakte_hinzufuegen: list[dict] | None = None,
    kontakte_entfernen: list[int | dict] | None = None,
    kontakte_aendern: list[dict] | None = None,
    wohnanlage: dict | None = None,
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
        gefahren_aendern=gefahren_aendern,
        merkmale_hinzufuegen=merkmale_hinzufuegen,
        merkmale_entfernen=merkmale_entfernen,
        merkmale_aendern=merkmale_aendern,
        zusatzadressen_hinzufuegen=zusatzadressen_hinzufuegen,
        zusatzadressen_entfernen=zusatzadressen_entfernen,
        kontakte_hinzufuegen=kontakte_hinzufuegen,
        kontakte_entfernen=kontakte_entfernen,
        kontakte_aendern=kontakte_aendern,
        wohnanlage=wohnanlage,
        duplikat_bestaetigt=duplikat_bestaetigt,
    )


@server.tool(
    name="objekt_dokument_uebergeben",
    description=(
        "Uebergibt ein PDF an ein Objekt. Genau eines von inhalt_base64 oder upload_id angeben. "
        "Fuer upload_id zuerst objekt_dokument_upload_vorbereiten aufrufen, dann die Datei mit dessen curl-Beispiel "
        "hochladen. seiten ist optional: [{\"nr\":1,\"dokumentart\":\"bma_datenblatt\",\"titel\":null}]; "
        "optional sind volltext, melderlinien, stand (YYYY-MM-DD), bei_einsatz_drucken. Fehlende Seiten werden "
        "serverseitig zur KI-Vorschlagsklassifizierung vorgemerkt. Korrekturen erfolgen ueber "
        "objekt_dokument_seiten_klassifizieren."
    ),
)
async def objekt_dokument_uebergeben(
    objekt_id: int,
    dateiname: str,
    inhalt_base64: str | None = None,
    upload_id: str | None = None,
    seiten: list[dict] | None = None,
    ersetzt_dokument_id: int | None = None,
    ctx: Context | None = None,
) -> dict[str, object]:
    return await _call_registered_tool(
        "objekt_dokument_uebergeben",
        objekt_id=objekt_id,
        dateiname=dateiname,
        inhalt_base64=inhalt_base64,
        upload_id=upload_id,
        seiten=seiten,
        ersetzt_dokument_id=ersetzt_dokument_id,
    )


@server.tool(
    name="objekt_dokument_upload_vorbereiten",
    description="Bereitet einen kurzlebigen Bearer-Upload fuer ein Objekt-PDF vor.",
)
async def objekt_dokument_upload_vorbereiten(
    objekt_id: int, dateiname: str, groesse_bytes: int | None = None, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool(
        "objekt_dokument_upload_vorbereiten",
        objekt_id=objekt_id,
        dateiname=dateiname,
        groesse_bytes=groesse_bytes,
    )


@server.tool(
    name="objekt_dokument_herunterladen",
    description=(
        "Liefert einen kurzlebigen Download-Link fuer ein Objektdokument. dokument_id kommt aus "
        "objekt_dokumente_auflisten; optional seite fuer eine Einzelseite. Der Link ist ca. 15 Minuten "
        "gueltig und im Browser klickbar. inline=True nur fuer kleine Dateien verwenden."
    ),
)
async def objekt_dokument_herunterladen(
    dokument_id: int, seite: int | None = None, inline: bool = False, ctx: Context | None = None
) -> dict[str, object]:
    return await _call_registered_tool(
        "objekt_dokument_herunterladen", dokument_id=dokument_id, seite=seite, inline=inline
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
