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
| Aufbewahrung | DB: **30 Tage / max. 7**, Medien: **14 Archive** | DB-Regeln in der Verwaltung, Medien über `BACKUP_KEEP_DAILY` |
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
| `BACKUP_KEEP_DAILY` | `14` | Medienanzahl, DB-Fallback bei nicht erreichbarer Policy-DB |
| `UPDATE_BACKUP_KEEP` | `5` | Pre-Update-Dumps je DB, unabhängig von täglichen Dumps |
| `BACKUP_DUMP_TIMEOUT_S` / `BACKUP_REMOTE_TIMEOUT_S` | `7200` / `1800` | Dump-/Tar-Gesamtlimit bzw. Remote-Prozess-/Socket-Timeout |
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
verwirft die Wegwerf-DB anschließend. Wetter-Dump und Medienintegrität werden
ebenfalls geprüft (Details im Abschnitt „Robuste DB-Sicherungen und Grenzen“). Die Produktions-DB wird **nie** berührt
(harte Namensprüfung im Code).

```bash
python -m app.cli restore-test           # nutzt neuesten Dump aus BACKUP_DIR
```

**Voraussetzung:** Der DB-Benutzer braucht `CREATE`/`DROP` auf die Wegwerf-DB. In
CloudPanel ggf. einmalig gewähren (als DB-root):

```sql
GRANT ALL PRIVILEGES ON `einsatzleiter_restore_test`.* TO 'einsatzleiter'@'%';
-- Bei konfigurierter Wetter-DB dem Wetter-DB-Benutzer gewähren:
GRANT ALL PRIVILEGES ON `einsatzleiter_restore_test_weather`.* TO 'einsatzleiter'@'%';
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
python3.14 -m venv .venv && source .venv/bin/activate && pip install -e .

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

## Robuste DB-Sicherungen und Grenzen

DB-Dumps werden zunächst als `<name>.sql.gz.partial`, Medienarchive als
`<name>.tar.gz.partial` geschrieben. Erst nach erfolgreichem Prozessende sowie
Flush und `fsync` wird die Datei atomar umbenannt. Teildateien zählen weder für
Retention noch Liste, Download, Restore-Probe oder Upload. Verwaiste `.partial`
werden am Beginn eines gesperrten Backup-Laufs nach 24 Stunden entfernt. Bei einem
harten Prozessabbruch bleiben sie bis dahin liegen. Verzeichnisrechte: 0700;
Dumps und Medienarchive: 0600 (auch bei einer großzügigeren Benutzer-umask).

Timer, Verwaltungsbutton und Pre-Update verwenden dieselbe nicht blockierende
Dateisperre `<BACKUP_DIR>/.backup.lock`. Ein zweiter Lauf endet mit Exit-Code 1 und
„Backup laeuft bereits“. Die Lockdatei darf bestehen bleiben; maßgeblich ist die
Betriebssystem-Sperre. Bei gleichem Sekundenzeitstempel erhalten weitere Dateien
einen eindeutigen Suffix.

Vor jedem DB-Dump wird der freie Platz gegen die Größe des letzten Dumps derselben
Datenbank (täglich oder vor Update) × 1,5 geprüft. Ohne Vorgänger entfällt die Schätzung. Die Schätzung garantiert
keinen ausreichenden Platz bei starkem Datenwachstum; ein Fehler veröffentlicht
auch dann keinen halben Dump.

```env
UPDATE_BACKUP_KEEP=5
BACKUP_DUMP_TIMEOUT_S=7200
BACKUP_REMOTE_TIMEOUT_S=1800
```

`BACKUP_DUMP_TIMEOUT_S` begrenzt jeden Dump und jeden Medien-Tar-Prozess insgesamt;
bei Überschreitung wird der Prozess beendet und seine Teildatei entfernt.
`BACKUP_REMOTE_TIMEOUT_S` begrenzt SCP/SFTP/rsync/rclone/SSH-Prozesse und FTP/FTPS-
Socketoperationen (kein Gesamtlimit für mehrere FTP-Dateien). SSH erhält zusätzlich
`ConnectTimeout=30`. Microsoft Graph verwendet sein eigenes HTTP-Timeout.
Die Backup-Unit hat `TimeoutStartSec=2h`; dieses Gesamtlimit gilt für den gesamten
Lauf einschließlich Medien und Upload. Für große Installationen beide Grenzen
bewusst aufeinander abstimmen. Ein lokales systemd-Drop-in kann unter `[Unit]`
`OnFailure=<eigene Alarmierungs-Unit>` setzen.

Pre-Update-Dumps heißen `pre-update-einsatzleiter-<stamp>.sql.gz` bzw.
`pre-update-einsatzleiter_weather-<stamp>.sql.gz`. Nur ihre eigene Anzahlgrenze
`UPDATE_BACKUP_KEEP` gilt je Datenbank; sie verdrängen keine täglichen Dumps und
werden von deren Regeln nicht entfernt. Die Verwaltung zeigt „Vor Update“ bzw.
„Täglich“. Die Retention-Felder der Verwaltung gelten für tägliche Dumps.

Die Restore-Probe wählt weiterhin den neuesten gültigen täglichen Haupt-Dump.
Sie verlangt `fire_dept` und `users` mit jeweils mindestens einer Zeile sowie genau
eine `alembic_version` mit dem aktuellen Alembic-Head des Codes. Eine Abweichung ist
ein Fehler (bei einer gezielten Probe eines Pre-Update-Dumps nur eine Warnung).
Wenn Wetter-DB konfiguriert und ein täglicher Wetter-Dump vorhanden ist, wird auch
dieser nach `<scratch>_weather` eingespielt und werden die Wettertabellen geprüft.
Die Wetter-DB wird über `create_all`, ohne Alembic, verwaltet; leere Messreihen sind
zulässig. CREATE/DROP-Rechte müssen auch für die zweite Wegwerf-DB vorhanden sein.
Beide Scratch-Namen werden gegen die Produktionsdatenbanken geprüft. Das neueste
fertige Medienarchiv wird vollständig gelesen, einschließlich gzip-Prüfsumme und
TAR-Inhalt. Fehlende Wetter-Dumps oder Medienarchive werden nicht geprüft.

## Monitoring

Der Gesamtzustand der [Systemdiagnose](Administration-Systemdiagnose) und das
parameterlose, lesende MCP-Tool `admin_backup_pruefen` prüfen dieselben lokalen
Statusdateien. Der Diagnose-Runner registriert den Check `backup`; seine Befunde
erscheinen in `admin_fehler_liste` und schließen bei erfolgreicher vollständiger
Nachprüfung automatisch. Es werden keine Fachdaten verändert.

| Indikator | ok | warn | fail |
| --- | --- | --- | --- |
| Backup-Alter | ≤ `BACKUP_MAX_AGE_H` (26 Stunden) | >26 bis einschließlich 52 Stunden | >52 Stunden |
| Restore-Probe | ≤ `BACKUP_RESTORE_MAX_AGE_D` (8 Tage) | >8 bis einschließlich 16 Tage | >16 Tage |
| Letzter Lauf fehlgeschlagen | — | — | unabhängig vom Alter |
| Off-Site aktiviert | letzter Upload erfolgreich | Upload fehlgeschlagen oder nicht belegt | — |
| Freier Speicher | mindestens zweimal Größe des letzten Dumps | darunter | — |

Die Grenzen sind konfigurierbar; Warnung reicht jeweils bis einschließlich des
doppelten Werts. Ein veraltetes Medienarchiv ist nur ein Hinweis. Fehlende oder
beschädigte Statusdaten ergeben `unbekannt`, sofern kein belegter Fehler oder
Warnindikator Vorrang hat. Unbekannte Sicherung/Probe erzeugen dennoch einen
Warnbefund mit Handlungsanweisung, also keine stillschweigende Entwarnung.

`BACKUP_DIR/.status-backup.json` enthält UTC-Abschlusszeit mit Z, Ergebnis,
Typ (`daily`/`pre-update`), erzeugte Dateinamen und Größen, bereinigte Fehler
und Off-Site-Ergebnis (`enabled`, `ok`, neutrale Zielbeschreibung ohne Zugangsdaten).
`BACKUP_DIR/.status-restore.json` enthält Abschlusszeit, Ergebnis, Haupt-Dump-Dateiname,
Namen der versuchten DB-Prüfungen und Warnungen (etwa fehlendes Medienarchiv).
Statusdateien werden atomar mit 0600 ersetzt, Backup-Status noch unter dem
Backup-Lock. Schreibfehler werden gewarnt und ändern den Rückgabecode des Laufs
nicht. Retention, Dump-Liste, Medien-Prune und Upload ignorieren die Punktdateien.
Das Monitoring gibt nur erlaubte Felder aus, keine beliebigen Fehlertexte,
Datenbankinhalte, URLs oder externen Pfade.

Ohne gültigen erfolgreichen Backup-Status dient der jüngste gültig benannte
Dump nach **mtime** als Altersbeleg; `.partial` zählt nie. Dies ist ein historischer
Dateibeleg und keine Integritätsgarantie. Kopieren/Ändern der mtime beeinflusst
diesen Rückfall. Ein explizit fehlgeschlagener letzter Lauf bleibt `fail`.
`backup-upload` allein erneuert den Laufstatus nicht; zur Bestätigung einen
vollständigen Backup-Lauf ausführen. Off-Site-Alter ist nur bei belegtem Erfolg
im letzten Backup-Lauf bekannt.

| Fehlerbild / stabiler Befundschlüssel | Nächster Schritt |
| --- | --- |
| `backup_zu_alt` | `systemctl status ec-backup.timer`, danach `python -m app.cli backup` |
| `backup_fehlgeschlagen` | `journalctl -u ec-backup.service`, Ursache beheben und Backup wiederholen |
| `restore_probe_zu_alt` | `python -m app.cli restore-test`, Wegwerf-DB-Rechte und Dump prüfen |
| `offsite_fehlgeschlagen` | Remote-Konfiguration und Dienstjournal prüfen, Backup wiederholen |
| `speicher_knapp` | Speicher erweitern oder kontrolliert freigeben, Retention prüfen |

Die Karte im Gesamtzustand zeigt Backup-Alter, Restore-Probe, Off-Site und
Empfehlungen. Für aktive Benachrichtigungen geplante Diagnose und Sammelmail
mit passendem Schweregrad einrichten. Die Sammelmail benötigt einen gespeicherten
Diagnoselauf; es gibt keinen separaten CLI-Befehl `backup-melden`.

### Optionaler systemd-Fehler-Hook

Die ausgelieferten Units aktivieren **kein** `OnFailure`. Für vom App-Prozess und
der DB unabhängige Alarmierung kann der Betreiber ein lokales Drop-in verwenden:

```ini
# systemctl edit ec-backup.service (analog ec-restore-test.service)
[Unit]
OnFailure=ec-backup-failure@%n.service
```

Beispiel einer **lokal anzulegenden** `/etc/systemd/system/ec-backup-failure@.service`:

```ini
[Unit]
Description=Backup-Fehler ins Betriebsmonitoring melden
[Service]
Type=oneshot
ExecStart=/usr/bin/systemd-cat -t ec-backup-failure -p err /usr/bin/echo Sicherungs-Unit fehlgeschlagen: %i
```

Alternativ im lokalen Hook einen eingerichteten Mailversand nutzen, etwa
`printf '%s\n' "Sicherungs-Unit fehlgeschlagen: $1" | mail -s 'Backup-Fehler' betrieb@example.org`
in einem betreiberseitigen Skript mit Unit als Argument. Mailprogramm, MTA und
Empfänger müssen installiert bzw. eingerichtet sein. Danach `systemctl daemon-reload`
und den lokalen Hook kontrolliert testen. `systemd-cat` schreibt nur ins Journal;
erst ein externes Journal-Monitoring oder Mailtransport stellt aktiv zu. Ein
komplett ausbleibender Timer-Lauf löst `OnFailure` nicht aus: dafür Alterscheck
regelmäßig abrufen oder die Tagesdiagnose aktivieren.
