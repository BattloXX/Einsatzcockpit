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
| `POST /pair` | Code einlösen → `device_token`, `device_id` (wie `pair_gateway`, Code 8 Zeichen, TTL 60 min (seit 2026-11-07, vorher 15), Brute-Force-Drossel) |
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
| S4 | Admin-UI Geräte/Displays/Pläne + Navigation + Doku, **Dialog „Gerät hinzufügen“ mit kopierbarem Einzeiler (Abschnitt 8)** | Playwright-Smoke, mobile |
| S5 | Overrides (GSL-Einsatzleitung), Allowlist, Audit | Rechte-Matrix-Test |
| S6 | Release-Upload (CI-Token), Manifest, Rollout/Canary, Update-Policy, `/update/result`, Admin „Updates“, **Installer-Endpunkte `install.sh` und Erst-Download (Abschnitt 8)** | 4.2-Tests |
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
| A5 | Setup-Assistent, `doctor`, systemd-Units, Postinst/Prerm (idempotent, Upgrade-fest), **`scripts/install.sh` (Einzeiler-Installer, Abschnitt 8)**, Doku Installation/Betrieb/Upgrade |
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

## 8. Einzeiler-Installation (Entscheidung 2026-11-07)

Ziel: Ein Org-Admin klickt im ECP auf „Gerät hinzufügen“, kopiert **eine Zeile** samt Server, Pairing-Code und Fingerprint, fügt sie auf dem frischen Ubuntu-Rechner ein, und danach läuft alles: Paket, Benutzer, Pairing, Kiosk, Autostart.

### 8.1 Der Einzeiler (im ECP per Kopier-Button)

```
curl -fsSL https://<ecp-host>/infoscreen-agent/install.sh | sudo bash -s -- --server https://<ecp-host> --pairing-code ABCD1234 --key-fingerprint sha256:<hex>
```

- Wird serverseitig aus Org-Host, frisch erzeugtem Pairing-Code (S1: einmalig, TTL 60 min (seit 2026-11-07, vorher 15)) und Fingerprint des Release-Signaturschlüssels zusammengesetzt; nie aus Nutzereingaben ohne Escaping (Shell-Quoting per `shlex.quote`, Test mit Sonderzeichen im Gerätenamen). Optionaler Gerätename: `--name "Fahrzeughalle"`.
- Alternative ohne Code in der Prozessliste/History: `curl ... | sudo EC_PAIRING_CODE=ABCD1234 bash -s -- --server ...`; UI bietet beide Varianten per Umschalter an. Der Code ist einmalig und kurzlebig; das Dialogtext weist darauf hin.
- Der Dialog zeigt den Code mit Restlaufzeit, einen „Neuen Code erzeugen“-Button und aktualisiert sich per HTMX, sobald das Gerät gekoppelt hat (Status „verbunden“, ohne F5).

### 8.2 Verhalten von `scripts/install.sh` (Agent-Repo, `set -euo pipefail`, POSIX-nahes Bash)

1. Vorprüfungen: root (sonst Hinweis auf `sudo`), Ubuntu/Debian mit `apt`, Architektur `amd64` (arm64 nur wenn Release vorhanden), Systemzeit/NTP plausibel, Netz zum ECP, genug Platz. Klare Fehlermeldungen mit Exit-Codes, nichts wird halb installiert.
2. Lädt per TLS den Erst-Download vom ECP (`GET /api/v1/infoscreen-agent/download/latest?arch=amd64&channel=stable`, Autorisierung mit dem Pairing-Code als Bearer, nicht öffentlich) samt Manifest (`.deb`-SHA256, Ed25519-Signatur).
3. Prüft Signatur und SHA256 **vor** `dpkg`/`apt`; der im Skript eingebettete öffentliche Schlüssel muss zum übergebenen `--key-fingerprint` passen (sonst Abbruch). Nur kanonisches Manifest, nur Paketname `ec-infoscreen-agent`.
4. `apt-get install -y ./paket.deb` (zieht Abhängigkeiten wie Chromium und sway aus Ubuntu; Chromium-Variante je Ubuntu-Version in `docs/hardware-matrix.md` festhalten, snap-Chromium-Falle prüfen).
5. `ec-infoscreen-agent setup --server ... --pairing-code ... --non-interactive` (A5): Pairing, Display-Erkennung, **Monitor-Probe nur mit `--probe` oder bei vorhandener grafischer Sitzung**, sonst `logical_blank` und Hinweis; Kiosk-Benutzer und Autostart; Dienste aktivieren und starten.
6. Abschluss: `ec-infoscreen-agent doctor` ausführen und Ergebnis kompakt ausgeben (grün/gelb/rot), Hinweis auf Neustart nur wenn nötig. Code und Token erscheinen nie in der Ausgabe oder im Journal.
7. Idempotent: erneuter Aufruf ist Reparatur/Update (gleiche Version -> nur `setup`/`doctor`); mit neuem Code wird neu gekoppelt (`--force-repair`). `--uninstall` entfernt Paket, Dienste und Benutzer, `--purge` zusätzlich Daten und Token (mit `unpair` am Server, wenn erreichbar).
8. Keine weiteren Kommandos aus dem Netz: das Skript führt nur eigene, fest einprogrammierte Schritte aus. `curl | bash`-Nachteile (Teilübertragung) mitigieren: gesamtes Skript steht in einer `main()`-Funktion, die erst am Dateiende aufgerufen wird.

### 8.3 Server-Seite

- Öffentliche Route `GET /infoscreen-agent/install.sh` (Content-Type `text/x-shellscript`, kein Cookie, enthält **keine** Geheimnisse): liefert die zuletzt signiert hochgeladene Skriptversion aus der Release-Ablage (CI lädt `install.sh` mit jedem Agent-Release zum ECP, signiert wie das Paket; ECP prüft die Signatur beim Upload). Rate-Limit, Cache-Header, Pfad zum Skript nicht aus Nutzereingaben.
- `GET /api/v1/infoscreen-agent/download/latest` akzeptiert als Bearer **nur** einen gültigen, noch nicht eingelösten Pairing-Code, liefert den freigegebenen `stable`-Build (Rollout-Prozent gilt nur für Updates, nicht für die Erstinstallation), Drossel gegen Raten, Cross-Org-/Replay-Tests.
- Admin-Dialog „Gerät hinzufügen“ (S4) erzeugt den Code per bestehendem `erzeuge_pairing_code`, zeigt Einzeiler, Kopier-Button (Clipboard-API mit Fallback „Text markieren“), Mobilansicht <= 760 px; Audit-Eintrag „Pairing-Code erzeugt“ (ohne Code).

### 8.4 Tests

- Agent-Repo: ShellCheck in der CI; Install-Test im Container ubuntu:24.04 und 26.04 gegen den Fake-ECP (frische Installation, zweiter Aufruf, falscher Fingerprint, manipulierte Signatur, abgelaufener Code, kein Netz, ohne root, Abbruch mitten im Download), Ausgabe enthält nie den Code.
- Server: Einzeiler-Generator (Quoting, Host aus Konfiguration, kein Open-Redirect-/Host-Header-Einfluss), Download nur mit gültigem Code, Code ist nach `setup` verbraucht, `install.sh` ohne Geheimnisse.
- Hardware-Gate: Einzeiler einmal auf frisch installiertem Ubuntu 26.04 am Pilotgerät (Fahrzeughalle) ausführen, Dauer und Fehlermeldungen protokollieren.

## 9. Vereinfachung Schluessel und Releases (Entscheidung 2026-11-07, Repo ist oeffentlich)

Problem: Schluessel erzeugen, `.env` editieren, Upload-Token, drei GitHub-Secrets und ein
Server-Neustart sind zu viele manuelle Schritte. Da das Agent-Repo oeffentlich ist, entfallen
die Gruende fuer Upload und Secrets-Aufwand: Pakete und Signaturen sind ohnehin oeffentlich;
das Vertrauen liegt allein im **gepinnten oeffentlichen Schluessel** im ECP, nicht in GitHub.

Zielablauf (3 Schritte):
1. **Einmalig, lokal:** `scripts/init_release_keys.sh` im Agent-Repo: erzeugt das Ed25519-Paar in
   einem temporaeren Verzeichnis, setzt `EC_RELEASE_SIGNING_KEY` per `gh secret set`, schreibt den
   oeffentlichen Teil nach `packaging/release-key.pem`, legt eine Sicherungskopie des privaten
   Schluessels verschluesselt (`openssl enc`/Passphrase) in eine vom Nutzer gewaehlte Datei ab,
   zeigt den Fingerprint gross an und raeumt auf (kein Klartext-Schluessel bleibt liegen). Das
   Skript ist idempotent (bricht ab, wenn das Secret schon existiert, ausser `--rotate`).
2. **Tag setzen:** die CI baut, signiert und veroeffentlicht ein normales GitHub-Release
   (Assets mit festen Namen: `release.json`, `ec-infoscreen-agent_<ver>_<arch>.deb`, `install.sh`,
   `release-key.pem`). **Kein Upload-Token, kein `ECP_URL`-Secret.** Der bisherige Upload
   (`POST /api/v1/infoscreen-agent/releases`) bleibt als optionaler Weg erhalten (nur wenn die
   Secrets gesetzt sind) fuer abgeschottete Installationen.
3. **Im ECP (Sysadmin, Agent-Releases):** einmal „Schluessel aus Repository uebernehmen“ (zeigt den
   Fingerprint, Sysadmin bestaetigt ihn gegen die Ausgabe von Schritt 1), danach „Releases von
   GitHub holen“ (Button, optional taeglicher Abgleich) und freigeben. Kein `.env`-Edit, kein
   Neustart.

Server-Anforderungen (PR S8):
- Tabelle `infoscreen_release_key` (key_id, PEM, Fingerprint, aktiv, bestaetigt_von/-am); Quelle
  zusaetzlich zum Setting `INFOSCREEN_AGENT_RELEASE_PUBKEYS` (Setting bleibt gueltig). Der
  Fingerprint fuer den Einzeiler kommt aus dem aktiven DB-Schluessel.
- Importer `infoscreen_agent_github_import`: **fest verdrahtetes Repo** (Setting
  `INFOSCREEN_AGENT_GITHUB_REPO`, Default `BattloXX/Einsatzcockpit-Infoscreen-Agent`, Format
  `owner/name` validiert), nur https, nur Hosts `api.github.com`, `github.com`,
  `objects.githubusercontent.com`; Redirects nur auf diese Hosts, Groessenlimits (.deb 512 MiB,
  Metadaten 1 MiB), Timeouts, ETag-Caching, kein Token noetig; dieselbe Pruefkette wie beim Upload
  (Signatur gegen gepinnten Schluessel, SHA256, Paketheader, Duplikate); neue Releases starten
  mit `rollout_percent=0`. Keine Ausfuehrung heruntergeladener Inhalte.
- Optionaler Abgleich im Hintergrund (Setting `INFOSCREEN_AGENT_GITHUB_SYNC_ENABLED`, Default
  false; ein Lauf pro Tag, Leader-Lock, `loop_thread_io`-Muster der Hintergrund-Loops).
- UI: Sysadmin-Seite zeigt Schluessel (Fingerprint, Status), Import-Button mit Ergebnisliste
  (neu/uebersprungen/abgelehnt mit Code), keine Geheimnisse.

Agent-Anforderungen (PR A8):
- `scripts/init_release_keys.sh` (ShellCheck, Test mit Fake-`gh` und Temp-Verzeichnis).
- `release.yml`: veroeffentlicht das GitHub-Release mit den festen Assets; Upload zum ECP nur wenn
  `ECP_URL` und `EC_RELEASE_UPLOAD_TOKEN` gesetzt sind. `release.json` enthaelt je Arch das
  Manifest nach `installer-protocol.md` plus Signatur von `install.sh`.
- Doku: `docs/release.md` und README neu (Repo ist oeffentlich), Schritt-fuer-Schritt in 3 Schritten.

Nicht Teil: Sigstore/Cosign (zu schwer fuer den Bash-Installer), Auto-Freigabe ohne Admin.

## 10. Repo-Modus: Installation und Update direkt aus dem Repo (Entscheidung 2026-11-07)

Vorbild ist der Windows-Druckagent: Das Paket kommt direkt aus den oeffentlichen GitHub-Releases,
das ECP gibt nur **URL + Token** mit und steuert das **Wann**. Das ECP speichert und verteilt
keine Pakete mehr (der Upload-/Import-Weg aus S6/S8 bleibt als optionaler "verwalteter Modus"
fuer abgeschottete Installationen erhalten, ist aber nicht mehr der Standard).

Installation (aus dem ECP-Dialog kopiert):
```
curl -fsSL https://github.com/BattloXX/Einsatzcockpit-Infoscreen-Agent/releases/latest/download/install.sh | sudo bash -s -- --server https://<ecp> --token ABCD1234
```
(`--token` ist der bisherige Pairing-Code; `--pairing-code` bleibt als Alias. `--channel beta`
liest die Release-Liste per API. Optional `--key-fingerprint sha256:...`.) Das Skript laedt `release.json`,
`.deb` und `release-key.pem` aus demselben Release-Tag, prueft SHA256 und Ed25519-Signatur
(oeffentlicher Schluessel steckt im Skript), installiert und koppelt mit dem ECP. Kein
Download-Endpunkt am ECP, Token wird nicht fuer den Download missbraucht.

Update (Quelle GitHub, Entscheidung beim ECP):
1. Der Agent fragt alle `check_interval_h` (Default 6 h, vom ECP vorgegeben, +-20 % Jitter) die
   Release-Liste auf GitHub ab (oeffentlich, ETag, nur Repo `BattloXX/Einsatzcockpit-Infoscreen-Agent`,
   nur Hosts github.com / api.github.com / *.githubusercontent.com), oder sofort bei WS-Hinweis
   `update_available` / Admin-Aktion "Jetzt pruefen".
2. Fuer die neueste Version seines Kanals fragt er das ECP: `POST /api/v1/infoscreen-agent/update/check`
   (Device-Bearer) mit {version, channel, arch, current} -> {allowed, reason(ok|incident|window|locked|pinned|rollout|blocked|unknown),
   retry_after_s, maintenance_window, critical_allowed, check_interval_h, pinned_version}. Das ECP entscheidet
   ueber Einsatz/GSL/Nachlauf, Wartungsfenster, Sperre, Pin, Rollout-Prozent (stabiler Bucket) und Sperre einer Version.
3. Nur bei `allowed` laedt der Agent das Paket **direkt von GitHub**, prueft SHA256 + Signatur gegen die im
   installierten Paket mitgelieferten Schluessel (Rotation wie bisher), und der Root-Helfer installiert (A6, unveraendert:
   erneute Pruefung, Healthcheck, Rollback). Ergebnis weiter per `update/result`.
4. "Direkt ueber das Repo": `ec-infoscreen-agent update now --from-repo` am Geraet prueft/installiert die neueste Version
   ohne ECP-Freigabe, aber **nie** bei laufendem Einsatz/GSL (lokale Pruefung des letzten bekannten Zustands) und nur mit
   gueltiger Signatur. Ist das ECP nicht erreichbar, gilt die Policy "kein automatisches Update" (konfigurierbar:
   `update_offline_policy = never|window`, Default never).
5. Rollout im ECP: neue Versionen werden beim ersten `update/check` automatisch als Zeile (version, channel, arch,
   rollout_percent=0, blocked=false) angelegt. Der Sysadmin gibt sie frei (10/50/100 %, Sperre/Rueckzug) in Agent-Releases;
   die Freigabe sendet `update_available`. Kein Paket, keine Signatur, kein Upload-Token im ECP noetig.

Sicherheitsbewertung (bewusst akzeptiert): Vertrauensanker ist der Signaturschluessel im Repo-Release
(plus optionaler Fingerprint im Einzeiler). Ein Angreifer mit Repo-/Actions-Zugriff koennte signieren; Schutz davor
sind GitHub-2FA, geschuetzte Tags/Branches und die ECP-seitige Freigabe (rollout 0 -> Pilot). Gegenueber dem
Windows-Agent (nur SHA256 aus demselben Release) ist die Signatur ein Mehrwert ohne Zusatzaufwand fuer den Anwender
(`init_release_keys.sh` einmalig). Geraete brauchen ausgehend github.com und *.githubusercontent.com (443).

Umsetzung: A9 (Agent: install.sh aus dem Release, `--token`, GitHub-Update-Quelle, `update/check`, `update now --from-repo`,
Offline-Policy, Tests, Doku) und S9 (Server: `update/check`, automatische Versionszeilen, Freigabe-UI "Version freigeben",
Einzeiler-Generator zeigt GitHub-URL + `--token`, Banner-Logik ohne Schluessel/Import, Doku/Wiki). Schluessel-UI und
Import aus S8 werden im Standard ausgeblendet (Setting `INFOSCREEN_AGENT_DISTRIBUTION=github|managed`, Default github).

## 7. Reihenfolge und Ablauf mit Codex

P0 (Claude, erledigt): dieser Plan. Danach:

1. **S1 → S2** (Server-Fundament, Resolver) – Codex.
2. Claude legt parallel das Agent-Repo an; **A1 → A2** gegen die Fake-Server-Spezifikation aus S2/S3.
3. **S3, A3, A4, A5** (WS/Events, Display, Kiosk, Installer).
4. **S4, S5** (UI, Overrides).
5. **S6 + A6** (Release/Manifest/Canary + Updater, Installer-Endpunkte; `install.sh` kommt in A5, Dialog in S4).
6. **A7, S7**, Hardware-Gates, Pilot.

Arbeitsregeln für jeden Codex-Auftrag: Modell `gpt-6.1-sol`; Codex committet nicht (read-only `.git`), Claude verifiziert Diff/Secrets/Checks und shippt (Commit, Push, PR, Merge). Aufträge selbsttragend mit `Datei:Zeile` und Sollverhalten; **keine** ELVIS/LIS-Polling-Schleifen, keine Emojis, nur ASCII-Anführungszeichen, DB-Routen synchron (`def`), Alembic-IDs nur Nummern. Wo dieser Plan eine Annahme macht, die der Code widerlegt, meldet Codex das im PR, statt zu raten.
