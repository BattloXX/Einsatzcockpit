"""Strukturierte Inhalte des oeffentlichen Bereichs (Pre-Login-Website).

Alle Marketing-Inhalte (Feature-Liste, Roadmap, Chips, Prinzipien, Status-Tags,
Navigation) liegen hier als einfache Python-Daten -- so lassen sie sich pflegen,
ohne die Templates anzufassen (Briefing: "Inhalte als strukturierte Daten,
nicht hart in Markup"). Die Status-Tags (Verfuegbar/Beta/In Arbeit/Geplant) sind
bewusst hier zentral, damit sie vor Veroeffentlichung leicht mit dem echten Stand
abgeglichen werden koennen.

Die Icon-Namen verweisen auf das SVG-Set in
``templates/public/_icons.html`` (Makro ``icon(name)``).
"""
from __future__ import annotations

# Status-Metadaten: Schluessel -> Anzeigelabel + CSS-Modifier (public.css:
# .pub-status--<cls> / .pub-tl-item--<cls>).
STATUS: dict[str, dict[str, str]] = {
    "verfuegbar": {"label": "Verfügbar", "cls": "avail"},
    "beta":       {"label": "Beta",      "cls": "beta"},
    "in_arbeit":  {"label": "In Arbeit", "cls": "live"},
    "geplant":    {"label": "Geplant",   "cls": "planned"},
}

# Hauptnavigation (Header + Footer). Reihenfolge = Anzeige.
NAV: list[dict[str, str]] = [
    {"slug": "start",      "label": "Start",            "href": "/"},
    {"slug": "funktionen", "label": "Funktionen",       "href": "/funktionen"},
    {"slug": "ueber",      "label": "Über das Projekt", "href": "/ueber-das-projekt"},
    {"slug": "roadmap",    "label": "Roadmap",          "href": "/roadmap"},
]

# Die sechs Highlight-Module. Genutzt auf der Startseite (Karten-Grid) und auf
# /funktionen (alternierende Split-Sektionen mit Anker). ``tagline`` = kurze
# Kartenbeschreibung, ``bullets`` = Faehigkeiten auf der Funktionsseite.
FEATURES: list[dict] = [
    {
        "key": "lagefuehrung",
        "anchor": "lagefuehrung",
        "title": "Lageführung",
        "status": "verfuegbar",
        "icon": "map",
        "tagline": "Digitale Echtzeit-Lagekarte: taktische Zeichen, Bereiche und "
                   "Marker – gemeinsam bearbeitet.",
        "bullets": [
            "Taktische Zeichen, Bereiche und Marker",
            "Gleichzeitige Bearbeitung durch mehrere Nutzer",
            "Einsatzabschnitte und Kartenlagen",
            "Export für Dokumentation und Übergabe",
        ],
    },
    {
        "key": "alarmierung",
        "anchor": "alarmierung",
        "title": "Alarmierung & Print-Gateway",
        "status": "verfuegbar",
        "icon": "bell",
        "tagline": "Anbindung an die lokale Infrastruktur im Feuerwehrhaus – "
                   "Ausdruck und Alarmmonitor, auch bei Netzstörungen.",
        "bullets": [
            "Anbindung an die lokale Infrastruktur im Feuerwehrhaus (ECPG)",
            "Automatische Einsatzausdrucke",
            "Alarmmonitor",
            "Robuste Alarmverarbeitung auch bei Netzstörungen",
        ],
    },
    {
        "key": "grossschadenslage",
        "anchor": "grossschadenslage",
        "title": "Großschadenslage (SKKM)",
        "status": "verfuegbar",
        "icon": "hub",
        "tagline": "Stabsarbeit nach SKKM: Kräfte, Ressourcen, Einsatztagebuch "
                   "und taktische Karte für ausgedehnte Lagen.",
        "bullets": [
            "Kräfte- und Ressourcenübersicht",
            "Einsatztagebuch und Aufgabenverteilung im Stab",
            "Überörtliche Marker",
            "Taktische Karte für ausgedehnte Lagen",
        ],
    },
    {
        "key": "wetter",
        "anchor": "wetter",
        "title": "Wetterwarnungen",
        "status": "verfuegbar",
        "icon": "cloud",
        "tagline": "Konfigurierbare Warnregeln auf Basis amtlicher Wetterdaten – "
                   "automatisch bei Unwetter-, Hochwasser- und Sturmlagen.",
        "bullets": [
            "Warnregeln auf Basis amtlicher Wetterdaten (GeoSphere Austria, Kachelmannwetter)",
            "Automatische Benachrichtigungen bei Unwetterlagen",
            "Hochwasser- und Sturmwarnungen",
        ],
    },
    {
        "key": "objekte",
        "anchor": "objekte",
        "title": "Objektverwaltung",
        "status": "beta",
        "icon": "clipboard",
        "tagline": "Einsatzrelevante Objekte zentral: Daten, Pläne, "
                   "Ansprechpartner und Besonderheiten – im Einsatz sofort abrufbar.",
        "bullets": [
            "Objektdaten, Pläne und Dokumente",
            "Ansprechpartner",
            "Besonderheiten – im Einsatz sofort abrufbar",
        ],
    },
    {
        "key": "protokollierung",
        "anchor": "protokollierung",
        "title": "Protokollierung & Dokumentation",
        "status": "verfuegbar",
        "icon": "doc",
        "tagline": "Lückenlose Dokumentation im Einsatz und im Alltagsbetrieb – "
                   "auswertbar und jederzeit abrufbar.",
        "bullets": [
            "Digitales Fahrtenbuch für alle Fahrzeugbewegungen",
            "Atemschutzgeräteprüfung mit Prüfhistorie",
            "Mannschaftslisten mit Anwesenheitserfassung (Einsätze, Übungen, Veranstaltungen)",
            "Auswertbar und jederzeit abrufbar",
        ],
    },
]

# "Und vieles mehr" – Chip-Grid auf /funktionen. ``tag`` = kleiner Mono-Zusatz.
MORE_CHIPS: list[dict[str, str]] = [
    {"label": "Leitstellen-Anbindung", "tag": "GPS · FMS"},
    {"label": "Mandantenfähigkeit", "tag": ""},
    {"label": "Single Sign-On", "tag": "Entra ID"},
    {"label": "Bild-Annotation & Skizzen", "tag": ""},
    {"label": "Drohnen-/UAS-Modul", "tag": ""},
    {"label": "Teilnehmerlisten", "tag": ""},
    {"label": "Berichte & Auswertungen", "tag": ""},
    {"label": "KI-Unterstützung", "tag": "in Entwicklung"},
]

# Roadmap – gruppiert nach Status. Reihenfolge der Gruppen = Anzeige.
# "verfuegbar" wird aus FEATURES abgeleitet (siehe available_roadmap_items()).
ROADMAP: list[dict] = [
    {
        "status": "in_arbeit",
        "items": [
            {"title": "KI-Unterstützung",
             "text": "Einsatzanalyse & Assistenz direkt im Einsatzverlauf."},
            {"title": "Objektverwaltung (Ausbau)",
             "text": "Erweiterung um weitere Objektdaten und Auswertungen."},
            {"title": "Drohnen-/UAS-Modul",
             "text": "Umsetzung nach der Richtlinie RL-UAS."},
        ],
    },
    {
        "status": "geplant",
        "items": [
            {"title": "Mobile Optimierungen",
             "text": "Weitere Verbesserungen für die Nutzung am Einsatzort."},
            {"title": "Erweiterte Berichte & Statistik",
             "text": "Mehr Auswertungen für Nachbereitung und Verwaltung."},
            {"title": "Weitere Leitstellen-Schnittstellen",
             "text": "Anbindung zusätzlicher Leitstellen- und Fremdsysteme."},
        ],
    },
]

# Prinzipien-Grid auf /ueber-das-projekt.
PRINCIPLES: list[dict[str, str]] = [
    {"icon": "heart", "title": "Nicht-kommerziell",
     "text": "Kein Verkauf, keine Lizenzkosten – aus Überzeugung."},
    {"icon": "code", "title": "Open Source",
     "text": "Der Quellcode ist offen einsehbar und auditierbar."},
    {"icon": "shield", "title": "Aus der Praxis",
     "text": "Entstanden im aktiven Feuerwehrdienst, gewachsen aus echten Einsätzen."},
    {"icon": "server", "title": "Datensouveränität",
     "text": "Betrieb auf eigener Infrastruktur möglich – keine Cloud-Pflicht."},
]

# Fakten-Leiste (Startseite).
TRUST_FACTS: list[str] = [
    "100 % Open Source",
    "Keine Kosten, keine Lizenzen",
    "Entwickelt aus der Einsatzpraxis",
]


def available_roadmap_items() -> list[dict[str, str]]:
    """Die verfuegbaren Highlight-Module als Roadmap-Kurzform (Status "Verfügbar")."""
    return [{"title": f["title"], "text": f["tagline"]} for f in FEATURES]
