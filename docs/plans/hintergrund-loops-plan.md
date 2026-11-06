# Hintergrund-Loops ohne Event-Loop-Blockade + Sysadmin-Ueberwachung

Stand: 2026-11-06. Anlass: Prod (vm1, Standard_B2ls_v2, 4 GB, Swap voll) hatte am 06.11.
16 `WORKER TIMEOUT` in 24 h. Ein Timeout bedeutet: die Event-Loop eines Workers stand
mindestens ~60 s (uvicorn meldet sich alle `timeout/2` beim Master). Ursache ist eine Kette:

1. VM-weite Stillstaende (Swap/IO-Druck, ggf. CPU-Credits) machen einfache Queries 1-15 s langsam.
2. Viele Hintergrund-Loops und Hintergrund-Tasks fuehren synchrone SQLAlchemy-Abfragen direkt in
   `async def` aus (AST-Scan: 82 Stellen in `app/services`, z. B. `lis_loop._sync_one_org`,
   `dibos_loop._check_org`, `incident_route_loop`). Jede langsame Query blockiert damit die ganze
   Loop des Workers: HTTP, WebSockets, Heartbeat.
3. Bisher sah die Sysadmin-Ueberwachung davon nichts: Worker-Abschuesse, Loop-Verzug, Speicher
   und Swap tauchten weder in der Systemdiagnose noch im Admin-MCP auf.

Bereits erledigt: #898 (Session-Middleware), #899 (MCP), #900 (Logging, Stackdump, Speicher).

## Muster fuer Loop-Umbauten

- DB-Arbeit in eine synchrone Funktion `def _..._sync(...)` ziehen, die ihre eigene Session
  oeffnet und schliesst (`with SessionLocal() as db:` + `set_tenant_context` wie bisher).
- Aus der Async-Funktion nur `await asyncio.to_thread(_..._sync, ...)` aufrufen.
- Ueber die Thread-Grenze nur einfache Werte (IDs, Dicts, Dataclasses) geben, nie ORM-Objekte
  oder Sessions; Async-I/O (httpx, WebSocket-Broadcast, ws_bus) bleibt in der Event-Loop.
- Reihenfolge, Commits, Sperren (`FOR UPDATE`, Claims) und Fehlerbehandlung bleiben
  inhaltlich gleich; je Umbau ein Test, der die Thread-Ausfuehrung belegt.
- Waechter `tests/test_async_services_sync_db.py`: AST-Scan auf `SessionLocal()` in
  `async def` unter `app/services`; Baseline `tests/data/async_services_sync_db_baseline.txt`
  darf nur schrumpfen.

## Phasen

| Phase | Inhalt | Status |
|---|---|---|
| U1 | Ueberwachung: Prozess-/Host-Metriken im Worker-Heartbeat, unsaubere Worker-Neustarts, Loop-Verzug, Speicher/Swap/PSI in Systemdiagnose und `admin_worker_pruefen` | umgesetzt |
| L1 | Waechter + Polling-Loops: `lis_loop`, `dibos_loop`, `incident_route_loop`, `abfluss_poll_loop`, `dienst_monitor_loop`, Atemschutz-Watchdog/-Notify | umgesetzt |
| L2 | Outbox/Dispatch/Erinnerungen: `alarm_outbox`, `api_message_dispatch_loop`, `mailing_dispatch_loop`, `road_closure_notification_loop`, `task_reminder`, `probe_erinnerung`, `gk_zugang_service`, `resend_inbound_service`, Mail-Poller/-Zuordnung/-Weiterleitung, Wetterwarnungen | umgesetzt |
| L3 | Ereignis-Tasks im Alarmpfad: `print_dispatcher.autoprint_*`, `dibos_enrich`, `incident_notify`, `alarm_apply_service`, `objekt_*`, `teams_alarm_service`, Rest der Baseline | offen |

Infrastruktur (ausserhalb des Codes): VM mit 8 GB ohne Burst-Grenze (z. B. D2as_v5),
`MALLOC_ARENA_MAX=2`, Prod 2 Worker, `vm.swappiness=10`, MaxScale stoppen.

## L1 umgesetzt (2026-11-06)

- Der AST-Waechter erfasst `SessionLocal(...)` als Name und Attribut und trennt
  verschachtelte synchrone und asynchrone Funktionskoerper. Die sortierte Baseline
  enthaelt nach L1 **53 Eintraege** (vorher bei korrekter Abgrenzung: 60). Neue sowie ohne Baseline-Anpassung entfernte
  Eintraege lassen den Test fehlschlagen.
- Org-Listen in LIS/DIBOS/Abfluss/Dienstmonitor und der Atemschutz-Watchdog waren
  bereits ausgelagert. Neue Tests sichern auch diese bestehenden Thread-Grenzen.
- LIS-Konfiguration und die gesamte Sync-Transaktion (inklusive DB-Anteilen in
  `lis_sync.sync_organization`) laufen jetzt im DB-Thread. Routenberechnung und
  Dienstmonitor halten ebenfalls ihre bisherige Session und Commit-Reihenfolge
  innerhalb eines synchronen Thread-Aufrufs. Fuer diese verzahnten Bestandsablaeufe
  fuehrt `loop_thread_io.thread_ablauf` die bestehende Coroutine auf einem
  kurzlebigen Worker-Loop aus. `auf_event_loop` reicht ausschliesslich Async-I/O
  mit Nutzdaten an den urspruenglichen Loop zurueck: LIS-HTTP, Routing-HTTP,
  Broadcasts, Mail-Transport und SMS. Sessions und ORM-Objekte bleiben im DB-Thread.
  Der LIS-Thread erhaelt nur IDs und einen Client-Schluessel; auch Client-Attribute
  werden auf dem urspruenglichen Loop gelesen/geschrieben. Es werden keine
  zusaetzlichen Konfigurations-Queries gegenueber dem bisherigen Ablauf benoetigt.
  Bei Abbruch wird die Worker-Coroutine abgebrochen und das Session-Ende abgewartet.
- DIBOS laedt die Konfiguration als einfache Werte im Thread; auch Probe-Schreibzugriffe
  laufen dort. Fast Path, Reihenfolge, Fingerprints, Deduplizierung, Reclose-Marker und
  Create-/Enrich-Aufrufe bleiben unveraendert.
- Atemschutz-Notify trennt faellige IDs, Einzel-Claim/Pruefung und SMS-Ergebnisverbuchung
  in Session-eigene Thread-Aufrufe. Push und sein Zustellungsstatus bleiben in derselben
  Transaktion; SMS bleibt async, einschliesslich unveraenderter Lease-, Eskalations-
  und Wiederholungsfristen. Die SMS-Konfiguration wird ebenfalls im Thread geladen.
  Bei SMS-Fehlern geht nur der Klassenname in die DB-Verbuchung; das bestehende
  Fehlerprotokoll verwendet die urspruengliche Exception auf dem aufrufenden Loop.

Bewusst offen fuer L2/L3:

- `dibos_enrich.enrich_and_broadcast` und seine verzahnten DB-/HTTP-/Alarm-/Geocoding-
  Ablaeufe bleiben L3. Insbesondere sind die dortigen Incident-Lookups und Writes,
  Deduplizierung und Reclose-Marker in L1 nicht umgebaut.
- Diagnose-Capture-Starts (`lis_capture.start_capture_for_org`,
  `dibos_capture.start_trace_for_org`), der manuelle LIS-Fahrzeugstatus-Push und
  Ereignis-Tasks (Objekt-Matching, Alarm-Outbox/Versand, Autoprint) behalten ihre
  eigenen Sessions und bleiben den Folgephasen zugeordnet. Solche Tasks werden
  weiterhin auf dem urspruenglichen Loop gestartet; keine LIS-Session wird uebergeben.
- Die uebrigen 53 Baseline-Eintraege werden in L2/L3 abgebaut. Der Waechter erkennt
  Session-Erzeugung, keine indirekten Zugriffe mit bereits uebergebenen Sessions;
  deshalb pruefen Laufzeittests zusaetzlich Thread und Loop der L1-Pfade.

Pruefprotokoll mit Dateizusammenfassung, exakten Befehlen und Ergebnissen:
[hintergrund-loops-l1-pruefung.md](hintergrund-loops-l1-pruefung.md).

Nachtrag Review: `im_db_thread` laeuft in einem eigenen Pool (`hintergrund-db`, 4 Threads).
Die Ablaeufe halten ihren Thread ueber HTTP-Wartezeiten; im asyncio-Standardpool
(6 Threads auf 2 CPUs) haetten sie die Session-Pruefung jeder Anfrage (#898) ausgehungert.


## L2 umgesetzt (2026-11-06)

- Die Baseline schrumpft von **53 auf 34 Eintraege**. Alle 19 angefragten
  periodischen/Dispatch-Eintraege einschliesslich `weather_alert_loop._process_org`
  und `alarm_outbox.process_incident_alarm` sind entfernt. `probe_erinnerung`
  hatte bereits keine eigene synchrone Session im Async-Loop.
- Einfache Session-eigene Sync-Helfer ueber `im_db_thread` uebernehmen Alarm-Listen,
  Alarm-Claims und Recovery-Abschluss, Strassensperren-Claims/Payloads/Ergebnis,
  Mailing-Claim samt Sperrliste und Versandvorbereitung/Verbuchung/Abgleich,
  Mail-Poller-Limits und Fehlerstatus, Zuordnungs-Claims sowie GK- und
  Resend-Aufraeumtransaktionen. Ueber die Grenze gehen nur Nutzdaten und IDs.
  Die Mailing-Anhangsdaten bleiben pro Batch gecacht; der Kampagnenabgleich
  benutzt weiterhin eine frische Session und `FOR UPDATE`.
- Nur die verzahnten Bestandsablaeufe laufen mit `thread_ablauf`: der beanspruchte
  Alarmauftrag (Kanalpruefung, Versand, Generation-Pruefung, Status und Abschluss),
  API-Dispatch (SMS-Fortschrittscallbacks und mehrere Commits), MIME-Speicherung
  samt Zuordnung/Anhaengen/Cursor, nachtraegliche Mail-Zuordnung, Weiterleitung
  mit Download und Versand sowie Wetter-Regelauswertung und Versandprotokoll.
  Auch die Erinnerungs-Versandhelfer halten ihre Session fuer die bestehenden
  konfigurationsabhaengigen Mail-Fallbacks im Thread. Die DB-Threads verwenden
  den eigenen `hintergrund-db`-Pool; das Session-Ende wird bei Abbruch abgewartet.
- Async-Transport und gemeinsam benutzte Loop-Zustaende bleiben auf dem
  aufrufenden Loop: Teams-HTTP, SMTP/Resend/Graph, SMS-Transport, Gateway-Liste,
  Wetteranbieter, Mail-Anhangsdownloads und Meldungs-Broadcasts. Die vorhandenen
  Mail-Fallback-Bruecken aus L1 werden weiterverwendet. Der synchrone
  `imaplib`-Abruf lief bereits in `asyncio.to_thread`; das bleibt einschliesslich
  Login, lesendem SELECT, UID-Abruf und Logout so.
- Die Wetter-Orgliste liefert jetzt IDs statt abgeloester ORM-Objekte; die
  Konfiguration wird im Session-besitzenden Thread gelesen. Explizit uebergebene
  Sessions bei `dispatch_once(db=...)` und `run_mail_zuordnung_once(db=...)`
  bleiben beim Aufrufer. Die periodischen Loops verwenden ausschliesslich die
  Session-eigenen Thread-Pfade; keine Aufrufer-Session wird in einen Thread gegeben.
- Neue Laufzeittests pruefen Session-Erzeugung, Queries, Flush, Commit und
  Schliessen ausserhalb des aufrufenden Threads sowie Transport auf dem
  urspruenglichen Event-Loop. Die bestehenden Alarm-Fencing-Tests synchronisieren
  ihre Sender-Attrappen jetzt explizit ueber diesen Loop.

Bewusst offen fuer L3:

- `resend_inbound_service.fetch_inbound_background`: kein einfacher DB-/HTTP-Split,
  da der Ereignispfad Abruf, Fehlerstatus, Zuordnung, Anhaenge und Autodruck mit
  eigener Session verzahnt. Die taegliche Retention ist in L2 umgestellt.
- Die eigenstaendigen Ereignis-Sender `sms_dispatch_service.dispatch_einsatzinfo`,
  `einsatzinfo_mail_service.dispatch_einsatzinfo_mail`, Push-Helfer aus
  `incident_notify` und `print_dispatcher.autoprint_mail_eingang_background`
  bleiben L3. Sie erhalten vom DB-Thread nur IDs/Nutzdaten und starten weiterhin
  auf dem urspruenglichen Loop; ihre eigenen DB-Zugriffe sind damit noch nicht
  insgesamt ausgelagert. Insbesondere wurde der SMS-/Mail-ALARM-Ereignispfad
  nicht nebenbei umgebaut. Teams-Konfiguration und Outbox-Verbuchung selbst
  laufen dagegen bereits im Thread; nur der HTTP-Payload geht zurueck.
