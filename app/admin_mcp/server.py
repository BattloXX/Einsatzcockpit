"""Separater SDK-Server mit eigenen OAuth-/Discovery-Pfaden."""
import asyncio
import json
from typing import cast
from uuid import uuid4

from limits import parse
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.revoke import RevocationRequest
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import RequestBodyLimitMiddleware, TransportSecuritySettings
from pydantic import AnyHttpUrl, ValidationError
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.admin_mcp.code_tools import collect_code
from app.admin_mcp.context import DENIED, load_live_context
from app.admin_mcp.context import admin_session as SessionLocal
from app.admin_mcp.diagnose_tools import collect_diagnose
from app.admin_mcp.masking import mask_arguments
from app.admin_mcp.provider import RESOURCE, provider
from app.admin_mcp.registry import ADMIN_TOOLS, CODE_TOOLS, DIAG_TOOLS, REPAIR_TOOLS, collect
from app.admin_mcp.repair_tools import collect_repair
from app.config import settings
from app.core.audit import write_audit
from app.core.security import hash_api_key
from app.models.admin_mcp import AdminMCPOAuthToken
from app.models.admin_security import utcnow
from app.services.repair.runner import gates, prepare_session

TOOL_TIMEOUT = 20
MAX_RESPONSE_BYTES = 64 * 1024
_tool_limiter = FixedWindowRateLimiter(storage_from_string(settings.REDIS_URL or "memory://"))


def _context(db):
    token = get_access_token()
    if not token or token.resource != RESOURCE or token.scopes != ["mcp-admin"]:
        raise PermissionError(DENIED)
    row = db.query(AdminMCPOAuthToken).filter(AdminMCPOAuthToken.token_hash == hash_api_key(token.token),
        AdminMCPOAuthToken.token_type == "access").first()
    if not provider._valid(db, row):
        raise PermissionError(DENIED)
    return load_live_context(db, row)


class AdminMCPServer(MCPServer):
    async def list_tools(self):
        with SessionLocal() as db:
            try:
                _context(db)
                try:
                    gates(db)
                    repair_enabled = True
                except ValueError:
                    repair_enabled = False
            except PermissionError:
                return []
        return [tool for tool in await super().list_tools()
                if repair_enabled or tool.name not in REPAIR_TOOLS]


def response(name: str, correlation: str, result: dict) -> dict:
    state = result["status"]
    return {"zeitpunkt": utcnow().isoformat() + "Z", "status": state,
        "modul": (ADMIN_TOOLS | REPAIR_TOOLS)[name].module,
        "schweregrad": {"ok": "info", "warn": "warnung", "fail": "fehler", "unbekannt": "info",
                       "eingeschraenkt": "info", "nicht verfuegbar": "info"}[state],
        "evidenz": result["evidence"],
        **({"github_zugang": result["github_zugang"]} if "github_zugang" in result else {}),
        "ursachenhypothese": "Hypothese: Teilkomponente gestoert oder Datenbasis unvollstaendig."
            if state != "ok" else "Keine Hypothese erforderlich.",
        "empfehlung": "Zustaendige Systemadministration soll den Teilcheck pruefen."
            if state != "ok" else "Keine Aktion erforderlich.",
        "korrelations_id": correlation}


def _repair_rejection(db, name: str, user_id: int | None, correlation: str, reason: str):
    if name in REPAIR_TOOLS:
        write_audit(db, "admin_repair.abgelehnt", user_id=user_id,
                    payload={"tool": name, "korrelations_id": correlation, "grund": reason})
        db.commit()


def _execute(name: str, arguments: dict, correlation: str) -> dict:
    # Eigene Session im Thread: Auch Live-Pruefung, Audit und Rate-Limit
    # blockieren die Event-Loop nicht und liegen innerhalb des Antwortzeitlimits.
    # Nach Timeout schliesst der Thread seine Session selbst. Reparaturen
    # persistieren Ergebnis und Idempotenz; ihr Mutex bleibt bis Abschluss belegt.
    with SessionLocal() as db:
        try:
            if name in REPAIR_TOOLS:
                prepare_session(db)
            context = _context(db)
            user_id = context.user.id
            write_audit(db, "admin_mcp.tool", user_id=user_id,
                payload={"tool": name, "argumente": mask_arguments(arguments), "korrelations_id": correlation})
            db.commit()
            try:
                permitted = _tool_limiter.hit(parse("30/minute"), "admin-mcp-tool", str(user_id))
            except Exception:
                _repair_rejection(db, name, user_id, correlation, "rate_limit_nicht_verfuegbar")
                return {"status": "unbekannt", "evidence": {"fehler": "Rate-Limit-Pruefung nicht verfuegbar"}}
            if not permitted:
                _repair_rejection(db, name, user_id, correlation, "rate_limit_erreicht")
                return {"status": "warn", "evidence": {"fehler": "Rate-Limit erreicht"}}
            try:
                if name in REPAIR_TOOLS:
                    return collect_repair(db, name, user_id, context.user.session_epoch, correlation,
                                          authorize=lambda: _context(db), web=False, **arguments)
                if name in CODE_TOOLS:
                    return collect_code(db, name, **arguments)
                if name in DIAG_TOOLS:
                    return collect_diagnose(db, name, user_id, correlation, **arguments)
                return collect(db, name, **arguments)
            except ValueError:
                return {"status": "unbekannt", "evidence": {"fehler": "ungueltige Parameter"}}
        except PermissionError:
            if name in REPAIR_TOOLS:
                db.rollback()
                _repair_rejection(db, name, None, correlation, "berechtigung_widerrufen")
            raise
        except Exception:
            return {"status": "fail", "evidence": {"fehler": "Teilcheck nicht verfuegbar"}}


async def call_tool(name: str, **arguments) -> dict:
    correlation = str(uuid4())
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_execute, name, arguments, correlation), timeout=TOOL_TIMEOUT)
    except TimeoutError:
        result = {"status": "unbekannt", "evidence": {"fehler": "Zeitlimit erreicht"}}
    except PermissionError as exc:
        raise ToolError(DENIED) from exc
    answer = response(name, correlation, result)
    if len(json.dumps(answer, ensure_ascii=False, default=str).encode()) > MAX_RESPONSE_BYTES:
        answer = response(name, correlation, {"status": "warn", "evidence": {"fehler": "Antwortgroesse begrenzt"}})
    return answer


registration = ClientRegistrationOptions(enabled=True, valid_scopes=["mcp-admin"], default_scopes=["mcp-admin"])
revocation = RevocationOptions(enabled=True)
server = AdminMCPServer("Einsatzcockpit Systemdiagnose", auth_server_provider=provider,
    auth=AuthSettings(issuer_url=cast(AnyHttpUrl, RESOURCE), resource_server_url=cast(AnyHttpUrl, RESOURCE),
        validate_token_resource=True, required_scopes=["mcp-admin"],
        client_registration_options=registration, revocation_options=revocation))


@server.tool(description=ADMIN_TOOLS["admin_system_uebersicht"].description)
async def admin_system_uebersicht() -> dict:
    return await call_tool("admin_system_uebersicht")


@server.tool(description=ADMIN_TOOLS["admin_dienste_pruefen"].description)
async def admin_dienste_pruefen() -> dict:
    return await call_tool("admin_dienste_pruefen")


@server.tool(description=ADMIN_TOOLS["admin_worker_pruefen"].description)
async def admin_worker_pruefen() -> dict:
    return await call_tool("admin_worker_pruefen")


@server.tool(description=ADMIN_TOOLS["admin_redis_pruefen"].description)
async def admin_redis_pruefen() -> dict:
    return await call_tool("admin_redis_pruefen")


@server.tool(description=ADMIN_TOOLS["admin_datenbank_pruefen"].description)
async def admin_datenbank_pruefen() -> dict:
    return await call_tool("admin_datenbank_pruefen")


@server.tool(description=ADMIN_TOOLS["admin_gateway_pruefen"].description)
async def admin_gateway_pruefen() -> dict:
    return await call_tool("admin_gateway_pruefen")


@server.tool(description=ADMIN_TOOLS["admin_logs_analysieren"].description)
async def admin_logs_analysieren(level: str = "WARNING", limit: int = 100, suchtext: str = "") -> dict:
    return await call_tool("admin_logs_analysieren", level=level, limit=limit, suchtext=suchtext)


@server.tool(description=ADMIN_TOOLS["admin_diagnose_starten"].description)
async def admin_diagnose_starten(checks: list[str] | None = None, umfang: str = "schnell") -> dict:
    return await call_tool("admin_diagnose_starten", checks=checks, umfang=umfang)


@server.tool(description=ADMIN_TOOLS["admin_diagnose_status"].description)
async def admin_diagnose_status(run_id: int | None = None) -> dict:
    return await call_tool("admin_diagnose_status", run_id=run_id)


@server.tool(description=ADMIN_TOOLS["admin_fehler_liste"].description)
async def admin_fehler_liste(modul: str | None = None, schweregrad: str | None = None,
    status: str | None = None, seit: str | None = None, limit: int = 50, cursor: str | None = None) -> dict:
    return await call_tool("admin_fehler_liste", modul=modul, schweregrad=schweregrad,
                           status=status, seit=seit, limit=limit, cursor=cursor)


@server.tool(description=ADMIN_TOOLS["admin_fehler_details"].description)
async def admin_fehler_details(finding_key: str | None = None, id: int | None = None) -> dict:
    return await call_tool("admin_fehler_details", finding_key=finding_key, id=id)


@server.tool(description=REPAIR_TOOLS["admin_reparatur_vorschlagen"].description)
async def admin_reparatur_vorschlagen(finding_key: str | None = None, id: int | None = None) -> dict:
    return await call_tool("admin_reparatur_vorschlagen", finding_key=finding_key, id=id)


@server.tool(description=REPAIR_TOOLS["admin_reparatur_simulieren"].description)
async def admin_reparatur_simulieren(run_id: int | None = None, aktion_id: str | None = None,
                                    scope: dict | None = None) -> dict:
    return await call_tool("admin_reparatur_simulieren", run_id=run_id, aktion_id=aktion_id, scope=scope)


@server.tool(description=REPAIR_TOOLS["admin_reparatur_ausfuehren"].description)
async def admin_reparatur_ausfuehren(run_id: int, simulation_hash: str, idempotency_key: str,
                                   bestaetigt: bool = False, approval_token: str | None = None) -> dict:
    return await call_tool("admin_reparatur_ausfuehren", run_id=run_id, simulation_hash=simulation_hash,
                          idempotency_key=idempotency_key, bestaetigt=bestaetigt, approval_token=approval_token)


@server.tool(description=REPAIR_TOOLS["admin_reparatur_status"].description)
async def admin_reparatur_status(run_id: int) -> dict:
    return await call_tool("admin_reparatur_status", run_id=run_id)


@server.tool(description=REPAIR_TOOLS["admin_reparatur_verifizieren"].description)
async def admin_reparatur_verifizieren(run_id: int, folgediagnose: bool = False) -> dict:
    return await call_tool("admin_reparatur_verifizieren", run_id=run_id, folgediagnose=folgediagnose)


@server.tool(description=ADMIN_TOOLS["admin_code_analyse"].description)
async def admin_code_analyse(quelle: str = "logs", finding_key: str | None = None,
                             level: str | None = None, limit: int = 100) -> dict:
    return await call_tool("admin_code_analyse", quelle=quelle, finding_key=finding_key, level=level, limit=limit)


@server.tool(description=ADMIN_TOOLS["admin_ci_status"].description)
async def admin_ci_status(sha: str | None = None) -> dict:
    return await call_tool("admin_ci_status", sha=sha)


@server.tool(description=ADMIN_TOOLS["admin_codex_auftrag_erstellen"].description)
async def admin_codex_auftrag_erstellen(finding_key: str | None = None, fehlerstelle: dict | None = None,
                                       titel: str | None = None) -> dict:
    return await call_tool("admin_codex_auftrag_erstellen", finding_key=finding_key,
                           fehlerstelle=fehlerstelle, titel=titel)


async def _token(request: Request) -> Response:
    # Das SDK gibt den Resource-Parameter am Token-Endpunkt derzeit nicht an
    # den Provider weiter. Abweichende explizite Ziele hier ablehnen.
    form = await request.form()
    if form.get("resource") not in (None, RESOURCE):
        return JSONResponse({"error": "invalid_target"}, status_code=400,
                            headers={"Cache-Control": "no-store"})
    return await TokenHandler(provider, ClientAuthenticator(provider)).handle(request)


async def _revoke(request: Request) -> Response:
    """RFC 7009 fuer oeffentliche Clients ohne client_secret-Formularfeld.

    Das SDK verlangt aktuell selbst bei auth_method=none ein vorhandenes
    nullable client_secret. Die Korrektur bleibt auf diesen Server beschraenkt.
    """
    try:
        client = await ClientAuthenticator(provider).authenticate_request(request)
    except AuthenticationError:
        return JSONResponse({"error": "unauthorized_client"}, status_code=401)
    form: dict = dict(await request.form())
    form.setdefault("client_secret", None)
    try:
        values = RevocationRequest.model_validate(form)
    except ValidationError:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    token = await provider.load_access_token(values.token) or await provider.load_refresh_token(client, values.token)
    if token and token.client_id == client.client_id:
        await provider.revoke_token(token)
    return Response(status_code=200, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def application():
    app = server.streamable_http_app(streamable_http_path="/mcp/admin", stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    metadata = build_metadata(cast(AnyHttpUrl, RESOURCE), None, registration, revocation)
    metadata.token_endpoint_auth_methods_supported = ["none"]
    metadata.revocation_endpoint_auth_methods_supported = ["none"]
    routes = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path == "/.well-known/oauth-authorization-server":
            route = Route("/.well-known/oauth-authorization-server/mcp/admin",
                endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"])
        elif path in ("/authorize", "/token", "/register", "/revoke"):
            # Neue Route statt path-Mutation: Starlettes kompiliertes Matching
            # muss die tatsaechlichen Admin-Pfade enthalten.
            endpoint = route.endpoint
            if path in ("/token", "/revoke"):
                endpoint = RequestBodyLimitMiddleware(
                    cors_middleware(_token if path == "/token" else _revoke, ["POST", "OPTIONS"]),
                    max_body_size=1024 * 1024)
            route = Route("/mcp/admin" + path, endpoint=endpoint, methods=route.methods)
        elif path == "/mcp/admin":
            route.app = AuthenticationMiddleware(AuthContextMiddleware(route.app),
                backend=BearerAuthBackend(ProviderTokenVerifier(provider),
                                          resource_server_url=cast(AnyHttpUrl, RESOURCE)))
        routes.append(route)
    # Discovery-Aliase nur fuer den separaten Admin-Issuer. Der Org-Issuer
    # und dessen pfadgebundene Resource-Metadaten bleiben unveraendert.
    for path in ("/.well-known/openid-configuration/mcp/admin",
                 "/mcp/admin/.well-known/openid-configuration"):
        routes.append(Route(path, endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
                            methods=["GET", "OPTIONS"]))
    resource_route = next(route for route in routes
                          if route.path == "/.well-known/oauth-protected-resource/mcp/admin")
    routes.append(Route("/.well-known/oauth-protected-resource", endpoint=resource_route.endpoint,
                        methods=["GET", "OPTIONS"]))
    for route in routes:
        if not hasattr(route.endpoint, "__name__"):
            route.endpoint.__name__ = "admin_mcp_sdk_endpoint"
    app.router.routes[:] = routes
    return app
