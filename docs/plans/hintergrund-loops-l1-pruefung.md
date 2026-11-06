# Hintergrund-Loops L1: Aenderungen und Pruefung

Stand: 2026-11-06. Worktree `/home/johannes/projects/ecp-loops`, Branch
`fix/hintergrund-loops-thread`. Kein Commit. Keine Migration.

L1 ist umgesetzt. Die Baseline enthaelt **53 Eintraege**; der korrekt nach
Funktionskontext abgegrenzte Scan vor L1 hatte 60. Es wurden sieben Eintraege
entfernt und keine hinzugefuegt. Die bereits ausgelagerten Org-Listen, Abfluss-Polls
und der Atemschutz-Watchdog werden durch die neuen Thread-Tests mit abgesichert.

## Zusammenfassung pro Datei

| Datei | Aenderung |
|---|---|
| [CHANGELOG.md](../../CHANGELOG.md) | Deutscher Eintrag oben unter Unreleased, mit "Keine Migration" und Hinweis auf die separat folgende DIBOS-Anreicherung. |
| [app/services/breathing_notify.py](../../app/services/breathing_notify.py) | Faellige IDs, Claims/Pruefung, Push und Ergebnisverbuchung im Thread. Push und Status bleiben atomar; SMS bleibt async. Bei SMS-Fehlern geht nur der Klassenname in den DB-Thread, die urspruengliche Exception bleibt fuer das bestehende Fehlerprotokoll auf dem Ursprungsloop. |
| [app/services/dibos/dibos_loop.py](../../app/services/dibos/dibos_loop.py) | Konfiguration auf einfache Werte reduzieren und Probe-Schreibzugriffe auslagern; Intake-Logik und Anreicherungsreihenfolge bleiben bestehen. |
| [app/services/dienst_monitor_dispatch.py](../../app/services/dienst_monitor_dispatch.py) | SMS-Konfiguration im DB-Thread aufloesen, Teams- und SMS-Versand auf dem urspruenglichen Loop. |
| [app/services/dienst_monitor_loop.py](../../app/services/dienst_monitor_loop.py) | Gesamten Check-/Claim-/Rollback-Ablauf mit thread-eigener Session ausfuehren; Entscheidungslogik unveraendert. |
| [app/services/incident_live_notify.py](../../app/services/incident_live_notify.py) | Org-Broadcast mit einfachem Payload auf dem urspruenglichen Loop ausfuehren. |
| [app/services/incident_notify.py](../../app/services/incident_notify.py) | Alarm-Outbox-Verarbeitung per Incident-ID auf dem urspruenglichen Loop starten; bestehende Commit-Reihenfolge erhalten. |
| [app/services/incident_route_loop.py](../../app/services/incident_route_loop.py) | Discovery, Claim, Berechnungstransaktion und unerwartete Fehler im Thread; Notification nach dem Commit auf dem Loop. |
| [app/services/lis/lis_loop.py](../../app/services/lis/lis_loop.py) | Eine eigene Session fuer Konfigurationsabfrage und gesamten Org-Sync im Thread; unveraenderte Query-Anzahl, Poll-Takt und persistenter Client. Ueber die Grenze gehen nur IDs, Konfigurationswerte und Client-Schluessel. |
| [app/services/lis/lis_sync.py](../../app/services/lis/lis_sync.py) | Reine I/O und ID-basierte Ereignis-Tasks zum urspruenglichen Loop reichen; DB und ORM des Sync bleiben im Thread. |
| [app/services/loop_thread_io.py](../../app/services/loop_thread_io.py) | Thread-/I/O-Bruecke mit Abbruch und Session-Cleanup; Client-Registry auf dem Ursprungsloop. Worker erhalten nur einen Schluessel, Client-Attribute und HTTP bleiben auf dem Ursprungsloop. |
| [app/services/mail_service.py](../../app/services/mail_service.py) | SMTP- und Resend-Transport des zentralen Dispatchers auf dem urspruenglichen Loop; bestehende DB-Lookups bleiben beim Aufrufer. |
| [app/services/o365_mail_service.py](../../app/services/o365_mail_service.py) | Graph-Transport mit einfachen Konfigurationswerten von den ORM-Zugriffen trennen; Fallback und Token-Retry erhalten. |
| [app/services/road_closure_incident_service.py](../../app/services/road_closure_incident_service.py) | Routing-Provider-Aufrufe auf dem urspruenglichen Loop, einschliesslich bestehender Timeout-/Alternativrouten-Behandlung. |
| [app/services/wordpress_report_service.py](../../app/services/wordpress_report_service.py) | Reinen Webhook-Transport ohne Session/ORM auf dem urspruenglichen Loop ausfuehren. |
| [docs/plans/hintergrund-loops-l1-pruefung.md](../../docs/plans/hintergrund-loops-l1-pruefung.md) | Dieses Pruefprotokoll mit Dateizusammenfassung und exakten Befehlen/Ergebnissen. |
| [docs/plans/hintergrund-loops-plan.md](../../docs/plans/hintergrund-loops-plan.md) | L1 auf umgesetzt, verbleibende 53 Eintraege, Transaktionsmuster und bewusste L2-/L3-Abgrenzung dokumentiert. |
| [tests/data/async_services_sync_db_baseline.txt](../../tests/data/async_services_sync_db_baseline.txt) | 53 sortierte, deduplizierte Eintraege nach L1 (vorher korrekt abgegrenzt: 60). |
| [tests/test_async_services_sync_db.py](../../tests/test_async_services_sync_db.py) | AST-Waechter mit korrekter Funktionsverschachtelung, Name-/Attributaufrufen, Regenerierungshinweis und Pruefung neuer/entfernter Eintraege. |
| [tests/test_background_loop_threads.py](../../tests/test_background_loop_threads.py) | 25 Laufzeitpruefungen fuer alle genannten Loops: DB im Thread; HTTP, Client-Attribute, Broadcast und Versand auf dem Ursprungsloop; echte LIS-Neuanlage mit Outbox, Claims/Fehlversand aller drei Monitor-Kanaele und Abbruch. |
| [tests/test_breathing_notify.py](../../tests/test_breathing_notify.py) | SMS-Adapter auf vorgeladene Konfiguration erweitert; zwei SMS-Fehlerregressionen fuer Backoff, Status, Lease und Logging. Bestehende Import-/Zeilenlaengen-Verstoesse manuell bereinigt. |

Unveraendert, da die DB-Bloecke bereits ueber `asyncio.to_thread` liefen:
`app/services/abfluss_poll_loop.py` und `_breathing_watchdog_loop` in
`app/services/breathing_service.py`. Die Laufzeitpruefungen erfassen beide.

Die verzahnten Transaktionen von LIS, Routen und Dienstmonitor laufen mit ihrer
eigenen Session in einem synchronen Thread-Aufruf. Dessen kurzlebiger Worker-Loop
fuehrt den bestehenden Ablauf aus; reine I/O-Aufrufe werden mit einfachen Nutzdaten
an den urspruenglichen Loop gereicht. Dadurch bleiben die bisherigen Transaktions-
und Commit-Grenzen erhalten. Sessions und ORM-Objekte verlassen den DB-Thread nicht.
Bei SMS bleibt die urspruengliche Ausnahme fuer das bestehende Fehlerprotokoll auf
dem Ursprungsloop; nur ihr Klassenname geht in die Ergebnisverbuchung im Thread.

## Bewusst zurueckgestellt

- DIBOS-Anreicherung (`dibos_enrich` mit Incident-Lookups, Writes, Geocoding,
  Deduplizierung, Reclose-Markern und Alarmierungs-Tasks): L3. Der Intake-Loop
  behaelt Fast Path, Reihenfolge, Fingerprints und Create-/Enrich-Trigger.
- Diagnose-Capture-Starts fuer LIS/DIBOS, manueller LIS-Fahrzeugstatus-Push und
  Ereignis-Tasks wie Objekt-Matching, Alarm-Outbox/Versand und Autoprint: L2/L3
  gemaess Plan. ID-basierte Tasks bleiben auf dem urspruenglichen Loop und erhalten
  keine Session des LIS-Sync.
- Weitere **53 Baseline-Eintraege**: L2/L3. Der AST-Waechter erfasst Session-Erzeugung;
  indirekte DB-Zugriffe werden zusaetzlich durch Laufzeitpruefungen abgesichert.

## Abschliessende Pruefungen

Die jeweils letzten Laeufe der 64 gezielt ausgewaehlten Testdateien ergeben
**1257 passed, 11 skipped, 0 failed**. Wiederholte Entwicklungslaeufe sind
nicht doppelt summiert. Die 11 Browser-Tests ueberspringen sich ohne
`ECP_BROWSER_TESTS=1` und `ECP_CHROMIUM` regulaer. Alle pytest-Prozesse liefen
nacheinander, jeder mit genau einer Testdatei und `-p no:cacheprovider`; keine
Gesamtsuite und kein paralleles pytest.

Die Dateiauswahl folgt dem Grep nach den angefragten Modulen, allen LIS-/DIBOS-/
Atemschutz-Testdateien sowie den betroffenen Mail-, WordPress- und Routing-Tests.
Die DB-Tests verwenden SQLite; ein MariaDB-Lasttest war nicht Teil dieser Pruefung.

| Exakter Befehl | Letztes Ergebnis |
|---|---|
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/browser/test_breathing_browser.py` | SKIP (Exit 0): 11 skipped, 50 warnings in 0.32s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_abfluss_service.py` | PASS (Exit 0): 5 passed, 51 warnings in 7.89s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_admin_mcp_tools.py` | PASS (Exit 0): 19 passed, 51 warnings in 15.85s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_alarm_outbox.py` | PASS (Exit 0): 28 passed, 51 warnings in 11.95s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_alarm_outbox_event_key.py` | PASS (Exit 0): 14 passed, 51 warnings in 7.60s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_async_services_sync_db.py` | PASS (Exit 0): 3 passed, 51 warnings in 4.19s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_atemschutz_pruefung_form_str.py` | PASS (Exit 0): 4 passed, 51 warnings in 8.82s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_atemschutz_pruefung_formular.py` | PASS (Exit 0): 6 passed, 51 warnings in 10.20s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_background_loop_threads.py` | PASS (Exit 0): 25 passed, 53 warnings in 2.89s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing.py` | PASS (Exit 0): 131 passed, 51 warnings in 4.36s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_alertbar.py` | PASS (Exit 0): 7 passed, 51 warnings in 4.28s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_board_html.py` | PASS (Exit 0): 63 passed, 51 warnings in 8.23s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_cleanup.py` | PASS (Exit 0): 1 passed, 51 warnings in 2.46s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_dialog.py` | PASS (Exit 0): 4 passed, 51 warnings in 4.20s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_hinweise.py` | PASS (Exit 0): 23 passed, 51 warnings in 2.75s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_karte.py` | PASS (Exit 0): 25 passed, 51 warnings in 4.88s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_notify.py` | PASS (Exit 0): 48 passed, 51 warnings in 3.88s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_offline.py` | PASS (Exit 0): 21 passed, 51 warnings in 5.35s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_phase1_model.py` | PASS (Exit 0): 8 passed, 51 warnings in 2.50s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_phase1_service.py` | PASS (Exit 0): 69 passed, 51 warnings in 2.46s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_phase2_model.py` | PASS (Exit 0): 7 passed, 51 warnings in 4.15s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_routes.py` | PASS (Exit 0): 24 passed, 51 warnings in 5.18s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_routes_phase1.py` | PASS (Exit 0): 42 passed, 51 warnings in 7.05s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_routes_phase3.py` | PASS (Exit 0): 55 passed, 51 warnings in 7.60s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_sitzung.py` | PASS (Exit 0): 66 passed, 51 warnings in 6.17s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_system_toggle.py` | PASS (Exit 0): 2 passed, 51 warnings in 3.75s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_breathing_timeline.py` | PASS (Exit 0): 8 passed, 51 warnings in 2.30s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_admin_routes.py` | PASS (Exit 0): 7 passed, 51 warnings in 4.05s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_capture.py` | PASS (Exit 0): 20 passed, 51 warnings in 2.51s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_client.py` | PASS (Exit 0): 16 passed, 51 warnings in 2.38s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_create_incidents.py` | PASS (Exit 0): 15 passed, 51 warnings in 3.26s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_enrich.py` | PASS (Exit 0): 28 passed, 51 warnings in 3.02s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_fallback.py` | PASS (Exit 0): 13 passed, 51 warnings in 4.38s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_loop.py` | PASS (Exit 0): 20 passed, 51 warnings in 2.57s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dibos_wache_status.py` | PASS (Exit 0): 10 passed, 51 warnings in 2.33s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_dienst_monitor.py` | PASS (Exit 0): 27 passed, 51 warnings in 3.89s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_einheit_kartenziele.py` | PASS (Exit 0): 10 passed, 51 warnings in 4.67s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_gateway_alarmempfang.py` | PASS (Exit 0): 21 passed, 51 warnings in 6.44s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_gateway_offline_alerts.py` | PASS (Exit 0): 12 passed, 51 warnings in 4.63s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_gateway_rename.py` | PASS (Exit 0): 7 passed, 51 warnings in 4.63s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_incident_duplicate_guard.py` | PASS (Exit 0): 7 passed, 51 warnings in 2.68s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_incident_reopen_lock.py` | PASS (Exit 0): 5 passed, 51 warnings in 4.05s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_incident_route_loop.py` | PASS (Exit 0): 7 passed, 61 warnings in 4.21s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_incident_vehicle_dispatch.py` | PASS (Exit 0): 11 passed, 51 warnings in 2.70s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_capture.py` | PASS (Exit 0): 16 passed, 51 warnings in 2.36s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_client.py` | PASS (Exit 0): 16 passed, 51 warnings in 2.30s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_geo.py` | PASS (Exit 0): 3 passed, 51 warnings in 2.35s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_health.py` | PASS (Exit 0): 2 passed, 51 warnings in 2.31s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_loop.py` | PASS (Exit 0): 2 passed, 51 warnings in 2.30s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_matching.py` | PASS (Exit 0): 28 passed, 51 warnings in 2.37s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_status_route.py` | PASS (Exit 0): 1 passed, 51 warnings in 3.87s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_lis_sync.py` | PASS (Exit 0): 55 passed, 51 warnings in 9.07s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_mail_deliver.py` | PASS (Exit 0): 15 passed, 66 warnings in 5.50s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_o365_mail_service.py` | PASS (Exit 0): 12 passed, 51 warnings in 2.31s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_probenplanung_uebungseinsatz.py` | PASS (Exit 0): 9 passed, 51 warnings in 5.59s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_public_tenant_isolation.py` | PASS (Exit 0): 41 passed, 51 warnings in 8.16s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_resend_mail_service.py` | PASS (Exit 0): 10 passed, 51 warnings in 2.30s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_road_closure_incident_service.py` | PASS (Exit 0): 12 passed, 105 warnings in 2.62s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_system_diagnose.py` | PASS (Exit 0): 32 passed, 51 warnings in 4.82s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_systemstatus_nav.py` | PASS (Exit 0): 10 passed, 51 warnings in 4.50s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_uebung_side_effects.py` | PASS (Exit 0): 26 passed, 73 warnings in 8.03s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_wordpress_report_archive.py` | PASS (Exit 0): 3 passed, 51 warnings in 3.88s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_wordpress_report_service.py` | PASS (Exit 0): 12 passed, 51 warnings in 2.60s |
| `/home/johannes/projects/einsatzcockpit/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_worker_monitor.py` | PASS (Exit 0): 8 passed, 51 warnings in 4.09s |

Weitere exakte Befehle und Ergebnisse:

```bash
/home/johannes/projects/einsatzcockpit/.venv/bin/python -m ruff check app/services/loop_thread_io.py app/services/lis/lis_loop.py app/services/lis/lis_sync.py app/services/dibos/dibos_loop.py app/services/incident_route_loop.py app/services/road_closure_incident_service.py app/services/breathing_notify.py app/services/dienst_monitor_loop.py app/services/dienst_monitor_dispatch.py app/services/incident_notify.py app/services/incident_live_notify.py app/services/mail_service.py app/services/o365_mail_service.py app/services/wordpress_report_service.py tests/test_async_services_sync_db.py tests/test_background_loop_threads.py tests/test_breathing_notify.py
```

PASS (Exit 0): `All checks passed!`

```bash
/home/johannes/projects/einsatzcockpit/.venv/bin/python -m mypy app/services/loop_thread_io.py app/services/lis/lis_loop.py app/services/lis/lis_sync.py app/services/dibos/dibos_loop.py app/services/incident_route_loop.py app/services/road_closure_incident_service.py app/services/breathing_notify.py app/services/dienst_monitor_loop.py app/services/dienst_monitor_dispatch.py app/services/incident_notify.py app/services/incident_live_notify.py app/services/mail_service.py app/services/o365_mail_service.py app/services/wordpress_report_service.py --ignore-missing-imports
```

PASS (Exit 0): `Success: no issues found in 14 source files`. Nur bestehende Hinweise zu ungeprueften untypisierten Funktionskoerpern in importierten Dateien.

```bash
git diff --check
```

PASS (Exit 0), keine Ausgabe. Kein `ruff format`, kein `ruff --fix`, kein Commit.
