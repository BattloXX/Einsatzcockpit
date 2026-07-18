# Backups

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
(sonst in der Prozessliste sichtbar). Alte Backups werden gemäß `BACKUP_KEEP_DAILY`
automatisch entfernt.

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

---

**Nächster Schritt:** [Updates einspielen](Installation-Updates)
