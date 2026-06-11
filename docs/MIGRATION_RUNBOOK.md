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
