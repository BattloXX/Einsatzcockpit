# Datenmodell

← [Zurück zur Startseite](Home)

## Überblick

Alle Tabellen haben: `id BIGINT PK AUTO_INCREMENT`, `created_at DATETIME UTC`. MariaDB 10.11, InnoDB, UTF8MB4.

`TenantScoped`-Modelle erhalten automatisch ein `org_id`-Feld und werden via SQLAlchemy `do_orm_execute`-Event auf die aktive Org gefiltert.

## Organisationen (Multi-Tenancy)

```
fire_dept
  id, slug (UNIQUE), name, color
  is_home_org, is_active
  deleted_at (NULL = aktiv, gesetzt = Soft-Delete)
  logo_path, contact_email, contact_phone, street, city
  timezone (IANA, NULL = DEFAULT_TIMEZONE)
  fallback_lat, fallback_lng
  short_code (max. 3 Zeichen, z.B. "WOL")
  storage_quota_bytes (NULL = unbegrenzt)
  withdraw_press_factor, withdraw_press_reserve

org_settings                     ← 1:1 zu fire_dept
  id, org_id FK (UNIQUE)
  primary_color, footer_text
  mi_auto_adopt
  autoclose_enabled (NULL = global), autoclose_after_hours, autoclose_grace_minutes
  weather_enabled (BOOL, Default 1)   ← Wetter-Opt-out je Org
  uas_module_enabled (BOOL, Default 0) ← UAS-Modul je Org (zusätzlich zu SystemSettings)
  gsl_lagemeldung_enabled (BOOL)      ← SKKM-Regelkreis aktivieren
  gsl_lagemeldung_interval_min        ← Fälligkeitsintervall in Minuten
  gsl_verleih_enabled (BOOL)          ← Geräteverleih-Modul aktivieren
```

## Stammdaten (TenantScoped = automatisch nach org_id gefiltert)

```
vehicle_master
  id, dept_id FK → fire_dept, code, name, type
  is_first_train, display_order, active, deleted
  bos_override

member  [TenantScoped: org_id]
  id, org_id FK, lastname, firstname, phone, email, active

qualification
  id, code (AGT|MA|GK|ZK|EL|TF|TM|JF), label
  is_einsatzleiter, is_gruppenkommandant

member_qualification (m:n)
  member_id FK, qualification_id FK, valid_until

alarm_type  [TenantScoped: org_id]
  id PK (bigint), org_id FK
  code (T1..T9, F1..F4)
  category (Technik|Feuer)
  default_first_train_only, notify_neighbors

task_suggestion  [TenantScoped: org_id]
  id, org_id FK, alarm_type_id FK, text, display_order

message_suggestion  [TenantScoped: org_id]
  id, org_id FK, alarm_type_id FK, text

lage_hint  [TenantScoped: org_id]
  id, org_id FK, text, display_order

default_message  [TenantScoped: org_id]
  id, org_id FK, alarm_type_id FK, text, due_after_sec

seed_template
  id, profile (UNIQUE), label, bos
  (JSON-Speicher für Alarmtypen, Vorlagen, Lage-Hinweise)
```

## Authentifizierung

```
user
  id, username (UNIQUE), password_hash (bcrypt)
  display_name, email, active, last_login_at
  org_id FK → fire_dept (NULL = system_admin ohne Org)
  failed_login_count, locked_until

role
  id, code (UNIQUE: system_admin|org_admin|admin|incident_leader|...), label

user_role (m:n)
  user_id FK, role_id FK

api_key
  id, key_hash (SHA-256), label
  org_id FK → fire_dept
  created_by_user_id FK?, expires_at, revoked_at, last_used_at

device_token                     ← Tablet/Geräte-Auto-Login
  id, user_id FK, token_hash, label, revoked_at, last_used_at

audit_log
  id, user_id FK?, api_key_id FK?
  org_id FK?
  action, entity_type, entity_id, payload_json, ip

push_subscription
  id, user_id FK, endpoint, p256dh, auth
```

## Einladungen

```
org_invitation
  id, org_id FK, token_hash (UNIQUE), email, admin_name
  created_by_user_id FK, expires_at, used_at
```

## Einsätze

```
incident
  id, external_key
  UNIQUE(primary_org_id, external_key)   ← Idempotenz je Org
  primary_org_id FK → fire_dept
  nummer, alarm_type_id FK
  status (active|closed|archived)
  started_at, closed_at
  incident_leader_user_id FK?, is_exercise
  address_street, address_no, address_city, address_lat, address_lng
  report_text, reason

incident_org  (m:n: Kollaboration)
  incident_id FK, org_id FK
  added_at, added_by_user_id FK?

incident_column
  id, incident_id FK, code, title, is_fixed, display_order

incident_vehicle
  id, incident_id FK, column_id FK?
  vehicle_master_id FK, commander_member_id FK?
  display_order, removed_at, org_color_override

task
  id, incident_id FK, column_id FK?, vehicle_id FK?
  title, detail
  is_done, done_at, is_cancelled, cancelled_at, display_order
  source (manual|api|ai_suggestion|template)

task_media
  id, task_id FK, incident_id FK
  file_type (image|pdf|video), original_filename
  stored_path, thumb_path, width, height, duration_sec, file_size_bytes

message
  id, incident_id FK
  title, detail, due_after_sec, due_at
  popup_shown, is_done, is_cancelled, display_order

rescued_person
  id, incident_id FK
  gender, person_group, age_range, name, location, vehicle_id FK?

incident_log            ← sichtbares Verlaufsprotokoll
  id, incident_id FK, ts, level (info|warn|alert)
  user_id FK?, text, entity_type?, entity_id?

incident_change         ← granulares Änderungslog (Zeitreise)
  id, incident_id FK, ts, action
  entity_type, entity_id?
  before_json (LONGTEXT), after_json (LONGTEXT)
  user_id FK?, api_key_id FK?, ip

incident_token          ← QR-Login-Tokens
  id, incident_id FK, token_hash
  issued_by_user_id FK?, target_user_id FK?, revoked_at
```

## Atemschutzüberwachung

```
breathing_troop
  id, incident_id FK, vehicle_id FK?
  name, status (bereit|im_einsatz|rueckzug|zurueck|erholt)
  task_text, start_press_avg
  entry_at, withdraw_press_calc, withdraw_at, back_at, notes

troop_member
  id, troop_id FK, member_id FK?, free_text_name
  role (truppfuehrer|truppmann)
  start_press, withdraw_press, back_press

pressure_log
  id, troop_id FK, ts, member_id FK?
  pressure_bar, recorded_by_user_id FK?
```

## KI-Prompts

```
ai_prompt_version  [TenantScoped: org_id]
  id, org_id FK
  prompt_key (report|task_suggestions|lage_hints|situation|mi_prioritize)
  content (LONGTEXT), version_number
  created_by_user_id FK?, is_active
```

## System-Einstellungen

```
system_settings          ← Key-Value-Store für globale Werte
  id, key (UNIQUE), value (TEXT)
  (smtp_host, smtp_password, vapid_*, ai_enabled, incident_autoclose_*, ...)
```

## Lagekarte / Großschadenslage

```
lagekarte_token          ← Read-only GeoJSON-Feed-Tokens
  id, org_id FK, token_hash, label, revoked_at

major_incident_*         ← Großschadenslage (Phasen-Kanban, Einsatzstellen, Stab, ...)
  (siehe app/models/major_incident.py)

lage_einheit             ← GSL-Ressourcen/Einheiten
  id, major_incident_id FK, org_id FK
  bezeichnung, typ (feuerwehr|rettung|polizei|technisch|sonstig)
  fremdorg_name, tetra_rufname, bemerkungen
  display_order

lage_dispatch            ← Mehrfach-Disposition (m:n Einheit ↔ Einsatzstelle)
  id, einheit_id FK, site_id FK, major_incident_id FK
  assigned_at, assigned_by_user_id FK?

cross_site_marker        ← Übergreifende Meldungen
  id, major_incident_id FK
  titel, status (meldung|achtung|hinweis|information)
  notizen, lat, lng, address
  is_done, done_at
```

## SSO (Microsoft Entra ID)

```
org_sso_config           ← SSO-Konfiguration je Org
  id, org_id FK (UNIQUE)
  tenant_id, client_id
  client_secret_enc (Fernet-verschlüsselt)
  allowed_domains (CSV), default_role_code
  enforce_sso (BOOL), sso_enabled (BOOL)

org_sso_group_map        ← Gruppen-Rollen-Mapping
  id, org_id FK, group_object_id, role_code, label
```

Felder in `user`:
```
user.entra_oid           ← Azure Object-ID des Benutzers
user.entra_tid           ← Azure Tenant-ID
user.auth_provider       ← 'local' oder 'entra'
user.password_hash       ← NULL wenn nur SSO
```

## Mail-Versand je Organisation

Fallback-Kette (siehe `app/services/mail_service.py::deliver()`): Office 365 →
eigener SMTP der Org → globaler SMTP (`system_settings`, s.o.).

```
org_smtp_config          ← eigener SMTP-Server je Org
  id, org_id FK (UNIQUE), enabled (BOOL)
  host, port, user, password_enc (Fernet-verschlüsselt)
  from_addr, starttls (BOOL), timeout
  imap_enabled, imap_host, imap_port, imap_use_ssl  ← Vorbereitung Posteingang, KEINE Verarbeitung implementiert

org_o365_mail_config      ← Microsoft Graph App-only-Mailversand je Org
  id, org_id FK (UNIQUE), enabled (BOOL)
  tenant_id, client_id
  client_secret_enc (Fernet-verschlüsselt)
  sender_address
  read_enabled              ← Vorbereitung Posteingang (Mail.Read), KEINE Verarbeitung implementiert
```

## UAS / Drohnen-Modul

```
uas_device  [TenantScoped: org_id]
  id, org_id FK
  bezeichnung, hersteller, typ_modell, registriernummer
  ce_klasse, unterkategorie, mtom_g, leergewicht_g
  waermebildkamera, allwettertauglich
  versicherungspolizze, versicherung_gueltig_bis
  sybos_id, beschaffungsdatum, tauschintervall_jahre
  status (aktiv|ausser_betrieb|ausgemustert)
  notizen

uas_wartung              ← Wartungsbuch je Gerät
  id, device_id FK, wartungsart, pruefdatum, pruefer
  ergebnis (io|nicht_io|bedingt_io)
  naechste_faelligkeit, bemerkungen
  positionen (JSON: Checklisten-Punkte)

uas_pilot  [TenantScoped: org_id]
  id, org_id FK, member_id FK?
  nachname, vorname, geburtsdatum
  a1a3_nr, a1a3_gueltig_bis
  a2_nr, a2_gueltig_bis
  bos_stufe, bos_datum, bos_rezertifizierung_bis
  lfv_zugelassen (BOOL), ist_truppfuehrer (BOOL), aktiv (BOOL)
  qualifikationen (JSON)

uas_flugbewegung         ← Manuelle Flugbewegungen (Pilot-Profil)
  id, pilot_id FK, datum, art, dauer_min, bemerkungen

uas_einsatz              ← UAS-Einsatz verknüpft mit Incident
  id, incident_id FK, org_id FK
  status (alarmiert|angemeldet|im_einsatz|abgemeldet|abgeschlossen)
  tetra_rufname, betreibernummer, einsatzgrund
  gesamteinsatzleiter, datenschutz_bestaetigt
  alarmierungszeitpunkt, startzeit, endzeit

uas_einsatz_rolle        ← Teambesetzung je UAS-Einsatz
  id, einsatz_id FK, pilot_id FK?
  rolle (teamleiter|pilot|operator|luftraumbeobachter|...)
  helfer_name, override_begruendung

uas_flug                 ← Flugbuch
  id, einsatz_id FK, device_id FK, pilot_id FK
  startzeit, landezeit, luftraum, hoehe_m
  durchfuehrungsgrundlage, wetterdaten (JSON)
  missionsziel, besonderheiten

uas_checkliste           ← Vor-/Nachflug-Checkliste (4-Augen)
  id, flug_id FK, art (vor|nach)
  abgehakt (JSON), sign1_name, sign1_at, sign2_name, sign2_at

uas_ereignis             ← Notfall-/Unfall-Workflow
  id, einsatz_id FK, art (notfall|unfall)
  beschreibung, meldekette_stufe (1-4)
  acg_meldung_at, nachbericht_at

uas_kartenobjekt         ← Karte des UAS-Einsatzes
  id, einsatz_id FK, typ, geojson (TEXT)
  bezeichnung, farbe

uas_medien               ← DSGVO-Medien-Verwaltung
  id, einsatz_id FK
  dateiname, stored_path, mime_type, file_size_bytes
  dsgvo_status (erfasst|begruendet|zur_loeschung|geloescht)
  rechtsgrundlage, loeschfrist_datum, geloescht_at
```

## Geräteverleih

```
verleih_artikel  [TenantScoped: org_id]
  id, org_id FK
  bezeichnung, barcode, einheit (stk|satz|karton|kg|l)
  bestand, beschreibung, aktiv

verleih_stueckliste  [TenantScoped: org_id]
  id, org_id FK, bezeichnung, beschreibung

verleih_stueckliste_position
  id, stueckliste_id FK, artikel_id FK, menge

verleih_ausleihe  [TenantScoped: org_id]
  id, org_id FK, lage_id FK
  site_id FK? (Einsatzstelle), einheit_freitext
  artikel_id FK? / stueckliste_id FK?
  status (offen|teilweise|zurueck)
  ausgeliehen_at, zurueck_at
  notizen, ausgegeben_von_user_id FK?

verleih_position         ← Einzelpositionen einer Ausleihe
  id, ausleihe_id FK, artikel_id FK
  menge_ausgegeben, menge_zurueck, zurueck_at

verleih_foto
  id, ausleihe_id FK, stored_path, thumb_path
  created_at, created_by_user_id FK?
```

## Lokale Wetterstation

Die Wetterstation nutzt **zwei getrennte Datenbanken** — die operative DB enthält nur den Snapshot, die Zeitreihe liegt in einer separaten Wetter-DB.

**Haupt-DB (`einsatzleiter`):**
```
weather_station  [TenantScoped: org_id]
  id, org_id FK → fire_dept
  name (VARCHAR 100)
  ingest_token_hash (VARCHAR 64 UNIQUE)   ← SHA-256 des wxst_-Tokens
  active (BOOL, Default 1)
  lat, lng (Dezimalgrad, optional)
  last_seen_at    ← Zeitstempel letzter erfolgreicher Push
  last_measured_at ← Zeitstempel des gemessenen Zeitpunkts
  last_temp_c, last_hum_pct
  last_wind_ms, last_gust_ms, last_wind_dir (VARCHAR 3, z.B. "NW")
  last_pressure_hpa
  last_rain_rate_mmh, last_rain_day_mm
  last_dew_c, last_solar_wm2, last_uv_index
```

**Wetter-DB (`einsatzleiter_weather`) — eigener Connection-Pool:**
```
weather_reading
  id, org_id, station_id (kein FK-Constraint in separater DB)
  ts (DATETIME UTC)
  temp_c, hum_pct, wind_ms, gust_ms, wind_dir
  pressure_hpa, rain_rate_mmh, rain_day_mm
  dew_c, solar_wm2, uv_index

  INDEX (org_id, station_id, ts)   ← Haupt-Abfrage-Index
```

Die Zeitreihe wird täglich um 03:30 (Europe/Vienna) bereinigt — Retention konfigurierbar via `WEATHER_READING_RETENTION_DAYS`.

## Wichtige Indexe

```sql
-- Idempotenz-Check (je Org):
UNIQUE INDEX ON incident(primary_org_id, external_key)

-- Aktive Einsätze schnell finden:
INDEX ON incident(status, started_at)
INDEX ON incident(primary_org_id, status)

-- Änderungslog zeitlich sortiert:
INDEX ON incident_change(incident_id, ts)

-- Pressure-Log je Trupp:
INDEX ON pressure_log(troop_id, ts)

-- Tenant-Filter:
INDEX ON member(org_id)
INDEX ON alarm_type(org_id)
INDEX ON task_suggestion(org_id)
```
