# Backup & Update – Härtungsplan (Review 2026-11-02)

Stand: `origin/main` @ 0792633a. Geprüft: `app/services/update_service.py`,
`app/routers/ui_settings.py` (Update-Routen), `app/services/backup_service.py`,
`app/cli.py` (backup/restore-test/backup-upload), `app/routers/ui_db_backup.py`,
`app/services/remote_backup_service.py`, `app/services/graph_backup_service.py`,
`app/services/org_backup_loop.py`, `app/routers/ui_org_backup.py`,
`app/services/org_import_service.py`, `app/services/org_export_media.py`,
`deploy/backup/*`, `deploy/einsatzleiter.service`, `.github/workflows/version-bump.yml`.

## Befunde

### Kritisch

| # | Befund | Ort | Wirkung |
|---|---|---|---|
| S1 | **Kommando-Injection über die Org-Backup-Konfiguration.** `org_admin`/`admin` (Org-Rolle, nicht System-Admin) setzen `ssh_strict`, `host`, `username`, `remote_path`, `rclone_remote` frei. `build_rsync_argv` baut `-e "ssh -o StrictHostKeyChecking=<ssh_strict> ..."` – rsync zerlegt den String an Leerzeichen, ein `ssh_strict` wie `accept-new -oProxyCommand=...` führt lokal ein Kommando aus (**lokal nachgestellt: ProxyCommand lief**). Weitere Vektoren: `remote_path` mit Zeilenumbruch → `!cmd` im `sftp -b`-Batch; `rclone_remote` als Connection-String (`:sftp,ssh=...:`); Hosts/Benutzer mit führendem `-`. | `remote_backup_service.py:93-138`, `ui_org_backup.py:147-221` | Codeausführung als App-User aus einer einzelnen Org heraus → `.env`, alle Orgs, DB. Ausgelöst per „Verbindung testen“. |
| U1 | **ZIP-Update spielt nie neue Migrationen ein.** `PROTECTED_PATHS` enthält `alembic/versions` → neue Migrationsdateien werden übersprungen, `alembic upgrade head` ist ein No-op, neuer Code läuft gegen altes Schema. | `update_service.py:28-34` | Betrifft jede Nicht-Git-Installation (ZIP-Upload und GitHub-Release ohne `.git`). |
| U2 | **Code wird vor dem Backup ausgetauscht.** Das Pre-Migrations-Backup läuft erst nach Kopie/`git reset --hard`. Scheitert es (oder pip, oder die Migration), läuft neuer Code auf der Platte ohne Migration und ohne Rollback; beim nächsten Neustart (Crash, Reboot) startet er gegen das alte Schema. Die Doku beschreibt das als gewollt. | `update_service.py:162-196, 563-604` | Halb aktualisierter Zustand; kein Weg zurück per UI. |

### Hoch

| # | Befund | Ort |
|---|---|---|
| U3 | **Reload funktioniert in der Standard-Unit nicht per SIGHUP**: `gunicorn` läuft ohne `--pid`, `gunicorn.pid` existiert nie → immer Fallback `sudo systemctl restart einsatzleiter` aus dem eigenen Prozess heraus (ohne sudoers-Regel: Fehlschlag; mit Regel: der Neustart beendet den laufenden Request selbst, die Antwort geht verloren). Dazu laufen Update + pip (bis 600 s) + Migration synchron im Web-Request. | `update_service.py:324-343`, `deploy/einsatzleiter.service` |
| U4 | **Versionserkennung nach Release-Update falsch**: `version-bump.yml` bumpt `pyproject.toml` erst *nach* dem Tag. Ein per Tag installierter Release meldet die Vorversion → `has_update` bleibt dauerhaft `True`. Außerdem `tag != current` (auch Downgrades gelten als Update), und der Bump nimmt das Workflow-Datum statt des Tags. | `update_service.py:346-441`, `.github/workflows/version-bump.yml` |
| U5 | **Branch-Update verbiegt den lokalen Branch**: `reset --hard FETCH_HEAD` setzt den *aktuell ausgecheckten* Branch (z. B. `main`) auf den Feature-Commit, erst danach `checkout -B <branch>`. Folge: lokales `main` zeigt auf fremden Commit, `git pull` an der Konsole divergiert. Der Branch-Name wird zudem ungeprüft an `git fetch` und in die API-URL gegeben (kein `--`, keine Ref-Prüfung). | `update_service.py:565-577, 470-492` |
| U6 | **Docker**: Kein Guard. Das In-App-Update schreibt in den Container-Layer und installiert pip dort, die Migration landet aber in der persistenten DB. Nach `docker compose up` (Recreate) läuft alter Code gegen das neue Schema. | `update_service.py`, `Dockerfile` |
| B1 | **Pre-Update-Dumps verdrängen die Tagessicherungen**: `run_backup(keep=-1)` beim Update nutzt dieselbe Anzahlgrenze (`backup_max_count`, Default 7) und dasselbe Namensschema. Bei mehreren Deploys am Tag (euer Normalfall) bleiben nur noch Dumps von heute, die Historie ist weg. | `cli.py:213-231`, `update_service.py:296-321` |
| B2 | **Dumps nicht atomar**: `mariadb-dump` schreibt direkt auf den finalen Namen. Ein Abbruch (Kill, Platte voll, Timeout) hinterlässt eine abgeschnittene, aber gültig benannte `.sql.gz`. Sie zählt bei der Retention mit (verdrängt gute Dumps), die Restore-Probe nimmt sie als neueste, und der Off-Site-Upload lädt sie hoch. Medien-Tar ebenso. | `cli.py:144-189` |
| R1 | **Org-Ersetzen löscht ohne gültige Sicherung**: `_safety_backup` ist best-effort. Scheitert es, löscht `replace=True` trotzdem alle Org-Daten. | `org_import_service.py:105-114, 158-182` |
| R2 | **Pfad-Traversal beim Org-Restore**: Medien-Zielpfade kommen aus Archivspalten (`storage_path`, `pfad`, `stored_filename` …) und werden ungeprüft an `root / wert` gehängt → `../../app/main.py` ist schreibbar. Archive stammen u. a. von Org-gesteuerten Remote-Zielen. | `org_export_media.py:33-59`, `org_import_service.py:254-274` |
| R3 | **`FOREIGN_KEY_CHECKS=0` kann im Pool hängen bleiben**: Das Aus-Schalten läuft auf der Transaktionsverbindung, das Wieder-Einschalten nach `commit/rollback` evtl. auf einer anderen Pool-Verbindung → eine Verbindung ohne FK-Prüfung bleibt im Pool. | `org_import_service.py:100-103, 179-241` |

### Mittel

| # | Befund |
|---|---|
| M1 | Keine Timeouts bei `scp/sftp/rsync/rclone/ssh`-Subprozessen und `ftplib` → ein hängendes Ziel blockiert den Backup-Lauf bzw. den `org_backup_loop` (Leader) unbegrenzt; systemd-Unit ohne `TimeoutStartSec`/`OnFailure`. |
| M2 | Kein Monitoring „letztes erfolgreiches Backup / letzte Restore-Probe“: weder Systemdiagnose noch Sysadmin-MCP kennen das Alter der Sicherung. Ausfälle bleiben unbemerkt, bis man sie braucht. |
| M3 | Restore-Probe prüft nur die Haupt-DB (Wetter-DB, Medien-Tar ungeprüft), keine Plausibilität (z. B. Zeilenzahl `fire_dept`/`users` > 0, `alembic_version` = Repo-Head), nimmt die neueste Datei ohne Vollständigkeitsprüfung. |
| M4 | Migrations-Subprozess: Timeout 120 s (große Migrationen werden mitten in nicht-transaktionaler MariaDB-DDL abgeschossen); Fehlermeldung zeigt `stderr[:500]` (Anfang statt Ende des Tracebacks). |
| M5 | Manuelles ZIP-Update installiert nie Abhängigkeiten (`install_deps` fehlt), GitHub-Update standardmäßig schon. Manueller ZIP-Upload wird nicht im Audit-Log erfasst, DB-Dump-Download und Retention-Änderung auch nicht. |
| M6 | Release-Update: Der POST ermittelt das „neueste“ Release neu statt den angezeigten Tag zu übernehmen. Erscheint dazwischen ein neues Release, wird ungesehen eine andere Version installiert. Bei Pre-Releases kann `releases[0]` ein Draft sein. |
| M7 | Org-Backup-Zeitplan: `hour` wird gegen UTC verglichen, die UI zeigt aber eine Uhrzeit (Org-Zeitzone) an → Lauf 1–2 h verschoben (Verstoß gegen die TZ-Regel). |
| M8 | Org-Restore: Das Archiv (bis `ORG_BACKUP_MAX_BYTES` = 2 GB) wird komplett in den RAM gelesen, nginx erlaubt aber nur 110 MB (`client_max_body_size`) → große Archive scheitern mit 413. Der Import läuft synchron im Request (`_restore_in_thread` ist kein Thread). Das Manifest enthält keine Alembic-Revision → keine Warnung bei Schema-Differenz. `org-safety`-Exporte werden nie aufgeräumt. |
| M9 | Dumps/Org-Exporte entstehen mit Default-umask (vermutlich 0644) und unverschlüsselt in `app_storage/backups`. FTP-Ziel unverschlüsselt (nur Log-Warnung). |
| M10 | Gleichzeitige Läufe (Timer + UI-Button + Pre-Update) haben nur einen prozesslokalen Lock → gleicher Sekunden-Zeitstempel/gleicher Dateiname, parallele Prunes. |

### Niedrig / Notiz
- Off-Site-Upload ohne temporären Namen und anschließendes Umbenennen. Ein Teil-Upload bleibt als „neueste“ Datei liegen, und die Remote-Prune kann dann ältere, gute Dateien löschen.
- `_dump_db` liest stderr erst nach stdout (Deadlock bei sehr viel stderr, theoretisch).
- Doku `Installation-Updates.md` beschreibt das heutige (fehlerhafte) Verhalten bei U2/U3 und muss nach den Fixes angepasst werden.

## Umsetzungsplan (Codex, je Phase ein PR)

Allgemein für jeden Codex-Auftrag: `--model gpt-6.1-sol`, kein paralleles pytest, Commit
durch Claude. Vor dem Merge laufen `ruff`, `mypy` und `pytest -n auto`. CHANGELOG
„Unreleased“ und Wiki-Seiten (`Installation-Updates`, `Installation-Backups`,
`Administration-Org-Datensicherung`) werden im selben PR gepflegt.

### Phase 1 – Sicherheitsfix Org-Remote-Ziel (S1) · zuerst, eigener PR
- Zentrale Validierung `remote_backup_service.config_pruefen` (greift auch beim Speichern):
  - `ssh_strict` nur aus `{"yes","accept-new","no"}`.
  - `host` per Hostname/IP-Regex, `username` `[A-Za-z0-9._-]+` ohne führendes `-`,
    `port` 0..65535.
  - `remote_path` ohne Steuerzeichen/Zeilenumbrüche, ohne `"` und ohne führendes `-`.
  - `rclone_remote` nur `^[A-Za-z0-9_-]+:$` (benannter Remote, keine Connection-Strings, kein `:backend,`).
- Org-Konfigurationen dürfen **kein** `rclone` (greift auf die serverweite rclone-Konfiguration zu)
  → nur System-Admin. Prüfen, ob `scp`/`rsync` für Orgs nötig sind, sonst nur `sftp/ftps/graph`.
- `build_rsync_argv`: ssh-Kommando mit `shlex.join` bauen, Ziel/Host mit `--` vom Optionsteil trennen
  (`ssh ... -- host cmd`, `sftp ... -- dest`, `scp ... -- files ziel`).
- `sftp_batch_text`: Pfade validieren/escapen und `!`-Zeilen ausschließen.
- Formular-Validierung mit verständlicher Fehlermeldung, Bestandsdaten mit ungültigen Werten → `last_error`
  „Konfiguration ungültig, bitte neu speichern“ statt Ausführung.
- Tests: Payload-Tabelle (ProxyCommand in `ssh_strict`, Newline+`!` im Pfad, `-o...` als Host/User,
  `:sftp,ssh=`-rclone) → `ValueError`, kein Runner-Aufruf. Org-Admin-Route-Test.

### Phase 2 – Sicherer Update-Ablauf (U1, U2, U5, U6, M4, M5, M6)
- Reihenfolge umbauen: **Vorprüfung → Backup → Code → pip → Migration → Reload**. Das Backup
  läuft vor jeder Dateiänderung. Scheitert es und ist `UPDATE_REQUIRE_BACKUP` gesetzt, bleibt
  der Code unverändert.
- ZIP-Overlay: `alembic/versions` aus `PROTECTED_PATHS` entfernen (Migrationen gehören zum Release).
  Staging statt Direktkopie: Extraktion in `.update-staging/`, dann Dateiliste kopieren.
  Vorher die zu überschreibenden Dateien nach `.update-rollback/<stamp>/` sichern.
  Bei pip- oder Migrationsfehler werden die Dateien automatisch zurückgespielt (bei einer
  teilweise gelaufenen Migration nur mit klarer Meldung samt Dump-Pfad).
- Git: vorher `before`-SHA merken. Bei Fehler nach dem Code-Schritt `git checkout --force <before>`
  zurücksetzen. Branch: `git checkout --force -B <branch> FETCH_HEAD` in **einem** Schritt
  (kein `reset --hard` auf dem aktuellen Branch). Branch-Name per `git check-ref-format --branch`
  validieren und `fetch ... -- <ref>` verwenden. API-URL mit `quote(branch, safe="")`.
- Token nicht in die Remote-URL (Prozessliste): `git -c http.extraHeader="Authorization: Bearer …"`
  über `GIT_CONFIG_*`-Env.
- Docker-Erkennung (`/.dockerenv` oder `EC_RUNTIME=docker`) → In-App-Update deaktiviert, UI zeigt
  `docker compose pull && up -d`-Anleitung.
- Migration: Timeout konfigurierbar (`UPDATE_MIGRATION_TIMEOUT_S`, Default 900), Fehlerausgabe = Ende
  von stderr. Vorher `alembic current` vs. `heads` loggen.
- pip automatisch, wenn sich `pyproject.toml` geändert hat (Hash-Vergleich), auch beim ZIP-Upload.
- Release-POST übernimmt den angezeigten `tag_name` und prüft ihn gegen die API, kein Neu-Ermitteln.
  Drafts werden ignoriert.
- Audit für ZIP-Upload, DB-Dump-Download und Retention-Änderung.
- Tests: Reihenfolge (Backup-Fehler ⇒ keine Dateiänderung), ZIP mit neuer Migration landet in
  `alembic/versions`, Rollback bei Migrationsfehler, Branch-Wechsel lässt `main` unverändert
  (echtes Temp-Git-Repo im Test), Docker-Guard, Ref-Validierung.

### Phase 3 – Neustart & Hintergrundausführung (U3)
- Update als eigener Prozess statt im Request: `systemd-run --user`-freie Variante, nämlich ein
  `einsatzleiter-update.service` (oneshot) plus sudoers-Eintrag *nur* für
  `systemctl start einsatzleiter-update` und `systemctl restart einsatzleiter`. Alternativ ein
  Doppel-Fork-Helper. Fortschritt landet in `app_storage/update/status.json`, die UI pollt per HTMX.
  Der Neustart passiert außerhalb des Web-Prozesses.
- `deploy/einsatzleiter.service`: `--pid /run/einsatzleiter/gunicorn.pid` + `RuntimeDirectory=`.
  `_reload_server` liest den Pfad aus den Settings. Bei mehreren Workern / Multi-Worker-Drop-in werden
  alle Units neu gestartet.
- Health-Check nach dem Neustart (`/healthz` liefert die neue Version und den Alembic-Head) als Erfolgskriterium.
- Doku/sudoers-Snippet in `deploy/README-Deployment.md` + Wiki.

### Phase 4 – Versionsquelle (U4)
- Release-Workflow umdrehen: Version wird **vor** dem Tag gesetzt (Workflow `release.yml`: Version
  bumpen → commit → Tag → Release). Alternativ ermittelt die App ihre Version aus `git describe --tags`
  bzw. bei ZIP-Installationen aus einer beim Update geschriebenen `app_storage/update/installed.json`.
- Vergleich per CalVer-Tupel (`2026.11.02.1` > `2026.11.02`), `has_update` nur bei neuerer Version,
  Downgrade nur mit expliziter Bestätigung.
- Tests für Vergleich/Parsing.

### Phase 5 – DB-Backup robust machen (B1, B2, M1, M3, M9, M10)
- Atomare Dateien: in `*.partial` schreiben, `fsync`, nach Erfolg umbenennen. Retention, Liste,
  Restore-Probe und Upload ignorieren `.partial`, verwaiste `.partial` > 24 h werden aufgeräumt.
  Vorher den freien Platz prüfen (Schätzung aus dem letzten Dump × 1,5).
- Pre-Update-Dumps eigener Präfix (`pre-update-einsatzleiter-…`) mit eigener Grenze
  (`UPDATE_BACKUP_KEEP`, Default 5). Sie verdrängen keine Tagessicherungen mehr.
  `parse_dump_dateiname`/UI kennen den Typ.
- Dateirechte 0600 (`os.umask(0o077)` im Lauf).
- Optional Verschlüsselung (`BACKUP_ENCRYPT_RECIPIENT`, `age`) für Off-Site.
- Prozessübergreifender Lock (`fcntl` auf `BACKUP_DIR/.backup.lock`) für Timer, UI und Update.
- Timeouts: `BACKUP_REMOTE_TIMEOUT_S` für alle Subprozesse (`subprocess.run(timeout=)`) und
  `ftplib.FTP(timeout=)`, `ConnectTimeout` für ssh. systemd: `TimeoutStartSec=2h`.
- Restore-Probe: Haupt- *und* Wetter-DB, Plausibilität (Zeilen `fire_dept`/`users` > 0,
  `alembic_version` = Head des Codes), `gzip -t` für Medien-Tar.
- Tests für jedes Teilstück (Fake-Binaries wie in `tests/test_backup_service.py`).

### Phase 6 – Org-Restore härten (R1, R2, R3, M7, M8)
- `replace=True` bricht ab, wenn das Safety-Backup fehlschlägt.
- Medienpfade: aufgelöstes Ziel muss unter dem jeweiligen Root liegen (`resolve()` +
  `is_relative_to`). Ungültige Pfade werden übersprungen und im Summary gezählt. Im Archiv steht
  `stored_filename` nur als Basename.
- FK-Checks: Import auf **einer** expliziten Connection (`db.connection()` festhalten), `SET` zurück
  im `finally` auf derselben Verbindung, alternativ `SET SESSION` + `connection.invalidate()` bei Fehler.
- Manifest um `alembic_revision` ergänzen. Beim Restore Warnung/Abbruch bei abweichender Revision
  (Override-Checkbox).
- Upload streamen (`shutil.copyfileobj` in eine Temp-Datei statt `read()`). Die nginx-Location für
  `/admin/org-backup/restore` bekommt eine eigene `client_max_body_size` passend zu
  `ORG_BACKUP_MAX_BYTES`. Import wird als Hintergrundjob mit Statusanzeige ausgeführt.
- `org-safety` mit Retention (z. B. 5 je Org).
- Org-Zeitplan: `hour` als Org-Lokalzeit interpretieren (`org.timezone`), Test über die DST-Grenze.

### Phase 7 – Monitoring & Doku (M2)
- Statusdatei/Tabelle „letzter Backup-Lauf“ (Zeit, Ergebnis, Größen, Off-Site-Ergebnis) und
  „letzte Restore-Probe“. Die Systemdiagnose und das Sysadmin-MCP (`admin_system_uebersicht`/
  neues `admin_backup_pruefen`) melden eine Warnung bei > 26 h ohne erfolgreichen Lauf bzw. > 8 Tagen
  ohne Restore-Probe.
- `ec-backup.service`: `OnFailure=` Hook (Mail/Teams via CLI) dokumentieren.
- Wiki/Runbook auf neuen Ablauf aktualisieren (Update-Reihenfolge, Rollback, Pre-Update-Präfix,
  Docker-Hinweis).

## Reihenfolge & Abhängigkeiten
1. **Phase 1 sofort** (aktive Sicherheitslücke, klein, unabhängig).
2. Phase 5 vor Phase 2. Phase 2 nutzt den neuen Pre-Update-Präfix und den Prozess-Lock.
3. Phase 2 → Phase 3 (Phase 3 baut auf dem neuen Ablauf auf) → Phase 4.
4. Phase 6 und 7 unabhängig, parallel möglich.

## Entscheidungen (Johannes, 2026-11-02)
- Produktion ist ein **Git-Checkout** mit funktionierender sudoers-Regel fuer `systemctl restart einsatzleiter`.
  U1 betrifft nur ZIP-Installationen; U3 wird dadurch zu "Neustart ausserhalb des Requests" statt "Neustart geht nicht".
- **Org-Self-Service-Ziele: nur `sftp`, `ftps`, `graph`.** `scp`, `rsync`, `rclone` und `ftp` (unverschluesselt)
  sind fuer Orgs nicht mehr waehlbar, bleiben aber in der serverweiten `.env`-Konfiguration erhalten.
  Bestehende Org-Konfigurationen mit gesperrtem Protokoll werden nicht ausgefuehrt, sondern mit
  `last_error` markiert und im UI zur Umstellung aufgefordert.
  `ssh_strict` fuer Orgs nur `yes`/`accept-new` (kein `no`).
  Host-Pruefung gegen interne Ziele (Loopback, Link-Local, private Netze) bei Org-Konfig, gegen SSRF.
- **Version: vor dem Tag setzen** (neuer Release-Workflow bumpt, committet, taggt, veroeffentlicht).
  `git describe` nur als Fallback fuer Entwicklungsstaende. Begruendung siehe Phase 4.
