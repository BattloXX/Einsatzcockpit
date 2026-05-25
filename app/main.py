"""FastAPI application – Einsatzleiter-Hilfswerkzeug (Multi-Org) v2.0.0."""
import asyncio
import logging
import secrets as _secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings, validate_startup_secrets
from app.core.security import unsign_session
from app.db import SessionLocal
from app.models.user import User
from app.routers import (
    api_v1,
    auth,
    ui_admin,
    ui_archive,
    ui_breathing,
    ui_incident,
    ui_media,
    ui_password_reset,
    ui_push,
    ui_settings,
    ui_stats,
    ws,
)

logger = logging.getLogger("einsatzleiter")


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

    # Bootstrap admin on first start
    _bootstrap_admin()

    # Background-Loop für 48h-Auto-Close-Lifecycle
    from app.services.autoclose import autoclose_loop
    autoclose_task = asyncio.create_task(autoclose_loop())
    try:
        yield
    finally:
        autoclose_task.cancel()
        try:
            await autoclose_task
        except (asyncio.CancelledError, Exception):
            pass


def _bootstrap_admin() -> None:
    db = SessionLocal()
    try:
        from app.models.user import User as U
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
    title="Einsatzleiter-Hilfswerkzeug",
    version=settings.APP_VERSION,
    description=(
        "REST-API des Einsatzleiter-Hilfswerkzeugs.\n\n"
        "**Authentifizierung:** API-Key via Header `X-API-Key`.\n\n"
        "API-Keys werden unter *Admin → API-Keys* verwaltet."
    ),
    contact={"name": "FF Wolfurt", "email": "office@feuerwehr-wolfurt.at"},
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


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


# Session middleware – inject request.state.user
@app.middleware("http")
async def session_middleware(request: Request, call_next):
    token = request.cookies.get("session")
    request.state.user = None
    if token:
        user_id = unsign_session(token)
        if user_id:
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_id, User.active == True).first()  # noqa: E712
                request.state.user = user
            finally:
                db.close()
    return await call_next(request)


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

# Rate-Limit via slowapi (Phase 7) — wenn nicht installiert, einfach überspringen.
try:
    from slowapi import Limiter  # type: ignore
    from slowapi.errors import RateLimitExceeded  # type: ignore
    from slowapi.middleware import SlowAPIMiddleware  # type: ignore
    from slowapi.util import get_remote_address  # type: ignore
    from starlette.responses import JSONResponse

    # Default-Limit für alle Requests; einzelne Endpoints (login, password-reset)
    # können engere Limits per Decorator setzen
    limiter = Limiter(key_func=get_remote_address, default_limits=["300/minute"])
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _ratelimit_handler(request, exc):  # type: ignore[override]
        return JSONResponse(
            {"detail": "Zu viele Versuche. Bitte später erneut probieren."},
            status_code=429,
        )
except ImportError:
    limiter = None  # type: ignore


# Routers
app.include_router(auth.router)
app.include_router(ui_password_reset.router)
app.include_router(api_v1.router)
app.include_router(ws.router)
app.include_router(ui_incident.router)
app.include_router(ui_media.router)
app.include_router(ui_breathing.router)
app.include_router(ui_archive.router)
app.include_router(ui_admin.router)
app.include_router(ui_stats.router)
app.include_router(ui_push.router)
app.include_router(ui_settings.router)


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


app.openapi = _custom_openapi
