"""Thin async wrapper around the Anthropic Messages API.

Every outgoing payload must pass through `_strip_persons()` before being sent.
Provider errors are always wrapped in `AIServiceError`; raw SDK exceptions never
escape this module.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import re as _re
from typing import Any

from anthropic import APIError, AsyncAnthropic, AuthenticationError, RateLimitError

from app.config import settings

logger = logging.getLogger("einsatzleiter.ai")

_PERSON_KEYS: frozenset[str] = frozenset({
    "name",
    "member",
    "member_id",
    "commander",
    "commander_member_id",
    "commander_id",
    "leader",
    "leader_member",
    "incident_leader_user_id",
    "incident_leader_member_id",
    "created_by_user_id",
    "user",
    "user_id",
    "email",
    "phone",
    "contact",
    "first_name",
    "last_name",
    "fullname",
    "username",
})


class AIServiceError(Exception):
    """Raised when the AI provider call fails; never exposes raw provider errors."""


def is_enabled() -> bool:
    """Return True only when AI_ENABLED=true AND an API key is configured."""
    return settings.AI_ENABLED and bool(settings.ANTHROPIC_API_KEY)


def _strip_persons(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove known person-data keys from a payload dict."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in _PERSON_KEYS:
            continue
        if isinstance(value, dict):
            result[key] = _strip_persons(value)
        elif isinstance(value, list):
            result[key] = [
                _strip_persons(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


async def complete(
    system: str,
    user: str,
    *,
    fast: bool = False,
    max_tokens: int | None = None,
) -> str:
    """Call the Anthropic Messages API and return the text response.

    Raises AIServiceError on any provider failure or timeout.
    Never raises raw SDK exceptions.
    """
    if not is_enabled():
        raise AIServiceError("KI-Dienst ist nicht aktiviert.")

    model = settings.AI_MODEL_FAST if fast else settings.AI_MODEL_DEFAULT
    tokens = max_tokens or settings.AI_MAX_TOKENS
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=float(settings.AI_TIMEOUT),
        )
    except TimeoutError:
        logger.warning("AI provider timeout after %ss (model=%s)", settings.AI_TIMEOUT, model)
        raise AIServiceError("KI-Dienst hat nicht rechtzeitig geantwortet (Timeout).")
    except AuthenticationError:
        logger.error("AI provider authentication failed")
        raise AIServiceError("KI-Dienst: Authentifizierungsfehler – API-Key prüfen.")
    except RateLimitError:
        logger.warning("AI provider rate limit exceeded (model=%s)", model)
        raise AIServiceError("KI-Dienst: Rate-Limit überschritten, bitte kurz warten.")
    except APIError as exc:
        logger.error("AI provider error: %s", exc)
        raise AIServiceError("KI-Dienst temporär nicht verfügbar.") from exc

    if not response.content:
        raise AIServiceError("KI-Dienst hat eine leere Antwort geliefert.")

    return response.content[0].text


_REPORT_SYSTEM = (
    "Du bist ein sachlicher Berichtsassistent der Feuerwehr. "
    "Erstelle aus den gelieferten Einsatzdaten einen strukturierten deutschen Verlaufsbericht "
    "(3–8 Sätze Fließtext, de-AT, sachlich, unpersönlich). "
    "Nutze ausschließlich die gelieferten Fakten. "
    "Was unbekannt oder nicht dokumentiert ist, bezeichnest du als 'nicht dokumentiert'. "
    "Keine Erfindungen, keine Spekulation. "
    "Ausgabe: nur der Berichtstext, ohne Überschrift, ohne Formatierung."
)


_SUGGEST_SYSTEM = (
    "Du bist ein taktischer Erstmaßnahmen-Assistent der Feuerwehr. "
    "Analysiere die Alarmmeldung und die Einsatzart. "
    "Schlage 3–5 konkrete Erstmaßnahmen als JSON-Array vor. "
    "Jedes Element hat die Felder: 'titel' (max 60 Zeichen) und optionales 'detail' (max 120 Zeichen). "
    "Ausgabe: NUR gültiges JSON-Array, keine Erklärungen, keine Umrahmung. "
    'Beispiel: [{"titel":"Wasserversorgung aufbauen","detail":"Hydrant Hauptstraße nutzen"}]'
)


async def suggest_tasks(meldung: str, einsatzart: str) -> list[dict]:
    """Return 3–5 first-response task suggestions for an incoming alarm.

    Returns an empty list on any failure (never raises).
    """
    user_msg = f"Alarmmeldung: {meldung}\nEinsatzart: {einsatzart}"
    try:
        raw = await complete(_SUGGEST_SYSTEM, user_msg, fast=True, max_tokens=600)
    except AIServiceError:
        return []

    # Extract the first JSON array from the response (model may wrap text around it)
    match = _re.search(r"\[.*?\]", raw, _re.DOTALL)
    if not match:
        return []

    try:
        items = _json.loads(match.group())
    except (ValueError, TypeError):
        return []

    if not isinstance(items, list):
        return []

    result: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        titel = str(item.get("titel", "")).strip()[:60]
        detail_raw = item.get("detail")
        detail = str(detail_raw).strip()[:120] if detail_raw else None
        if titel:
            result.append({"titel": titel, "detail": detail})
    return result


async def generate_report_draft(incident_data: dict) -> str:
    """Generate a German prose incident report draft from structured incident data."""
    safe_data = _strip_persons(incident_data)
    user_msg = f"Einsatzdaten (JSON):\n{_json.dumps(safe_data, ensure_ascii=False, indent=2)}"
    return await complete(_REPORT_SYSTEM, user_msg)


_SITUATION_SYSTEM = (
    "Du bist ein taktischer Lageberichts-Assistent der Feuerwehr. "
    "Erstelle aus den gelieferten Live-Einsatzdaten eine knappe Lagebeschreibung "
    "(3–5 Sätze, de-AT, sachlich, unpersönlich). "
    "Nutze ausschließlich die gelieferten Fakten. "
    "Keine Erfindungen, keine Spekulation. "
    "Ausgabe: nur der Lagetext, ohne Überschrift, ohne Formatierung."
)


async def generate_situation_brief(context: dict) -> str:
    """Generate a 3–5 sentence live situation summary from incident context."""
    safe_context = _strip_persons(context)
    user_msg = (
        f"Live-Einsatzdaten (JSON):\n"
        f"{_json.dumps(safe_context, ensure_ascii=False, indent=2)}"
    )
    return await complete(_SITUATION_SYSTEM, user_msg, fast=True, max_tokens=600)
