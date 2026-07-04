"""Reine Parsing-/Mapping-Helfer für die LIS/IPR-Anbindung (kein DB-/Netzwerkzugriff).

Referenz: LIS_IPR_Schnittstellen_Dokumentation.md Abschnitte 7 (Fahrzeugstatus),
8 (Personen-Zu-/Absagen) und 9.3 (.NET-Default-Datum).
"""
from __future__ import annotations

import re
from datetime import datetime

# ── Alarmstichwort-Mapping (LIS Type.Code → interner AlarmType-Code) ──────────
# Bewusst eine eigene, kleine Tabelle statt Import aus api_v1 (vermeidet Zirkelimport
# app.routers.api_v1 ↔ app.services.lis.*). Werte spiegeln STUFE_MAP in api_v1.py.
_STUFE_MAP: dict[str, str] = {
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f14": "F14",
    "t1": "T1", "t2": "T2", "t3": "T3", "t4": "T4", "t6": "T6", "t7": "T7",
    "1": "T1", "2": "T2", "3": "T3", "4": "T4", "6": "T6", "7": "T7",
    "t9": "T9", "9": "T9",
}


def map_stichwort(lis_type_code: str | None) -> str:
    """Mappt ein LIS-Type.Code (z.B. 'f1', 'T4') auf einen internen AlarmType-Code.
    Fallback ist immer 'T1' (unbekannt/leer)."""
    if not lis_type_code:
        return "T1"
    return _STUFE_MAP.get(lis_type_code.strip().lower(), "T1")


# ── Fahrzeugstatus (S4/S5 → interne UNIT_STATUS_VALUES) ───────────────────────
def map_unit_status(label: str | None) -> str | None:
    """Mappt ein LIS-OperationUnitStatusType.Label auf einen internen unit_status.

    Nur S4 ("zum Einsatzort") und S5 ("am Einsatzort") werden übernommen — alle
    anderen Codes (S1-S3, S6-S8 etc.) werden bewusst NICHT gemappt (Rückgabe None),
    da hierfür keine Entsprechung im internen 3-Werte-Modell existiert.
    """
    if not label:
        return None
    text = label.strip().upper()
    if text.startswith("S4") or "ZUM EINSATZORT" in text:
        return "Einsatz übernommen"
    if text.startswith("S5") or "AM EINSATZORT" in text:
        return "Am Einsatzort"
    return None


# ── Personen-Zu-/Absagen (aus UNITSTATUSHISTORY-Task-Freitext) ────────────────
PERSON_RESPONSE_RE = re.compile(
    r"^(?P<person>[\w.\-]+)"
    r"(?:\s*\((?P<role>[^)]+)\))?"
    r":\s*(?P<status>Zugesagt|Abgesagt)"
    r"(?:\s+Ankunftszeit\s+(?P<arrival>\d{2}:\d{2}))?\s*$"
)


def parse_person_response(description: str | None, task_type: str | None) -> dict | None:
    """Erkennt Personen-Zu-/Absagen in einem UNITSTATUSHISTORY-Task-Freitext.

    Gibt None zurück, wenn der Task kein Personen-Eintrag ist (z.B. Fahrzeug-
    Statuswechsel wie 'Wolfurt KDOF: S4 - zum Einsatzort' oder ein anderer
    Task-Typ) — diese werden separat über map_unit_status() behandelt.
    """
    if task_type != "UNITSTATUSHISTORY" or not description:
        return None
    m = PERSON_RESPONSE_RE.match(description.strip())
    if not m:
        return None
    return {
        "person": m.group("person"),
        "role": m.group("role"),
        "status": m.group("status"),
        "arrival_time": m.group("arrival"),
    }


# ── Normalisierung für die Matching-Heuristik (Grund/Adresse) ─────────────────
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[.,;:!?\"'`´()\[\]{}/\\-]+")


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip().lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def normalize_reason(value: str | None) -> str:
    return _normalize_text(value)


def normalize_address(street: str | None, city: str | None) -> str:
    return _normalize_text(f"{street or ''} {city or ''}")


# ── .NET-Default-Datum ("0001-01-01T00:00:00") → None ────────────────────────
def clean_dotnet_date(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.year <= 1:
        return None
    return value
