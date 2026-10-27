"""Gemeinsame Anonymisierung fuer gespeicherte Testfaelle und MCP-Ausgaben."""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_TELEFON = re.compile(r"(?<!\w)(?:\+43|0043|0)[ ()/-]*(?:\d[ ()/-]*){6,14}\d(?!\w)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b", re.IGNORECASE)
_NAME = re.compile(
    r"(\b(?i:Notrufer|Melder|Anrufer)\s*:?\s+)"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜ]?[a-zäöüß]+)*"
    r"(?:[ \t]+[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜ]?[a-zäöüß]+)*){0,3}",
    re.IGNORECASE,
)

_KEIN_NAME = {"nicht", "unbekannt", "anonym", "keiner", "keine", "bereits", "ist", "hat", "vorort"}


def _maskiere_name(match: re.Match[str]) -> str:
    name = match.group()[len(match.group(1)):]
    if name.split()[0].casefold() in _KEIN_NAME:
        return match.group()
    return match.group(1) + "[Name]"


def anonymisiere(text: str) -> str:
    """Ersetzt erkannte Personendaten; unerkannte Freitextnamen bleiben moeglich."""
    text = _EMAIL.sub("[E-Mail]", text)
    text = _IBAN.sub("[IBAN]", text)
    text = _TELEFON.sub("[Telefon]", text)
    return _NAME.sub(_maskiere_name, text)
