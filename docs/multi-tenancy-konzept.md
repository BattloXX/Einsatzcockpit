# Einsatzleiter-Hilfswerkzeug – Multi-Tenancy-Konzept & Claude-Code-Plan

**Ziel:** SaaS-fähige Plattform für mehrere unabhängige Einsatzorganisationen (Start: Vorarlberg, später Gemeindeeinsatzleitungen u. a.). Vollständige Mandantentrennung bei Daten, Einsätzen und Medien – mit Ausnahme explizit eingeladener Kooperation (Nachbarwehr, Großschadenslage).

**Stand:** 11.06.2026 · Basis: Code-Review des aktuellen `main`-Branch

---

## 1. Ist-Analyse (Code-Review-Befund)

### 1.1 Was bereits mandantenfähig ist ✅

| Bereich | Stand |
|---|---|
| `FireDept` | Vollwertige Org-Entität: slug, name, color, bos, logo_path, contact_email/phone, street, city, timezone, fallback_lat/lng, short_code, is_active, is_home_org |
| `OrgSettings` | Branding je Org (logo, primary_color, footer_text, mi_auto_adopt) |
| `User` | `org_id` (NULL = System-Admin), Rollen `system_admin` / `org_admin` / `admin`, `require_system_admin`, `same_org_or_system_admin` |
| `Member`, `Qualification` | org_id vorhanden |
| `VehicleMaster` | `dept_id` (= org-Bindung) vorhanden, inkl. `display_label` mit Org-Kürzel |
| `ApiKey` | org-gebunden (`org_id` RESTRICT), api_v1 bindet neue Einsätze an `api_key.org_id` ✅ |
| `SmsGatewayToken` | org-gebunden (CASCADE) |
| `Incident` | `primary_org_id` + `IncidentOrg` (Kooperations-Tabelle) – Basis für Einladungsmodell existiert |
| Permissions | `core/permissions.py` mit Incident-Zugriffsprüfung über primary_org + collaborating_orgs |

### 1.2 Lücken – noch global (kein org_id) ❌

| Entität | Problem | Schwere |
|---|---|---|
| `AlarmType` | **String-PK `code` ist global.** 5 Tabellen referenzieren `alarm_type.code` per FK (TaskSuggestionAlarm, MessageSuggestionAlarm, LageHintAlarm, DefaultMessageAlarm, AlarmDispatchVehicle) | 🔴 Kern-Migration |
| `TaskSuggestion` (Auftragsvorlagen) | global | 🔴 |
| `MessageSuggestion` (Meldungsvorlagen) | global | 🔴 |
| `LageHint` (Lage-Hinweise) | global | 🔴 |
| `DefaultMessage` (Defaultmeldungen) | global | 🔴 |
| `AlarmDispatchVehicle` (Ausrückordnung) | hängt indirekt über `vehicle_master.dept_id`, FK auf globalen AlarmType | 🔴 |
| `AIPromptVersion` | global – KI-Prompts gelten für alle Orgs | 🔴 |
| KI-Konfiguration | zentraler Key in `settings`/`SystemSettings`, keine Org-Wahl | 🟠 |
| `AuditLog` | kein `org_id` – Org-Admins können kein eigenes Audit-Log einsehen, ohne fremde Einträge zu sehen | 🟠 |
| Medienspeicher | Pfad `{incident_id}/{task_id}/…` ohne Org-Ebene; keine Quota, nur Per-Datei-Limits (`MAX_UPLOAD_BYTES_*`) | 🔴 |
| Backup | `/admin/backup` nur system_admin, exportiert **alle** Stammdaten global | 🟠 |
| `SystemSettings` | Key-Value global – ok für Systemwerte, aber org-spezifische Werte (Auto-Schließen etc.) müssen raus | 🟠 |
| Einsatz-Auto-Schließen (`autoclose.py`) | vermutlich global konfiguriert – muss je Org steuerbar werden | 🟠 |
| Device-Logins / Push | `DeviceToken`, `FcmToken`, `PushSubscription` – org-Bindung prüfen, läuft teils über User | 🟡 |
| Lagekarten-Tokens (`IncidentToken`) | über Incident indirekt org-gebunden – Erzeugung/Verwaltung muss org-gescoped sein | 🟡 |

### 1.3 Strukturelle Risiken im Code

1. **Kein erzwungenes Tenant-Scoping in Queries.** Router nutzen `db.query(AlarmType).all()` u. ä. ohne Filter. Bei ~11.000 Zeilen Router-Code ist manuelles Nachrüsten fehleranfällig – ein vergessener Filter = Datenleck zwischen Orgs. → Es braucht ein **zentrales Scoping-Pattern** (Dependency + Service-Schicht), nicht 200 Einzel-Fixes.
2. **AlarmType-String-PK.** `code` als globaler PK verhindert, dass zwei Orgs denselben Code (z. B. „B3“) mit unterschiedlicher Bedeutung führen. Migration auf Surrogat-PK + `UNIQUE(org_id, code)` zieht sich durch 6 Tabellen und alle Stellen, die `db.get(AlarmType, code)` verwenden.
3. **Quota nicht atomar planbar mit reinem Dateisystem.** `bytes` wird je Medium in der DB gespeichert (TaskMedia/MessageMedia/PersonMedia) – gut. Aber eine `SUM()`-Abfrage bei jedem Upload ist bei Last teuer und race-anfällig. → Zähler-Tabelle mit transaktionalem `UPDATE … WHERE` (harter Cut) + nächtlicher Reconciliation.
4. **Audit-Log ohne org_id** erschwert die geforderte Org-Selbstverwaltung des Logs.
5. **`is_home_org`-Konzept** stammt aus der Single-Tenant-Welt („meine Wehr + Nachbarn“). Im SaaS-Modell gibt es keine Home-Org mehr – jede Org ist gleichberechtigt. Das Flag und alle davon abhängigen Codepfade müssen entfernt/neutralisiert werden.
6. **Login/Session ohne Org-Kontext-Slug.** Für SaaS empfehlenswert: Login erkennt Org über User; optional später Org-Slug in URL (`/o/{slug}/…`) – fürs Erste reicht User-Bindung, aber Session sollte `org_id` cachen.

---

## 2. Zielarchitektur

### 2.1 Tenancy-Modell

**Shared Database, Shared Schema, Row-Level-Scoping über `org_id`** – passend zur Zielgruppe (viele kleine Orgs, ein Betreiber, SQLite/MariaDB-kompatibel). Kein Schema-pro-Tenant (Overkill, Alembic-Hölle), kein DB-pro-Tenant.

**Drei Verteidigungslinien (Defense in Depth):**

1. **Request-Ebene:** Dependency `CurrentOrg` löst aus Session/API-Key die Org auf. Jeder Router-Endpoint deklariert sie explizit. System-Admins können per Query-Param/Dropdown eine Org „betreten" (Impersonation mit Audit-Eintrag).
2. **Service-Ebene:** Alle Stammdaten-/Einsatz-Services nehmen `org_id` als Pflichtparameter. Repository-Helfer `org_scoped(db, Model, org_id)` liefert vorgefilterte Queries.
3. **Daten-Ebene (Netz und doppelter Boden):** SQLAlchemy-Event `do_orm_execute` injiziert automatisch `WHERE org_id = :current_org` für alle als `TenantScoped` markierten Modelle (Mixin). Vergessene Filter fallen dadurch nicht mehr durch. Bypass nur über explizites `execution_option(include_all_tenants=True)` – das nur System-Admin-Code verwendet.

```python
class TenantScoped:
    """Mixin: Markiert Modelle für automatisches Org-Filtering."""
    org_id: Mapped[int] = mapped_column(
        ForeignKey("fire_dept.id", ondelete="CASCADE"), nullable=False, index=True
    )

@event.listens_for(Session, "do_orm_execute")
def _add_tenant_filter(execute_state):
    if execute_state.is_select and not execute_state.execution_options.get("include_all_tenants"):
        org_id = execute_state.session.info.get("current_org_id")
        if org_id is not None:
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(TenantScoped, lambda cls: cls.org_id == org_id,
                                     include_aliases=True)
            )
```

### 2.2 Rollenmodell

| Rolle | Scope | Darf |
|---|---|---|
| `system_admin` | global | Orgs anlegen/deaktivieren, Speicher-Quota setzen, Server-Log, Seiten (CMS), System-Update, Seed-Templates pflegen, in Org-Kontext wechseln (auditiert) |
| `org_admin` | eigene Org | Mitglieder, Fahrzeuge, Qualifikationen, Alarmtypen, Ausrückordnung, alle Vorlagen, Benutzer, Geräte-Logins, Push, API-Keys, Lagekarten-Tokens, SMS-Gateway, KI-Prompts/-Konfig, Großschadenslage-Funktionen, Auto-Schließen, Org-Daten/Branding, eigenes Audit-Log lesen, Konfig-Backup/Restore |
| `user` / Einsatzrollen | eigene Org | operative Nutzung (Einsätze, Lagekarte, S1–S6 …) |

Bestehende Rolle `admin` wird zu `org_admin` migriert (Alias-Übergang).

### 2.3 Datenmodell-Änderungen (Kern)

```
AlarmType:            id (neu, PK) + org_id + code, UNIQUE(org_id, code)
                      → 5 FK-Tabellen wechseln von alarm_type_code auf alarm_type_id
TaskSuggestion:       + org_id
MessageSuggestion:    + org_id
LageHint:             + org_id
DefaultMessage:       + org_id
AIPromptVersion:      + org_id, UNIQUE(org_id, prompt_key, version)
AuditLog:             + org_id (nullable; NULL = systemweit)
FireDept:             + storage_quota_bytes (NULL = unbegrenzt), is_home_org → entfernen
OrgSettings:          + ai_mode ('central'|'byok'), ai_api_key_encrypted,
                      + ai_monthly_token_quota, ai_tokens_used_month,
                      + autoclose_enabled, autoclose_hours,
                      + Großschadenslage-Schalter (mi_* Felder konsolidieren)
NEU OrgStorageUsage:  org_id PK, used_bytes, updated_at  (transaktionaler Zähler)
NEU OrgInvitation:    id, incident_id, inviting_org_id, invited_org_id,
                      status (pending/accepted/declined/revoked), created_by, timestamps
NEU SeedTemplate:     Versionierte System-Vorlagen (Alarmtypen, Auftrags-/Meldungs-
                      vorlagen, Lage-Hinweise, Defaultmeldungen, KI-Prompts) – nur
                      system_admin pflegt sie; beim Org-Anlegen werden sie KOPIERT.
```

**Verbindliche Migrationsregel für Bestandsdaten:** Alle bereits vorhandenen Datensätze, die heute keine `org_id` tragen, werden bei der Migration **`org_id = 1`** (der bestehenden Stamm-Organisation) zugewiesen. Das gilt durchgängig für AlarmType, TaskSuggestion, MessageSuggestion, LageHint, DefaultMessage, AIPromptVersion, AlarmDispatchVehicle (über die neuen Alarmtyp-FKs), den AuditLog-Bestand, den initialen `OrgStorageUsage`-Zähler sowie alle Medienpfade (Verschiebung nach `1/{incident_id}/…`). Jede Alembic-Migration in den PRs 1–3 und 5–6 enthält diesen Backfill-Schritt; nach dem Backfill werden die `org_id`-Spalten auf `NOT NULL` gesetzt. Damit ist der heutige Produktivbetrieb nach der Migration unverändert als „Organisation 1" lauffähig.

### 2.4 Medienspeicher & harte Quota

**Pfadstruktur neu:** `{MEDIA_STORAGE_DIR}/{org_id}/{incident_id}/…` – macht Org-Löschung, Backup und Verbrauchsmessung trivial. Bestandsdaten werden per Migrationsskript verschoben (Medien-Tabellen speichern relative Pfade → Update in einer Transaktion).

**Harter Cut, race-sicher:**

```python
def reserve_storage(db, org_id: int, nbytes: int) -> bool:
    res = db.execute(text("""
        UPDATE org_storage_usage
           SET used_bytes = used_bytes + :n
         WHERE org_id = :org
           AND used_bytes + :n <= COALESCE(
               (SELECT storage_quota_bytes FROM fire_dept WHERE id = :org),
               :unlimited)
    """), {"n": nbytes, "org": org_id, "unlimited": 2**62})
    return res.rowcount == 1   # 0 → Quota überschritten → HTTP 413
```

- Reservierung **vor** der Verarbeitung mit der Rohgröße; nach Pillow/ffmpeg-Verkleinerung wird die Differenz gutgeschrieben. Bei Verarbeitungsfehler: vollständige Gutschrift (try/finally).
- Beim Löschen von Medien/Einsätzen: Gutschrift.
- Nächtlicher Reconciliation-Job: `SUM(bytes)` aus den Media-Tabellen vs. Zähler, Abweichungen korrigieren + loggen.
- UI: Fortschrittsbalken „2,3 GB von 5 GB" im Org-Admin-Bereich; Warn-Banner ab 90 %; bei vollem Speicher klare Fehlermeldung mit Hinweis, alte Einsatzmedien zu löschen.
- **Default-Quota für neue Orgs: 1 GB.** Staffelung: 1 GB / 10 GB / 100 GB. Die Staffelstufen sind für System-Admins konfigurierbar (`SystemSettings`-Key `storage_quota_tiers`, JSON-Liste in Bytes); die Quota-Auswahl im Org-Formular zeigt die Stufen als Dropdown plus Freitextfeld für Sonderwerte und „unbegrenzt".
- Nur **Medienspeicher** zählt (Task-/Message-/Person-/Lage-Medien + Org-Logo). DB-Inhalte zählen nicht.

### 2.5 Org-übergreifende Einsätze (Einladungsmodell)

- Standard: **vollständige Isolation.** Org B sieht von Org A nichts – keine Einsätze, Stammdaten, Medien, Statistiken.
- Org A (führende Org, `primary_org_id`) kann je Einsatz gezielt Orgs **einladen** (`OrgInvitation`). Die eingeladene Org erhält eine Benachrichtigung (Push/Mail) und muss **annehmen** – erst dann entsteht der `IncidentOrg`-Eintrag und der Einsatz erscheint bei ihr.
- Eingeladene Orgs sehen **nur diesen einen Einsatz** (Lage, Aufträge, Meldungen, Medien des Einsatzes), niemals Stammdaten oder andere Einsätze der einladenden Org. Fahrzeuge/Mitglieder bringt jede Org selbst ein (`IncidentVehicle` referenziert die jeweils eigene `VehicleMaster`).
- Führende Org kann Einladung widerrufen → Zugriff endet, beigesteuerte Daten bleiben im Einsatzprotokoll (Nachvollziehbarkeit).
- Großschadenslage nutzt denselben Mechanismus, optional mit „Sammel-Einladung" an mehrere Orgs.
- Das bestehende `notify_neighbors`-Flag der Alarmtypen wird zu „Einladungsvorschlag": Beim Anlegen schlägt das System konfigurierten Partner-Orgs vor, ersetzt aber nie die explizite Annahme.

### 2.6 KI je Organisation

- `OrgSettings.ai_mode`:
  - **`central`** (Default): Plattform-Key aus Server-Env; Verbrauch wird je Org gezählt (`ai_tokens_used_month`), System-Admin setzt `ai_monthly_token_quota`. Bei Überschreitung: KI-Features deaktiviert bis Monatswechsel (sauberer Fehlertext im UI).
  - **`byok`**: Org hinterlegt eigenen Anthropic-Key, verschlüsselt at rest (Fernet, Schlüssel aus Server-Env, nie im Backup-Export). Keine Plattform-Quota. **Entscheidung: BYOK vorerst ausschließlich Anthropic** – kein Provider-Feld, keine Bedrock-Abstraktion; das hält PR 5 schlank und kann später erweitert werden.
- `AIPromptVersion` wird org-scoped; beim Org-Anlegen werden die System-Seed-Prompts als Version 1 kopiert. Org-Admins editieren nur ihre Kopien.
- `ai_service.py` erhält `org`-Parameter und wählt Key/Quota daraus; alle KI-Aufrufe schreiben Token-Verbrauch ins Audit-Log (org-scoped).

### 2.7 Org-Self-Service-Backup

- **Export** (org_admin): JSON-Bundle der eigenen Konfiguration – Stammdaten (Mitglieder, Fahrzeuge, Qualifikationen, Alarmtypen, Ausrückordnung), alle Vorlagen, KI-Prompts, Org-Settings. **Nicht** enthalten: Passwort-Hashes, API-Key-Hashes, verschlüsselte KI-Keys, SMS-Tokens, Medien, Einsätze (eigener Einsatz-/Medien-Export ist ein separates späteres Feature, Quota-relevant).
- **Restore** (org_admin): Validierung gegen JSON-Schema + Versionsfeld, Dry-Run mit Diff-Anzeige („3 Alarmtypen neu, 2 geändert"), dann Import strikt in die eigene org_id – fremde IDs im File werden ignoriert/neu gemappt.
- Bestehender System-Backup (`/admin/backup`) bleibt system_admin-only und wird auf „alle Orgs inkl. org_id" erweitert.

### 2.8 Was System-Admin-exklusiv bleibt

Server-Log, System-Update (`update_service`), Seiten/CMS (`site_pages`), Org-Verwaltung (anlegen, deaktivieren, Quota, Slug), Seed-Template-Pflege, globale `SystemSettings`, Reconciliation-Tools, Org-Kontext-Wechsel (auditiert mit `acting_as_org`).

---

## 3. Beschlossene Erweiterungen über die Kernanforderungen hinaus

*Die Punkte 1–9 wurden am 11.06.2026 in den verbindlichen Umfang aufgenommen und sind im PR-Plan (Abschnitt 4) den jeweiligen PRs zugeordnet.*

1. **Org-Lifecycle:** „Deaktivieren" (Login gesperrt, Daten bleiben) vor „Löschen" (Soft-Delete mit 30-Tage-Frist, dann Purge inkl. Medienordner). Verhindert Datenverlust bei Kündigungen.
2. **Onboarding-Wizard für System-Admins:** Org anlegen → Seed-Auswahl (z. B. Profil „Feuerwehr Vorarlberg" vs. künftig „Gemeindeeinsatzleitung") → erster org_admin per Einladungs-Mail mit Passwort-Set-Link (Password-Reset-Flow existiert bereits, wiederverwenden).
3. **Seed-Profile statt eines Seeds:** `SeedTemplate` bekommt ein `profile`-Feld – „Think Big": dieselbe Plattform bedient später GEL/Rettung/Bergrettung mit jeweils passenden Alarmtypen und Prompts, ohne Codeänderung.
4. **Tenant-Isolationstests als CI-Gate:** Pytest-Fixture mit zwei Orgs; parametrisierter Test, der **jeden** GET/POST-Endpoint als Org-B-User gegen Org-A-Ressourcen aufruft und 403/404 erwartet. Das ist die wichtigste einzelne Qualitätsmaßnahme des ganzen Projekts.
5. **Rate-Limits je API-Key/Org** (rate_limit.py erweitern) – eine Org mit fehlkonfiguriertem Leitstellen-Connector darf die Plattform nicht lahmlegen.
6. **WebSocket-Scoping prüfen (`ws.py` / `broadcast.py`):** Broadcasts müssen org-gefiltert sein – ein globaler „neue Meldung"-Broadcast wäre ein Leck. Kanalname = `org:{id}:incident:{id}`.
7. **Statistiken (`ui_stats.py`)** org-scopen – leicht zu übersehen.
8. **Logo-/Branding-Konsolidierung:** Logo + Farbe existieren doppelt (FireDept *und* OrgSettings) → auf OrgSettings konsolidieren, FireDept behält nur Identität (name, slug, bos, short_code).
9. **`external_key`-Eindeutigkeit** bei API-Einsätzen auf `UNIQUE(org_id, external_key)` ändern (zwei Leitstellen können dieselbe Einsatznummer vergeben).
10. **Später (nicht jetzt):** Self-Signup mit Freigabe-Queue, Abrechnung/Pläne, S3-kompatibler Medienspeicher, Org-Slug-URLs.

---

## 4. Implementierungsplan für Claude Code (12 PRs)

Reihenfolge ist abhängigkeitsgetrieben; jeder PR ist eigenständig deploybar, Migrationen abwärtskompatibel (expand → migrate → contract). Vor PR 1: Branch `feature/multi-tenancy`, Staging-Kopie der Produktiv-DB für Migrationstests.

### Phase A – Fundament

**PR 1 – Tenant-Infrastruktur & Rollen**
- `TenantScoped`-Mixin + `do_orm_execute`-Listener + `session.info["current_org_id"]`
- Dependency `CurrentOrg` (Session-User-Org bzw. API-Key-Org; system_admin: `?org=`-Wechsel mit Audit)
- Rolle `admin` → `org_admin` migrieren (Datenmigration, Alias in `role_codes`)
- `AuditLog.org_id` + `write_audit(..., org_id=)` durchziehen
- `is_home_org` deprecaten: Flag bleibt in DB, sämtliche Logik-Abhängigkeiten entfernen
- Pytest-Fixture „zwei Orgs" + erste Isolationstests als Muster
- *Akzeptanz:* Listener-Test: ungefiltertes `select(Member)` liefert nur Org-A-Daten, wenn Org A im Session-Kontext.

**PR 2 – AlarmType-Migration (kritischster PR)**
- `AlarmType`: neuer Surrogat-PK `id`, `org_id`, `UNIQUE(org_id, code)`
- 5 FK-Tabellen auf `alarm_type_id` umstellen; Incident.alarm_type-Referenz prüfen
- Alembic in 3 Schritten (Spalten hinzufügen → Daten kopieren, **Bestand erhält org_id = 1** → alte FKs droppen)
- Alle `db.get(AlarmType, code)`-Aufrufer auf `(org_id, code)`-Lookup-Helper umstellen
- *Akzeptanz:* Zwei Orgs können beide Alarmtyp „B3" mit unterschiedlichem Label führen; alle bestehenden Tests grün.

**PR 3 – Stammdaten org-scopen**
- `org_id` auf TaskSuggestion, MessageSuggestion, LageHint, DefaultMessage (+ TenantScoped-Mixin auf alle bereits org-gebundenen Modelle); **Backfill: Bestand → org_id = 1**, danach NOT NULL
- `ui_settings.py`, `ui_admin.py`, `incident_service.py` auf Scoping umstellen
- `UNIQUE(org_id, external_key)` für API-Einsätze
- Isolationstests für alle Stammdaten-Endpoints

### Phase B – Org-Features

**PR 4 – Seed-Templates & Org-Onboarding**
- `SeedTemplate`-Modell + system_admin-UI zur Pflege (initial befüllt aus aktuellem `seed_data.py`)
- „Org anlegen"-Wizard: Profilauswahl → Kopie aller Seeds in die neue Org → org_admin-Einladung per Mail (Password-Set-Link)
- **Seed-Profil „Feuerwehr Vorarlberg"** mit dem LWZ-Stichwortkatalog als erstes Profil anlegen (Alarmtypen siehe Anhang A); Vorarlberger Wehren starten damit einheitlich
- Org-Lifecycle: deaktivieren / Soft-Delete mit Frist

**PR 5 – KI je Org**
- `AIPromptVersion.org_id` + Migration (**Bestand → org_id = 1**), Seed-Kopie beim Onboarding; BYOK nur Anthropic
- `OrgSettings`: ai_mode, verschlüsselter BYOK-Key (Fernet), Token-Quota + Monatszähler
- `ai_service.py` org-parametrisieren; `ui_ai_prompts.py` auf org_admin öffnen
- Quota-Abschneiden mit verständlicher UI-Meldung; Token-Verbrauch ins Audit-Log

**PR 6 – Speicher-Quota (harter Cut)**
- `FireDept.storage_quota_bytes`, `OrgStorageUsage`, `reserve_storage`/`release_storage` in `media_service.py` (alle Upload-Pfade inkl. `lage_media_service.py` und Logo-Upload)
- Migrationsskript: Medienordner nach `{org_id}/…` verschieben (Bestand → `1/…`), Pfade in DB updaten, Zähler initial befüllen
- Default 1 GB für neue Orgs; konfigurierbare Staffelung (1/10/100 GB) über `SystemSettings.storage_quota_tiers` inkl. Pflege-UI für System-Admins
- Reconciliation-Job (Scheduler) + system_admin-Übersicht „Speicher je Org" mit Quota-Eingabe (Staffel-Dropdown + Freitext)
- Org-Admin-UI: Verbrauchsanzeige + 90 %-Warnung
- *Akzeptanz:* Paralleltest – 2 gleichzeitige Uploads, die zusammen die Quota sprengen: genau einer schlägt mit 413 fehl.

**PR 7 – Einladungsmodell für org-übergreifende Einsätze**
- `OrgInvitation` + Annahme-/Ablehnungs-/Widerrufs-Flow, Push/Mail-Benachrichtigung
- Permissions: eingeladene Org sieht ausschließlich diesen Einsatz; Widerruf entzieht Zugriff
- `notify_neighbors` → Einladungsvorschlag; Großschadenslage: Sammel-Einladung
- WebSocket-Broadcasts auf `org:{id}`-Kanäle umstellen (`ws.py`, `broadcast.py`)

**PR 8 – Org-Self-Service: Backup, Audit, Auto-Schließen, Branding**
- Org-Konfig-Export/-Import (JSON-Schema, Dry-Run-Diff, strikte org_id-Bindung)
- Org-Audit-Log-Ansicht für org_admin (nur eigene org_id)
- Auto-Schließen je Org (`OrgSettings.autoclose_*`, `autoclose.py` org-iterierend)
- Branding konsolidieren (Logo/Farbe nur noch OrgSettings), Theme-CSS je Org aus primary_color
- Geräte-Logins, Push-Verwaltung, Lagekarten-Tokens, SMS-Gateway: org_admin-Selbstverwaltung verifizieren/nachziehen

### Phase C – Härtung & Abschluss

**PR 9 – Vollständige Router-Durchsicht + Isolationstest-Matrix**
- Systematischer Sweep aller 17 Router: jeder Endpoint bekommt `CurrentOrg` oder explizites `system_admin`-Gate; `ui_stats.py`, `ui_archive.py`, `public.py`, `device_api.py`, `lagekarte_api.py` besonders prüfen
- Parametrisierter Cross-Tenant-Test über alle Routen als CI-Gate

**PR 10 – Rate-Limits & API-Härtung**
- Rate-Limit je API-Key und je Org; API-Doku (`docs/wiki`) um Multi-Org-Setup ergänzen

**PR 11 – System-Admin-Konsole**
- Org-Übersicht (Status, Speicher, KI-Verbrauch, letzte Aktivität), Quota-Verwaltung, Org-Kontext-Wechsel mit Banner „Sie agieren als Org X", Reconciliation-Trigger

**PR 12 – Doku, Migration-Runbook, Aufräumen**
- `is_home_org` & tote Codepfade entfernen (contract-Phase), Betreiber-Runbook (Onboarding, Quota, Restore), Org-Admin-Handbuch im Wiki, CHANGELOG

### Hinweise für die Claude-Code-Sessions

- **Ein PR pro Session**, Start jeweils mit: „Lies `docs/multi-tenancy-konzept.md` Abschnitt X und implementiere PR n. Führe vor dem Commit `pytest` aus."
- Konzeptdatei als `docs/multi-tenancy-konzept.md` ins Repo legen – Claude Code referenziert sie dann direkt.
- Nach PR 2 und PR 6 jeweils Migrationstest gegen Kopie der Produktiv-DB, bevor weitergebaut wird.
- Die Isolationstest-Fixture aus PR 1 in **jedem** Folge-PR um die neuen Endpoints erweitern lassen – das explizit in jeden Prompt schreiben.

---

## 5. Entschieden / Offen

**Entschieden (11.06.2026):**

1. Default-Quota **1 GB**, Staffelung **1 / 10 / 100 GB**, Stufen für System-Admins konfigurierbar.
2. Vorarlberger Wehren starten aus dem gemeinsamen Seed-Profil **„Feuerwehr Vorarlberg"** mit Alarmtypen nach LWZ-Stichwortkatalog (Anhang A).
3. BYOK vorerst **nur Anthropic**.
4. Bestandsdaten ohne org_id werden der **org_id = 1** zugewiesen.

**Noch offen:**

1. Soll der spätere Einsatz-/Medien-Archiv-Export (PDF/ZIP je Einsatz) Teil des Org-Backups werden oder eigenes Feature bleiben? (Empfehlung: eigenes Feature)

---

## Anhang A – Seed-Profil „Feuerwehr Vorarlberg" (Alarmtypen nach LWZ-Stichwortkatalog)

### Brandereignis (Kategorie B)

| Code | Bezeichnung |
|---|---|
| f1 | Kleinstereignis Brand |
| f2 | Kleinereignis Brand |
| f3 | Mittelereignis Brand |
| f4 | Großereignis Brand |
| f5 | Nachbarschaftshilfe Brand |
| f10 | Abklärung |
| f11 | Sondereinsatzmittel |
| f14 | Brandmeldeanlage |
| f21 | Bootseinsatz Brand |
| f30 | Proberuf |

### Technisches Ereignis (Kategorie T)

| Code | Bezeichnung |
|---|---|
| t1 | Kleinstereignis Technik |
| t2 | Kleinereignis Technik |
| t3 | Mittelereignis Technik |
| t4 | Großereignis Technik |
| t5 | Nachbarschaftshilfe Technik |
| t6 | Gefahrgut klein |
| t7 | Gefahrgut groß |
| t9 | Großlage |
| t21 | Bootseinsatz Technik |

Hinweise für die Seed-Befüllung:

- Mapping auf das bestehende `AlarmType`-Schema: `code` wie oben, `category` = „B" bzw. „T", `label` = Bezeichnung.
- Vorschlagswerte für Flags (im Seed-Editor anpassbar): `triggers_major_incident` bei f4, t4, t7 und t9; `notify_neighbors` (künftig „Einladungsvorschlag", siehe 2.5) bei f5 und t5; f30 (Proberuf) ohne Statistik-Relevanz kennzeichnen, sofern ein entsprechendes Flag eingeführt wird.
- Ausrückordnung, Auftrags-/Meldungsvorlagen, Lage-Hinweise und Defaultmeldungen je Stichwort werden im Seed-Profil mitgepflegt, damit neue Wehren sofort arbeitsfähig sind – Basis ist der heutige Bestand der Organisation 1.
