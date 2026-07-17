"""Evakuierungs-/Absperrzonen (tabellenbasiert).

Liefert konzentrische Zonen (Radius in Metern + Farbe + Beschriftung) fuer die
Lagekarte. Grundlage sind die generischen Abstaende des Emergency Response
Guidebook (ERG 2020, frei; US DOT/Transport Canada) fuer Gefahrgut ohne
stoffspezifische Karte:
- kleine Freisetzung  -> Sofort-Sperrbereich 50 m
- grosse Freisetzung  -> Sofort-Sperrbereich 100 m
- Tank/Brand betroffen -> Sperrbereich 100 m + Evakuierung 800 m in alle Richtungen

Bewusst NICHT stoffspezifisch (TIH-Tabellen sind sicherheitskritisch und werden
nicht handgepflegt) — die Einsatzkraft waehlt ein Preset oder gibt Radien vor.
"""
from __future__ import annotations

_FARBEN = {"sperr": "#dc2626", "evak": "#f59e0b"}
_LABELS = {"sperr": "Sofort-Sperrbereich", "evak": "Evakuierungs-/Warnbereich"}

# Presets: rolle -> Radius (m). ERG-2020-generische Abstaende.
PRESETS: dict[str, dict] = {
    "klein": {"label": "Kleine Freisetzung", "roh": [("sperr", 50)]},
    "gross": {"label": "Grosse Freisetzung", "roh": [("sperr", 100)]},
    "brand": {"label": "Tank/Brand betroffen", "roh": [("sperr", 100), ("evak", 800)]},
}


def _zone(rolle: str, radius_m: int) -> dict:
    return {
        "rolle": rolle,
        "radius_m": int(radius_m),
        "farbe": _FARBEN.get(rolle, "#dc2626"),
        "label": _LABELS.get(rolle, "Gefahrenbereich"),
    }


def zonen(
    preset: str | None = None,
    sperr_radius_m: int | None = None,
    evak_radius_m: int | None = None,
) -> list[dict]:
    """Zonenliste (grosster Radius zuerst, damit er beim Zeichnen unten liegt).

    - preset (klein/gross/brand) hat Vorrang.
    - sonst werden sperr_radius_m/evak_radius_m als individuelle Zonen genutzt.
    """
    roh: list[tuple[str, int]] = []
    if preset and preset in PRESETS:
        roh = list(PRESETS[preset]["roh"])
    else:
        if sperr_radius_m and sperr_radius_m > 0:
            roh.append(("sperr", int(sperr_radius_m)))
        if evak_radius_m and evak_radius_m > 0:
            roh.append(("evak", int(evak_radius_m)))
    zonen_liste = [_zone(rolle, r) for rolle, r in roh if r > 0]
    zonen_liste.sort(key=lambda z: z["radius_m"], reverse=True)
    return zonen_liste
