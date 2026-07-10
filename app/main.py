"""FastAPI application – Einsatzcockpit (Multi-Org) v2.0.0."""
import asyncio
import logging
import os as _os
import secrets as _secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as _StarletteHTTPException

from app.config import settings, validate_startup_secrets
from app.core.dependencies import _resolve_current_org
from app.core.security import unsign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentToken
from app.models.major_incident import LageToken, MajorIncident, MajorIncidentStatus
from app.models.user import DeviceToken, Role, User
from app.routers import (
    api_import,
    api_v1,
    api_weather,
    auth,
    device_api,
    gateway_api,
    lagekarte_api,
    public,
    sso,
    teams_bot,
    ui_admin,
    ui_ai_prompts,
    ui_annotation,
    ui_archive,
    ui_atemschutz_pruefung,
    ui_atemschutz_pruefung_admin,
    ui_backup,
    ui_breathing,
    ui_druck,
    ui_fahrtenbuch,
    ui_fahrtenbuch_admin,
    ui_gateway,
    ui_gsl_staff,
    ui_hilfe,
    ui_incident,
    ui_infoscreen_alarm,
    ui_invitation,
    ui_lagefuehrung,
    ui_lis,
    ui_major_incident,
    ui_media,
    ui_objekt,
    ui_objekt_dokumente,
    ui_org_mail,
    ui_password_reset,
    ui_profile,
    ui_push,
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
    try:
        from websockets.exceptions import ConnectionClosed
    except Exception:  # pragma: no cover - websockets immer vorhanden (uvicorn-Dep)
        ConnectionClosed = None  # type: ignore[assignment]

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
            "ConnectionClosedError", "ConnectionClosedOK", "ConnectionClosed",
        ):
            logger.debug("WebSocket getrennt (keepalive/close): %s", exc)
            return
        (prev or loop_.default_exception_handler)(context)

    loop.set_exception_handler(_handler)

# In-Memory-Log-Buffer so früh wie möglich registrieren, damit auch Startup-Logs erfasst werden
from app import log_buffer as _log_buffer  # noqa: E402

_log_buffer.setup()


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

    import app.models  # noqa: F401 – importiert alle Modell-Module in die Registry
    configure_mappers()

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

    # Background-Loop für 48h-Auto-Close-Lifecycle
    from app.services.autoclose import autoclose_loop
    autoclose_task = asyncio.create_task(autoclose_loop())

    # Background-Watchdog für AS-Warnungen (alle 5 Sekunden)
    from app.services.breathing_service import _breathing_watchdog_loop
    watchdog_task = asyncio.create_task(_breathing_watchdog_loop())

    # Background-Loop für fällige Meldungen (alle 30 Sekunden)
    from app.services.task_reminder import task_reminder_loop
    reminder_task = asyncio.create_task(task_reminder_loop())

    # Background-Loop für überfällige GSL-Lagemeldungen (SKKM-Regelkreis)
    from app.services.gsl_lagemeldung_reminder import gsl_lagemeldung_reminder_loop
    lagemeldung_task = asyncio.create_task(gsl_lagemeldung_reminder_loop())

    # Background-Loop für automatische Geräteverleih-Erinnerungs-SMS
    from app.services.verleih_erinnerung import verleih_erinnerung_loop
    verleih_task = asyncio.create_task(verleih_erinnerung_loop())

    # Background-Loop für Wetterstations-Zeitreihen-Retention (täglich 03:30)
    from app.services.weather_retention import weather_retention_loop
    weather_retention_task = asyncio.create_task(weather_retention_loop())

    # Background-Loop für GPS-Positionshistorie-Retention (täglich 03:45)
    from app.services.vehicle_position_retention import vehicle_position_retention_loop
    vehicle_position_retention_task = asyncio.create_task(vehicle_position_retention_loop())

    # Background-Loop für Wetterwarnungen (alle 5 Minuten je Org)
    from app.services.weather_alert_loop import weather_alert_loop
    weather_alert_task = asyncio.create_task(weather_alert_loop())

    # Background-Loop für die LIS/IPR-Anbindung (Poll-Intervall je Org konfigurierbar)
    from app.services.lis.lis_loop import lis_poll_loop
    lis_task = asyncio.create_task(lis_poll_loop())

    # Background-Loop für die Löschfrist von LIS-Rohdaten-Aufzeichnungen (täglich 04:00,
    # DSGVO — enthalten Personenbezug, siehe lis_capture.py)
    from app.services.lis.lis_capture import lis_capture_retention_loop
    lis_capture_retention_task = asyncio.create_task(lis_capture_retention_loop())

    try:
        yield
    finally:
        from app.services import ws_bus
        await ws_bus.stop()
        autoclose_task.cancel()
        watchdog_task.cancel()
        reminder_task.cancel()
        lagemeldung_task.cancel()
        verleih_task.cancel()
        weather_retention_task.cancel()
        vehicle_position_retention_task.cancel()
        weather_alert_task.cancel()
        lis_task.cancel()
        lis_capture_retention_task.cancel()
        for t in (autoclose_task, watchdog_task, reminder_task, lagemeldung_task, verleih_task,
                  weather_retention_task, vehicle_position_retention_task, weather_alert_task,
                  lis_task, lis_capture_retention_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


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
    except Exception:
        # Another worker may have seeded concurrently — safe to ignore
        db.rollback()
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

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")
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


# Session middleware – inject request.state.user + sliding-window token refresh
@app.middleware("http")
async def session_middleware(request: Request, call_next):
    token = request.cookies.get("session")
    request.state.user = None
    request.state.display_name = None
    request.state.qr_incident_id = None
    request.state.qr_lage_id = None
    request.state.is_device = False
    _refresh_user_id: int | None = None  # set for non-QR sessions to trigger cookie refresh
    _refresh_remember: bool = False      # "Login merken" – längeres, gleitendes Fenster

    if token:
        session_data = unsign_session(token)
        if session_data:
            (user_id, is_qr, qr_incident_id, is_device, display_name,
             qr_lage_id, is_remember) = session_data
            db = SessionLocal()
            set_tenant_context(db, None)
            try:
                user = db.query(User).filter(User.id == user_id, User.active == True).first()  # noqa: E712
                if user and is_qr:
                    if qr_lage_id is not None:
                        # Lage QR session: valid while Lage is active and token not revoked.
                        db_token = db.query(LageToken).filter(
                            LageToken.lage_id == qr_lage_id,
                            LageToken.issued_by_user_id == user_id,
                            LageToken.revoked_at.is_(None),
                        ).first()
                        lage = db.get(MajorIncident, qr_lage_id) if db_token else None
                        if not db_token or not lage or lage.status != MajorIncidentStatus.active:
                            user = None
                        else:
                            recorder = db.query(Role).filter(Role.code == "recorder").first()
                            user = _QrUser(user, recorder)  # type: ignore[assignment]
                    elif qr_incident_id is not None:
                        # Incident QR session: valid while incident is open and token not revoked.
                        db_token = db.query(IncidentToken).filter(  # type: ignore[assignment]
                            IncidentToken.incident_id == qr_incident_id,
                            IncidentToken.issued_by_user_id == user_id,
                            IncidentToken.revoked_at.is_(None),
                        ).first()
                        inc = db.get(Incident, qr_incident_id) if db_token else None
                        if not db_token or not inc or inc.status != "active":
                            user = None  # Incident closed or token revoked → logged out
                        else:
                            recorder = db.query(Role).filter(Role.code == "recorder").first()
                            user = _QrUser(user, recorder)  # type: ignore[assignment]
                    else:
                        user = None  # QR session without incident_id or lage_id → force re-login
                elif user and is_device:
                    # SEC-5: Device-Session-Widerruf. Das Session-Cookie speichert
                    # keine device_token_id (10 Jahre gueltig, siehe
                    # sign_session(device=True)) -- daher pruefen wir, ob der User
                    # ueberhaupt noch ein NICHT widerrufenes Geraet hat. Schliesst
                    # die Luecke fuer den Hauptfall (Geraet verloren -> Token
                    # widerrufen -> Cookie soll sofort ungueltig werden). Bei
                    # mehreren Geraeten je User bleibt die Pruefung grobkoernig,
                    # solange das Cookie keinen Token-Bezug hat.
                    has_active_device = db.query(DeviceToken).filter(
                        DeviceToken.user_id == user_id,
                        DeviceToken.revoked_at.is_(None),
                    ).first() is not None
                    if not has_active_device:
                        user = None
                elif user and not is_device:
                    # Regular session: refresh token to slide the inactivity window.
                    _refresh_user_id = user_id
                    _refresh_remember = is_remember
                request.state.user = user
                request.state.display_name = display_name
                request.state.qr_incident_id = qr_incident_id
                request.state.qr_lage_id = qr_lage_id
                request.state.is_device = is_device
            except Exception:
                # Transienter DB-Fehler darf anonyme Routen nicht blockieren.
                logger.exception("session_middleware: User-Lookup fehlgeschlagen")
            finally:
                db.close()

    response = await call_next(request)

    # Sliding-Window-Refresh, ABER nicht auf /logout: dort löscht der Handler das
    # Session-Cookie – ein Refresh würde es sofort wieder setzen und das Abmelden
    # damit wirkungslos machen.
    if (_refresh_user_id is not None and request.state.user is not None
            and request.url.path != "/logout"):
        from app.core.security import sign_session as _sign
        _cookie_max_age = (
            settings.SESSION_REMEMBER_MAX_AGE_SECONDS if _refresh_remember
            else settings.SESSION_MAX_AGE_SECONDS
        )
        response.set_cookie(
            "session",
            _sign(_refresh_user_id, remember=_refresh_remember),
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="lax",
            max_age=_cookie_max_age,
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
# dieselbe Proxy-IP teilen. Nur aktivieren wenn ein vertrauenswürdiger Reverse-
# Proxy vorgelagert ist (Nginx, Traefik …) — sonst ist XFF fälschbar.
# Steuerung über Env-Variable TRUST_PROXY_HEADERS (true/false, default true).
# WICHTIG: Muss NACH SlowAPIMiddleware registriert werden (Starlette macht die
# zuletzt registrierte Middleware zur äußersten) — sonst sieht SlowAPI noch die
# Proxy-IP statt der echten Client-IP aus X-Forwarded-For (SEC-4).
if _os.environ.get("TRUST_PROXY_HEADERS", "true").lower() == "true":
    try:
        from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: F401
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    except ImportError:
        logger.warning(
            "ProxyHeadersMiddleware nicht verfügbar — Rate-Limits arbeiten mit Proxy-IP. "
            "Setze TRUST_PROXY_HEADERS=false wenn kein Reverse-Proxy vorgelagert ist."
        )


# Routers
app.include_router(auth.router)
app.include_router(sso.router)
app.include_router(public.router)
app.include_router(ui_password_reset.router)
app.include_router(api_v1.router)
app.include_router(api_weather.router)
# TEMPORAER: EUS-Datenmigration — nach Abschluss entfernen (siehe app/routers/api_import.py)
app.include_router(api_import.router)
app.include_router(device_api.router)
app.include_router(gateway_api.router)
app.include_router(lagekarte_api.router)
app.include_router(teams_bot.router)
app.include_router(ws.router)
app.include_router(ui_incident.router)
app.include_router(ui_lagefuehrung.router)
app.include_router(ui_invitation.router)
app.include_router(ui_backup.router)
app.include_router(ui_major_incident.router)
app.include_router(ui_gsl_staff.router)
app.include_router(ui_media.router)
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
app.include_router(ui_org_mail.router)
app.include_router(ui_teams_bot.router)
app.include_router(ui_sysadmin.router)
app.include_router(ui_ai_prompts.router)
app.include_router(ui_profile.router)
app.include_router(ui_weather.router)
app.include_router(ui_termin.router)
app.include_router(ui_uas.router)
app.include_router(ui_objekt.router)
app.include_router(ui_objekt_dokumente.router)
app.include_router(ui_wasserstelle.router)
app.include_router(ui_gateway.router)
app.include_router(ui_druck.router)
app.include_router(ui_infoscreen_alarm.router)
app.include_router(ui_verleih.router)
app.include_router(ui_fahrtenbuch.router)
app.include_router(ui_fahrtenbuch_admin.router)
app.include_router(ui_atemschutz_pruefung.router)
app.include_router(ui_atemschutz_pruefung_admin.router)


# Emoji + Titel je Status fuer die HTML-Fehlerseite (errors/fehler.html)
_ERROR_META = {
    400: ("⚠️", "Ungültige Anfrage"),
    401: ("\U0001F510", "Anmeldung erforderlich"),
    403: ("⛔", "Kein Zugriff"),
    404: ("\U0001F50D", "Nicht gefunden"),
    410: ("⌛", "Nicht mehr verfügbar"),
    429: ("⏳", "Zu viele Anfragen"),
    500: ("⚠️", "Interner Fehler"),
}


def _login_redirect(request: Request) -> RedirectResponse:
    """Leitet nicht angemeldete Browser-Nutzer zum Login (mit Rücksprung-Ziel)."""
    from urllib.parse import quote
    path = request.url.path
    if request.url.query:
        path += "?" + request.url.query
    return RedirectResponse(f"/login?next={quote(path, safe='')}", status_code=302)


def _render_error_page(request: Request, status: int, detail, *, authenticated: bool):
    from app.core.templating import templates
    emoji, title = _ERROR_META.get(status, ("⚠️", "Fehler"))
    return templates.TemplateResponse(
        request, "errors/fehler.html",
        {"status": status, "title": title, "emoji": emoji,
         "detail": detail or title, "authenticated": authenticated},
        status_code=status,
    )


@app.exception_handler(HTTPException)
@app.exception_handler(_StarletteHTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
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
    is_api = path.startswith("/api/") or path.endswith(".json")
    wants_html = "text/html" in request.headers.get("accept", "") and not is_api
    user = getattr(request.state, "user", None)

    # Browser-Navigation: nicht angemeldet + geschützt → Login; sonst schöne Fehlerseite
    if wants_html:
        if exc.status_code in (401, 403) and user is None:
            return _login_redirect(request)
        return _render_error_page(request, exc.status_code, exc.detail,
                                  authenticated=user is not None)

    # Nicht-HTML (API/Fetch/Tests): bisheriges Verhalten; .json/API bleiben JSON
    if exc.status_code == 401 and not is_api:
        return RedirectResponse("/login", status_code=302)
    if exc.status_code == 403:
        _body_style = (
            "display:flex;flex-direction:column;align-items:center;"
            "justify-content:center;min-height:100vh;gap:1rem"
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
    return RedirectResponse("/static/img/favicon.ico")


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
