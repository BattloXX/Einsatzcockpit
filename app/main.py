"""FastAPI application – Einsatzcockpit (Multi-Org) v2.0.0."""

import asyncio
import logging
import os
import secrets as _secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.provider import ProviderTokenVerifier
from pydantic import AnyHttpUrl
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as _StarletteHTTPException
from starlette.middleware.authentication import AuthenticationMiddleware

from app.config import settings, validate_startup_secrets
from app.core.dependencies import _resolve_current_org
from app.core.multi_account import ACCOUNTS_COOKIE, add_account, load_accounts, set_accounts_cookie
from app.core.security import pruefe_geraete_session, unsign_native_link_token, unsign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp import download_router as mcp_download_router
from app.mcp import router as mcp_router
from app.mcp import upload_router as mcp_upload_router
from app.mcp.server import application as mcp_application
from app.mcp.server import provider as mcp_provider
from app.middleware.write_failure_log import WriteFailureLogMiddleware
from app.models.incident import Incident, IncidentToken
from app.models.major_incident import LageToken, MajorIncident, MajorIncidentStatus
from app.models.user import Role, User
from app.routers import (
    api_feed,
    api_kontakt_sync,
    api_live,
    api_messaging,
    api_v1,
    api_weather,
    auth,
    device_api,
    gateway_api,
    lagekarte_api,
    mail_inbound_webhook,
    mailing_webhook,
    monitoring_api,
    objektpflege_public,
    public,
    public_mailing_tracking,
    sso,
    teams_bot,
    ui_account_switch,
    ui_admin,
    ui_ai_prompts,
    ui_annotation,
    ui_archive,
    ui_atemschutz_pruefung,
    ui_atemschutz_pruefung_admin,
    ui_backup,
    ui_bma_import,
    ui_breathing,
    ui_db_backup,
    ui_dibos,
    ui_dienst_monitor,
    ui_druck,
    ui_einheit,
    ui_einsatz_import,
    ui_fahrtenbuch,
    ui_fahrtenbuch_admin,
    ui_foerderstrecke,
    ui_foerderstrecke_admin,
    ui_gateway,
    ui_gk_zugang,
    ui_gsl_staff,
    ui_hilfe,
    ui_incident,
    ui_infoscreen_alarm,
    ui_infoscreen_stats,
    ui_invitation,
    ui_kontakt,
    ui_lagedokument,
    ui_lagefuehrung,
    ui_lis,
    ui_mailing,
    ui_major_incident,
    ui_map_tiles,
    ui_media,
    ui_medienverwaltung,
    ui_nachschlagewerke,
    ui_objekt,
    ui_objekt_dokumente,
    ui_objekt_pflege_review,
    ui_org_backup,
    ui_org_mail,
    ui_password_reset,
    ui_pin_login,
    ui_probenplanung,
    ui_probenplanung_admin,
    ui_probenplanung_public,
    ui_profile,
    ui_push,
    ui_ressourcenkarte,
    ui_road_closure,
    ui_road_closure_admin,
    ui_road_closure_public,
    ui_settings,
    ui_sms,
    ui_sso,
    ui_stats,
    ui_sysadmin,
    ui_teams_bot,
    ui_termin,
    ui_uas,
    ui_verleih,
    ui_wasserstelle,
    ui_weather,
    ws,
)
from app.services.leader_lock import LeaderLock

logger = logging.getLogger("einsatzleiter")

# Laute Drittanbieter-Logger dämpfen: WeasyPrint subsettet bei JEDEM PDF-Druck Fonts
# über fontTools, das dabei hunderte DEBUG/INFO-Zeilen erzeugt (Prod-Log 2026-07-08).
# Auf WARNING setzen (deckt via Logger-Hierarchie auch fontTools.subset/.ttLib/.timer ab).
for _noisy in ("fontTools", "weasyprint", "PIL", "pdf2image"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _install_ws_quiet_exception_handler() -> None:
    """Dämpft benigne WebSocket-Trennungen im asyncio-Log.

    Wenn ein WS-Client (Print-/SMS-Gateway, Browser) nicht mehr auf den keepalive-Ping
    antwortet (Netzabbruch o. Ä.), schließt uvicorn/websockets die Verbindung mit
    `ConnectionClosedError` (1011, "keepalive ping timeout"). Diese landet als
    „exception in shielded future" beim asyncio-Default-Handler auf ERROR-Level –
    reines Rauschen, KEIN Absturz (die Verbindung wird geschlossen und der Client
    reconnectet). Hier auf DEBUG herabgestuft; alle übrigen Loop-Exceptions laufen
    unverändert über den bisherigen Handler."""
    # Vorab-Annotation, damit mypy den Fallback (Modul fehlt) als Variable statt als
    # "Neuzuweisung eines Typs" behandelt (Standardmuster fuer optionale Imports).
    ConnectionClosed: type[BaseException] | None
    try:
        from websockets.exceptions import ConnectionClosed
    except Exception:  # pragma: no cover - websockets immer vorhanden (uvicorn-Dep)
        ConnectionClosed = None

    loop = asyncio.get_running_loop()
    prev = loop.get_exception_handler()

    def _handler(loop_, context: dict) -> None:
        exc = context.get("exception")
        # Die Exception hängt je nach asyncio-Codepfad am 'future'/'task' statt an
        # 'exception' (z. B. "exception in shielded future"). Beide Fälle abdecken.
        if exc is None:
            fut = context.get("future") or context.get("task")
            try:
                if fut is not None and fut.done() and not fut.cancelled():
                    exc = fut.exception()
            except Exception:
                exc = None
        name = type(exc).__name__ if exc is not None else ""
        if (ConnectionClosed is not None and isinstance(exc, ConnectionClosed)) or name in (
            "ConnectionClosedError",
            "ConnectionClosedOK",
            "ConnectionClosed",
        ):
            logger.debug("WebSocket getrennt (keepalive/close): %s", exc)
            return
        # prev (ein zuvor via set_exception_handler registrierter Callback) erwartet
        # (loop, context) -- loop.default_exception_handler ist dagegen eine gebundene
        # Methode und erwartet nur (context). Beide ueber denselben Aufruf zu bedienen
        # (frueher: `(prev or loop_.default_exception_handler)(context)`) rief prev mit
        # zu wenigen Argumenten auf und haette bei TypeError gecrasht, sobald bereits
        # ein anderer Handler registriert war.
        if prev is not None:
            prev(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(_handler)


# In-Memory-Log-Buffer so früh wie möglich registrieren, damit auch Startup-Logs erfasst werden
from app import log_buffer as _log_buffer  # noqa: E402

_log_buffer.setup()


def _start_background_loops() -> list[asyncio.Task]:
    """Startet alle Hintergrund-Loops; läuft nur im Worker mit dem Leader-Lock."""
    from app.services.abfluss_poll_loop import abfluss_poll_loop
    from app.services.ai_log_retention import ai_log_retention_loop
    from app.services.alarm_outbox import alarm_outbox_loop
    from app.services.api_message_dispatch_loop import api_message_dispatch_loop
    from app.services.autoclose import autoclose_loop
    from app.services.breathing_service import _breathing_watchdog_loop
    from app.services.dibos.dibos_capture import dibos_trace_retention_loop
    from app.services.dibos.dibos_loop import dibos_poll_loop
    from app.services.dienst_monitor_loop import dienst_monitor_loop
    from app.services.gk_zugang_service import gk_versand_aufraeum_loop
    from app.services.gsl_lagemeldung_reminder import gsl_lagemeldung_reminder_loop
    from app.services.incident_route_loop import incident_route_loop
    from app.services.lis.lis_capture import lis_capture_retention_loop
    from app.services.lis.lis_loop import lis_poll_loop
    from app.services.mailing_dispatch_loop import mailing_dispatch_loop
    from app.services.mailing_schedule_loop import mailing_schedule_loop
    from app.services.mcp_upload_service import mcp_upload_retention_loop
    from app.services.nachschlagewerk_sync import nachschlagewerk_sync_loop
    from app.services.org_backup_loop import org_backup_loop
    from app.services.print_watchdog import print_job_watchdog_loop
    from app.services.probe_erinnerung import probe_erinnerung_loop
    from app.services.resend_inbound_service import resend_inbound_retention_loop
    from app.services.road_closure_notification_loop import road_closure_notification_loop
    from app.services.sms_dispatch_service import einsatzinfo_nachversand_loop
    from app.services.sms_log_retention import sms_log_retention_loop
    from app.services.task_reminder import task_reminder_loop
    from app.services.vehicle_position_retention import vehicle_position_retention_loop
    from app.services.verleih_erinnerung import verleih_erinnerung_loop
    from app.services.weather_alert_loop import weather_alert_loop
    from app.services.weather_retention import weather_retention_loop

    loops = (
        autoclose_loop(), _breathing_watchdog_loop(), task_reminder_loop(), print_job_watchdog_loop(),
        gsl_lagemeldung_reminder_loop(), gk_versand_aufraeum_loop(), verleih_erinnerung_loop(), probe_erinnerung_loop(),
        weather_retention_loop(), ai_log_retention_loop(), mcp_upload_retention_loop(),
        sms_log_retention_loop(), einsatzinfo_nachversand_loop(), alarm_outbox_loop(), road_closure_notification_loop(),
        vehicle_position_retention_loop(), weather_alert_loop(), dienst_monitor_loop(), abfluss_poll_loop(),
        lis_poll_loop(), lis_capture_retention_loop(), dibos_poll_loop(), dibos_trace_retention_loop(),
        nachschlagewerk_sync_loop(), org_backup_loop(), mailing_dispatch_loop(),
        api_message_dispatch_loop(), mailing_schedule_loop(),
        incident_route_loop(), resend_inbound_retention_loop(),
    )
    return [asyncio.create_task(loop) for loop in loops]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup-Validierung der kritischen Konfiguration
    errors = validate_startup_secrets()
    if errors and not settings.DEBUG:
        for err in errors:
            logger.critical("Konfigurationsfehler: %s", err)
        raise RuntimeError(
            "Fataler Konfigurationsfehler beim Start: "
            + "; ".join(errors)
            + ". Setze SECRET_KEY in der .env auf einen langen zufälligen String."
        )
    elif errors:
        for err in errors:
            logger.warning("Konfigurations-Warnung (DEBUG=True): %s", err)

    # Alle ORM-Modelle registrieren und Mapper sofort konfigurieren. Ohne dies
    # werden manche Module (z. B. app.models.uas) erst lazy beim ersten Request
    # geladen; eine spätere Re-Konfiguration kann dann mitten im Request mit
    # "failed to locate a name" abbrechen und den Request in einen 500 reißen.
    # Hier fällt ein solcher Fehler stattdessen deterministisch beim Boot auf.
    from sqlalchemy.orm import configure_mappers

    # Alias, sonst bindet `import app.models` den Namen `app` lokal in dieser Funktion
    # (Python-Semantik von `import a.b`) -- das kollidiert mit dem weiter unten
    # modulweiten `app = FastAPI(...)` (mypy: "Incompatible import of 'app'").
    import app.models as _app_models  # noqa: F401 – importiert alle Modell-Module in die Registry

    configure_mappers()

    # StreamableHTTPSessionManager ist bewusst nur einmal startbar. Der
    # globale FastAPI-App-Objekt wird bei TestClient jedoch mehrfach durch
    # seinen Lifespan gefahren. Deshalb wird pro Lifespan ein frisches
    # Streamable-HTTP-Sub-App gebaut und nur der /mcp-Handler ausgetauscht;
    # die statischen OAuth-Discovery-Routen bleiben dabei unveraendert.
    from app.mcp.server import application as build_mcp_application
    from app.mcp.server import server as mcp_server

    runtime_mcp_routes = build_mcp_application().routes
    runtime_mcp_route = next(route for route in runtime_mcp_routes if getattr(route, "path", None) == "/mcp")
    app.state.mcp_streamable_route.app = runtime_mcp_route.app
    mcp_session_context = mcp_server.session_manager.run()
    await mcp_session_context.__aenter__()

    # Benigne WebSocket-Trennungen dämpfen (siehe _install_ws_quiet_exception_handler).
    _install_ws_quiet_exception_handler()

    # Redis Pub/Sub-Bus für worker-übergreifende WS-Zustellung starten (No-Op ohne
    # REDIS_URL). Nach dem Router-Import, damit alle Bus-Handler registriert sind.
    from app.services import ws_bus

    await ws_bus.start()

    # Bootstrap admin on first start
    _bootstrap_admin()

    # Separate Wetter-DB (Zeitreihe lokaler Stationen) initialisieren, falls konfiguriert.
    try:
        from app.db_weather import init_weather_db

        init_weather_db()
    except Exception as exc:  # Wetter ist unkritisch – Start nie blockieren.
        logger.warning("Wetter-DB-Init übersprungen: %s", exc)

    # Hintergrund-Loops laufen nur in einem Worker (Datei-Lock je Host). Sonst pollt
    # jeder Gunicorn-Worker DIBOS/LIS und sendet SMS/Teams-Nachversand doppelt.
    leader = LeaderLock(settings.LEADER_LOCK_PATH)
    background_tasks: list[asyncio.Task] = []
    leader_watcher: asyncio.Task | None = None
    try:
        acquired = leader.try_acquire()
    except Exception:
        # Fail open: ein Worker ohne Alarm-Loops wäre schlimmer als doppelte Loops.
        logger.warning("Leader-Lock nicht verfuegbar, Hintergrund-Loops starten trotzdem", exc_info=True)
        acquired = True
    if acquired:
        background_tasks.extend(_start_background_loops())
        logger.info("Hintergrund-Loops aktiv in PID %s", os.getpid())
    else:
        logger.info("Hintergrund-Loops laufen in einem anderen Worker (PID %s wartet)", os.getpid())

        async def _wait_for_leadership() -> None:
            while True:
                await asyncio.sleep(15)
                try:
                    if not leader.try_acquire():
                        continue
                except Exception:
                    logger.warning("Leader-Lock-Uebernahme fehlgeschlagen", exc_info=True)
                    continue
                background_tasks.extend(_start_background_loops())
                logger.info("Hintergrund-Loops uebernommen in PID %s", os.getpid())
                return

        leader_watcher = asyncio.create_task(_wait_for_leadership())

    try:
        yield
    finally:
        await mcp_session_context.__aexit__(None, None, None)
        from app.services import ws_bus

        await ws_bus.stop()
        from app.services.teams_alarm_service import aclose_teams_client

        await aclose_teams_client()
        if leader_watcher is not None:
            leader_watcher.cancel()
        for t in background_tasks:
            t.cancel()
        for t in [*background_tasks, *([leader_watcher] if leader_watcher else [])]:
            try:
                await t
            except asyncio.CancelledError, Exception:
                pass
        try:
            leader.release()
        except Exception:
            logger.warning("Leader-Lock konnte nicht freigegeben werden", exc_info=True)


def _bootstrap_admin() -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.models.user import User as U
        from app.seed_data import _upsert_roles

        _upsert_roles(db)  # always sync role labels (e.g. Schriftführer → Bearbeiter)
        db.commit()

        existing = db.query(U).first()
        if existing:
            return
        from app.seed_data import seed

        seed(db)
        from app.cli import create_admin

        password = settings.BOOTSTRAP_ADMIN_PASSWORD
        generated = False
        if not password:
            password = _secrets.token_urlsafe(18)
            generated = True

        create_admin(settings.BOOTSTRAP_ADMIN_USER, password)

        if generated:
            # Einmalige Ausgabe — Admin muss das Passwort sofort notieren
            logger.warning("=" * 70)
            logger.warning("BOOTSTRAP-ADMIN ANGELEGT — diesen Block einmalig notieren:")
            logger.warning("  Benutzer:  %s", settings.BOOTSTRAP_ADMIN_USER)
            logger.warning("  Passwort:  %s", password)
            logger.warning("Beim nächsten Login bitte Passwort ändern.")
            logger.warning("=" * 70)
    except IntegrityError:
        # Another worker seeded concurrently — benign race, safe to ignore
        db.rollback()
        logger.debug("Bootstrap-Seed: paralleler Worker war schneller (IntegrityError)")
    except Exception:
        # C1: Echte Seed-/Bootstrap-Fehler (z. B. Migrationsstand) dürfen nicht
        # still verschwinden — der Start läuft weiter, aber der Fehler muss ins Log.
        db.rollback()
        logger.exception("Bootstrap-Admin/Seed fehlgeschlagen")
    finally:
        db.close()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    dependencies=[Depends(_resolve_current_org)],
    description=(
        "REST-API von Einsatzcockpit.\n\n"
        "**Authentifizierung:** API-Key via Header `X-API-Key`.\n\n"
        "API-Keys werden unter *Admin → API-Keys* verwaltet."
    ),
    contact={"name": "Einsatzcockpit", "email": "office@einsatzcockpit.com"},
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Die vom SDK erwarteten Auth-Middleware liegen auf der Haupt-App, weil die
# SDK-Routen fuer Discovery vor dem Static-Mount in deren Router uebernommen werden.
app.add_middleware(AuthContextMiddleware)
app.add_middleware(
    AuthenticationMiddleware,
    backend=BearerAuthBackend(
        ProviderTokenVerifier(mcp_provider),
        resource_server_url=cast(AnyHttpUrl, settings.effective_public_base_url.rstrip("/") + "/mcp"),
    ),
)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")
# MCP registriert die OAuth-Metadaten vor dem Static-Mount fuer /.well-known.
_mcp_routes = mcp_application().routes
for _mcp_route in _mcp_routes:
    _mcp_endpoint = getattr(_mcp_route, "endpoint", None)
    if _mcp_endpoint is not None and not hasattr(_mcp_endpoint, "__name__"):
        # SlowAPI erwartet bei gerouteten ASGI-Middleware-Objekten einen Namen.
        setattr(_mcp_endpoint, "__name__", "mcp_sdk_endpoint")
app.router.routes.extend(_mcp_routes)
app.state.mcp_streamable_route = next(route for route in _mcp_routes if getattr(route, "path", None) == "/mcp")
app.mount("/.well-known", StaticFiles(directory="app/static/.well-known"), name="well-known")


def _require_system_admin(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise __import__("fastapi").HTTPException(status_code=401, detail="Login erforderlich")
    roles = [r.code for r in getattr(user, "roles", [])]
    if "system_admin" not in roles:
        raise __import__("fastapi").HTTPException(status_code=403, detail="Nur für System-Admins")


@app.get("/api/docs", include_in_schema=False)
async def api_docs(request: Request, _=Depends(_require_system_admin)):
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="API Dokumentation")


@app.get("/api/redoc", include_in_schema=False)
async def api_redoc(request: Request, _=Depends(_require_system_admin)):
    return get_redoc_html(openapi_url="/api/openapi.json", title="API Dokumentation (ReDoc)")


class _QrUser:
    """Wraps a User for QR-Code sessions, exposing only the recorder role."""

    def __init__(self, user, recorder_role):
        self._user = user
        self.roles = [recorder_role] if recorder_role else []

    def __getattr__(self, name):
        return getattr(self._user, name)


# Statische Auslieferung braucht keinen Benutzer: ohne diesen Skip löst JEDER
# Asset-Request mit Session-Cookie einen User-Lookup (+ Rollen) in der DB aus
# (Audit B3). /sw.js und /favicon.ico sind zwar Routen, nutzen den User aber
# ebenfalls nicht.
_SESSION_SKIP_PREFIXES = ("/static/", "/.well-known/")
_SESSION_SKIP_PATHS = ("/sw.js", "/favicon.ico")


# Session middleware – inject request.state.user + sliding-window token refresh
@app.middleware("http")
async def session_middleware(request: Request, call_next):
    from app.services.broadcast import reset_request_client_id, set_request_client_id

    client_token = set_request_client_id(request.headers.get("X-EC-Client"))
    _path = request.url.path
    if _path.startswith(_SESSION_SKIP_PREFIXES) or _path in _SESSION_SKIP_PATHS:
        try:
            return await call_next(request)
        finally:
            reset_request_client_id(client_token)

    token = request.cookies.get("session")
    request.state.user = None
    request.state.display_name = None
    request.state.qr_incident_id = None
    request.state.qr_lage_id = None
    request.state.is_device = False
    request.state.device_token_id = None
    request.state.accounts = []
    _refresh_user_id: int | None = None  # set for non-QR sessions to trigger cookie refresh
    _refresh_remember: bool = False  # "Login merken" – längeres, gleitendes Fenster

    # Native-App-Erkennung (server-seitig statt client-seitig): index.html der
    # Android-App haengt ?native=1 an ihre erste Navigation zu einsatzcockpit.com
    # an (dort ist Capacitor garantiert verfuegbar). Client-seitige
    # isNativePlatform()-Checks auf DIESER (remote nachgeladenen) Seite sind
    # NICHT verlaesslich: Capacitor injiziert seine JS-Bruecke nicht zuverlaessig
    # in Seiten, die per server.allowNavigation extern geladen werden (bekannte
    # Einschraenkung, siehe ionic-team/capacitor#7454) — betraf zuletzt den
    # "Über die App"-Menüeintrag (blieb dauerhaft ausgeblendet). Das Cookie ist
    # bewusst harmlos/nicht sicherheitsrelevant (steuert nur UI-Sichtbarkeit).
    _native_query = request.query_params.get("native") == "1"
    request.state.is_native_app = _native_query or bool(request.cookies.get("ec_native"))

    if token:
        session_data = unsign_session(token)
        if session_data:
            (user_id, is_qr, qr_incident_id, is_device, display_name, qr_lage_id, is_remember, device_token_id) = (
                session_data
            )
            db = SessionLocal()
            set_tenant_context(db, None)
            try:
                user = db.query(User).filter(User.id == user_id, User.active == True).first()  # noqa: E712
                if user and is_qr:
                    if qr_lage_id is not None:
                        # Lage QR session: valid while Lage is active and token not revoked.
                        db_token = (
                            db.query(LageToken)
                            .filter(
                                LageToken.lage_id == qr_lage_id,
                                LageToken.issued_by_user_id == user_id,
                                LageToken.revoked_at.is_(None),
                            )
                            .first()
                        )
                        lage = db.get(MajorIncident, qr_lage_id) if db_token else None
                        if not db_token or not lage or lage.status != MajorIncidentStatus.active:
                            user = None
                        else:
                            recorder = db.query(Role).filter(Role.code == "recorder").first()
                            user = _QrUser(user, recorder)  # type: ignore[assignment]
                    elif qr_incident_id is not None:
                        # Incident QR session: valid while incident is open and token not revoked.
                        db_token = (
                            db.query(IncidentToken)
                            .filter(  # type: ignore[assignment]
                                IncidentToken.incident_id == qr_incident_id,
                                IncidentToken.issued_by_user_id == user_id,
                                IncidentToken.revoked_at.is_(None),
                            )
                            .first()
                        )
                        inc = db.get(Incident, qr_incident_id) if db_token else None
                        if not db_token or not inc or inc.status != "active":
                            user = None  # Incident closed or token revoked → logged out
                        else:
                            recorder = db.query(Role).filter(Role.code == "recorder").first()
                            user = _QrUser(user, recorder)  # type: ignore[assignment]
                    else:
                        user = None  # QR session without incident_id or lage_id → force re-login
                elif user and is_device:
                    # SEC-5 + Audit A4: Device-Session-Widerruf. Neue Cookies
                    # tragen die device_token_id ("t") — der Widerruf GENAU
                    # dieses Geraets beendet die Session sofort, auch wenn der
                    # User weitere aktive Geraete hat. Bestandscookies ohne
                    # Token-Bezug (vor PR 6 ausgestellt) fallen auf die
                    # grobkoernige Pruefung "hat noch irgendein aktives
                    # Geraet" zurueck.
                    if not pruefe_geraete_session(db, user_id, is_device, device_token_id):
                        user = None
                    else:
                        request.state.device_token_id = device_token_id
                elif user and not is_device:
                    # Regular session: refresh token to slide the inactivity window.
                    _refresh_user_id = user_id
                    _refresh_remember = is_remember
                request.state.user = user
                request.state.display_name = display_name
                request.state.qr_incident_id = qr_incident_id
                request.state.qr_lage_id = qr_lage_id
                request.state.is_device = is_device
                fcm_token = request.query_params.get("fcm_token")
                if user is not None and fcm_token:
                    from app.services.push_service import upsert_fcm_token

                    upsert_fcm_token(
                        db,
                        user_id=user.id,
                        token=fcm_token,
                        device_token_id=device_token_id if is_device else None,
                    )
                    # request.state.user bleibt nach db.close() in den Routen
                    # in Gebrauch; der Commit darf dessen geladene Rollen nicht
                    # ablaufen lassen.
                    db.expire_on_commit = False
                    db.commit()
            except Exception:
                # Transienter DB-Fehler darf anonyme Routen nicht blockieren.
                logger.exception("session_middleware: User-Lookup fehlgeschlagen")
            finally:
                db.close()

    # Native-App-Datei-Handoff: kein Cookie-Zugriff im Custom Tab moeglich, daher
    # Fallback auf ein kurzlebiges, exakt pfadgebundenes ?nt=-Token (siehe
    # sign_native_link_token / /api/v1/device/native-link). Greift nur, wenn die
    # normale Cookie-Session nichts geliefert hat, und authentifiziert
    # ausschliesslich DIESEN einen Request - keine generelle Session/Cookie.
    if request.state.user is None:
        nt = request.query_params.get("nt")
        if nt:
            nt_result = unsign_native_link_token(nt)
            if nt_result and nt_result[0] == request.url.path:
                db = SessionLocal()
                set_tenant_context(db, None)
                try:
                    request.state.user = (
                        db.query(User)
                        .filter(
                            User.id == nt_result[1],
                            User.active == True,  # noqa: E712
                        )
                        .first()
                    )
                except Exception:
                    logger.exception("session_middleware: nt-Token User-Lookup fehlgeschlagen")
                finally:
                    db.close()

    _bereinigte_accounts = []
    if (
        request.state.user is not None
        and not request.state.is_device
        and request.state.qr_incident_id is None
        and request.state.qr_lage_id is None
    ):
        cookie_accounts = load_accounts(request.cookies.get(ACCOUNTS_COOKIE))
        account_ids = [account["u"] for account in cookie_accounts]
        if account_ids:
            db = SessionLocal()
            set_tenant_context(db, None)
            try:
                users = db.query(User).filter(User.id.in_(account_ids), User.active == True).all()  # noqa: E712
                users_by_id = {user.id: user for user in users}
                now = datetime.now(UTC)
                for account in cookie_accounts:
                    account_user = users_by_id.get(account["u"])
                    if not account_user or account_user.is_device:
                        continue
                    locked_until = account_user.locked_until
                    if locked_until and locked_until.tzinfo is None:
                        locked_until = locked_until.replace(tzinfo=UTC)
                    if locked_until and locked_until > now:
                        continue
                    _bereinigte_accounts.append(account)
                    request.state.accounts.append(
                        {
                            "user_id": account_user.id,
                            "display_name": account_user.display_name,
                            "org_name": account_user.org.name if account_user.org else "Keine Organisation",
                            "is_active_account": account_user.id == request.state.user.id,
                        }
                    )
            except Exception:
                logger.exception("session_middleware: Kontoliste konnte nicht validiert werden")
                request.state.accounts = []
                _bereinigte_accounts = []
            finally:
                db.close()
        if _refresh_user_id is not None:
            _bereinigte_accounts = add_account(
                _bereinigte_accounts,
                _refresh_user_id,
                _refresh_remember,
            )
            if not any(account["is_active_account"] for account in request.state.accounts):
                active_user = request.state.user
                request.state.accounts.append(
                    {
                        "user_id": active_user.id,
                        "display_name": active_user.display_name,
                        "org_name": active_user.org.name if active_user.org else "Keine Organisation",
                        "is_active_account": True,
                    }
                )
            request.state.accounts.sort(key=lambda account: not account["is_active_account"])

    try:
        response = await call_next(request)
    finally:
        reset_request_client_id(client_token)

    # Sliding-Window-Refresh, ABER nicht auf /logout: dort löscht der Handler das
    # Session-Cookie – ein Refresh würde es sofort wieder setzen und das Abmelden
    # damit wirkungslos machen.
    if (
        _refresh_user_id is not None
        and request.state.user is not None
        and request.url.path not in {"/logout", "/logout/alle", "/login", "/benutzer/wechseln"}
    ):
        from app.core.security import sign_session as _sign

        _cookie_max_age = (
            settings.SESSION_REMEMBER_MAX_AGE_SECONDS if _refresh_remember else settings.SESSION_MAX_AGE_SECONDS
        )
        response.set_cookie(
            "session",
            _sign(_refresh_user_id, remember=_refresh_remember),
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="lax",
            max_age=_cookie_max_age,
        )
        set_accounts_cookie(response, _bereinigte_accounts)

    if _native_query and not request.cookies.get("ec_native"):
        response.set_cookie(
            "ec_native",
            "1",
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="lax",
            max_age=400 * 24 * 3600,  # ~400 Tage (Chrome-Maximum) — lange, aber nicht sicherheitskritisch
        )

    return response


# CORS für lagekarte.info GeoJSON-Endpoint
try:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET"],
        allow_headers=["*"],
        allow_credentials=False,
        max_age=600,
    )
except Exception:
    pass

# Security headers middleware (Phase 7)
try:
    from app.middleware.security_headers import SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware)
except ImportError:  # falls Modul noch nicht vorhanden
    pass

# CSRF (Phase 7)
try:
    from app.middleware.csrf import CSRFMiddleware

    app.add_middleware(CSRFMiddleware)
except ImportError:
    pass

# Rate-Limit via slowapi — shared limiter lives in app.core.rate_limit.
from app.core.rate_limit import limiter  # noqa: E402

if limiter is not None:
    try:
        from slowapi.errors import RateLimitExceeded  # type: ignore
        from slowapi.middleware import SlowAPIMiddleware  # type: ignore
        from starlette.responses import JSONResponse

        app.state.limiter = limiter
        app.add_middleware(SlowAPIMiddleware)

        @app.exception_handler(RateLimitExceeded)
        async def _ratelimit_handler(request, exc):  # type: ignore[override]
            return JSONResponse(
                {"detail": "Zu viele Versuche. Bitte später erneut probieren."},
                status_code=429,
            )
    except ImportError:
        pass

# Proxy-Header-Middleware: setzt request.client.host auf die echte Client-IP aus
# X-Forwarded-For, damit Rate-Limits pro Angreifer greifen und nicht alle Clients
# dieselbe Proxy-IP teilen. SEC-12: XFF wird nur noch akzeptiert, wenn die
# Verbindung selbst von einer vertrauten Proxy-IP kommt (TRUSTED_PROXY_IPS,
# Default localhost — nginx läuft laut deploy/nginx-snippet.conf auf demselben
# Host). Vorher galt trusted_hosts="*": Wer den App-Port direkt erreichte,
# konnte sich per gefälschtem XFF-Header eine beliebige Client-IP geben
# (Rate-Limit-Bypass, falsche Audit-IPs).
# WICHTIG: Muss NACH SlowAPIMiddleware registriert werden (Starlette macht die
# zuletzt registrierte Middleware zur äußersten) — sonst sieht SlowAPI noch die
# Proxy-IP statt der echten Client-IP aus X-Forwarded-For (SEC-4).
if settings.TRUST_PROXY_HEADERS:
    try:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        _proxy_ips = [ip.strip() for ip in settings.TRUSTED_PROXY_IPS.split(",") if ip.strip()]
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_proxy_ips or "127.0.0.1")
    except ImportError:
        logger.warning(
            "ProxyHeadersMiddleware nicht verfügbar — Rate-Limits arbeiten mit Proxy-IP. "
            "Setze TRUST_PROXY_HEADERS=false wenn kein Reverse-Proxy vorgelagert ist."
        )

# Schreibfehler-Protokollierung muss äußerste Middleware sein: Starlette führt
# zuletzt registrierte Middleware zuerst aus, daher ist request.state.user nach
# der Session-Middleware beim Auswerten der Antwort verfügbar.
app.add_middleware(WriteFailureLogMiddleware)


# Routers
app.include_router(auth.router)
app.include_router(mcp_router.router)
app.include_router(mcp_upload_router.router)
app.include_router(mcp_download_router.router)
app.include_router(sso.router)
app.include_router(public.router)
app.include_router(public_mailing_tracking.router)
app.include_router(mailing_webhook.router)
app.include_router(mail_inbound_webhook.router)
app.include_router(ui_password_reset.router)
app.include_router(ui_pin_login.router)
app.include_router(ui_account_switch.router)
app.include_router(api_v1.router)
app.include_router(api_kontakt_sync.router)
app.include_router(api_messaging.router)
app.include_router(api_weather.router)
app.include_router(device_api.router)
app.include_router(api_feed.router)
app.include_router(api_live.router)
app.include_router(gateway_api.router)
app.include_router(lagekarte_api.router)
app.include_router(teams_bot.router)
app.include_router(ws.router)
app.include_router(ui_incident.router)
app.include_router(ui_lagefuehrung.router)
app.include_router(ui_map_tiles.router)
app.include_router(ui_invitation.router)
app.include_router(ui_backup.router)
app.include_router(ui_db_backup.router)
app.include_router(ui_org_backup.router)
app.include_router(ui_major_incident.router)
app.include_router(ui_einheit.router)
app.include_router(ui_gk_zugang.router)
app.include_router(ui_ressourcenkarte.router)
app.include_router(ui_gsl_staff.router)
app.include_router(ui_lagedokument.router)
app.include_router(ui_media.router)
app.include_router(ui_medienverwaltung.router)
app.include_router(ui_annotation.router)
app.include_router(ui_breathing.router)
app.include_router(ui_archive.router)
app.include_router(ui_hilfe.router)
app.include_router(ui_admin.router)
app.include_router(ui_sms.router)
app.include_router(ui_stats.router)
app.include_router(ui_push.router)
app.include_router(ui_settings.router)
app.include_router(ui_sso.router)
app.include_router(ui_lis.router)
app.include_router(ui_dibos.router)
app.include_router(ui_org_mail.router)
app.include_router(ui_mailing.router)
app.include_router(ui_teams_bot.router)
app.include_router(ui_sysadmin.router)
app.include_router(ui_dienst_monitor.router)
app.include_router(monitoring_api.router)
app.include_router(ui_ai_prompts.router)
app.include_router(ui_profile.router)
app.include_router(ui_weather.router)
app.include_router(ui_termin.router)
app.include_router(ui_probenplanung_public.public_router)
app.include_router(objektpflege_public.public_router)
app.include_router(ui_probenplanung.router)
app.include_router(ui_probenplanung_admin.router)
app.include_router(ui_probenplanung_admin.legacy_router)
app.include_router(ui_uas.router)
app.include_router(ui_bma_import.router)
app.include_router(ui_einsatz_import.router)
app.include_router(ui_objekt.router)
app.include_router(ui_objekt_dokumente.router)
app.include_router(ui_objekt_pflege_review.router)
app.include_router(ui_kontakt.router)
app.include_router(ui_road_closure.router)
app.include_router(ui_road_closure_admin.router)
app.include_router(ui_road_closure_public.router)
app.include_router(ui_nachschlagewerke.router)
app.include_router(ui_nachschlagewerke.cache_router)
app.include_router(ui_wasserstelle.router)
app.include_router(ui_foerderstrecke_admin.router)
app.include_router(ui_foerderstrecke.router)
app.include_router(ui_foerderstrecke.public_router)
app.include_router(ui_gateway.router)
app.include_router(ui_druck.router)
app.include_router(ui_infoscreen_alarm.router)
app.include_router(ui_infoscreen_stats.router)
app.include_router(ui_verleih.router)
app.include_router(ui_fahrtenbuch.router)
app.include_router(ui_fahrtenbuch_admin.router)
app.include_router(ui_atemschutz_pruefung.router)
app.include_router(ui_atemschutz_pruefung_admin.router)


# Emoji + Titel je Status fuer die HTML-Fehlerseite (errors/fehler.html)
_ERROR_META = {
    400: ("⚠️", "Ungültige Anfrage"),
    401: ("\U0001f510", "Anmeldung erforderlich"),
    403: ("⛔", "Kein Zugriff"),
    404: ("\U0001f50d", "Nicht gefunden"),
    410: ("⌛", "Nicht mehr verfügbar"),
    429: ("⏳", "Zu viele Anfragen"),
    500: ("⚠️", "Interner Fehler"),
}


def _login_redirect(request: Request) -> RedirectResponse:
    """Leitet nicht angemeldete Browser-Nutzer zum Login (mit Rücksprung-Ziel)."""
    from app.core.redirects import login_redirect

    return login_redirect(request)


def _render_error_page(request: Request, status: int, detail, *, authenticated: bool):
    from app.core.templating import templates

    emoji, title = _ERROR_META.get(status, ("⚠️", "Fehler"))
    return templates.TemplateResponse(
        request,
        "errors/fehler.html",
        {"status": status, "title": title, "emoji": emoji, "detail": detail or title, "authenticated": authenticated},
        status_code=status,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Protokolliert abgelehnte Formulareingaben (422) mit Feld und Fehlertyp, ohne
    Eingabewerte. Bisher stand im Log nur der Statuscode (Vorfall 2026-10-05:
    9x 422 auf POST /einsatz/387/meldung vom Tablet, Ursache nicht nachvollziehbar)."""
    fehler = [
        f"{'.'.join(str(teil) for teil in fehler.get('loc', ()))}:{fehler.get('type')}"
        for fehler in exc.errors()
    ]
    user = getattr(request.state, "user", None)
    logger.warning(
        "Eingabe abgelehnt (422): methode=%s pfad=%s user_id=%s content_type=%s fehler=%s",
        request.method, request.url.path, getattr(user, "id", None),
        request.headers.get("content-type", "").split(";")[0], fehler,
    )
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(HTTPException)
@app.exception_handler(_StarletteHTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Tablet-API des GSL-Einheitenmodus: strukturierte Fehler ({"code", "details"})
    # unverändert als JSON, damit die Offline-Outbox sie auswerten kann (Plan 5.2).
    if request.url.path.startswith("/einheit/api/"):
        codes = {401: "nicht_angemeldet", 403: "verboten", 404: "nicht_gefunden"}
        body = (
            exc.detail if isinstance(exc.detail, dict)
            else {"code": codes.get(exc.status_code, "fehler"), "detail": exc.detail}
        )
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    # HTMX requests: JSON detail for toast handler; for 401 also trigger full-page redirect
    if request.headers.get("HX-Request"):
        if exc.status_code == 401:
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers={"HX-Redirect": "/login"},
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    path = request.url.path
    is_api = path.startswith("/api/") or path.startswith("/health/dienst") or path.endswith(".json")
    wants_html = "text/html" in request.headers.get("accept", "") and not is_api
    user = getattr(request.state, "user", None)

    # Browser-Navigation: nicht angemeldet + geschützt → Login; sonst schöne Fehlerseite
    if wants_html:
        if exc.status_code in (401, 403) and user is None:
            return _login_redirect(request)
        return _render_error_page(request, exc.status_code, exc.detail, authenticated=user is not None)

    # Nicht-HTML (API/Fetch/Tests): bisheriges Verhalten; .json/API bleiben JSON
    if exc.status_code == 401 and not is_api:
        return RedirectResponse("/login", status_code=302)
    if exc.status_code == 403:
        _body_style = (
            "display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;gap:1rem"
        )
        return HTMLResponse(
            f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nicht erlaubt</title>
<link rel="stylesheet" href="/static/css/app.css">
</head><body style="{_body_style}">
<h2 style="color:var(--color-warn,#f6ad55)">&#9888; Nicht erlaubt</h2>
<p>{exc.detail}</p>
<a href="javascript:history.back()" class="btn btn--ghost">&#8592; Zurück</a>
</body></html>""",
            status_code=403,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    from fastapi.responses import FileResponse

    return FileResponse("app/static/sw.js", media_type="application/javascript")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from app.core.templating import templates

    img_version = templates.env.globals.get("IMG_VERSION", "1")
    return RedirectResponse(f"/static/img/favicon.ico?v={img_version}")


@app.get("/health", include_in_schema=False)
async def health():
    """Unauthentifizierter Verfügbarkeits-Check für externes Monitoring (z. B. Uptime Kuma)."""
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check: DB nicht erreichbar")
        return JSONResponse({"status": "error", "db": "down"}, status_code=503)
    finally:
        db.close()
    return JSONResponse({"status": "ok", "db": "up"})


# Override OpenAPI schema to add X-API-Key security scheme
def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        contact=app.contact,
        routes=app.routes,
    )
    schema.setdefault("components", {})
    schema["components"].setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "API-Key aus dem Admin-Bereich (/admin/api-keys)",
    }
    for path in schema.get("paths", {}).values():
        for op in path.values():
            if isinstance(op, dict):
                op.setdefault("security", [{"ApiKeyAuth": []}])
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[method-assign]
