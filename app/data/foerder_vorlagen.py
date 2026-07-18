"""Read-only Vorlagen (Templates) für den Förderstrecken-Gerätekatalog.

Kein DB-Zustand: dieses Modul liefert nur fixe Vorlagendaten. „Aus Vorlage anlegen"
(app/routers/ui_foerderstrecke_admin.py) kopiert eine dieser Vorlagen in eine
frei editierbare, org-eigene Katalogzeile (quelle="vorlage", vorlage_key=<key>).

Kennlinien sind Q-H-Punktlisten je Drehzahlstufe (Q in l/min, H in m):
{ "<rpm-oder-stufe>": [[Q, H], …] }. Die Höhe H [m] entspricht ≈ 10·p [bar].
Werte aus Datenblättern/Normwerten digitalisiert — vor Produktivnutzung bzw. über
Übungsmessungen (Kalibrierung, PR 7) feinjustieren.
"""
from __future__ import annotations

from typing import Any

# ── Pumpen-Vorlagen ─────────────────────────────────────────────────────────────
# Jede Vorlage: key → {name, beschreibung, felder{…Spalten von FoerderPumpenTyp…}}.
# felder verwendet dieselben Namen wie das Modell; kennlinien/verbrauch/npshr als
# Python-Strukturen (der Router serialisiert sie nach JSON).

PUMPEN_VORLAGEN: dict[str, dict[str, Any]] = {
    "hlp_16000_pas200hf": {
        "name": "HLP 16.000 (Atlas Copco PAS 200HF CNP)",
        "beschreibung": "Hochleistungspumpe 950 m³/h · 3× F-150 Druck, 4× 150 Saug · 2 t, nur Kran/WLF-Standorte.",
        "felder": {
            "kennlinien": {"2000": [[0, 53], [5000, 48], [8300, 42], [10800, 36], [13300, 28], [15800, 18]]},
            "druck_anschluss_dn": 150,
            "druck_parallel_max": 3,
            "saug_anschluss_dn": 150,
            "saug_parallel_max": 4,
            "max_ansaughoehe_m": 7.5,
            "min_eingangsdruck_bar": 1.5,
            "max_ausgangsdruck_bar": None,
            "tank_l": 415,
            "verbrauch": {"1400": 8.2, "2000": 19.5},
            "hinweise": "2.000 kg (1280×2840×1950 mm) — nur Kran-/Hakengerät-Standorte. "
                        "Kennlinie 2000 rpm aus Performance-Kurve digitalisiert; vor Produktivnutzung "
                        "gegen Atlas-Copco-Datenblatt feinjustieren, weitere Drehzahlstufen erfassbar.",
        },
    },
    "hlp_8000_pas150": {
        "name": "HLP 8.000 (Atlas Copco PAS 150 Hardhat)",
        "beschreibung": "500 m³/h · 1× F-150 Druck, 1× 150 Saug · Tank 200 l → Laufzeit 24 h+.",
        "felder": {
            "kennlinien": {"2000": [[0, 37], [1700, 34], [3300, 31], [5700, 26], [7200, 18], [8300, 10]]},
            "druck_anschluss_dn": 150,
            "druck_parallel_max": 1,
            "saug_anschluss_dn": 150,
            "saug_parallel_max": 1,
            "max_ansaughoehe_m": 7.5,
            "min_eingangsdruck_bar": 1.5,
            "max_ausgangsdruck_bar": None,
            "tank_l": 200,
            "verbrauch": {"2000": 8.4},
            "hinweise": "Kennlinie 2000 rpm digitalisiert; vor Produktivnutzung feinjustieren.",
        },
    },
    "ts_1600_fox3": {
        "name": "TS 1600 (Rosenbauer FOX 3)",
        "beschreibung": "Tragkraftspritze ~167 kg · 1–2× B-75 Druck, A-110 Saug · flache Kennlinie → ideal als Verstärkerpumpe.",
        "felder": {
            # FOX 3 bei 3 m Saughöhe: 1000→15 bar, 1600→10 bar, 2000→3 bar; Schließdruck ~16,5 bar
            "kennlinien": {"nenn": [[0, 165], [1000, 150], [1600, 100], [2000, 30]]},
            "druck_anschluss_dn": 75,
            "druck_parallel_max": 2,
            "saug_anschluss_dn": 110,
            "saug_parallel_max": 1,
            "max_ansaughoehe_m": 7.5,
            "min_eingangsdruck_bar": 1.5,
            "max_ausgangsdruck_bar": None,
            "tank_l": 20,
            "verbrauch": {"nenn": 10.0},
            "hinweise": "~167 kg betriebsbereit → Standort auch abseits befestigter Wege. "
                        "Nennleistung bei 3 m Saughöhe. Tank 20 l → Laufzeit ~1,5–2,5 h "
                        "(Nachtank-Hinweis beachten). NPSH-Derating optional erfassbar.",
        },
    },
    "ts_1200": {
        "name": "TS 1200 (Tragkraftspritze, PFPN 10-1000)",
        "beschreibung": "Genormte Tragkraftspritze · 1–2× B-75 Druck, A-110 Saug · Kennlinie als Normwert-Startkurve.",
        "felder": {
            # Normwert-Startkurve PFPN 10-1000 (≈ TS 8/8-Nachfolger); über Nassbewerb kalibrieren.
            "kennlinien": {"nenn": [[0, 140], [800, 110], [1000, 100], [1200, 85], [1600, 55], [2000, 20]]},
            "druck_anschluss_dn": 75,
            "druck_parallel_max": 2,
            "saug_anschluss_dn": 110,
            "saug_parallel_max": 1,
            "max_ansaughoehe_m": 7.5,
            "min_eingangsdruck_bar": 1.5,
            "max_ausgangsdruck_bar": None,
            "tank_l": None,
            "verbrauch": {},
            "hinweise": "Kennlinie = Normwerte (PFPN 10-1000) als Startkurve — vor Produktivnutzung "
                        "bzw. über einen Nassbewerb kalibrieren (Kalibrierung/Übungsmessungen).",
        },
    },
    "fremdpumpe_fpn": {
        "name": "Fremdpumpe generisch (FPN 10-2000 / 10-1000)",
        "beschreibung": "Normpumpe für gemischte Ketten mit Nachbarfeuerwehren · Kennlinie anpassen.",
        "felder": {
            "kennlinien": {"nenn": [[0, 140], [1000, 100], [2000, 60], [3000, 20]]},
            "druck_anschluss_dn": 75,
            "druck_parallel_max": 2,
            "saug_anschluss_dn": 110,
            "saug_parallel_max": 1,
            "max_ansaughoehe_m": 7.5,
            "min_eingangsdruck_bar": 1.5,
            "max_ausgangsdruck_bar": None,
            "tank_l": None,
            "verbrauch": {},
            "hinweise": "Generische Normpumpe für gemischte Relais-Ketten; Werte an die tatsächliche "
                        "Fremdpumpe anpassen.",
        },
    },
}

# ── Schlauch-Vorlagen ───────────────────────────────────────────────────────────
# k_verlust: bar je 100 m bei 1000 l/min (quadratisch skaliert in der Engine).
# Herleitung: B-75 → 1,56 (= 1,0 bar/100 m @ 800 l/min); Skalierung ∝ 1/d⁵:
# A-110 ≈ B-75/6,8 ≈ 0,23; F-150 ≈ B-75/32 ≈ 0,049.

SCHLAUCH_VORLAGEN: dict[str, dict[str, Any]] = {
    "f_150": {
        "name": "F-150 (Storz F, 150 mm)",
        "beschreibung": "Großschlauch für HLP-Hauptleitung · sehr geringer Verlust.",
        "felder": {
            "kuerzel": "F-150",
            "durchmesser_mm": 150,
            "k_verlust": 0.049,
            "element_laenge_m": 30,
            "max_betriebsdruck_bar": 10.0,
        },
    },
    "a_110": {
        "name": "A-110 (Storz A, 110 mm)",
        "beschreibung": "Saug-/Zubringerleitung 110 mm.",
        "felder": {
            "kuerzel": "A-110",
            "durchmesser_mm": 110,
            "k_verlust": 0.23,
            "element_laenge_m": 20,
            "max_betriebsdruck_bar": 12.0,
        },
    },
    "b_75": {
        "name": "B-75 (Storz B, 75 mm)",
        "beschreibung": "Standard-Druckleitung 75 mm · Referenz für k-Wert-Herleitung.",
        "felder": {
            "kuerzel": "B-75",
            "durchmesser_mm": 75,
            "k_verlust": 1.56,
            "element_laenge_m": 20,
            "max_betriebsdruck_bar": 16.0,
        },
    },
}


def pumpen_vorlage(key: str) -> dict[str, Any] | None:
    return PUMPEN_VORLAGEN.get(key)


def schlauch_vorlage(key: str) -> dict[str, Any] | None:
    return SCHLAUCH_VORLAGEN.get(key)
