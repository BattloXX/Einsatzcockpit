# 2FA für alle Benutzer (TOTP + SMS) und Admin-Reset – Umsetzungsplan

Stand: 2026-11-04, geprüft gegen `main` (c5fbc27d). Baut auf `system-admin-mcp-2fa-plan.md` (Phase 1, #750) auf.

## Entscheidungen

1. Org-Admins dürfen 2FA von Benutzern **und anderen Org-Admins der eigenen Org** zurücksetzen, nie von Systemadmins
   und nie von sich selbst. Systemadmins dürfen alle Benutzer aller Orgs zurücksetzen. Grundlage:
   `authorize_user_mutation()` (`app/core/permissions.py:196`) – genau diese Regeln (other_org, target_system_admin).
2. Standard-Richtlinie je Org: **aus**. Die Org schaltet selbst ein (optional / Pflicht für Admin-Rollen / Pflicht für alle).
3. **SMS nicht für Systemadmins.** Systemadmins bleiben bei TOTP + Recovery-Codes (Admin-MCP, globaler Schalter
   `admin_2fa_enabled` bleiben unverändert).
4. Kein 2FA-Reset per MCP. MCP liefert nur den Status.
5. 2FA wird nur bei der Anmeldung abgefragt, nie mitten in einer Session (Einsatzbetrieb).

## Sicherheitsbewertung SMS (Kurzfassung)

SMS-OTP ist ein „eingeschränkter“ Faktor (NIST 800-63B): SIM-Swap/Portierung, SS7, Android-Malware/Sperrbildschirm,
Phishing, SMS-Pumping, Zustellprobleme. Für Benutzer/Org-Admins als Alternative zu TOTP vertretbar unter diesen Regeln:

- **PIN-Login-Regel:** `/pin-login` ist bereits ein SMS-basierter Erstfaktor. Nach PIN-Login ist SMS als zweiter
  Faktor **nicht** zulässig, nur TOTP oder Recovery-Code. Dafür trägt das Session-Cookie die Login-Methode (`lm`).
- **Eigene 2FA-Nummer:** `user.phone` ist an vielen Stellen änderbar (`ui_profile.py:72`, `ui_admin.py:943/1009`,
  MCP `benutzer_aktualisieren` in `app/mcp/tools/benutzer.py:233`, Mitgliederabgleich). Die 2FA-Nummer liegt daher
  separat (`user_mfa_sms`), wird nur über Bestätigungscode an die neue Nummer + frischen Step-up gesetzt, löst eine
  Mail aus und ist für Admins/MCP nicht änderbar.
- Strenge Limits: pro Benutzer 3 SMS / 10 min, 10 / Tag, zusätzlich Rate-Limit pro IP. Kein Link in der SMS.

## Prüfbefunde

- 2FA heute nur für `is_system_admin`: Gate in `app/main.py:810-839`, WebSocket `app/routers/ws.py:128`,
  Org-MCP-Sperre `app/mcp/router.py:62-66`. Gate öffnet pro Anfrage eine DB-Session.
- Faktoren: `AdminTotp`, `AdminRecoveryCode` (`app/models/admin_security.py`), Logik `app/services/totp_service.py`,
  Routen `app/routers/ui_mfa.py` (Router verlangt `system_admin`), CLI `mfa-reset` (`app/cli.py:610`).
- Reset heute nur Sysadmin→Sysadmin (`/mfa/zuruecksetzen`, Ziel muss `is_system_admin` sein).
- Session-Cookie: `sign_session()` (`app/core/security.py:226`) mit `e` (Epoche), `a` (Start), `m` (MFA-Zeit).
  Sessions entstehen in `auth.py:99` (Passwort), `sso.py:361`, `ui_pin_login.py:216`, `ui_account_switch.py:49`.
- Org-Einstellungen: `OrgSettings` (`app/models/master.py:382`).
- PIN-Login (`ui_pin_login.py`): 6 Stellen, 10 min, 5 Versuche, Vergleich per `!=` (→ `hmac.compare_digest`).
- Benutzerliste: `/admin/benutzer` (`ui_admin.py:136`, Template `admin/users.html`), Org-Filter `_org_filter`.
- Letzte Migration: 0343.

## Phasen

Jede Phase ein PR (bzw. getrennte Commits). Vor Merge: `ruff check app/`, `mypy app/ --ignore-missing-imports`,
`pytest -q -n auto`, CI inkl. MariaDB-Migrationsjob.

### P1 – Datenmodell und Service generalisieren
- `app/services/mfa_service.py` (aus `totp_service.py`); `totp_service` bleibt als dünner Re-Export, damit
  bestehende Importe/Tests unverändert laufen. Tabellen `admin_totp`/`admin_recovery_code` bleiben (Name egal,
  Spalten passen für alle Benutzer); Modelle bekommen Aliase `UserTotp`/`UserRecoveryCode`.
- Migration 0344: `OrgSettings.mfa_policy` (String(20), Default `aus`; Werte `aus|optional|admins|alle`),
  `OrgSettings.mfa_sms_erlaubt` (Bool, Default true), `OrgSettings.mfa_sso_ausgenommen` (Bool, Default true);
  `User.mfa_reset_at` (DateTime, nullable) für die 24-h-Neueinrichtungsfrist.
- `mfa_service.required_for(db, user) -> bool`: Sysadmin → `admin_2fa_enabled`; sonst Org-Policy
  (`admins` = Rollen aus `ORG_ADMIN_ROLES`), SSO-Benutzer (`entra_oid`) ausgenommen wenn `mfa_sso_ausgenommen`.
  Kleiner In-Prozess-Cache (TTL 30 s) für Policy-Lookups.
- Session-Cookie: neues Feld `lm` (Login-Methode `pw|sso|pin|switch`) in `sign_session`/`session_security`,
  an allen vier Login-Stellen gesetzt. Alte Cookies ohne `lm` = `pw`.

### P2 – Gate, Einrichtung und Profil für alle Benutzer (TOTP)
- Gate in `main.py` und `ws.py` nutzt `required_for()` statt `is_system_admin`; Ausnahmen wie bisher (Geräte, QR,
  GK, Alarmlink, Gast, API-Key, Exempt-Pfade). Benutzer mit Pflicht ohne Faktor → `/mfa/einrichten`.
- `ui_mfa.py`: Router-Dependency auf eingeloggten persönlichen Benutzer statt `system_admin`; Verify/Einrichten/
  Bestätigen funktionieren für alle. Weiterleitung nach Verify auf ursprüngliches Ziel bzw. `/`.
- Profil `/profil`: Abschnitt „Sicherheit“ (Status, Einrichten, Recovery-Codes neu, eigene 2FA mit Code entfernen –
  nur wenn keine Pflicht).
- Org-Einstellung (Verwaltung, Org-Admin): Richtlinie + SMS erlaubt + SSO ausgenommen. Einschalten von `alle`/`admins`
  zeigt Anzahl noch nicht eingerichteter Benutzer; keine Sperre, die betroffenen richten beim nächsten Login ein.
- Org-MCP-Sperre (`mcp/router.py:62`) bleibt nur für Sysadmins.

### P3 – Admin-Reset (Org- und Systemadmin)
- Service `mfa_service.admin_reset(db, actor, target, grund, mfa_at)`:
  `authorize_user_mutation(db, actor, target, "mfa_reset")`; Selbst-Reset verboten (403); Step-up: hat der Akteur
  eine aktive 2FA, muss `fresh(mfa_at)` gelten; Grund Pflicht (3–500 Zeichen). Löscht TOTP, Recovery-Codes,
  SMS-Faktor, `session_epoch += 1`, setzt `mfa_reset_at`, Audit `admin.mfa.reset` mit Akteur, Ziel, Grund.
  Benachrichtigung per Mail (bestehendes `notify_reset`) und – falls bestätigte 2FA-Nummer vorhanden war – SMS.
- Eigene Aktion „Sperre aufheben“ (`failed_count=0`, `locked_until=None`), gleiche Berechtigung, Audit `admin.mfa.unlock`.
- 24-h-Frist: Ist `mfa_reset_at` älter als 24 h und noch kein Faktor eingerichtet, während Pflicht gilt, wird die
  Anmeldung abgewiesen („2FA-Reset abgelaufen, Admin kontaktieren“); neuer Reset setzt die Frist neu.
- UI `/admin/benutzer`: Spalte/Badge 2FA (aus / TOTP / SMS / gesperrt), Button „2FA zurücksetzen“ mit Dialog
  (Grund) und „Sperre aufheben“; POST `/admin/benutzer/{id}/mfa-zuruecksetzen` und `/mfa-entsperren`, CSRF.
  Sysadmin sieht dort alle Orgs (bestehendes Verhalten); `/mfa/zuruecksetzen` ruft intern denselben Service.
- Tests: Org-Admin A → Benutzer A ok, → Org-Admin A ok, → Sysadmin 403, → Benutzer Org B 403, Selbst 403,
  ohne Step-up 403, Grund fehlt 400, Sessions des Ziels ungültig, Audit-Eintrag, Mail ausgelöst, Frist.

### P4 – SMS als zweiter Faktor (nicht für Sysadmins)
- Tabelle `user_mfa_sms` (user_id PK, phone_enc, verified_at, failed_count, locked_until, created_at) (Migration 0345);
  `login_pin.purpose` (`login|mfa|mfa_setup`, Default `login`).
- Einrichtung: Nummer eingeben (Vorschlag `user.phone`), Code an diese Nummer, Bestätigung → Faktor aktiv,
  Recovery-Codes, Mail an Benutzer. Nummernwechsel nur mit frischem Step-up.
- Verify: Code anfordern (Button), 6 Stellen, 5 min, Einmalgebrauch, `compare_digest`, 5 Fehlversuche → Sperre 5 min.
  Limits 3/10 min und 10/Tag pro Benutzer, IP-Limit über `limiter`. SMS-Text ohne Link.
- PIN-Login-Regel: `lm == "pin"` → SMS-Faktor nicht angeboten, nur TOTP/Recovery; hat der Benutzer nur SMS,
  Hinweis „Bitte mit Passwort anmelden“.
- Sysadmins: SMS-Einrichtung abgelehnt; Org mit `mfa_sms_erlaubt=false`: SMS nicht angeboten.
- Fallback bei `sms_available()==false`: Hinweis auf Recovery-Code.
- Nebenbei: PIN-Vergleich in `ui_pin_login.py:202` auf `hmac.compare_digest`.

### P5 – MCP-Status und Doku
- `benutzer_lesen`/`benutzer_liste`: `zwei_faktor: {aktiv, methode, gesperrt}` ohne Geheimnisse; kein Reset-Tool.
- Wiki/Docs (Admin-Handbuch: Richtlinie, Reset-Ablauf, Notfall-CLI), `CHANGELOG.md` Unreleased, Release-Hinweis
  (Migrationen 0344–0345, Policy Default aus).

## Offene Punkte nach Umsetzung
- Gerätetest Android-App/Tablet (Einrichtung im WebView, QR-Code abscannen auf demselben Gerät → Secret-Text).
- Live-Test SMS-Faktor mit echtem Gateway.
