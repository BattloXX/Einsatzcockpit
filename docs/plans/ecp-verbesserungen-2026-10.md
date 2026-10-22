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
