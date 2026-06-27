"""Flugbuch-Berechnungen und Checklisten-Seeds (RL Anh. 8.2 v9, PR 4)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

# ── Berechnungen (RL Anh. 8.2 v9) ────────────────────────────────────────────

def berechne_flugsicherheitswerte(hoehe_m: float) -> dict[str, float]:
    """Contingency Volume, GRB, Mindestabstand Menschenansammlung."""
    cv = hoehe_m * 0.10
    grb = hoehe_m + cv
    abstand = grb + cv + 120.0
    return {
        "contingency_volume_m": round(cv, 1),
        "ground_risk_buffer_m": round(grb, 1),
        "abstand_menschenansammlung_m": round(abstand, 1),
    }


def pruefe_1zu1_regel(flughoehe_m: float, abstand_zu_unbeteiligten_m: float | None) -> bool:
    """Horizontaler Mindestabstand ≥ Flughöhe (1:1-Regel)."""
    if abstand_zu_unbeteiligten_m is None:
        return False
    return abstand_zu_unbeteiligten_m >= flughoehe_m


def berechne_dauer_min(start_at: datetime | None, landung_at: datetime | None) -> int | None:
    if start_at is None or landung_at is None:
        return None
    delta = landung_at - start_at
    return max(0, int(delta.total_seconds() / 60))


# ── Checklisten-Seeds (Anh. 8.2 v9) ──────────────────────────────────────────

CHECKLISTE_VORFLUG: list[dict[str, Any]] = [
    {"key": "v01",
     "label": "Grundlage (OPEN A1/A2/A3 oder Specific/Bescheid) festgelegt?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v02", "label": "Betriebsart VLOS / BVLOS festgelegt?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v03", "label": "Gemeindegebiet / Bebauungsklasse geprüft?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v04", "label": "Flughöhe festgelegt (max. 120 m über Grund OPEN)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v05", "label": "Contingency Volume (10 % der Flughöhe) berechnet?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v06", "label": "Ground Risk Buffer (Flughöhe + CV) berechnet?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v07",
     "label": "Mindestabstand Menschenansammlung (GRB + CV + 120 m) eingehalten?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v08",
     "label": "1:1-Regel: Horizontalabstand zu Unbeteiligten ≥ Flughöhe?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v09",
     "label": "Rollenbesetzung vollständig (Pilot + Luftraumbeobachter, ≥2 Pers.)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v10", "label": "Pilot: Freigabestatus grün (Zertifikate + Currency)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v11", "label": "Gerät: Registriernummer + Versicherung + Status aktiv?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v12", "label": "Wetter: Wind / Sicht / Niederschlag akzeptabel?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v13", "label": "Nachtbetrieb: Beleuchtung sichergestellt (RL 4.3)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v14",
     "label": "Kommunikationsmatrix bekannt (TETRA-Rufname/Sprechgruppe)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v15",
     "label": "Risikobewertung durchgeführt und dokumentiert (RL 4.11)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v16", "label": "Pilotenzone abgesperrt (funk-/mobilfrei, RL 5.5)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v17", "label": "Datenschutz: Aufklärungspflicht erfüllt (RL 4.9)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v18",
     "label": "BVLOS: Bescheid-Nr. vorhanden und mitgeführt (RL 4.4)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v19",
     "label": "Gerät: Vorflug-Sichtkontrolle i.O. (Propeller, Akku, Kamera)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "v20", "label": "Gefahrgut/Last: Genehmigung vorhanden (RL 4.6)?",
     "erledigt": False, "bemerkung": ""},
]

CHECKLISTE_NACHFLUG: list[dict[str, Any]] = [
    {"key": "n01", "label": "Gerät sicher gelandet und gesichert?", "erledigt": False, "bemerkung": ""},
    {"key": "n02", "label": "Akkus entnommen, Lagerung OK?", "erledigt": False, "bemerkung": ""},
    {"key": "n03", "label": "Flugbuch ausgefüllt (Dauer, Ort, Pilot, Gerät)?", "erledigt": False, "bemerkung": ""},
    {"key": "n04", "label": "Schäden / Auffälligkeiten dokumentiert?", "erledigt": False, "bemerkung": ""},
    {"key": "n05", "label": "Unfall / Störung: Meldung eingeleitet (PR 5)?", "erledigt": False, "bemerkung": ""},
    {"key": "n06",
     "label": "Medien: DSGVO-Workflow eingeleitet (Löschfrist gesetzt)?",
     "erledigt": False, "bemerkung": ""},
    {"key": "n07", "label": "EL über Abschluss informiert / beim EL abgemeldet?", "erledigt": False, "bemerkung": ""},
]


def inhalt_hash(flug_data: dict) -> str:
    """SHA-256-Hash für Append-Only-Audit (Verbesserung 9)."""
    canonical = json.dumps(flug_data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
