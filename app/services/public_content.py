"""Strukturierte Inhalte des oeffentlichen Bereichs (Pre-Login-Website).

Alle Marketing-Inhalte (Feature-Liste, Roadmap, Chips, Prinzipien, Status-Tags,
Navigation) liegen hier als einfache Python-Daten -- so lassen sie sich pflegen,
ohne die Templates anzufassen (Briefing: "Inhalte als strukturierte Daten,
nicht hart in Markup"). Die Status-Tags (Verfuegbar/Beta/In Arbeit/Geplant) sind
bewusst hier zentral, damit sie vor Veroeffentlichung leicht mit dem echten Stand
abgeglichen werden koennen.

Die Icon-Namen verweisen auf das globale Icon-System (``icon(name)``).
"""

from __future__ import annotations

# Status-Metadaten: Schluessel -> Anzeigelabel + CSS-Modifier (public.css:
# .pub-status--<cls> / .pub-tl-item--<cls>).
STATUS: dict[str, dict[str, str]] = {
    "verfuegbar": {"label": "Verfügbar", "cls": "avail"},
    "beta": {"label": "Beta", "cls": "beta"},
    "in_arbeit": {"label": "In Arbeit", "cls": "live"},
    "geplant": {"label": "Geplant", "cls": "planned"},
}

# Hauptnavigation (Header + Footer). Reihenfolge = Anzeige.
NAV: list[dict[str, str]] = [
    {"slug": "start", "label": "Start", "href": "/"},
    {"slug": "funktionen", "label": "Funktionen", "href": "/funktionen"},
    {"slug": "ueber", "label": "Über das Projekt", "href": "/ueber-das-projekt"},
    {"slug": "roadmap", "label": "Roadmap", "href": "/roadmap"},
]

# Die fuenf Highlight-Bereiche. Genutzt auf der Startseite (Karten-Grid) und auf
# /funktionen (alternierende Split-Sektionen mit Anker). ``tagline`` = kurze
# Kartenbeschreibung, ``bullets`` = Faehigkeiten auf der Funktionsseite.
FEATURES: list[dict] = [
    {
        "key": "einsatzinfo",
        "anchor": "einsatzinfo",
        "title": "Einsatzinfo",
        "status": "verfuegbar",
        "icon": "bell",
        "image": "/seite/bild/Lagefuehrung.png",
        "tagline": "Alle wichtigen Einsatzinformationen sofort und übersichtlich – "
        "vom Feuerwehrhaus bis zur Einsatzstelle.",
        "intro": "Mit dem Alarm steht alles Wichtige auf einen Blick bereit – für Mannschaft "
        "und Einsatzleitung, am PC, Tablet oder Handy. So sind Einsatzdaten, Anfahrt "
        "und relevante Informationen schon vor dem Ausrücken griffbereit.",
        "wiki": "Anwender-Einsatzinfo",
        "bullets": [
            "Stichwort, Einsatzort und Alarmierungsdaten sofort im Überblick",
            "Alarmierte Fahrzeuge mit aktuellem Status",
            "Verknüpfte Objekte mit Einsatzunterlagen, BMA-Daten und Gefahrenhinweisen",
            "Karte mit Anfahrt, Hydranten und Gefahren in der Umgebung",
            {
                "text": "Hinweis auf Straßensperren entlang der Anfahrt",
                "tag": "optional",
                "wiki": "Anwender-Strassensperren",
            },
            {"text": "Alarm-Infoscreen für Wandmonitore im Feuerwehrhaus", "wiki": "Administration-Alarm-Infoscreen"},
            {"text": "Mobil abrufbar – auch per SMS-/Teams-Link ohne Login", "wiki": "Anwender-Mobile-Nutzung-PWA"},
        ],
    },
    {
        "key": "einsatzboard",
        "anchor": "einsatzboard",
        "title": "Einsatzboard",
        "status": "verfuegbar",
        "icon": "layout-dashboard",
        "image": "/seite/bild/Protokollierung.png",
        "tagline": "Die gesamte Einsatzführung in einer gemeinsamen, aktuellen Übersicht.",
        "intro": "Auf dem Einsatzboard führt die Einsatzleitung Aufträge, Meldungen und Kräfte "
        "an einem Ort zusammen. Alle Beteiligten sehen Änderungen sofort und arbeiten "
        "mit demselben aktuellen Lagebild.",
        "wiki": "Anwender-Kanban-Board-bedienen",
        "bullets": [
            {
                "text": "Aufträge, Meldungen, Fahrzeuge und Personen auf einem Kanban-Board",
                "wiki": "Anwender-Auftraege-und-Meldungen",
            },
            "Änderungen sofort auf allen Geräten – mehrere Benutzer arbeiten gleichzeitig",
            {
                "text": "Taktische Lagekarte mit genormten Zeichen und Einsatzabschnitten",
                "wiki": "Anwender-Taktische-Lagekarte",
            },
            "Status und Verlauf jedes Auftrags nachvollziehbar",
            {
                "text": "Einsatzjournal, Audit-Log und PDF-Bericht für die Nachbereitung",
                "wiki": "Anwender-Archiv-und-PDF-Export",
            },
            {
                "text": "Atemschutzüberwachung mit Rückzugsdruck und Zeitwarnungen",
                "wiki": "Anwender-Atemschutzueberwachung",
            },
            {
                "text": "Förderstrecken-Planer für Löschwasser über lange Wege",
                "wiki": "Anwender-Foerderstrecken-Planer",
            },
        ],
    },
    {
        "key": "grossschadenslage",
        "anchor": "grossschadenslage",
        "title": "Großschadenslage (GSL)",
        "status": "verfuegbar",
        "icon": "topology-star-3",
        "image": "/seite/bild/Grossschadenslage.png",
        "tagline": "Auch komplexe Lagen strukturiert führen – von der Einsatzleitung bis zur einzelnen Einheit.",
        "intro": "Für ausgedehnte oder parallele Ereignisse bringt die GSL klare Abläufe in die "
        "Einsatzführung. Einsatzleitung, Stab und Einheiten arbeiten vom selben Lagebild "
        "aus, auch wenn die Verbindung an der Einsatzstelle schwankt.",
        "wiki": "Anwender-Grosschadenslage",
        "bullets": [
            "Einsatzstellen durchlaufen Erkundung, Bewertung, Disposition und Abarbeitung",
            {
                "text": "GSL-Board mit Abschnitten und Kräfteübersicht",
                "wiki": "Anwender-GSL-Sektoren-und-Planung",
            },
            {
                "text": "Ressourcen und Gruppenkommandanten zentral verwalten",
                "wiki": "Anwender-GSL-Ressourcenverwaltung",
            },
            {
                "text": "Einheitenansicht für Fahrzeug-Tablets und Smartphones",
                "tag": "Beta",
                "wiki": "Anwender-GSL-Einheitenansicht",
            },
            "Zugang für Einheiten per QR-Code oder SMS – ohne eigenes Benutzerkonto",
            "Aufträge erreichen die Einheit automatisch",
            "Lagemeldungen, Fotos und Status direkt von der Einsatzstelle – auch bei schlechtem Netz",
            {"text": "GSL-Lagekarte, Journale und Druckblätter", "wiki": "Anwender-Grosschadenslage-Karte"},
            {
                "text": "Stabsarbeit mit SKKM-Regelkreis (Lage → Auftrag → Kontrolle)",
                "wiki": "Anwender-GSL-Stab-und-Journal",
            },
        ],
    },
    {
        "key": "verwaltung",
        "anchor": "verwaltung",
        "title": "Verwaltung & Einsatzvorbereitung",
        "status": "verfuegbar",
        "icon": "clipboard-list",
        "image": "/seite/bild/Objektverwaltung.png",
        "tagline": "Objekte, Kontakte, Übungen, Fahrzeuge und Infrastruktur – gut vorbereitet in den nächsten Einsatz.",
        "intro": "Gute Vorbereitung spart im Einsatz wertvolle Zeit. Einsatzcockpit bündelt die "
        "Daten, Unterlagen und Abläufe des Feuerwehralltags, damit sie im richtigen "
        "Moment verlässlich zur Verfügung stehen.",
        "wiki": "Anwender-Objekte",
        "groups": [
            {
                "title": "Objekte & Kontakte",
                "wiki": "Anwender-Objekte",
                "bullets": [
                    {
                        "text": "Objekte mit Einsatzunterlagen, BMA, Gefahren und Ansprechpartnern",
                        "wiki": "Anwender-Objektpflege",
                    },
                    "Versionierte Dokumente mit Volltextsuche (OCR)",
                    "Externe Objektpflege durch Betreiber – mit Freigabe durch die Feuerwehr",
                    {
                        "text": "Zentrale Kontakte mit Import, Dublettenprüfung und Verknüpfungen",
                        "wiki": "Anwender-Kontaktverwaltung",
                    },
                ],
            },
            {
                "title": "Übungen & Mannschaft",
                "wiki": "Anwender-Probenplan",
                "bullets": [
                    "Probenplan mit Jahresübersicht, Checklisten und Teilnehmern",
                    {"text": "Übungseinsätze und Nachbereitung", "wiki": "Anwender-Uebungsmodus"},
                    {
                        "text": "Planspiele: Übungseinsätze und Unwetter-GSL automatisch erzeugen, "
                                "mit Regie, Folgemeldungen und Auswertung – ohne echte Alarmierung",
                        "wiki": "Anwender-Planspiele",
                        "tag": "Beta",
                    },
                    {
                        "text": "Mannschaftsregister mit Qualifikationen und Ablaufdaten",
                        "wiki": "Anwender-Mannschaftsregister",
                    },
                ],
            },
            {
                "title": "Fahrzeuge & Ausrüstung",
                "wiki": "Anwender-Fahrtenbuch",
                "bullets": [
                    "Digitales Fahrtenbuch mit Auswertungen und Maschinisten-Matrix",
                    {"text": "Geräteverleih mit Stücklisten und Barcode-Scan", "wiki": "Anwender-Geraeteverleih"},
                    {"text": "Drohnendokumentation nach RL-UAS", "wiki": "Anwender-Drohne-UAS"},
                ],
            },
            {
                "title": "Infrastruktur & Informationen",
                "wiki": "Anwender-Strassensperren",
                "bullets": [
                    "Straßensperren mit Karte und Nachbarfreigaben",
                    {"text": "Alarm- und Einsatzpläne, Maßnahmen- und Katastrophenpläne "
                             "mit Freigabe, Karte, Suche und PDF",
                     "wiki": "Anwender-Katastrophenplaene"},
                    {"text": "Anfahrtsrouting mit Umfahrung", "tag": "optional", "wiki": "Anwender-Strassensperren"},
                    {"text": "Wetter, Unwetterwarnungen und Radar", "wiki": "Anwender-Wetter"},
                    {"text": "Lokale Wetterstation", "tag": "optional", "wiki": "Administration-Wetterstation"},
                    {"text": "Gefahrgut-Suche und Rettungsdatenblätter", "wiki": "Anwender-Nachschlagewerke"},
                ],
            },
        ],
    },
    {
        "key": "schnittstellen",
        "anchor": "schnittstellen",
        "title": "Schnittstellen & Automatisierung",
        "status": "verfuegbar",
        "icon": "server",
        "image": "/seite/bild/Gateway.png",
        "tagline": "Einsatzcockpit fügt sich in die vorhandene Infrastruktur ein – von der Leitstelle bis zum Drucker.",
        "intro": "Einsatzcockpit verbindet sich mit den Wegen, die in Ihrer Organisation schon "
        "funktionieren. Alarmdaten, Benachrichtigungen und externe Systeme werden dort "
        "eingebunden, wo sie die Arbeit im Alltag und im Einsatz erleichtern.",
        "wiki": "Home#kernfunktionen",
        "bullets": [
            {"text": "Anbindung an DIBOS und LIS sowie REST-API", "wiki": "Administration-DIBOS-Elvis-Anbindung"},
            {"text": "Alarmierung per SMS, E-Mail, Teams und Push", "wiki": "Administration-SMS-Einsatzinfo"},
            {
                "text": "Print & Alarm Gateway mit automatischen Druckregeln",
                "tag": "optional",
                "wiki": "Administration-Print-Alarm-Gateway",
            },
            {"text": "Android-App und PWA – auch offline nutzbar", "wiki": "Anwender-Native-Android-App"},
            {"text": "MCP-Schnittstelle für KI-Assistenten", "tag": "Beta", "wiki": "Anwender-MCP-Assistent"},
            {
                "text": "KI-Assistent für Lagebilder, Vorschläge und Berichtsentwürfe",
                "tag": "optional",
                "wiki": "Administration-Einstellungen",
            },
            {
                "text": "Mehrere Organisationen, Rollen und Berechtigungen, Single Sign-On",
                "wiki": "Administration-Benutzer-und-Rollen",
            },
        ],
    },
]

# "Und vieles mehr" – Chip-Grid auf /funktionen. ``tag`` = kleiner Mono-Zusatz.
MORE_CHIPS: list[dict[str, str]] = [
    {"label": "Mail-Eingang / Posteingang", "tag": ""},
    {"label": "Bild-Annotation & Skizzen", "tag": ""},
    {"label": "Statistik-Dashboard", "tag": ""},
    {"label": "WordPress-Berichte", "tag": "optional"},
    {"label": "Mailing-Kampagnen", "tag": ""},
    {"label": "Org-Datensicherung", "tag": ""},
    {"label": "In-App-Update", "tag": ""},
    {"label": "Bürger-Meldungsportal", "tag": "GSL"},
]

# Roadmap – gruppiert nach Status. Reihenfolge der Gruppen = Anzeige.
# "verfuegbar" wird aus FEATURES abgeleitet (siehe available_roadmap_items()).
ROADMAP: list[dict] = [
    {
        "status": "in_arbeit",
        "items": [
            {
                "title": "Katastrophenpläne – Pilot",
                "text": "Hochwasser-Maßnahmenplan als erster Pilot für Freigabe, Karte, Quellen und Offline-PDF.",
            },
            {
                "title": "GSL-Einheitenansicht – Praxiserprobung",
                "text": "Tests auf Fahrzeug-Tablets und Smartphones: Kamera, Upload im Funkloch, "
                        "mehrere Geräte pro Fahrzeug. Rückmeldungen aus Übungen fließen direkt ein.",
            },
            {
                "title": "Mail-Eingang – Erprobung",
                "text": "Einsatz-Mails über Resend, IMAP oder Microsoft 365 landen als Meldung im "
                        "Einsatz; der Betrieb mit echten Postfächern wird erprobt.",
            },
            {
                "title": "Planspiele – Erprobung",
                "text": "Übungseinsätze und Unwetter-Großschadenslagen mit Szenario-Katalog, "
                        "Folgemeldungen, simulierten Einheiten und Auswertung – im ersten Praxistest.",
            },
            {
                "title": "KI-Assistenten über MCP – Erprobung",
                "text": "Objekte, Kontakte, Straßensperren, Einsätze und die GSL-Lage per "
                        "KI-Assistent abfragen und pflegen – im Praxistest mit Claude und ChatGPT.",
            },
        ],
    },
    {
        "status": "geplant",
        "items": [
            {
                "title": "Einheitenansicht: Navigation mit Straßensperren",
                "text": "Route zur Einsatzstelle direkt im Einheitenmodus, unter Berücksichtigung "
                        "aktiver Sperren.",
            },
            {
                "title": "Einheitenansicht: Hintergrund-Synchronisation",
                "text": "Offline erfasste Meldungen und Fotos überträgt die Android-App auch dann, "
                        "wenn sie gerade nicht geöffnet ist.",
            },
            {
                "title": "Auswertung von Großschadenslagen",
                "text": "Zeiten, Einheitenstatus und Abläufe einer GSL für die Nachbereitung auswerten.",
            },
            {"title": "Erweiterte Berichte & Statistik", "text": "Mehr Auswertungen für Nachbereitung und Verwaltung."},
            {
                "title": "Weitere Leitstellen-Schnittstellen",
                "text": "Anbindung zusätzlicher Leitstellen- und Fremdsysteme.",
            },
        ],
    },
]

# Prinzipien-Grid auf /ueber-das-projekt.
PRINCIPLES: list[dict[str, str]] = [
    {"icon": "heart", "title": "Nicht-kommerziell", "text": "Kein Verkauf, keine Lizenzkosten – aus Überzeugung."},
    {"icon": "code", "title": "Open Source", "text": "Der Quellcode ist offen einsehbar und auditierbar."},
    {
        "icon": "shield",
        "title": "Aus der Praxis",
        "text": "Entstanden im aktiven Feuerwehrdienst, gewachsen aus echten Einsätzen.",
    },
    {
        "icon": "server",
        "title": "Datensouveränität",
        "text": "Betrieb auf eigener Infrastruktur möglich – keine Cloud-Pflicht.",
    },
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
