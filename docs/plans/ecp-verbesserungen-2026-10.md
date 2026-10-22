# ECP-Verbesserungen 2026-10 – Umsetzungsdokumentation

Plan: Auftrag vom 2026-10-22 (ECP-01..07, GSL-01..07, APP-01/02, DOC-01, SEC-01..04).
Pro Block: ID / Status / Ursache / Loesung / Dateien / Datenbank / Tests / Hinweise.

## ECP-02 – Spalten-Umbenennen

| Feld | Inhalt |
|---|---|
| Status | Teilweise – Ursache des dauerhaften Verlusts nicht reproduziert |
| Ursache | Serverseitig sicher: `rename_column_endpoint` aendert nur `column.title`, alle Karten und Services nutzen `column_id`/`code`. Regressionstest (3x Umbenennen) zeigt keinen Verlust. Verdacht liegt im Client: Einzelspalten-Swap per OOB (`_swapColumn`), Tabs/Dropdown wurden nicht nachgezogen. |
| Loesung | `column_renamed` laedt die komplette Kanban-Struktur (`_swapKanban`) und zieht Tabs und Dropdown nach (`syncLaneTabs`). Neues Lesescript `scripts/spalten_diagnose.py` findet Karten ohne gueltige Spalte. |
| Dateien | `app/static/js/app.js`, `app/static/js/incident-board.js`, `scripts/spalten_diagnose.py`, `tests/test_incident_column_rename.py` |
| Datenbank | keine |
| Tests | `tests/test_incident_column_rename.py` (2), Gesamtlauf 5887 passed |
| Hinweise | Auf Prod `scripts/spalten_diagnose.py` ausfuehren. Tritt der Verlust weiter auf: betroffenen Einsatz mit Uhrzeit und Browser/Geraet melden, dann `IncidentChange` pruefen. |

## ECP-05 – LIS nur bei Statusaenderung

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Live-Test gegen LIS offen) |
| Ursache | `push_vehicle_status_to_lis` wurde bei jedem Status-POST und bei jedem Karten-Verschieben aufgerufen, ohne Vergleich. Kein Retry, kein Erfolgsprotokoll; der Poll konnte frisch gesetzte Status zurueckschreiben. |
| Loesung | Aufrufer senden nur bei geaendertem `unit_status`. Im Push zusaetzlich Schutz gegen identische Wiederholung (zuletzt erfolgreich gesendeter LIS-Prefix je Fahrzeug), 3 Versuche mit Backoff 1 s/3 s, Erfolg als `IncidentChange` `lis.status_pushed`. Der Poll respektiert ein 30-s-Schutzfenster fuer lokal gepushte Status und loescht den Merker, wenn er einen LIS-Status uebernimmt. |
| Dateien | `app/services/lis/lis_sync.py`, `app/routers/ui_incident.py`, `tests/test_lis_sync.py`, `tests/test_lis_status_route.py` |
| Datenbank | keine |
| Tests | 4 neue Tests in `test_lis_sync.py`, 1 Routentest |
| Hinweise | Merker liegt im Prozessspeicher (nach Neustart wird der naechste Status wieder gesendet). Bei mehreren Gunicorn-Workern hat jeder Worker eigenen Speicher; dadurch ist eine seltene Doppelsendung moeglich, aber nie eine verlorene Aenderung. |

## GSL-01 – Neue Einsaetze live im GSL-Board

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Test mit zwei echten Browsern/Tablets offen) |
| Ursache | Die automatischen Pfade (`POST /einsatz/neu` mit Trigger-Alarmart oder Uebernahme in laufende Lage, API-Alarm `/einsatz`) legten die Einsatzstelle an, sendeten aber nur `notify_gsl_created` und keinen `site_created`-Broadcast. Der API-Pfad `POST /lage/.../stelle` sendete `site_created` ohne `site_id`, die Karte ignorierte das. |
| Loesung | Nach dem Commit ruft jeder Pfad `gsl_broadcast.stelle_angelegt` auf (Board laedt per `sitePhaseChanged` komplett neu, daher keine Duplikate). `site_id` im API-Broadcast ergaenzt. Reconnect-Resync und manueller Refresh bleiben unveraendert. |
| Dateien | `app/routers/ui_incident.py`, `app/routers/api_v1.py`, `tests/test_gsl_site_created_broadcast.py` |
| Datenbank | keine (UNIQUE-Index auf `(major_incident_id, external_key)` bewusst nicht ergaenzt, solange Altduplikate ungeprueft sind) |
| Tests | 2 neue Tests, schlagen ohne Fix fehl |
| Hinweise | LIS/Mail/DiBOS legen keine Einsatzstellen an; dort ist nichts zu tun. |

## GSL-02 – Prioritaetsfarben Lagekarte

| Feld | Inhalt |
|---|---|
| Status | Erledigt (Sichtpruefung am Geraet offen) |
| Ursache | Marker-Farben waren in vier Kopien hartkodiert und wichen von den Board-Farben ab (`#ef4444` statt `#d42225` usw.). Prioritaetsaenderungen (`site_prio_changed`) aktualisierten die Karte nicht, `site_created` ohne `site_id` wurde ignoriert, die Prioritaetslegende fehlte. |
| Loesung | Eine Funktion `site_map_color` und `SITE_PRIORITY_HEX` neben `SITE_PRIORITY_COLOR`, Werte identisch zu `--red/--orange/--yellow`. Karte reagiert auf `site_prio_changed`, Prioritaetslegende mit Buchstabe + Text (nicht nur Farbe). |
| Dateien | `app/models/major_incident.py`, `app/routers/ui_major_incident.py`, `app/templates/incident_major/karte.html`, `tests/test_gsl_prio_farben_karte.py` |
| Datenbank | keine |
| Tests | 8 neue Tests |
| Hinweise | Der Infoscreen (`ui_infoscreen_alarm.py`, `_GSL_PRIO_COLOR`) und die Druckvorlage (`druck.html`) haben eigene Paletten und wurden nicht angefasst. |
