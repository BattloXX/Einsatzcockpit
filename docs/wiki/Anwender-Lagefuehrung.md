# Lageführung (Einsatz-Lagekarte)

← [Zurück zur Startseite](Home)

Die Lageführung ist eine **einsatzbezogene, interaktive Lagekarte** (Leaflet/OSM): Fahrzeuge, Einsatzort und das verknüpfte Objekt erscheinen automatisch — ohne manuelles Nachzeichnen. Ergänzt wird ein reduziertes Set taktischer Zeichen (ÖNORM S 2308 / ÖBFV RL E-27) für Schadenlage, Abschnitte und Maßnahmen. Mehrere Nutzer können gleichzeitig zeichnen (Presence, Soft-Locks), die Karte kann als PDF-Lagebericht oder 1:1-Kartendruck ausgegeben werden.

Von lagekarte.info übernommen: gleiche Grundbedienung (Symbol antippen → auf Karte klicken → drehen/skalieren), aber automatische Datenanreicherung statt manuellem Eintragen.

---

## Modul aktivieren

Zweistufiger Feature-Flag wie bei der Objektverwaltung, beide auf derselben Seite `Admin → Einstellungen`:

1. **System-weit** (nur `system_admin` sichtbar): Abschnitt **🗺️ Lageführung (Lagekarte)** → *Systemweit aktivieren*.
2. **Je Organisation**: weiter unten auf derselben Seite den Org-Schalter **Lageführung aktivieren** setzen (bleibt deaktiviert/ausgegraut, solange der System-Schalter aus ist).

Erst wenn beide Schalter aktiv sind, erscheint der Button **🗺️ Lageführung** im Einsatz. Ausschalten versteckt nur die Ansicht — es werden keine Daten gelöscht.

---

## Lagekarte öffnen

- Über den Button **🗺️ Lageführung** im Einsatz-Board oder in der Einsatzinfo.
- URL: `/einsatz/{id}/lagefuehrung`

Im Banner oben wird angezeigt, wer aktuell die **Lageführung** hat. Jeder berechtigte Nutzer kann sie per Klick auf **Lageführung übernehmen** übernehmen (wird protokolliert).

---

## Rollen: Lageführer, Editor, Viewer

| Rolle | Rechte |
|-------|--------|
| **Lageführer** | Hat die Lageführung übernommen; kann Viewern Editor-Rechte geben/entziehen |
| **Editor** | `incident_leader`, `admin`, `org_admin`, `recorder` **oder** vom Lageführer explizit berechtigt; darf zeichnen |
| **Viewer** | Sieht die Karte live mit, Palette ausgeblendet |

Rechte vergeben: In der Präsenzliste neben einem Nutzer auf **Editor machen** / **entziehen** klicken (nur für den aktuellen Lageführer sichtbar).

---

## Auto-Layer (automatisch, ohne Zutun)

| Layer | Inhalt |
|-------|--------|
| **📍 Einsatzort** | Marker aus den Einsatz-Koordinaten |
| **🚒 Fahrzeuge** | Alle disponierten Fahrzeuge mit Live-GPS (sofern vorhanden), taktischem Zeichen (aus der Fahrzeugverwaltung gepflegt, sonst generisches Fahrzeug-Symbol) und Statusfarbring; Fahrzeuge ohne GPS lassen sich in der Fahrzeuge-Liste per **📍 Platzieren** manuell auf die Karte pinnen |
| **🏢 Objekt** | Das mit dem Einsatz verknüpfte Objekt: Klick öffnet ein Popup mit Gefahren (Piktogramm, UN-Nummer, Stoffname), Informationen, Anfahrtsweg und Kontakten (inkl. Telefonnummern); zusätzlich werden die in der [Objekt-Lagekarte](Anwender-Objekte) hinterlegten **Zufahrten und Sammelplätze** (und weitere Kartenobjekte wie Hydranten, Zugänge, FSD/BMZ-Standorte) mit demselben Kürzel-Symbol wie im Objektmodul eingeblendet — Linien (z. B. Zufahrten) gestrichelt, Punkte als kleines Symbol-Badge |
| **💧 Wasserstellen** | Hydranten/Löschwasserentnahmestellen aus den Wasserstellen-Stammdaten in der Nähe |
| **✏️ Zeichnungen** | Manuell gesetzte Elemente (Zeichenwerkzeuge + taktische Zeichen) |
| **🏷️ Beschriftungen** | Blendet alle Labels (Fahrzeugnamen, Symbol-Beschriftungen, Distanzangaben, Objekt-Kartenobjekte) ein/aus, ohne die Elemente selbst auszublenden |

Jeder Layer lässt sich über die Checkboxen im Tab **Layer** ein-/ausblenden. Basiskarte umschaltbar zwischen OSM Standard und Orthofoto (basemap.at).

---

## Taktische Zeichen setzen (Tab „Taktik", nur Editoren)

1. Tab **Taktik** öffnen, Symbol antippen (Suche/Kategorien wie bei lagekarte.info).
2. Auf die Karte klicken — das Symbol wird platziert.
3. Im Popup des platzierten Symbols: Rotation (±15°-Schritte) und Skalierung einstellen, Beschriftung eintragen.

Weitere Werkzeuge (nur Editoren):

- **📝 Text** — Freitext-Label auf der Karte
- **📢 Meldung** — Lagemeldung mit Zeit/Text (erscheint in der Chronologie)
- **📏 Distanzlinie** / **⭕ Distanzkreis** — Absperrbereiche, Reichweiten mit automatischer Meterangabe
- **🧭 Windrichtung** — Vorschlagswert aus den aktuellen Wetterdaten (GeoSphere/Kachelmann/Open-Meteo), manuell drehbar
- **📸 Momentaufnahme** — friert den aktuellen Kartenstand als Bild ein, an die Chronologie angehängt
- Bestehende Zeichenwerkzeuge (Linie, Polygon, Freihand, Marker)

Ein Fahrzeugsymbol lässt sich bei GPS-Ausfall manuell **festpinnen** (Position bleibt fix, kleines Pin-Icon).

---

## Chronologie & Replay

- **Chronologie** (Sidebar unten): jede Änderung (Zeichen gesetzt/verschoben/entfernt, Statuswechsel, Meldung, Momentaufnahme) mit Zeitstempel.
- **⏱ Replay**: spult den Kartenstand anhand der Chronologie vor/zurück (Slider + Abspielen).

---

## Mehrere Editoren gleichzeitig

- **Präsenzanzeige**: zeigt, wer gerade online ist.
- **Soft-Lock**: Fasst ein Editor ein Element an, wird es für alle anderen kurz als „wird bearbeitet von …" markiert (löst sich nach ca. 15 s Inaktivität automatisch).
- Konflikte (zwei Editoren ändern gleichzeitig dasselbe Element) werden über Versionsprüfung abgefangen — die zuletzt gespeicherte Änderung gewinnt, der vorherige Stand bleibt über die Chronologie nachvollziehbar.

---

## Drucken & PDF-Export

Zwei unabhängige Ausgabewege:

### 🖨 Drucken (WYSIWYG-Kartendruck)

Druckt **exakt den aktuellen, sichtbaren Kartenausschnitt** mit den gerade eingeschalteten Layern (inkl. Beschriftungen-Zustand), plus Legende der verwendeten Zeichen und Zeitstempel — kein Journal, kein Bericht, reine Karte.

1. Kartenausschnitt wie gewünscht zoomen/verschieben, Layer ein-/ausblenden.
2. Papierformat wählen (A4/A3, Hoch-/Querformat).
3. **🖨 Drucken** klicken — öffnet einen neuen Tab mit der Druckvorschau und startet automatisch den Browser-Druckdialog.

**QuickPrint:** Papierformat, Basiskarte und die zuletzt gedruckten Layer werden je Browser gemerkt (nicht serverseitig, sondern lokal im Browser) — beim nächsten Aufruf ist wieder alles wie beim letzten Mal vorbelegt.

### PDF-Lagebericht

Vollständiger Bericht mit Kopfdaten, Kartenausschnitt, Legende, Kräfteübersicht (Fahrzeuge/Status) und Chronologie/Zeitstrahl als WeasyPrint-PDF, unter `/einsatz/{id}/lagefuehrung/pdf` abrufbar.

> Aktuell ohne eigenen Button in der Lageführung-Oberfläche (dort steht nur der 🖨-Kartendruck oben) — direkter Aufruf der URL nötig.

---

## Mobile Nutzung

Auf Smartphones (≤ 760 px Breite) ist die Karte vollflächig sichtbar; Layer- und Taktik-Werkzeuge öffnen sich über den Button **🧰 Werkzeuge** unten als Bottom-Sheet (nach oben ausfahrendes Panel) und lassen sich per **✕ Schließen** wieder einklappen. Der primäre mobile Anwendungsfall ist Mitlesen (Viewer) und Meldung absetzen — intensives Zeichnen ist auf Tablet/Desktop komfortabler.

---

## Verwandte Seiten

- [Lagekarte.info](Anwender-Lagekarte) — externe Alternative/Ergänzung per GeoJSON-Feed
- [Objekte](Anwender-Objekte) — Pflege von Gefahren, Kontakten, Zufahrten und Sammelplätzen, die auf der Lageführungskarte erscheinen
- [Wetter-Integration](Anwender-Wetter) — Datenquelle für die Windrichtungs-Vorbelegung
