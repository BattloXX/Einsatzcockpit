"""Shared slowapi limiter instance + Rate-Limit-Key-Hilfsfunktionen.

Import this in routers that need per-endpoint rate limiting.
The limiter is None when slowapi is not installed; callers must guard.

Storage (Audit A2): Mit gesetztem REDIS_URL zählen alle Worker gegen
DENSELBEN Redis-Bucket — vorher hielt jeder Worker sein eigenes In-Memory-
Kontingent, das effektive Limit war also Limit × Workerzahl (bei -w 2:
Login-Brute-Force mit 20/min statt 10/min). Ohne REDIS_URL (Dev, -w 1)
bleibt es beim In-Memory-Storage. Fällt Redis zur Laufzeit aus, greift der
In-Memory-Fallback von slowapi (fail-open je Worker statt 500er).
"""
from __future__ import annotations

import logging
from hashlib import sha256

from fastapi import Request

logger = logging.getLogger("einsatzleiter.rate_limit")


def _build_limiter():
    try:
        from slowapi import Limiter
        from slowapi.util import get_remote_address
    except ImportError:
        return None

    from app.config import settings

    kwargs: dict = {}
    if settings.REDIS_URL:
        kwargs = {
            "storage_uri": settings.REDIS_URL,
            "in_memory_fallback_enabled": True,
        }
    try:
        return Limiter(
            key_func=get_remote_address,
            default_limits=["300/minute"],
            **kwargs,
        )
    except Exception:
        # Ungültige Storage-URI o. Ä.: lieber mit per-Worker-Limits starten
        # als den App-Start zu reißen.
        logger.exception(
            "Rate-Limit-Storage %r nicht nutzbar — Fallback auf In-Memory (per Worker)",
            settings.REDIS_URL,
        )
        return Limiter(key_func=get_remote_address, default_limits=["300/minute"])


limiter = _build_limiter()


def get_api_key_identifier(request: Request) -> str:
    """Rate-Limit-Key für API-Key-authentifizierte Endpunkte.

    Verwendet den Hash des X-API-Key-Headers als Bucket-Schlüssel, damit
    jeder API-Key ein eigenes Limit-Kontingent erhält. Fällt auf IP zurück,
    wenn kein Key vorhanden ist.
    """
    key_header = request.headers.get("X-API-Key", "").strip()
    if key_header:
        return f"apikey:{sha256(key_header.encode()).hexdigest()[:24]}"
    # Fallback: IP-Adresse (wie get_remote_address)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
