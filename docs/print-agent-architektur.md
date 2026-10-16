# Windows-Druckagent: Architektur, Protokoll, Migration

Stand 2026-10-16. Diese Datei ist die verbindliche Spezifikation für die Cloud
(`BattloXX/Einsatzcockpit`) und den Agent (`BattloXX/Einsatzcockpit-Windows-Gateway`).

## Ziel

Ein oder mehrere Windows-PCs drucken ohne zusätzliche Hardware für Einsatzcockpit. Der
erste Anwendungsfall ist der Kommandofunk: Zwei PCs bedienen denselben Netzwerkdrucker
redundant. Das Modell ist aber allgemein gehalten:

- ein PC mit einem Drucker
- ein PC mit mehreren Druckern
- mehrere PCs mit einem Drucker
- ein Ausweichdrucker als weitere Route

Das Linux-Gateway (`/ws/gateway`, Push-Zustellung) bleibt unverändert.

## Begriffe

| Begriff | Umsetzung |
|---|---|
| Druckziel | bestehendes `Printer` (z. B. „Kommandofunk“). Regeln, Druckdialog, manueller Druck und Ersatzdrucker referenzieren weiter `printer.id`. |
| Agent | `Gateway` mit `kind = "windows_agent"`, Pairing wie beim Linux-Gateway, eigenes Device-Token je PC |
| Route | `PrinterAgentRoute`: Druckziel ↔ Agent ↔ lokale Windows-Queue, mit Priorität |
| Lease | befristeter, exklusiver Anspruch eines Agents auf einen Job (`lease_token`, `lease_expires_at`, `lease_generation`) |
| Intent | Freigabe der Cloud direkt vor der Übergabe an den Spooler. Danach wird nie automatisch umgeroutet. |

## Datenmodell (Migration 0289, nur additiv)

### `gateway`

| Spalte | Typ | Bedeutung |
|---|---|---|
| `kind` | String(20), NOT NULL, `server_default 'linux'` | `"linux"` oder `"windows_agent"` |
| `agent_info` | JSON, nullable | `{host, os, machine_id, version, spooler, queues: [...], reported_at}` |

### Neue Tabelle `printer_agent_route`

Die Tabelle ist TenantScoped und in `_TENANT_TABLE_NAMES` eingetragen.

| Spalte | Typ |
|---|---|
| `id` | BigInteger PK |
| `org_id` | aus TenantScoped |
| `printer_id` | BigInteger FK `printer.id` ON DELETE CASCADE, NOT NULL |
| `gateway_id` | BigInteger FK `gateway.id` ON DELETE CASCADE, NOT NULL |
| `priority` | Integer NOT NULL default 100. Kleiner bedeutet bevorzugt. |
| `local_queue` | String(255) NOT NULL: Windows-Queue-Name bzw. Ziel bei `ipp_direct`/`folder` |
| `render_mode` | String(20) NOT NULL default `driver`: `driver`, `pdf_direct`, `ipp_direct`, `command` oder `folder` |
| `options` | JSON nullable: `{tray, orientation, scaling, force_color, dpi, collate, target_uri, target_dir, command}` |
| `identity_check` | Boolean NOT NULL default True |
| `aktiv` | Boolean NOT NULL default True |
| `identity` | JSON nullable: zuletzt gemeldete Ist-Identität `{serial, uuid, mac, ip, host}` |
| `ready` | Boolean NOT NULL default False |
| `readiness` | JSON nullable: `{state, detail, checked_at}` |
| `erstellt_am` | DateTime |

Constraints und Indizes:

- `UniqueConstraint(printer_id, gateway_id, name="uq_printer_agent_route")`
- `Index(org_id, gateway_id)`

### `print_job`

| Spalte | Typ |
|---|---|
| `delivery` | String(10) NOT NULL `server_default 'push'`. `"lease"` steht für den Agent-Pfad. |
| `lease_gateway_id` | BigInteger FK `gateway.id` ON DELETE SET NULL, nullable |
| `lease_token` | String(64) nullable |
| `lease_expires_at` | DateTime nullable (naive UTC) |
| `lease_generation` | Integer NOT NULL default 0 (`server_default '0'`) |
| `phase` | String(20) nullable: `leased`, `fetching`, `submitting` oder `spooled` |
| `spool_ref` | String(120) nullable: Spooler-Job-ID bzw. Dokumentname |
| `queued_at` | DateTime nullable |
| `agent_contact_at` | DateTime nullable: letzte Meldung des leasenden Agents zu diesem Job |

Index: `Index("ix_print_job_delivery_status", "delivery", "status")`

### Neuer Job-Status

`JOB_UNCERTAIN = "uncertain"`, Label „Unklar – bitte prüfen“.

- Er steht **nicht** in `JOB_TERMINAL`.
- Er löst keinen Ersatzdruck, keine Watchdog-Eskalation und keine Umroutung aus.
- Auflösung nur durch einen Menschen: `POST /gateway/jobs/{id}/resolve` mit `action=reprint|done|discard`.

## Ablauf eines Agent-Jobs

```
create_print_job (unverändert)
   │
dispatch_job ── Drucker hat aktive Route? ──nein──► bisheriger Push-Pfad (unverändert)
   │ ja
   ▼
status=queued, delivery=lease, queued_at=now, Hinweis job_available an die Agents der Routen
   │
Agent: lease_request ─► bedingtes UPDATE (nur 1 Gewinner) ─► status=sent, phase=leased
   │
Agent lädt PDF / rendert HTML (phase=fetching, per job_status gemeldet)
   │
Agent: print_intent ─► bedingtes UPDATE phase=submitting (nur mit gültigem Lease)
   │ ok
Agent persistiert "submitting" lokal, übergibt an den Spooler, persistiert spool_ref
   │
job_status printing (phase=spooled) … done | failed
```

### Lease-Regeln (Cloud, `app/services/print_agent_service.py`)

Konstanten:

| Konstante | Wert |
|---|---|
| `LEASE_TTL_S` | 60 |
| `AGENT_ONLINE_S` | 90 |
| `READINESS_FRESH_S` | 180 |
| `FALLBACK_GRACE_S` | 15 |
| `UNCERTAIN_AFTER_S` | 600 |
| `QUEUE_MAX_WAIT_S` | 180 |
| `MAX_AGENT_ATTEMPTS` | 3 |
| `SWEEP_INTERVAL_S` | 15 |

**Route-Kandidaten** eines Druckziels sind Routen, für die gilt:

- die Route ist aktiv
- der Gateway ist `windows_agent`, gekoppelt (`device_token_hash` gesetzt), `status == online` und `last_seen_at` jünger als `AGENT_ONLINE_S`
- `ready == True` und `readiness.checked_at` jünger als `READINESS_FRESH_S`
- bei aktivem `identity_check` passt die Identität zum Druckziel (siehe Identität)

Die Kandidaten werden nach `(priority, id)` sortiert.

**`darf_leasen(route, kandidaten, job, now)`** ist eine reine Funktion:

1. Ist die Route nicht unter den Kandidaten, lautet das Ergebnis False.
2. `rang` ist der Index der Route in `kandidaten`. Bei `rang == 0` lautet das Ergebnis True.
3. Sonst True, wenn der Job seit mindestens `FALLBACK_GRACE_S * rang` Sekunden (gemessen ab `queued_at`) in `queued` liegt. Damit springen Agents mit niedrigerer Priorität erst nach einer Wartezeit ein, wenn der bessere Agent nicht zugreift.

**`lease_jobs(db, gateway, max_n, now)`:**

1. Kandidaten-Jobs laden:
   - `delivery == 'lease'`, `status == 'queued'`, `org_id == gateway.org_id`
   - `printer_id` ist eine aktive Route dieses Agents
   - Sortierung nach `queued_at`, höchstens 20
2. Je Job, für den `darf_leasen` zutrifft, ein bedingtes UPDATE:
   ```
   UPDATE print_job SET status='sent', phase='leased', lease_gateway_id=:gw,
     lease_token=:neu, lease_generation=lease_generation+1,
     lease_expires_at=:now+TTL, agent_contact_at=:now, gateway_id=:gw,
     attempts=attempts+1
   WHERE id=:id AND org_id=:org AND status='queued' AND delivery='lease'
   ```
   `rowcount == 1` bedeutet gewonnen. Danach wird sofort committet. Der Ablauf ist sicher bei mehreren Workern und mehreren Agents.
3. Für gewonnene Jobs wird ein Grant zurückgegeben:
   ```json
   {"job_id", "lease_token", "lease_generation", "lease_expires_in": 60,
    "document_type", "artifact_url", "render_kind": "pdf"|"html",
    "options": {copies, duplex, color, media},
    "route": {"route_id", "printer_id", "local_queue", "render_mode", "options"}}
   ```
   `artifact_url` kommt aus `print_artifact_service.artifact_url(job)`, das bestehende signierte URLs liefert. `render_kind` wird über `is_html_render(job)` bestimmt.

**`renew(db, gateway, leases, now)`:**

- `leases` hat die Form `[{job_id, lease_token}]`.
- Bedingtes UPDATE `lease_expires_at = now + TTL, agent_contact_at = now` mit der Bedingung `lease_token` + `lease_gateway_id` passen und `status IN ('sent','printing')`.
- Rückgabe sind die Job-IDs, deren Lease nicht mehr gilt. Der Agent bekommt dafür `abandon`, sofern die Phase noch vor `submitting` liegt.

**`print_intent(db, gateway, job_id, token, now)`:**

```
UPDATE … SET phase='submitting', agent_contact_at=now, lease_expires_at=now+TTL
WHERE id=:id AND lease_gateway_id=:gw AND lease_token=:t AND status='sent'
  AND phase IN ('leased','fetching') AND lease_expires_at >= now
```

Bei `rowcount == 1` lautet das Ergebnis `ok`. Sonst wird `reason` aus dem aktuellen Zustand abgeleitet:

- `lease_expired`
- `canceled`
- `not_owner`
- `already_submitted`

Ein zweiter Intent desselben Tokens in Phase `submitting` ist idempotent und ergibt ebenfalls `ok`.

**`apply_agent_status(db, gateway, job_id, token, status, phase, spool_ref, error, now)`:**

Passen Token oder `lease_gateway_id` nicht, wird die Meldung ignoriert. Das Ergebnis ist `"stale"`, die Meldung wird geloggt. Sonst gilt je Status:

| Status | Wirkung |
|---|---|
| `printing` | `status = printing`, `phase = spooled`, `spool_ref` setzen, `agent_contact_at = now` |
| `done` | `status = done`, Lease bleibt zur Nachvollziehbarkeit stehen |
| `failed` mit Phase vor `submitting` (`leased`/`fetching`) | Bei `attempts < MAX_AGENT_ATTEMPTS`: zurück auf `queued`, `queued_at = now`, Lease leeren, `error` setzen, Agents benachrichtigen. Sonst `failed` und Ersatzdruck (`dispatch_fallback_for_failed_job`). |
| `failed` mit Phase `submitting`/`spooled` | `failed` und Ersatzdruck. Der Spooler hat den Fehler sicher gemeldet, ein Druck fand nicht statt. |
| `uncertain` | `status = uncertain`, `error` setzen, **kein** Ersatzdruck |
| `canceled` | `status = canceled` |

Alle Übergänge laufen als bedingte UPDATEs und nur aus nicht-terminalen Zuständen. `uncertain` lässt sich nur durch Resolve verlassen.

Gibt es eine echte Statusänderung, folgt ein Broadcast `print_job_status`, wie beim Linux-Pfad.

**`resume(db, gateway, open_jobs, now)` beim `hello`:**

- `open_jobs` hat die Form `[{job_id, lease_token, local_state}]`.
- Gehört der Job diesem Agent, passt der Token und ist der Job nicht terminal: Lease verlängern.
- Sonst: `abandon`. Der Agent verwirft den Job dann nur, solange `local_state` vor `submitting` liegt. Ab `submitting` behält er den Status lokal und meldet `uncertain`, er druckt aber nie neu.

**`sweep(db, now)`** läuft als Loop in jedem Worker, analog `print_watchdog`, mit bedingten UPDATEs:

| Fall | Bedingung | Wirkung |
|---|---|---|
| a | `status = sent`, `phase IN (leased, fetching)`, `lease_expires_at < now` | zurück auf `queued` (`queued_at = now`, Lease leeren), Agents benachrichtigen |
| b | `status IN (sent, printing)`, `phase IN (submitting, spooled)`, `agent_contact_at < now - UNCERTAIN_AFTER_S` | `status = uncertain` mit `error` „Keine Rückmeldung des Druckagents nach Druckübergabe – bitte am Drucker prüfen“ |
| c | `status = queued`, `delivery = lease`, `queued_at < now - QUEUE_MAX_WAIT_S` | `failed` mit „Kein Druckagent bereit“, danach `dispatch_fallback_for_failed_job` |

Jede Statusänderung erzeugt einen Broadcast `print_job_status`.

### Identität

`Printer.identity` ist die Soll-Identität. Fehlt sie, übernimmt die erste gemeldete Ist-Identität einer Route sie automatisch. Die Prüfung ist in `identitaet_passt(soll, ist)` umgesetzt:

1. Es wird der erste Schlüssel aus `serial`, `uuid`, `mac` gewählt, der in beiden vorhanden ist. Die Werte werden normalisiert (Kleinbuchstaben, ohne `:`/`-`/Leerzeichen) und verglichen.
2. Haben beide keinen dieser Schlüssel, wird `ip` bzw. `host` verglichen.
3. Ist nichts vergleichbar, gilt die Identität als passend. Das ist ein Kompromiss für USB- und Dateiziele.

Bei einer Abweichung wird die Route nicht Kandidat, und die Readiness zeigt `identity_mismatch`.

## WebSocket `/ws/print-agent`

Die Authentifizierung entspricht `/ws/gateway` (Bearer-Device-Token oder `?token=`). Der Gateway muss `kind == "windows_agent"` haben, sonst wird mit Code 4401 geschlossen. `/ws/gateway` lehnt umgekehrt Tokens von `windows_agent` ab (Code 4401), damit ein Agent nie Legacy-Broadcasts erhält.

Alle Nachrichten haben das Format `{"type": ..., "id"?: ..., "rid"?: ..., "payload": {...}}`. `rid` korreliert Anfrage und Antwort.

### Agent → Cloud

| type | payload | Antwort |
|---|---|---|
| `hello` | `{version, host_info:{host, os, machine_id}, open_jobs:[{job_id, lease_token, local_state}]}` | `config_sync`, danach je nicht mehr gültigem Job `abandon` |
| `heartbeat` | `{}` | – (`mark_seen`) |
| `inventory` | `{queues:[{queue, driver, port, host, identity:{…}, capabilities:{media[], duplex, color, trays[]}, status}]}` | – (`agent_info.queues`) |
| `readiness` | `{routes:[{route_id, ready, state, detail, identity:{…}, capabilities?:{…}}]}` | – (Route aktualisieren, `capabilities` in `Printer.capabilities` übernehmen, wenn gesetzt) |
| `lease_request` | `{max}` | `lease_grant {jobs:[…]}` mit gleicher `rid` |
| `lease_renew` | `{leases:[{job_id, lease_token}]}` | `abandon` für ungültige |
| `print_intent` | `{job_id, lease_token}` | `intent_result {job_id, ok, reason}` mit gleicher `rid` |
| `job_status` | `{job_id, lease_token, status, phase, spool_ref, error}` | `ack {id}`, wenn die Nachricht `id` hatte |
| `log_event` | `{level, message}` | – |
| `ping` | – | `pong` |

### Cloud → Agent

| type | payload |
|---|---|
| `config_sync` | `{agent_id, name, routes:[{route_id, printer_id, printer_name, local_queue, priority, render_mode, options, identity_check, defaults}], intervals:{heartbeat_s:30, readiness_s:30, lease_poll_s:15, renew_s:20}}` |
| `job_available` | `{job_ids:[…]}` (nur ein Hinweis, der Agent antwortet mit `lease_request`) |
| `lease_grant` | siehe oben |
| `intent_result` | `{job_id, ok, reason}` |
| `cancel_job` | `{job_id}` |
| `abandon` | `{job_id, reason}` |
| `ack` | `{id}` |

### Mehrere Worker

- Der Socket hängt an genau einem Worker. Alle Anfragen eines Agents verarbeitet dieser Worker als DB-Transaktion. Konflikte entscheiden bedingte UPDATEs.
- Gezielte Nachrichten an einen Agent (`job_available`, `cancel_job`, `config_sync`) laufen über `ws_bus.CH_GW` mit `{"kind": "agent", "org_id", "gateway_id", "message"}`. Jeder Worker stellt sie nur an seine lokal verbundenen Agents mit dieser `gateway_id` zu. Ohne Redis läuft die Zustellung direkt in der lokalen Registry.
- Hinweise dürfen verloren gehen, weil der Agent zusätzlich alle `lease_poll_s` Sekunden einen `lease_request` schickt.

## Bestehender Code: notwendige Eingriffe

| Stelle | Änderung | Grund |
|---|---|---|
| `print_dispatcher.dispatch_job` | Hat `job.printer_id` aktive Routen, wird `print_agent_service.enqueue_for_agents(db, job)` aufgerufen und `{"status": "queued"}` zurückgegeben. Sonst bleibt alles wie bisher. | einziger Verzweigungspunkt, alle Aufrufer bleiben kompatibel |
| `print_watchdog._pruefe_haengende_jobs` | zusätzlich `PrintJob.delivery == "push"` | Lease-Jobs übernimmt der Sweep, sonst entstünde ein falscher Ersatzdruck |
| `ui_gateway.cancel_print_job` | Bei `delivery == "lease"` und `lease_gateway_id` wird zusätzlich `cancel_job` gezielt an diesen Agent geschickt. | Ein Org-Broadcast erreicht Agents nicht. |
| `/ws/gateway` | lehnt `kind == "windows_agent"` ab | Trennung der Protokolle |
| `models/gateway.py` | `JOB_UNCERTAIN` + Label, neue Spalten, Modell `PrinterAgentRoute` | |

## Windows-Agent: lokale Zustandsmaschine

Jeder Zustand wird in SQLite persistiert, **bevor** die zugehörige Aktion startet.

```
granted → fetching → intent_pending → submitting → spooled → done | failed
                 ↘ failed(fetch)        ↘ abandoned (Intent abgelehnt)
submitting nach Neustart: Queue nach Dokumentname "ECPG-<job_id>-g<generation>" durchsuchen
   Treffer → spooled   |   kein Treffer → uncertain (nie automatisch neu drucken)
granted/fetching nach Neustart → verwerfen (die Cloud reiht nach Lease-Ablauf neu ein)
```

Statusmeldungen gehen über eine lokale Outbox mit `id` und werden wiederholt, bis die Cloud sie mit `ack` bestätigt.

## Migration und Rollout

1. Cloud-Deploy mit Migration 0289. Bestandsdaten bekommen `kind = 'linux'` und `delivery = 'push'`. Ohne Routen ändert sich kein Verhalten.
2. In der Web-UI einen „Windows-Druckagent“ je PC anlegen, einen Pairing-Code erzeugen und den Agent installieren (`install.ps1`, `pair`).
3. Der Agent meldet sein Inventar. In der UI wird die Queue einem Druckziel zugeordnet (neu oder bestehend), mit Priorität 10 für PC A und 20 für PC B.
4. Regeln und Druckdialog verwenden das Druckziel wie jeden anderen Drucker.
5. Rückweg: Routen deaktivieren. Das Druckziel fällt dann auf den Push-Pfad zurück, falls ihm ein Linux-Gateway zugeordnet ist.
