"""Security-Header-Middleware (Phase 7).

Setzt restriktive Default-Header für alle HTTP-Antworten:
- Content-Security-Policy (Self + Data-URLs für QR-PNGs; HTMX/Alpine sind self-hosted)
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY (Ausnahmen: /einsatz/*/qr/print + /medien/.../datei/* → SAMEORIGIN,
  damit der In-App-Media-Viewer PDFs/Videos im <iframe> einbetten kann; ist
  TRUSTED_FRAME_ANCESTORS konfiguriert, wird X-Frame-Options global nicht gesetzt,
  siehe unten)
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
    "https://*.rainviewer.com https://*.wien.gv.at; "
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
# Alarm-/GSL-Wandmonitor (/infoscreen/alarm/…): bettet die admin-konfigurierte
# Idle-URL-Rotation (fremde HTTPS-Seiten) per <iframe> ein. Die Default-CSP
# erlaubt in frame-src nur 'self' + windy → der Rotations-Iframe wurde vom
# Browser blockiert (Vorfall 2026-07-06). Da der Monitor ein interner, vertrauens-
# wuerdiger Wandbildschirm ist, wird frame-src auf beliebige HTTPS-Origins geoeffnet.
_CSP_ALARM_INFOSCREEN_BASE = _CSP_BASE.replace(
    "frame-src 'self' https://embed.windy.com",
    "frame-src 'self' https:",
)
# Wetter-Infoscreen: standalone FullHD-Seite mit Tailwind CDN (Fonts sind lokal, siehe fonts.css)
_CSP_INFOSCREEN_BASE = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src https://embed.windy.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'none'"
)


def _is_embeddable_route(path: str) -> bool:
    """Routen, deren Antworten in einem same-origin <iframe> dargestellt werden."""
    if path.endswith("/qr/print"):
        return True
    # In-App-Media-Viewer (Lightbox) bindet PDFs/Videos per <iframe>/<video> ein
    if "/medien/" in path and "/datei/" in path:
        return True
    return False


def _trusted_frame_ancestors() -> str:
    """Liefert die konfigurierten externen Eltern-Origins (leerzeichen-getrennt), oder ""."""
    return " ".join((settings.TRUSTED_FRAME_ANCESTORS or "").split())


def _frame_ancestors_directive(*, self_allowed: bool, trusted: str) -> str:
    """CSP frame-ancestors für eine Antwort.

    Sind externe Origins konfiguriert (`trusted`), dürfen sie die Seite IMMER
    zusätzlich zu 'self' einbetten (App-weit, nicht auf einzelne Routen
    beschränkt) — 'none' entfällt dann, da CSP 'none' nicht mit anderen Quellen
    kombinierbar ist. Ohne Konfiguration bleibt es beim bisherigen, strikteren
    Verhalten je Routen-Kategorie (self_allowed).
    """
    if trusted:
        return "frame-ancestors 'self' " + trusted
    return "frame-ancestors 'self'" if self_allowed else "frame-ancestors 'none'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path
        embeddable = _is_embeddable_route(path)
        infoscreen = path.startswith("/wetter/infoscreen/")
        alarm_infoscreen = path.startswith("/infoscreen/alarm/")
        trusted = _trusted_frame_ancestors()

        if infoscreen:
            csp_base = _CSP_INFOSCREEN_BASE
            self_allowed = True
        elif alarm_infoscreen:
            csp_base = _CSP_ALARM_INFOSCREEN_BASE
            self_allowed = True
        elif embeddable:
            csp_base = _CSP_BASE
            self_allowed = True
        else:
            csp_base = _CSP_BASE
            self_allowed = False

        csp = csp_base + "; " + _frame_ancestors_directive(self_allowed=self_allowed, trusted=trusted)

        # CSP überschreibt frame-ancestors → eigener X-Frame-Options als Fallback
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(self), camera=(self), payment=()",
        )

        if trusted:
            # X-Frame-Options kann keine fremde Origin erlauben (ALLOW-FROM ist tot).
            # Deshalb hier KEIN X-Frame-Options setzen – moderne Browser richten sich
            # nach CSP frame-ancestors. Ein evtl. vom Reverse-Proxy (nginx) gesetztes
            # X-Frame-Options muss dort separat entfernt werden.
            pass
        elif self_allowed:
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
