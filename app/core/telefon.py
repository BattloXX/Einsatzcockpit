"""Gemeinsame Normalisierung von Telefonnummern.

``telefon_kompakt`` ist fuer technische ``tel:``-Links sowie fuer Vergleiche
und Deduplizierung gedacht. ``telefon_normalisiert`` bildet dagegen die
kanonische Identitaet einer Rufnummer und ist fuer die SMS-Freigabe von
Objektkontakten vorgesehen. Es gibt bewusst zwei Varianten, weil eine
Gleichsetzung von ``00`` und ``+`` bestehende Rufnummern-Zuordnungen, etwa
beim sicherheitsrelevanten PIN-Login, veraendern wuerde.
"""
import re

_TRENNZEICHEN_RE = re.compile(r"[\s\-()/]")


def telefon_kompakt(wert: str | None) -> str:
    """Entfernt Leerzeichen, Bindestriche, Klammern und Schraegstriche."""
    return _TRENNZEICHEN_RE.sub("", wert or "")


def telefon_normalisiert(wert: str | None) -> str:
    """Liefert die kanonische Rufnummern-Identitaet, mit ``00`` als ``+``."""
    kompakt = telefon_kompakt(wert)
    return "+" + kompakt[2:] if kompakt.startswith("00") else kompakt


def telefon_e164(wert: str | None) -> str | None:
    """Normalisiert und prüft strikt E.164 (+ und 8 bis 15 Ziffern)."""
    if not wert:
        return None
    normalisiert = telefon_normalisiert(wert)
    if normalisiert and re.fullmatch(r"\+[0-9]{8,15}", normalisiert):
        return normalisiert
    return None
