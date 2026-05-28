# Lagekarte.info Integration

← [Zurück zur Startseite](Home)

Die Lagekarte.info-Integration ermöglicht es, einen laufenden Einsatz mit korrekten Koordinaten auf [lagekarte.info](https://www.lagekarte.info) zu öffnen und die Fahrzeuge des Einsatzes als Live-Pins auf der Karte anzuzeigen.

---

## Adresse & Koordinaten pflegen

Koordinaten werden beim Einsatz-Board unter der Alarmstichworter-Anzeige gespeichert.

**Koordinaten setzen:**

1. Im Einsatz-Board auf die **Adresse** klicken (erscheint mit ✏ für berechtigte Nutzer).
2. Das Bearbeitungs-Modal öffnet sich mit den aktuellen Adressfeldern.
3. **Automatisch suchen**: Auf **📍 Koordinaten automatisch suchen** klicken. Die App sendet die Adresse an den kostenlosen Geocoding-Dienst Nominatim (OpenStreetMap) und trägt die gefundenen Koordinaten ein.
4. **Manuell per Karte**: Den Marker auf der Karte an die gewünschte Position ziehen — oder direkt auf die Karte klicken. Die Koordinatenfelder werden automatisch aktualisiert.
5. **Manuell per Eingabe**: Lat/Lng direkt in die Textfelder eintippen (Dezimalgrad, z.B. `47.488847` / `9.741011`).
6. **Speichern** klicken.

> **Hinweis**: Wenn Geocoding keinen Treffer liefert, startet die Karte am konfigurierten Org-Fallback-Standort (→ [Org-Einstellungen](#fallback-standort)).

---

## Lagekarte.info öffnen

Sobald Koordinaten gespeichert sind, erscheint im Einsatz-Header (neben *AS-Überwachung*) der Button **🗺️ Lagekarte**.

- Ein Klick öffnet lagekarte.info in einem **neuen Fenster**.
- **Wenn ein SHASH-Link gespeichert ist**: wird dieser verwendet (→ öffnet das gespeicherte lagekarte.info-Projekt).
- **Ohne SHASH-Link**: wird automatisch ein Einsatz-Link der Form `?einsatz=lat,lng` generiert, der lagekarte.info auf die Einsatzkoordinaten zentriert.

---

## Eigenen Projekt-Link verwenden (SHASH)

lagekarte.info erlaubt es, die aktuelle Kartenansicht zu speichern und per Link zu teilen. Dieser Link kann im Einsatz hinterlegt werden, damit der **🗺️ Lagekarte**-Button immer die vollständige Projektansicht öffnet.

**So wird der SHASH-Link gespeichert:**

1. lagekarte.info öffnen und die gewünschte Karte einrichten (Layer, Zoom, Bereich etc.).
2. In lagekarte.info auf **Speichern & Teilen** klicken — den angezeigten Link kopieren (enthält `?shash=...`).
3. Im Einsatz-Board auf die Adresse klicken → Bearbeitungs-Modal öffnen.
4. Den kopierten Link im Feld **Lagekarte.info-Projekt-Link** einfügen.
5. **Speichern**.

**Unterstützte Link-Formen** (alle werden unverändert gespeichert und verwendet):

| Parameter | Bedeutung | Beispiel |
|-----------|-----------|---------|
| `?shash=…` | Gespeichertes Projekt (Speichern & Teilen) | `?shash=AbCdEf` |
| `?einsatz=lat,lng` | Einsatz-Marker setzen | `?einsatz=47.488847,9.741011` |
| `?center=lat,lng,zoom` | Karte auf Position zentrieren | `?center=47.488847,9.741011,16` |
| `?zoom=N` | Zoom-Level setzen | `?zoom=18` |
| `?s=Stadt` | Stadtname suchen | `?s=Wolfurt` |
| `?map=Name` | Kartentyp setzen | `?map=topo` |

> Der Button erscheint nur, wenn entweder ein SHASH-Link **oder** Koordinaten gespeichert sind.

---

## Live-Fahrzeuge in lagekarte.info (GeoJSON-Feed)

Der **GeoJSON-Feed** liefert alle aktiven Fahrzeuge eines Einsatzes als Punkte auf der Lagekarte — mit Fahrzeugname, Typ und aktuellem Status. lagekarte.info kann diesen Feed automatisch in einem konfigurierbaren Intervall abrufen.

### Voraussetzungen

- Einsatz muss Koordinaten gesetzt haben (sonst liefert der Feed eine leere Liste).
- Ein **Lagekarte-Token** muss erstellt worden sein (→ [Admin → Lagekarte-Tokens](Administration-Lagekarte-Tokens)).

### Einrichten in lagekarte.info

1. **Lagekarte-Token erstellen** (einmalig, pro Einsatz oder für alle Einsätze der Org):
   - `Admin → Lagekarte-Tokens → + Token erstellen`
   - Label eingeben, optional auf einen Einsatz beschränken, Speichern.
   - Den angezeigten Token-String kopieren (wird nur **einmal** angezeigt!).

2. **URL zusammenbauen:**
   ```
   https://<server>/api/lagekarte/einsatz/<EINSATZ_ID>/fahrzeuge.geojson?token=<TOKEN>
   ```

3. **In lagekarte.info eintragen:**
   - In lagekarte.info: *Daten importieren → URL* anklicken.
   - Die zusammengebaute URL eintragen.
   - **Auto-Reload** aktivieren (empfohlenes Intervall: 30–60 Sekunden).
   - Fahrzeuge erscheinen als Punkte auf der Karte mit Name, Typ und Status.

4. Optional: **KML-Export** unter `…/fahrzeuge.kml?token=<TOKEN>` (für Programme, die KML bevorzugen).

### Eigenschaften je Fahrzeug-Feature

| Feld | Inhalt | Beispiel |
|------|--------|---------|
| `name` | Funkrufname / Kürzel | `RLF` |
| `typ` | Fahrzeugtyp | `Rüstlöschfahrzeug` |
| `status` | Aktueller Einsatz-Status | `Am Einsatzort` |
| `info` | Offene Aufgaben (wenn vorhanden) | `2 offene Aufgaben` |
| `einsatz_id` | Einsatz-ID | `42` |
| `fahrzeug_id` | Fahrzeug-Zuordnungs-ID | `1337` |

> **Koordinaten**: Da Fahrzeuge im System keine eigene GPS-Position haben, werden alle Fahrzeuge in einem kleinen Kreis (~15 m) um die Einsatz-Koordinaten herum angezeigt. Die Position bleibt zwischen Abrufen stabil (kein zufälliges Springen).

---

## Fallback-Standort {#fallback-standort}

Für Organisationen, die nicht in Wolfurt liegen, kann in den Org-Einstellungen ein Standard-Startpunkt für den Karten-Picker gesetzt werden.

`Admin → Organisation → Karte / Lagekarte.info → Fallback-Breitengrad / -Längengrad`

Dieser Wert wird als Startposition der Karte im Adress-Bearbeitungs-Dialog verwendet, wenn noch keine Koordinaten gespeichert sind und Geocoding keinen Treffer liefert.

Standard-Fallback (wenn nicht konfiguriert): **Wolfurt, Vorarlberg** (47.4664, 9.7416).
