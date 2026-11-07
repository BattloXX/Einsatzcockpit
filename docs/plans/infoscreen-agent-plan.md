# ECP – Ubuntu-Infoscreen-Agent (Kiosk, Zeitpläne, Monitorsteuerung, Auto-Update)

Stand: 2026-11-07. Umsetzungsplan für Codex auf Basis des Konzepts
`Einsatzcockpit_Infoscreen_Agent_Konzept_Claude_Code.md` und einer Code-Erkundung des
**bestehenden** Stands. Es wird keine zweite Infoscreen-Fachlogik gebaut: Der Agent steuert nur
Kiosk-Browser, Monitor-Strom, Zeitplan und Updates; Einsatz-/GSL-Darstellung bleibt in den
vorhandenen Infoscreen-Seiten.

## 0. Ausgangslage (aus dem Code geprüft)

| Baustein | Ist-Zustand | Fundstelle |
|---|---|---|
| Alarm-Infoscreen | Token-URL `/infoscreen/alarm/{token}`, Daten `…/daten` (Modus `idle`/`gsl`/`alarm`), Vorrang **GSL > Einsatz > Idle** ist schon serverseitig | `app/routers/ui_infoscreen_alarm.py:275,288,317-345` |
| Idle-Rotation | `_baue_idle_urls(db, token_row, wetter_url)` liefert URL-Liste (`InfoscreenUrl`, `dwell_sec`), die Seite rotiert per iframe | `ui_infoscreen_alarm.py:98`, `objekt/infoscreen_alarm.html:540-570` |
| Token | `AlarmInfoscreenToken` (Hash + Fernet-`token_enc`, `url_ids_json`, `zeigt_wetter`), `_token_org()` scopt | `app/models/objekt.py:1066`, `ui_infoscreen_alarm.py:69` |
| Browser-Kanal | `WS /ws/infoscreen/{token}` = Org-Kanal (`ORG_WS_OFFSET+org_id`), nur ping/pong; Seite lädt `…/daten` bei Event neu | `ui_infoscreen_alarm.py:705`, `services/broadcast.py:139` |
| Event-Bus | Redis-Hinweisbus mit Kanälen `ec:ws`, `ec:gw`, … | `app/services/ws_bus.py` |
| Pairing-Muster | `erzeuge_pairing_code` / `pair_gateway`, Code-Hash, TTL, Device-Token (nur Hash gespeichert) | `app/services/gateway_service.py:53-100`, `routers/gateway_api.py:78` |
| Geräte-WS-Muster | `/ws/gateway`, `/ws/print-agent` (Bearer-Device-Token, Presence) | `app/routers/ws.py:763,1200` |
| Weitere Infoscreens | Statistik `/infoscreen/statistik/{token}`, Wetter, Straßensperren | `ui_infoscreen_stats.py`, `ui_weather.py`, `ui_road_closure*.py` |
| Admin-Menü | Hauptpunkt „Infoscreens“ mit Tabs | `app/services/admin_navigation.py:251` |
| Windows-Updater (Referenz) | `UpdateService.cs`: **öffentliche** GitHub-Releases über `update_repo`, nur **SHA256SUMS** (keine Signatur), Pause bei laufenden Druckjobs, Update per `update.ps1`, Healthcheck, Rollback auf vorherige EXE, `update-failed.json` gegen Wiederholung. Kein Kanal, kein Canary | `Einsatzcockpit-Windows-Gateway/src/…/Update/UpdateService.cs`, README „Automatische Updates“ |
| Release-Pipeline ECP | `release.yml`, CalVer `vJJJJ.MM.TT` | `.github/workflows/` |
| Migrationen | Head `0354`; neue IDs sind reine Nummern (`0355`, …) | `alembic/versions/` |
| Repo | `BattloXX/Einsatzcockpit-Infoscreen-Agent` existiert **nicht**; `gh` ist als `BattloXX` angemeldet (Anlegen möglich) | `gh repo view` |

### Konsequenzen aus der Erkundung

1. Die Prioritätskette Einsatz/GSL liegt schon im Server. Der neue Resolver **ruft die vorhandenen Funktionen** (`_aktive_gsl`, aktiver Incident) auf bzw. zieht sie in einen Service (Verhalten unverändert), er dupliziert sie nicht.
2. Der Agent lädt **eine** URL pro Display: die vorhandene Alarm-Infoscreen-URL (Token). Die Seite schaltet Idle/GSL/Alarm selbst um. Der Agent schaltet nur **Monitor an/aus** und **Overrides** (andere Seite).
3. Zeitplan-Rotation ersetzt die Idle-URL-Liste dieser Seite (siehe 2.3), damit nichts am Frontend dupliziert wird.
4. Der Windows-Updater ist **kein** Vorbild für Signatur/Canary; übernommen werden nur Pause-bei-Aktivität, Healthcheck, Rollback, „fehlgeschlagene Version nicht wiederholen“.
5. Zeitzonen-/DST-Logik wird **nur im Server** implementiert (CLAUDE.md-Regel). Der Agent bekommt eine fertig berechnete UTC-Timeline (siehe 2.2) und hat keine eigene Wochenplan-Logik.

## 1. Leitentscheidungen (verbindlich, nicht neu aufrollen)

| # | Entscheidung |
|---|---|
| E1 | Agent in **Python ≥ 3.12** (Ubuntu 26.04 liefert neuere Versionen), asyncio, `websockets`, `httpx`, `cryptography` (Ed25519). Auslieferung als `.deb` mit eigenem venv unter `/opt/ec-infoscreen-agent/` (keine Abhängigkeit von System-Python-Paketversionen). CI baut im Container je Ziel-Ubuntu (24.04, 26.04). |
| E2 | Zwei Prozesse: `ec-infoscreen-agent.service` (System-Dienst, Nutzer `ecinfo`, Netz/Plan/Update-Entscheidung) und `ec-infoscreen-kiosk.service` (User-Dienst des Kiosk-Benutzers `ecinfo-kiosk`, startet Compositor/Chromium, Watchdog). Kommunikation über Unix-Socket `/run/ec-infoscreen/agent.sock` (JSON-Zeilen, Rechte 0660, Gruppe `ecinfo`). |
| E3 | **Kiosk-Compositor-Empfehlung: `sway` im Kiosk-Modus** (wlroots, Mehr-Monitor, `swaymsg output <name> power on|off`). Backends: `sway`, `gnome-mutter` (D-Bus `org.gnome.Mutter.DisplayConfig`, nur globaler PowerSaveMode), `x11` (xrandr/DPMS), `cec`, `ddc`, `logical_blank`. Reihenfolge der Auswahl wird beim Setup per Probe-ON/OFF validiert; nie ein Backend ohne erfolgreichen Rückkanal als „Strom aus“ melden. |
| E4 | Server ist Autorität. Konfiguration = versionierter Snapshot (`config_version`, ETag). Zustand = `revision` (monoton). WS-Events sind nur **Invalidate/Wake**; danach immer Snapshot holen. |
| E5 | Das Gerät bekommt eine **vorberechnete UTC-Timeline** der nächsten 72 h und folgt ihr offline. Kein Wochenplan-/DST-Code im Agent. |
| E6 | **Fail-safe**: Ist das Gerät > `offline_wake_after_min` (Default 5) ohne Serververbindung, schaltet es den Monitor **ein** und zeigt die zuletzt gültige Seite plus dezenten Offline-Hinweis, weil es Alarme nicht mehr erkennen kann. Pro Display abschaltbar. |
| E7 | Release-Verteilung über den **ECP-Server**: CI lädt signierte Artefakte per scoped CI-Token zum ECP hoch (Ablage `app_storage`), Geräte laden nur vom ECP (TLS), kein GitHub-PAT auf Geräten. Signatur Ed25519, öffentlicher Schlüssel im `.deb`, zweiter Notfallschlüssel vorgesehen (Rotation). |
| E8 | Updates laufen über einen kleinen root-Helfer `ec-infoscreen-updater` (systemd-oneshot, vom Agent per D-Bus/Socket nur **angestoßen**, kein freier Befehl). Der Helfer prüft Signatur, SHA256, Architektur, Paketname, Mindestversion **selbst** erneut. Nie Shell-Befehle vom Server. |
| E9 | Kein Browser-Flag `--ignore-certificate-errors`; Chromium mit eigenem Profil unter `/var/lib/ec-infoscreen/profile`, URL-Allowlist (Host muss ECP-Host sein oder in der Admin-Allowlist stehen). |

Offene Punkte, die **Codex nicht entscheiden darf** (siehe 6): Hardware-Messungen. Entschieden: GSL-Override = `org_admin`; Pilotgerät = Fahrzeughalle.

## 2. Server – Repo `BattloXX/Einsatzcockpit` (eigene PRs, Branch `feat/infoscreen-agent-*`)

### 2.1 Datenmodell (Migrationen ab `0355`, MariaDB-FK-Falle beachten: INT/BIGINT-Typen exakt spiegeln, Tests auch gegen MariaDB)

- `infoscreen_device`: `id`, `org_id`, `name`, `device_token_hash`, `pairing_code_hash`, `pairing_expires_at`, `status` (`pending`/`active`/`revoked`), `hostname`, `os_version`, `agent_version`, `channel` (`stable`/`beta`), `update_pinned_version`, `update_locked`, `last_seen_at`, `last_ip`, `agent_connection_id`, `agent_worker_id`, `agent_presence_expires_at`, `config_version`, `erstellt_am`.
- `infoscreen_display`: `id`, `org_id`, `device_id` (FK), `display_key` (stabil, z. B. `hall-main`), `edid_id` (Hersteller+Modell+Serial-Hash, Mapping-Hilfe), `name`, `alarm_token_id` (FK auf `alarm_infoscreen_token`, **UNIQUE**), `schedule_id` (FK), `power_mode` (`auto`/`logical_blank`/`output_power_save`/`hardware_standby`), `nachlauf_min` (Default 10), `offline_wake_after_min` (Default 5), `gsl_override_erlaubt` (bool), `aktiv`.
- `infoscreen_schedule`: `id`, `org_id`, `name`, `timezone` (Default `Europe/Vienna`), `ruhemodus` (`aus`/`schwarz`).
- `infoscreen_schedule_slot`: `id`, `schedule_id`, `weekdays` (Bitmaske), `start_time`, `end_time` (über Mitternacht erlaubt), `valid_from`/`valid_to` (optional), `power` (`on`/`off`), `rotation_json` (Liste `{url_id|builtin, dwell_sec}`), `priority`, `label`.
- `infoscreen_schedule_exception`: `schedule_id`, `datum`, `label`, `slots_ersatz` (`kein Eintrag` = ganzer Tag aus) – Feiertage/Ausnahmetage.
- `infoscreen_override`: `id`, `org_id`, `display_id` (NULL = alle Displays der Org), `kind` (`gsl_page`/`manual`), `url_kind` (`weather`/`lagekarte`/`einsatzstellen`/`statistik`/`custom_allowlisted`), `url_ref`, `von`, `bis` (Pflicht, max 8 h), `created_by`, `reason`, `aufgehoben_am`, `aufgehoben_von`.
- `infoscreen_device_event` (Audit + Diagnose, **keine Einsatzdetails**): `device_id`, `typ`, `revision`, `payload_json` (nur Codes/Zahlen), `ts`. Aufbewahrung 90 Tage über bestehenden Retention-Loop.
- `infoscreen_release`: `id`, `version`, `channel`, `arch`, `sha256`, `signature`, `size`, `min_version`, `rollout_percent`, `blocked`, `notes`, `created_at`, `storage_path`. Plattformweit (kein `org_id`), nur Sysadmin.
- `infoscreen_url_allowlist` (je Org): zusätzliche Hosts für Custom-URLs; Standard nur eigener ECP-Host.
- Alle org-gebundenen Tabellen `TenantScoped`; Public-/Geräte-Routen scopen selbst über `device.org_id` (CLAUDE.md „Tenant-Scoping“).

### 2.2 Resolver (rein funktional, `app/services/infoscreen_agent_resolver.py`)

`resolve(display, now_utc, lage) -> State` mit Prioritäten (höchste zuerst):

1. Gerät `revoked`/gesperrt → `mode=locked`, `power=on`, schwarze Sperrseite ohne Daten.
2. Aktiver `gsl_page`-Override (nur wenn `gsl_override_erlaubt` und Override gültig) → `mode=override`.
3. Aktive GSL → `mode=gsl`, `url=<Alarm-Token-URL>`, `power=on`.
4. Aktiver regulärer Einsatz (Status `active`) → `mode=incident`, `power=on`.
5. Nachlauf: Ende (Incident `closed`/GSL `ended_at`) liegt < `nachlauf_min` zurück → `mode=nachlauf` (wie 3/4, `valid_until=ende+nachlauf`).
6. Aktiver `manual`-Override → `mode=manual`.
7. Wochenplan-Slot (Ausnahmetag vor Wochentag; Überschneidung: höhere `priority`, dann spätere Startzeit) → `mode=schedule`, `power` und Rotation aus dem Slot.
8. Standard bei fehlendem Plan: `power=on`, Rotation des Tokens (heutiges Verhalten).

Ausgabe wie im Konzept (`revision`, `mode`, `display_power`, `url`, `reason`, `valid_until`, `config_version`) plus:
`timeline`: Liste `{from, until, power, rotation_ref}` der nächsten 72 h **nur aus Plan und Ausnahmen** (UTC, DST serverseitig aufgelöst), `update_allowed` (false bei GSL/Einsatz/Nachlauf), `server_time`.

- `revision` = monotoner Zähler je Display, erhöht sich nur bei geänderter Auflösung (Hash der Ausgabe ohne `server_time`). Tabelle/Spalte `infoscreen_display.revision`, Increment atomar.
- Aktivität (Einsatz/GSL) ausschließlich aus den bestehenden Abfragen (`_aktive_gsl`, `Incident.status == "active"`), in einen kleinen Service verschoben, `ui_infoscreen_alarm.py` ruft ihn weiter auf (Regressionstest: `/daten` unverändert).
- Rotation im Plan-Slot ersetzt `_baue_idle_urls` für dieses Display: `_baue_idle_urls` bekommt optional `slot_rotation` (nur wenn der Token zu einem Agent-Display gehört, sonst unverändert).

### 2.3 REST-API (`/api/v1/infoscreen-agent/…`, Device-Bearer-Token, Rate-Limits)

Vor Implementierung bestehendes Versionsschema (`/api/v1/gateway/…`) spiegeln.

| Methode/Pfad | Zweck |
|---|---|
| `POST /pair` | Code einlösen → `device_token`, `device_id` (wie `pair_gateway`, Code 8 Zeichen, TTL 15 min, Brute-Force-Drossel) |
| `GET /config` | Snapshot (Displays, Allowlist, Intervalle, Timeline, Update-Policy), `ETag`/`If-None-Match` → 304 |
| `GET /state?display=…` | Resolver-Ergebnis; ETag = `revision` |
| `POST /heartbeat` | alle 60–120 s: Agent-Version, OS, Displays mit tatsächlichem Strom-/Seitenzustand, Backend, `last_revision`, Fehlercodes. Keine Einsatzdaten, keine Seiteninhalte |
| `GET /update/manifest` | siehe 4.2 |
| `POST /update/result` | `installing`/`ok`/`failed`/`rolled_back` + Version + Fehlercode |
| `WS /ws/infoscreen-agent` | Bearer, Presence wie `/ws/print-agent`; Server sendet `{type: "invalidate", revision, reason}` und `ping`; max. eine Verbindung je Gerät |

- Ereignisse (`incident_started/changed/finished`, `gsl_started/changed/finished`, `config_changed`, `override_changed`, `update_available`) werden über `ws_bus` mit neuem Kanal verteilt (Muster `CH_GW`), damit **Multi-Worker** funktioniert. Auslöser: an den Stellen, an denen heute `broadcast_org` bzw. die GSL-/Incident-Statuswechsel passieren (Codex sucht die Hooks; nicht pollen, nicht ELVIS/LIS anfassen).
- Admin-Aktionen: Pairing-Code erzeugen, Gerät sperren/entsperren (Token wird sofort ungültig, WS getrennt), Token-Rotation, Display-Zuordnung.
- Rechte: Geräte/Pläne/Allowlist = `org_admin`. **GSL-Override** setzen/aufheben = ebenfalls `org_admin` (Entscheidung 2026-11-07, keine eigene Rolle); jeder Override schreibt Audit (`created_by`, `reason`).

### 2.4 Admin-UI (Pflichten aus CLAUDE.md)

- Neuer **Tab** „Geräte & Pläne“ im bestehenden Hauptpunkt „Infoscreens“ (`admin_navigation.py`, kein neuer Hauptpunkt), Unterseiten: Geräte, Displays, Zeitpläne (Wochenraster, Ausnahmetage, „Was zeigt Monitor X am Montag 10:00?“-Vorschau), Overrides (nur Einsatzleitung), Updates (Kanal, Pin, Sperre, Freigabe).
- Statusliste: online/offline, OS, Agent-Version, letzter Heartbeat, aktuelle Seite, Monitorstatus, Backend, Update-Fehler.
- Mobile ≤ 760 px, CSRF, HTMX-Swap statt Reload, Icons nur über Icon-System, Zeiten über `|local*`, nur ASCII-Anführungszeichen.
- Mitpflegen: `tests/test_admin_navigation.py`, `docs/architecture/admin-navigation.md`, Wiki-Seite `Administration-Navigation`, `CHANGELOG.md`.

### 2.5 Server-PR-Zuschnitt (sequenziell, je PR eigener Branch)

| PR | Inhalt | Abnahme |
|---|---|---|
| S1 | Modelle + Migration `0355`, Pairing, Device-Token, Sperren/Rotation, Cross-Org-Tests | pytest + MariaDB-Migrationsjob |
| S2 | Resolver + Timeline + `/config` `/state` + Regressionstest `/daten` unverändert | Tabellentest Prioritäten, DST 2026-03-29 / 2026-10-25, über Mitternacht, Feiertag |
| S3 | `/heartbeat`, `WS /ws/infoscreen-agent`, ws_bus-Kanal, Event-Hooks, Presence | Reconnect-Test, 2 Worker, Revision monoton |
| S4 | Admin-UI Geräte/Displays/Pläne + Navigation + Doku | Playwright-Smoke, mobile |
| S5 | Overrides (GSL-Einsatzleitung), Allowlist, Audit | Rechte-Matrix-Test |
| S6 | Release-Upload (CI-Token), Manifest, Rollout/Canary, Update-Policy, `/update/result`, Admin „Updates“ | 4.2-Tests |
| S7 | Wiki/Doku, MCP optional (nur lesend: Geräte-/Displaystatus), Changelog | – |

## 3. Agent – neues privates Repo `BattloXX/Einsatzcockpit-Infoscreen-Agent`

Anlegen mit `gh repo create BattloXX/Einsatzcockpit-Infoscreen-Agent --private` (Codex-Sandbox kann das nicht; **Claude legt Repo, Branch-Schutz und Secrets an**). Lizenz/README analog Windows-Repo.

Struktur: `src/ec_infoscreen_agent/` (`api.py`, `ws.py`, `state.py`, `timeline.py`, `store.py`, `display/` mit je einem Backend, `kiosk/`, `update/`, `cli.py`, `doctor.py`), `packaging/` (debian/, systemd-Units, sway-Kiosk-Config, polkit/sudoers-frei), `tests/`, `.github/workflows/build.yml`, `docs/`.

### Verhalten

- **Zustandsautomat**: Eingaben = letzter Snapshot (`/state`), Timeline, Verbindungsstatus, lokale Uhr. Ausgabe = je Display `(power, url)`. Übergänge idempotent; jede Änderung nur bei neuer `revision`; Reconnect → sofort `/config` + `/state`.
- **Polling-Fallback**: Standard 20 s (aus Config), `If-None-Match`/304, Jitter ±20 %, höchstens ein Request offen, bei Fehlern Backoff 20 s → 5 min. WS: Ping 25 s, Backoff mit Jitter 1 s → 60 s. Heartbeat 60–120 s (Config).
- **Persistenz**: `/var/lib/ec-infoscreen/state.json` atomar (tmp + `fsync` + `rename`), Rechte 0600, nur Konfiguration + Timeline + Token; **keine** Einsatzpayloads.
- **Display-Adapter**: Interface `capabilities()`, `power(display_key, on|off) -> verified`, `identify()`. Display-IDs aus EDID (Hersteller/Modell/Serial) statt Connector-Name; Mapping-Fallback Connector. Probe beim Setup: AUS, Rückkanal prüfen, AN, Rückkanal prüfen; erst bei Erfolg darf `power=off` je Display im Betrieb gelten, sonst automatisch `logical_blank` (Kiosk zeigt Schwarz) und im Heartbeat als „nicht verifiziert“ gemeldet.
- **Kiosk**: Chromium `--kiosk` mit separatem Profil je Display, Seite wird nur auf `url`-Wechsel neu geladen, Watchdog (Prozess tot, Seite hängt: Heartbeat über `/daten`-Erreichbarkeit der Seite, Renderer-Absturz) startet neu mit Backoff. Suspend/Hibernate/Bildschirmschoner/Idle-Sperre per systemd-Inhibit und Masking verhindern. Strg-Alt-Tasten/Kontextmenü/DevTools deaktivieren (Policy-Datei), Navigation nur auf Allowlist.
- **Sicherheit**: Agent und Kiosk laufen unprivilegiert; privilegierte Aktionen nur im Updater (E8). Token nicht auf Kommandozeile und nie im Log. TLS-Validierung immer an.
- **CLI**: `ec-infoscreen-agent setup` (Server-URL, Pairing-Code, Display-Erkennung, Monitor-Probe, Kiosk-Setup, Autostart, Diagnose), `doctor` (Netz, Zertifikat, Uhrzeit/NTP, Session-Typ, Backend, Probe, Chromium, Berechtigungen, Update-Stand), `status`, `unpair`.

### Agent-PR-Zuschnitt

| PR | Inhalt |
|---|---|
| A1 | Repo-Gerüst, `pyproject`, Lint/Tests, `debian/`, CI baut `.deb` (24.04 + 26.04 Container), README, Architekturentscheidungen |
| A2 | API-Client (Pairing, config/state/heartbeat, ETag), WS-Client mit Reconnect, Zustandsautomat + Timeline-Folger + Fail-safe, Store, Fake-Server für Tests |
| A3 | Display-Backends `logical_blank`, `sway`, `gnome-mutter`, `x11` (+ Fake-Backend für Tests), Probe, EDID-Mapping; `cec`/`ddc` nur hinter Konfigurationsschalter |
| A4 | Kiosk-Dienst (sway-Kiosk-Config, Chromium-Start, Watchdog, Inhibit, Unix-Socket zwischen Agent und Kiosk) |
| A5 | Setup-Assistent, `doctor`, systemd-Units, Postinst/Prerm (idempotent, Upgrade-fest), Doku Installation/Betrieb/Upgrade |
| A6 | Auto-Update (siehe 4) |
| A7 | E2E mit Fake-ECP, Ubuntu-VM-Smoke in CI soweit ohne echte Monitore möglich, Betriebs-/Fehlerhandbuch, Hardware-Abnahmeprotokoll-Vorlage |

## 4. Auto-Update

### 4.1 Agent-Seite
- Prüfung alle 6 h (+ `update_available`-Event + „Jetzt prüfen“), nur über ECP-Manifest, nur TLS.
- Entscheidungsregeln (Agent **und** Helfer): kein Update bei `update_allowed=false` (Einsatz/GSL/Nachlauf), Wartungsfenster (Default 02:00–04:30 lokal, vom Server geliefert), `update_locked`, Pin, ungeeignete Architektur, Version ≤ installiert, `blocked`, Version in lokaler `update-failed.json`. Ausnahme „kritisch“ nur, wenn das signierte Manifest `critical=true` trägt **und** der Admin die Ausnahme in der Org freigegeben hat.
- Ablauf: Download in Staging → SHA256 → Ed25519-Signatur über kanonisches Manifest (Version, Kanal, Arch, SHA256, Größe, `min_version`) → Helfer installiert `dpkg -i` (Vorgänger-`.deb` vorher nach `/var/lib/ec-infoscreen/previous/` kopiert, Konfiguration gesichert) → Dienste neu starten → Healthcheck (Server erreichbar, WS verbunden, Kiosk läuft, Display-Probe) binnen 120 s → bei Fehler Rollback auf Vorgänger-`.deb`, `update-failed.json`, `POST /update/result` (`rolled_back`).
- Stromverlust mitten im Update: Pending-Marker `/var/lib/ec-infoscreen/update.pending`; beim nächsten Start Healthcheck, sonst Rollback. Wiederholungsfest (idempotent).

### 4.2 Server/CI-Seite
- Agent-Repo-Workflow: Tests → Build `.deb` (amd64, optional arm64) → `SHA256SUMS` → Signatur mit Ed25519-Schlüssel aus GitHub-Secret (Schlüssel nie auf Geräten, Verfahren in `docs/release.md`) → GitHub-Release (privat, Archiv) → Upload zum ECP (`POST /api/v1/infoscreen-agent/releases`, scoped CI-Token, vom Sysadmin erzeugt und widerrufbar).
- ECP-Manifest je Gerät: Kanal, Pin, `rollout_percent` (stabiler Hash `device_id` → Bucket, damit dasselbe Gerät konsistent drinnen/draußen bleibt), `min_version`, `blocked`, `critical`, Download-URL (ECP, signiert/kurzlebig), Wartungsfenster.
- Admin: Kanal je Gerät, Pin, Sperre, „Jetzt prüfen“, Testgruppe freigeben, Release sperren (Rückzug).
- Trennung bleibt: Agent-Update ≠ Anzeigeinhalt (HTML kommt immer live vom ECP).

## 5. Tests (Pflicht je PR)

- Server: Prioritätstabelle (alle 8 Stufen + Kombinationen, Nachlauf-Rand), DST-Umstellung, Mitternacht-Slots, Ausnahmetage, `revision` monoton + idempotent, Pairing (Ablauf, Wiederverwendung, Brute-Force), Cross-Org (`tests/test_public_tenant_isolation.py`-Muster, Device-Token Org A sieht nichts von Org B), gesperrtes Gerät, URL-Allowlist/SSRF, Override-Rechte + Ablauf, WS-Reconnect + Snapshot-Autorität, 2-Worker-Fanout, Manifest/Canary-Bucket, Heartbeat ohne personenbezogene Daten, `/daten` unverändert.
- Agent: Zustandsautomat mit Fake-Uhr, Timeline offline, Fail-safe, Backoff/Jitter-Grenzen (kein 2-Sekunden-Dauerpolling), Atomic-Store (Abbruch mitten im Schreiben), Backends gegen Fake, Watchdog, Updater (kaputte Signatur, falscher Hash, falsche Arch, Downgrade, Stromverlust, Rollback, Aufschub bei Einsatz), kein Token im Log.
- Vor Merge je ECP-PR: `ruff check app/`, `mypy app/ --ignore-missing-imports`, `pytest -q -n auto` (nie zwei pytest-Prozesse gleichzeitig), MariaDB-Migrationsjob in CI; Agent-Repo: eigene CI.

## 6. Menschliche Gates (Codex darf sie nicht abhaken)

1. Repo-Anlage, Secrets (Signaturschlüssel, CI-Upload-Token), Branch-Schutz (Claude).
2. **Hardwarespike vor A3-Merge**: Auf dem Pilot-PC (Fahrzeughalle) prüfen, welche Session-/Compositor-Variante Monitor-Aus wirklich kann (sway-`output power`, Mutter `PowerSaveMode`, CEC/DDC). Ergebnis in `docs/hardware-matrix.md`; ohne Messung bleibt `logical_blank` Standard.
3. Aufwachzeit nach Monitor-Aus am realen Gerät messen und dokumentieren (keine zugesicherte Zeit).
4. Pilot in der Fahrzeughalle (Pilotgerät, Display-Key z. B. `fahrzeughalle`): Alarm im Standby, GSL-Override, Netz weg, Strom weg, Update + Rollback, Browser-Absturz.
5. Produktivsetzung/Deploy des Servers (Migration `0355ff`) durch Betrieb; ECP-Prod bekommt die Releases erst nach Pilot.

## 7. Reihenfolge und Ablauf mit Codex

P0 (Claude, erledigt): dieser Plan. Danach:

1. **S1 → S2** (Server-Fundament, Resolver) – Codex.
2. Claude legt parallel das Agent-Repo an; **A1 → A2** gegen die Fake-Server-Spezifikation aus S2/S3.
3. **S3, A3, A4, A5** (WS/Events, Display, Kiosk, Installer).
4. **S4, S5** (UI, Overrides).
5. **S6 + A6** (Release/Manifest/Canary + Updater).
6. **A7, S7**, Hardware-Gates, Pilot.

Arbeitsregeln für jeden Codex-Auftrag: Modell `gpt-6.1-sol`; Codex committet nicht (read-only `.git`), Claude verifiziert Diff/Secrets/Checks und shippt (Commit, Push, PR, Merge). Aufträge selbsttragend mit `Datei:Zeile` und Sollverhalten; **keine** ELVIS/LIS-Polling-Schleifen, keine Emojis, nur ASCII-Anführungszeichen, DB-Routen synchron (`def`), Alembic-IDs nur Nummern. Wo dieser Plan eine Annahme macht, die der Code widerlegt, meldet Codex das im PR, statt zu raten.
