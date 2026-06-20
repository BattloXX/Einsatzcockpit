"""Notfall-/Unfall-Workflow Service (RL 4.7, Anh. 8.3/8.4, PR 5)."""
from __future__ import annotations

import zoneinfo
from datetime import date, datetime

# Notfallcheckliste (Anh. 8.3) – 7 Vorfälle
NOTFALLCHECKLISTE = [
    {
        "id": "nf1",
        "titel": "Ausfall Fernsteuerung / Verbindungsverlust",
        "verhalten": "Drohne aktiviert RTH (Return to Home) automatisch.",
        "massnahmen": [
            "Pilot versucht Verbindung wiederherzustellen",
            "Absperrradius Pilotenzone einhalten – niemand unter RTH-Route",
            "Gegebenenfalls Landebefehl manuell auslösen wenn Verbindung wiederhergestellt",
            "Landung beobachten – bei Absturz sofort Meldung",
        ],
    },
    {
        "id": "nf2",
        "titel": "Kommunikationsverlust (TETRA/Funk)",
        "verhalten": "Betrieb nur mit direktem Sichtkontakt Pilot ↔ Luftraumbeobachter.",
        "massnahmen": [
            "Alternative Kommunikation (Handzeichen, Handy) aktivieren",
            "EL informieren (kein Tetra-Kontakt)",
            "Flug beenden wenn keine sichere Kommunikation möglich",
        ],
    },
    {
        "id": "nf3",
        "titel": "Ausfall Kamera / Payload",
        "verhalten": "Flug kann fortgesetzt werden wenn Drohne sicher steuerbar.",
        "massnahmen": [
            "Einsatzleiter Drohne informieren",
            "Auftrag neu bewerten – ist Weiterflug sinnvoll?",
            "Gerät nach Landung prüfen, im Wartungsbuch dokumentieren",
        ],
    },
    {
        "id": "nf4",
        "titel": "Akku-Warnung (gelb / niedriger Ladestand)",
        "verhalten": "30 % Restkapazität – erhöhte Aufmerksamkeit, Rückkehr einplanen.",
        "massnahmen": [
            "Pilot: RTH-Route freimachen, Landezone bereit",
            "Einsatzleiter Drohne informieren",
            "Flug beenden wenn Auftrag abgeschlossen",
        ],
    },
    {
        "id": "nf5",
        "titel": "Akku-Warnung KRITISCH (unter 15 %)",
        "verhalten": "Sofortige Rückkehr / Notlandung.",
        "massnahmen": [
            "Sofortige Einleitung RTH oder manuelle Landung",
            "Pilotenzone freimachen – alle zurück",
            "Landung beobachten, Gerät sichern",
            "Unfall/Störung dokumentieren",
        ],
    },
    {
        "id": "nf6",
        "titel": "Akku leer / ungesteuerte Landung",
        "verhalten": "Drohne landet unkontrolliert.",
        "massnahmen": [
            "Bereich absperren – keine Personen in Absturzzone",
            "Gerät sichern – Akku entfernen",
            "Verletzungscheck Personen im Umfeld",
            "Unfall melden: Pilot → Teamleiter → Stützpunktleiter → ACG (Anh. 8.4)",
        ],
    },
    {
        "id": "nf7",
        "titel": "Absturz / Havarie",
        "verhalten": "Gerät unkontrolliert abgestürzt.",
        "massnahmen": [
            "Absturzzone absperren (Brandgefahr Akku!)",
            "Erstversorgung verletzter Personen",
            "Brandbekämpfung wenn erforderlich",
            "Unfallmeldung: Pilot → Teamleiter → Stützpunktleiter → Behörde ACG → EL → LFV (RL 4.7)",
            "Gerät sicherstellen (Unfalluntersuchung) – nicht verändern",
        ],
    },
]

_WIEN_TZ = zoneinfo.ZoneInfo("Europe/Vienna")


def lokal_zu_utc(datum_lokal: date, zeit_lokal_str: str) -> tuple[date, str]:
    """Konvertiert lokale Ortszeit (Europe/Vienna) nach UTC."""
    try:
        h, m = map(int, zeit_lokal_str.strip().split(":"))
    except (ValueError, AttributeError):
        return datum_lokal, zeit_lokal_str

    dt_lokal = datetime(datum_lokal.year, datum_lokal.month, datum_lokal.day, h, m,
                        tzinfo=_WIEN_TZ)
    dt_utc = dt_lokal.astimezone(zoneinfo.ZoneInfo("UTC"))
    return dt_utc.date(), dt_utc.strftime("%H:%M")
