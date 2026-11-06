"""HTTP-Validatoren fuer geschuetzte Dateien und den statischen Fallback."""

import hashlib
import json
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


def datei_cache_header(pfad: Path, variante: object) -> dict[str, str]:
    """Stat und Ausgabevariante identifizieren die ausgelieferte Darstellung."""
    stat = pfad.stat()
    kennung = json.dumps([1, str(pfad), stat.st_mtime_ns, stat.st_size, variante],
                         separators=(",", ":"), sort_keys=True)
    return {
        "ETag": '"' + hashlib.sha256(kennung.encode()).hexdigest() + '"',
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "Cache-Control": "private, no-cache",
    }


def unveraendert(request: Request, headers: dict[str, str]) -> Response | None:
    """If-None-Match hat Vorrang, auch wenn keiner der Werte passt."""
    kandidaten = request.headers.get("if-none-match")
    if kandidaten is not None:
        passt = any(wert.strip().removeprefix("W/") in ("*", headers["ETag"])
                    for wert in kandidaten.split(","))
    else:
        passt = False
        seit = request.headers.get("if-modified-since")
        if seit:
            try:
                datum = parsedate_to_datetime(seit)
                geaendert = parsedate_to_datetime(headers["Last-Modified"])
                passt = datum.tzinfo is not None and geaendert <= datum
            except (ValueError, TypeError, OverflowError):
                passt = False
    return Response(status_code=304, headers=headers) if passt else None


class CacheStaticFiles(StaticFiles):
    """Versionsgebundene Assets duerfen dauerhaft im Browser bleiben."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        antwort = await super().get_response(path, scope)
        if antwort.status_code in (200, 206, 304):
            versioniert = "v" in Request(scope).query_params
            antwort.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if versioniert else "public, max-age=604800"
            )
        return antwort
