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

_CSP_BASE = (
    "default-src 'self'; "
    "img-src 'self' data: blob: https://tile.openstreetmap.org https://*.tile.openstreetmap.org; "
    "media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss: https://nominatim.openstreetmap.org; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_CSP_DEFAULT = _CSP_BASE + "; frame-ancestors 'none'"
# Fuer Routen, die per <iframe>/<video> im eigenen UI eingebettet werden:
_CSP_SAMEORIGIN_FRAME = _CSP_BASE + "; frame-ancestors 'self'"


def _is_embeddable_route(path: str) -> bool:
    """Routen, deren Antworten in einem same-origin <iframe> dargestellt werden."""
    if path.endswith("/qr/print"):
        return True
    # In-App-Media-Viewer (Lightbox) bindet PDFs/Videos per <iframe>/<video> ein
    if "/medien/" in path and "/datei/" in path:
        return True
    return False


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path
        embeddable = _is_embeddable_route(path)

        # CSP überschreibt frame-ancestors → eigener X-Frame-Options als Fallback
        response.headers.setdefault(
            "Content-Security-Policy",
            _CSP_SAMEORIGIN_FRAME if embeddable else _CSP_DEFAULT,
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(self), camera=(self), payment=()",
        )

        if embeddable:
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
