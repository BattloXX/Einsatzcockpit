"""Ausgabe-Maskierung ohne abschaltbaren Klartextpfad."""

import re
import unicodedata
from typing import Any

from .anonymisieren import anonymisiere, erkannte_namen

# Auch internationale Nummern und lokale Nummern ohne fuehrende Null.
# Datum/Uhrzeit (Alarmzeit) zuerst matchen und unveraendert lassen: fuer die
# Parser-Optimierung ist sie unverzichtbar und enthaelt keine Personendaten.
_TELEFON = re.compile(
    r"(?P<datum>(?<!\w)\d{1,2}\.\d{1,2}\.\d{2,4}(?:\s+\d{1,2}:\d{2})?(?!\w))"
    r"|(?P<tel>(?<!\w)\+?\d(?:[ ()/.-]*\d){6,14}(?!\w))"
)


def maskiere(text: str, referenz: str = "") -> str:
    text = "".join(char for char in text if char in "\n\r\t" or not unicodedata.category(char).startswith("C"))
    text = _TELEFON.sub(lambda m: m.group("datum") or "[Telefon]", anonymisiere(text))
    for name in erkannte_namen(referenz):
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", "[Name]", text, flags=re.IGNORECASE)
    return text


def maskiere_ausgabe(value: Any, referenz: str = "") -> Any:
    """Erst vollstaendig maskieren, erst danach kuerzen oder tokenisieren."""
    if isinstance(value, str):
        return maskiere(value, referenz)
    if isinstance(value, dict):
        return {maskiere(str(key), referenz): maskiere_ausgabe(item, referenz) for key, item in value.items()}
    if isinstance(value, list):
        return [maskiere_ausgabe(item, referenz) for item in value]
    return value
