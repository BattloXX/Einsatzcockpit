# Backups

Vor Web-Updates kann zusätzlich automatisch ein DB-Dump erzeugt und bei Fehlschlag als
harte Update-Voraussetzung behandelt werden; siehe
[Updates – Automatisches Backup vor Migrationen](Installation-Updates#automatisches-backup-vor-migrationen).

← [Zurück zur Startseite](Home)

> **Vollständiges Verfahren inkl. getesteter Restore-Probe und Disaster-Recovery:**
> [Backup & Disaster-Recovery](Betrieb-Backup-und-Disaster-Recovery). Diese Seite ist die
> Kurzanleitung.

## Empfohlen: eingebautes Backup-Tooling

Das Tool bringt Backup und **getestete Restore-Probe** mit — beide DBs
(`einsatzleiter` + `einsatzleiter_weather`) plus die Medien unter `app_storage`:

```bash
su - clp-einsatz
cd /home/clp-einsatz/htdocs/einsatzleiter
source .venv/bin/activate

python -m app.cli backup          # Dumps beider DBs + Medien nach BACKUP_DIR (mit Retention)
python -m app.cli restore-test    # spielt den neuesten Dump testweise ein und verifiziert ihn
```

Passwörter kommen über die `.env`/Umgebung (`MYSQL_PWD`), **nie** auf der Kommandozeile
(sonst in der Prozessliste sichtbar). Tägliche DB-Dumps werden nach den Retention-Regeln der Verwaltung entfernt
(Standard 30 Tage oder höchstens 7 je DB), Medien nach `BACKUP_KEEP_DAILY`
(Standard 14 Archive).

## Automatisierung (systemd-Timer)

```bash
# Als root
cp deploy/backup/ec-backup.service deploy/backup/ec-backup.timer /etc/systemd/system/
cp deploy/backup/ec-restore-test.service deploy/backup/ec-restore-test.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ec-backup.timer ec-restore-test.timer
systemctl list-timers 'ec-*'
```

Tägliches Backup 02:30, wöchentliche Restore-Probe So 04:00. (Alternativ bietet
CloudPanel unter **Settings → Backup** eigene, dateibasierte Sicherungen.)

## Was wird gesichert?

| Was | Vom Tool | Hinweis |
|-----|----------|---------|
| MariaDB `einsatzleiter` (+ `_weather`) | ✅ | konsistenter `mariadb-dump`, gzip |
| Medien `app_storage/` | ✅ | Einsatzfotos, Objektdokumente, Nachschlagewerke |
| `.env` (Secrets, `FERNET_KEY`) | ❌ | **extra sichern** — sonst SSO-/KI-/Mail-Secrets nach Restore unlesbar |
| Betriebssystem / nginx / TLS | ❌ | CloudPanel + `deploy/nginx-snippet.conf` |

Die Codebasis ist via Git versioniert und muss nicht gesondert gesichert werden.

## Off-Site (3-2-1)

Der Backup-Job kann die Dumps **automatisch** an eine Gegenstelle schieben
(SFTP/SCP/rsync/FTPS/rclone). In der `.env` aktivieren:

```env
BACKUP_REMOTE_ENABLED=true
BACKUP_REMOTE_PROTOCOL=sftp
BACKUP_REMOTE_HOST=backup.example.org
BACKUP_REMOTE_USER=ec-backup
BACKUP_REMOTE_KEY=/home/clp-einsatz/.ssh/id_ed25519
BACKUP_REMOTE_PATH=/srv/einsatzcockpit-backups
```

Testen: `python -m app.cli backup-upload`. Details, rclone-Cloud-Ziele und
Sicherheitshinweise (die Dumps sind unverschlüsselt und enthalten personenbezogene
Daten) im [DR-Runbook, Abschnitt 7](Betrieb-Backup-und-Disaster-Recovery).

## Org-Ziele und bestehende Konfigurationen

Der [Org-Self-Service](Administration-Org-Datensicherung) erlaubt nur **SFTP, FTPS
und Microsoft Graph** mit öffentlichen Zieladressen. Bestehende Org-Ziele mit SCP,
rsync, rclone oder FTP sind gesperrt und erhalten einen Fehlerstatus. Org-Admins
müssen ein erlaubtes Protokoll wählen, die SSH-Hostkey-Prüfung auf `yes` oder
`accept-new` setzen, neu speichern und testen.

Die serverweite `.env`-Konfiguration behält alle Protokolle (einschließlich SCP,
rsync, rclone und FTP) und interne Ziele. Auch hier gelten strenge Eingabeprüfungen:
SSH-Hostkey-Prüfung nur `yes`/`accept-new`/`no`, Port 0–65535, sichere Hostnamen und
Benutzernamen sowie Pfade ohne Steuerzeichen oder Anführungszeichen und ohne
führendes `-`/`!`. rclone akzeptiert ausschließlich benannte Remotes wie `offsite:`,
keine Connection-Strings wie `:sftp,ssh=...:`.

---

**Nächster Schritt:** [Updates einspielen](Installation-Updates)

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

## Backup-Monitoring einrichten

`BACKUP_MAX_AGE_H=26` und `BACKUP_RESTORE_MAX_AGE_D=8` steuern die Altersgrenzen.
Bis zur doppelten Grenze wird gewarnt, danach ist der Status `fail`; ein
fehlgeschlagener letzter Lauf ist sofort `fail`. Beim ersten Lauf entstehen
private Punktdateien `.status-backup.json` und `.status-restore.json` unter
`BACKUP_DIR`; sie werden weder hochgeladen noch von Retention/Liste erfasst.
Ohne Status greift für Dumps ein mtime-Rückfall, für Restore bleibt der Stand unbekannt.

Nach Installation beide CLI-Läufe ausführen und die Datensicherungskarte im
Gesamtzustand der Systemdiagnose oder `admin_backup_pruefen` kontrollieren.
Off-Site-Ausfälle und weniger als zweimal die letzte Dump-Größe an freiem Platz
warnen ebenfalls. Für aktive Meldungen die geplante Diagnose mit Sammelmail
konfigurieren. Optionalen `OnFailure`-Hook nur lokal einrichten; die mitgelieferten
Units enthalten kommentierte Beispiele. Details und Fehlerbilder:
[Monitoring-Runbook](Betrieb-Backup-und-Disaster-Recovery#monitoring).
