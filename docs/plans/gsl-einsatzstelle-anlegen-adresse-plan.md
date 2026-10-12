# Plan: GSL „Einsatzstelle anlegen“ wie „Neuer Einsatz“ (Karte, Picker, Adressprüfung)

Stand: 2026-10-12 · Umsetzung: Codex · Prüfung/Merge: Claude

## Ausgangslage

| | Neuer Einsatz (`index.html`, `#newIncidentModal`) | GSL Einsatzstelle (`incident_major/_neue_stelle_dialog.html`) |
|---|---|---|
| Reihenfolge | Ort → Straße/Nr. | Straße/Nr. → Ort |
| Ort vorbelegt | `default_city` (Org-Stadt, Fallback `settings.DEFAULT_INCIDENT_CITY`) | leer |
| Autocomplete | `address-autocomplete.js` für Ort, Straße und Nr. (`/adresse/vorschlaege`) | keine; Chrome blendet stattdessen sein eigenes Autofill ein (Screenshot des Users) |
| Adressprüfung | Button „📍 Koordinaten suchen“ + Auto-Geocode bei `blur` → `/adresse/geocode`, Status „✓ …“ / „✗ Nicht gefunden“ | keine; Geocoding erst serverseitig nach dem Absenden (`_geocode_site`), Fehler bleiben still |
| Karte | Leaflet-Picker (`#newIncMapContainer`), Marker verschiebbar, Klick setzt Pin, versteckte `lat`/`lng` | keine |
| Einsatzgrund | Feld vorhanden | Route akzeptiert `einsatzgrund`, im Dialog fehlt das Feld |

Ziel: Der GSL-Dialog verhält sich wie „Neuer Einsatz“. Damit beide nicht wieder auseinanderlaufen, wird **ein gemeinsamer Baustein** verwendet. Kein zweites Copy-and-paste.

## Umsetzung

### 1. Gemeinsamer Adress-Picker (neu)

- `app/static/js/address-picker.js`: Logik aus dem Script-Block von `index.html` herauslösen.
  - Aufruf: `initAddressPicker({ prefix, mapId, latId, lngId, cityId, streetId, noId, statusId, geocodeBtnId, defaultLat, defaultLng })`
  - Leaflet und `map-picker.js` bei Bedarf nachladen (`ensureLeaflet` aus `index.html` übernehmen, inklusive Callback-Warteschlange)
  - Karte beim Öffnen des Dialogs initialisieren: MutationObserver auf das `open`-Attribut des `<dialog>`, `invalidateSize` bei 200/600 ms
  - Marker verschiebbar, Klick auf die Karte setzt den Pin; beides schreibt `lat`/`lng`
  - Autocomplete für Ort/Straße/Nr. über `initAddressAutocomplete`; bei Auswahl Marker setzen und Status „✓ label“
  - Auto-Geocode bei `blur` (Debounce 400 ms) und Button → `POST /adresse/geocode`, Status ✓/✗
  - **Manuell gesetzter Pin hat Vorrang:** Hat der User den Marker gezogen oder geklickt, überschreibt ein späteres Auto-Geocode bei `blur` ihn nicht mehr. Der Button „Koordinaten suchen“ überschreibt ihn bewusst.
  - Reset-Funktion (`picker.reset()`): Felder, Status und Marker auf den Ausgangswert zurücksetzen. Wird nach erfolgreichem Anlegen aufgerufen.
  - Mehrere Instanzen auf einer Seite möglich, keine globalen `window.newInc*`-Funktionen mehr. Die bestehenden globalen Namen in `index.html` bleiben nur, wenn sie anderswo verwendet werden (vorher `rg newIncSetMarker|newIncidentGeocode|newIncAutoGeocode app/`).
- Jinja-Makro `app/templates/_macros/address_picker.html` (oder vorhandenes Makro-Verzeichnis nutzen):
  - `address_picker(prefix, default_city, ort_name="address_city", strasse_name="address_street", nr_name="address_no")`
  - rendert Ort → Straße/Nr. → Button + Status → Kartencontainer → versteckte `lat`/`lng`
  - Feldnamen sind parametrierbar, weil GSL `ort`/`strasse`/`hausnr` verwendet
  - Karte 200 px hoch, mobil (≤760 px) 180 px
- `index.html` auf Makro und JS umstellen. Verhalten muss identisch bleiben (Übungs-/Echt-Bestätigung bleibt unverändert im Template).

### 2. GSL-Dialog `_neue_stelle_dialog.html`

- Felder: Bezeichnung* → Einsatzgrund (optional, neu) → Adress-Picker (Ort vorbelegt mit Org-Stadt)
- Feldnamen bleiben `ort`, `strasse`, `hausnr`; neu sind `lat`, `lng`
- Chrome-Autofill: Das eigene Dropdown von `initAddressAutocomplete` soll Chromes Adressvorschläge ersetzen. `autocomplete="off"` an `<form>` und an allen drei Feldern setzen. Die Feldnamen `ort`/`strasse`/`hausnr` bleiben, weil die Route sie erwartet. Im Browser prüfen, ob Chrome trotzdem Vorschläge zeigt. Falls ja, im Bericht melden und keinen Workaround erfinden.
- Nach Erfolg: Dialog schließen, `form.reset()` **und** `picker.reset()`
- Fehleranzeige: Das bestehende `alert` bleibt.
- Dialog-Breite wie Standard: `max-width:560px; width:95vw`
- `default_city` und Fallback-Koordinaten an den Kontext von `_lage_layout.html` übergeben. Die Route `ui_major_incident` hat bereits `org_lat`/`org_lng` (Z. ~408), `default_city` kommt analog zu `ui_incident.py:398` dazu. Alle Seiten prüfen, die `_lage_layout.html` rendern (Board, Stab, Sektoren …), damit die Variablen überall gesetzt sind; sonst im Template `default(...)`.

### 3. Backend

- `ui_major_incident.site_create` (`POST /lage/{lage_id}/stellen/neu`): optionale Formularfelder `lat: str = Form("")`, `lng: str = Form("")`
  - parsen und auf gültigen Bereich prüfen (−90..90 / −180..180), sonst ignorieren
  - an `create_site(..., lat=, lng=)` übergeben
  - `_geocode_site` überspringt bereits, wenn `lat`/`lng` gesetzt sind; `auto_assign_section` läuft danach unverändert
  - ohne Koordinaten ist das Verhalten wie bisher (serverseitiges Geocoding)
- Rollen angleichen, damit GSL-Nutzer den Picker verwenden dürfen. GSL-Anlegen erlaubt `incident_leader, admin, org_admin, recorder`:
  - `POST /adresse/geocode` (`ui_incident.py:3658`) erlaubt heute nur `incident_leader, admin` → um `org_admin, recorder` erweitern
  - `GET /adresse/vorschlaege` (`ui_incident.py:3621`) erlaubt `incident_leader, admin, recorder` → `org_admin` ergänzen
  - `gsl_nur_lesen`-Nutzer sehen den Dialog ohnehin nicht. Keine weitere Änderung.
  - Achtung: `/adresse/vorschlaege` ist auch in `ui_road_closure.py:575` registriert. Feststellen, welche Route greift (Router-Reihenfolge in `app/main.py`), und im Bericht nennen. Nicht im Rahmen dieses Plans umbauen, außer der GSL-Picker bekommt dadurch keine Vorschläge.
- Keine Migration nötig (`IncidentSite.lat/lng` existieren).

### 4. Nicht im Umfang

- „Als Einsatzstelle anlegen“ aus der Meldungskarte und `stellen/via-karte`: unverändert
- Bearbeiten der Adresse einer bestehenden Stelle: Der „📍 Koordinaten per Pin setzen“-Flow bleibt. Möglicher Folgeschritt, den Picker dort ebenfalls einzusetzen.

## Tests

- `tests/test_gsl_einsatzstelle_anlegen_picker.py` (neu):
  - POST mit `lat`/`lng` → Stelle hat genau diese Koordinaten, Geocoder wird **nicht** aufgerufen (monkeypatch `geocode_address`)
  - POST ohne Koordinaten → Geocoder wird aufgerufen (bisheriges Verhalten)
  - ungültige Koordinaten (`lat=200`, `lng=abc`) → werden ignoriert, Fallback Geocoding, kein 500
  - `einsatzgrund` wird gespeichert
  - Abschnitts-Autozuweisung greift mit den übergebenen Koordinaten (vorhandenes Sektor-Fixture aus `test_gsl_sektoren.py` nutzen)
  - Board-HTML enthält Kartencontainer, `name="lat"`, Ort vorbelegt mit Org-Stadt, `address-picker.js`
  - Rollen: `recorder` und `org_admin` bekommen von `/adresse/geocode` kein 403 (Geocoder gemockt); `readonly` bekommt 403
- `index.html` enthält weiterhin den Picker (Regression für „Neuer Einsatz“); vorhandene Tests zu `/einsatz/neu` bleiben grün
- Vorhandene Tests `test_gsl_sektoren.py`, `test_gsl_reload_fixes.py`, `test_gsl_zielbild.py` und `test_einheit_gesamtansicht.py` bleiben grün
- `test_template_jinja_kommentare.py` (kein `{#` in CSS), `rg '[“”„‘’]' app/templates` leer

## Abnahme im Browser (Claude, lokaler E2E-Stack Port 8094)

- Desktop 1280 px und mobil 390 px, Board einer aktiven Lage → „Einsatzstelle anlegen“:
  - Karte sichtbar, nicht grau (invalidateSize), Ort vorbelegt
  - Straße tippen → Vorschläge des Einsatzcockpits (nicht Chrome-Autofill), Auswahl setzt Marker und „✓“
  - Unbekannte Adresse → „✗ Nicht gefunden“
  - Pin verschieben, danach Feld verlassen → Pin bleibt
  - Anlegen → Karte erscheint ohne Reload auf dem Board, Detail zeigt die gewählten Koordinaten
  - Dialog erneut öffnen → leer, Ort wieder vorbelegt, Marker auf Ausgangsposition
- „Neuer Einsatz“ auf der Startseite: unverändert funktionsfähig (Autocomplete, Geocode, Pin, Übungs-Bestätigung)
- keine `pageerror`

## Checks vor PR

```
.venv/bin/python -m ruff check app/
.venv/bin/python -m mypy app/ --ignore-missing-imports
.venv/bin/python -m pytest -q
```

Wiki: `Anwender-GSL-…`-Artikel zum Anlegen einer Einsatzstelle um Karte/Adressprüfung ergänzen (nur gezielt, nichts streichen).
