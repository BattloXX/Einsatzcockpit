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
   Der Ablauf bleibt synchron im Web-Request; die Neustartmechanik ist unverändert.

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
