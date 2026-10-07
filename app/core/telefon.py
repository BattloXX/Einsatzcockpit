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


def telefon_identitaet_at(wert: str | None) -> str:
    """Vergleichsschluessel fuer Rufnummern mit oesterreichischer Vorwahl als Standard.

    ``0664 1234567``, ``+43 664 1234567`` und ``0043 664 1234567`` ergeben denselben
    Schluessel ``+436641234567``. Nationale Nummern (fuehrende ``0``) werden als
    oesterreichisch gewertet; andere Formate bleiben wie ``telefon_normalisiert``.
    """
    normalisiert = telefon_normalisiert(wert)
    if normalisiert.startswith("0"):
        return "+43" + normalisiert[1:]
    return normalisiert


def ist_oesterreichische_mobilnummer(wert: str | None) -> bool:
    """Erkennt österreichische Mobilvorwahlen in nationaler und E.164-Schreibweise.

    Reale Kontaktdaten aus BMA- und Excel-Importen verwenden sowohl ``0...``
    als auch ``+43...``; deshalb werden beide Formate bewusst unterstützt.
    """
    normalisiert = telefon_normalisiert(wert)
    if normalisiert.startswith("+43"):
        vorwahl_text = normalisiert[3:6]
    elif normalisiert.startswith("0"):
        vorwahl_text = normalisiert[1:4]
    else:
        return False
    if not vorwahl_text.isdigit():
        return False
    vorwahl = int(vorwahl_text)
    return vorwahl in {650, 651, 652, 653, 655, 657} or 659 <= vorwahl <= 661 or 663 <= vorwahl <= 699


def telefon_e164(wert: str | None) -> str | None:
    """Normalisiert und prüft strikt E.164 (+ und 8 bis 15 Ziffern)."""
    if not wert:
        return None
    normalisiert = telefon_normalisiert(wert)
    if normalisiert and re.fullmatch(r"\+[0-9]{8,15}", normalisiert):
        return normalisiert
    return None
