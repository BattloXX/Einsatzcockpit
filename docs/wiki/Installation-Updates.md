# Updates einspielen

← [Zurück zur Startseite](Home)

## Auto-Update über das Webinterface (empfohlen)

Unter **`/admin/system/update`** (nur `system_admin`) gibt es drei Wege, ganz ohne SSH:

### 1. GitHub-Release (Produktion)

„Auf Updates prüfen" vergleicht die installierte Version mit dem neuesten GitHub-Release
(optional inklusive Pre-Releases) und zeigt Release-Notes an. „Server-Update einspielen"
lädt das Release-ZIP herunter und spielt es ein.

### 2. Direkt vom Repository (Branch — Hotfixes & Testsysteme)

Der Abschnitt **„Direkt vom Repository"** lädt den aktuellen Stand eines beliebigen
Branches (Standard `main`) als Zipball von GitHub und spielt ihn ein — ohne Release.
Die Branch-Auswahl zeigt den letzten Commit (SHA, Nachricht, Autor); ein bereits
eingespielter Stand wird erkannt und der Button deaktiviert. Der zuletzt eingespielte
Branch-Stand (`branch@sha`) wird gespeichert und oben auf der Seite angezeigt.

> Gedacht für Hotfixes und Testsysteme — für Produktion sind Releases der sauberere Weg.

### 3. ZIP-Upload (manuell, Fallback)

Wie bisher: Release-ZIP herunterladen und über das Formular hochladen
(optional mit SHA256-Prüfung).

### Ablauf bei allen drei Wegen

1. ZIP strukturell validieren (Zip-Slip-/Symlink-Schutz; GitHub-Zipballs mit Root-Ordner werden erkannt)
2. Geschützte Dateien bleiben unangetastet (`.env`, `alembic/versions/`, Uploads)
3. Optional **Abhängigkeiten installieren** (`pip install -e .`) — bei GitHub-Updates
   standardmäßig aktiv, damit neue Dependencies (z. B. `pdf2image`) automatisch nachgezogen werden
4. `alembic upgrade head`
5. Gunicorn-Reload (SIGHUP) bzw. `systemctl restart`

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

## Rollback nach fehlgeschlagenem Update

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

**Nächster Schritt:** [Troubleshooting](Installation-Troubleshooting)
