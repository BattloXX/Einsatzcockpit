# Plan: Hydranten blau, Einsatztimer stoppen, Sperren mit mehreren Abschnitten

Stand 2026-11-06. Anlass: Einsatz 1417 (f26009808, T1 L41 Senderstraße) auf dem Infoscreen im
Gerätehaus. Umsetzung durch Codex in Paketen P1–P5, je Paket ein Commit.

Für **jeden** Codex-Auftrag gilt:

- Kein `ruff format`, kein `--fix`, nichts umformatieren, nur die genannten Stellen ändern.
- Nur gezielte Testdateien ausführen, **nie** die volle Suite.
- CSS nur in `app/static/css/tailwind.input.css`, danach `npm run build` (nie direkt in `app.css`).
- Nur gerade ASCII-Anführungszeichen in Code und Attributen (CLAUDE.md).
- Icons nur über das Icon-System, keine Emojis.

---

## Befund RLF-Störung (MCP, 2026-11-06)

- Laut Einsatz 1417 hat RLF-A **keinen** Störungsstatus: Er war eingesetzt, wurde um 07:55 Ortszeit per LIS
  entlassen und steht seitdem auf „Einsatzbereit“.
- **Serverseitiger Hänger 07:40:15–07:41:07 Ortszeit:** Alle 11 App-Geräte melden gleichzeitig
  `SocketTimeoutException` (Live-Poll, Gateway, Diagnosebericht). Im Worker-Log stehen im selben Fenster:
  - Loop-Lag-Watchdog (`app/services/loop_lag_watchdog.py:42`, `app/main.py:183/192`)
  - Slow-Query-Warnungen (`app/db.py:84 _sq_end`), unter anderem zu Gateway und Redis
  - `gateway_routing._poll` ERROR (`app/services/gateway_routing.py:323`)
  - zwei fehlgeschlagene POST-Schreibzugriffe (`write_failure_log`, 07:41:05 und 07:41:13)
  - `iteration_watch` und LIS `_load_tasks`-Fehler
- Direkt nach der Alarmierung (07:30) schlugen auf 7 Geräten Standortmeldungen fehl.
- Bis 08:04 meldeten drei Geräte (ID 5, 7 und 10) `gateway connection: SocketException`, ohne Verbindung zum Gateway.
- Das Alarm-SMS hat das Ergebnis „unklar“ (Tagebuch 07:30:59).
- Die Diagnose-Befunde zeigen nicht, welches Tablet im RLF verbaut ist.

**Schluss:** Wahrscheinlich hat sich der Ausfall um 07:40 auf dem RLF-Tablet als „Störung“ gezeigt. Er traf
aber alle Geräte gleichzeitig und hat seine Ursache am Server, nicht am Fahrzeug. Paket P5 behandelt das.

---

## P1 – Hydranten blau auf Infoscreen und Einsatz-Info

**Ziel:** Rote „H“-Marker überdecken den roten Einsatzort-Marker (siehe Foto). Auf dem Alarm-Infoscreen und auf
der Einsatz-Info-Seite (`incident/info.html`, inkl. Druck) werden darum alle Hydranten blau. Rot bleibt allein
dem Einsatzort.

- `app/static/css/tailwind.input.css` (~Z. 2137–2145): neuer Scope-Modifier, ohne die globalen Farben zu ändern
  (Objektkarte, Wasserstellen-Admin und Lageführung bleiben unverändert):
  ```css
  .hydranten-blau .hydrant-icon--ueberflur,
  .hydranten-blau .hydrant-icon--hydrant { background: #2563eb; }
  .hydranten-blau .hydrant-icon--unterflur { background: #1e3a8a; }   /* bleibt eckig */
  .hydranten-blau .hydrant-icon { opacity: .9; }
  ```
  Überflur bleibt rund mit „H“, Unterflur eckig mit „UH“: Die Unterscheidung läuft über Form und Text.
- `app/templates/objekt/infoscreen_alarm.html`:
  - Den Container `#is-karte` (Z. ~739) mit der Klasse `hydranten-blau` versehen.
  - Legende Z. 743/744: Überflur `#2563eb` als Kreis, Unterflur `#1e3a8a` als Quadrat.
  - Einsatzort-Marker größer zeichnen (weißer Rand) und mit `zIndexOffset: 1000` immer über die Hydranten legen.
- `app/templates/incident/info.html` und `app/static/js/einsatz_info.js` (Z. ~263–300): Karten-Container und
  Hydrantenliste bekommen `hydranten-blau`; Legende bzw. Listen-Icons gleich anpassen; Einsatzort-Marker
  ebenfalls mit `zIndexOffset`.
- Öffentliche Alarmzusammenfassung `app/templates/public/alarm_summary.html` (Z. 98–116) gleich behandeln,
  sie ist die Einsatz-Info per SMS-Link.
- Das Teams-/Mail-Kartenbild (`staticmap_service.render_incident_map_png`) zeigt keine Hydranten und bleibt
  unverändert.
- **Tests:** Bestehende Infoscreen- und Einsatz-Info-Rendertests erweitern: Das HTML enthält `hydranten-blau`,
  die Legende enthält `#2563eb` und nicht mehr `#dc2626` für Überflur.

## P2 – Einsatztimer stoppt beim Abschluss

**Ist:**

- `app/templates/incident/dashboard.html:726–735`: Der Timer zählt ab `started_at` ohne Ende, auch bei
  geschlossenen Einsätzen in der Nur-Ansicht (#549).
- Infoscreen `infoscreen_alarm.html:314–328` (`alarmBeginn`): Auch er zählt ohne Ende, und der GSL-Modus nutzt
  denselben Timer.
- Live-Banner `app/static/js/app.js:356–363` (`tick()`) rechnet ebenfalls ohne Ende.

**Soll:** Ist der Einsatz abgeschlossen, bleibt der Timer auf `closed_at − started_at` stehen und wird
optisch gedämpft. Dazu kommt das Label „Abgeschlossen“ bzw. „Einsatzdauer (abgeschlossen)“.

- `app/routers/ui_incident.py:1705`: zusätzlich `closed_at_iso` (Z-Suffix, `None` bei aktiven Einsätzen)
  in den Kontext geben.
- `incident/_dashboard_body.html:61`: `#elapsedTimer` bekommt `data-started-at` und `data-closed-at`. So tragen
  WS-Swaps den aktuellen Zustand mit.
- `dashboard.html`: `tickTimer()` liest beide Attribute pro Tick aus dem Element. Ist `closed-at` gesetzt, wird
  einmal fest gerendert und nicht weitergezählt.
- `ui_infoscreen_alarm.py:459`: `"ende": closed_at` ins Payload aufnehmen, GSL-Payload Z. 263 analog mit
  `ended_at`/`closed_at` der Lage (Feldnamen prüfen). JS: `alarmEnde`; der Tick nimmt `alarmEnde || Date.now()`.
- `app.js` `tick()`: Liegt `closed_at`/`ended_at` im Live-Objekt, wird damit gerechnet. Der Server liefert
  das Feld in den Live-Daten mit (Quelle der Live-Daten suchen, `started_at` daneben ergänzen).
- **Tests:**
  - Dashboard-Render eines geschlossenen Einsatzes enthält `data-closed-at`.
  - Das Infoscreen-JSON enthält `ende`.
  - JS-Unit-Test (`npm run test:js`) für die Dauerberechnung mit und ohne Ende. Falls dafür eine Funktion
    nötig ist, `formatElapsedDuration` ergänzen.

## P3 – Straßensperre mit mehreren Abschnitten (Datenmodell und Service)

**Anlass:** Mail der Gemeinde vom 06.11.2026: „Verordnung zur Straßensperre 09.11.–18.12.2026, Dammstraße, Feldweg,
Inselstraße, Röhleweg, Sportplatzstraße“. Eine Verordnung betrifft **fünf Straßen**, heute ist pro Sperre aber
nur eine Straße/Von/Bis und eine Geometrie möglich. `road_closure_document_service.py:204–212` findet bereits
bis zu 10 Straßen im PDF, verwendet aber nur `streets[0]`.

**Bereits vorhanden:** `road_closure_geo_service.validate_geometry` akzeptiert `MultiLineString`/`MultiPolygon`.
Routing (`classify`, `avoid_polygon`) arbeitet mit shapely und kann damit mehrteilige Geometrien verarbeiten.

**Modell:**

- Neue Tabelle `road_closure_section`, Migration `0351_road_closure_sections`; nur Nummern-ID, MariaDB-tauglich.
  Spalten:
  - `id`
  - `org_id` (TenantScoped)
  - `road_closure_id` (FK, CASCADE)
  - `position` (int)
  - `street`, `from_text`, `to_text`, `direction`
  - `geometry_geojson` (Text)
  - `geometry_status`, `geometry_quality`, `geometry_meta_json`
  - `created_at`/`updated_at`
- Die Felder `street/from_text/to_text/direction/geometry_*` auf `road_closure` bleiben als **Aggregat**
  bestehen, damit alle Leser (Liste, Suche, Public, Teams, MCP-Lesen, Routing) weiter funktionieren:
  - `street` = Straßennamen mit „, “ verbunden (max. 200 Zeichen, sonst „Dammstraße, Feldweg +3“)
  - `from_text`/`to_text` nur bei genau einem Abschnitt
  - `geometry_geojson` = Vereinigung aller Abschnittsgeometrien: nur Linien ergeben `MultiLineString`, nur
    Flächen `MultiPolygon`, gemischt eine `GeometryCollection`. Für Letztere `validate_geometry`, `bbox`,
    `_metric_geometry`, `avoid_polygon` und `render_road_closure_map_png` erweitern.
  - `geometry_status` = `ok` nur, wenn alle Abschnitte `ok` sind; sonst `pending`/`missing`.
    `geometry_quality` = schlechteste Qualität.
- Datenmigration: Jede bestehende Sperre mit Straße oder Geometrie bekommt genau einen Abschnitt
  (`position` 0) aus den Bestandsfeldern.
- `road_closure_service`: `set_sections(db, closure, sections, user_id, source)` validiert jeden Abschnitt,
  schreibt die Tabelle neu, berechnet das Aggregat (`_aggregate_sections`), schreibt Audit
  (`RoadClosureChange` field=`sections`) und ruft `mark_routes_stale_for_closure`. `create_closure` und
  `update_closure` akzeptieren optional `sections`. Ohne `sections` bleibt das alte Verhalten:
  Einzelfelder werden als ein Abschnitt übernommen.
- `find_duplicates`/`find_related`: Straßenvergleich über alle Abschnittsnamen.
- Suche (`app/services/suche/live.py:234`): Abschnittsstraßen in den Suchtext aufnehmen.
- `road_closure_public_service.py:55–61`: Kurztext mehrzeilig je Abschnitt.
- **Tests (neu `tests/test_road_closure_sections.py`):**
  - Aggregat bei 1 und 5 Abschnitten
  - gemischte Geometrie
  - Status `ok` nur, wenn alle `ok` sind
  - Migration der Bestandsfelder in einen Abschnitt
  - Dublettenerkennung über den zweiten Abschnitt
  - Routing-Klassifikation trifft den dritten Abschnitt
  - Tenant: Abschnitt fremder Org ist nicht lesbar

  Eindeutige Straßennamen je Test verwenden (gemeinsame test.db).

## P4 – UI, PDF-Entwurf und MCP für mehrere Abschnitte

- `road_closure/edit.html` Abschnitt „Ort“: Die Felder Straße/Von/Bis/Richtung werden zu einer wiederholbaren
  Abschnittsliste (Alpine). Je Abschnitt gibt es:
  - die Felder Straße, Von, Bis und Richtung
  - einen Button „Abschnitt aus Adresse ermitteln“
  - den Status von Geometrie und Qualität
  - einen Button „Entfernen“

  Unter der Liste steht der Button „+ Weitere Straße“. Die Daten gehen als verstecktes `sections_json` ans
  Formular, Ort und Gemeinde bleiben gemeinsam. Mobil (≤760px) stehen die Abschnitte als gestapelte Karten
  untereinander.
- `app/static/js/road_closure_editor.js`, Kopfkommentar „Editor fuer genau eine Sperrengeometrie“: Je
  Abschnitt gibt es eine eigene Layer-Gruppe mit eigener Farbe und Nummern-Label.
  - Zeichnen/Bearbeiten wirkt auf den aktiven Abschnitt.
  - `pm:create` ersetzt nicht mehr alles, sondern setzt die Geometrie des aktiven Abschnitts.
  - „Abschnitt ermitteln“ ruft `/strassensperren/abschnitt` pro Abschnitt auf.
  - Am Ende passt `fitBounds` die Karte auf alle Abschnitte ein.
  - „Geometrie geprüft“ wird je Abschnitt gesetzt.
- `app/routers/ui_road_closure.py`: Create/Update lesen `sections_json` und rufen `set_sections`.
  `/strassensperren/abschnitt` bleibt für einen Abschnitt zuständig. Die Detailseite listet alle Abschnitte;
  in der Karte sind alle Abschnitte zu sehen.
- PDF-Entwurf (`road_closure_document_service.py:204`): Für jede erkannte Straße entsteht ein Abschnitt-Vorschlag
  (`abschnitte: [{street, from_text, to_text}]`). `title_vorschlag` erhält bei mehreren Straßen die Form
  „Straßensperre Dammstraße, Feldweg +3“. Der UI-Import aus PDF füllt die Abschnittsliste vor.
- MCP (`app/mcp/tools/strassensperren.py`, Wrapper in `app/mcp/server.py`):
  - `strassensperre_anlegen`/`_aktualisieren` bekommen den optionalen Parameter
    `abschnitte: list[{street, von, bis, richtung, geometry_geojson?}]`.
  - `strassensperre_geometrie_ermitteln` bekommt optional `abschnitt_index` (oder `abschnitt_id`). Bei
    `uebernehmen` wird nur dieser Abschnitt gesetzt, danach wird das Aggregat neu berechnet.
  - `strassensperre_geometrie_bestaetigen` bekommt optional `abschnitt_id`.
  - `strassensperre_lesen` gibt zusätzlich `abschnitte` aus.
  - `strassensperre_entwurf_aus_pdf` gibt `abschnitte` aus.
  - Tool-Beschreibungen erweitern: „Eine Verordnung mit mehreren Straßen = eine Sperre mit mehreren Abschnitten“.
- Teams-Karte und Infoscreen-Sperren zeigen alle Abschnitte. Sie nutzen bereits das Aggregat, darum nur die
  Textzeile prüfen.
- Doku: `docs/` Straßensperren-Seite, Wiki-Seite Straßensperren, `CHANGELOG.md` (Unreleased).
- **Tests:**
  - UI-Create mit 2 Abschnitten über `sections_json`
  - Update entfernt einen Abschnitt
  - PDF-Entwurf mit 5 Straßen liefert 5 Abschnitte
  - MCP: Anlegen mit 5 Abschnitten, `geometrie_ermitteln` mit `abschnitt_index=2` und `uebernehmen`;
    `lesen` liefert die Abschnitte
  - CSRF im Formular vorhanden

## P5 – RLF-Störung: Ursache des Hängers um 07:40 und Gerätezuordnung

1. **Diagnose (Claude, vor Codex):** Mit `admin_fehler_details`/`admin_code_analyse` und `journalctl` auf Prod
   die vollständigen Meldungen von `_sq_end` und vom Loop-Lag-Watchdog um 06:40 UTC lesen. Zu klären ist,
   welche Abfrage bzw. welcher Loop die Event-Loop blockiert hat. Kandidaten sind Gateway-Routing-Poll,
   Hydranten/Overpass, LIS-Sync, PDF-Erzeugung und Redis.
2. **Fix:** Die blockierende Arbeit aus dem Event-Loop verlagern (Threadpool/`run_async`-Regeln, CLAUDE.md) oder
   die langsame Abfrage indizieren. Den genauen Auftrag schreibt Claude nach Schritt 1.
3. **Gerätezuordnung sichtbar machen:** `admin_geraet_details`/`admin_geraete_pruefen` zeigen zusätzlich das
   zugeordnete Fahrzeug bzw. die Einheit, sofern ein Gerät einem Fahrzeug zugeordnet ist (Feld im Gerätemodell
   suchen; gibt es keins, nur den Benutzer- oder Gerätenamen). Dann lässt sich „Störung RLF“ künftig direkt
   einem Gerät zuordnen.
4. **Tests:** Sysadmin-MCP-Ausgabe enthält das Fahrzeugfeld; die Fix-Tests ergeben sich aus der Ursache.

---

## Reihenfolge

P1 und P2 sind klein und unabhängig, sie gehen in **einen** PR mit zwei Commits. P3 und P4 kommen als
eigener PR, P4 gestapelt auf P3. P5 startet erst nach der Prod-Diagnose. Vor jedem Merge laufen ruff, mypy,
`pytest -n auto` und, falls `app/static/js` betroffen ist, `npm run test:js`.
