# ECP – Stabilitaetskorrekturen vom 28.10.2026

Stand: 2026-10-28. Status: Plan, noch nichts umgesetzt. Grundlage: Pruefung des Auftrags gegen den Code
auf `main` (282d7c7d). Das Produktionslog 07:08-07:10 lag nicht vor; Ursachen sind deshalb in
**gesichert** (im Code belegt) und **Hypothese** (braucht Prod-Pruefung) getrennt.

## Abgleich Auftrag gegen Code (Kurzfassung)

| Auftragspunkt | Befund |
|---|---|
| SMS: eindeutige Job-IDs, persistente Zustaende, idempotente Wiederholung | **Bereits vorhanden** (`sms_outbox`: `job_key` je Org eindeutig, `payload_hash`, `may_have_sent`, `uncertain`, Generationen). Kein Neubau, nur Tests. |
| SMS: Worker-uebergreifende Zuordnung | **Bereits vorhanden** (`gateway_presence`/`gateway_routing`, Praesenz in der DB, `worker_id`, Migration 0312). Nur Luecken pruefen. |
| Teams: Unique pro Org + Leitstellennummer | **Bereits vorhanden**: `uq_incident_org_lis_operation_number` (ein Einsatz je Org und Nummer) und `uq_incident_alarm_job_incident_channel_variant` (ein Teams-Job je Einsatz/Variante). Parallele Worker oder doppelte Eingangsmeldungen koennen damit **keinen** zweiten Erst-Teams-Job erzeugen. |
| Teams: Retry bei unklarem Status | **Luecke (gesichert)**, siehe T1. Das ist die wahrscheinlichste Ursache der Doppelkarte. |
| fontTools auf WARNING | Steht schon in `app/main.py:177-178`. Ursache fuer weiter sichtbare Ausgabe unklar (Hypothese L1). |
| Redis fuer Hydranten | `shared_cache` (Redis, `lock`, `get_entry`) und ein Single-Flight-Muster existieren im Wetterdienst; wiederverwendbar. |
| Slow-Query `suche/adapter.py:150` | Ursache **gesichert**, siehe P1. |

Naechste freie Migration: **0330**.

## Priorität 1a – SMS-Gateway (`app/routers/ws.py`)

### Gesichert

1. `sms_gateway_ws` ruft beim Verbindungsaufbau `_touch_sms_gateway_token` auf (ws.py:436). Das ist ein
   ORM-Lesen mit `db.get` plus Commit, **ohne Retry**. Ein 1020 dort wirft aus dem Handler, **bevor**
   `websocket.accept()` kommt. Das Gateway sieht einen Abbruch und verbindet neu.
2. Der Aufruf ist **redundant**: `gateway_presence.begin` setzt `last_used_at` (gateway_presence.py:120)
   bereits im selben Verbindungsaufbau. `_heartbeat_sms_gateway_token` ist toter Code (nur noch in
   `tests/test_ws_gateway_stability.py:61` gepatcht).
3. `gateway_routing.begin/touch/state/end` und `presence.touch` haben **keinen** Retry. `touch` laeuft bei
   **jeder** eingehenden Nachricht (inkl. Pong) und schreibt Ablaufzeit **und** Heartbeat: ein
   Schreibzugriff je Nachricht je Gateway, auf derselben Zeile, auf die auch Outbox-Claims zielen.
   Das erzeugt die Konkurrenz auf `sms_gateway_token`.
4. `SMS_TTL_S = 60` bei Ping alle 30 s: nur ein Worker-Stall von mehr als ~30 s (vgl. P3) laesst die Praesenz
   ablaufen. Dann meldet `dispatch_sms` "kein Gateway verbunden" bzw. Timeout. Zusammenhang mit den
   Event-Loop-Verzuegen ist plausibel, aber nicht belegt.

### Hypothese (vor der Umsetzung pruefen, ca. 10 Minuten)

- Dass 1020 **trotz** `DB_SNAPSHOT_ISOLATION_OFF=True` auftrat: Der Snapshot-Plan (PR #759-#764) wurde
  am selben Tag fertiggestellt. Pruefen: war der Fix um 07:08 schon deployt, steht `DB_SNAPSHOT_ISOLATION_OFF`
  in der Prod-`.env` auf `false`, liefert `SHOW SESSION VARIABLES LIKE 'innodb_snapshot_isolation'` in einer
  App-Verbindung `OFF` (Diagnose-Check aus #764), und erkennt `supports_mariadb_snapshot_isolation` die
  Prod-Versionsstring-Form? Ohne diese Klarheit koennte der Code-Fix ein Konfigurationsproblem verdecken.

### Umsetzung

| Schritt | Aenderung | Dateien |
|---|---|---|
| S1 | `_touch_sms_gateway_token` und `_heartbeat_sms_gateway_token` entfernen. `last_used_at` kommt aus `presence.begin`. | `ws.py`, `tests/test_ws_gateway_stability.py` |
| S2 | `gateway_routing.begin/touch/state/end` je Versuch mit **neuer Session** und `mit_wiederholung` (3 Versuche, Codes 1020/1205/1213, Jitter, `rollback` vor Wiederholung). Der Callback enthaelt nur den DB-Schritt, keine Aussenwirkung. | `gateway_routing.py` (`_db()` bleibt), nutzt `db_transactions.py` |
| S3 | **Praesenz ist best effort nach dem Aufbau**: scheitert `touch` nach allen Versuchen mit einem DB-Fehler, wird die Verbindung **nicht** geschlossen (nur WARNING mit Fehlerklasse). Geschlossen wird nur bei eindeutigem `False` (ersetzt/abgelaufen). Beim Aufbau (`begin`) bleibt ein endgueltiger DB-Fehler ein Abbruch, aber mit Close-Code 1013 ("try again") statt 500, damit das Gateway kontrolliert neu verbindet. | `ws.py` |
| S4 | **Weniger Schreibzugriffe**: `touch` je Verbindung hoechstens alle 15 s schreiben (Merker `last_touch_monotonic` am `Connection`-Objekt im Handler). Dazwischen nur lokal pruefen. Supersede-Erkennung bleibt ueber die bestehende periodische `state`-Abfrage im Idle-Zweig (alle 30 s) erhalten. Heartbeat-Spalte wie bisher hoechstens einmal je 60 s. Bei `SMS_TTL_S = 60` bleibt damit mehr als 3x Reserve. | `gateway_presence.py` oder Aufrufer in `ws.py` |
| S5 | `SMS_TTL_S` auf 90 s anheben (Reserve fuer kurze Loop-Stalls). Zu klaeren, ob `sms_outbox.LEASE_S`/`RECEIPT_TIMEOUT_S` dazu passen. | `gateway_presence.py` |
| S6 | Logs je Verbindung: `connection_id`, `worker_id`, Close-Code, Dauer, Fehlerklasse (bei Disconnect ist das schon teilweise da, bei Fehlern ergaenzen). Keine Tokens/Hashes. | `ws.py` |

### SMS-Timeouts, Reconnects, Worker-Zuordnung (nur Pruefung, kein Neubau)

- `dispatch_sms` meldet "Timeout oder unklarer Versand" als `RuntimeError`; die Outbox haelt den Job dann als
  `uncertain`/`may_have_sent` und wiederholt **nicht** automatisch auf anderem Gateway. Das ist korrekt und
  bleibt. Zu pruefen: ob ein **Reconnect desselben Tokens** (neue `connection_id`) einen laufenden Job
  verwaist (`generation`/`supersede`), und ob `hello` den Zustand sauber abgleicht. Ergebnis als Test festhalten.
- Keine neue Job-ID-Logik: `alarm:{incident_id}:{variant}:{hash(phone)}` ist bereits der stabile Schluessel.

### Tests (neu)

- `tests/mariadb/test_sms_gateway_presence_conflicts.py`: zwei Verbindungen desselben Tokens gleichzeitig
  (`begin`), Gewinner eindeutig, Verlierer `superseded`; parallele `touch` + Outbox-Claim auf derselben Zeile
  ohne unbehandeltes 1020.
- Unit: `begin` wirft zweimal 1020, dritter Versuch klappt; `touch` wirft dauerhaft 1213 und die Verbindung bleibt offen;
  `touch` ohne Schreibzugriff innerhalb der 15-s-Drosselung (Zaehler der UPDATEs).
- Unit: Gateway-Abbruch mitten im Versand ergibt `uncertain`, Reconnect ergibt **keinen** zweiten Versand
  (gleicher `job_key`).
- Mehrworker: zwei Prozesse/Sessions, Gateway haengt an Worker A, Auftrag entsteht auf B, wird ueber DB-Routing zugestellt
  (Muster `test_gateway_presence_routing.py`).

## Priorität 1b – Doppelte Teams-Benachrichtigung

### Ablauf (Ist)

Eingang (DIBOS `dibos_enrich`, LIS `lis_sync`, API `api_v1`, Gateway `alarm_apply_service.wende_an`)
→ Einsatz (Unique Org+Nummer, Race-Fallback auf Gewinner) → `enqueue_incident_alarm`
(Job je Kanal, Unique Einsatz+Kanal+Variante) → `process_incident_alarm` → `_claim_once`
(atomares `UPDATE ... WHERE status IN (pending, retry)`) → `_send_teams` → `post_incident_card` → Webhook.
Teams wird nur an dieser einen Stelle versendet (`post_incident_card` hat einen einzigen Aufrufer,
`alarm_outbox.py:251`). Parallele Worker, wiederholte Eingangsmeldungen und Neustarts sind durch die
beiden Unique-Constraints und den Claim bereits abgedeckt.

### Gesicherte Ursachen einer Doppelkarte

| ID | Ursache | Beleg | Wahrscheinlichkeit |
|---|---|---|---|
| **T1** | Bei Timeout/Transportfehler liefert `_post_payload` `retryable=True` (teams_alarm_service.py:99-101). `_send_teams` wirft daraufhin `RuntimeError` → Job `retry` → nach 5 s erneuter POST. Ein `ReadTimeout` (10 s) heisst aber **nicht**, dass Teams nichts gepostet hat. Ebenso 5xx. | alarm_outbox.py:261-262, `_retry_at` Teams (5, 20, 60, 300, 900 s) | **hoch**; bis zu 6 Karten moeglich |
| **T2** | `_record_card_post` (nur mit `suppress_card_in_major_incident`) laeuft **nach** erfolgreichem POST. Wirft `db.flush()` dort etwas anderes als `IntegrityError` (z. B. 1020), gilt der Versand als Fehler → Retry → Doppelkarte. | teams_alarm_service.py:213-214 | niedrig, nur mit GSL-Option |
| **T3** | Echte Nachalarmierung (`nach:`-Variante) mit gleicher Leitstellennummer: eigener Job, eigener Text-Praefix `NACHALARMIERUNG`. Fachlich gewollt, kann aber wie eine Doppelkarte aussehen, besonders wenn `stichwort_erhoeht` durch unterschiedliche Stichwort-Mappings von DIBOS und Gateway ausgeloest wird. | alarm_apply_service.py:383-405 | mittel, **Prod pruefen** |

### Prod-Pruefung vor der Umsetzung (Lesezugriff)

Fuer die betroffene Leitstellennummer: `incident_alarm_job` (channel=teams: `variant`, `attempt_count`, `status`,
`last_error`) und `incident_log` ("Alarmierung Teams"). `attempt_count > 1` mit `last_error` Timeout/HTTP 5xx
bestaetigt T1; zwei Zeilen mit unterschiedlicher `variant` bestaetigen T3; zwei Einsaetze scheiden aus (Unique).

### Konzept

1. **Eindeutigkeit bleibt** an `(primary_org_id, lis_operation_number)` und `(incident_id, channel, variant)`.
   Ergaenzend ein **expliziter Ereignisschluessel** je Job (Migration 0330), damit die Regel "pro Org,
   Leitstellennummer, Typ ein Auftrag" auch dann haelt, falls ein Einsatz neu angelegt wird (z. B. Merge/Neuanlage):
   `event_key = "{org_id}:{leitstellennummer oder 'inc'+incident_id}:{channel}:{variant}"`, **Unique** `(org_id, event_key)`
   für Kanal `teams` (Erstalarm und Nachalarm getrennt durch `variant`). Insert-Konflikt = bereits vorhanden,
   wird protokolliert statt erneut zu senden.
2. **Zustand pro Benachrichtigung** (alle auf `incident_alarm_job`, Migration 0330, nur Additionen, nullable/Default):
   `event_key`, `send_started_at` (vor dem POST committed), `may_have_sent` (Bool, vor dem POST gesetzt),
   `external_status` (`accepted` / `rejected` / `unknown`), `external_http_status`. `finished_at`/`status` bleiben.
3. **Retry-Regel (T1)**: automatisch wiederholt wird **nur**, wenn sicher nichts gepostet wurde:
   Verbindungsfehler vor dem Senden (`ConnectError`, `ConnectTimeout`, DNS), HTTP 429 mit `Retry-After`,
   ungueltige URL/Konfiguration (nicht retry, sondern failed). **Alles andere unklare** (`ReadTimeout`,
   `WriteTimeout`, `RemoteProtocolError`, 5xx, 502/504, Abbruch nach dem Senden) → Status `uncertain`,
   `may_have_sent = true`, **kein** automatischer Retry, `IncidentLog` Warnung "Teams-Zustellung unklar, nicht
   wiederholt", plus Eintrag im Admin (bestehende Alarm-Diagnose). Manuelles "Erneut senden" mit Bestaetigung
   fuer die Fuehrungskraft (Opt-in, protokolliert), damit die Alarmierung nie stillschweigend ausfaellt.
   SMS, Push, Mail sind davon unberuehrt und laufen unabhaengig weiter.
4. **Klassifizierung** zentral in `_post_payload`: Rueckgabe statt `(sent, retryable, reason)` ein kleines
   Ergebnisobjekt `(outcome: accepted|rejected_safe|unknown, http_status, reason)`. `retryable` ergibt sich nur aus
   `rejected_safe`.
5. **T2**: `_record_card_post` in `try/except Exception` kapseln; der Versand gilt trotzdem als `sent`.
6. **Wiederholte Eingangsmeldungen protokollieren**: bei Treffer auf bestehende Nummer (DIBOS/LIS/Gateway/API)
   einmal je Quelle und Minute ein Audit `incident.alarm_duplicate_ingest` (Quelle, Nummer-Praefix, ohne Personendaten)
   ohne neue Erstbenachrichtigung. Gateway hat `dedup_action = duplicate` bereits; DIBOS/LIS ergaenzen.
7. **Korrigierte Alarmmeldung**: `merge_folgenachricht` bleibt der einzige Weg; Test, dass eine korrigierte Meldung mit
   gleicher Nummer **keinen** neuen Einsatz und keinen Erstalarm erzeugt, aber bei erhoehtem Stichwort/neuen Codes genau
   eine `nach:`-Karte.
8. **Restrisiko (im Konzept festgehalten)**: Teams-Workflow-/Webhook-Schnittstellen kennen keinen Idempotenzschluessel.
   Zwischen "Request gesendet" und "Antwort bestaetigt" kann Teams die Nachricht verarbeitet haben. Exakt-einmal ist
   daher nicht garantierbar. Gewaehlt wird **at-most-once bei Unklarheit** (lieber eine manuell nachgesendete Karte als
   bis zu sechs automatische) und **at-least-once nur bei sicherer Ablehnung**. SMS/Push decken die Alarmierung
   unabhaengig ab.

### Tests (neu, `tests/test_alarm_outbox_teams_dedup.py` plus MariaDB-Variante)

- Gleiche Leitstellennummer zweimal direkt hintereinander (DIBOS, LIS, Gateway, API): ein Einsatz, ein Teams-Job, ein POST.
- Gleichzeitig aus zwei Sessions/Workern (`threading` + eigene Sessions, MariaDB): ein Einsatz, ein Teams-Job, ein POST.
- Nach Neustart (neuer Prozess/`_running_incident_tasks` leer, Job `sent`): kein weiterer POST.
- Webhook `ReadTimeout`: genau **ein** POST, Job `uncertain`, `may_have_sent`, kein Retry; `ConnectError` und 429: Retry mit Backoff.
- 5xx: `uncertain`, kein Retry.
- Echte Nachalarmierung: genau eine zusaetzliche `nach:`-Karte, nicht doppelt bei Wiederholung derselben Codes.
- Zwei Organisationen mit gleicher Leitstellennummer: je ein Einsatz, je eine Karte.
- `_record_card_post` wirft: Job bleibt `sent`.
- Lease-Ablauf eines Teams-Jobs in `sending` (Worker-Crash): `uncertain`, kein zweiter POST (besteht teilweise, verifizieren).

## Priorität 2 – Overpass/Hydranten (`app/services/hydrant_service.py`)

### Ist (gesichert)

Nur prozesslokaler Cache (`_cache`), kein Single-Flight, bei jedem Fehler `[]` ohne Fehlercache: jeder Worker und jede
Anfrage laeuft erneut in 429/502 und wartet bis zu `HYDRANT_TIMEOUT_SECONDS` (8 s). Der Aufrufer
(`ui_incident.py:1469`) hat bereits den Fallback auf `incident.hydranten_json`, kennzeichnet ihn aber nicht als veraltet
und wartet trotzdem die volle Zeit.

### Umsetzung

| Schritt | Aenderung |
|---|---|
| H1 | Ergebniscache ueber `shared_cache` (Redis, TTL wie `HYDRANT_CACHE_TTL_SECONDS`), lokaler Cache als Stufe davor. Ohne `REDIS_URL` bleibt das heutige Verhalten. |
| H2 | Single-Flight: pro Schluessel `(lat, lng, radius, limit)` lokal `asyncio.Lock` und worker-uebergreifend `shared_cache.lock`; Wartende lesen danach den Cache (Muster `weather_service._single_flight`). Sperr-TTL < Gesamtbudget. |
| H3 | Fehlercache: nach 429/502/503/504/Timeout kurzer Negativeintrag (z. B. 60 s, bei 429 `Retry-After`), exponentieller Backoff bis 15 min, mit Jitter. |
| H4 | Circuit Breaker (Redis-Zaehler, prozesslokal als Rueckfall): ab N Fehlern in M Minuten offen, dann sofort Fallback ohne Netzaufruf; Half-Open-Probe nach Ablauf. Eigene Konfiguration `HYDRANT_BREAKER_*`. |
| H5 | Rueckgabe als `HydrantResult(items, stale: bool, stand, quelle)`; Aufrufer (`ui_incident`, Infoscreen, Einsatzinfo, MCP) zeigen "Stand vom ... (veraltet)". Fallback-Reihenfolge: Redis-Altbestand (auch abgelaufen) → `incident.hydranten_json` → Stammdaten (`wasserstelle_service`) → leer. |
| H6 | Harte Gesamtzeit je Aufruf (z. B. 3 s) und Aufruf aus der Einsatzansicht **nicht** in der DB-Session-Haltezeit: Netzabfrage ausserhalb offener Transaktion bzw. als Hintergrundaufgabe mit Fallback fuer die erste Antwort. |

Tests: `tests/test_hydranten.py` erweitern (429/502/Timeout → Fallback, Backoff, Breaker oeffnet/schliesst, Single-Flight mit 20
parallelen Aufrufern = 1 Upstream-Request, Redis zwischen zwei "Workern" via fakeredis, Einsatzansicht blockiert nicht,
`stale`-Kennzeichnung).

## Priorität 3 – Performance

### Gesichert

- **P1** `ObjektDokumentAdapter.alle_ids` (`suche/adapter.py:143-150`): laedt je Seite die **kompletten**
  Entities `ObjektDokumentSeite` inkl. `volltext`, `Objekt` und `ObjektDokument` (Dreifach-Join, Zeilen als ORM-Objekte), nur um
  Strings und `volltext.strip()` zu pruefen. Bei grossen PDFs/OCR sind das Hunderte MB Text. Der Filter `volltext/titel nicht leer`
  laeuft in Python statt in SQL. **Fix**: nur `ObjektDokumentSeite.id` selektieren, Bedingung in SQL
  (`or_(func.length(trim(volltext)) > 0, func.length(trim(titel)) > 0)`), `yield_per`/Batches. Gleiches Muster fuer `dokument()`
  nicht noetig. Vorschlag Index: `objekt_dokument_seite (org_id, dokument_id)` pruefen (`EXPLAIN` auf Prod),
  `objekt_dokument (org_id, freigabe_status, ist_aktuelle_version)`.
- Aufgerufen wird `alle_ids` aus `suche/indexer.py:108` und `suche/outbox.py:186`; beide laufen als Hintergrundaufgabe. Wenn sie
  synchron DB-Zugriffe im Event-Loop-Thread ausfuehren, erklaert das Verzuege.

### Hypothese (Messung noetig)

Die Event-Loop-Verzuege 3217 ms / 1635 ms (`main.py:160`, nur Dauer, keine Ursache) lassen sich ohne Prod-Daten nicht
zuordnen. Kandidaten: WeasyPrint-PDF (`pdf_service.py` rendert synchron; Aufrufer pruefen, ob `to_thread`), Suchindex,
synchrone DB-Aufrufe in `async def`-Pfaden (Baseline `tests/data/async_routes_sync_db_baseline.txt`), Overpass-Wartezeit.

### Umsetzung

| Schritt | Aenderung |
|---|---|
| P1 | `alle_ids` in SQL umbauen (siehe oben) + Test mit grossem Volltext (Speicher/Query-Anzahl) |
| P2 | **Diagnose zuerst**: `_loop_lag_monitor` ergaenzen um Stack-Dump der Hauptthread-Aufgabe ueber `faulthandler`/`sys._current_frames()` bei Verzug > Schwelle (nur Funktionsnamen, gedrosselt auf 1/min), dazu Name der laufenden Task. Damit zeigt der naechste Vorfall die Ursache statt Vermutung. |
| P3 | PDF-Erzeugung: alle `HTML(...).write_pdf` per `asyncio.to_thread` bzw. Prozess-Pool mit Limit (CPU-gebunden, GIL-Haltezeit pruefen), Parallelitaetsbegrenzung (Semaphore) |
| P4 | `async def` mit synchroner DB pruefen (Ruff-/Baseline-Liste), die Treffer in der Zeit der Verzuege zuerst umstellen |
| P5 | Suchindexer als eigener Leader-Task mit Batches und `sleep(0)` zwischen Batches; Hintergrund-Worker (bestehender `background_runtime`) fuer schwere Jobs |

## Priorität 4 – Logging und Datenqualitaet

| ID | Befund | Umsetzung |
|---|---|---|
| L1 | `fontTools` wird in `main.py:177` schon gedaempft. Ausgabe trotzdem moeglich, wenn Logging **nach** dem Import neu konfiguriert wird (uvicorn/gunicorn `dictConfig`, Log-Puffer-Handler setzt Level) oder in Kindprozessen. | Reproduzieren (Logger-Level im Worker auslesen), Dämpfung in einen `configure_logging()` verschieben, der nach der Server-Konfiguration laeuft, inkl. `fontTools.subset`, `fontTools.ttLib`, `fontTools.misc.timeTools`. Test: Logger-Level nach App-Start. |
| L2 | DIBOS-Mitgliedszuordnung: nur ueber `idSybos` (`_match_member_by_sybos`); fehlt die ID, wird Freitext gespeichert. | Auswertung der Prod-Daten (Anteil `mitglied_id IS NULL` je Org), Fallback-Match (Name + Org, nur bei eindeutigem Treffer), Hinweis im Admin. Kein automatisches Raten bei Mehrdeutigkeit. |
| L3 | `apply_printer_status` schreibt bei jeder Meldung alle Drucker und der WS-Handler sendet immer einen `printer_report`-Broadcast (ws.py:896-901), auch bei unveraendertem Status (`checked_at` aendert sich jedes Mal). | Vergleich ohne `checked_at`; nur bei Aenderung schreiben und broadcasten; `checked_at` hoechstens alle 5 min persistieren. Logzeilen nur bei Statuswechsel. |
| L4 | Fehlerlogs ohne Fehlerklasse/Dauer/Worker/Korrelations-ID. Teilweise vorhanden (`worker_id`, `request_id` in Admin-MCP). | Kleiner Helfer `log_fehler(logger, op, exc, start, **ids)` mit Fehlerklasse, Dauer, `worker_id`, `correlation_id` (ContextVar, aus `X-Request-ID` oder generiert; fuer WS die `connection_id`); Tokens, Hashes, Telefonnummern, SQL-Parameter nie. Zuerst bei Alarm-Outbox, SMS-Gateway, Teams, Hydranten einsetzen. |

## Reihenfolge, Pull Requests

1. **PR A** (S1-S4, S6, Tests): SMS-Gateway Verbindungsaufbau/Praesenz. Klein, sofort deploybar.
2. **PR B** (T1, T2, Klassifizierung, Tests): Teams-Retry-Regel. Ohne Migration, Hauptfix der Doppelkarte.
3. **PR C** (Migration 0330, T3-Absicherung, `event_key`, Audit, manuelles Nachsenden, Tests).
4. **PR D** (S5, Mehrworker-Tests, Reconnect-Pruefung).
5. **PR E** (H1-H6), **PR F** (P1-P5), **PR G** (L1-L4).

Pro PR: `ruff`, `mypy`, `pytest -q -n auto`; MariaDB-Tests fuer A-D; keine Alarmfunktion wird abgeschaltet
(SMS, Push, Teams, Mail bleiben aktiv; Teams geht bei Unklarheit auf manuelle Wiederholung, nicht auf "aus").

## Offene Entscheidungen

- Teams bei unklarem Status: **nie automatisch wiederholen** (Empfehlung) oder genau **einmal** nach 60 s?
- `SMS_TTL_S` 90 s: einverstanden?
- Sollen bei Hydranten veraltete Daten (> 24 h) im UI sichtbar markiert oder ausgeblendet werden?

## Deployment (voraussichtlich)

- PR A/B/E/F/G: kein Schema, normaler Neustart. Neue `.env`: `HYDRANT_BREAKER_*`, optional `HYDRANT_TOTAL_TIMEOUT_S`.
- PR C: `alembic upgrade head` (0330, nur Spaltenadditionen und Unique-Index; bestehende Jobs bekommen
  `event_key` per Backfill-Schritt in der Migration, Konflikte werden gemeldet statt abgebrochen).
- Vorab pruefen: `innodb_snapshot_isolation` in Prod, Deploy-Stand um 07:08, Alarm-Jobs der betroffenen Nummer.

## Restrisiken

- Teams: Exakt-einmal nicht garantierbar (siehe T-Konzept 8).
- Ursachen von L1 und der Loop-Verzuege sind Hypothesen bis zur Messung (P2).
- Hydranten: Fallback-Daten koennen veraltet sein; sie werden gekennzeichnet.
