# Multi-Tenancy Migration Runbook

**Version:** 2.2.0  
**Datum:** 2026-06-11  
**Branch:** `feature/multi-tenancy` (merged to `main`)

---

## Übersicht

Dieses Runbook beschreibt den Migrationspfad von einer Single-Org-Instanz (v2.0.x) zur Multi-Tenancy-Architektur (v2.2.0). Die Migration erfolgt in 12 PRs, jeder mit einem eigenen Alembic-Skript.

---

## Pre-Flight-Checkliste

Vor der Migration prüfen:

- [ ] Datenbank-Backup erstellt (vollständiges Dump)
- [ ] Applikation gestoppt (kein laufender Prozess auf Port 8092)
- [ ] Python-Virtualenv aktiv und alle Abhängigkeiten installiert
- [ ] `.env` enthält `SECRET_KEY` (≥ 32 Zeichen, nicht der Platzhalter)
- [ ] `alembic.ini` zeigt auf die korrekte Datenbank-URL

---

## Migrationsskripte in Reihenfolge

| Revision | Datei | Inhalt |
|----------|-------|--------|
| 0044 | `0044_multitenancy_pr1_infrastructure.py` | `fire_dept`-Erweiterung: slug, is_home_org, is_active, deleted_at, contact_*, timezone, short_code |
| 0045 | `0045_multitenancy_pr2_expand.py` | Expand: neue Nullable-Spalten für Benutzer und Einsätze |
| 0046 | `0046_multitenancy_pr2_migrate.py` | Datenmigration: erste Org anlegen, bestehende Zeilen zuweisen |
| 0047 | `0047_multitenancy_pr2_contract.py` | Contract: NOT NULL setzen, alte Spalten entfernen |
| 0048 | `0048_multitenancy_pr3_expand.py` | Org-Einstellungen: `org_settings`-Tabelle, Spalten für Settings |
| 0049 | `0049_multitenancy_pr3_migrate.py` | Datenmigration: bestehende SystemSettings-Werte in OrgSettings |
| 0050 | `0050_multitenancy_pr3_contract.py` | Contract: Constraints setzen |
| 0051 | `0051_multitenancy_pr4_seed_templates.py` | Seed-Vorlagen-Tabelle (`seed_template`) |
| 0052 | `0052_multitenancy_pr5_ai_per_org.py` | KI-Prompts per Org: `ai_prompt_version.org_id` |
| 0053 | `0053_multitenancy_pr6_storage_quota.py` | `fire_dept.storage_quota_bytes` |
| 0054 | `0054_multitenancy_pr7_invitations.py` | `org_invitation`-Tabelle, `incident_token`-Tabelle |
| 0055 | `0055_multitenancy_pr8_autoclose_backup.py` | `org_settings`: autoclose_enabled, autoclose_after_hours, autoclose_grace_minutes |

---

## Migrations-Ausführung

```bash
# 1. Alle Migrationen sequenziell anwenden:
alembic upgrade head

# 2. Aktuellen Revisions-Stand prüfen:
alembic current

# Erwarteter Output nach vollständiger Migration:
# 0055_multitenancy_pr8_autoclose_backup (head)
```

Bei einem Fehler in einer Intermediate-Migration:

```bash
# Zurück auf den Stand vor dem fehlgeschlagenen Skript:
alembic downgrade <vorherige_revision>
# Beispiel: downgrade auf vor 0046:
alembic downgrade 0045
```

---

## Post-Migration-Verifikation

Nach erfolgreichem `alembic upgrade head`:

1. **Applikation starten** und auf Startfehler prüfen:
   ```bash
   uvicorn app.main:app --port 8092
   ```

2. **Admin-Dashboard** öffnen (`/admin`) → KPI-Werte müssen plausibel sein.

3. **System-Konsole** öffnen (`/admin/system/orgs`) → Org-Tabelle muss mindestens eine Zeile zeigen (die Home-Org).

4. **API-Endpunkt testen:**
   ```bash
   curl -X POST http://localhost:8092/api/v1/einsatz \
     -H "X-API-Key: <key>" \
     -H "Content-Type: application/json" \
     -d '{"Key": "TEST-001"}'
   ```

5. **Audit-Log** prüfen (`/admin/audit`) → keine unerwarteten Fehlereinträge.

---

## Bekannte Fallstricke beim Schreiben neuer Migrationen

### BIGINT-Regel: FK-Spalten auf `fire_dept.id` müssen BIGINT sein

`fire_dept.id` wurde in Migration 0001 als `BIGINT` angelegt. **Alle** FK-Spalten, die darauf zeigen, müssen ebenfalls `BIGINT` sein — nicht `INT`. MariaDB erzeugt bei Typabweichung:

```
errno: 150 "Foreign key constraint is incorrectly formed"
```

Falsch → richtig:

```sql
-- FALSCH (INT ≠ BIGINT → errno 150 beim FK-Anlegen):
ADD COLUMN org_id INT NULL DEFAULT NULL,
ADD CONSTRAINT fk_... FOREIGN KEY (org_id) REFERENCES fire_dept(id) ...

-- RICHTIG:
ADD COLUMN org_id BIGINT NULL DEFAULT NULL,
ADD CONSTRAINT fk_... FOREIGN KEY (org_id) REFERENCES fire_dept(id) ...
```

Gleiches gilt für alle anderen PKs in diesem Projekt (`user.id`, `incident.id`, `api_key.id`, `member.id` usw.) — sie sind alle `BIGINT`. Entsprechend müssen auch FK-Spalten auf diese Tabellen `BIGINT` sein.

Im SQLAlchemy-ORM: `mapped_column(BigInteger, ForeignKey("fire_dept.id"), ...)` — **nicht** `Integer`.

### Idempotenz: Fehler in Migrate-Schritt hinterlässt Teilzustand

Schlägt eine Migration in einem mittleren Schritt fehl (z.B. FK-Anlegen), hat der erste Schritt (ADD COLUMN) bereits Wirkung gezeigt. Beim Retry scheitert dann `ADD COLUMN` erneut mit „Duplicate column". Neu geschriebene Migrationen sollten daher idempotent sein:

```python
# Prüfen ob Spalte existiert, bevor ADD COLUMN ausgeführt wird:
r = conn.execute(text(
    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS"
    " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='t' AND COLUMN_NAME='c'"
))
if r.scalar() == 0:
    conn.execute(text("ALTER TABLE t ADD COLUMN c BIGINT NULL"))

# FK nur anlegen wenn noch nicht vorhanden:
r = conn.execute(text(
    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS"
    " WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='t' AND CONSTRAINT_NAME='fk_...'"
))
if r.scalar() == 0:
    conn.execute(text("ALTER TABLE t ADD CONSTRAINT fk_... FOREIGN KEY ..."))
```

Migration 0044 ist das Referenzbeispiel für diese idempotente Bauweise.

---

## Bekannte Einschränkungen

### `db.get()` umgeht den Tenant-Filter

SQLAlchemy `Session.get()` lädt direkt über den Primary Key — der `do_orm_execute`-Event-Handler für Row-Level-Isolation greift dabei **nicht**. Router-Code, der `db.get(Model, id)` verwendet, muss daher nach dem Laden selbst prüfen, ob `obj.org_id == user.org_id`.

**Betroffene Stellen:** Alle Mutation-Endpoints in `ui_incident.py` (Aufträge, Fahrzeuge, Journaleinträge) sowie `ui_admin.py` für Mitglieder und Fahrzeuge. Diese Prüfungen sind über `same_org_or_system_admin()` bereits implementiert.

**Nicht betroffen:** List-Endpoints, die `db.query(Model)` verwenden — der Event-Handler filtert dort korrekt.

### Einsatz-Kollaboration in KPIs

Die Einsatz-Zähler in der System-Konsole (`/admin/system/orgs`) basieren auf `primary_org_id`. Kollaborative Einsätze (über `IncidentOrg`) werden nicht mitgezählt. Das Admin-Dashboard (`/admin`) zählt kollaborative Einsätze hingegen korrekt via `visible_incidents_q()`.

---

## Rollback

Falls ein vollständiger Rollback auf v2.0.x notwendig ist:

```bash
# 1. Applikation stoppen
# 2. Datenbank aus Backup wiederherstellen (kein Alembic-Downgrade für Datenmigrations-Skripte!)
# 3. Letzten stabilen Release-Stand deployieren
```

> **Achtung:** Die Expand/Migrate/Contract-Migrationen (0045–0047, 0048–0050) lassen sich nur zurückrollen, wenn **keine** neuen Daten im neuen Schema gespeichert wurden. Bei produktivem Betrieb ist ein Rollback auf v2.0.x nur über Datenbank-Restore möglich.

---

## Konfigurationsänderungen (`.env`)

Die folgenden neuen Umgebungsvariablen wurden in v2.2.0 eingeführt:

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `LOGIN_RATELIMIT` | `10/minute` | Rate-Limit für POST /login |
| `API_ALARM_RATELIMIT` | `60/minute` | Rate-Limit für Alarm-API |
| `UPLOAD_RATELIMIT` | `20/minute` | Rate-Limit für Medien-Uploads |

Alle Variablen haben sinnvolle Defaults und müssen nicht explizit gesetzt werden.

## DB-Sicherung vor Migrationen (Backup-Härtung)

Automatische Pre-Update-Dumps liegen unter `BACKUP_DIR` als
`pre-update-einsatzleiter-<stamp>.sql.gz` und optional
`pre-update-einsatzleiter_weather-<stamp>.sql.gz`. Sie haben eine eigene
Anzahlgrenze `UPDATE_BACKUP_KEEP` (Standard 5 je DB). Nur fertige Dateien ohne
`.partial` zum Wiederherstellen verwenden. Dateien sind 0600, das Verzeichnis
0700. Vor manuellen Migrationen weiterhin eine Sicherung erstellen und prüfen.

Bei belegter `.backup.lock`, Platzmangel (Schätzung: letzter Dump × 1,5) oder
Timeout (`BACKUP_DUMP_TIMEOUT_S=7200`, Remote: `BACKUP_REMOTE_TIMEOUT_S=1800`) erst
die Ursache beheben und den Sicherungslauf wiederholen. Die wöchentliche Probe
prüft tägliche Dumps gegen den aktuellen Alembic-Head; ein Dump vor einer
Migration kann erwartungsgemäß eine ältere Revision enthalten. Die Wetter-Probe
benötigt zusätzlich CREATE/DROP auf `<BACKUP_RESTORE_TEST_DB>_weather`.

## Infoscreen-Agent: Migrationen 0355–0358

Die lineare Kette lautet **0354 → 0355 → 0356 → 0357 → 0358**.
Mit dem üblichen `alembic upgrade head` im Deployment ausführen, nicht einzelne
Dateien manuell importieren. S4/S5/S7 benötigen keine zusätzliche Migration.

| Revision | Änderung |
| --- | --- |
| 0355 | Geräte, Displays, Wochenpläne/Slots/Ausnahmen, Overrides, Events, Releases und Host-Allowlist mit Org-FKs; Presence und Token-Hashes |
| 0356 | Dauerhafter resolution_hash am Display für idempotente Revisionen |
| 0357 | reported_config_version am Gerät; reported_status und last_revision am Display |
| 0358 | Update-Diagnose, Release-Key/Installer/Critical, CI-Upload-Token-Tabelle, Org-Wartungsfenster/Freigabe; Versionsspalten unter MariaDB auf 100 Zeichen |

0358 vermeidet einen SQLite-Tabellenneubau, weil dieser bei aktiven FKs Displays
per Cascade löschen könnte. SQLite erzwingt VARCHAR-Längen nicht. MariaDB-
Migrationsprüfung inklusive Seed im CI muss erfolgreich sein; ein SQLite-Test
ersetzt sie nicht.

### Prod-Deploy-Checkliste

- [ ] DB und Release-Ablage konsistent sichern, Restore prüfen; Wartungs-/Rollbackzeit
  festlegen. Org-Backup allein enthält die plattformweiten Releases/Upload-Tokens nicht.
- [ ] Ziel-Revision und freien Platz für DB-DDL, Uploads und dauerhaft aufbewahrte
  Release-Dateien prüfen. Schreibrechte der App auf persistente Release-Ablage
  sicherstellen; kein Static-Mount, keine Ablage im flüchtigen Container-Dateisystem.
- [ ] Settings aus `app/config.py` prüfen: `INFOSCREEN_AGENT_KEY_FINGERPRINT=""`,
  `INFOSCREEN_AGENT_INSTALLER_ENABLED=False`, `INFOSCREEN_AGENT_RELEASE_PUBKEYS={}`,
  `INFOSCREEN_AGENT_RELEASE_STORAGE_DIR="app_storage/infoscreen_agent_releases"`
  sind die deklarierten Defaults. Pubkeys als JSON-Objekt **key_id → PEM**
  konfigurieren; für den Agent-Release-Workflow ist der Key **`release`** erforderlich.
  Keine privaten Schlüssel im ECP speichern. Fingerprint muss zum im signierten
  Installerskript eingebetteten Key passen; bei leerem Setting wird der erste
  konfigurierte Public Key verwendet.
- [ ] Öffentliche HTTPS-Origin `PUBLIC_BASE_URL`/`APP_BASE_URL`, Proxy-Vertrauen,
  Zertifikat und Uhrzeit/NTP prüfen. Redis und `ec:isa` für alle Worker prüfen;
  WS-Upgrade und Authorization-Header erhalten.
- [ ] Vor dem ersten Release-Upload die auskommentierte Location in `deploy/nginx-*.conf`
  durch Betrieb prüfen: ausschließlich `/api/v1/infoscreen-agent/releases` benötigt
  mindestens `client_max_body_size 520m`, passende Upload-/Proxy-Timeouts und Platz
  für Body-/Dateitemporärdaten. Beispielwerte sind keine produktive Freigabe.
  Proxy-Konfiguration validieren und separat ausrollen.
- [ ] Migrationen in obiger Reihenfolge über Alembic ausführen; Erfolg/Head und
  App-Start prüfen, keine alten Worker mit unvollständigem Schema weiterbetreiben.
- [ ] Als Sysadmin unter **Updates & Backups > Agent-Releases** Upload-Token
  erzeugen, den einmalig sichtbaren Wert unmittelbar als CI-Secret
  `EC_RELEASE_UPLOAD_TOKEN` speichern; `ECP_URL` ist die HTTPS-Origin.
  Nur Release-Upload-Scope; Widerruf testen, Wert nicht in Logs/Tickets übernehmen.
- [ ] Signiertes Paket plus Skript aus CI hochladen, `key_id=release`, Signatur/Hash
  und Architektur kontrollieren. Upload startet mit 0 %, Pilotfreigabe separat setzen.
  Ohne Pubkeys ist der Upload gesperrt. Fehlversuche 401/413/422 gezielt prüfen.
- [ ] Installer-Schalter erst mit passendem freigegebenem stable-Paket, gültigem
  signiertem Skript und bestätigtem Fingerprint aktivieren; Org-Dialog kontrollieren.
  Aktueller Download selektiert nur Architektur/Kanal: Ubuntu-/Python-kompatibles
  Paket sicherstellen, insbesondere für 26.04 (Release-Build derzeit auf 24.04).
- [ ] Pilot: Einzeiler/Pairing, Heartbeat, WS-Reconnect über mehrere Worker,
  Display-Vorschau, Overrides, Sperren und Offline-Verhalten kontrollieren.
  Aufwachzeit/Monitor-Aus und Update-Rollback am echten Gerät menschlich abnehmen.
- [ ] Org-Fenster (Default 02:00–04:30 Org-Zeitzone), stable/beta, Pin und Updatesperre
  prüfen; kritische Fenster-Ausnahme standardmäßig aus. Einsatzschutz prüfen,
  dann Rollout stufenweise erhöhen; Release-Rückzug auf Downloadwirkung testen.
- [ ] Backupplan um `INFOSCREEN_AGENT_RELEASE_STORAGE_DIR` ergänzen; nach Restore
  Artefakte/Signaturen, Token-Widerruf und Pilotstatus prüfen.

### Rollback-Hinweise

Ein App-Rollback und ein Agent-Paketrollback sind getrennte Vorgänge.
Für ein kurzfristiges Stoppen der Erstinstallation den Installer-Schalter auf
false setzen; Updates bei Bedarf separat über Release-Sperre/Geräte-Updatesperre
stoppen. Rückzug deinstalliert keine vorhandenen Pakete.

Schema-Downgrade nur mit gestoppter App/allen Workern und gesichertem Datenbestand:
**0358 → 0357 → 0356 → 0355 → 0354**. 0358 verliert Upload-Token-Hashes,
Release-Signatur-/Installerzuordnung, Update-Diagnose und Org-Policy; bei MariaDB
können Werte über 50 Zeichen die Rückverkleinerung der Versionsspalten verhindern.
0357 verliert Heartbeat-Diagnose, 0356 die Auflösungs-Hashes, 0355 sämtliche
Agenttabellen samt Kopplungen/Plänen/Audit. Ein Downgrade ist kein Weg zur
Bewahrung bereits angelegter Agentdaten. Im Produktivbetrieb konsistenten
DB-/Release-Backupstand und passenden Appstand wiederherstellen. Nach Restore
mögliche wieder gültige alte Tokens berücksichtigen und nötigenfalls widerrufen.
Dateien werden durch Alembic nicht zurückgerollt oder gelöscht.

### Bekannte Grenze des aktuellen Agent-Release-Workflows

Das Agent-Repository ist öffentlich. Sein lokal gelesener `release.yml` enthält
vor dem ECP-Upload noch einen Schritt „Private repository release archive“, der
mit `test ... .private = true` abbricht, wenn das Repository öffentlich ist.
Der Betreiber muss diesen Workflow im Agent-Repository an den öffentlichen
Betrieb anpassen, bevor ein Tag-Release durchgehend bis zum ECP-Upload laufen
kann. S7 ändert das Agent-Repository nicht. Die CI-Secrets dort heißen
`EC_RELEASE_SIGNING_KEY` (privater Signaturschlüssel), `EC_RELEASE_UPLOAD_TOKEN`
und `ECP_URL`; der Signierer verwendet standardmäßig `key_id="release"`.
Keine Secret-Werte gehören in dieses Runbook.

Technische Details und genaue Signatur-/Settings-Referenz:
[Architekturübersicht](architecture/infoscreen-agent-uebersicht.md),
[Release-Vertrag](architecture/infoscreen-agent-releases.md).


## 0359 – Infoscreen-Release-Schluessel (S8)

Vorgaenger: **0358**, Revision ausschliesslich **0359**. `alembic upgrade head`
legt `infoscreen_release_key` an: globale, explizit bestaetigte Ed25519-Public-Keys,
DER-SHA256-Fingerprint, Status, Quelle und Bestaetigungsdaten. Keine Org-Spalte;
vom Org-Export ausgeschlossen. `bestaetigt_von` ist BIGINT wie `user.id`,
FK ON DELETE SET NULL; SQLite verwendet die vorhandene INTEGER-Kompilierung.
Keine Aenderung bestehender Geraete/Displays, kein SQLite-Tabellen-Rebuild.

Vorher DB und Release-Ablage gemeinsam sichern. Nachher als Sysadmin im bestehenden
Agent-Releases-Tab Key-Fingerprint bestaetigen, GitHub-Import pruefen und Pilot
manuell freigeben. Setting-Keys bleiben gueltig und gewinnen bei gleicher ID.
Der Hintergrundabgleich ist standardmaessig aus. Keine privaten Keys migrieren.

Downgrade auf 0358 entfernt nur die neue Key-Tabelle. Vorher benoetigte Public-Keys
in `INFOSCREEN_AGENT_RELEASE_PUBKEYS` uebernehmen und den Altweg-Installer-Schalter
setzen, falls Installation weiterhin verfuegbar sein soll. Releases/Artefakte bleiben
bestehen. SQLite-Roundtrip und SQLite/MySQL/MariaDB-DDL sind durch Tests abgedeckt;
ein echter MariaDB-Upgrade bleibt Teil des Migrationsjobs.


## 0360: Infoscreen-Agent Repo-Modus

Vor Upgrade DB-Backup erstellen. `alembic upgrade head` fuehrt 0360 nach 0359 aus.
Die globale Release-Tabelle erlaubt NULL fuer sha256, signature, size und storage_path.
SQLite baut nur diese Tabelle neu; keine eingehenden FKs, kein Rebuild der
Geraete-/Displaytabellen und damit kein CASCADE-Datenverlust. MariaDB verwendet
ALTER COLUMN mit den bestehenden Typen. Es werden keine FKs hinzugefuegt/geaendert.
Neue nullable Geraetefelder: arch, last_update_check_at/-reason, release_discovery_day;
release_discovery_count startet bei 0. Globaler Entdeckungs-Mutex wird mit id=1 angelegt.
Bestehende Paket-/Signaturdaten bleiben unveraendert. Nach Upgrade ein Geraet koppeln,
GitHub-Einzeiler und Check (zunaechst rollout) pruefen, Version freigeben und Event/Audit
pruefen. In abgeschotteten Umgebungen vor Deploy explizit
`INFOSCREEN_AGENT_DISTRIBUTION=managed` setzen; Standard wechselt auf github.
Downgrade entfernt ausschliesslich die nicht im alten Schema darstellbaren
Metadatenzeilen (ohne vollstaendige Artefaktfelder), neue Geraetefelder und Mutex.
Freigaben solcher Metadatenzeilen gehen beim Downgrade verloren; vorher exportieren.
SQLite-Datenerhalt und MySQL-/MariaDB-DDL sind durch Migrationstests abgedeckt.
