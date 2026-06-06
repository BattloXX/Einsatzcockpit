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

_AI_SETTING_KEYS: frozenset[str] = frozenset({
    "ai_enabled", "ai_api_key", "ai_model_default", "ai_model_fast",
    "ai_max_tokens", "ai_timeout",
})


def _get_ai_cfg() -> dict:
    """Read AI settings from SystemSettings table, fall back to env vars."""
    from app.db import SessionLocal
    from app.models.master import SystemSettings as _SS
    try:
        db = SessionLocal()
        try:
            rows = db.query(_SS).filter(_SS.key.in_(_AI_SETTING_KEYS)).all()
            db_cfg = {r.key: r.value for r in rows if r.value}
        finally:
            db.close()
    except Exception:
        db_cfg = {}
    return {
        "enabled": db_cfg.get("ai_enabled", "true" if settings.AI_ENABLED else "false") == "true",
        "api_key": db_cfg.get("ai_api_key") or settings.ANTHROPIC_API_KEY,
        "model_default": db_cfg.get("ai_model_default") or settings.AI_MODEL_DEFAULT,
        "model_fast": db_cfg.get("ai_model_fast") or settings.AI_MODEL_FAST,
        "max_tokens": int(db_cfg.get("ai_max_tokens") or settings.AI_MAX_TOKENS),
        "timeout": int(db_cfg.get("ai_timeout") or settings.AI_TIMEOUT),
    }


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
    """Return True when AI is enabled and an API key is configured (DB overrides env vars)."""
    cfg = _get_ai_cfg()
    return cfg["enabled"] and bool(cfg["api_key"])


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
    cfg = _get_ai_cfg()
    if not (cfg["enabled"] and bool(cfg["api_key"])):
        raise AIServiceError("KI-Dienst ist nicht aktiviert.")

    model = cfg["model_fast"] if fast else cfg["model_default"]
    tokens = max_tokens or cfg["max_tokens"]
    client = AsyncAnthropic(api_key=cfg["api_key"])

    try:
        response = await asyncio.wait_for(
            client.messages.create(
                model=model,
                max_tokens=tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            ),
            timeout=float(cfg["timeout"]),
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


# ── Prompt-Bausteine ─────────────────────────────────────────────────────────
# Feste Teile: definieren Rolle, Datenintegrität und Ausgabeformat – nicht editierbar.
# Variable Teile: Stil, Länge, taktische Schwerpunkte – editierbar im Admin unter /admin/ki-prompts.

_REPORT_FIXED_PREFIX = (
    "Du bist ein sachlicher Berichtsassistent der Feuerwehr. "
    "Erstelle aus den gelieferten Einsatzdaten einen strukturierten deutschen Verlaufsbericht. "
    "Nutze ausschließlich die gelieferten Fakten. "
    "Was unbekannt oder nicht dokumentiert ist, bezeichnest du als 'nicht dokumentiert'. "
    "Keine Erfindungen, keine Spekulation."
)
_REPORT_VARIABLE_DEFAULT = (
    "Länge: 3–8 Sätze Fließtext. Sprache: de-AT, sachlich, unpersönlich."
)
_REPORT_FIXED_SUFFIX = "Ausgabe: nur der Berichtstext, ohne Überschrift, ohne Formatierung."

_SUGGEST_FIXED_PREFIX = (
    "Du bist ein taktischer Erstmaßnahmen-Assistent der Feuerwehr. "
    "Analysiere die Alarmmeldung und die Einsatzart."
)
_SUGGEST_VARIABLE_DEFAULT = (
    "Schlage 3–5 konkrete Erstmaßnahmen als JSON-Array vor. "
    "Priorisiere lebensrettende Sofortmaßnahmen und einsatztaktisch wichtige Schritte."
)
_SUGGEST_FIXED_SUFFIX = (
    "Jedes Element hat die Felder: 'titel' (max 60 Zeichen) und optionales 'detail' (max 120 Zeichen). "
    "Ausgabe: NUR gültiges JSON-Array, keine Erklärungen, keine Umrahmung. "
    'Beispiel: [{"titel":"Wasserversorgung aufbauen","detail":"Hydrant Hauptstraße nutzen"}]'
)

_SITUATION_FIXED_PREFIX = (
    "Du bist ein taktischer Lageberichts-Assistent der Feuerwehr. "
    "Erstelle aus den gelieferten Live-Einsatzdaten eine knappe Lagebeschreibung. "
    "Nutze ausschließlich die gelieferten Fakten. "
    "Keine Erfindungen, keine Spekulation."
)
_SITUATION_VARIABLE_DEFAULT = (
    "Länge: 3–5 Sätze. Sprache: de-AT, sachlich, unpersönlich. "
    "Struktur: Einsatzart und Ort, Laufzeit, eingesetzte Kräfte, aktuelle Lage."
)
_SITUATION_FIXED_SUFFIX = "Ausgabe: nur der Lagetext, ohne Überschrift, ohne Formatierung."

_HINTS_FIXED_PREFIX = (
    "Du bist ein taktischer Assistent der Feuerwehr. "
    "Analysiere Alarmstichwort, Meldungstext und Einsatzadresse. "
    "Erstelle kurze taktische Hinweise und Checklisten-Punkte für den Einsatzleiter."
)
_HINTS_VARIABLE_DEFAULT = (
    "Schlage 3–6 prägnante Lage-Hinweise vor. "
    "Berücksichtige einsatztaktische Besonderheiten, Gefahren und Koordinationsaufgaben."
)
_HINTS_FIXED_SUFFIX = (
    "Jeder Hinweis: max 120 Zeichen, aktiv formuliert (z.B. 'Zufahrt freihalten'). "
    "Ausgabe: NUR gültiges JSON-Array von Strings, keine Erklärungen, keine Umrahmung. "
    'Beispiel: ["Zufahrt für Nachkräfte freihalten","Rotes Kreuz nachfordern?"]'
)

PROMPT_META: dict[str, dict] = {
    "report": {
        "label": "Einsatzbericht",
        "fixed_prefix": _REPORT_FIXED_PREFIX,
        "fixed_suffix": _REPORT_FIXED_SUFFIX,
        "variable_default": _REPORT_VARIABLE_DEFAULT,
    },
    "suggest": {
        "label": "Auftragsvorschläge",
        "fixed_prefix": _SUGGEST_FIXED_PREFIX,
        "fixed_suffix": _SUGGEST_FIXED_SUFFIX,
        "variable_default": _SUGGEST_VARIABLE_DEFAULT,
    },
    "situation": {
        "label": "Lagebild",
        "fixed_prefix": _SITUATION_FIXED_PREFIX,
        "fixed_suffix": _SITUATION_FIXED_SUFFIX,
        "variable_default": _SITUATION_VARIABLE_DEFAULT,
    },
    "hints": {
        "label": "Lage-Hinweise",
        "fixed_prefix": _HINTS_FIXED_PREFIX,
        "fixed_suffix": _HINTS_FIXED_SUFFIX,
        "variable_default": _HINTS_VARIABLE_DEFAULT,
    },
}


def _assemble_prompt(prefix: str, suffix: str, variable: str) -> str:
    return f"{prefix}\n{variable}\n{suffix}"


def _get_active_variable(prompt_key: str) -> str | None:
    """Return the latest saved variable part for *prompt_key*, or None if none saved yet."""
    from app.db import SessionLocal
    from app.models.master import AIPromptVersion
    try:
        db = SessionLocal()
        try:
            row = (
                db.query(AIPromptVersion)
                .filter(AIPromptVersion.prompt_key == prompt_key)
                .order_by(AIPromptVersion.version.desc())
                .first()
            )
            return row.variable_part if row and row.variable_part.strip() else None
        finally:
            db.close()
    except Exception:
        return None


async def suggest_tasks(meldung: str, einsatzart: str) -> list[dict]:
    """Return 3–5 first-response task suggestions for an incoming alarm.

    Returns an empty list on any failure (never raises).
    """
    variable = _get_active_variable("suggest") or _SUGGEST_VARIABLE_DEFAULT
    system = _assemble_prompt(_SUGGEST_FIXED_PREFIX, _SUGGEST_FIXED_SUFFIX, variable)
    user_msg = f"Alarmmeldung: {meldung}\nEinsatzart: {einsatzart}"
    try:
        raw = await complete(system, user_msg, fast=True, max_tokens=600)
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
    variable = _get_active_variable("report") or _REPORT_VARIABLE_DEFAULT
    system = _assemble_prompt(_REPORT_FIXED_PREFIX, _REPORT_FIXED_SUFFIX, variable)
    safe_data = _strip_persons(incident_data)
    user_msg = f"Einsatzdaten (JSON):\n{_json.dumps(safe_data, ensure_ascii=False, indent=2)}"
    return await complete(system, user_msg)


async def generate_lage_hints(meldung: str, alarm_type: str, address: str) -> list[str]:
    """Return 3–6 tactical hint strings for the incident ticker.

    Returns an empty list on any failure (never raises).
    """
    variable = _get_active_variable("hints") or _HINTS_VARIABLE_DEFAULT
    system = _assemble_prompt(_HINTS_FIXED_PREFIX, _HINTS_FIXED_SUFFIX, variable)
    user_msg = f"Alarmstichwort: {alarm_type}\nMeldung: {meldung}\nAdresse: {address}"
    try:
        raw = await complete(system, user_msg, fast=True, max_tokens=400)
    except AIServiceError:
        return []

    match = _re.search(r"\[.*?\]", raw, _re.DOTALL)
    if not match:
        return []
    try:
        items = _json.loads(match.group())
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    return [str(h).strip()[:120] for h in items if isinstance(h, str) and str(h).strip()]


async def generate_situation_brief(context: dict) -> str:
    """Generate a 3–5 sentence live situation summary from incident context."""
    variable = _get_active_variable("situation") or _SITUATION_VARIABLE_DEFAULT
    system = _assemble_prompt(_SITUATION_FIXED_PREFIX, _SITUATION_FIXED_SUFFIX, variable)
    safe_context = _strip_persons(context)
    user_msg = (
        f"Live-Einsatzdaten (JSON):\n"
        f"{_json.dumps(safe_context, ensure_ascii=False, indent=2)}"
    )
    return await complete(system, user_msg, fast=True, max_tokens=600)


_ERKUNDUNG_SYSTEM = """Du bist ein Einsatzassistent für die österreichische Feuerwehr.
Analysiere den folgenden Erkundungsbericht einer Einsatzstelle und antworte AUSSCHLIESSLICH
mit einem JSON-Objekt (kein Markdown, kein Text davor oder danach) mit diesen Feldern:
{
  "einsatzgrund": "<Kurzbezeichnung, max. 60 Zeichen>",
  "gefahr": "<Kurzeinschätzung der Gefahr, max. 100 Zeichen>",
  "benoetigte_mittel": "<empfohlene Einsatzmittel, max. 120 Zeichen>",
  "danger_score": <1–4, 1=gering, 4=extrem>,
  "urgency_score": <1–4, 1=aufschiebbar, 4=sofort>,
  "prio_vorschlag": <1–4, 1=sofort/Leben, 2=dringend, 3=normal, 4=aufschiebbar>,
  "zusammenfassung": "<2–3 Sätze Lageeinschätzung>"
}
Keine Personendaten. Datensparsamkeit beachten."""


async def analyze_site_reconnaissance(erkundungstext: str, site_info: dict) -> dict:
    """Analyze a site reconnaissance report; returns structured assessment dict.

    Returns an empty dict on failure (never raises).
    """
    user_msg = (
        f"Einsatzstelle: {site_info.get('bezeichnung', '')} | "
        f"Ort: {site_info.get('ort', '')} | "
        f"Straße: {site_info.get('strasse', '')}\n\n"
        f"Erkundungsbericht:\n{erkundungstext}"
    )
    try:
        raw = await complete(_ERKUNDUNG_SYSTEM, user_msg, fast=True, max_tokens=500)
    except AIServiceError:
        return {}

    match = _re.search(r"\{.*\}", raw, _re.DOTALL)
    if not match:
        return {}
    try:
        result = _json.loads(match.group())
    except (ValueError, TypeError):
        return {}
    if not isinstance(result, dict):
        return {}
    result["danger_score"] = max(1, min(4, int(result.get("danger_score") or 2)))
    result["urgency_score"] = max(1, min(4, int(result.get("urgency_score") or 2)))
    result["prio_vorschlag"] = max(1, min(4, int(result.get("prio_vorschlag") or 3)))
    return result


_PRESSE_SYSTEM = """Du bist Pressesprecher der Freiwilligen Feuerwehr.
Erstelle aus den folgenden Einsatzdaten einer Großschadenslage einen sachlichen,
informativen Pressetext für die Öffentlichkeit (3–5 Absätze, max. 350 Wörter).
Keine Personendaten, keine Spekulationen. Verfasse den Text auf Deutsch.
Beginne direkt mit dem Text ohne Überschrift oder Anrede."""


async def generate_pressemeldung(context: dict) -> str:
    """Generate a press release text from major incident context.

    Returns an empty string on failure (never raises).
    """
    safe_context = _strip_persons(context)
    user_msg = (
        f"Lage-Daten (JSON):\n"
        f"{_json.dumps(safe_context, ensure_ascii=False, indent=2)}"
    )
    try:
        return await complete(_PRESSE_SYSTEM, user_msg, fast=False, max_tokens=800)
    except AIServiceError:
        return ""
