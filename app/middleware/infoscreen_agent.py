"""Transportregeln des Agent-Vertrags, auch fuer Fehlerantworten."""
from fastapi import HTTPException
from starlette.responses import JSONResponse


class InfoscreenAgentMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1/infoscreen-agent/"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        headers = dict(scope.get("headers", []))
        # Kein Range/If-Range: ein Artefakt ist immer genau ein vollstaendiges Paket.
        if "/download/artifact/" in path:
            scope = {**scope, "headers": [(k, v) for k, v in scope.get("headers", [])
                                          if k.lower() not in (b"range", b"if-range")]}
        maximum = 514 * 1024 * 1024
        if path.endswith("/"):
            await JSONResponse({"detail": "not_found"}, 404,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        try:
            size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            size = maximum + 1
        if size > maximum:
            await JSONResponse({"detail": "upload_too_large"}, 413,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                raise HTTPException(413, "upload_too_large")
            return message

        async def no_store(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() not in (b"cache-control", b"set-cookie")]
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, limited_receive, no_store)
