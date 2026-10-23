# Statistik-Infoscreen

Der Monitor `/infoscreen/statistik/{token}` verwendet die Farben, lokalen Schriften,
Kopfzeile und Kartenflächen des Wetter-Monitors. Das 1920 × 1080 große Layout wird
proportional auf die Bildschirmgröße skaliert, auch auf 4K; Chart.js rendert die
Diagramme mit angepasster Pixeldichte. Es gibt keine scrollenden Inhalte.

## Bedienung und Dauerbetrieb

- Ansicht 1: sieben Kennzahlen, Leaflet-/OSM-Karte, Alarmstichworte und Fuhrpark.
- Ansicht 2: gestapelte Monatsentwicklung, Tageszeiten, Wochentage und Einsatzdauer.
- Automatischer Wechsel alle 30 Sekunden, über die Fußzeile pausierbar. Die Auswahl
  bleibt je Organisation und Browser gespeichert. Beide Ansichten sind manuell anwählbar.
- URL-Optionen: `?rotate=0` deaktiviert den Wechsel, `?interval=60` setzt ihn auf
  60 Sekunden (zulässig: 10–300). Kombination: `?rotate=0&view=development` für eine
  feste Entwicklungsansicht. `rotate=1` schaltet den Wechsel ausdrücklich ein.
- Datenabruf alle fünf Minuten über `/{token}/daten`, ohne Seitenreload. Die Uhr läuft
  sekündlich in der Zeitzone der Organisation. Diagramme und Karteninstanz bleiben erhalten.
- Bei Netz-/Serverfehlern bleibt der letzte gültige Stand im geöffneten Monitor sichtbar;
  gelber Statuspunkt und Wiederholungsversuch nach einer Minute. Der angezeigte
  Datenstand ist der Erstellungszeitpunkt des letzten gültigen Datensatzes.
- Anfragen brechen nach 20 Sekunden ab; gleichzeitige Abrufe werden verhindert.
  Gelöschte Tokens bzw. deaktivierte Screens erhalten auch beim Datenabruf keinen Zugriff.
- Der vorhandene optionale Redis-JSON-Cache wird organisations- und tagesweise für
  höchstens fünf Minuten genutzt. Ohne Redis wird direkt aus der Datenbank gelesen.
  Antworten werden nicht im HTTP-Cache abgelegt. Kein zusätzlicher Cache-Dienst.

## Datenbasis und Berechnung

Alle Auswertungen beziehen sich ausschließlich auf Einsätze im laufenden Kalenderjahr
bis zum Erstellungszeitpunkt, in der Zeitzone der Organisation. Übungen und zukünftige
Alarmierungen zählen nicht. Die bestehende Sichtbarkeit eigener und gemeinsam
bearbeiteter Einsätze bleibt erhalten. Keine Vorjahres- oder Personalstatistiken.

- **Brand / Technisch:** vorhandene Alarmtypenkategorie B/F bzw. T; unbekannte Kategorien
  bleiben Sonstige. Alarmstichworte werden unverändert übernommen und nach Häufigkeit
  sortiert. Kleine Gruppen werden bei Bedarf als Weitere zusammengefasst.
- **Aktueller Monat:** Anzahl seit lokalem Monatsbeginn. Ohne belegbaren Datenzeitraum
  erscheint ein Strich, kein angenommener Nullmonat.
- **Monatsdurchschnitt:** Gesamtzahl / Anzahl Kalendermonate vom ersten gespeicherten
  Einsatzmonat dieses Jahres bis einschließlich laufendem Monat. Der laufende Monat
  ist unvollständig. Monate davor sind unbekannt, nachfolgende leere Monate zählen mit
  Null; zukünftige Monate sind offen. Ohne Einsatzdaten gibt es keinen Durchschnitt.
- **Median Einsatzdauer:** bevorzugt `ready_again_at - started_at`, ersatzweise
  `closed_at - started_at`. Erkennbare automatische LIS-/48h-Verwaltungsabschlüsse
  ohne Einsatzbereitschaft werden ausgeschlossen. Fehlende, negative und zukünftige
  Endzeiten zählen nicht. Die Zahl gültiger Zeitpaare wird angezeigt.
- **Median Alarmierung bis Einsatzort:** ausschließlich `on_scene_at - started_at`;
  keine Ableitung aus Fahrzeuganlage oder Fahrtenbuch. Negative/zukünftige Ankünfte,
  Ankünfte nach dem Ende oder vor einer erfassten Ausfahrt werden ausgeschlossen.
- **Dauerklassen:** unter 30, 30 bis unter 60, 60 bis einschließlich 120, über 120 Minuten.
  Nur gültige Zeitpaare; fehlende Angaben werden nicht als Dauer Null eingestuft.
- **Tageszeiten / Wochentage:** lokale Alarmierungszeit; Montag bis Sonntag.
- **Fuhrpark:** bestehende Fahrzeugzuordnungen (`IncidentVehicle`), eindeutig nach Einsatz.
  Wiederholte Zuordnungen und gleich benannte Stammdatensätze derselben Organisation
  werden über die Vereinigung ihrer Einsatz-IDs entdoppelt. Entfernte Zuordnungen zählen
  wie bisher nicht. Die Ansicht zeigt den aktiven eigenen Fuhrpark, sortiert nach Häufigkeit.
  Fahrtenbuch-Einsatzfahrten sind eine andere Größe und werden nicht als Beteiligungszahl
  übernommen. Kilometer-/Betriebsstundenzähler werden auf dem Monitor nicht angezeigt.

## Karte und Datenschutz

Die vorhandenen lokalen Leaflet-, Markercluster- und OSM-Tile-Proxy-Komponenten werden
verwendet. Brand ist rot, Technisch blau. Der Ausgangspunkt ist die konfigurierte
Organisationsposition, ersatzweise Wolfurt. Nur Punkte innerhalb von 25 km bestimmen
den automatischen Ausschnitt; entfernte Punkte bleiben in der Kartenebene erhalten und
werden separat gezählt. Keine automatischen Rundfahrten; identische Daten verändern
den Ausschnitt beim Aktualisieren nicht.

Die öffentliche HTML-/JSON-Antwort enthält keine Einsatz-IDs, Nummern, Adressen,
Personen, Meldungstexte oder genauen Einsatzkoordinaten. Orte sind auf zwei Dezimalstellen
(ca. 1-km-Raster) gerundet. Der frühere Ticker mit genauen Einsatzadressen entfällt.

## Grenzen der vorhandenen Daten

Ein verlässlicher Beginn der vollständigen Datenerfassung ist nicht hinterlegt. Deshalb
ist der erste gespeicherte Einsatz die ausdrücklich angezeigte Grundlage des
Monatsdurchschnitts, kein Nachweis lückenloser Erfassung. Zeitkennzahlen hängen von
den gepflegten Zeitstempeln ab; verspätete manuelle Abschlüsse sind nicht durchgängig
als solche erkennbar. Es werden keine fehlenden Zeiten geschätzt. Die im Auftrag
angeführten MCP-Einsatzfahrten sind keine Prüfsollwerte für unterschiedliche
Fahrzeugbeteiligungen. Es wurden keine Produktionsdaten geändert oder ergänzt.

## Prüfung

Backendtests prüfen Kalender-/Zeitzonengrenzen, Übungen, Organisationssichtbarkeit,
fehlende/ungültige Zeiten, Dauerklassen, Fahrzeugentdoppelung und datensparsame Orte.
Token- und Cross-Org-Tests decken auch den JSON-Endpunkt ab. Ein optionaler Playwright-Test
verwendet die tatsächlichen vendorten Diagramm-/Kartenbibliotheken und prüft Full HD,
4K, Ansichtswechsel, Wiederherstellung nach Fehlern und Aktualisierung ohne neue Charts.
Bei fehlendem Chromium wird nur dieser Browsertest übersprungen; alternativ kann
`ECP_PLAYWRIGHT_EXECUTABLE` auf eine vorhandene Chromium-Datei gesetzt werden.
