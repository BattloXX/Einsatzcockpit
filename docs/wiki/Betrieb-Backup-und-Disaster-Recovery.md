# Backup & Disaster-Recovery

← [Zurück zur Startseite](Home)

> Zielgruppe: Betreiber / System-Admin · Betrifft: Produktivserver (Debian 12 + CloudPanel + MariaDB)

Dieses Runbook beschreibt die **automatisierten Datensicherungen**, die **getestete
Restore-Probe** und das **vollständige Wiederherstellungsverfahren** bis zum
Totalausfall. Es ergänzt den Org-Konfig-JSON-Export (im Tool unter `/backup`,
`app/routers/ui_backup.py`), der nur die *Konfiguration* einer Organisation abdeckt —
**nicht** Einsatzdaten, Mannschaft, Medien oder mehrere Organisationen.

---

## 1. Schutzziele (RPO / RTO)

| Kennzahl | Zielwert | Begründung |
|----------|----------|------------|
| **RPO** (max. Datenverlust) | **24 h** | tägliches Backup 02:30; bei Bedarf häufiger (siehe Timer) |
| **RTO** (max. Ausfallzeit) | **~2 h** | Neuinstallation + Restore nach diesem Runbook |
| Aufbewahrung | **14 Tage** rollierend | `BACKUP_KEEP_DAILY`, je DB und Medien getrennt |
| Restore-Nachweis | **wöchentlich** | automatische Restore-Probe (So 04:00) |

---

## 2. Was gesichert wird — und was nicht

**Gesichert (automatisch, `python -m app.cli backup`):**

- **Haupt-Datenbank** `einsatzleiter` — vollständiger, konsistenter `mariadb-dump`
  (`--single-transaction`, inkl. Routinen/Trigger/Events), gzip-komprimiert.
- **Wetter-Datenbank** `einsatzleiter_weather` (falls `WEATHER_DATABASE_URL` gesetzt).
- **Medien** unter `app_storage/` als `tar.gz` — Einsatzfotos, Objektdokumente/-scans,
  Nachschlagewerke-Daten. (Das Backup-Verzeichnis selbst wird ausgeschlossen.)

**NICHT vom Backup-Job erfasst — separat sichern:**

- **`.env`** mit den Secrets. **Kritisch:** ohne `FERNET_KEY` und `SECRET_KEY` ist ein
  Dump nur **teilweise** nutzbar:
  - **`FERNET_KEY`** entschlüsselt gespeicherte **SSO-Client-Secrets, KI-API-Keys,
    Mail-/Graph-Secrets, verschlüsselte Monitor-URLs**. Geht er verloren, sind diese
    Felder in der wiederhergestellten DB **unlesbar** und müssen neu eingetragen werden.
  - **`SECRET_KEY`** entwertet bei Verlust alle aktiven Sessions/CSRF-Tokens (unkritisch,
    Nutzer müssen sich neu anmelden) — **außer** wenn `FERNET_KEY` leer ist und der
    Datenschlüssel aus `SECRET_KEY` abgeleitet wird (dann gilt die `FERNET_KEY`-Warnung
    sinngemäß für `SECRET_KEY`).
  → **`.env` verschlüsselt und getrennt vom DB-Dump aufbewahren** (z. B. Passwortmanager
  oder GPG-Datei im Offsite-Speicher). Am besten **einmal ausdrucken** und im Safe ablegen.
- **Betriebssystem, CloudPanel-Konfiguration, nginx-Vhost, TLS-Zertifikate** — CloudPanel
  reproduziert diese beim Neuaufbau; das nginx-Snippet liegt im Repo
  (`deploy/nginx-snippet.conf`).

> **3-2-1-Regel:** Die Dumps liegen zunächst lokal in `BACKUP_DIR`
> (`app_storage/backups`). Für echten Katastrophenschutz **mindestens eine Kopie an
> einen anderen Ort** spiegeln (rsync/rclone zu Offsite-Storage, S3, zweiter Server).
> Ein Backup nur auf demselben Server überlebt keinen Serververlust. Siehe Abschnitt 7.

---

## 3. Einrichtung der automatischen Backups

```bash
# Als root: Units installieren
cp deploy/backup/ec-backup.service   /etc/systemd/system/
cp deploy/backup/ec-backup.timer     /etc/systemd/system/
cp deploy/backup/ec-restore-test.service /etc/systemd/system/
cp deploy/backup/ec-restore-test.timer   /etc/systemd/system/
systemctl daemon-reload

# Tägliches Backup + wöchentliche Restore-Probe aktivieren
systemctl enable --now ec-backup.timer
systemctl enable --now ec-restore-test.timer

# Kontrolle
systemctl list-timers 'ec-*'
```

Konfiguration über die `.env` (Defaults in `app/config.py`):

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `BACKUP_DIR` | `app_storage/backups` | Zielverzeichnis der Dumps |
| `BACKUP_KEEP_DAILY` | `14` | behaltene Backups je DB/Medien |
| `BACKUP_INCLUDE_MEDIA` | `true` | Medien-`tar.gz` miterzeugen |
| `BACKUP_DUMP_BIN` / `BACKUP_CLIENT_BIN` | `mariadb-dump` / `mariadb` | Binaries (ggf. absoluter Pfad) |
| `BACKUP_RESTORE_TEST_DB` | `einsatzleiter_restore_test` | Wegwerf-DB der Restore-Probe |

---

## 4. Manuelles Backup

```bash
su - clp-einsatz
cd /home/clp-einsatz/htdocs/einsatzleiter
source .venv/bin/activate
python -m app.cli backup                 # beide DBs + Medien nach BACKUP_DIR
python -m app.cli backup --out /mnt/extern --no-media --keep 30
```

Ergebnis: `einsatzleiter-JJJJMMTT-HHMMSSZ.sql.gz`,
`einsatzleiter_weather-…​.sql.gz`, `medien-…​.tar.gz`. Ältere werden gemäß `--keep`
automatisch entfernt. Exit-Code ≠ 0 bei Fehlern (für Monitoring auswertbar).

---

## 5. Restore-Probe (getesteter Dump)

Der Job beweist, dass der **neueste** Haupt-Dump **tatsächlich wiederherstellbar** ist:
er spielt ihn in eine **Wegwerf-DB** ein, prüft `alembic_version` und Kerntabellen und
verwirft die Wegwerf-DB anschließend. Die Produktions-DB wird **nie** berührt
(harte Namensprüfung im Code).

```bash
python -m app.cli restore-test           # nutzt neuesten Dump aus BACKUP_DIR
```

**Voraussetzung:** Der DB-Benutzer braucht `CREATE`/`DROP` auf die Wegwerf-DB. In
CloudPanel ggf. einmalig gewähren (als DB-root):

```sql
GRANT ALL PRIVILEGES ON `einsatzleiter_restore_test`.* TO 'einsatzleiter'@'%';
FLUSH PRIVILEGES;
```

Fällt die Probe durch (Exit ≠ 0), ist das ein **Alarm**: die Backups sind wertlos, bis
die Ursache behoben ist. Empfehlung: `OnFailure=`-Benachrichtigung an die Units hängen.

---

## 6. Vollständige Wiederherstellung (Disaster-Recovery)

Ausgangslage: Server verloren, es liegen die **DB-Dumps**, das **Medien-Archiv** und
die **`.env`** (getrennt gesichert) vor.

```bash
# 0) Neuen Debian-12-Server + CloudPanel aufsetzen, Site + DB-Benutzer anlegen
#    (wie deploy/README-Deployment.md, Schritte 1–3), Systemabhängigkeiten installieren.

su - clp-einsatz
cd /home/clp-einsatz/htdocs/
git clone https://github.com/BattloXX/Einsatzcockpit.git einsatzleiter
cd einsatzleiter
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e .

# 1) Secrets zurückspielen — die gesicherte .env an ihren Platz
cp /sicherer-ort/.env .env
#    Kontrolle: SECRET_KEY, FERNET_KEY, DATABASE_URL, VAPID-Keys vorhanden?

# 2) Datenbanken anlegen (leer) — in CloudPanel oder als DB-root
#    CREATE DATABASE einsatzleiter CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
#    (analog einsatzleiter_weather, falls genutzt)

# 3) Dumps einspielen (Schema + Daten stammen aus dem Dump — KEIN alembic upgrade nötig)
gunzip -c einsatzleiter-JJJJMMTT-HHMMSSZ.sql.gz \
  | mariadb --host=127.0.0.1 --user=einsatzleiter -p einsatzleiter
gunzip -c einsatzleiter_weather-JJJJMMTT-HHMMSSZ.sql.gz \
  | mariadb --host=127.0.0.1 --user=einsatzleiter -p einsatzleiter_weather

# 4) Medien zurückspielen (in das Verzeichnis, das app_storage enthält)
tar -xzf medien-JJJJMMTT-HHMMSSZ.tar.gz -C /home/clp-einsatz/htdocs/einsatzleiter/

# 5) Falls der Dump älter als der Code ist: Schemastand nachziehen
alembic upgrade head

# 6) Service + nginx + TLS wie im Deployment-README (Schritte 8–10), dann starten
sudo systemctl enable --now einsatzleiter
journalctl -u einsatzleiter -f
```

**Verifikation nach Restore:**

1. Login als System-Admin möglich.
2. Ein bestehender Einsatz/Archiv-Eintrag ist sichtbar (DB ok).
3. Ein Einsatzfoto/Objektdokument lädt (Medien ok).
4. SSO/KI/Mail: Falls „Secret ungültig" — `FERNET_KEY` stimmt nicht mit dem Stand zur
   Verschlüsselung überein → Secrets in den Admin-Seiten neu eintragen.

---

## 7. Offsite-Upload (eingebaut)

Das Backup lädt die frisch erzeugten Dumps **automatisch** an eine Gegenstelle, sobald
`BACKUP_REMOTE_ENABLED=true` gesetzt ist — direkt am Ende jedes `app.cli backup`-Laufs
(also auch über den täglichen Timer). Unterstützte Protokolle:

| Protokoll | Transport | Auth | Hinweis |
|-----------|-----------|------|---------|
| `sftp` | SSH | **SSH-Key** | empfohlen, verschlüsselt |
| `scp` | SSH | SSH-Key | verschlüsselt |
| `rsync` | SSH | SSH-Key | verschlüsselt, `--partial` (Wiederaufnahme) |
| `ftps` | FTP+TLS | Passwort | verschlüsselt |
| `ftp` | FTP | Passwort | **unverschlüsselt — nur im LAN** |
| `rclone` | rclone-Remote | rclone-Config | Catch-all: S3, Backblaze B2, WebDAV, Google Drive … |

Konfiguration in der `.env` (Ausschnitt, vollständige Liste in `.env.example`):

```env
BACKUP_REMOTE_ENABLED=true
BACKUP_REMOTE_PROTOCOL=sftp
BACKUP_REMOTE_HOST=backup.example.org
BACKUP_REMOTE_USER=ec-backup
BACKUP_REMOTE_KEY=/home/clp-einsatz/.ssh/id_ed25519      # SSH-Key ohne Passphrase (für Automatik)
BACKUP_REMOTE_PATH=/srv/einsatzcockpit-backups
```

**Passwörter/Keys erscheinen nie in der Prozessliste:** SSH-Protokolle nutzen Key-Auth
(`BatchMode=yes`, kein interaktiver Prompt), FTP/FTPS die Zugangsdaten über das Protokoll,
nicht die Kommandozeile.

Manuell auslösen / Remote-Konfiguration testen (ohne neuen Dump):

```bash
python -m app.cli backup-upload          # nur die neuesten Dumps je Typ
python -m app.cli backup-upload --all    # alle Backups im BACKUP_DIR
```

**Einrichtung SSH-Key** (einmalig, für sftp/scp/rsync):

```bash
su - clp-einsatz
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ''        # ohne Passphrase → automatikfähig
ssh-copy-id -i ~/.ssh/id_ed25519 ec-backup@backup.example.org
# Ersten Verbindungsaufbau einmal bestätigen ODER known_hosts vorbefüllen; dann ggf.
# BACKUP_REMOTE_SSH_STRICT=yes setzen (strikte Hostkey-Prüfung).
```

> **rclone** deckt Cloud-Ziele ab: `rclone config` einrichten, dann
> `BACKUP_REMOTE_PROTOCOL=rclone` und `BACKUP_REMOTE_RCLONE_REMOTE=offsite:` (bzw.
> `s3:bucket`) setzen. Der Upload läuft als `rclone copy <BACKUP_DIR> <remote:pfad>`.

> **Datenschutz:** Die Dumps sind **unverschlüsselt** und enthalten personenbezogene Daten
> (Mitglieder, Telefonnummern, Einsatzdaten). Gegenstelle und Übertragung
> **zugriffsbeschränkt** halten: ein verschlüsseltes Protokoll wählen (sftp/scp/rsync/ftps,
> **nicht** ftp über offene Netze), das Zielverzeichnis auf `700` beschränken und bei
> erhöhtem Schutzbedarf serverseitige Verschlüsselung bzw. `rclone crypt` nutzen.

Ein fehlgeschlagener Upload setzt den Backup-Exit-Code auf ≠ 0 (der lokale Dump bleibt
erhalten) — so schlägt das Monitoring an, wenn die Offsite-Kopie ausbleibt.

---

## 8. Überwachung

```bash
systemctl list-timers 'ec-*'              # nächste/letzte Läufe
systemctl status ec-backup.service         # Ergebnis des letzten Backups
journalctl -u ec-backup.service --since today
journalctl -u ec-restore-test.service -n 50
```

Empfehlung: eine `OnFailure=`-Unit (Mail/Teams/Healthcheck-Ping) an `ec-backup.service`
und `ec-restore-test.service` hängen, damit ein stiller Ausfall auffällt.

---

## 9. Bezug zur CRA-Compliance

Der Cyber Resilience Act verlangt u. a. Wiederherstellbarkeit und Verfügbarkeit. Dieses
Runbook liefert den Nachweis über: **automatisierte** Sicherung (Abschnitt 3),
**regelmäßig getestete** Wiederherstellbarkeit (Abschnitt 5, wöchentliche Restore-Probe)
und ein **dokumentiertes** Wiederherstellungsverfahren mit RPO/RTO (Abschnitte 1, 6).
Ergänzend gehören dazu die getrennte Secret-Sicherung (Abschnitt 2) und die
Offsite-Spiegelung (Abschnitt 7).

---

## Verwandte Seiten

- [Backups (Kurzanleitung)](Installation-Backups)
- [Deployment](../deploy/README-Deployment.md)
- Org-Konfig-Backup (JSON-Export/Import): im Tool unter `/backup`
