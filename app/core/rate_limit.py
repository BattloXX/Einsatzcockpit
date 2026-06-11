"""Shared slowapi limiter instance + Rate-Limit-Key-Hilfsfunktionen.

Import this in routers that need per-endpoint rate limiting.
The limiter is None when slowapi is not installed; callers must guard.
"""
from __future__ import annotations

from hashlib import sha256

from fastapi import Request

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    limiter: Limiter | None = Limiter(
        key_func=get_remote_address,
        default_limits=["300/minute"],
    )
except ImportError:
    limiter = None


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
