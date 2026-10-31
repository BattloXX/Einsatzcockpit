"""Oeffentliche Einsatzinfo-Links: Kurzlink (/a/<code>) fuer SMS, Langlink als Fallback.

Ist SHORT_BASE_URL gesetzt, nutzt der Kurzlink diese Domain; der Langlink bleibt bei PUBLIC_BASE_URL.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

from app.config import settings

# Base58: ohne 0, O, I, l - fuer Diktat und SMS-Anzeige eindeutig lesbar.
_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
KURZCODE_LAENGE = 10


def neuer_kurzcode() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(KURZCODE_LAENGE))


def kurzcode_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def alarm_link(incident: Any, suffix: str = "") -> str:
    """Basis-URL + Kurzlink (bzw. Langlink fuer Einsaetze ohne Kurzcode); leer ohne Token."""
    code = getattr(incident, "alarm_kurzcode", None)
    if isinstance(code, str) and code:
        base = (settings.SHORT_BASE_URL.strip() or settings.effective_public_base_url).rstrip("/")
        return f"{base}/a/{code}{suffix}"
    base = settings.effective_public_base_url.rstrip("/")
    token = getattr(incident, "alarm_token", None)
    return f"{base}/alarm/{token}{suffix}" if isinstance(token, str) and token else ""
