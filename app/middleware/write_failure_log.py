"""Protokolliert fehlgeschlagene, zustandsändernde HTTP-Anfragen."""
import logging

logger = logging.getLogger("einsatzleiter.write_failures")

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXEMPT_PREFIXES = ("/ws/", "/static/", "/mcp", "/.well-known/")


class WriteFailureLogMiddleware:
    """Reine ASGI-Middleware ohne Body-Pufferung."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        path = scope.get("path", "")
        if method not in _WRITE_METHODS or path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        status_code: int | None = None

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)
        if status_code is None or status_code < 400:
            return

        state = scope.get("state", {})
        user = state.get("user")
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        logger.warning(
            "Schreibzugriff fehlgeschlagen: methode=%s pfad=%s status=%s user_id=%s is_device=%s hx_request=%s",
            method, path, status_code, getattr(user, "id", None), state.get("is_device", False),
            headers.get(b"hx-request", b"").lower() == b"true",
        )
