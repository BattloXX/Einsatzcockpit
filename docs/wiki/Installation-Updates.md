# Updates einspielen

← [Zurück zur Startseite](Home)

## Auto-Update über das Webinterface (empfohlen)

Unter **`/admin/system/update`** (nur `system_admin`) gibt es drei Wege, ganz ohne SSH:

### 1. GitHub-Release (Produktion)

„Auf Updates prüfen" vergleicht die installierte Version mit dem neuesten GitHub-Release
(optional inklusive Pre-Releases) und zeigt Release-Notes an. Auf einer Git-Installation
wird der Release-Tag per `git fetch` geholt und als detached HEAD ausgecheckt. Nur bei
Installationen ohne `.git` wird das Release-ZIP als geschütztes Overlay eingespielt.
Der POST übernimmt den angezeigten Tag. Vor der Installation wird geprüft, ob er noch
das aktuelle veröffentlichte Release ist; bei einem Wechsel muss erneut geprüft werden.
Entwürfe werden auch bei eingeschlossenen Pre-Releases ignoriert.
Der Vergleich erfolgt numerisch nach CalVer (z. B. `2026.11.02.1` > `2026.11.02`).
Ist der installierte Stand neuer, erscheint „Installierte Version ist neuer als das letzte Release"
und kein Server-Update-Button.

Nach bestätigtem Neustart speichern Release-Updates `app_storage/update/installed.json`
atomar mit Dateirechten 0600 (`kind`, `tag`, `sha`, `installed_at`). Damit zeigen auch alte
Release-Tags mit einer Vorversion in `pyproject.toml` die tatsächlich installierte Version.
Bei Git gilt die Datei nur für denselben HEAD; ohne Git nur, wenn die Code-Version
parsbar und nicht neuer als der gespeicherte Tag ist. Sonst gilt wieder `pyproject.toml`.

### 2. Direkt vom Repository (Branch — Hotfixes & Testsysteme)

Der Abschnitt **„Direkt vom Repository"** lädt den aktuellen Stand eines beliebigen
Branches (Standard `main`) ein — ohne Release. Git-Installationen werden per
`fetch` und `git checkout --force -B <branch> FETCH_HEAD` synchronisiert; ohne `.git` dient der Zipball als Fallback.
Ein zuvor ausgecheckter anderer Branch (z. B. `main`) behält seinen Commit.
Die Branch-Auswahl zeigt den letzten Commit (SHA, Nachricht, Autor); ein bereits
eingespielter Stand wird erkannt und der Button deaktiviert. Der zuletzt eingespielte
Branch-Stand (`branch@sha`) wird gespeichert und oben auf der Seite angezeigt.

> Gedacht für Hotfixes und Testsysteme — für Produktion sind Releases der sauberere Weg.

### 3. ZIP-Upload (manuell, Fallback)

Release-ZIP herunterladen und über das Formular hochladen. Wenn
`UPDATE_ZIP_REQUIRE_HASH=true` (Standard), ist die erwartete SHA256-Prüfsumme Pflicht;
der Server lehnt Uploads ohne Prüfsumme vor der Verarbeitung ab.

### Ablauf bei allen drei Wegen

1. Ein exklusiver Update-Lock wird belegt; ein zweiter gleichzeitiger Versuch wird sofort abgelehnt.
2. Vorprüfung: ZIP-Struktur und erwartete SHA256-Prüfsumme prüfen, sicher in ein temporäres
   Staging-Verzeichnis extrahieren; bei Git den validierten Branch bzw. Tag per `fetch` holen.
3. Pre-Update-DB-Backup erstellen, **bevor Dateien der Installation ersetzt werden**.
4. Code austauschen. ZIPs sichern vorher alle zu ersetzenden bestehenden Dateien unter
   `.update-rollback/<UTC-Stamp>/files/`; `manifest.json` merkt ersetzte und neue Dateien.
   `.env`, `.env.local`, Uploads, `app_storage`, Git-/venv-Daten und die Update-Sperre bzw.
   Rollback-Ablage bleiben geschützt. **`alembic/versions/` gehört zum Release und wird aktualisiert.**
5. Abhängigkeiten installieren (`pip install -e .`), wenn gewählt. Bei ZIPs löst eine
   geänderte `pyproject.toml` den Schritt automatisch aus (Hash-Vergleich).
6. `alembic current` und `heads` erfassen und protokollieren, dann `alembic upgrade head`.
   Die Ergebnisse enthalten `migrations_from` und `migrations_to`.
   `UPDATE_MIGRATION_TIMEOUT_S=900` begrenzt die Migration (Sekunden); Fehlermeldungen
   zeigen die letzten 800 Zeichen von stderr.
7. Erst nach erfolgreichem Abschluss Gunicorn-Reload (SIGHUP) bzw. `systemctl restart` anfordern.
   Der Ablauf läuft als eigener Hintergrundprozess; die HTTP-Antwort enthält sofort die Fortschrittsanzeige.
8. Nach dem Neustart bestätigt die App den neuen Prozess, Alembic-Stand und bei Git den erwarteten Commit.

### Fortschritt und Nachprüfung

Nach der Vorprüfung startet der Request `.venv/bin/python -m app.cli update-run <job_id>`
als eigenen Prozess mit neuer Session. pip und Migration laufen unabhängig von Browser-
und Proxy-Timeouts weiter. Die bestehende Update-Seite zeigt Sicherung, Code,
Abhängigkeiten, Migration und Neustart sowie die letzten 40 bereinigten Protokollzeilen.
Sie fragt alle zwei Sekunden den Status ab und stoppt bei einem Endzustand.
Auch beim erneuten Öffnen der Seite bleibt der letzte Job sichtbar.

Unter `app_storage/update/` liegen der atomar geschriebene `status.json` und
`<job_id>.log`. ZIP und Parameterdatei haben Rechte 0600. Die Parameterdatei wird
im Kind unmittelbar nach dem Einlesen gelöscht, das ZIP bei Abschluss und bereits
vor dem Neustart. Das GitHub-Token wird mit eigener DB-Session neu geladen;
es steht weder in Jobargumenten noch in Status oder Protokoll. Pro Installation
läuft nur ein Job; ein unerwartet beendeter Prozess wird als fehlgeschlagen markiert.

Vor dem Neustart wird `restarting` samt erwartetem Git-SHA und Alembic-Head gespeichert.
**systemd mit dem Standard `KillMode=control-group` beendet beim Restart auch den
Update-Prozess**, obwohl dieser eine eigene Session hat. Daher erledigt der Job
nach dem Neustart keine weiteren Arbeiten. Ein neuer App-Prozess prüft best-effort
beim Start (nur DB-lesend) und beim Statusabruf: neuer Prozess nach Restartbeginn,
DB-Revision gleich Code-Head gleich erwartetem Head sowie Git-HEAD gleich erwartetem SHA.
Erst dann gilt `success`. `/health` bleibt unverändert.

Nach `UPDATE_VERIFY_TIMEOUT_S=300` ohne Bestätigung gilt `unconfirmed`.
Das bedeutet: Ergebnis offen, keine automatische Wiederholung. Serverprozess,
`alembic current`, `alembic heads` und `git rev-parse HEAD` prüfen. Eine abweichende
Revision, ein alter Prozess oder ein anderer Commit verhindern die Erfolgsanzeige.
`system.update_job` auditiert Job-ID, Art, Ziel und Endzustand beim Statusabruf;
die bestehenden Aktionsaudits bleiben bestehen.

### PID-Datei und sudoers

Die mitgelieferte Unit verwendet `--pid /run/einsatzleiter/gunicorn.pid`,
`RuntimeDirectory=einsatzleiter` und `RuntimeDirectoryMode=0750`.
In `.env` dazu setzen:

```dotenv
GUNICORN_PID_FILE=/run/einsatzleiter/gunicorn.pid
UPDATE_VERIFY_TIMEOUT_S=300
```

Nach Änderung der Unit als Betreiber `sudo systemctl daemon-reload` und einen
manuellen Neustart ausführen. Bei erreichbarer Master-PID nutzt die App SIGHUP
für einen graceful Reload. Ohne Setting wird wie bisher `gunicorn.pid` im
Projektverzeichnis versucht; ohne gültige PID greift `sudo -n systemctl restart einsatzleiter`
mit 15 Sekunden Zeitlimit. Es werden keine zusätzlichen sudo-Rechte benötigt.
Das minimale Beispiel in `deploy/sudoers-einsatzleiter.example` lautet:

```sudoers
clp-einsatz ALL=(root) NOPASSWD: /usr/bin/systemctl restart einsatzleiter
```

Mit `sudo visudo -f /etc/sudoers.d/einsatzleiter` bearbeiten und mit `sudo visudo -c`
prüfen. Es wird kein Recht für `systemd-run` oder eine zusätzliche Update-Unit benötigt.
Bei Fehler erscheint "Neustart fehlgeschlagen, bitte manuell: sudo systemctl restart einsatzleiter".
**Code und Migration sind dann bereits eingespielt.** Neustartrecht, Dienst und
Journal prüfen und den Neustart manuell ausführen. Ein Job mit "Prozess unerwartet
beendet" kann einen halbfertigen Code-/DB-Zustand hinterlassen: vor erneutem Update
Protokoll, Pre-Update-Dump und Migrationsstand prüfen.

### Automatisches Backup vor dem Code-Austausch

`UPDATE_BACKUP_BEFORE_MIGRATE=true` erstellt vor jedem Update DB-Dumps ohne Medienarchiv.
Sie landen mit Präfix `pre-update-` in `BACKUP_DIR` und unterliegen separat
`UPDATE_BACKUP_KEEP` (Standard 5 je DB). Mit `UPDATE_REQUIRE_BACKUP=true` (Standard)
stoppt ein fehlgeschlagenes Backup das Update **bei unverändertem Code**, vor pip,
Migration und Reload. Nach Behebung der Ursache kann das Update erneut gestartet werden.
Bei `false` wird die Backup-Warnung angezeigt, das Update läuft weiter.
Restore-Anleitung und DR-Runbook: [Backups](Installation-Backups).

### Privates Repository: GitHub-Token

Ist das Repository privat, auf der Update-Seite einen **Fine-grained Personal Access Token**
mit Berechtigung *Contents: Read* auf das Repo hinterlegen (Feld „GitHub-Zugriffstoken").
Der Token wird Fernet-verschlüsselt in den SystemSettings gespeichert und für Release-Check,
Branch-Check und Downloads verwendet. Git erhält die Authentifizierung über
`GIT_CONFIG_*`-Umgebungsvariablen als HTTP-Header; das Token steht weder in der
Remote-URL noch in Subprozessargumenten. Leer speichern löscht ihn. Alle Update-Aktionen und
Token-Änderungen landen im Audit-Log.

## Standard-Update-Prozess (SSH, Alternative)

> **Wichtig bei manuellem `git pull`:** Wenn sich Abhängigkeiten geändert haben,
> muss anschließend **`pip install -e .` in der aktivierten App-Umgebung** ausgeführt
> werden, bevor Migrationen und Neustart folgen. `git pull` allein installiert keine
> Python-Pakete. Der Web-Updater übernimmt diesen Schritt automatisch, wenn
> **Abhängigkeiten installieren** aktiviert ist (bei GitHub-Updates standardmäßig aktiv).

```bash
cd /home/clp-einsatz/htdocs/einsatzleiter
source .venv/bin/activate

# 1. Aktuellen Stand sichern:
mysqldump -u einsatzleiter -p einsatzleiter --single-transaction > ../backup_vor_update.sql

# 2. Neuen Code holen:
git pull origin main

# 3. Abhängigkeiten aktualisieren:
pip install -e ".[dev]"

# 4. Datenbankmigrationen ausführen:
alembic upgrade head

# 5. Dienst neu starten:
sudo systemctl restart einsatzleiter

# 6. Status prüfen:
sudo systemctl status einsatzleiter
journalctl -u einsatzleiter -n 20
```

## Prüfen ob Migrationen ausstehen

```bash
alembic current   # Aktuelle Revision
alembic heads     # Neueste Revision im Code
```

Falls sie sich unterscheiden: `alembic upgrade head` ausführen.

## Update mit Docker Compose

Bei vorgebauten Images werden neue Images geladen und die Services neu erstellt:

```bash
docker compose pull && docker compose up -d
docker compose ps
```

Beim mitgelieferten Compose-Setup wird die App lokal gebaut. Nach dem Aktualisieren
des Repositories daher `docker compose up -d --build` verwenden. Der Entrypoint führt
`alembic upgrade head` automatisch vor jedem App-Start aus.

In Docker sind alle In-App-Updates (ZIP, Release, Branch) deaktiviert. Die Erkennung
nutzt `/.dockerenv` oder `EC_RUNTIME=docker`. Die Update-Seite zeigt statt Formularen
`docker compose pull && docker compose up -d`; direkte Update-POSTs liefern ebenfalls
einen verständlichen Hinweis. Bei lokalen Builds gilt der oben beschriebene Build-Befehl.

Falls ein eigenes Image den mitgelieferten Entrypoint nicht verwendet, Migrationen
vor der Freigabe manuell ausführen:

```bash
docker compose exec app alembic upgrade head
```

## Rollback nach fehlgeschlagenem Update

Bei pip-Fehlern wird der Code automatisch zurückgespielt: ZIPs stellen ersetzte
Dateien wieder her und entfernen neu angelegte Dateien. Git kehrt zum vorherigen
Commit und zum vorherigen Branch bzw. Detached-Zustand zurück. Installierte Python-Pakete
werden dabei nicht automatisch zurückgesetzt; nach Prüfung die zum Code passenden
Abhängigkeiten installieren.

Bei Migrationsfehlern wird `alembic current` vor und nach dem Lauf verglichen.
Nur bei sicher unverändertem Revisionsstand erfolgt ein automatischer Code-Rollback.
Wurden bereits Revisionen angewendet oder ist der Stand nicht lesbar, bleibt der neue
Code liegen; die Meldung erklärt das und nennt den Pre-Update-Dump für den Restore.
**MariaDB-DDL ist nicht transaktional:** Auch bei unveränderter Revision kann die DB
teilweise geändert sein. Vor dem manuellen Neustart DB-Zustand prüfen und nötigenfalls
Dump und passenden Code gemeinsam wiederherstellen; kein blindes `alembic downgrade`.
Bei Fehlern wird kein automatischer Reload angefordert.

Die ZIP-Rollback-Ablage bleibt nach Erfolg erhalten. Nach jedem erfolgreichen Update
werden nur die letzten drei Verzeichnisse unter `.update-rollback/` behalten;
ältere werden entfernt. `.update-rollback/` und `.update.lock` sind Git-ignoriert.
Die Dateisicherung ersetzt kein Datenbank-Backup. Dump-Downloads und Änderungen der
Backup-Aufbewahrung werden ebenso wie ZIP-Uploads (Name, Größe, SHA256-Präfix, kein Inhalt)
im Audit-Log erfasst.

## Release erstellen

Releases über **GitHub → Actions → Release → Run workflow** oder die GitHub-CLI starten:

```bash
gh workflow run release.yml -f notes="Fehlerbehebungen und Verbesserungen im Update-Verfahren."
```

`notes` enthält die deutschen Release-Notes; über die CLI sind auch mehrzeilige Werte
möglich. Optional: `-f version=2026.11.02.1`, `-f prerelease=true` und `-f target=main`.
Ohne `version` berechnet der Workflow das heutige Datum als `YYYY.MM.DD` und ergänzt
bei gleicher Code-Version oder belegtem Tag einen freien `.N`-Suffix. Explizite Versionen
müssen `YYYY.MM.DD` oder `YYYY.MM.DD.N` entsprechen; vorhandene Tags werden abgelehnt.

Der Workflow liest `target` (Standard `main`), aktualisiert `pyproject.toml`, `APP_VERSION`
und beide README-Versionsangaben, committet als `github-actions[bot]` und pusht diesen
Commit auf `main`. Ein anderer Quellstand muss sich daher ohne Force-Push auf `main`
übernehmen lassen. Erst danach setzt und pusht er `v<version>` auf genau diesen Commit
und erstellt das Release mit den eingegebenen Notes. Die Gruppe `release` serialisiert
Release-Läufe. Der vorhandene `GITHUB_TOKEN` genügt; kein neuer Secret ist nötig.

Tags und Releases nicht mehr manuell vor dem Versionscommit anlegen. Der bisherige
Workflow `version-bump.yml` verändert keine Dateien mehr: Bei `release: published`
prüft er die `pyproject.toml` des Tags und meldet Abweichungen mit Hinweis auf `release.yml`.

## Update-Frequenz

Regelmäßige Updates werden als GitHub Releases veröffentlicht. Empfohlen:
- **Kritische Fixes:** sofort einspielen
- **Feature-Updates:** außerhalb der Einsatzsaison (z.B. Winter)
- **Sicherheits-Updates:** innerhalb 48 Stunden

## Wartungsmodus

Für größere Updates kann eine Wartungsseite geschaltet werden:

```bash
# In NGINX-Konfiguration (CloudPanel Vhost):
# Temporär auf Wartungsseite umleiten
```

---

**Nächster Schritt:** [Troubleshooting](Installation-Troubleshooting)

### Getrennte Aufbewahrung der Pre-Update-Dumps

Vor Migrationen erzeugte Dumps heißen `pre-update-einsatzleiter-<stamp>.sql.gz`
und `pre-update-einsatzleiter_weather-<stamp>.sql.gz`. `UPDATE_BACKUP_KEEP=5`
begrenzt sie separat je Datenbank. Tägliche Sicherungen behalten ihre eigene
Aufbewahrung und werden durch häufige Updates nicht verdrängt. Die Verwaltung
zeigt den Typ „Vor Update“.

Die Dateisperre `<BACKUP_DIR>/.backup.lock` serialisiert Timer, UI und Pre-Update.
Bei „Backup laeuft bereits“ wartet der Update-Backup-Schritt nicht; mit
`UPDATE_REQUIRE_BACKUP=true` bricht das Update ab. Nach Ende der Sicherung erneut
versuchen. Pro Dump wird vorab der freie Platz geprüft (letzter Dump derselben
Datenbank × 1,5; ohne Vorgänger kein Check). Ein Platzfehler oder das Gesamtzeitlimit
`BACKUP_DUMP_TIMEOUT_S=7200` veröffentlicht keinen unvollständigen Dump.
Remote-Prozesse verwenden `BACKUP_REMOTE_TIMEOUT_S=1800`, SSH zusätzlich 30 Sekunden
Verbindungszeitlimit. Details: [Backups](Installation-Backups#robuste-db-sicherungen-und-grenzen).

Das Pre-Update-Backup läuft vor dem Code-Austausch; die getrennte Aufbewahrung
bleibt dabei erhalten.
