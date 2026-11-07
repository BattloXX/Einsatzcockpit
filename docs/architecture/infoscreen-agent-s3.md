# Infoscreen-Agent S3: Heartbeat und Wake-Hinweise

Die Device-Bearer-Authentifizierung aus S1 und die Config-/State-Snapshots aus S2
bleiben massgeblich. Der Heartbeat meldet technische Beobachtungen; er aendert
weder die Server-Konfiguration noch die vom Resolver vergebene Revision.

`POST /api/v1/infoscreen-agent/heartbeat` akzeptiert ausschliesslich:

- `agent_version` (1-50 Zeichen), `os_version` (1-100), `hostname` (1-255;
  Buchstaben, Ziffern, Punkt, Unterstrich, Bindestrich).
- `config_version` und `last_revision` (maximal 32 Display-Keys): nichtnegative
  Ganzzahlen bis 2^63-1, keine Bool-Werte/Strings. Der gemeldete Config-Stand wird
  separat als `reported_config_version` gespeichert.
- `displays` (maximal 32, keine doppelten Keys): `display_key` (1-100;
  Buchstaben, Ziffern, `_ . : -`), `power` (`on`, `off`, `unknown`),
  `verified` (strikter Bool), `backend`, `page_state`, `errors` (maximal 16).

Backends: `sway`, `gnome-mutter`, `x11`, `cec`, `ddc`, `logical_blank`, `unknown`.
Seitenstatus: `ready`, `loading`, `failed`, `offline`, `blank`, `unknown`.
Fehlercodes: `backend_unavailable`, `power_failed`, `power_unverified`,
`browser_failed`, `page_unreachable`, `renderer_failed`, `offline`, `clock_skew`,
`update_failed`. Freitext, URLs und weitere Felder werden mit 422 abgelehnt.
Alle gemeldeten Display-Keys muessen zum authentifizierten Geraet UND dessen Org
gehoeren; ansonsten 404 ohne teilweise Speicherung. Das Limit von 6/min gilt je
Geraet, damit mehrere PCs hinter einer NAT-IP unabhaengig bleiben.
Die Antwort enthaelt den Server-Stand: `{config_version, revisions: {display_key: int}}`.

`WS /ws/infoscreen-agent` nutzt ausschliesslich den Authorization-Bearer-Header.
Ein neuer Connect ersetzt die DB-Presence atomar. Jeder Zustellversuch prueft die
aktuelle Presence; ein alter Disconnect kann die neue Verbindung nicht loeschen.
Ungueltige/gesperrte/verdraengte Verbindungen werden mit 4401 geschlossen.
Der Server pingt alle 25 Sekunden und erwartet innerhalb von 25 Sekunden
`{"type":"pong"}` (sonst 4408). Nur Pong erneuert die Presence (TTL 75 Sekunden).
Bei Worker-Abbruch reicht der abgelaufene UTC-Zeitstempel fuer die Offline-Erkennung.

Der Server sendet ausschliesslich `{"type":"ping"}` und
`{type: "invalidate", revision, reason, display_key}`. Ein Hinweis darf eine
bereits bekannte Revision enthalten. Der Agent muss trotzdem den Snapshot
abfragen: Erst dessen Aufloesung erhoeht bei einer fachlichen Aenderung atomar
die Revision. Weder ein Hinweis noch ein Client-Heartbeat vergibt Revisionen.
Connect und Redis-Reconnect senden lokale Wake-Hinweise zum Nachholen verpasster
Aenderungen. Mehrfach empfangene Hinweise sind unschaedlich.

Der neue Bus-Kanal `ec:isa` wird vor dem Bus-Start registriert. Erfolgreiche
Redis-Publikationen werden ueber die Subscriber zugestellt, im Ausfall lokal.
Ein anderer Worker erkennt eine verpasste Sperre/Verdrängung spaetestens bei
seiner naechsten Presence-Pruefung; beim Bus-Reconnect erfolgt sofortiger Abgleich.

Die gemeinsamen Post-Commit-Broadcast-Pfade liefern entkoppelte Hinweise:

| Bestehender Pfad | Zentral erfasste Ereignisse |
|---|---|
| UI/API Incident, Alarm-Apply, DIBOS-Anlage/-Update, Autoclose | `incident_*`, `einsatz_live`, `alarm_type_changed`, `address_updated` |
| GSL-Anlage/-Aenderung/-Ende, automatische GSL-Anlage | `gsl_*`, `lage_closed`, `lage_updated` |
| GSL-Wiedereroeffnung | `lage_updated` |
| Planspiel-Start, Regie/Generator, Ende | `planspiel_changed`, dazu Incident-/GSL-Broadcasts |

Der Hook uebernimmt ausschliesslich Kanal-ID und Ereignistyp. DB-Zugriffe,
Redis und Socket-I/O laufen in separaten, fehlertoleranten Tasks. Bestehende
ELVIS-/LIS-Dateien und Poller werden nicht geaendert. GSL-Startmeldungen verwenden
teilweise schon im Bestand den generischen Typ `gsl_changed`; die Wake-Hinweise
behalten diesen generischen Grund bei. Der Snapshot bestimmt den tatsaechlichen Modus.

S4/S5 koennen nach ihrem Konfigurations-Commit
`await infoscreen_agent_service.publish_config_changed(org_id, display_ids)`
aufrufen. Die IDs werden immer nochmals nach Org gefiltert; `[]` versendet nichts.
Der mutierende Service muss `config_version` selbst erhoehen. Sperren und
Token-Rotation schreiben transaktional `token_revoked`; erst nach erfolgreichem
Commit wird der Verbindungsabgleich auf den langlebigen Worker-Loop gestellt.
Ein Rollback trennt keine Verbindung.

`connect`, `disconnect` und `token_revoked` enthalten keine Hostnamen, IPs,
Tokens oder Einsatzinhalte. Die vorhandene Event-Tabelle und Presence-Spalten
stammen bereits aus 0355; 0357 ergaenzt nur nullable Heartbeat-Spalten, keine FKs.
Der bestehende taegliche KI-Log-Retention-Loop loescht unabhaengig von seinem
KI-Fehlerbereich auch Infoscreen-Events nach 90 Tagen, org-gefiltert in Batches.

## Geaenderte Dateien und Abweichungen vom Plan

| Bereich | Dateien |
|---|---|
| Heartbeat und Speicher | `app/routers/infoscreen_agent_api.py`, `app/models/infoscreen_agent.py`, `alembic/versions/0357_infoscreen_heartbeat.py` |
| WS, Presence, Wake-Hooks, Audit | `app/routers/ws.py`, `app/services/infoscreen_agent_events.py`, `app/services/broadcast.py`, `app/services/infoscreen_agent_service.py` |
| Worker-Bus und Lebenszyklus | `app/services/ws_bus.py`, `app/main.py` |
| Bestehender Retention-Lauf | `app/services/infoscreen_agent_retention.py`, `app/services/ai_log_retention.py` |
| Tests und begruendete lokale Socket-/Task-Halter | `tests/test_infoscreen_agent_s3.py`, `tests/test_public_tenant_isolation.py`, `tests/test_migration_0355_infoscreen_agent.py`, `tests/data/module_state_allowlist.txt` |
| Dokumentation | diese Datei, `CHANGELOG.md` |

Die Bestandspruefung korrigiert/praezisiert folgende Planannahmen:

- Presence-Spalten und Event-Tabelle existieren bereits seit S1. 0357 fuegt
  ausschliesslich Heartbeat-Speicher hinzu; es sind keine neuen FKs erforderlich.
- GSL-Wiedereroeffnung benutzt `lage_updated`, nicht `gsl_changed`. Manuelle
  Incident-Adress-/Alarmtypaenderungen benutzen `address_updated` bzw.
  `alarm_type_changed`. Auch diese Pfade werden zentral erfasst.
- GSL-Starts verwenden im Bestand zum Teil generische `gsl_changed`-/`gsl_live`-
  Meldungen. Die Hinweise behalten dort den Grund `gsl_changed`; der Resolver
  bestimmt Start/Ende/Modus. Es werden keine Browser-Ereignistypen umbenannt.
- Sperren/Rotation aus S1 loeschen schon die DB-Presence, hatten aber bisher
  keinen aktiven Socket-Abgleich. Der neue transaktionale Audit-/Commit-Hook
  ergaenzt diesen Abgleich auch fuer synchrone DB-Threads.
- Die Prozesszustands-Allowlist erlaubt bewusst lokale Socket-Referenzen und
  laufende Tasks, wie beim Gateway. Authentifizierung, Verbindungsbesitz,
  Presence und Revisionen bleiben dauerhaft in der DB; der Testwaechter bleibt
  unveraendert.

Die 97 gezielten Tests decken auch Schema-Roundtrips, DDL fuer SQLite/MySQL/
MariaDB, den Modulzustands-Waechter und die Public-Cross-Org-Suite ab. Der
2-Worker-Test simuliert Publisher und Subscriber mit getrennten Socket-Haltern
und dem registrierten Bus-Handler; er ersetzt keinen echten Redis-Netzwerktest.
Lokal ist `MARIADB_TEST_URL` nicht gesetzt. Ein echter MariaDB-Migrationslauf
wurde daher nicht ausgefuehrt und bleibt dem vorhandenen CI-Migrationsjob.

## Pruefergebnis (2026-11-07)

- `.venv/bin/python -m ruff check app/`: bestanden.
- `.venv/bin/python -m mypy app/ --ignore-missing-imports`: bestanden,
  663 Quelldateien (nur bestehende Hinweise zu ungeprueften untypisierten Funktionskoerpern).
- Gezielter Lauf S3, Public-Tenant-Isolation, Migration/Modell und
  Modulzustands-Waechter: **97 bestanden**, 11,95 Sekunden.
- `.venv/bin/python -m pytest -q -n auto -p no:warnings`: **10.795 bestanden,
  248 uebersprungen, 4 fehlgeschlagen**, 425,91 Sekunden. Vor jedem Lauf wurde
  `ps -eo args | grep "[p]ytest"` geprueft; keine parallelen pytest-Laeufe.
- Die vier Fehler sind ausschliesslich
  `tests/test_admin_uebersicht_deploy.py::test_deploy_und_befunde` mit den
  Parametern `0`, `None`, `exception`, `sha_unbekannt`. Sie entsprechen den im
  Auftrag benannten lokalen Bestandsfehlern. Main wurde nicht separat erneut getestet.
- Im ersten Gesamtlauf wurden zusaetzlich der noch nicht um 0357 erweiterte
  Schema-Vergleichstest und die fehlenden Socket-/Task-Allowlist-Eintraege
  gefunden. Beide wurden korrigiert; der abschliessende Gesamtlauf hat diese
  Befunde nicht mehr.
- `git diff --check`: bestanden. Nach Testende keine zusaetzlichen unversionierten
  Testartefakte im Arbeitsverzeichnis. Branch weiterhin `feat/infoscreen-agent-s3`;
  kein Commit, Push oder Branch-Wechsel.

Verifizierte bestehende Hook-Fundstellen (zentral abgefangen, dort keine neuen
Netzwerkaufrufe hinzugefuegt):

- `app/routers/ui_incident.py`: Anlage, `alarm_type_changed`, `address_updated`,
  Abschluss und Wiedereroeffnung.
- `app/routers/ui_major_incident.py`: Anlage, Bearbeiten, Abschluss und Wiedereroeffnung.
- `app/services/alarm_apply_service.py`: manueller Alarmupdate und serieller Alarm-Nachlauf.
- `app/services/dibos/dibos_enrich.py`: Anlage-/Update-Benachrichtigung und Abschluss.
- `app/services/autoclose.py`: automatischer Incident-Abschluss.
- `app/services/incident_live_notify.py`, `app/services/gsl_live_notify.py`:
  gemeinsame Live-Aenderungsmeldungen, auch bei automatischer GSL-Anlage.
- `app/services/planspiel_run_service.py`, `app/services/planspiel_engine.py`,
  `app/routers/ui_planspiel.py`: Start, Regie/Generator-Aenderungen und Ende.
