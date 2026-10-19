"""Öffentlicher CSP-Meldeendpunkt ohne Org-Daten oder Datenbankzugriffe."""

import json
import logging
import re
from collections import OrderedDict
from time import monotonic
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response

from app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger("einsatzleiter.csp")
_MAX_BODY = 16 * 1024
_MAX_KEYS = 500
_LOG_INTERVAL = 3600
# Schlüssel -> (Zeitpunkt der letzten Logzeile, seitdem eingegangene Meldungen).
_reports: OrderedDict[tuple[str, str, str], tuple[float, int]] = OrderedDict()


def _blocked_origin(value: str) -> str:
    """Entfernt Pfad, Zugangsdaten, Query und Fragment der blockierten Quelle."""
    if value in {"inline", "eval"}:
        return value
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https", "ws", "wss"} and parsed.hostname:
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            return f"{parsed.scheme}://{host}{port}"
    except ValueError:
        return "unbekannt"
    return "unbekannt"


def _document_path(value: str) -> str:
    """Nur den Dokumentpfad mit anonymisierten numerischen Segmenten behalten."""
    try:
        path = urlsplit(value).path
    except ValueError:
        return "/"
    return "/".join("{id}" if segment.isdecimal() else segment for segment in path.split("/"))


def _log_report(report: dict) -> None:
    directive = report.get("effective-directive") or report.get("violated-directive")
    blocked = report.get("blocked-uri", "")
    document = report.get("document-uri", "")
    if not isinstance(directive, str) or not isinstance(blocked, str) or not isinstance(document, str):
        return
    # Nur Direktiven-Namen übernehmen, nie mitgelieferte Quellen oder Freitext.
    directive = directive.split()[0] if directive.split() else ""
    if not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", directive):
        return
    key = (directive, _blocked_origin(blocked), _document_path(document))
    now = monotonic()
    last, count = _reports.get(key, (now - _LOG_INTERVAL, 0))
    count += 1
    if now - last >= _LOG_INTERVAL:
        logger.warning(
            "CSP-Verstoß: Direktive=%r Quelle=%r Dokument=%r; %d Meldungen seit der letzten Logzeile",
            *key, count,
        )
        last, count = now, 0
    _reports[key] = (last, count)
    if len(_reports) > _MAX_KEYS:
        _reports.popitem(last=False)


@router.post("/csp-report", status_code=204)
@(limiter.limit("60/minute") if limiter else lambda f: f)
async def csp_report(request: Request) -> Response:
    """Browser-Berichte begrenzt lesen; ungültige Berichte still verwerfen."""
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BODY:
            return Response(status_code=413)
        body.extend(chunk)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"application/csp-report", "application/json"}:
        return Response(status_code=204)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return Response(status_code=204)
    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):
        _log_report(payload["csp-report"])
    return Response(status_code=204)
