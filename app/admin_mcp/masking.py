"""Gemeinsame Maskierung fuer Diagnose und Audit; keine Rohargumente speichern."""
import re
from typing import Any

_COOKIE = re.compile(r"(?im)\b(?:set-cookie|cookie)\s*[:=]\s*[^\r\n]+")
_SECRET = re.compile(
    r"(?i)\b(?:authorization|bearer|cookie|set-cookie|password|passwort|secret|token|api[_-]?key|"
    r"access_token|refresh_token|code|totp|recovery)[\w-]*\b[\s\"']*[:= ]\s*[^\r\n,;]+"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_IP = re.compile(r"(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|(?<!\w)(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.%]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()/.-]{6,}\d(?!\w)")
_OPAQUE = re.compile(r"[A-Za-z0-9_+/=-]{24,}")


def mask_text(value: str) -> str:
    for pattern in (_COOKIE, _SECRET, _EMAIL, _IP, _PHONE, _OPAQUE):
        value = pattern.sub("[MASKIERT]", value)
    return value


def mask_arguments(arguments: dict[str, Any]) -> dict:
    # Freitext kann beliebige Personen-/Org-Daten enthalten: im Audit nur die
    # numerischen Grenzwerte und fest erlaubte Log-Level erhalten.
    return {key: value if (key == "limit" and type(value) is int)
            or (key == "level" and value in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))
            else "[MASKIERT]" for key, value in arguments.items()}
