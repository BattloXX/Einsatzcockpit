# Lagekarte der Großschadenslage

← [Zurück zur Großschadenslage](Anwender-Grosschadenslage)

Die Lagekarte (`/lage/{id}/karte`) zeigt alle Einsatzstellen und Abschnitt-Polygone einer Großschadenslage auf einer interaktiven OpenStreetMap-Karte.

---

## Übersicht der Bedienoberfläche

```
┌─────────────────────────────────────────────────────────────┐
│  ← Board-Name          [aktiv]    Subnav: Board Dashboard … │  ← Topbar (fest)
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Priorität] [Abschnitt]  | Filter-Checkboxen | 📍 Pin     │  ← Filter-Leiste
│                                                             │
│                        Karte                                │
│   🔲 Geoman-Toolbar                                         │
│                                                             │
│                    ●  ●  ●  ●  (Einsatzstellen)             │
│            ┌──── Abschnitt-Polygon ────┐                    │
│            │                           │                    │
│            └───────────────────────────┘                    │
│                                                             │
│  [Abschnitt-Filter-Chips]           [FAHRZEUGE-Legende]     │
│  [Einsatzstellen ohne Koordinaten]  [xx / yy Stellen]       │
└─────────────────────────────────────────────────────────────┘
```

---

## Einsatzstellen-Marker

Jede Einsatzstelle mit bekannten Koordinaten erscheint als farbiger Kreis. Die Farbe richtet sich nach dem gewählten Anzeigemodus:

| Modus | Beschreibung |
|-------|-------------|
| **Priorität** | Rot = Sofort · Orange = Dringend · Gelb = Normal · Grau = keine Prio / Erledigt |
| **Abschnitt** | Farbe des zugeordneten Abschnitts; Grau = kein Abschnitt |

Umschalten über die Buttons **Priorität** / **Abschnitt** in der Filter-Leiste.

### Klick auf Einsatzstelle

Ein Klick öffnet das **Einsatz-Details-Panel** (rechts oder von unten auf Mobilgeräten) mit vollständigen Informationen und Aktionsbuttons — ohne die Karte zu verlassen.

Das Panel-Popup zeigt außerdem:
- **→ Board** — öffnet das Großschadenslage-Board in einem neuen Tab
- **→ Einsatzkarte** — öffnet die Leaflet-Karte des verknüpften regulären Einsatzes (erscheint nur, wenn der Einsatz ein zugehöriges Einsatz-Objekt hat)

---

## Filter-Leiste

Die Filter-Leiste befindet sich direkt unter der Topbar und ist immer sichtbar.

| Element | Funktion |
|---------|---------|
| **Priorität / Abschnitt** | Wechselt den Einfärbe-Modus der Marker |
| **Phase-Checkboxen** | Ein-/Ausblenden nach Phase (Eingegangen, Erkundung, …) |
| **📥 Meldungen** | Bürger-Meldungs-Marker ein-/ausblenden (erscheint nur wenn Meldungen vorhanden) |
| **📍 Pin-Modus** | Neue Einsatzstelle per Kartenklick anlegen (nur für Berechtigte) |

### Abschnitt-Filter-Chips

Rechts oben erscheinen Chips für alle angelegten Abschnitte:
- **Alle** — zeigt alle Stellen
- Chip je Abschnitt — zeigt nur Stellen dieses Abschnitts
- **Ohne** — zeigt nur Stellen ohne Abschnitt

---

## Abschnitt-Polygone zeichnen

> Voraussetzung: Rolle `recorder`, `incident_leader`, `admin` oder `org_admin`

Abschnitt-Polygone werden direkt auf der Karte gezeichnet und **ohne Seiten-Neuladen** sofort dargestellt.

### Neues Polygon zeichnen

1. In der Geoman-Toolbar (links auf der Karte) das **Polygon-Werkzeug** aktivieren
2. Jeden Eckpunkt durch Klick auf die Karte setzen
3. Letzten Punkt erneut klicken (oder den ersten Punkt) zum Schließen
4. Dialog **„Abschnitt benennen"** öffnet sich:
   - Bezeichnung eingeben (z. B. „Abschnitt Nord")
   - Farbe aus der Palette wählen
   - **Speichern** klicken
5. Der Abschnitt erscheint sofort als farbig ausgefülltes Polygon auf der Karte — kein F5 nötig

### Bestehendes Polygon bearbeiten

1. In der Geoman-Toolbar den **Bearbeitungsmodus** aktivieren
2. Eckpunkte des Polygons verschieben
3. Bearbeitungsmodus beenden → Geometrie wird automatisch gespeichert

### Geoman-Toolbar (Deutsch)

Die Zeichenwerkzeuge sind vollständig auf Deutsch beschriftet:

| Symbol | Funktion |
|--------|---------|
| Polygon-Icon | Polygon zeichnen |
| Bearbeitungs-Icon | Formen bearbeiten |

---

## 📍 Pin-Modus: Einsatzstelle per Kartenklick anlegen

Der Pin-Modus ermöglicht das schnelle Anlegen neuer Einsatzstellen direkt auf der Karte — ideal wenn Meldungen mit bekanntem Ort eintreffen.

### Ablauf

1. **📍 Pin-Modus**-Button in der Filter-Leiste aktivieren (wird blau hervorgehoben)
2. Cursor wechselt auf Fadenkreuz
3. Auf die gewünschte Position auf der Karte klicken
4. Dialog öffnet sich:
   - **Koordinaten** werden automatisch angezeigt
   - **Adresse** wird automatisch per Reverse Geocoding (Nominatim/OpenStreetMap) ermittelt und eingeblendet
   - **Einsatzgrund** eingeben (optional)
5. **Anlegen** klicken
6. Die neue Einsatzstelle erscheint sofort als Marker auf der Karte; Pin-Modus wird automatisch beendet

> **Hinweis:** Die automatische Adressermittlung benötigt eine Internetverbindung zum OpenStreetMap-Dienst Nominatim. Ohne Verbindung können Koordinaten und ein Einsatzgrund eingetragen werden.

---

## Fahrzeug-Positionen

GPS-Positionen und manuell gesetzte Fahrzeug-Pins werden als separate Marker auf der Karte angezeigt:

| Farbe | Bedeutung |
|-------|-----------|
| 🟢 Grün (leuchtend) | GPS live (Position ≤ 5 Minuten alt) |
| ⚫ Grau | GPS veraltet (Position > 5 Minuten alt) |
| 🟠 Orange (gestrichelt) | Manuell gesetzt |

Fahrzeug-Marker zeigen beim Klick Fahrzeugname, Typ, GPS-Quelle und Zeitpunkt der letzten Positionsmeldung.

---

## Live-Updates via WebSocket

Die Karte empfängt Echtzeit-Ereignisse vom Server:

| Ereignis | Reaktion |
|----------|---------|
| `site_updated` | Seite neu laden (Einsatzstellen-Daten aktualisieren) |
| `section:changed` | Abschnitt-Polygone **ohne Reload** neu laden und rendern |
| `vehicle:position` | Fahrzeug-Marker wird live bewegt |

---

## Einsatzstellen ohne Koordinaten

Einsatzstellen ohne bekannte Koordinaten erscheinen nicht auf der Karte, werden aber in einer Liste **links unten** aufgeführt. Ein Klick auf den Namen öffnet das Board.

---

## Taktischer Modus (ÖBFV E-27)

Die Lagekarte kann in einen **taktischen Anzeigemodus** nach ÖBFV-Richtlinie E-27 umgeschaltet werden:

- Einsatzstellen erhalten **normierte taktische Symbole** (Quadrat, Dreieck, Kreis je Einsatzart)
- Einheiten werden mit **Magnetfarben** dargestellt (Rot = Feuerwehr, Blau/Weiß = Sanitäts, ...)
- Eine **taktische Legende** kann ein-/ausgeblendet werden
- Der Modus ist für Druckausdrucke geeignet (Legende erscheint automatisch)

**Umschalten:** Karten-Steuerung oben rechts → **„Taktisch"**-Schalter.

→ Vollständige Dokumentation: [Taktische Lagekarte (ÖBFV E-27)](Anwender-Taktische-Lagekarte)

---

## Radar-Overlay (Niederschlag)

Das **Niederschlagsradar-Overlay** (via RainViewer) kann über die Karten-Steuerung ein-/ausgeblendet werden:

- Letzte 2 Stunden (animiert rückwärts)
- Aktueller Nowcast
- Farbskala: blau (leicht) → rot (stark)

Das Overlay benötigt Internetverbindung und ist nicht für Offline-Betrieb verfügbar.

→ Mehr zur Wetter-Integration: [Wetter](Anwender-Wetter)

---

## Druck & Print-Center {#druck--print-center}

Die Lagekarte kann als **PDF / Druckvorschau** ausgegeben werden.

### Einfacher Druck

Über **Drucken** (oben rechts in der Karten-Toolbar):
- Druckformat: **A4** oder **A3**
- Kartenausschnitt: aktueller Zoom/Pan-Stand
- Inhalt: **Lagekarte** (nur Karte) oder **Lagebericht** (Karte + Einsatzstellen-Tabelle)

### Print-Center (Sammeldruck)

Das Print-Center ermöglicht das **Mehrfach-Drucken** mehrerer Konfigurationen in einem Schritt:

1. **Print-Center** öffnen (Button in der Toolbar)
2. Gewünschte Kombinationen wählen (Karte A4, Lagebericht A3, Taktisch A4, ...)
3. **Sammeldruck** → alle gewählten Konfigurationen als separate PDFs oder in einem Multi-Page-PDF

### Druckfußzeile

Jeder Ausdruck enthält automatisch eine Fußzeile mit:
- Org-Logo
- Name der Großschadenslage
- Zeitstempel des Drucks
- Einsatzstellen-Statistik (Anzahl nach Phase/Priorität)
- Ressourcen-Übersicht (aktive Einheiten)

---

## Berechtigungen

| Funktion | Rolle |
|----------|-------|
| Karte ansehen | `readonly` und höher |
| Abschnitt-Polygon zeichnen / bearbeiten | `recorder`, `incident_leader`, `admin`, `org_admin` |
| Einsatzstelle per Pin-Modus anlegen | `recorder`, `incident_leader`, `admin`, `org_admin` |
| Taktischer Modus anzeigen | alle |
| Radar-Overlay anzeigen | alle |
| Drucken / Print-Center | `recorder`, `incident_leader`, `admin`, `org_admin` |
