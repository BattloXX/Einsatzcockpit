"""Security-Header-Middleware (Phase 7).

Setzt restriktive Default-Header für alle HTTP-Antworten:
- Content-Security-Policy (Self + Data-URLs für QR-PNGs; HTMX/Alpine sind self-hosted)
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY (Ausnahmen: /einsatz/*/qr/print + /medien/.../datei/* → SAMEORIGIN,
  damit der In-App-Media-Viewer PDFs/Videos im <iframe> einbetten kann)
- Referrer-Policy: same-origin
- Permissions-Policy
- Strict-Transport-Security (nur bei HTTPS)

CSP nutzt 'unsafe-inline' für Styles + Scripts, da das Template-System derzeit
viele Inline-Handler und Styles enthält. Schrittweise Härtung kann via Nonces erfolgen.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

_CSP_BASE = (
    "default-src 'self'; "
    "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org "
    "https://*.rainviewer.com; "
    "media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss: https://nominatim.openstreetmap.org https://api.rainviewer.com; "
    "frame-src 'self' https://embed.windy.com; "
    "object-src 'none'; "
    "worker-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_CSP_DEFAULT = _CSP_BASE + "; frame-ancestors 'none'"
# Fuer Routen, die per <iframe>/<video> im eigenen UI eingebettet werden:
_CSP_SAMEORIGIN_FRAME = _CSP_BASE + "; frame-ancestors 'self'"
# Alarm-/GSL-Wandmonitor (/infoscreen/alarm/…): bettet die admin-konfigurierte
# Idle-URL-Rotation (fremde HTTPS-Seiten) per <iframe> ein. Die Default-CSP
# erlaubt in frame-src nur 'self' + windy → der Rotations-Iframe wurde vom
# Browser blockiert (Vorfall 2026-07-06). Da der Monitor ein interner, vertrauens-
# wuerdiger Wandbildschirm ist, wird frame-src auf beliebige HTTPS-Origins
# geoeffnet. frame-ancestors 'self' erlaubt zugleich die Monitor-Matrix-Einbettung.
_CSP_ALARM_INFOSCREEN = (
    _CSP_BASE.replace(
        "frame-src 'self' https://embed.windy.com",
        "frame-src 'self' https:",
    )
    + "; frame-ancestors 'self'"
)
# Wetter-Infoscreen: standalone FullHD-Seite mit Tailwind CDN (Fonts sind lokal, siehe fonts.css)
_CSP_INFOSCREEN = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src https://embed.windy.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'none'; "
    "frame-ancestors 'self'"
)


def _is_embeddable_route(path: str) -> bool:
    """Routen, deren Antworten in einem same-origin <iframe> dargestellt werden."""
    if path.endswith("/qr/print"):
        return True
    # In-App-Media-Viewer (Lightbox) bindet PDFs/Videos per <iframe>/<video> ein
    if "/medien/" in path and "/datei/" in path:
        return True
    return False


# Fahrtenbuch-Seiten dürfen (falls konfiguriert) auf externen, vertrauenswürdigen
# Websites per <iframe> eingebettet werden – z.B. dem Feuerwehr-Intern-Bereich.
_FAHRTENBUCH_FRAME_PREFIXES = (
    "/fahrtenbuch",            # Erfassungsformular + HTMX-Partials
    "/f/",                     # Token-/QR-Erfassung (öffentlich)
    "/verwaltung/fahrten",     # Fahrtenbuch-Verwaltung
    "/admin/fahrtenbuch",      # Stammdaten (Zwecke/Zielorte/Fahrzeuge/Token/Einstellungen)
    "/statistik/fahrtenbuch",  # Fahrtenbuch-Statistik
)


def _is_fahrtenbuch_route(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _FAHRTENBUCH_FRAME_PREFIXES)


def _fahrtenbuch_frame_ancestors() -> str:
    """Liefert die konfigurierten externen Eltern-Origins (leerzeichen-getrennt), oder ""."""
    return " ".join((settings.FAHRTENBUCH_FRAME_ANCESTORS or "").split())


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path
        embeddable = _is_embeddable_route(path)
        infoscreen = path.startswith("/wetter/infoscreen/")
        alarm_infoscreen = path.startswith("/infoscreen/alarm/")
        # Fahrtenbuch auf externen Seiten einbettbar, sofern Eltern-Origins konfiguriert sind.
        fb_ancestors = _fahrtenbuch_frame_ancestors() if _is_fahrtenbuch_route(path) else ""

        if infoscreen:
            csp = _CSP_INFOSCREEN
        elif alarm_infoscreen:
            csp = _CSP_ALARM_INFOSCREEN
        elif fb_ancestors:
            csp = _CSP_BASE + f"; frame-ancestors 'self' {fb_ancestors}"
        elif embeddable:
            csp = _CSP_SAMEORIGIN_FRAME
        else:
            csp = _CSP_DEFAULT

        # CSP überschreibt frame-ancestors → eigener X-Frame-Options als Fallback
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(self), camera=(self), payment=()",
        )

        # Wandmonitor darf same-origin eingebettet werden (Monitor-Matrix) und
        # setzt darum SAMEORIGIN — konsistent mit frame-ancestors 'self', statt
        # eines widerspruechlichen DENY (das mit einem evtl. vom Reverse-Proxy
        # gesetzten SAMEORIGIN zu "DENY, SAMEORIGIN" kombiniert wuerde).
        if fb_ancestors:
            # X-Frame-Options kann keine fremde Origin erlauben (ALLOW-FROM ist tot).
            # Deshalb hier KEIN X-Frame-Options setzen – moderne Browser richten sich
            # nach CSP frame-ancestors. Ein evtl. vom Reverse-Proxy (nginx) gesetztes
            # X-Frame-Options muss dort separat für diese Pfade entfernt werden.
            pass
        elif embeddable or alarm_infoscreen:
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        else:
            response.headers.setdefault("X-Frame-Options", "DENY")

        # HSTS nur, wenn die Anfrage über HTTPS kam (oder hinter Proxy mit X-Forwarded-Proto)
        scheme = request.url.scheme
        if scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        # Cross-Origin: vorsichtige Defaults
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

        return response
