"""CSRF-Schutz via Double-Submit-Cookie-Pattern (Phase 7).

Reine ASGI-Middleware: Wir replayen den Body, damit nachgelagerte
Handler `request.form()` weiterhin nutzen können.

- Cookie `ec_csrf` wird automatisch gesetzt (HTTPOnly=False, lesbar für JS).
- Bei unsafen Methoden (POST/PUT/PATCH/DELETE) muss der gleiche Wert
  als `X-CSRF-Token`-Header ODER als `_csrf`-Form-Feld vorhanden sein.
- Stimmt nichts → 403.

Ausnahmen: /ws/*, /api/v1/* (X-API-Key authentifiziert), /static/*, /push/*.

SEC-8: /api/v1/device/* ist eine Ausnahme von der Ausnahme — dieser Unterpfad ist
NICHT API-Key- sondern Session-Cookie-authentifiziert (native Android-App via
Capacitor, siehe app/routers/device_api.py). Ein vollwertiger CSRF-Token wäre für
die native App aber nicht ohne App-seitige Änderung nachrüstbar (separates Repo),
daher hier ein leichtgewichtiger Origin-Check statt Token-Pflicht: Browser senden
bei Cross-Origin-Requests einen Origin-Header, der bei Fremd-Origin abgelehnt wird;
native HTTP-Clients senden i. d. R. keinen Origin-Header und bleiben unberührt.
"""
import secrets
from urllib.parse import parse_qs, urlsplit

from app.config import settings

CSRF_COOKIE = "ec_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FORM_FIELD = "_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_PREFIXES = ("/ws/", "/api/v1/", "/api/lagekarte/", "/static/", "/push/")
# Cookie-authentifizierte API-Endpunkte innerhalb von /api/v1/ (SEC-8) — kein
# Token-Exempt-Freifahrtschein, sondern Origin-Check (siehe Docstring oben).
_ORIGIN_CHECK_PREFIXES = ("/api/v1/device/",)


def _allowed_origin_host() -> str | None:
    try:
        return urlsplit(settings.effective_public_base_url).netloc or None
    except Exception:
        return None


def _parse_cookie(header_value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not header_value:
        return out
    for part in header_value.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _constant_time_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


class CSRFMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope["method"]
        path: str = scope.get("path", "")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}

        cookies = _parse_cookie(headers.get("cookie", ""))
        existing_token = cookies.get(CSRF_COOKIE)
        new_token: str | None = None
        if not existing_token:
            new_token = secrets.token_urlsafe(32)
            existing_token = new_token

        # Token für Templates verfügbar machen (request.state.csrf_token).
        # So können Forms das Hidden-Feld serverseitig rendern und funktionieren
        # auch ohne JavaScript.
        scope.setdefault("state", {})["csrf_token"] = existing_token

        is_exempt = any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)
        needs_check = method not in SAFE_METHODS and not is_exempt

        if (
            is_exempt
            and method not in SAFE_METHODS
            and any(path.startswith(p) for p in _ORIGIN_CHECK_PREFIXES)
        ):
            origin = headers.get("origin")
            if origin:
                allowed_host = _allowed_origin_host()
                origin_host = urlsplit(origin).netloc
                if not allowed_host or origin_host != allowed_host:
                    from starlette.responses import JSONResponse
                    resp = JSONResponse(
                        {"detail": "Ungültiger Origin"}, status_code=403,
                    )
                    await resp(scope, receive, send)
                    return

        # Body buffern, damit wir ihn ggf. parsen UND später replayen können
        body_chunks: list[bytes] = []
        more_body = True
        if needs_check:
            while more_body:
                message = await receive()
                if message["type"] == "http.request":
                    body_chunks.append(message.get("body", b""))
                    more_body = message.get("more_body", False)
                else:
                    # http.disconnect oder ähnliches durchreichen
                    await self.app(scope, lambda: message, send)
                    return

            raw_body = b"".join(body_chunks)
            content_type = headers.get("content-type", "")

            header_token = headers.get(CSRF_HEADER.lower())
            submitted: str | None = header_token

            if not submitted and "application/x-www-form-urlencoded" in content_type:
                try:
                    parsed = parse_qs(raw_body.decode("utf-8", errors="ignore"),
                                      keep_blank_values=True)
                    if CSRF_FORM_FIELD in parsed:
                        submitted = parsed[CSRF_FORM_FIELD][0]
                except Exception:
                    pass
            elif not submitted and "multipart/form-data" in content_type:
                # Manuelles Multipart-Parsing wäre teuer. Pragmatisch:
                # Suche den Token-Wert anhand der Boundary
                try:
                    boundary = None
                    for piece in content_type.split(";"):
                        piece = piece.strip()
                        if piece.startswith("boundary="):
                            boundary = piece.split("=", 1)[1].strip().strip('"')
                            break
                    if boundary:
                        marker = b'name="' + CSRF_FORM_FIELD.encode() + b'"'
                        idx = raw_body.find(marker)
                        if idx >= 0:
                            after = raw_body[idx + len(marker):]
                            # nächstes \r\n\r\n überspringen, dann bis nächste boundary lesen
                            sep = b"\r\n\r\n"
                            sidx = after.find(sep)
                            if sidx >= 0:
                                tail = after[sidx + len(sep):]
                                end_boundary = b"\r\n--" + boundary.encode()
                                eidx = tail.find(end_boundary)
                                if eidx >= 0:
                                    submitted = tail[:eidx].decode("utf-8", errors="ignore").strip()
                except Exception:
                    pass

            if not submitted or not _constant_time_eq(submitted, existing_token):
                from starlette.responses import JSONResponse
                resp = JSONResponse(
                    {"detail": "CSRF-Token fehlt oder ungültig"},
                    status_code=403,
                )
                await resp(scope, receive, send)
                return

            # Body replay: neuen receive callable bauen, der den gepufferten Body liefert
            sent = False

            async def replay_receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": raw_body, "more_body": False}
                return {"type": "http.disconnect"}

            await self._call_with_cookie(scope, replay_receive, send, new_token)
            return

        # Safe oder exempt → einfach durchreichen
        await self._call_with_cookie(scope, receive, send, new_token)

    async def _call_with_cookie(self, scope, receive, send, new_token: str | None):
        if not new_token:
            await self.app(scope, receive, send)
            return

        cookie_value = (
            f"{CSRF_COOKIE}={new_token}; Path=/; SameSite=Lax; Max-Age=2592000"
            + ("; Secure" if settings.COOKIE_SECURE else "")
        )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                # Set-Cookie als zusätzlichen Header hinzufügen
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", cookie_value.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
