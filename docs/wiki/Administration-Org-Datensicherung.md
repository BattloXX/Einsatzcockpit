# Datensicherung je Organisation (Self-Service)

← [Zurück zur Startseite](Home)

> URL: `/admin/org-backup` · Menü: **Admin → Datensicherung**
> Backup einrichten/herunterladen: `org_admin` · Wiederherstellen: `system_admin`

Jede Organisation kann ihre **eigenen** Daten selbst sichern — als Download oder
automatisch an ein **selbst konfiguriertes Ziel**. Das ist etwas anderes als:

- der **[Org-Konfig-Export](Administration-Backup-Import)** (`/admin/backup`): nur wenige
  Konfigurationsfelder als JSON, keine operativen Daten;
- das **[serverweite Backup & Disaster-Recovery](Betrieb-Backup-und-Disaster-Recovery)**: ein
  `mariadb-dump` ALLER Organisationen, Betreiber-Aufgabe.

Hier geht es um ein **vollständiges, tenant-gescoptes Archiv genau einer Organisation**.

---

## Was ist enthalten?

Ein ZIP mit:

- `manifest.json` — Format-Version, App-Version, Quell-Org, Zeitpunkt, Zeilenzahl je Tabelle.
- `data/<tabelle>.jsonl` — die Datensätze der Organisation (Einsätze/Archiv, Mannschaft,
  Objekte, Teilnahme, Fahrtenbuch, GSL, Lageführung, Konfiguration …).
- `media/…` — die zugehörigen Mediendateien (Einsatzfotos, Objektdokumente/-scans, GSL-Medien).
- `config.json` — der Org-Konfig-Export.

**Nicht enthalten** (bewusst): server-gebundene Geheimnisse (`*_enc`: SSO-/Mail-Secrets),
API-Keys, SMS-/Gateway-/Push-Tokens sowie Sessions/Einmal-Token. Diese sind mit dem
Server-Schlüssel verschlüsselt bzw. flüchtig und nach einer Wiederherstellung neu zu setzen.
Das Archiv enthält **nur die Daten dieser einen Organisation** — Fremd-Orgs sind ausgeschlossen.

---

## Herunterladen (Org-Admin)

**Admin → Datensicherung → „Archiv herunterladen"** erzeugt das ZIP und lädt es sofort herunter.

> **Datenschutz:** Das Archiv enthält personenbezogene Daten (Mannschaft, Einsätze). Bewahren
> Sie es zugriffsbeschränkt auf.

---

## Automatisch an ein eigenes Ziel senden (Org-Admin)

Unter **„Automatische Sicherung"** ein Ziel eintragen, testen und einen Zeitplan wählen:

| Protokoll | Transport | Auth |
|-----------|-----------|------|
| `sftp` / `scp` / `rsync` | SSH | **privater SSH-Key** (empfohlen) |
| `ftps` | FTP + TLS | Passwort |
| `ftp` | FTP | Passwort — **unverschlüsselt, nur im LAN** |
| `rclone` | rclone-Remote | rclone-Config (S3, WebDAV, Backblaze, Google Drive …) |

- **Zeitplan:** täglich (ab Stunde, UTC) oder wöchentlich (Wochentag + Stunde). Ein
  Hintergrund-Loop schiebt fällige Sicherungen automatisch, höchstens einmal pro Tag.
- **Verbindung testen:** lädt eine kleine Probe-Datei hoch.
- **Jetzt sichern:** erstellt sofort ein Archiv und überträgt es.
- Zugangsdaten (Passwort / SSH-Key) werden **Fernet-verschlüsselt** gespeichert; ein leeres
  Feld beim Speichern lässt das bestehende Secret unangetastet.
- Der Status des letzten Laufs (OK/Fehler) wird angezeigt.

> Ziel und Übertragung liegen in der Verantwortung der Organisation. Ein verschlüsseltes
> Protokoll wählen (nicht `ftp` über offene Netze) und das Zielverzeichnis zugriffsbeschränkt
> halten.

---

## Wiederherstellen (System-Admin)

**Admin → Datensicherung → „Archiv wiederherstellen…"** (`/admin/org-backup/restore`):

1. Archiv hochladen → ohne Bestätigung erscheint eine **Vorschau** (Quell-Org, Tabellen, Anzahl).
2. Mit **„Wiederherstellung bestätigen"** wird eine **neue Organisation** angelegt und das Archiv
   dort importiert. Alle Datensätze erhalten **neue IDs** (ID-Remapping), Fremdschlüssel werden
   umgeschrieben, Medien zurückgelegt. **Bestehende Organisationen bleiben unverändert.**

> Ein In-place-Restore (eine laufende Org überschreibt sich selbst) ist bewusst **nicht**
> vorgesehen — er würde Datenverlust riskieren. Für Portabilität/DR wird immer in eine neue Org
> importiert.

---

## Konfiguration (.env)

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `ORG_BACKUP_ENABLED` | `true` | Globaler Kill-Switch (false = Modul aus) |
| `ORG_BACKUP_MAX_BYTES` | `2147483648` | Größenlimit je Archiv (0 = unbegrenzt) |
| `ORG_BACKUP_LOOP_INTERVAL_S` | `900` | Prüfintervall für fällige geplante Backups |

---

## Intern (Kurzüberblick)

| Bereich | Umsetzung |
|---------|-----------|
| Modell | `OrgBackupConfig` (Ziel + Zeitplan, Fernet-Secrets), Migration 0166 |
| Export | `app/services/org_export_service.py` (generischer FK-Collector, Secret-Redaktion) + `org_export_media.py` |
| Push/Zeitplan | `app/services/org_backup_loop.py` (reuse `remote_backup_service`) |
| Restore | `app/services/org_import_service.py` (ID-Remapping, Fixup, Medien) |
| UI | `app/routers/ui_org_backup.py` (`/admin/org-backup`) |

---

## Verwandte Seiten

- [Backup & Disaster-Recovery (serverweit)](Betrieb-Backup-und-Disaster-Recovery)
- [Org-Konfig-Backup (JSON)](Administration-Backup-Import)
