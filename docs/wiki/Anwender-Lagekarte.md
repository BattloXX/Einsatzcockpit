# Lagekarte.info Integration

← [Zurück zur Startseite](Home)

Die Lagekarte.info-Integration ermöglicht es, die Fahrzeuge eines laufenden Einsatzes per GeoJSON/KML-Feed in [lagekarte.info](https://www.lagekarte.info) einzubinden — für Organisationen, die weiterhin mit lagekarte.info arbeiten.

> **Hinweis:** Der direkte **🗺️ lagekarte.info**-Button im Einsatz-Board und in der Einsatzinfo wurde entfernt — die einsatzbezogene Lagekarte läuft jetzt über das eingebaute [Lageführung](Anwender-Lagefuehrung)-Modul (kein externer Dienst nötig, automatische Fahrzeug-/Objektdaten). Der GeoJSON/KML-Feed dieser Seite bleibt für Organisationen bestehen, die zusätzlich lagekarte.info nutzen möchten.

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
