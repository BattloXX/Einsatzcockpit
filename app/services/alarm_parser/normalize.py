"""Arbeitskopie bereinigen; das Original bleibt beim Aufrufer unveraendert."""

import unicodedata

from app.services.lis.lis_mapping import _normalize_text, normalize_address


def normalisiere(text: str) -> str:
    return " ".join("".join(
        " " if char.isspace() else "" if unicodedata.category(char).startswith("C") else char
        for char in text
    ).split())


def vergleichsform(text: str) -> str:
    return _normalize_text(normalisiere(text))


def adressvergleich(text: str) -> str:
    return normalize_address(normalisiere(text), None)
