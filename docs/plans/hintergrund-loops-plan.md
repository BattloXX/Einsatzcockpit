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
| L1 | Waechter + Polling-Loops: `lis_loop`, `dibos_loop`, `incident_route_loop`, `abfluss_poll_loop`, `dienst_monitor_loop`, Atemschutz-Watchdog/-Notify | offen |
| L2 | Outbox/Dispatch/Erinnerungen: `alarm_outbox`, `api_message_dispatch_loop`, `mailing_dispatch_loop`, `road_closure_notification_loop`, `task_reminder`, `probe_erinnerung`, `gk_zugang_service`, `resend_inbound_service`, Mail-Poller/-Zuordnung/-Weiterleitung | offen |
| L3 | Ereignis-Tasks im Alarmpfad: `print_dispatcher.autoprint_*`, `dibos_enrich`, `incident_notify`, `alarm_apply_service`, `objekt_*`, `teams_alarm_service`, Rest der Baseline | offen |

Infrastruktur (ausserhalb des Codes): VM mit 8 GB ohne Burst-Grenze (z. B. D2as_v5),
`MALLOC_ARENA_MAX=2`, Prod 2 Worker, `vm.swappiness=10`, MaxScale stoppen.
