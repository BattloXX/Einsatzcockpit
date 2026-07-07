# Einsatzcockpit Print & Alarm Gateway (ECPG) – Implementierungsplan

Stand: 2026-07-07. Lokaler Docker-Container im Feuerwehrhaus, der die lokale Infrastruktur (W&T Com-Server als Alarmquelle, Netzwerkdrucker) mit der Einsatzcockpit-Cloud (Azure) verbindet: **serieller Alarmempfang → Einsatz-Anlage**, **zentral verwalteter Netzwerkdruck** (Automatik + manuell), **ausfallsicher** (Offline-Notdruck, lokaler Spool). Fachliche Referenz: Konzept `KONZEPT_Einsatzcockpit_Gateway.md`.

Zwei Codebasen:
- **Cloud** (`Einsatzcockpit`, dieses Repo): neue Entitäten, Gateway-API, WebSocket-Kanal, PrintDispatcher, Admin-UI, Druckbuttons.
- **Gateway** (`BattloXX/einsatzcockpit-gateway`, neues leeres Repo): Python/asyncio-Container, WSS-Client, CUPS-Druck, W&T-TCP-Client, Parser, lokaler Spool.

**Zentrale Erkenntnis (Wiederverwendung):** Der bestehende **SMS-Gateway** ist die exakte Blaupause. `SmsGatewayToken` (`app/models/user.py`), der token-authentifizierte WebSocket `/ws/sms-gateway` mit Per-Org-Registry und Request/Response über Pending-Futures (`app/routers/ws.py`, `dispatch_sms`), sowie `hash_api_key` (`app/core/security.py`) werden für den Druck-/Alarmkanal 1:1 gespiegelt. Einsatz-Anlage + Dedup nutzen `create_incident`/`find_matching_incident` aus dem LIS-Pfad (`app/services/lis/lis_sync.py`).

---

## Leitplanken (bestehende Konventionen)

- **Tenant-Doppelmuster (wichtig):** Web-UI-verwaltete Tabellen (`Gateway`, `Printer`, `PrintRule`, `PrintJob`, `AlarmIngest`) werden `TenantScoped` (Mixin zuerst: `class Printer(TenantScoped, Base)`), in `_TENANT_TABLE_NAMES` (`app/core/tenant.py`) eingetragen und im Tenant-Isolation-Test abgedeckt. **Aber:** die Gateway-zugewandten Endpunkte (`/pair`, `/ws/gateway`, `/alarms`, Artifact-Download) laufen **ohne** eingeloggten User/Tenant-Kontext — dort wie `SmsGatewayToken` verfahren: `set_tenant_context(db, None)` und **explizit** nach `org_id` filtern (aus dem Device-Token aufgelöst). Nie ungefiltert queren.
- **Modellmodul eager importieren** in `app/models/__init__.py` — bekannter Produktionsbug (500 bei Mapper-Init) wenn vergessen (siehe `[[project_einsatzleiter_mapper_init]]`).
- **Feature-Flag** nach UAS-/Objekt-Muster: `SystemSettings`-Key `gateway_module_enabled` UND `OrgSettings.gateway_module_enabled`, effektiv = beides an. Helfer in `app/services/gateway_service.py`, `request.state.gateway_enabled` in `_resolve_current_org` (`app/core/dependencies.py`), Route-Guard `require_gateway_enabled` (404) analog `app/routers/ui_uas.py`.
- **Sprache/Format:** Deutsch in UI/Kommentaren, **nur gerade ASCII-Anführungszeichen** in Code/Attributen (`"`/`'`), CSRF `_csrf` in POST-Formularen, kein `location.reload` nach HTMX (gezieltes Swap), WS-Broadcast für Multi-User-Sync, Mobile ≤760 px.
- **Zeitzonen:** DB = naive UTC, Anzeige über `|local_datetime` / `format_local_datetime(.., org)`; nie rohes `.strftime()` auf DB-Datetimes.
- **Kein npm am Dev-Rechner:** neues CSS händisch in `app/static/css/tailwind.input.css` UND `app/static/css/app.css` (identisch).
- **Migrationskette:** Head aktuell `0140` (`0140_wasserstelle.py`); neue Migrationen ab `down_revision = "0140"`, reine Expand-Migrationen (neue Tabellen/Spalten mit `server_default`).
- **Gateway-Repo:** eigenständig, kein Einsatzcockpit-Import. Kommuniziert ausschließlich über die dokumentierte HTTPS/WSS-API. Outbound-only.

---

## 1. Cloud-Datenmodell (neu)

Alle Modelle in **`app/models/gateway.py`** (+ Import in `app/models/__init__.py`, UI-Tabellen in `_TENANT_TABLE_NAMES`). FK-Kaskaden wie im Bestand.

### 1.1 Gateway & Printer (Migration 0141, PR1)

**`gateway`** (TenantScoped):

| Spalte | Typ | Bemerkung |
|---|---|---|
| id | Integer PK | |
| org_id | FK fire_dept.id | via TenantScoped |
| name | String(150) | z. B. „Gerätehaus" |
| standort | String(200) null | |
| device_token_hash | String(64) null unique | gesetzt nach Pairing (`hash_api_key`), rotierbar |
| pairing_code_hash | String(64) null | Einmal-Code, `hash_api_key` |
| pairing_expires_at | DateTime UTC null | Code 10 min gültig |
| status | String(20) default `unpaired` | `unpaired`/`online`/`offline` (abgeleitet aus `last_seen_at`) |
| last_seen_at | DateTime UTC null | |
| version | String(40) null | aus `hello` |
| serial_connected | Boolean default False | W&T-Verbindungsstatus (aus `serial_status`) |
| wut_config | JSON | `{host, port, datagram_strategy, idle_ms, charset, notfalldruck_printer_id}` |
| parser_config | JSON | `{parser: "rfl_vbg", regex_set: {...}, version}` — versioniert |
| erstellt_am / aktualisiert_am | DateTime UTC | |

Ein Gateway gehört zu genau einer Org; mehrere Gateways je Org erlaubt. Token widerrufbar (`device_token_hash = NULL`, Status → `unpaired`).

**`printer`** (TenantScoped): id, org_id, gateway_id FK CASCADE, name String(150) (frei, „Florianstation"), modell String(150) null, uri String(300) (`ipp://<ip>/ipp/print`), identity JSON (`{serial, mac, uuid, ip}`), capabilities JSON (`{duplex, color, media[], …}`), defaults JSON (`{duplex, color, media:"A4"}`), aktiv Boolean default False, status JSON (`{reachable, state, toner_pct, paper, checked_at}`), discovered_at null, activated_at null. Index `(org_id, gateway_id)`. Identität bevorzugt Serial/UUID, sonst MAC, sonst IP.

**Flags:** `system_settings` + `gateway_module_enabled`; `org_settings` + `gateway_module_enabled` Boolean NOT NULL server_default `0`, + `gateway_offline_alert_min` Integer server_default `15`.

### 1.2 PrintJob & Artifact-Signatur (Migration 0142, PR1)

**`print_job`** (TenantScoped): id, org_id, gateway_id FK, printer_id FK SET NULL, source String(20) (`manual`/`rule`), rule_id null, incident_id FK SET NULL null, gsl_id (major_incident) null, objekt_id null, document_type String(40) (`einsatzinfo`/`gsl_lageblatt`/`alarm_rohtext`/`objektblatt`/`objekt_dokument`), artifact_ref String(120) (interner Render-Key, nicht die URL), options JSON (`{copies, duplex, color, media}`), status String(20) (`queued`/`sent`/`printing`/`done`/`failed`/`canceled`), idempotency_key String(120) UNIQUE, error String(500) null, created_by_id FK user null, erstellt_am / aktualisiert_am. Indizes `(org_id, incident_id)`, `(org_id, gateway_id, status)`.

`idempotency_key` = `sha256(f"{rule_id|manual}:{einsatz/gsl/objekt}:{document_type}:{printer_id}")` — garantiert „max. einmal automatisch je (Quelle, Regel, Dokument, Drucker)". Manueller Druck: `manual:{uuid}` (immer eindeutig).

**Artifact-Auslieferung:** kein DB-Modell nötig. Kurzlebige signierte URL `GET /api/v1/print/artifacts/{job_id}?exp=…&sig=…`. Signatur via `itsdangerous`-Muster analog `sign_session`/`unsign_session` (`app/core/security.py`) mit eigenem Salt `ecpg-artifact`, TTL 5 min. Der Endpoint rendert das PDF **on demand** aus `document_type` + Job-Kontext (kein Persistieren des PDF in der Cloud) und liefert `application/pdf`. Auth = gültige Signatur (kein Session-/API-Zugriff).

### 1.3 AlarmIngest (Migration 0143, Phase 3)

**`alarm_ingest`** (TenantScoped): id, org_id, gateway_id FK, raw_hash String(64) UNIQUE (sha256 des Rohtexts → Idempotenz gegen Retries), raw_text Text, charset String(20), parsed JSON null, parse_status String(20) (`parsed`/`parse_failed`), einsatz_id FK incident SET NULL null, dedup_action String(20) null (`created`/`merged_lis`/`merged_api`), received_at DateTime UTC. Index `(org_id, received_at)`.

`wut_config`/`parser_config` liegen an `gateway` (1.1); die versionierten Regex-Sets kommen so aus der Cloud-Config zum Gateway (Parser-Update ohne Container-Update).

### 1.4 PrintRule (Migration 0144, Phase 4)

**`print_rule`** (TenantScoped): id, org_id, name String(150), aktiv Boolean, trigger String(30) (`einsatz_created`/`einsatz_updated`/`gsl_created`/`gsl_lage_updated`/`alarm_serial_received`/`manual_only`), filters JSON (`{min_alarmstufe, stichwort[], nur_bma, zeitfenster}`), documents JSON (Liste Dokumenttypen), objekt_elements JSON (Liste, s. u.), printer_ids JSON (1..n), fallback_printer_id FK SET NULL null, options JSON (`{copies, duplex, color}`), sort_order Integer. Index `(org_id, trigger, aktiv)`.

**Dokumenttypen (Cloud-Rendering):** `einsatzinfo` (bestehende Einsatzinfo-Pipeline, Druckvariante), `gsl_lageblatt`, `alarm_rohtext`.
**Objekt-Elemente (aus Objektverwaltung):** Feuerwehrplan, BMA-Laufkarten, Hydranten-/Löschwasserplan, Zufahrtsplan, Ansprechpartner-Liste, taktische Karte; generisch: Objekt-Dokumentseiten mit Flag `bei_einsatz_drucken` (existiert bereits: `objekt_dokument_seite.bei_einsatz_drucken`). Nachzügler-Logik: Objekt erst nach Einsatz-Anlage zugeordnet → Regel feuert für Objekt-Elemente einmalig nach (dedupliziert über `idempotency_key`).

### 1.5 Migrationsübersicht

| Nr | Datei | Inhalt | PR |
|---|---|---|---|
| 0141 | `0141_gateway_printer.py` | gateway, printer; System-/OrgSettings-Flags + offline_alert | PR1 |
| 0142 | `0142_print_job.py` | print_job | PR1 |
| 0143 | `0143_alarm_ingest.py` | alarm_ingest | PR6 (Phase 3) |
| 0144 | `0144_print_rule.py` | print_rule | PR7 (Phase 4) |

---

## 2. Gateway-lokales Datenmodell (SQLite, im Container)

`/data/gateway.db` (Volume). Kein Alembic — schlichte `CREATE TABLE IF NOT EXISTS`-Migration beim Start.

- **`spool_jobs`**: id, job_id (Cloud-UUID) UNIQUE, printer_uri, artifact_url, options_json, pdf_path null, status (`pending`/`downloading`/`printing`/`done`/`failed`), attempts, next_retry_at, error, created_at, updated_at. Überlebt Neustarts.
- **`raw_alarms_ring`**: id, received_at, bytes BLOB, charset, forwarded (0/1), raw_hash. Ringpuffer letzte 500 (ältere löschen) — unverzichtbar fürs Parser-Debugging.
- **`config_cache`**: singleton-JSON (letzter `config_sync`: Drucker, W&T, Parser, Notfalldruck-Drucker) — für Offline-Notdruck & Reconnect.
- **`kv`**: `device_token`, `gateway_id`, sonstige Kleinwerte.

---

## 3. WebSocket-Protokoll (Cloud ↔ Gateway)

Gespiegelt auf `/ws/sms-gateway`. Neuer Endpoint **`/ws/gateway`** in `app/routers/ws.py`, Bearer-Token (`Authorization`-Header oder `?token=`), `hash_api_key` gegen `gateway.device_token_hash`. Eigene Registry `_print_gateways: dict[int, list[WebSocket]]` + `_job_pending: dict[str, asyncio.Future]` (analog `_sms_gateways`/`_sms_pending`). Nachrichten JSON `{type, id, ts, payload}`, jede mit `ack`; Cloud queued unbestätigte `print_job`/`config_sync` und stellt bei Reconnect erneut zu (at-least-once, Gateway dedupliziert via `id`).

**Cloud → Gateway:** `config_sync` (Drucker, W&T, Parser, Notfalldruck), `print_job {job_id, artifact_url, printer_uri, options}`, `cancel_job {job_id}`, `discover_printers`, `probe_printer {ip}`, `test_page {printer_id}`, `update_available {version, changelog}`.

**Gateway → Cloud:** `hello {version, host_info}` (→ danach `config_sync`), `heartbeat` (30 s), `printer_report [vorschläge/status]`, `job_status {job_id, status, error?}`, `serial_status {connected}`, `alarm_notice {raw_hash}` (Signal; verbindlicher Ingest bleibt REST), `log_event`.

**Helper Cloud-seitig** (Muster `dispatch_sms`/`is_sms_gateway_connected`): `dispatch_print_job(org_id, job_id, payload)`, `is_gateway_connected(org_id)`, `push_config_sync(org_id)`. `broadcast_org(org_id, {"type":"gateway_status", …})` (`app/services/broadcast.py`) hält die Admin-UI live.

---

## 4. Cloud-Architektur (neue/geänderte Dateien)

**Modelle:** `app/models/gateway.py` (+ `__init__.py`, `_TENANT_TABLE_NAMES`).

**Services:**
- `app/services/gateway_service.py` — Flags/Guard-Helfer, Pairing (Code erzeugen/prüfen, Token setzen/rotieren/widerrufen), Config-Assembly für `config_sync`, Status-Ableitung online/offline, Offline-Alert (in `task_reminder`-Loop).
- `app/services/print_dispatcher.py` — **Kernstück:** hört auf Domain-Events, wertet `PrintRule` aus (Trigger + Filter), löst Dokumente/Objekt-Elemente auf, erzeugt `PrintJob` (idempotent) und ruft `dispatch_print_job`. Nachzügler-Hook bei Objektzuordnung.
- `app/services/print_artifact_service.py` — mappt `document_type` → Renderer, signierte URL erzeugen/prüfen. Renderer wiederverwendet: `render_incident_pdf` (`pdf_service.py`) für `einsatzinfo`; GSL-Lageblatt aus dem bestehenden Major-Incident-PDF-Pfad; Objektblatt aus `objekt_pdf_service.py`; Objekt-Dokumentseiten → bestehende Einzel-/Sammel-PDF-Assembly; `alarm_rohtext` → schlichtes WeasyPrint-Template `pdf/alarm_rohtext.html`.
- `app/services/serial_alarm_service.py` (Phase 3) — Alarm-Ingest: Idempotenz über `raw_hash`, `create_incident` + `find_matching_incident` (Dedup gegen LIS/API, Muster `_get_or_link_incident`), `source="serial"`, Einsatznummer in `report_text`/externes Feld, `broadcast_org`. Bei `parse_failed`: „unklassifizierter Alarm" + Rohtext.

**Router:**
- `app/routers/gateway_api.py` — `POST /api/v1/gateway/pair` (Code → Device-Token), `POST /api/v1/gateway/alarms` (idempotenter Ingest), `GET /api/v1/print/artifacts/{job_id}` (signiert). WS `/ws/gateway` liegt in `ws.py`.
- `app/routers/ui_gateway.py` — Admin-UI (Gateway-Liste/Detail, Pairing-Code, Drucker, „Netzwerk durchsuchen"/„Per IP", Testseite, Druckregeln-CRUD), Guard `require_gateway_enabled`. Registrierung in `app/main.py`, Nav-Eintrag „Gateway/Druck" nur bei `request.state.gateway_enabled` (org_admin).
- **Erweiterung bestehender Views:** Drucken-Button + Dialog auf Einsatz-Detail (`ui_incident.py`/Board), GSL-Detail (`ui_major_incident.py`), Objekt-Detail/-Dokument (`ui_objekt*.py`).

**Templates** (`app/templates/gateway/`): `liste.html`, `detail.html`, `_drucker.html`, `_druckregeln.html`, `_regel_form.html`, `_druckhistorie.html`, `pairing.html`; Druck-Dialog-Partial `app/templates/_print_dialog.html` (wiederverwendbar); PDF-Templates `app/templates/pdf/alarm_rohtext.html`.

**Statisch:** minimales `app/static/js/print_dialog.js` (Drucker-/Optionen-Auswahl, „zuletzt verwendet" in localStorage, Toast-Feedback + Retry).

**Rollen:** Druckregeln/Gateway/Drucker verwalten = `org_admin`. Manuell drucken = jeder Nutzer mit Einsatz-/Objektzugriff. Kein neues Rollengewicht nötig (org_admin genügt) — offene Frage 2.

**Domain-Event-Hooks (Phase 4):** `create_incident_api` (`api_v1.py`, nach commit, neben `_geocode_incident`), LIS `_get_or_link_incident`, GSL-Anlage/Lage-Update, serieller Alarm, Objekt↔Einsatz-Verknüpfung (Nachzügler). Alle rufen `print_dispatcher.on_event(trigger, context)`.

---

## 5. Gateway-Container (`einsatzcockpit-gateway`, neues Repo)

### 5.1 Tech-Stack & Layout
Python 3.12 asyncio; `websockets`, `httpx`, `pycups` (+ CUPS im Container, Treiber `everywhere`), `zeroconf`, `pysnmp`, `aiosqlite`, `reportlab` (Offline-Notdruck). Ein Container, Prozess-Supervisor (asyncio-Tasks + CUPS als Subprozess via `supervisord`). Image `ghcr.io/battloxx/einsatzcockpit-gateway`, Multi-Arch amd64+arm64. `network_mode: host` (mDNS/SNMP).

```
einsatzcockpit-gateway/
  ecpg/
    __init__.py
    main.py            # asyncio-Supervisor, Task-Orchestrierung
    settings.py        # ENV: ECPG_CLOUD_URL, ECPG_PAIRING_CODE, TZ
    cloud_connector.py # WSS-Client: Reconnect (1s→60s), Heartbeat 30s, hello, ack/dedup
    pairing.py         # POST /pair → device_token (ENV oder Statusseite)
    print_manager.py   # pycups: Queue-Sync aus config, Job: download→spool→CUPS→poll
    printer_discovery.py # zeroconf mDNS + pysnmp + optional TCP-Scan 631/9100
    serial_ingest.py   # TCP-Client W&T, Datagramm-Erkennung, Ringpuffer
    alarm_parser.py    # AlarmParser-Interface + RflVbgParser (Regex aus config)
    offline_print.py   # reportlab-Notdruck des Rohtexts
    spool.py           # SQLite: spool_jobs, raw_alarms_ring, config_cache, kv
    status_server.py   # aiohttp :8631, read-only + /healthz + Pairing-Eingabe
    protocol.py        # Nachrichten-Typen, ack/dedup
  tests/
    test_parser_rfl.py       # anonymisierte Real-Mitschnitte
    test_serial_datagram.py  # Idle-Timeout/FormFeed
    test_spool_retry.py
    fake_comserver.py        # TCP-Server-Testtool, spielt Mitschnitte ab
  Dockerfile               # CUPS + Python, non-root wo möglich
  docker-compose.yml       # network_mode:host, Volume ecpg-data
  .github/workflows/build.yml # Multi-Arch → GHCR, semver
  README.md
```

### 5.2 Kernverhalten
- **Cloud Connector:** persistente WSS, exponentielles Reconnect, Heartbeat; bei (Re-)Connect `hello` → vollständiger `config_sync`; ack/dedup via `id`.
- **Print Manager:** CUPS-Queues ausschließlich programmatisch aus Cloud-Config (Single Source of Truth). Job: signierte PDF-URL laden → SQLite+Datei spoolen → CUPS → Status pollen → `job_status`. Retry mit Backoff (Default 5×/10 min) → `failed` → optional Fallback-Drucker. Spool-PDF nach Erfolg + 24 h löschen.
- **Serial Ingest:** TCP-Client auf `WUT_HOST:WUT_PORT` (Default 8000), Keepalive; Datagramm-Ende per Idle-Timeout (Default 2 s) oder Form Feed `\x0c`; Charset CP850/Latin-1 (mit echten Mitschnitten verifizieren); jedes Datagramm roh in Ringpuffer.
- **Alarm Parser:** `RflVbgParser` mit Regex-Set aus `parser_config`. Flow: `POST /alarms` (Rohtext + geparste Felder, idempotent) → Cloud legt Einsatz an → Druckregeln → `print_job`. Parse-Fehler: trotzdem melden (`parse_failed`) + lokaler Rohtext-Notdruck (nie einen Alarm verschlucken).
- **Discovery:** periodischer mDNS-Scan + optional aktiver Subnetz-Scan + SNMP (Modell/Serial/Status); Fund = **Vorschlag** an Cloud (`printer_report`), Aktivierung nur im Web-UI. Statusüberwachung aktiver Drucker alle 60 s.
- **Statusseite:** `http://<gw>:8631/` read-only (Cloud/W&T-Status, letzte Alarme roh, Spool, Druckerstatus, Version, Log-Tail) + `/healthz`; Pairing-Code-Eingabe als Alternative zur ENV.

---

## 6. Ausfallsicherheit & Sicherheit

| Szenario | Verhalten |
|---|---|
| Cloud weg, Alarm kommt seriell | **Offline-Notdruck** (reportlab, Rohtext) auf konfigurierten Notfalldrucker (aus gecachter Config); Alarm gepuffert, bei Reconnect nachgemeldet (`raw_hash`-Idempotenz verhindert Doppel-Einsatz) |
| Drucker offline | Job im Spool, Retry+Backoff, dann Fallback-Drucker, Status im UI |
| W&T weg | Reconnect-Loop + `serial_status:false` → Warnung im UI + Admin-Push |
| Gateway-Neustart | Spool + Config-Cache in SQLite → Jobs/Notdruck überleben |
| Doppelalarm (seriell + LIS) | Cloud-Dedup über Einsatznummer/Zeitfenster (`find_matching_incident`) + `idempotency_key` beim Druck |

**Sicherheit:** ausschließlich ausgehende Verbindungen (WSS/HTTPS/TCP/IPP), keine Portfreigaben. Device-Token hash-gespeichert (`hash_api_key`), pro Gateway, widerrufbar, Scope nur Gateway-Endpunkte. Signierte, kurzlebige (5 min) PDF-URLs — kein generischer API-Zugriff. Container non-root wo möglich, read-only FS außer `/data`. Keine personenbezogenen Daten dauerhaft (Ringpuffer begrenzt, Spool-PDFs +24 h gelöscht). W&T-Web-Config im LAN per Passwort/VLAN sichern (Doku).

---

## 7. Deployment

```yaml
# docker-compose.yml (Feuerwehrhaus)
services:
  ecpg-gateway:
    image: ghcr.io/battloxx/einsatzcockpit-gateway:latest
    network_mode: host
    restart: unless-stopped
    environment:
      ECPG_CLOUD_URL: https://app.einsatzcockpit.com
      ECPG_PAIRING_CODE: "..."   # nur Erststart
      TZ: Europe/Vienna
    volumes:
      - ecpg-data:/data
volumes:
  ecpg-data:
```

Auto-Update Watchtower-kompatibel + `update_available`-Hinweis im UI. CI: GitHub Actions → Multi-Arch → GHCR, semver-Tags. Healthcheck `/healthz`. Cloud-Deploy: WeasyPrint (GTK) vorhanden; Alarm-Rohtext-Template braucht keine Zusatzabhängigkeit.

---

## 8. PR-Phasenplan

Reihenfolge = Konzept-Phasen; PR1–3 = Phase 1 (Druckpfad end-to-end lauffähig, ohne Discovery/Alarm).

| PR | Repo | Inhalt | Tests | Aufwand |
|---|---|---|---|---|
| **PR1 – Cloud: Gateway+Print-Grundmodul** | Cloud | Mig 0141/0142, Modelle, Tenant-Reg, Flags/Guard, Gateway-CRUD + Pairing-Code/-Token (Muster `SmsGatewayToken`+`ui_admin.py:2621`), `/ws/gateway` (Registry+dispatch, Muster `dispatch_sms`), `config_sync`, `PrintJob` + signierte Artifact-URL, `print_artifact_service` (nur `einsatzinfo`), Drucker „per IP hinzufügen" (Cloud-Seite, ohne Discovery), Admin-UI Gateway-Liste/Detail | Pairing (Code→Token, Ablauf, Rotation, Widerruf), Token-Auth WS (gültig/ungültig/widerrufen), Artifact-Sig (gültig/abgelaufen/manipuliert), Job-Idempotenz, Tenant-Isolation (Zwei-Org), Flag/Guard | 6 T |
| **PR2 – Gateway-Container-Skeleton** | Gateway | Repo-Bootstrap, `cloud_connector` (WSS/Reconnect/Heartbeat/hello/ack), `pairing`, `print_manager` (CUPS-Queue aus config, IPP-Job, Status-Poll, Spool), `spool` (SQLite), `status_server` + `/healthz`, Dockerfile + compose + CI (Multi-Arch GHCR) | `test_spool_retry`, CUPS gegen PDF-Dummy-Drucker in CI, Reconnect-Unit, ack/dedup | 7 T |
| **PR3 – Manueller Druck (E2E)** | Cloud | Druck-Dialog-Partial + `print_dialog.js`, Buttons auf Einsatz/GSL/Objekt/Dokument, `document_type`-Renderer (gsl_lageblatt, objektblatt, objekt_dokument, alarm_rohtext), Job-Status-Feedback (WS → Toast/Retry), Druckhistorie/Audit je Einsatz | Renderer-Pfade (WeasyPrint gemockt auf Win), Job-Status-Flow, Historie, Rollen (Zugriff), Isolation | 5 T |
| **PR4 – Discovery & Druckerverwaltung** | beide | Gateway: `printer_discovery` (mDNS/SNMP/Scan), `probe_printer`, `test_page`, Statusüberwachung 60 s. Cloud: `printer_report`-Verarbeitung, Vorschlags-Workflow (übernehmen/ignorieren), „Netzwerk durchsuchen"-Button, Testseite, Live-Druckerstatus, Fallback-Drucker-Feld | Gateway: Discovery-Parsing (SNMP/IPP-Attr gemockt). Cloud: Vorschlag→aktiv, Status-Update, IP-Wechsel-Erkennung | 6 T |
| **PR5 – Härtung Druckpfad** | beide | Retry/Backoff end-to-end, Fallback-Drucker-Ausführung, Offline-Spool-Nachhol-Logik, Spool-PDF-Cleanup +24 h, Admin-Benachrichtigung Gateway/Drucker offline (`task_reminder`), Token-Rotation-UI | Retry-Zähler, Fallback-Auslösung, Offline→Reconnect-Nachholung, Offline-Alert (kein Doppelversand) | 4 T |
| **PR6 – Serieller Alarm** | beide | Mig 0143. Gateway: `serial_ingest` (TCP/Datagramm/Ringpuffer), `alarm_parser` (RflVbg, Regex aus config), `offline_print`. Cloud: `POST /alarms` (idempotent), `serial_alarm_service` (Einsatz-Anlage + Dedup gegen LIS/API via `find_matching_incident`), `serial_status`-Warnung, `parse_failed`-Fluss | Gateway: Parser gegen anonymisierte Mitschnitte, Datagramm (Idle/FormFeed), Charset, `fake_comserver`. Cloud: raw_hash-Idempotenz, Dedup-Merge vs. neu, parse_failed→unklassifiziert, Isolation | 7 T |
| **PR7 – Druckregeln** | Cloud | Mig 0144, `PrintRule` + CRUD-UI (Aktiv-Toggle, Sortierung), `print_dispatcher` auf Domain-Events (Einsatz/GSL/Alarm), Objekt-Elemente inkl. `bei_einsatz_drucken` + Nachzügler-Logik, Automatikdruck bei Einsatz-/GSL-Anlage, Dedup-Garantie | Trigger×Filter-Matrix, Dedup (LIS+seriell parallel), Objekt-Nachzügler (einmalig), Fallback in Regel, Isolation | 6 T |
| **PR8 – Abschluss/Doku** | beide | `update_available`-Hinweis + Changelog-UI, Probealarm-Simulation (Testflag durch ganze Kette), Setup-Assistent im Web-UI, Betriebs-/Installationsdoku (W&T-Konfig, DHCP-Reservierung, poppler n. z.) | Probealarm-E2E (seriell simuliert→Parser→Einsatz(Test)→Druck), Doku-Review | 4 T |

**Summe: ~45 Personentage** (+ ~15 % Puffer: CUPS/IPP-Realgeräte, W&T-Charset-Verifikation, Multi-Arch-Build, WeasyPrint-Fallback).

Erweiterungsideen (nicht eingeplant): Alarmmonitor-Ansicht (Kanal/Config existieren), Toner-/Papierwarnungen, Zeitprofile für Regeln, W&T Web-IO (Tore/Licht), Kurz- vs. Vollinfo-Ausdrucke, E-Mail/Drive-Export als weiterer Regel-Kanal.

---

## 9. Teststrategie

- **Cloud:** flach in `tests/`, `test_gateway_pr{n}.py` (UAS-/Objekt-Schema), sqlite-Conftest, `pytest tests/ -v`. Zwei-Org-Isolationsfixture je PR (Org B sieht keine Gateways/Drucker/Jobs/Regeln/Alarme von Org A; Artifact-URL fremder Org → 403/404). Flag/Guard (System aus → 404 trotz Org-Flag). WeasyPrint auf Windows gemockt. WS-Auth und `dispatch_*` mit Fake-WebSocket.
- **Gateway:** `pytest tests/`. Serial-Ingest gegen `fake_comserver` (echte Mitschnitte). Druckpfad gegen CUPS-PDF-Dummy-Drucker in CI. Parser-Units mit anonymisierten Real-Mitschnitten. Spool-Retry/Offline-Nachholung.

---

## 10. Offene Fragen (vor PR1 zu entscheiden)

1. **Cloud-Host/URL des Gateways:** `ECPG_CLOUD_URL` = produktive App-Domain? Ableitung `effective_public_base_url` (`app/config.py`) für Artifact-URLs bestätigen.
2. **Rollenmodell:** genügt `org_admin` für Gateway-/Drucker-/Regel-Verwaltung, oder eigene Rolle `druck_admin`? (Empfehlung: vorerst org_admin, keine neue Rolle.)
3. **Pairing-Entität:** Pairing-Felder direkt an `gateway` (dieser Plan) vs. separate Kurzlebig-Tabelle? (Empfehlung: an `gateway`, einfacher.)
4. **Einsatznummer serieller Alarm:** eigenes Feld am `incident` (analog `lis_operation_number`) oder nur in `report_text`? Beeinflusst Dedup-Präzision gegen LIS.
5. **W&T-Charset & Datagramm-Strategie:** mit echten Mitschnitten der RFL Vorarlberg verifizieren (CP850 vs. Latin-1, Idle-Timeout vs. Form Feed) — vor PR6 Mitschnitt beschaffen.
6. **Parser-Regex-Set:** initiales Format der RFL Vorarlberg als versioniertes `parser_config` — Beispiel-Alarmdruck nötig.
7. **GSL-Lageblatt-Renderer:** existierender Major-Incident-PDF-Pfad wiederverwendbar oder eigenes Druck-Template? (in PR3 klären).
8. **Notfalldrucker-Auswahl:** Teil von `wut_config` (`notfalldruck_printer_id`) — bestätigt.

---

## 11. Aufwandsschätzung

| PR | Titel | Repo | Tage |
|---|---|---|---|
| 1 | Gateway+Print-Grundmodul | Cloud | 6 |
| 2 | Gateway-Container-Skeleton | Gateway | 7 |
| 3 | Manueller Druck (E2E) | Cloud | 5 |
| 4 | Discovery & Druckerverwaltung | beide | 6 |
| 5 | Härtung Druckpfad | beide | 4 |
| 6 | Serieller Alarm | beide | 7 |
| 7 | Druckregeln | Cloud | 6 |
| 8 | Abschluss/Doku | beide | 4 |
| | **Gesamt** | | **45** (+15 % Puffer) |
