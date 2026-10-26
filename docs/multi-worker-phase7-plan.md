# Multi-Worker Phase 7: Restbefunde vor 2–4 Workern (Plan für Codex)

Stand: 26.10.2026. Grundlage: Review von Cloud `feat/multi-worker-phase6` (`246a8641`,
enthält Phasen 2–6), Android `feat/sms-outbox-phase3` (`e5f11a3`), Linux-Gateway
`feat/multi-worker-phase4` (`b34b578`), Windows-Agent `feat/multi-worker-phase4` (`965c5cd`).

Die Gateway-, SMS- und Druckpfade (Presence, adressierte Queue, Claims, Lease/Generation,
Intents) sind sauber gefenced. Die Befunde unten betreffen vor allem **prozesslokalen
Zustand außerhalb dieser Pfade**, der bei mehreren Workern still falsches Verhalten erzeugt,
sowie Kapazitäts- und Client-Punkte. Jede Teilaufgabe = eigener Commit mit Regressionstest.

Allgemeine Regeln für alle Pakete:
- Zustand, den ein Request auf Worker A schreibt und ein Request/Loop auf Worker B liest,
  gehört in DB oder Redis (mit TTL), nie in ein Modul-Dict.
- Ohne `REDIS_URL` (Dev, `-w 1`) muss alles wie bisher funktionieren.
- Tests: Zwei-Prozess-Varianten nach dem Muster `tests/worker_probe_app.py` /
  `e2e/multi_worker`, wo es um Workergrenzen geht; sonst Unit-Tests, die zwei
  "Worker" durch getrennte Modulzustände simulieren (Dict leeren zwischen Schreiben/Lesen).
- Keine neuen Ruff-Baseline-Einträge; `ruff`, `mypy app/` (vollständig!), `pytest -n auto`.

---

## P0 – Blocker (vor jedem Test mit 2 Workern)

### P0.1 mypy auf dem Branch rot
`mypy app/ --ignore-missing-imports` meldet 2 Fehler in
`app/services/verleih_erinnerung.py:52` und `:64` (Optional-Typen; Phase-6-Doku prüfte nur
9 Module). CI würde scheitern. Fix: lokale Variablen mit `assert`/Guard
(`erinnerung_geplant_at`, `org_id`), kein `type: ignore`.

### P0.2 Live-Events gehen bei Redis-Ausfall komplett verloren
`app/services/broadcast.py` `ConnectionManager.broadcast()/broadcast_all()`: Ist
`REDIS_URL` gesetzt, wird **nur** publiziert. Liefert `ws_bus.publish()` `False`
(Redis weg, keine Subscriber), wird das Event an niemanden zugestellt – auch nicht an
Browser im eigenen Worker. Betrifft Board, Lage, Org-Broadcasts, Alarmanzeigen.
- Fix: Rückgabewert von `publish()` auswerten; bei `False` sofort `_deliver_local(...)`
  bzw. `_deliver_all_local(...)`. Gilt analog für `broadcast_lage/_einheit/_org`.
- Clients: nach WS-Reconnect wird bereits nachgeladen (Phase-6-Szenario 10). Zusätzlich
  prüfen, dass Board/Lage bei `ws_bus`-Ausfall einen Hinweis/Resync bekommen
  (z. B. Event `bus_degraded` → Clients laden bei Wiederkehr einmal neu). Mindestens
  dokumentieren, dass Browser auf anderen Workern im Ausfall nur per Reload aktuell werden.
- Test: Redis konfiguriert, `_redis=None` → lokaler Socket erhält Event genau einmal;
  Redis ok → genau einmal (kein Doppel durch lokal + Echo).

### P0.3 DB-Verbindungsbudget bei 3–4 Workern
`app/db.py`: `pool_size=10, max_overflow=30` **pro Prozess** → 4 Worker = bis zu 160
Verbindungen (+ Wetter-DB, Alembic, Admin). MariaDB-Default `max_connections=151`;
`docker-compose.yml` setzt nichts. Zudem lastet `sms_outbox.run_once()` pro Worker bis zu
100 parallele `drive()` über `asyncio.to_thread` auf Pool und Default-Executor.
- Fix: `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` als Settings (Default unverändert für `-w 1`);
  `gunicorn.conf.py`-Vorschlag dokumentiert empfohlene Werte je Workerzahl
  (z. B. 2 Worker: 10/20, 4 Worker: 8/12) und prüft beim Start
  `workers × (pool+overflow) + Reserve ≤ max_connections` (Warnung im Log + Systemstatus).
- `sms_outbox.run_once()`: Parallelität mit `asyncio.Semaphore` (z. B. 8) begrenzen;
  nur Jobs mit `next_attempt_at <= now` bzw. abgelaufener Lease laden statt aller
  nicht-terminalen Jobs jede Sekunde.
- Rollout-Doku: `max_connections` am Zielhost prüfen/erhöhen; Compose-Vorschlag mit
  `--max-connections=300` für MariaDB ergänzen.

### P0.4 18 Regressionen in der vollen Testsuite
`pytest -q -n auto` auf `246a8641`: **18 failed**, 6359 passed (main `748c3565`: dieselben
Dateien grün). Isoliert reproduzierbar. Gruppen:
1. **Loop-Registrierung (6)**: `test_incident_route_loop`, `test_mail_graph_poller`,
   `test_mail_imap_poller`, `test_mail_weiterleitung`, `test_mail_zuordnung_loop`,
   `test_planspiel_loop` suchen `'xxx_loop(),'` im Quelltext von `main.py`; die Loops
   stehen jetzt als Funktionsreferenzen im Tupel. Tests auf eine echte Prüfung umstellen
   (z. B. `_start_background_loops` mit gepatchtem `supervised_loop` aufrufen und Namen
   der erzeugten Tasks prüfen), nicht auf String-Suche.
2. **Event-Loop-Leck in `gateway_routing`/`sms_outbox`/`linux_print_service` (2+)**:
   `test_feed_fundament`, `test_foerderstrecke_pr6`: `RuntimeError … attached to a different
   loop` in `gateway_routing._poll` – das Modul-Global `_task` überlebt einen
   TestClient-Lifespan und wird im nächsten Loop awaited. Fix: Task zusammen mit seinem
   Loop speichern bzw. im `app.state` statt Modul-Global halten; `stop()` nur Tasks des
   laufenden Loops awaiten. Gleiches Muster in `sms_outbox`/`linux_print_service` prüfen.
3. **Dokumentdruck (4)** `test_dokumentdruck.py`: Station meldet 409 „Drucker oder
   Druckdienst offline“, bzw. Job bleibt `queued` statt `sent`. **Zuerst klären, ob echte
   Regression** (Bereitschaftsprüfung liest noch alte Presence-Felder bzw. verlangt die neue
   `agent_connection_id`/`agent_presence_expires_at`) oder nur fehlende Presence im
   Test-Fixture (`tests/gateway_helpers.py` verwenden). Bei Regression Produktivcode fixen.
4. **Alarmierung (3)** `test_push_alarm_channel.py`: Log-Text jetzt „SMS suppressed“
   statt „uebersprungen“ (vereinheitlichen, deutsch wie Rest); `MultipleResultsFound` in
   `test_f30_wird_nie_alarmiert` (Query mit `.one()` trifft jetzt mehrere Zeilen –
   Ursache prüfen, nicht nur `.first()`); `IncidentAlarmJob.last_error` enthält nur noch
   `RuntimeError` statt Meldung – für Admin-Diagnose `"<Typ>: <gekürzte, redigierte
   Meldung>"` speichern, Test beibehalten.
5. **Org-Export** `test_org_export_service`: neue Tabelle `worker_runtime` als System-/
   nicht exportierte Tabelle klassifizieren.
6. **CSS** `test_css_klassen_definiert`: `alert--danger` in
   `admin/_systemstatus_liste.html` ist nicht definiert → vorhandene Modifier-Klasse
   verwenden oder in CSS ergänzen.

---

## P1 – Funktionsfehler durch prozesslokalen Zustand

### P1.1 Bürgerportal-SMS-Verifizierung (`ui_major_incident.py`)
`_pending_verifications` (Modul-Dict, Zeile ~123) hält PIN, Formulardaten und Ablauf.
Absenden auf Worker A, PIN-Eingabe auf Worker B → „abgelaufen/ungültig“, Bürgermeldung
geht verloren.
- Fix: neue Tabelle `citizen_report_verification` (token_hash PK, org_id, lage_id,
  pin_hash, phone, form_data JSON, photo_fn, attempts, expires_at, created_at) mit
  Migration `0316`; atomarer Verbrauch per `UPDATE … WHERE token_hash=? AND expires_at>now
  AND consumed_at IS NULL`. Versuchszähler (max. 5) gegen PIN-Raten.
- Nebenbei: PIN mit `secrets.randbelow(10000)` statt `random.randint`.
- Cleanup abgelaufener Zeilen im bestehenden Retention-Loop (Leader).
- Test: Erzeugen und Bestätigen über getrennt geleerte Prozesszustände; doppelte
  Bestätigung legt genau eine Meldung an.

### P1.2 LIS-Status-Rückschreibung (`app/services/lis/lis_sync.py`)
`_lokal_gepushter_status` wird im Request-Worker (BackgroundTask aus
`ui_incident.py:3050/3805`) geschrieben, aber
a) im LIS-Poll-Loop (nur Leader) für `_lokal_status_geschuetzt()` gelesen → Schutzfenster
   greift nur, wenn der Request zufällig auf dem Leader lief → Status springt zurück;
b) in `push_vehicle_status_to_lis()` zur Dedup genutzt → Folge A(W1) → B(W2) → A(W1)
   wird auf W1 als „unverändert“ übersprungen, **LIS bleibt auf B**.
- Fix: Schutzfenster/letzten Push in der DB (Spalten `lis_pushed_status`,
  `lis_pushed_at` an `IncidentVehicle`, Migration `0316`) oder Redis-Key mit TTL
  `_PUSH_SCHUTZFENSTER_S`. Dedup nur gegen den gemeinsam gespeicherten Wert.
  Zeitbasis UTC-Wanduhr statt `time.monotonic()` (monotonic ist prozesslokal).
- Test: Push auf „Worker A“, Dict leeren, Poll-Abgleich auf „Worker B“ respektiert
  Schutzfenster; Sequenz A→B→A erzeugt drei Pushes.

### P1.3 Lagedokument (Yjs) – Datenverlust bei Raum-Erstellung auf zweitem Worker
`app/services/lagedokument_collab.py`: Rooms sind pro Worker; Updates werden per Redis
weitergereicht, aber ein Room, der auf Worker B neu entsteht, lädt nur den DB-Snapshot
(bis zu 10 s alt). Änderungen von A aus diesem Fenster fehlen B dauerhaft, und B
überschreibt beim nächsten Debounce-Save `ydoc_state` mit seinem unvollständigen Stand
(Last-Writer-Wins auf Vollsnapshot). Ebenso nach Redis-Unterbrechung.
- Fix (beides):
  1. **Merge-on-save**: `_save_ydoc_state` liest den DB-Stand `SELECT … FOR UPDATE`,
     wendet ihn und den Room-Stand auf ein frisches `Doc` an und speichert das Merge-Ergebnis
     (CRDT-Merge ist idempotent). Danach den gemergten DB-Stand auch auf den Room anwenden.
  2. **Join-Sync über den Bus**: beim Erzeugen eines Rooms `CH_LAGEDOKUMENT` mit
     `{"lage_id", "sync_request": state_vector}` publizieren; Worker mit offenem Room
     antworten mit `get_update(state_vector)`. Gleiches nach Bus-Reconnect für alle Rooms.
- Test: zwei Rooms in getrennten Modulzuständen, Edit in A ohne Save, Room B neu,
  B speichert → DB enthält A-Edit; Join-Sync liefert fehlende Updates.

### P1.4 Lageführung-Presence und Soft-Locks (`app/routers/ws.py` `_lft_presence/_lft_locks`)
`lagefuehrung.presence.changed` sendet über Redis die **lokale** Teilliste → Browser auf
anderen Workern sehen abwechselnd unterschiedliche Teilnehmer; Soft-Locks wirken nur
im eigenen Worker (fachlich abgesichert über version/409, aber UI irreführend).
- Fix: Presence und Locks in Redis (Hash `lft:{incident}:presence` mit
  connection_id → user, TTL über Heartbeat; Lock per `SET NX PX 15000`), Broadcast
  der vollständigen Liste aus Redis. Ohne Redis wie bisher lokal.
- Test: zwei Prozesszustände, beide joinen → Liste enthält beide; Lock von A blockiert B.

### P1.5 Planspiel-OSM-Import-Status (`ui_planspiel_admin.py` `_osm_importe`)
Start auf A, Status-Poll auf B → leeres Ergebnis / „läuft nicht“; zweiter Start auf B
möglich → doppelter Import.
- Fix: Status in DB (bestehende Planspiel-Settings oder kleine Tabelle) mit atomarem
  Start (`UPDATE … WHERE laeuft = false`) und Ablaufzeit gegen hängende Einträge nach
  Worker-Absturz. Optional Import als Leader-Job ausführen.

### P1.6 Diagnose-Aufzeichnungen DIBOS/LIS (`dibos_capture._active_traces`, `lis_capture._active_captures`)
Status/Abbruch nur auf dem startenden Worker möglich; zweiter Start auf anderem Worker
startet parallele Aufzeichnung.
- Fix: laufende Aufzeichnung als DB-/Redis-Eintrag (org_id, run_id, worker_id,
  expires_at); `cancel` per Bus-Nachricht an den Owner-Worker. Niedrige Priorität,
  aber klein.

### P1.7 Kleinere UI-Inkonsistenzen
- `ws._passthrough_status` (Serial-Fan-Out-Status) nur im Socket-Worker sichtbar →
  in `Gateway`-Zeile (JSON-Spalte) oder Redis-Key mit TTL ablegen.
- `abfluss_service._store` (HQ-Werte) wird vom Leader-Loop gefüllt, von
  `ui_weather.py:1458` gelesen → auf Nicht-Leadern fehlen HQ-Stufen bis zum ersten
  UI-Refresh. HQ-Werte über `shared_cache` (Redis) teilen oder aus der DB laden.
- `weather_station_service._station_history` nur Fallback ohne Wetter-DB → dokumentieren.
- `log_buffer` (Admin-Logansicht) zeigt nur den bedienenden Worker → Hinweis
  „Worker X von N“ in der Ansicht; optional Worker-Auswahl über `worker_runtime`.

---

## P2 – Robustheit, Last, Aufräumen (Cloud)

### P2.1 Start-Races
`_bootstrap_admin()` (`_upsert_roles`, Seed) und `init_weather_db()` laufen in jedem
Worker gleichzeitig. Bei Erststart Duplicate-Key, sonst mögliche Deadlocks → Worker
crasht beim Boot.
- Fix: unter MariaDB `GET_LOCK('ecp_bootstrap', 30)` (SQLite: no-op) oder nur im Worker
  mit Leader-Lock; Idempotenz-Test mit zwei parallelen Aufrufen.

### P2.2 SMS-Erstzustellung schneller umleiten
Ein an eine halb-offene Verbindung gesendetes `sms.prepare/send` wird erst nach
`LEASE_S = 60` als „nicht versendet“ erkannt (kein Empfangs-ACK) und dann umgeleitet.
Für Alarme sind das 60 s Verzögerung; Presence-TTL ist zusätzlich 90 s.
- Fix: eigene `RECEIPT_TIMEOUT_S` (z. B. 10 s) für `dispatched` ohne `received_at`
  → sicher (`may_have_sent=False`) zurück auf `queued` mit Ausschluss dieses Gateways
  für diese Generation. Presence-TTL/Heartbeat für SMS prüfen (Android-Heartbeat-Intervall
  abstimmen, Ziel TTL ≤ 45 s). Phase-6-Szenario 3 entsprechend erweitern.

### P2.3 Projektionsabfrage jede Sekunde in jedem Worker
`sms_outbox.run_once()` joint `sms_log_recipient`↔`sms_dispatch_job` mit
`dispatch_status != status` über den gesamten Retention-Zeitraum, pro Worker sekündlich.
- Fix: Projektion ereignisgetrieben (beim Statuswechsel) und Sweep nur über Jobs mit
  `finished_at > now - 1 h` oder Flag `projection_pending`; `EXPLAIN` in MariaDB-Test
  dokumentieren.
- `gateway_routing._due()`: Löschung alter `gateway_message` nur im Leader (vermeidet
  konkurrierende DELETEs/Deadlock-Logs aus 4 Workern).

### P2.4 Tote prozesslokale Altpfade entfernen
`ws._sms_pending`, `ws._job_pending`, `ws._sms_last_gateway`,
`_ordered_sms_gateway_entries`, `connected_print_agent_ids`, `_send_to_local_agents`,
`gateway_routing.wait_result` haben keine Aufrufer mehr (bzw. nur Tests). Entfernen,
Tests anpassen, damit niemand die Worker-lokalen Registries wieder als Quelle nutzt.
Ein Guard-Test, der neue Modul-Dicts in `app/routers`/`app/services` gegen eine
Allowlist prüft (Caches erlaubt, Zustand nicht), verhindert Rückfälle.

### P2.5 Rebase und Integration
Branch basiert auf `faa41045`, main ist 1 Commit weiter (keine Dateiüberschneidung).
Rebase auf aktuelles main, `alembic heads` = genau ein Head, Migration `0316` für
P1.1/P1.2 anschließen.

---

## P3 – Clients

### Linux-Gateway (`feat/multi-worker-phase4`)
- **P3.1 (wichtig)** `print_manager._process_one`: Wird keine Cloud-Druckfreigabe erteilt
  (Timeout 10 s, Verbindungsabbruch, `ok=false`), setzt der Code `uncertain`
  („kein lokaler Druck gestartet“). Das ist nachweislich *nicht gedruckt* – `uncertain`
  blockiert aber den Cloud-Fallback und verlangt manuelle Auflösung; der Alarmdruck
  bleibt aus. Fix: bei Timeout/Abbruch Intent erneut anfragen (Backoff, bis Job-TTL),
  bei expliziter Ablehnung `failed` mit `not_submitted=True` melden. `begin_submit`
  bleibt die einzige Grenze zu `uncertain`. Tests in `test_print_failure_paths.py`.
- P3.2 `_intent_waiters` beim Disconnect sofort mit `False` auflösen (nicht 10 s warten),
  danach Retry gemäß P3.1.
- Tests laufen (55 passed), aber nur mit installiertem `httpx`; Testabhängigkeiten in
  `pyproject.toml` (`[project.optional-dependencies] test`) und README ergänzen.

### Android (`feat/sms-outbox-phase3`)
- P3.3 Journal-Retention: `sms_jobs` mit `result_acked=1` und terminaler Phase nach
  z. B. 7 Tagen löschen; Zeilen fremder `scope` (alter Token/URL nach Neu-Kopplung)
  nach 30 Tagen. Sonst wächst die DB unbegrenzt.
- P3.4 Hängende Multipart-Versuche: bleibt `attempting` ohne alle Part-Callbacks
  (Modem hängt), nach z. B. 5 min lokal `uncertain` (`multipart_callback_timeout`)
  statt erst beim nächsten App-Neustart.
- P3.5 `onUpgrade = Unit`: Migrationsgerüst anlegen (DB-Version 2 mit Retention-Index
  auf `created_at`), damit spätere Schemaänderungen nicht still scheitern.
- P3.6 Heartbeat-Intervall zur Cloud-TTL aus P2.2 abstimmen.

### Windows-Agent (`feat/multi-worker-phase4`)
- P3.7 `JobStore.Cleanup` löscht keine Zeilen mehr (nur Felder genullt) → `jobs` wächst
  unbegrenzt. Nach z. B. 90 Tagen endgültig löschen (Idempotenzfenster ist durch Lease/
  Generation der Cloud ohnehin begrenzt).
- P3.8 `WindowsSpooler._deleted` ist nur im Speicher: Nach Agent-Neustart wird ein
  während des Löschens beobachteter Job als `Gone` → `done` gemeldet. Löschbeobachtung im
  JobStore persistieren oder bei unbekanntem Verbleib nach Neustart `uncertain` melden.

---

## Abnahme nach Umsetzung
1. `ruff`, vollständiges `mypy app/`, `pytest -q -n auto` grün; `e2e/multi_worker` mit
   `--ecp-workers 2` und `4` grün, erweitert um: Bürgerportal-PIN über Workergrenze,
   LIS-Push-Sequenz, Lagedokument-Join auf zweitem Worker, Broadcast bei gestopptem Redis,
   paralleler Kaltstart von 4 Workern auf leerer DB.
2. Linux `pytest`, Android Unit-Tests, Windows `dotnet test` grün.
3. Doku: `multi-worker-rollout.md` um DB-Verbindungsbudget, Redis-Ausfallverhalten im
   Browser und die neuen Migrationen ergänzen.
4. Danach erst die physische Abnahme aus Phase 6 (Modem, CUPS, zwei Windows-PCs).

Reihenfolge für Codex: P0.1/P0.4 (Suite grün) → P0.2/P0.3 → P1.1–P1.3 → P3.1 → P2.1–P2.3 → P1.4–P1.7 → P3.3–P3.8 → P2.4/P2.5.
