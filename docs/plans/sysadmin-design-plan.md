# Sysadmin-Design: Systemdiagnose, System-Einstellungen und Umfeld

Stand 2026-11-01. Ziel: Den System-Admin-Bereich optisch auf den Stand der übrigen Oberfläche
(Tabler-Icons, Karten, Statusfarben, Mobilansicht) bringen, ohne Verhalten, Routen, HTMX-Ziele
oder Berechtigungen zu ändern.

## Befund (Ist-Zustand)

| Seite | Datei | Problem |
|---|---|---|
| Systemdiagnose | `admin/systemdiagnose/index.html`, `_panel.html` | Reiter sind graue Einzelbuttons ohne aktiven Zustand; Gesamtzustand besteht aus `<pre>`-JSON; Befunde, Läufe, Audit sind unstrukturierte Absätze; Ampel nur als Textzeile |
| Systemdiagnose (Geräte) | `_geraete.html`, `_geraet.html`, `_finding.html`, `_codex.html` | 8-Spalten-Tabelle ohne Zeilenhierarchie, Detailseite aus Fließtext, `<h3 style="font-size:1.75rem">` |
| Texte | alle `systemdiagnose/*` | ASCII-Umschreibungen ("Pruefung", "Geraete", "Zurueck", "ausdruecklich") statt Umlaute |
| System-Einstellungen | `admin/system_settings.html` (649 Zeilen) | 13 gleich aussehende Karten untereinander, keine Sprungnavigation, Schalter als `<select>` Inaktiv/Aktiv, Inline-Styles, Sicherheitsschalter ohne Gruppierung |
| Systemstatus | `systemstatus.html`, `_systemstatus_liste.html` | Karten ohne Statusfarbe/Icon, Rohstatus-Text, Worker-/Gateway-Tabellen ohne Zustandspunkte |
| Umfeld | `server_log.html`, `system_update.html`, `sysadmin_orgs.html`, `sysadmin_quotas.html`, `repair_approval.html`, `mcp/admin_anmelden.html` | uneinheitliche Kopfzeilen, Inline-Styles |

## Designprinzipien

1. Eine neue Datei `app/static/css/sysadmin.css` (handgeschrieben, kein Tailwind-Build nötig,
   Präfix `sys-`), nur auf Sysadmin-Seiten per `<link>` eingebunden. Nutzt ausschließlich vorhandene
   Variablen (`--surface*`, `--border`, `--text-muted`, `--green`, `--red`, `--on-surface`).
2. Bausteine: `sys-hero` (Gesamtampel mit Icon, Version, Commit, Build), `sys-kpis`/`sys-kpi`
   (Kennzahlkarten), `sys-card` mit Kopf (Icon, Titel, Statuspill), `sys-kv` (Definitionsliste
   Schlüssel/Wert statt `<pre>`-JSON), `sys-tabs` (aktiver Reiter, scrollbar auf Mobil),
   `sys-switch-row` (Beschriftung + Beschreibung + Schalter), `sys-table` (sticky Kopf,
   Zeilen-Hover, Zustandspunkt), `sys-empty` (Leerzustand mit Icon).
3. Icons nur über `{{ icon("…") }}` und `status_dot` (CLAUDE.md). Neue Icon-Namen in
   `app/static/icons/tabler-auswahl.txt` und `npm run vendor`.
4. Keine Emojis, keine Inline-Styles außer dynamischen Werten, nur ASCII-Anführungszeichen.
5. Mobil (<= 760 px): einspaltig, Touch-Ziele 44 px, Tabellen in `overflow-x:auto` oder als Karten.
6. Barrierefreiheit: `aria-live` und `aria-label` bleiben, Reiter mit `aria-current`/`role`,
   Fokusrahmen sichtbar, Statusfarbe nie allein (immer Text oder Icon dazu).
7. Verhalten unverändert: gleiche `hx-*`-Attribute, Formularfelder, Feldnamen und CSRF-Felder.

## Pakete (je ein Codex-Auftrag, gestapelt im selben Worktree)

### P1 – Fundament `sysadmin.css`
- Komponenten oben anlegen, auf der Seite `system_settings.html` noch nicht verwenden.
- Mini-Styleguide als Kommentarblock im CSS. `CSS_VERSION` bleibt (Datei bekommt eigene
  Versionierung über `?v={{ CSS_VERSION }}`).

### P2 – Systemdiagnose: Rahmen, Gesamtzustand, Diagnoselauf
- `index.html`: `sys-hero`-Kopf, Reiter als `sys-tabs` mit Icons und aktivem Zustand
  (Alpine `x-data="{tab:'gesamt'}"`, HTMX-Swap setzt aktiv; ohne JS bleibt `hx-get`).
- `_panel.html` Abschnitt `gesamt`: Ampel-Hero, je Prüfteil eine `sys-card` mit Statuspill und
  `sys-kv` aus `part.evidence` (Makro `kv(value)`), JSON nur noch unter `<details>` "Rohdaten".
- Abschnitt `diagnose`: Startformular als Kartenzeile, Lauffortschritt mit `<progress>` und
  Schweregrad-Pills, Checkliste als Tabelle (ID, Beschreibung, Kosten-Pill).
- Umlaute korrigieren, dabei Testbehauptungen mitziehen (siehe Tests).

### P3 – Systemdiagnose: Befunde, Läufe, Reparaturen, GitHub, Audit
- Befunde: Filterleiste als `sys-toolbar`, Karten mit farbigem Schweregradband
  (info/warn/fail/kritisch → `status_dot`), Aktionen als Buttonzeile.
- Läufe und Audit: `sys-table` mit Seitenschaltern.
- Reparaturen: Risikostufe als Pill (LOW/MEDIUM/HIGH/CRITICAL), Freigabeschritte als nummerierte
  Liste, gesperrt-Hinweis als `alert`.
- GitHub-CI/Code: `tree()`-Makro durch `sys-kv`-Darstellung ersetzen.

### P4 – Systemdiagnose: App-Geräte, Befund-Detail, Codex-Auftrag
- `_geraete.html`: Verteilungskarten als Balkenliste, Filter als `sys-toolbar`, Tabelle mit
  Gesundheits-/Versionspill, auf Mobil Kartenliste.
- `_geraet.html`: Kopfkarte (Gerät, Benutzer, Org, Ampel), Detailgruppen als `sys-kv`-Karten,
  Fehlerliste als `sys-table`. `_finding.html`, `_codex.html` auf Karten umstellen,
  Codex-Textarea mit Monospace und Kopierbutton in der Kartenkopfzeile.

### P5 – System-Einstellungen
- Sticky Sprungnavigation links (Desktop) bzw. Chips (Mobil) mit Gruppen:
  Sicherheit (2FA, Admin-MCP, GitHub-Zugang), Diagnose, E-Mail (SMTP, Resend), Push (Web, FCM),
  Betrieb (Auto-Schließen, Allgemein), KI, Großschadenslage, Benutzerdefiniert, Verbindungstests.
- Jede Karte: Icon im Kopf, einzeiliger Beschreibungstext, Statuspill (konfiguriert / nicht
  konfiguriert / aktiv).
- `<select>` Inaktiv/Aktiv bei reinen Ein/Aus-Schaltern → `sys-switch-row` mit Checkbox und
  `role="switch"`. Das gesendete Feld bleibt kompatibel: `enabled` weiterhin `true`/`false`
  (verstecktes Feld + Checkbox-Skript, Router-Tests prüfen das).
- Versionszeile als Kopfkennzahl (Version, Commit, Build) statt Fließtext.
- Meldungen (`mail_ok`, `github_error` …) als gemeinsames Makro `flash()`.
- Speicherleiste: pro Karte eigener Speichern-Button bleibt, Formulare werden nicht zusammengelegt.

### P6 – Systemstatus und Umfeld
- `systemstatus.html` / `_systemstatus_liste.html`: Dienstkarten mit Statusfarbe links, Icon,
  Teilzustände als Zeilen mit `status_dot`, Worker-/Gateway-Tabellen mit Online-Punkt.
- `server_log.html`, `system_update.html`, `sysadmin_orgs.html`, `sysadmin_quotas.html`,
  `repair_approval.html`, `mcp/admin_anmelden.html`: Kopfzeile und Karten auf `sys-*`
  vereinheitlichen, Inline-Styles entfernen.

### P7 – Tests, Sichtprüfung, Doku
- Bestehende Tests anpassen, wo sie Text prüfen (`tests/test_systemdiagnose_ui.py`,
  `tests/test_app_geraet_ui.py`, `tests/test_github_token_management.py`,
  `tests/test_fcm_settings_roundtrip.py`, `tests/test_systemstatus_nav.py`).
- Neuer Test: Sysadmin-Seiten enthalten kein `style="` außerhalb dynamischer Werte, keine
  Smart Quotes, `sysadmin.css` ist eingebunden, Schalter senden `true`/`false`.
- Playwright-Screenshots (Desktop 1440 px, Mobil 390 px, Hell/Dunkel falls vorhanden) der
  sechs Seiten gegen Demo-Daten, Ergebnis im PR.
- Doku: Kurzabschnitt in `docs/` (Komponentenkatalog `sys-*`), Eintrag in README/Wiki nur wenn
  vorhandene Admin-Doku Screenshots zeigt.

## Nicht im Umfang
Routen, Berechtigungen, Datenmodelle, Migrationen, MCP-Tools, Org-Admin-Seiten außerhalb der
Sysadmin-Navigation, Textänderungen an Fachaussagen.

## Risiken
- Tests prüfen teils Klartext ("Bestaetigung", "Rate-Limit erreicht"): Umlaut-Korrektur nur
  zusammen mit Testanpassung, Fachbegriffe in Servermeldungen (Router) bleiben vorerst.
- Schalter statt Select: Router erwarten `enabled=true|false`; verstecktes Feld sichert das.
- HTMX-Swap von `#diag-panel` darf den aktiven Reiter nicht verlieren (Alpine-Zustand außerhalb
  des Panels halten).
- Kein `ruff format`, kein globaler Reformat; Codex nur gezielte Testdateien, nie die volle Suite.

## Abnahme
ruff, mypy, `pytest -n auto`, Screenshots, CI grün, dann Squash-Merge; Gerätesichtprüfung durch den Nutzer.

## Erweiterung 2026-11-01: restliche Verwaltung (Pakete V1-V8)

Alle weiteren Seiten, die `admin/_layout.html` erweitern (82 Dateien, davon bis zu 170 Inline-Styles
je Seite), werden auf dieselben `sys-*`-Bausteine umgestellt. Vorgehen je Seite: `sys-page` +
`sys-hero`-Kopf, Karten als `sys-card`, Tabellen als `sys-table` (mobil mit `data-label`),
Formulare im `sys-form-grid`, Inline-Styles durch Klassen ersetzen (Hilfsklassen `sys-stack`,
`sys-row`, `sys-grid-2`, `sys-mt-*`, `sys-muted`), Emojis/ASCII-Umlaute bereinigen. Verhalten,
Routen, Felder, `hx-*`, Alpine-Logik und IDs bleiben unveraendert. Je Paket Chromium-Pruefung bei
1440 und 390 px (kein horizontaler Overflow, keine JS-Fehler).

| Paket | Inhalt |
|---|---|
| V1 | Organisations-Einstellungen: `settings.html`, `settings_wetter*.html`, `settings_strassensperren_infoscreen.html`, `road_closure/einstellungen.html`, `settings_statistik.html`, `_settings_statistik_panel.html`, `settings_wordpress_report.html`, `settings_org_mail.html` |
| V2 | Integrationen: `settings_sso.html`, `_sso_group_map.html`, `settings_dibos.html`, `_dibos_*.html`, `dibos_einsaetze.html`, `settings_teams_bot.html`, `settings_lis.html`, `_lis_captures.html`, `gsl_einstellungen.html`, `einsatz_import.html` |
| V3 | Alarmierung und Nachrichten: `sms_*.html`, `_sms_*.html`, `einsatzinfo_sms.html`, `einsatzinfo_mail.html`, `push_notifications.html`, `default_messages.html`, `dispatch_order.html`, `alarm_types.html`, `mail_eingang*.html`, `_mail_eingang_content.html`, `mail_weiterleitungen.html`, `message_suggestions.html`, `task_suggestions.html`, `lage_hints.html`, `ai_prompts.html`, `ai_request_log.html` |
| V4 | Geraete und Zugaenge: `device_tokens.html`, `device_token_detail.html`, `api_keys.html`, `lagekarte_tokens.html`, `_auth_failures.html`, `atemschutz_pruefung/*`, `gateway/*` |
| V5 | Stammdaten: `users.html`, `user_mail_preview.html`, `members.html`, `vehicles.html`, `qualifications.html`, `organisations.html`, `wasserstellen.html` |
| V6 | Foerderstrecke, Verleih, Probenplanung, Planspiel, Objekt-Infoscreen: `foerder_*.html`, `verleih/*`, `probenplanung/*`, `planspiel/admin/*`, `objekt/infoscreen_verwaltung.html` |
| V7 | Betrieb: `backup.html`, `org_backup.html`, `org_restore.html`, `db_backups.html`, `_db_backups_liste.html`, `_github_update.html`, `_github_branch_update.html`, `medienverwaltung.html`, `seed_templates.html`, `pages_list.html`, `page_edit.html`, `konfig.html`, `about.html`, `hilfe.html`, `index.html` |
| V8 | Abschluss: Waechtertest auf alle Admin-Seiten erweitern (keine statischen Inline-Styles), Screenshots, CHANGELOG, Wiki-/Doku-Hinweis, volle CI |
