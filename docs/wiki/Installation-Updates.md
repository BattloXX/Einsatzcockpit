# Updates einspielen

← [Zurück zur Startseite](Home.md)

## Auto-Update über das Webinterface (empfohlen)

Unter **`/admin/system/update`** (nur `system_admin`) gibt es drei Wege, ganz ohne SSH:

### 1. GitHub-Release (Produktion)

„Auf Updates prüfen" vergleicht die installierte Version mit dem neuesten GitHub-Release
(optional inklusive Pre-Releases) und zeigt Release-Notes an. Auf einer Git-Installation
wird der Release-Tag per `git fetch` geholt und als detached HEAD ausgecheckt. Nur bei
Installationen ohne `.git` wird das Release-ZIP als geschütztes Overlay eingespielt.

### 2. Direkt vom Repository (Branch — Hotfixes & Testsysteme)

Der Abschnitt **„Direkt vom Repository"** lädt den aktuellen Stand eines beliebigen
Branches (Standard `main`) ein — ohne Release. Git-Installationen werden per
`fetch`/`reset --hard` synchronisiert; ohne `.git` dient der Zipball als Fallback.
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
2. ZIPs werden strukturell validiert (Zip-Slip-/Symlink-Schutz; GitHub-Zipballs mit Root-Ordner werden erkannt).
3. Geschützte Dateien bleiben im ZIP-Pfad unangetastet (`.env`, `alembic/versions/`,
   `app/static/img/uploads/`, `app_storage/`). Git-Updates aktualisieren dagegen auch die
   mit dem Release ausgelieferten Migrationen; ignorierte Nutzerdaten bleiben erhalten.
4. Vor Migrationen wird automatisch ein DB-Backup erstellt.
5. Optional **Abhängigkeiten installieren** (`pip install -e .`) — bei GitHub-Updates
   standardmäßig aktiv, damit neue Dependencies (z. B. `pdf2image`) automatisch nachgezogen werden
6. `alembic upgrade head`
7. Gunicorn-Reload (SIGHUP) bzw. `systemctl restart`

### Automatisches Backup vor Migrationen

`UPDATE_BACKUP_BEFORE_MIGRATE=true` erstellt vor jedem Migrationslauf DB-Dumps ohne
Medienarchiv. Sie landen in `BACKUP_DIR` und unterliegen `BACKUP_KEEP_DAILY`. Mit dem
Standard `UPDATE_REQUIRE_BACKUP=true` stoppt ein fehlgeschlagenes Backup das Update nach
dem Code-Austausch bewusst vor Dependencies, Migration und Reload. Bei `false` wird die
Backup-Warnung angezeigt, das Update läuft aber weiter. Restore-Anleitung und DR-Runbook:
[Backups](Installation-Backups.md).

### Privates Repository: GitHub-Token

Ist das Repository privat, auf der Update-Seite einen **Fine-grained Personal Access Token**
mit Berechtigung *Contents: Read* auf das Repo hinterlegen (Feld „GitHub-Zugriffstoken").
Der Token wird Fernet-verschlüsselt in den SystemSettings gespeichert und für Release-Check,
Branch-Check und Downloads verwendet. Leer speichern löscht ihn. Alle Update-Aktionen und
Token-Änderungen landen im Audit-Log.

## Standard-Update-Prozess (SSH, Alternative)

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

`.git` wird durch `.dockerignore` nicht ins Image kopiert. Deshalb ist
`is_git_checkout()` im App-Container immer `False`: Release- und Branch-Installationen
über das Webinterface verwenden in Docker stets den ZIP-Overlay-Pfad, niemals Git. Der
empfohlene Docker-Updateweg bleibt `docker compose pull && docker compose up -d --build`.
Backup vor Migrationen, `app_storage`-Schutz, Locking, Fehleranzeige und Hash-Pflicht
gelten auch für ZIP-Updates im Container.

Falls ein eigenes Image den mitgelieferten Entrypoint nicht verwendet, Migrationen
vor der Freigabe manuell ausführen:

```bash
docker compose exec app alembic upgrade head
```

## Rollback nach fehlgeschlagenem Update

Bei einem Dependency- oder Migrationsfehler wird **kein automatischer Reload** ausgelöst;
der laufende Container/Prozess bleibt aktiv und der Fehler wird im Update-Ergebnis
angezeigt. Nach Fehlerbehebung bzw. Restore aus `BACKUP_DIR` den Dienst manuell neu starten.

```bash
# Zur vorherigen Alembic-Revision:
alembic downgrade -1

# Code auf vorherigen Stand zurück:
git log --oneline -5
git checkout <commit-hash>

# Dienst neu starten:
sudo systemctl restart einsatzleiter
```

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

**Nächster Schritt:** [Troubleshooting](Installation-Troubleshooting.md)
