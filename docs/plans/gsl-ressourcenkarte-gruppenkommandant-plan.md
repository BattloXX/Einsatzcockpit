# GSL-Ressourcenkarte und Gruppenkommandanten-Zugang – Implementierungsplan

Stand: 2026-10-10 · Basis: `main` @ `b19479b1` (Einsatzcockpit)
Status: **Plan, nichts umgesetzt** · Schwesterplan: `docs/plans/gsl-einheitenmodus-plan.md` (Phase 1 umgesetzt, Phase 2/3 offen)

> **Leitlinie:** Die vorhandene Ressourcenübersicht (`/lage/{id}/ressourcen`) wird nicht neu gebaut. Jede Einheitenkarte
> bekommt eine Detailkarte (Drawer), die vorhandene Daten und Services bündelt und um das Fehlende ergänzt:
> Gruppenkommandant mit Telefonnummer, Journal pro Ressource, Personal, Ausstattung, Zugang.
> Der Gruppenkommandant arbeitet danach im **bestehenden** Einheitenmodus (`/einheit`) – nur mit einem anderen Anmeldeweg.
> **Sicherheitsprinzip:** Der Zugangslink ist ein Geheimnis, das in SMS- und Chatverläufen liegen bleibt.
> Deshalb gilt: nur der Hash wird gespeichert, es gibt genau einen gültigen Zugang je Einheit, jede Neuausgabe ersetzt den alten
> Zugang atomar, und jede Anfrage prüft den Zugang gegen die Datenbank neu.

Legende (wie im Schwesterplan): ✅ vorhanden, wird unverändert genutzt · 🔧 vorhanden, wird erweitert · 🆕 neu

---

## Inhalt

0. [Kurzfassung und Kernentscheidungen](#0-kurzfassung-und-kernentscheidungen)
1. [Ist-Analyse](#1-ist-analyse)
2. [Vorhandene Funktionen, Datenmodelle und Abgrenzung zum Einheitenmodus-Plan](#2-vorhandenes-und-abgrenzung)
3. [Erweiterungen der Ressourcenübersicht](#3-erweiterungen-der-ressourcenübersicht)
4. [UI-/UX-Konzept](#4-ui-ux-konzept)
5. [Datenmodell und Migrationen](#5-datenmodell-und-migrationen)
6. [Token- und Berechtigungskonzept](#6-token--und-berechtigungskonzept)
7. [SMS-Versand und Copy & Paste](#7-sms-versand-und-copy--paste)
8. [Automatische SMS und GSL-Konfiguration](#8-automatische-sms-und-gsl-konfiguration)
9. [Einbindung des Einheitenmodus und Offline](#9-einbindung-des-einheitenmodus-und-offline)
10. [Ressourcenjournal und Einsatzhistorie](#10-ressourcenjournal-und-einsatzhistorie)
11. [Personal, Ausstattung, Verstärken, Teilen](#11-personal-ausstattung-verstärken-teilen)
12. [Schnittstellen und MCP](#12-schnittstellen-und-mcp)
13. [Sicherheits- und Fehlerszenarien](#13-sicherheits--und-fehlerszenarien)
14. [Tests und Akzeptanzkriterien](#14-tests-und-akzeptanzkriterien)
15. [Arbeitspakete](#15-arbeitspakete)
16. [Umsetzungsregeln für Codex und offene Punkte](#16-umsetzungsregeln-und-offene-punkte)

---

## 0. Kurzfassung und Kernentscheidungen

| # | Entscheidung | Begründung |
|---|---|---|
| D1 | Der Gruppenkommandant-Zugang ist **kein `User` und kein `DeviceToken`**. Er ist ein eigener Principal (Tabellen `lage_einheit_zugang` + `lage_einheit_zugang_session`) mit eigenem Cookie `ec_gk`. | Ein User mit Rolle könnte jede `require_role`-Route erreichen (das ist die Lücke, die der Einheitenmodus-Plan in 9.2 für Tablets mit Allowlist schließt). Ohne `request.state.user` scheitern alle Führungsrouten, MCP (`load_live_context` verlangt einen aktiven Nicht-Geräte-User) und `/ws/lage/{id}` von selbst. Berechtigt wird nur, was `einheit_kontext` bewusst auflöst. |
| D2 | **Eine Zeile je Einheit** (`UNIQUE(einheit_id)`). Rotation ist ein `UPDATE` (neuer Hash, `generation + 1`). | „Nie mehr als ein gültiger Zugang je Einheit“ wird von der Datenbank erzwungen, nicht nur vom Code. Rotation ist atomar. |
| D3 | **Nur der Hash wird gespeichert** (SHA-256, Muster `hash_api_key`). Klartext existiert nur als Rückgabewert der Erzeugungsfunktion. **Erneute Zustellung = kontrollierte Rotation.** | Vorgabe der Anforderung; kein reversibler Speicher (`encrypt_secret` wird bewusst nicht verwendet). |
| D4 | Link-Format `https://<host>/gk#<token>` (Token im **Fragment**). `GET /gk` ist statisch und löst nichts ein; Einlösung per `POST`. | Das Fragment erscheint weder in nginx-/App-Logs noch im Referer. Link-Vorschau-Bots von WhatsApp/Teams (Defender SafeLinks) und SMS-Scanner rufen nur `GET /gk` ab und verbrauchen nichts. |
| D5 | Sitzungen sind serverseitig (Cookie = Zufallswert, DB = Hash) und tragen die `generation` des Zugangs. Jede Anfrage prüft Zugang, Sitzung, Gruppenkommandant, Telefonnummer, Einheit, Lage und Org-Schalter in der Datenbank neu. | Widerruf wirkt sofort auch auf offene Sitzungen; es gibt keinen Berechtigungs-Cache. |
| D6 | Optionale **SMS-PIN** beim Einlösen (Org-Schalter, Standard aus). Die PIN geht an die hinterlegte Nummer des Gruppenkommandanten. | Schützt genau den Fall „Link liegt in einer Chatgruppe“. Ohne PIN bleibt der Link ein Inhaber-Token. |
| D7 | Die Telefonnummer des Gruppenkommandanten gehört an `LageEinheitLeader` (lagebezogen, aus `Member.phone` vorbelegt). Mitglieder-Stammdaten werden nie verändert. | Trennung Stammdaten / Lagezustand (Anforderung 10 und 11). |
| D8 | Das Ressourcenjournal bekommt **keine neue Tabelle**. `LageJournalEntry` erhält `einheit_id` u. a.; die Karte zeigt eine zusammengeführte Sicht aus `LageJournalEntry`, `SiteLogEntry` (+ `CommLogEntry` nach P2-1 des Schwesterplans) und den Dispatch-Zeitstempeln. | „Keine zweite Journalstruktur.“ |
| D9 | Personal und Ausstattung brauchen zwei schlanke Lagetabellen (es gibt heute keine). Aggregatzahlen liegen an `LageEinheit`. Doppelzählung verhindert ein DB-Schlüssel (`aktiv_key`). | Im Repository existiert kein Personal-/Beladungsmodell außer `Member`, `AtemschutzGeraet` und dem Geräteverleih. |
| D10 | Die Detailkarte ist ein **Drawer außerhalb** des 30-s-Polling-Containers der Kräfteübersicht. | `kraefteuebersicht-container` ersetzt sich per `hx-swap="innerHTML"` selbst; ein Drawer darin würde Eingaben verlieren (siehe 4.3). |
| D11 | Der automatische Versand ist ein **Outbox-Eintrag im selben Commit** wie die Zuweisung (Status `geplant`), der Versand läuft nach dem Commit. Der Klartext-Token wird **nie** in der Outbox abgelegt. | Kein SMS-Versand vor dem Commit, kein Doppelversand (UNIQUE-Schlüssel), sichtbarer Zustand auch nach Prozessabbruch. |
| D12 | **MCP kann den Link weder anzeigen noch kopieren.** `gsl_ressource_zugang_senden` verschickt nur per SMS und gibt nie Token oder Link zurück. | Tool-Antworten landen in KI-Transkripten. |

Standardwerte: Gesamtschalter `gk_zugang_aktiv = false`, automatischer Versand `false`, SMS-PIN `false`. Solange der Schalter aus ist, ändert sich für niemanden etwas. Das ist zugleich die Freigabesperre für die Phasen 2/3.

---

## 1. Ist-Analyse

Alle Angaben gegen `b19479b1` geprüft (nicht gegen die Stände der Schwesterpläne).

### 1.1 Ressourcenübersicht und Detailkarte

| Befund | Ort | Folge für den Plan |
|---|---|---|
| Ressourcenübersicht: `GET /lage/{id}/ressourcen` (`lage_ressourcen`, `ui_major_incident.py:4773`), Template `incident_major/ressourcen.html` (342 Z.) mit Tabs Übersicht / Planung / Journal | ✅ | bleibt; Tab-Struktur bleibt |
| Karten werden im Makro `einheit_card(e)` in `_kraefteuebersicht.html` gerendert; der Container `#kraefteuebersicht-container` lädt sich alle 30 s und bei `ressourceChanged` per `hx-swap="innerHTML"` neu | `ressourcen.html:253` | Drawer muss außerhalb liegen (wie `<dialog id="siteDetailModal">`, `ressourcen.html:284`) |
| Es gibt **keine** Detailansicht pro Einheit, nur Inline-Aktionen (Sektor, Pool, „Abg.“, Führer-Textfeld, Löschen) | `_kraefteuebersicht.html` | Karte ist neu, die Inline-Aktionen bleiben |
| Datenbasis `resource_service.kraefteuebersicht()` liefert Pool / im Einsatz / Abschnitte / abgerückt, `dispatched_sites_by_einheit`, `letzte_rueckmeldung_by_einheit`, `conflict_vids` | `resource_service.py:885` | wird für die Karte nicht dupliziert; die Karte liest je Einheit |
| WebSocket-Event `ressource:changed` trägt **keine** `einheit_id`; `lage_board.js`/`ressourcen.html` reagieren mit Vollrefresh des Containers | `ui_major_incident.py` (alle Einheiten-Routen) | additiv `einheit_id` ergänzen (abwärtskompatibel) |
| Legacy-Routen: `POST …/einheiten/{id}/kommandant` (Freitext-Inline-Feld, feuert bei jedem `change`), `…/fuehrer` | `ui_major_incident.py:5001/5157` | beide rufen `rotate_einheit_leadership` (legt **immer** eine neue Führer-Zeile an, auch bei gleichem Namen) → würde bei Auto-SMS Doppelversand und unnötige Rotation auslösen; werden auf den neuen Service umgestellt |

### 1.2 Gruppenkommandant

- `LageEinheitLeader` (`major_incident.py:485`): `member_id`, `person_name`, `start_at`, `end_at`, `predecessor_id`, `note`, `created_by`. **Keine Telefonnummer, keine Rolle (Führer/Stellvertreter).** `LageEinheit.leader_assignment_id` zeigt auf die aktuelle Zeile, `commander_label` ist die denormalisierte Anzeige.
- `Member.phone` und `Member.ist_gruppenkommandant` existieren (`master.py:189/193`).
- `resource_service.rotate_einheit_leadership()` (Z. 756) beendet die alte Zeile, legt eine neue an, schreibt Journal `ressource_fhr`. Keine Änderungserkennung.
- Telefon-Helfer: `app/core/telefon.py` (`telefon_identitaet_at`, `telefon_e164`). **Lücke:** `telefon_e164("0664 123 45 67")` liefert `None` (nationale Nummern werden nicht umgesetzt) – ein Helfer „AT-national → E.164“ fehlt (5.5).

### 1.3 Einsätze (Dispositionen)

- `EinheitSiteDispatch` ist seit dem Einheitenmodus (Migration 0263) der „Auftrag“: `auftrag`, `einheit_status`, `status_at`, `bestaetigt_at`, `beendet_at`, `beendet_grund`, `reihenfolge`, `version`, `letzte_rueckmeldung_at`, `vor_ort_at`, `withdrawn_at`, `dispatched_by`, `author_name`.
- `einheit_service.auftraege_fuer_einheit(db, ctx)` liefert bereits die Kategorien `aktuell / weitere / abgeschlossen / zurueckgezogen` – wird für die Karte wiederverwendet (über `kontext_fuer_einheit(..., quelle="funk")`).
- **Lücken für Anforderung 8:** Rückzug speichert nur `withdrawn_at` – es fehlen **Grund und veranlassende Person**. „Abschlussmeldung“ und „dokumentierte Maßnahmen“ liegen verstreut (`beendet_grund`, `SiteLogEntry.kind in (lagemeldung, massnahmen)` mit `einheit_id`). Eine Einsatznummer gibt es nur indirekt (`IncidentSite.incident_id → Incident.lis_operation_number`, sonst `external_key`).

### 1.4 Journal und Ereignisse

- Das „Ressourcen-Journal“ ist heute `LageJournalEntry` mit `category in ("ressource", "ressource_fhr")`, reiner Text (`resource_service._journal`, Z. 74), **ohne Bezug zur Einheit**. `GET /lage/{id}/ressourcen/journal` zeigt die letzten 300 Einträge der ganzen Lage (`_ressourcen_journal.html`).
- `SiteLogEntry` hat seit 0263 `einheit_id`, `kind="einheit"` für Statusänderungen. `CommLogEntry` hat **noch keinen** Einheitenbezug (kommt mit P2-1 des Schwesterplans).
- `POST /lage/{id}/journal/{entry}/loeschen` löscht Stab-Journal-Einträge **hart** (`ui_major_incident.py:2479`) – für Ressourceneinträge nicht zulässig (Nachvollziehbarkeit).
- Nicht für jedes der 14 geforderten Ereignisse wird heute ein Eintrag geschrieben (z. B. „Einsatz begonnen“, „Lagemeldung übermittelt“ nur an der Stelle, nicht an der Einheit; Personal/Ausstattung/Unterstützung existieren noch nicht).

### 1.5 SMS-Infrastruktur

- `sms_service.send_sms(org_id, to, text, ctx=…)` (Gateway-WebSocket und/oder EUS, mit Fallback-Kette) → `SmsDeliveryResult(success, provider, gateway_token_id)`; `sms_available(org_id)`; `resolve_sms_config()`.
- Protokoll: `SmsLog` (+ `SmsLogRecipient`) mit **Klartext-`text`** (`sms.py:99`), Quellen `alarm|manual|api|gsl_alarm`. **Konsequenz:** Der Standard-Pfad (`dispatch_manual_sms` & Co.) schreibt den gesendeten Text ins Protokoll – für Zugangslinks wäre das ein Klartext-Token in der DB. Der Zugangsversand muss `send_sms` direkt aufrufen und `SmsLog` selbst mit **geschwärztem** Text schreiben.
- Übungsschutz: `exercise_guard.darf_extern("sms", is_exercise, org_id, db)` (Org-Flag `einsatzinfo_sms_send_exercise`, Standard aus).
- Vorbild für Org-konfigurierbare GSL-SMS-Texte: `OrgSettings.gsl_alarm_text`, `gsl_verleih_sms_*_text` (`master.py:514–520`), Formular `admin/gsl_einstellungen.html`, Speichern in `ui_settings.gsl_einstellungen_save` (Z. 2655), Platzhalter über `sms_dispatch_service.render_template`.
- Für Alarm-SMS existiert das Outbox-Muster `alarm_outbox.py` (Jobs mit Retry) – für den Zugangslink **nicht** wiederverwendbar, weil dort der Text in der Zeile steht.

### 1.6 Geräte-, Session- und Tokenmodelle (Muster)

| Muster | Ort | Wiederverwendung |
|---|---|---|
| `DeviceToken` (Hash, Widerruf, `gsl_profil`), Session-Cookie mit `device_token_id`, Prüfung pro Request in `session_middleware` | `user.py:154`, `main.py:569`, `security.py:28` | Vorbild, **nicht** erweitern (D1) |
| Straßensperren-Zugangstokens (`rcd_`-Präfix, Hash, Widerruf, Ablauf) | `road_closure_token_service.py` | Präfix- und Hash-Muster |
| Lage-QR-Token (`LageToken`, Session `qr_lage_id`, Rolle `recorder`) | `main.py:513` | Gegenbeispiel: ein QR-User bekommt Rolle `recorder` und könnte Zugänge ausgeben → Ausgabe nur für echte Personen-Sessions (6.7) |
| PIN-Rate-Limits (`slowapi`, `5/15minutes`), SHA-256-PIN mit Versuchszähler | `auth.py:164`, `login_pin.py` | Muster für die SMS-PIN |
| CSRF Double-Submit (`ec_csrf`), unabhängig von der Session | `middleware/csrf.py` | gilt unverändert auch für `ec_gk`-Sitzungen |
| Einheitenmodus-Kontext `einheit_kontext` (Gerät oder Admin-Simulation) | `ui_einheit.py:163` | bekommt einen dritten Zweig (9.1) |
| `EinheitKontext(device_token, vehicle, einheit, lage, org_id, quelle, simulation)` | `einheit_service.py:143` | `device_token=None` ist bereits zulässig |
| `EinheitAktion.device_token_id` für Idempotenz (`gleich = vorhanden.device_token_id == …`) | `ui_einheit.py:237/598` | braucht `zugang_id` (5.3) |
| `/ws/lage/{id}` prüft `lage.org_id == user.org_id` und ist ein Lage-Kanal (Events tragen nur IDs) | `ws.py:212` | Zugang bekommt einen **eigenen, einheitengefilterten** Kanal (9.4) |
| MCP: `load_live_context` lehnt Geräte-Benutzer ab; keine GSL-Tools vorhanden | `mcp/context.py`, `mcp/tools/` | GK-Zugang kann MCP strukturell nicht erreichen (12.3) |
| `close_lage()` ist der einzige Abschluss-Pfad (`major_incident_service.py:146`), Wiedereröffnung setzt nur `status = active` (`ui_major_incident.py:1968`) | ✅ | zentraler Hook für Widerruf (6.5) |
| Migrationsstand: Head ist **0264** (`merge_org_admin_role`); der Schwesterplan hatte 0264 für Phase 2 reserviert | `alembic/versions` | dieser Plan beginnt bei **0265**; Schwesterplan-Phase-2 nimmt die jeweils nächste freie Nummer |
| Rolle `org_admin` wurde in `admin` aufgegangen (#493), Dekoratoren führen sie teils noch | Router | neue Routen verwenden dieselben Rollentupel wie ihre Nachbarrouten; keine eigene Rechtematrix |

---

## 2. Vorhandenes und Abgrenzung

### 2.1 Wiederverwendung statt Neubau

| Bereich | Vorhandenes | Wiederverwendung | Neu |
|---|---|---|---|
| Übersicht | `lage_ressourcen`, `_kraefteuebersicht.html`, `kraefteuebersicht()` | Karten, Filter (Abschnitte), Drag/Dispo, 30-s-Refresh bleiben | Klick auf Karte → Drawer |
| Einheit | `LageEinheit` (+ `resource_type`, `status`, `sector`, `org_name`, `bos`, `qty`) | alle Felder | `funkrufname`, `bereitstellungsraum`, `status_at`, Personal-Aggregate, `verband_id` |
| GK | `LageEinheitLeader`, `rotate_einheit_leadership`, `Member.phone/ist_gruppenkommandant` | Historie, Predecessor-Kette, Journal `ressource_fhr` | `phone`, `phone_e164`, `phone_version`, `rolle`; Service `setze_gruppenkommandant` (ersetzt den Rotationspfad) |
| Aufträge | `EinheitSiteDispatch`, `auftraege_fuer_einheit`, Stellen-Detail `_site_detail.html` | Kategorien, Klick → `/lage/{id}?openSite=` bzw. `htmx.ajax('GET', '/lage/{id}/stellen/{site}')` | Rückzug: Grund und Person |
| Journal | `LageJournalEntry`, `SiteLogEntry`, `_journal` | `_journal` wird zur zentralen Ereignisfunktion | Spalten `einheit_id`, `site_id`, `ereignis_typ`, `quelle`, Storno |
| SMS | `send_sms`, `sms_available`, `SmsLog`, `darf_extern`, `render_template` | Versandkette, Provider-Fallback, Übungssperre | Nachrichtenvorlage, Segmentzähler, geschwärztes Protokoll |
| Einstellungen | `OrgSettings`, `gsl_einstellungen.html`, `gsl_einstellungen_save` | Seite und Speicherpfad | 8 Spalten (Abschnitt 8) |
| Einheitenmodus | `ui_einheit.py`, `einheit_service`, `einheit_modus.js`, `einheit_outbox.js`, `sw.js` | alles | Principal „Zugang“, eigener WS-Kanal, Cache-Bereinigung |
| Echtzeit | `broadcast_lage`, `ws_bus` | `ressource:changed`, `einheit:changed` | `einheit_id` im Payload, `zugang:widerrufen` |
| Fahrzeug | `VehicleMaster` | Typ, Kürzel | `funkrufname` (Stammdatum) |
| MCP | `register_tool`, `MCPContext`, `load_live_context` | Muster `wasserstelle.py` | `app/mcp/tools/gsl.py` (gemeinsam mit Schwesterplan P3-3) |

### 2.2 Abgrenzung zum Einheitenmodus-Plan (Widersprüche vermeiden)

| Thema | Schwesterplan | Dieser Plan | Regel |
|---|---|---|---|
| Migrationsnummern | 0263 (fertig), „0264“ für Phase 2 | 0265 ff. | Phase 2 des Schwesterplans nimmt die nächste freie Nummer; keine festen Nummern mehr in Plänen |
| Kommunikationsmodell (`CommLogEntry.einheit_id`, `art`, `kritisch`, `quittiert_at`) | P2-1 | Phase 5 liest/zeigt nur | **Kein zweiter Weg.** Material-/Personal-/Ablöseanforderungen sind Kategorien von `funkjournal_service.anforderung_erstellen` (P2-1); P2-1 ergänzt die Kategorien `unterstuetzung`, `material`, `personal`, `abloesung`. Phase 5 ist von P2-1 abhängig (15) |
| Push (FCM) an Fahrzeug-Tablets | P2-4 | GK hat kein FCM | GK wird per WS-Kanal und Polling informiert, nicht per Push |
| Offline-Outbox | P1-4c fertig, P3-1 (Konflikt-UI, Android-Worker) | nutzt dieselbe Outbox | Neu nur: Principal-getrennte DB, terminale 401 (9.5). Android-Worker (P3-1) betrifft den GK nicht (Browser-Zugang) |
| MCP | P3-3 `gsl.py` mit `gsl_lagen_liste`, `gsl_einheit_auftraege`, `gsl_einheit_status_setzen`, … | Ressourcen-Tools (12) | **Eine Datei, eine Namensliste** (12.2): `gsl_einheit_auftraege` entfällt zugunsten von `gsl_ressource_details`; Status-/Lagemeldungs-/Foto-Tools bleiben in P3-3 |
| Gesamtansicht (E1: Tablet darf lesen) | Allowlist `EINHEIT_GESAMTANSICHT_ROUTEN` | **gilt nicht für den GK** | GK hat keine Gesamtansicht; die Karten-Routen werden *nicht* in die Tablet-Allowlist aufgenommen (sie zeigen Telefonnummern und Zugang) |
| Admin-Simulation (E7) | `/einheit?sim=<id>` | unverändert | Simulation stellt **nie** Zugänge aus oder löst sie ein; „als Gruppenkommandant ansehen“ ist dieselbe Simulation |
| Statusmaschine, E3 (Stelle nur → in Arbeit), E6 (kritisch per Funk) | `setze_einheit_status` | unverändert | GK-Aktionen laufen mit `quelle="zugang"` durch dieselben Services; kein eigener Pfad |
| `hat_tablet()` / 📱📻 | `einheit_service.hat_tablet` | erweitert | Symbol zusätzlich 📲 „Gruppenkommandant-Zugang aktiv“, wenn ein gültiger Zugang mit Sitzung besteht |
| Terminologie | „Einheitsführer“ (Code: `leader`) | UI-Label **„Gruppenkommandant“** | Code-Namen bleiben (`LageEinheitLeader`); Labels in Karte, Journal und Einstellungen einheitlich „Gruppenkommandant“ |

---

## 3. Erweiterungen der Ressourcenübersicht

### 3.1 Was an der Übersicht ändert

1. Klick auf den Kopf einer Einheitenkarte (Label, Typ, Status – nicht auf Formularelemente) öffnet die Detailkarte. Zusätzlich Deep-Link `/lage/{id}/ressourcen?einheit=<id>` (öffnet nach dem Laden) und Verknüpfung aus Board-Chips.
2. Die Karte zeigt zusätzlich einen kleinen Zugangsindikator (📲 aktiv / ⚠ Nummer fehlt) – ohne neue Abfrage pro Karte: `kraefteuebersicht()` liefert `zugang_status_by_einheit` aus **einer** gesammelten Abfrage.
3. Inline-Aktionen (Sektor, Pool, „Abg.“, Löschen) bleiben. Das Inline-Feld „Führer…“ bleibt funktionsfähig, ruft aber `setze_gruppenkommandant` (ohne Telefon) auf und löst nur bei echter Änderung (Enter/Blur mit anderem Wert) aus, nicht bei jedem `change`.
4. `ressource:changed` erhält additiv `einheit_id`; offene Drawer laden bei passender ID ihren aktiven Tab nach (4.3).

### 3.2 Inhalte der Detailkarte – Quellen und Lücken

| Bereich / Feld | Quelle | Neu nötig |
|---|---|---|
| Name/Bezeichnung | `LageEinheit.label` | – |
| Organisation | `org_name` bzw. `FireDept` bei `is_from_org`; `VehicleMaster.org_display_name` | – |
| Ressourcentyp | `resource_type` (+ Label `RESOURCE_TYPE_LABEL`); neu `verband` (11.4) | Typ `verband` |
| Fahrzeug, Funkrufname | `vehicle` (`code`, `name`, `kennzeichen`); Funkruf: `LageEinheit.funkrufname` ← `VehicleMaster.funkrufname` | beide Felder 🆕 |
| Aktueller Status | `status` (`STATUS_LABEL`) | – |
| Zugewiesener Abschnitt | `sector` | – |
| Standort / Bereitstellungsraum | `incident_site` (wenn vor Ort) sonst `bereitstellungsraum` (Text) | `bereitstellungsraum` 🆕 |
| Zeitpunkt der Bereitstellung | `arrived_at` (gesetzt bei `bereitgestellt`), sonst `added_at` | – |
| Letzte Statusänderung | `status_at` | 🆕 (wird in `set_status`, `move_to_pool`, `dispatch_to_site` gesetzt; Backfill = Maximum der vorhandenen Zeitstempel) |
| Gruppenkommandant | aktuelle `LageEinheitLeader`-Zeile | Telefon, Rolle 🆕 |
| Stellvertreter | `LageEinheitLeader(rolle="stellvertreter", end_at IS NULL)` | 🆕 |
| Historie | alle Zeilen der Einheit, `predecessor_id`-Kette | – |
| Einsätze | `auftraege_fuer_einheit` + Ergänzungen | Rückzug: Grund, Person 🆕 |
| Journal | zusammengeführte Sicht (Abschnitt 10) | Spalten an `LageJournalEntry` 🆕 |
| Personal / Ausstattung | Abschnitt 11 | Tabellen 🆕 |
| Kommunikation | Lagemeldung: letzter `SiteLogEntry(kind=lagemeldung, einheit_id)`; Statusänderung: `status_at`; nicht quittierte Aufträge: Dispatches mit `einheit_status="zugewiesen"`; übernommen: `bestaetigt_at`; offene Anforderungen: `CommLogEntry` (nach P2-1); GK-Aktivität: `zugang.last_activity_at` | Anforderungen abhängig von P2-1 |
| Zugang | `lage_einheit_zugang`, `_session`, `_versand` | 🆕 |

### 3.3 Neue Serverbausteine (nur Lese-Aggregation + Mutationen in vorhandenen Services)

| Datei | Art | Inhalt |
|---|---|---|
| `app/services/ressource_karte_service.py` | 🆕 (reine Lese-Aggregation) | `karte(db, lage, einheit, user) -> RessourcenKarte` (DTO), `einsaetze(db, lage, einheit)`, `journal(db, lage, einheit, filter, limit, vor_id)`, `kommunikation(db, lage, einheit)`. Kein Schreibzugriff. Genutzt von Drawer **und** MCP. |
| `app/services/resource_service.py` | 🔧 | `setze_gruppenkommandant`, `setze_stellvertreter`, `entferne_gruppenkommandant`, `aktualisiere_einheit_stamm` (Funkruf, Bereitstellungsraum, Org, BOS), `status_at` pflegen, `_journal` → Ereignisfunktion (10.2), `withdraw_from_site(grund, …)` |
| `app/services/gk_zugang_service.py` | 🆕 | Token/Sitzung/Versand (Abschnitte 6–8) |
| `app/services/ressource_pflege_service.py` | 🆕 | Personal/Ausstattung/Verstärken/Teilen (Abschnitt 11) |

Router: Führungsrouten bleiben in `ui_major_incident.py` (Konvention: die Datei ist schon 5 309 Zeilen; die neuen Karten-Routen kommen **nicht** dort hinein, sondern in `app/routers/ui_ressourcenkarte.py` 🆕 mit `router = APIRouter()`, importiert/registriert in `main.py` neben `ui_einheit`; Helfer `_lage_or_404`, `_check_org_access`, `_can_edit`, `_get_mi_features` werden aus `ui_major_incident` importiert, wie es `ui_verleih.py` und `ui_lagedokument.py` bereits tun). Die öffentlichen Einlöse-Routen liegen in `app/routers/ui_gk_zugang.py` 🆕.

---

## 4. UI-/UX-Konzept

### 4.1 Desktop (Drawer rechts, ≥ 761 px)

```
┌─ Ressourcenübersicht ─────────────────────────────┬──────────────────────────────────────────┐
│ [Pool] [Im Einsatz] [Abschnitt A] [Abschnitt B]    │ RLF Wolfurt   [Im Einsatz]  Abschn. A  ✕ │
│  ┌──────────────┐ ┌──────────────┐                 │ FF Wolfurt · Fahrzeug · Funk: „Florian 3“│
│  │ RLF Wolfurt ◀┼─┤ TLF Lauterach │                 │ ┌Übersicht┬Einsätze┬Journal┬Personal┬   │
│  │ 👤 M. Huber   │ │ …            │                 │ │Ausstattung┬Zugang┐                    │
│  └──────────────┘ └──────────────┘                 │ ├──────────────────────────────────────┤
│  (30-s-Refresh ersetzt nur diesen Bereich)         │ │ Gruppenkommandant                    │
│                                                    │ │  Martin Huber (Mitglied) +43 664 …   │
│                                                    │ │  Stv.: –          [Wechseln] [Ändern]│
│                                                    │ │ Aktueller Einsatz  #12 Keller …      │
│                                                    │ │ Letzte Lagemeldung 14:32 · Status 14:10│
│                                                    │ └──────────────────────────────────────┘
```

- `<aside id="ressourceKarte">` ist ein fixer Drawer (Breite `min(520px, 42vw)`) im Hauptbereich von `ressourcen.html` **außerhalb** von `#kraefteuebersicht-container` und außerhalb des Tab-`x-data`-Blocks, analog `siteDetailModal`.
- Der Kopf (Name, Status-Chip, Abschnitt, Funkruf, Org) bleibt sichtbar; darunter Tab-Leiste mit Zählern („Einsätze 3“, „Journal 24“, Zugang-Statuspunkt).
- Jeder Tab lädt lazy per `hx-get="/lage/{lage}/einheiten/{einheit}/karte/{tab}"` in `#ressourceKarteBody`. Der Drawer-Rahmen (`GET …/karte`) enthält Kopf + Tabs + den zuletzt gewählten Tab (`localStorage`, in try/catch).
- Klick auf einen Einsatz im Tab „Einsätze“ öffnet das **vorhandene** Stellen-Detail (`#siteDetailModal`, `htmx.ajax('GET', '/lage/{lage}/stellen/{site}', { target: '#siteDetailContent', swap: 'innerHTML' })`) über dem Drawer; keine zweite Einsatzverwaltung.
- Escape und Backdrop-Klick schließen; Fokus-Falle und Rückgabe des Fokus an die Karte.

### 4.2 Mobil (≤ 760 px)

- Vollbild-Sheet statt Drawer (Muster `person-flyout` des Einheitenmodus: Vollbild unter 600 px, Trefferflächen ≥ 48 px).
- Tabs als horizontal scrollbare Chips; **sticky Aktionsleiste unten** mit den kontextabhängigen Primäraktionen (Übersicht: „GK ändern“; Zugang: „SMS senden“).
- Einsatzlisten als Karten statt Tabelle; Journal als Zeitstrahl; Formulare als untereinander gestapelte `form-control` (min-height 48 px).
- Copy-Fallback (7.4) als Vollbild-Dialog.

### 4.3 Live-Aktualisierung ohne Eingabeverlust (CLAUDE.md: kein F5)

- Alle Mutationen sind HTMX-Posts mit `hx-swap="none"` + `HX-Trigger`/after-request-Event `ressourceChanged` (bestehendes Muster der Kräfteübersicht) und liefern den betroffenen **Tab-Teil** zurück (`HX-Retarget="#ressourceKarteBody"`), kein Reload.
- Eingehendes `ressource:changed{einheit_id}` oder `einheit:changed{einheit_id}`: Drawer lädt **nur den aktiven Tab neu** und nur, wenn kein Formularfeld im Drawer den Fokus hat und kein `data-dirty` gesetzt ist; sonst Hinweis „Neue Daten – aktualisieren“ (kleiner Banner, kein Überschreiben).
- Karte hinter dem Drawer aktualisiert sich über den bestehenden Container-Refresh.
- Board: nach Statusänderungen weiterhin `site:card_changed` + `einheit:changed` (unverändert).
- Zugangsstatus (Versandergebnis, Sitzungen): nach Auto-SMS und bei GK-Aktivität `ressource:changed{einheit_id}` (Aktivität gedrosselt, höchstens alle 30 s je Einheit).

### 4.4 Tab „Übersicht“

Abschnitte (nur Anzeige + gezielte Bearbeitung):
1. **Allgemein** – Felder aus 3.2; „Bearbeiten“ öffnet Inline-Formular (`funkrufname`, `org_name`, `bos`, `bereitstellungsraum`, Menge/Einheit bei Material). Statuswechsel und Abschnitt über die bestehenden Selects.
2. **Gruppenkommandant** – Name, Mitglied-Badge oder „extern“, Telefon (anklickbar `tel:`), „seit“, Stellvertreter, Buttons *Wechseln*, *Angaben ändern*, *Entfernen*. Hinweis-Box „Keine Telefonnummer – Zugang und SMS nicht möglich. [Nummer eintragen]“. Historie als einklappbare Liste.
3. **Einsätze (Kurzfassung)** – aktueller Einsatz + Zähler weitere/abgeschlossen/zurückgezogen, Link in den Tab.
4. **Kommunikation** – letzte Lagemeldung (Zeit, relativ), letzte Statusänderung, nicht quittierte Aufträge (Anzahl, ältester seit), offene Anforderungen (nach P2-1), Zugang: Status + letzte Aktivität.

### 4.5 Formular „Gruppenkommandant“ (Flyout im Drawer)

| Feld | Verhalten |
|---|---|
| Quelle | Umschalter *Mitglied* / *Extern (Freitext)* |
| Mitglied | Suchfeld über `org_members` (aktive Mitglieder; `ist_gruppenkommandant` zuerst, Chip „GK“); Auswahl füllt Telefon aus `Member.phone` vor |
| Name (extern) | Pflicht, ≤ 120 Zeichen |
| Telefon | Eingabe frei, Anzeige der normalisierten Form (`+43 664 …`) unter dem Feld; ungültig → Feldfehler; leer erlaubt (Hinweis „Zugang nicht möglich“) |
| Art der Änderung | Radio: *Wechsel (andere Person)* – *Angaben korrigieren (gleiche Person)*. Vorbelegung per Erkennung (Mitglied/Name gleich → Korrektur). Bei *Wechsel*: Hinweis „Bisheriger Zugang wird sofort gesperrt“; bei Nummernänderung dasselbe |
| Stellvertreter | optional, eigene Zeile wie oben (kein Zugang) |
| Notiz | optional, ≤ 500 Zeichen (→ `LageEinheitLeader.note`) |
| Auto-SMS-Hinweis | Wenn Org-Schalter an: „Nach dem Speichern wird automatisch eine SMS an +43 … gesendet.“ (bei Übungslage: „…unterdrückt (Übung)“) |

Ergebnis wird sofort im Tab angezeigt (Versandstatus-Chip „SMS wird gesendet…“ → „gesendet 14:35 ✓“ oder „fehlgeschlagen: …“ [Erneut senden]).

### 4.6 Tab „Einsätze“

Vier Gruppen, jede Zeile klickbar (öffnet Stellen-Detail):
- **Aktueller Einsatz** (Karte): Einsatznummer (`Incident.lis_operation_number` → `external_key` → `S{site.id}`), Bezeichnung, Adresse, Priorität-Badge (`SITE_PRIORITY_COLOR`), Auftrag, Status-Chip (`EINHEIT_STATUS_COLOR`), Zuweisung (`dispatched_at`), Übernahme (`bestaetigt_at`), Beginn (`vor_ort_at`/Anfahrt), letzte Rückmeldung (`letzte_rueckmeldung_at`), letzte Lagemeldung.
- **Weitere Aufträge** (Liste, sortiert wie `auftraege_fuer_einheit`): Reihenfolge, Priorität, Status.
- **Abgeschlossene**: Nummer/Bezeichnung, Auftrag, Beginn (`vor_ort_at`), Ende (`beendet_at`), Dauer (`beendet_at − vor_ort_at`, sonst `− dispatched_at`), Abschlussmeldung (`beendet_grund`, sonst letzte Lagemeldung der Einheit an der Stelle), dokumentierte Maßnahmen (`SiteLogEntry kind=massnahmen, einheit_id`).
- **Zurückgezogene**: Auftrag, `withdrawn_at`, Grund, veranlassende Person (`withdrawn_by`/`withdrawn_author`).

Aktionen (nur `_can_edit`): Auftragstext/Reihenfolge ändern, Wiedereröffnen, stellvertretend Status setzen – **vorhandene** Routen (`…/einheit/{dispatch}/auftrag|wiedereroeffnen|status`) werden aus dem Tab aufgerufen; keine neue Logik.

### 4.7 Tab „Journal“

Zeitstrahl (neueste oben) mit Filterchips *Alle / Status / Einsätze / Führung / Personal / Ausstattung / Meldungen / Manuell*, optionalem Filter „Einsatz“, Paging („ältere laden“ per `vor_id`). Zeile: Zeit (`|local_datetime`), Icon + Typ, Beschreibung, Quelle (Mensch / System / Gruppenkommandant / MCP), verknüpfter Einsatz (Link). Oben ein Formular „Eintrag hinzufügen“ (Text ≤ 1 000, optional Einsatz) für `_can_edit`.

### 4.8 Tab „Zugang“ (Kommunikation und Zugangsverwaltung)

```
Gruppenkommandant: Martin Huber · +43 664 1234567
Zugang:   ● Aktiv · gültig bis Sa 14:35 · Link-ID gk-7·3
Sitzungen: 1 aktiv (Android/Chrome, zuletzt 14:52) [beenden]
Letzte Aktivität des Zugangs: 14:52
Letzter Versand: SMS 14:35 ✓ gesendet (2 SMS)   [Protokoll ▾]
[ SMS senden ]  [ Nachricht kopieren ]  [ Nur Link kopieren ]
[ Zugang verlängern ]  [ Zugang widerrufen ]
ⓘ Ein neuer Link macht den zuletzt gesendeten Link und aktive Sitzungen sofort ungültig.
```

Zustände: *Zugang deaktiviert (Org)*, *Keine Telefonnummer*, *Kein Zugang ausgegeben*, *Aktiv*, *Abgelaufen*, *Widerrufen (Grund, wann, wer)*. Bei aktiver Sitzung (letzte Aktivität < 30 min) fragen *SMS senden / Kopieren* vor der Rotation per Dialog nach („Der Gruppenkommandant ist gerade angemeldet. Neuer Link beendet seine Sitzung.“). Das Versandprotokoll listet Zeit, Kanal (SMS/kopiert), Auslöser (automatisch/manuell/MCP), Benutzer, Status, Fehlertext – nie Token oder Link.

### 4.9 Weitere Tabs

„Personal“ und „Ausstattung“ siehe Abschnitt 11 (Tabellen mit Inline-Bearbeitung, Summenkopf).

### 4.10 Barrierefreiheit und Anzeigeregeln

- Datumsausgaben nur über `|local_datetime`/`|local_time`; Datetime-Inputs über `|local` (CLAUDE.md). Services mit Zeitausgabe erhalten `org`.
- Nur gerade ASCII-Anführungszeichen in Attributen/JS (`rg '[“”„‘’]' app/templates`).
- Alle POST-Formulare mit `_csrf`; HTMX-Requests tragen den Header über `csrf.js`.
- Alle Texte Deutsch (Österreich).

---

## 5. Datenmodell und Migrationen

Konventionen: nullable bzw. Server-Defaults; `sa.text("'x'")`-Defaults, `sa.false()` für Booleans; keine SQLite-Spezifika; Backfill nur als gefiltertes Lade-und-Setzen oder `UPDATE … WHERE` mit Begründung im Migrationskommentar (CLAUDE.md: Bulk-Updates auf Tenant-Tabellen nur mit expliziter `org_id`-Bedingung). Jede Migration läuft in CI gegen MariaDB (`alembic upgrade head`); je Migration ein Test `tests/test_migration_02xx_*.py` nach dem Muster `test_migration_0263_gsl_einheitenmodus.py`. Neue Tabellen mit `org_id` sind `TenantScoped` und stehen in `_TENANT_TABLE_NAMES` (Verteidigung in der Tiefe; öffentliche Routen scopen trotzdem selbst).

### 5.1 Migration 0265 – Ressourcenkarte (Phase 1)

**`lage_einheit_leader` 🔧**

| Spalte | Typ | Zweck |
|---|---|---|
| `rolle` | `String(16) NOT NULL DEFAULT 'fuehrer'` | `fuehrer` / `stellvertreter` |
| `phone` | `String(30) NULL` | Eingabe wie erfasst (Anzeige) |
| `phone_e164` | `String(20) NULL` | normalisiert, Basis für Versand und Vergleich |
| `phone_version` | `Integer NOT NULL DEFAULT 1` | wird bei jeder Nummernänderung erhöht (Auto-SMS-Schlüssel, Bindung des Zugangs) |
| `ende_grund` | `String(24) NULL` | `wechsel`, `entfernt`, `lage_ende`, `korrektur` |
| `ende_von` | `BigInteger NULL FK user.id ON DELETE SET NULL` | wer beendet hat |
| `member_id` | (vorhanden) | Mitglied; `NULL` = extern |

Index `ix_lel_einheit_aktiv (einheit_id, rolle, end_at)`. Backfill: `rolle='fuehrer'` (Server-Default); bestehende Zeilen ohne Nummer; für Zeilen mit `member_id` wird die Nummer **nicht** rückwirkend kopiert (kein stilles Anlegen von Zugangsvoraussetzungen).

**`lage_einheit` 🔧**

| Spalte | Typ | Zweck |
|---|---|---|
| `funkrufname` | `String(40) NULL` | Lage-Funkrufname (bei Anlage aus `VehicleMaster.funkrufname` kopiert) |
| `bereitstellungsraum` | `String(200) NULL` | Standort/BR als Text |
| `status_at` | `DateTime NULL` | letzte Statusänderung; Backfill = größter Wert aus `released_at, committed_at, arrived_at, requested_at`, sonst `added_at` |

**`vehicle_master` 🔧**: `funkrufname String(40) NULL` (Stammdatum; Eingabefeld in der Fahrzeugverwaltung; kleine Änderung an Formular + Router, mit Test).

**`einheit_site_dispatch` 🔧**: `withdrawn_by BigInteger NULL FK user.id ON DELETE SET NULL`, `withdrawn_author String(120) NULL`, `withdrawn_grund String(500) NULL`.

**`lage_journal_entry` 🔧**

| Spalte | Typ | Zweck |
|---|---|---|
| `einheit_id` | `Integer NULL FK lage_einheit.id ON DELETE SET NULL`, Index | Bezug zur Ressource |
| `site_id` | `Integer NULL FK incident_site.id ON DELETE SET NULL` | optional verknüpfter Einsatz |
| `ereignis_typ` | `String(24) NULL` | siehe 10.2 (`status`, `disponiert`, `uebernommen`, `begonnen`, `abgeschlossen`, `zurueckgezogen`, `gk_zugewiesen`, `gk_gewechselt`, `personal`, `ausstattung`, `unterstuetzung`, `lagemeldung`, `abgerueckt`, `angelegt`, `manuell`, `zugang`) |
| `quelle` | `String(12) NULL` | `system`, `manuell`, `tablet`, `zugang`, `funk`, `mcp` |
| `storniert_at` | `DateTime NULL` | Storno statt Löschen |
| `storniert_von` | `String(120) NULL` | Name der stornierenden Person |
| `storno_grund` | `String(300) NULL` | |

Kategorien: neu `ressource_manuell` (manuelle Einträge). Bestehende Zeilen bleiben unverändert (`einheit_id` NULL); Altdaten erscheinen weiterhin im Lage-Ressourcenjournal und in der Einheitenkarte nur über die Dispatch-/Sitelog-Quellen (bewusste Grenze, keine textbasierte Rückzuordnung).

Konstante `RESSOURCE_CATEGORIES` wird um `ressource_manuell` erweitert; `lage_journal_delete` lehnt Einträge dieser Kategorien mit 409 („Bitte stornieren“) ab.

### 5.2 Migration 0266 – Gruppenkommandanten-Zugang (Phase 2)

**`lage_einheit_zugang` 🆕** (`TenantScoped`)

| Spalte | Typ | Zweck |
|---|---|---|
| `id` | `BigInteger PK` | |
| `org_id` | via Mixin, Index | |
| `lage_id` | `Integer FK major_incident ON DELETE CASCADE`, Index | |
| `einheit_id` | `Integer FK lage_einheit ON DELETE CASCADE`, **UNIQUE** | genau ein Zugang je Einheit |
| `leader_id` | `Integer FK lage_einheit_leader ON DELETE SET NULL` | Gruppenkommandant, an den der Zugang gebunden ist |
| `phone_e164` | `String(20)` | Nummer zum Ausstellungszeitpunkt |
| `phone_version` | `Integer` | Version des Leaders zum Ausstellungszeitpunkt |
| `token_hash` | `String(64) NULL`, UNIQUE | SHA-256 des Tokens; `NULL` = kein gültiger Token |
| `generation` | `Integer NOT NULL DEFAULT 0` | +1 bei jeder Rotation/Sperre; Sitzungen tragen sie |
| `status` | `String(12)` | `aktiv`, `widerrufen`, `abgelaufen`, `kein_token` |
| `widerruf_grund` | `String(24) NULL` | `wechsel`, `telefon`, `manuell`, `abgerueckt`, `lage_ende`, `deaktiviert`, `entfernt`, `rotation` |
| `widerrufen_at`, `widerrufen_von` | `DateTime`, `BigInteger FK user` | |
| `ausgestellt_at`, `ausgestellt_von` | `DateTime`, `BigInteger FK user NULL` | `NULL` = automatisch |
| `laeuft_ab_at` | `DateTime NULL` | Ablauf des Tokens (und aller Sitzungen) |
| `einloesungen` | `Integer DEFAULT 0` | Zähler |
| `erste_einloesung_at`, `letzte_aktivitaet_at` | `DateTime NULL` | Aktivität gedrosselt (1/min) |
| `pin_pflicht` | `Boolean DEFAULT false` | Snapshot des Org-Schalters bei Ausstellung |
| `pin_hash`, `pin_gueltig_bis`, `pin_versuche`, `pin_gesperrt_bis` | `String(64)`, `DateTime`, `Integer`, `DateTime` | SMS-PIN |
| `created_at`, `updated_at` | `DateTime` | |

**`lage_einheit_zugang_session` 🆕** (`TenantScoped`): `id`, `zugang_id FK ON DELETE CASCADE` (Index), `generation`, `session_hash String(64) UNIQUE`, `created_at`, `last_seen_at`, `laeuft_ab_at`, `revoked_at NULL`, `revoke_grund NULL`, `client_kurz String(120)` (gekürztes User-Agent), `ip_gruppe String(45)` (IP gekürzt auf /24 bzw. /56, kein Volltext), `verifiziert_at NULL`.

**`lage_einheit_zugang_versand` 🆕** (`TenantScoped`): `id`, `zugang_id FK ON DELETE CASCADE` (Index), `einheit_id`, `leader_id`, `generation`, `kanal` (`sms`, `kopie_nachricht`, `kopie_link`, `pin_sms`), `ausloeser` (`auto`, `manuell`, `mcp`), `user_id NULL`, `status` (`geplant`, `gesendet`, `fehlgeschlagen`, `unklar`, `uebersprungen`, `verworfen`), `fehler String(300) NULL` (Klartextgrund ohne Token), `ziel_maske String(30)` (`+43 664 ***4567`), `sms_log_id NULL FK sms_log ON DELETE SET NULL`, `zeichen Integer`, `segmente Integer`, `auto_schluessel String(64) NULL UNIQUE` (`"<leader_id>:<phone_version>"` – verhindert doppelten Auto-Versand), `created_at`, `abgeschlossen_at`.

**`einheit_aktion` 🔧**: `zugang_id BigInteger NULL FK lage_einheit_zugang ON DELETE SET NULL`; `quelle` kennt jetzt `zugang`.

Hinweis: `SmsLog.source` erhält den neuen Wert `gk_zugang` (Freitextspalte, keine Migration nötig), `SmsLog.text` wird mit geschwärztem Link gespeichert (7.5).

### 5.3 Migration 0267 – GSL-Organisationseinstellungen (Phase 3)

`org_settings` 🔧 (alle mit Server-Default, bestehende Orgs unverändert):

| Spalte | Typ | Default |
|---|---|---|
| `gk_zugang_aktiv` | Boolean | `false` |
| `gk_zugang_auto_sms` | Boolean | `false` |
| `gk_zugang_nachricht` | Text NULL | `NULL` (= Standardtext) |
| `gk_zugang_gueltigkeit_stunden` | Integer | `48` (zulässig 1–168) |
| `gk_sitzung_stunden` | Integer | `12` (zulässig 1–72, nie länger als der Token) |
| `gk_zugang_max_sitzungen` | Integer | `2` (zulässig 1–5) |
| `gk_zugang_sms_pin` | Boolean | `false` |
| `gk_zugang_ressource_pflegen` | Boolean | `false` |

### 5.4 Migration 0268 – Personal, Ausstattung, Verbände (Phase 4)

**`lage_einheit` 🔧**: `staerke_gesamt`, `staerke_fuehrung`, `staerke_agt`, `staerke_sanitaeter` (`Integer NULL`), `personal_modus String(8) NOT NULL DEFAULT 'summe'` (`summe` = Zahlen manuell, `liste` = aus Personenzeilen berechnet), `personal_bemerkung Text NULL`, `verband_id Integer NULL FK lage_einheit.id ON DELETE SET NULL`, `aufgeteilt_von_id Integer NULL FK lage_einheit.id ON DELETE SET NULL`. `resource_type` erhält den Wert `verband` (Spalte ist `String(12)`, kein Enum).

**`lage_einheit_person` 🆕** (`TenantScoped`): `id`, `lage_id`, `einheit_id FK CASCADE`, `member_id NULL FK member ON DELETE SET NULL`, `name String(120)` (Snapshot), `funktion String(16)` (`fuehrung`, `agt`, `sanitaeter`, `maschinist`, `mannschaft`, `sonstige`), `qualifikationen String(200) NULL` (CSV von `Qualification.code`), `von_at`, `bis_at NULL`, `herkunft String(14)` (`stamm`, `frei`, `verstaerkung`, `umbuchung`), `abloesung_von_id NULL FK self`, `umbuchung_id String(36) NULL`, `bemerkung String(300) NULL`, **`aktiv_key BigInteger NULL`** (= `member_id` solange `bis_at IS NULL`, sonst `NULL`), `UNIQUE(lage_id, aktiv_key)` – ein Mitglied kann in einer Lage nur bei einer Einheit aktiv sein (MariaDB und SQLite erlauben mehrere `NULL`), `created_by`, `created_at`. Externe Personen ohne `member_id` bekommen `aktiv_key = NULL` (Namensabgleich nur als Warnung).

**`lage_einheit_ausstattung` 🆕** (`TenantScoped`): `id`, `lage_id`, `einheit_id FK CASCADE`, `kategorie String(24)` (Katalog 11.3), `bezeichnung String(120)`, `ist_faehigkeit Boolean`, `menge Integer DEFAULT 1`, `status String(16)` (`einsatzbereit`, `eingeschraenkt`, `defekt`, `nicht_verfuegbar`), `bemerkung String(300) NULL`, `stamm_ref_typ String(24) NULL` (`atemschutz_geraet`, `verleih_artikel`, `vorlage_lage`), `stamm_ref_id BigInteger NULL`, `created_by`, `created_at`, `updated_at`. Löschen ist hart zulässig (Journal hält die Änderung fest).

### 5.5 Telefon-Helfer

`app/core/telefon.py`: `telefon_zu_e164_at(wert) -> str | None` = `telefon_e164(telefon_identitaet_at(wert))`; `telefon_anzeige(e164) -> str` (`+43 664 1234567`); `telefon_maske(e164) -> str` (`+43 664 ***4567`). Tests: `0664 123 45 67`, `+43 664 …`, `0043 …`, Ausland `+49 …`, Festnetz (Warnung `ist_oesterreichische_mobilnummer`), Müll → `None`.

---

## 6. Token- und Berechtigungskonzept

### 6.1 Zugang, Token und Sitzung

| Objekt | Inhalt | Lebensdauer |
|---|---|---|
| Token | `gkz_` + `secrets.token_urlsafe(24)` (192 Bit), nur im Link, nur als SHA-256 in `lage_einheit_zugang.token_hash` | bis `laeuft_ab_at` (Standard 48 h), bis Rotation/Widerruf |
| Sitzung | Cookie `ec_gk` = `secrets.token_urlsafe(32)`; DB nur SHA-256; `HttpOnly; Secure; SameSite=Lax; Path=/` | `min(laeuft_ab_at, jetzt + gk_sitzung_stunden)`; Cookie `Max-Age` entsprechend |
| Generation | Zähler am Zugang, Kopie in jeder Sitzung | jede Rotation/Sperre erhöht ihn |

`Path=/` ist nötig, weil Cookie, `/einheit`, `/ws/einheit-zugang` und `/gk` verwendet werden. `SameSite=Lax` genügt, weil der Link aus SMS/Chat als Top-Level-Navigation geöffnet wird; CSRF-Schutz bleibt über `ec_csrf` bestehen. Alle Antworten der `/gk*`-Routen: `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow`.

### 6.2 Bindung (Token ↔ Org ↔ Lage ↔ Einheit ↔ Gruppenkommandant)

Der Token ist an eine Zeile gebunden, die `org_id`, `lage_id`, `einheit_id` und `leader_id` + `phone_version` festschreibt. Der Token enthält selbst nichts; es gibt keine ID-Parameter in der URL, die eine Berechtigung ändern könnten. Die Einheit eines Requests ergibt sich ausschließlich aus der Sitzung.

### 6.3 Prüfung bei **jeder** Anfrage (`gk_zugang_service.sitzung_pruefen`)

Eine Abfrage (Join Sitzung ⨝ Zugang ⨝ Einheit ⨝ Lage ⨝ Leader ⨝ OrgSettings), danach alle Bedingungen:

1. Sitzung existiert (Hash), `revoked_at IS NULL`, `laeuft_ab_at > jetzt`.
2. `session.generation == zugang.generation` und `zugang.status == "aktiv"` und `zugang.token_hash IS NOT NULL`.
3. `zugang.laeuft_ab_at > jetzt`.
4. `lage.status == active` und `lage.id == zugang.lage_id`.
5. `einheit.status IN (bereitgestellt, im_einsatz)` (wie Geräte-Kontext).
6. `einheit.leader_assignment_id == zugang.leader_id` und `leader.end_at IS NULL` (Wechsel/Entfernen wirkt auch ohne Widerrufsjob).
7. `leader.phone_e164 == zugang.phone_e164` und `leader.phone_version == zugang.phone_version` (Nummernänderung wirkt auch ohne Widerrufsjob).
8. `OrgSettings.gk_zugang_aktiv` ist wahr; `lage.org_id == zugang.org_id`.
9. Wenn `pin_pflicht`: `session.verifiziert_at IS NOT NULL`.

Fehler → `401` mit Code `zugang_widerrufen | zugang_abgelaufen | zugang_ungueltig` (JSON auf `/einheit/api/*`, sonst Hinweisseite). Bei 2/6/7/8 wird die Sitzung zusätzlich mit `revoked_at` markiert (Aufräumen).

Die Redundanz ist gewollt: Die explizite Sperre (6.5) und die Prüfung der Bindungen (6) sichern sich gegenseitig ab, etwa wenn ein Pfad die Sperre vergisst.

### 6.4 Ausstellen und Rotieren (atomar)

`gk_zugang_service.stelle_zugang_aus(db, lage, einheit, *, user_id, grund) -> NeuerZugang`:

1. Voraussetzungen: Org-Schalter an, aktueller Leader mit gültiger `phone_e164`, Lage `active`, Einheit nicht abgerückt, keine Simulation.
2. Zeile `lage_einheit_zugang` per `SELECT … FOR UPDATE` (SQLite: Transaktion genügt) laden; fehlt sie, einfügen (`UNIQUE(einheit_id)` fängt Rennen ab: bei `IntegrityError` neu laden und weitermachen).
3. Token erzeugen, `token_hash` ersetzen, `generation += 1`, `status="aktiv"`, `laeuft_ab_at`, `leader_id`, `phone_*`, `pin_pflicht` neu setzen, PIN-Felder leeren, `widerruf_*` leeren.
4. Bestehende Sitzungen laden und mit `revoked_at`/`revoke_grund="rotation"` markieren (ORM-Mutation je Zeile, kein Bulk).
5. Audit `gsl.zugang.ausgestellt` (Payload: `lage_id`, `einheit_id`, `generation`, `grund` – **kein** Token), Ressourcenjournal-Ereignis `zugang`.
6. Rückgabe `NeuerZugang(link, generation, laeuft_ab_at)`; die `__repr__` maskiert den Link. Erst **nach** `commit()` darf der Aufrufer den Link weiterverwenden.

Zwei gleichzeitige Rotationen serialisiert die Zeilensperre; die zweite Anfrage sieht bei `bestehender_link` (7.3) eine andere Generation und rotiert erneut oder bricht mit `409 zugang_geaendert` ab (UI-Hinweis „Zugang wurde soeben von <Name> erneuert“).

### 6.5 Sperren (zentral: `gk_zugang_service.widerrufe(...)`)

| Auslöser | Ort der Verdrahtung | `grund` |
|---|---|---|
| Gruppenkommandantenwechsel | `setze_gruppenkommandant` (gleiche Transaktion) | `wechsel` |
| Telefonnummer geändert | `setze_gruppenkommandant` (Korrektur mit neuer Nummer) | `telefon` |
| Gruppenkommandant entfernt | `entferne_gruppenkommandant` | `entfernt` |
| Einheit abgerückt / aus der Lage entfernt | `resource_service.set_status(abgerueckt)`, `lage_einheit_delete` (Kaskade löscht Zugang), `move_to_pool` **nicht** (Einheit bleibt bereitgestellt) | `abgerueckt` |
| Lage geschlossen | `major_incident_service.close_lage` ruft `widerrufe_alle_fuer_lage` | `lage_ende` |
| Manuell | Button „Zugang widerrufen“ | `manuell` |
| Org-Schalter aus | Prüfung 8 (live) + Hinweis in den Einstellungen | `deaktiviert` |
| Notbremse | Button in den GSL-Einstellungen „Alle GK-Zugänge dieser Organisation widerrufen“ | `manuell` |

`widerrufe()`: Zeile sperren, `token_hash = NULL`, `status="widerrufen"`, `generation += 1`, alle Sitzungen mit `revoked_at`, Audit `gsl.zugang.widerrufen`, Journal-Ereignis, nach Commit `broadcast_einheit_zugang(zugang_id, {"type": "zugang:widerrufen"})` – der WS-Server schließt die Verbindung. Wiedereröffnung der Lage (`status = active`) **reaktiviert nichts**; es braucht einen neuen Link.

### 6.6 Einlösen

| Schritt | Route | Verhalten |
|---|---|---|
| 1 | `GET /gk` | statische Seite (Template `gk/einloesen.html`), kein DB-Zugriff, JS liest `location.hash`, ruft `history.replaceState` (entfernt das Fragment aus der Adresszeile/History-Eintrag) und hält den Token nur im Speicher |
| 2 | `POST /gk/pruefen` `{token}` | liefert nur Anzeigedaten: Lage-Name, Einheit, ob PIN nötig; **keine** Änderung. Rate-Limit `10/15minutes` je IP. Antworten für unbekannt/abgelaufen/ersetzt/widerrufen: unbekannt → generisch „Link ungültig“; bekannt aber beendet → „Der Zugang wurde beendet oder ersetzt. Bitte neuen Link bei der Einsatzleitung anfordern“ |
| 3 (optional) | `POST /gk/pin` `{token}` | sendet die SMS-PIN (6 Stellen, SHA-256, 10 min gültig, max. 3 Anforderungen/10 min, 5 Fehlversuche → Sperre 15 min, Journal-Hinweis für die Führung); die PIN-SMS wird nur versendet, wenn der Token gültig ist |
| 4 | `POST /gk/einloesen` `{token, pin?}` | Hash-Lookup, alle Bedingungen aus 6.3 (soweit sie nicht sitzungsbezogen sind), PIN prüfen, Sitzung anlegen (Limit `gk_zugang_max_sitzungen`: älteste Sitzung wird beendet), Cookie setzen, `einloesungen += 1`, Audit `gsl.zugang.eingeloest` (Payload ohne Token; `client_kurz`, gekürzte IP), Antwort `{redirect: "/einheit"}` |
| 5 | `POST /gk/abmelden` | beendet nur die eigene Sitzung (Cookie löschen, `revoked_at`) |

Alle `/gk*`-POSTs unterliegen dem CSRF-Double-Submit (die statische Seite erhält das Cookie `ec_csrf` beim `GET`). Fehlermeldungen enthalten nie den Token; Logger bekommen nie den Request-Body; `repr`/Exception-Texte der Services dürfen den Token nicht enthalten (Test, 14).

### 6.7 Berechtigungsmatrix

**Gruppenkommandant (Zugang)** – nur über `/einheit/api/*` und nur für die eigene Einheit:

| Erlaubt | Umsetzung |
|---|---|
| Eigene Einheit, zugewiesene Einsätze und Historie ansehen | `GET /einheit/api/zustand`, `/auftrag/{id}`, `/karte` (nur eigene Stellen), `/medien/{id}` (nur Medien eigener Stellen) |
| Aufträge übernehmen/quittieren, Status aktualisieren | `POST …/status` (`bestaetigt`, `anfahrt`, `vor_ort`, `in_arbeit`, `abgeschlossen`, `nicht_durchfuehrbar`) – dieselbe Statusmaschine wie Tablet |
| Lagemeldungen, Maßnahmen, Notizen | `POST …/meldung` |
| Fotos/Dokumentation zu eigenen Aufträgen | `POST …/foto` |
| Unterstützung/Material/Personal/Ablösung anfordern | `POST …/anforderung` (nach P2-1) |
| Eigene Ressourceninformationen (nur wenn `gk_zugang_ressource_pflegen`) | `POST /einheit/api/ressource/personal` (Zahlen, Bemerkung) und `…/ausstattung/{id}` (Menge/Status) – **nicht**: Telefonnummer, Gruppenkommandant, Abschnitt, Einheitenstatus, Typ, Löschen |

| Nicht erlaubt | Durchsetzung |
|---|---|
| Andere Einheiten | Kontext enthält genau eine `einheit_id`; Aufträge werden nur über `EinheitSiteDispatch.einheit_id == ctx.einheit.id` und `site.major_incident_id == ctx.lage.id` geladen, fremde IDs → 404 |
| Ressourcen verwalten, disponieren, Reihenfolge ändern | es gibt keine Route dafür im Einheitenmodus |
| Aufträge anderen zuweisen, Lage verwalten, Phase/Priorität, Stelle abschließen | keine Route; Phasen laufen nur über E3-Anhebung in `setze_einheit_status` (Abschluss/Abbruch der **Stelle** bleibt Führungsentscheidung; „Auftrag abgeschlossen“ ist die Meldung der Einheit) |
| Führungsroutenzugriff | Principal ist kein `User` → `require_role` schlägt fehl; Test über alle App-Routen (14) |
| MCP | kein Bearer, kein `User` (12.3) |
| `/ws/lage/{id}` | `_resolve_user` kennt `ec_gk` nicht → 4401; eigener Kanal (9.4) |

**Führung (Ausgabe und Verwaltung des Zugangs):**

| Aktion | Rollen |
|---|---|
| Karte ansehen (Übersicht, Einsätze, Journal, Personal, Ausstattung) | alle GSL-Rollen inkl. `readonly`; **nicht** `gsl_profil="einheit"`-Tablets (nicht in der Allowlist) |
| Telefonnummer des Gruppenkommandanten sehen, Tab „Zugang“ | `incident_leader`, `admin`, `recorder` (`readonly`: Nummer maskiert, kein Zugang-Tab) |
| Gruppenkommandant, Personal, Ausstattung, Journal pflegen | `_can_edit` (`incident_leader`, `admin`, `recorder`) |
| SMS senden, Link/Nachricht kopieren, widerrufen, verlängern | `_can_edit`, **zusätzlich**: echte Personen-Session – nicht `is_device`, nicht `qr_lage_id`/`qr_incident_id` (QR-Sitzungen laufen mit Rolle `recorder` und dürfen keine Zugänge ausstellen), nicht Simulation |
| GSL-Einstellungen, Notbremse | `admin` (wie `gsl_einstellungen`) |

Hilfsfunktion `_darf_zugang_verwalten(request, user)` in `ui_ressourcenkarte.py`; die Zugangs-Routen verwenden sie zusätzlich zu `require_role`.

---

## 7. SMS-Versand und Copy & Paste

### 7.1 Nachrichtenvorlage

Eine Vorlage pro Org (`OrgSettings.gk_zugang_nachricht`), eine Renderfunktion `gk_zugang_service.nachricht_rendern(org_settings, lage, einheit, leader, link)` für SMS **und** Zwischenablage.

Platzhalter: `{lage}`, `{einheit}`, `{gruppenkommandant}`, `{link}`. Bei Übungslagen setzt der Renderer `[ÜBUNG] ` voran (Vorbild `dispatch_gsl_alarm`). Beim Speichern in den Einstellungen: unbekannte Platzhalter ablehnen, `{link}` ist Pflicht, maximal 480 Zeichen. Eingesetzte Werte werden gekürzt (`{lage}` ≤ 40, `{einheit}` ≤ 30, `{gruppenkommandant}` ≤ 40) und von Steuerzeichen/Zeilenumbrüchen befreit, damit keine fremden Inhalte oder Links über Namen in die SMS kommen.

Standardtext (nur GSM-7-Zeichen, ≈ 235 Zeichen inkl. Link → 2 SMS):

```
GSL {lage}
Einheit: {einheit}
Du bist als Gruppenkommandant zugewiesen. Einsätze, Status, Lagemeldungen und Fotos:
{link}
Gilt nur für deine Einheit in dieser Lage. Bitte nicht weitergeben.
```

(Der ausführliche Beispieltext der Anforderung kann in den Einstellungen als Vorlage hinterlegt werden; die Länge zeigt der Zähler.)

`gk_zugang_service.sms_laenge(text) -> (zeichen, kodierung, segmente)`: GSM-7 (160/153, mit Erweiterungszeichen `€ [ ] { } \ ^ ~ | ` doppelt), sonst UCS-2 (70/67). Im Formular zeigt Alpine live „212 Zeichen · 2 SMS · GSM-7“ und warnt bei UCS-2 („Sonderzeichen wie – oder „ “ erhöhen die SMS-Anzahl“) und bei > 3 Segmenten. Der Link wird mit seiner echten Länge (`len(PUBLIC_BASE_URL) + len("/gk#") + 36`) gerechnet.

### 7.2 Zugangslink erzeugen

Link = `settings.effective_public_base_url.rstrip("/") + "/gk#" + token`. Beide Aktionen („SMS senden“, „Kopieren“) rufen `stelle_zugang_aus` auf, **es sei denn** der Client übergibt einen noch frischen Link (7.3).

### 7.3 Ablauf „SMS senden“ (manuell)

`POST /lage/{lage}/einheiten/{einheit}/zugang/senden` (`kanal=sms`, optional `bestehender_link`)

1. Berechtigung (6.7), Org-Schalter, Lage aktiv.
2. Nummer prüfen (`phone_e164`); fehlt/ungültig → `422` mit Hinweis, **keine** Rotation.
3. `sms_available(org_id)`; sonst Versand-Zeile `uebersprungen` ("Kein SMS-Anbieter verbunden"), **keine** Rotation (der alte Zugang bleibt gültig) – Kopieren bleibt möglich.
4. Übungslage: `darf_extern("sms", …)` falsch → `uebersprungen` ("Übung: SMS unterdrückt"), keine Rotation.
5. Aktiver Sitzungs-Dialog (4.8) wurde bestätigt (`bestaetigt=1`), sonst `409 sitzung_aktiv` mit Daten für den Dialog.
6. Wenn `bestehender_link` übergeben: Token extrahieren, SHA-256 gegen `token_hash` und `generation` prüfen; passt er, **keine Rotation** (der Link wurde eben erst kopiert). Sonst `stelle_zugang_aus(grund="rotation")`.
7. `commit()` (Zugang gültig, Versandzeile `geplant`).
8. Text rendern, `send_sms(org_id, phone_e164, text, timeout=15)` direkt aufrufen.
9. Ergebnis in Versandzeile (`gesendet` / `fehlgeschlagen` + Fehlertext aus dem Provider / `unklar` bei Timeout nach Übergabe), `SmsLog` mit **geschwärztem** Text + `SmsLogRecipient` (Name = Gruppenkommandant), `commit()`.
10. `ressource:changed{einheit_id}` broadcasten; Antwort rendert den Tab „Zugang“ neu (Ergebnis sofort sichtbar).

Ein Fehler in 8/9 ändert **nichts** an der Gruppenkommandanten-Zuweisung (die ist längst committed); der Zugang bleibt gültig, die Karte zeigt „SMS fehlgeschlagen – Erneut senden / Nachricht kopieren“. „Erneut senden“ rotiert erneut (Klartext existiert nicht mehr), mit denselben Prüfungen.

### 7.4 Ablauf „Nachricht kopieren“ / „Nur Link kopieren“

- `POST …/zugang/link` (JSON-Antwort, `Cache-Control: no-store`) → `{link, text, laeuft_ab_at, generation}`. Rotiert nach denselben Regeln (inkl. Sitzungs-Dialog); der Klartext steht nur in dieser Antwort.
- Der Client (Alpine-Store `ressourceKarte`) hält Link und Text **nur im Speicher** (nie in DOM-Attributen, `localStorage`, HTML), maximal 5 Minuten oder bis der Drawer schließt. Während dieser Zeit verwenden beide Kopier-Buttons und „SMS senden“ denselben Link – **keine Mehrfachrotation durch zweimaliges Klicken** (`bestehender_link`, 7.3 Schritt 6).
- Kopieren (`copyText(text)` in `app/static/js/ressourcen_karte.js` 🆕):
  1. `navigator.clipboard.write([new ClipboardItem({"text/plain": promise})])` mit Promise (in der Nutzer-Geste gestartet; funktioniert in Safari/iOS trotz asynchronem Abruf),
  2. sonst `navigator.clipboard.writeText`,
  3. sonst Fallback-Dialog: schreibgeschütztes `<textarea>` mit vorselektiertem Inhalt, Hinweis „Zum Kopieren lange drücken / Strg+C“, Inhalt wird beim Schließen geleert.
- Bestätigung: Toast „Nachricht kopiert · gültig bis 14:35 · ersetzt den vorherigen Link“.
- Versandzeile: `kanal=kopie_nachricht|kopie_link`, `status=gesendet`, `ausloeser=manuell` (ohne Inhalt) – so ist nachvollziehbar, wann und von wem ein Link ausgegeben wurde.
- Funktioniert auf Desktop und Mobil (HTTPS ist Secure Context).

### 7.5 Protokollierung ohne Klartext

- `SmsLog.text`: Link ersetzt durch `{base}/gk#••••••••`; PIN-SMS: Code ersetzt durch `••••••`.
- Kein Token in Audit-Payloads, Log-Records, Exceptions, `repr`, HTMX-Antworten außer der JSON-Antwort aus 7.4, Fehlerseiten, Testausgaben.
- Request-Body-Logging: In der App existiert keines; ein Test (`caplog`) stellt sicher, dass beim Einlösen und beim Senden kein Logeintrag den Token enthält.
- Nicht kontrollierbar: das Protokoll des SMS-Gateway-Geräts bzw. des EUS-Anbieters sowie Chatverläufe. Diese Restrisiken tragen Kürze der Gültigkeit, Widerruf, Einmal-pro-Einheit, Sitzungslimit und die optionale SMS-PIN (13.2).

### 7.6 Zustände und Anzeige

Versandstatus (Chip): `geplant` (spinner), `gesendet`, `fehlgeschlagen` (mit Grund), `unklar` ("Ergebnis unbekannt – bitte beim Gruppenkommandanten nachfragen"), `uebersprungen` (Grund), `verworfen` (Gruppenkommandant/Nummer inzwischen geändert). Zugangsstatus (Punkt): grau *kein Zugang*, grün *aktiv*, gelb *läuft in < 2 h ab*, rot *abgelaufen/widerrufen*.

---

## 8. Automatische SMS und GSL-Konfiguration

### 8.1 Einstellungen (bestehende Seite, keine neue)

`admin/gsl_einstellungen.html` bekommt eine Karte **„Gruppenkommandanten-Zugang“**; `gsl_einstellungen_save` liest die neuen Felder; `OrgSettings` bekommt die Spalten aus 5.3.

| Einstellung | Typ | Standard | Hinweis |
|---|---|---|---|
| Gruppenkommandanten-Zugang aktivieren | Schalter | aus | Gesamtschalter (live geprüft, 6.3 Nr. 8) |
| **SMS bei Gruppenkommandanten-Zuweisung automatisch versenden** | Schalter | **aus** | Geltungsbereich Organisation; nur wirksam, wenn Gesamtschalter an |
| Nachrichtenvorlage | Textarea + Platzhalterliste + Live-Zähler | Standardtext | 7.1; Button „Standard wiederherstellen“; Vorschau mit Beispielwerten |
| Gültigkeit des Zugangs | Stunden | 48 | 1–168; „Zugang verlängern“ (ohne Rotation) setzt `laeuft_ab_at` neu |
| Sitzungsdauer | Stunden | 12 | ≤ Gültigkeit |
| Maximale gleichzeitige Sitzungen | Zahl | 2 | z. B. Smartphone + Tablet |
| SMS-Bestätigungscode beim Öffnen | Schalter | aus | D6; Hinweis: empfohlen, wenn Links in Chatgruppen geteilt werden; ohne SMS-Anbieter nicht erfüllbar → beim Speichern Warnung |
| Gruppenkommandant darf Personal/Ausstattung pflegen | Schalter | aus | 6.7 |
| Notbremse: alle Zugänge widerrufen | Button + Bestätigung | – | `admin` |

Bewertung der „weiteren Konfigurationsmöglichkeiten“: Vorlage, Gültigkeit, SMS-Verifizierung und Gesamtschalter sind sinnvoll und enthalten; **Zustellprotokoll ist nicht abschaltbar** (immer aktiv, Aufbewahrung folgt der SMS-Log-Retention der Org, `log_retention_days`); weitere Schalter werden bewusst nicht eingeführt.

Unterschied zu bestehenden GSL-SMS-Einstellungen: Der Übungsschutz verwendet das vorhandene Flag `einsatzinfo_sms_send_exercise`; kein zweites Flag.

### 8.2 Ablauf bei Zuweisung/Wechsel (`resource_service.setze_gruppenkommandant`)

Signatur (Auszug): `setze_gruppenkommandant(db, lage, einheit, *, member_id, person_name, telefon, modus, note, user_id, author_name, org_settings, quelle) -> GkErgebnis`. `GkErgebnis` enthält `aenderung` (`keine | korrektur | wechsel | neu | telefon`), `leader`, `zugang_gesperrt: bool`, `auto_sms: AutoSmsAuftrag | None`.

1. Eingaben normalisieren: Name trimmen, Mitglied laden (nur gleiche Org, aktiv), Telefon aus Eingabe, sonst aus `Member.phone`; `phone_e164 = telefon_zu_e164_at(...)`. Ungültige Eingabe → `ValueError` (Feldfehler), Zuweisung ohne Nummer bleibt erlaubt.
2. **Änderungserkennung** gegen die aktuelle Führer-Zeile: gleiche Person (`member_id` gleich bzw. bei extern `casefold(name)` gleich) **und** gleiche `phone_e164` → `aenderung="keine"`: nichts schreiben, keine Rotation, kein Journal, kein SMS.
3. Gleiche Person, andere Nummer (oder `modus="korrektur"`): Zeile aktualisieren, bei Nummernänderung `phone_version += 1`, Journal `gk_gewechselt`-nahe Zeile „Telefonnummer geändert“ (nur maskierte Nummern), **Zugang sperren** (`widerrufe(grund="telefon")`).
4. Andere Person: bisherige Zeile `end_at=now`, `ende_grund="wechsel"`, neue Zeile (`predecessor_id`), `leader_assignment_id` und `commander_label` setzen, Journal „Gruppenkommandant gewechselt: A → B“ bzw. „zugewiesen“ (`gk_zugewiesen`/`gk_gewechselt`), **Zugang sperren** (`widerrufe(grund="wechsel")`).
5. Nur wenn `gk_zugang_aktiv` **und** `gk_zugang_auto_sms` **und** gültige Nummer **und** `aenderung in (neu, wechsel, telefon)`:
   - `stelle_zugang_aus(...)` (Token entsteht im Speicher),
   - Versandzeile `geplant` mit `auto_schluessel="<leader_id>:<phone_version>"` einfügen; `IntegrityError` (Savepoint) → bereits versendet → überspringen,
   - `AutoSmsAuftrag(zugang_id, versand_id, link, leader_id, phone_version)` zurückgeben.
6. Ohne Nummer: Versandzeile `uebersprungen` ("Keine Telefonnummer"), Hinweis in der Karte; ohne Auto-Schalter: **kein** Token (wird bei Bedarf lazy erzeugt, 7.3).
7. Router: `commit()`; **erst danach** `BackgroundTasks.add_task(gk_zugang_service.sende_auto_sms, auftrag)`; `broadcast_lage(ressource:changed{einheit_id})`; Antwort rendert den Tab sofort (Versandchip „SMS wird gesendet…“).

`sende_auto_sms` (eigene DB-Session, Tenant-Kontext None, explizite `org_id`-Filter):
1. Versandzeile laden; Zustand prüfen: Leader noch aktuell und `phone_version` unverändert, Zugang noch aktiv, Lage aktiv, Org-Schalter noch an; sonst `verworfen`.
2. Übungsschutz/`sms_available` wie 7.3 (→ `uebersprungen`).
3. `send_sms` → Ergebnis in Versandzeile + `SmsLog` (geschwärzt) + Audit `gsl.zugang.sms_auto`.
4. `broadcast_lage(ressource:changed{einheit_id})` → Karte zeigt das Ergebnis ohne Reload.
5. Kein automatischer Wiederholungsversuch (Wiederholung würde rotieren und einen eventuell inzwischen kopierten Link entwerten); Fehler bleibt sichtbar, **„Erneut senden“** manuell.

Aufräumen: Beim Start und alle 60 s markiert eine kleine Schleife (Muster `gsl_lagemeldung_reminder`/`sms_dispatch_service.einsatzinfo_nachversand_loop`, im bestehenden Lifespan-Hook) Versandzeilen `geplant` älter als 2 min als `fehlgeschlagen` ("Versand abgebrochen – bitte erneut senden"); verhindert ewige Spinner nach Neustart.

### 8.3 Sonderfälle

| Fall | Verhalten |
|---|---|
| Keine Telefonnummer | kein Versand, Hinweis „Nummer eintragen“ in Übersicht/Zugang; nachtragen = Korrektur mit `phone_version += 1`; bei Auto-Schalter an löst das Nachtragen den Auto-Versand aus (erstmalige Nummer = `aenderung="telefon"`) |
| Telefonnummer geändert | Zugang gesperrt (Sitzungen sofort tot), bei Auto an neue SMS |
| Gruppenkommandant gewechselt | alter Zugang gesperrt, neuer Leader, bei Auto an SMS |
| Gleicher GK, gleiche Nummer | `aenderung="keine"`, kein Token, kein SMS |
| SMS fehlgeschlagen | Chip „fehlgeschlagen: <Grund>“, Buttons *Erneut senden* / *Nachricht kopieren*; Zuweisung bleibt; kein zweiter Leader |
| Lage geschlossen | alle Zugänge der Lage widerrufen (`close_lage`) |
| Übungslage | Auto-/Manuell-SMS nur mit `einsatzinfo_sms_send_exercise`; sonst Hinweis, Kopieren funktioniert |
| Einheit ohne `vehicle_id` (extern/Material) | Zugang möglich (Zugang hängt an `LageEinheit`, nicht am Fahrzeug) |

---

## 9. Einbindung des Einheitenmodus und Offline

### 9.1 Kontextauflösung

- `Quelle` erhält `"zugang"`; `EinheitKontext` bekommt `zugang: LageEinheitZugang | None`, `leader: LageEinheitLeader | None`; `ctx.akteur_name` = `"<Gruppenkommandant> (GK <Einheit>)"` (für `author_name` in Chronik/Journal), `ctx.principal_key` = `"d:<device_token_id>" | "z:<zugang_id>" | "s:<user_id>"`.
- `einheit_service.kontext_fuer_zugang(db, sitzung) -> EinheitKontext | None` ruft `gk_zugang_service.sitzung_pruefen` (6.3).
- `ui_einheit.einheit_kontext`: Reihenfolge **Simulation (Header `X-EC-Einheit-Sim`, nur Admin) → Gerät → Zugang (`ec_gk`) → 403**. Ein Admin im Browser mit zufällig vorhandenem `ec_gk` bleibt in seiner Rolle.
- `_einheit_seite` (HTML-Hülle): zusätzlicher Zweig „kein User, aber gültiger Zugang“; ohne Zugang weiterhin Redirect auf `/login`. Banner „Gruppenkommandant-Zugang · <Name>“; ausgeblendet: „Normale Ansicht“, Umschalter Gesamtansicht (E1), Simulationselemente; sichtbar: „Abmelden“.
- Idempotenz (`EinheitAktion`): Vergleich `device_token_id` **und** `zugang_id` (ein Replay mit derselben `client_uuid` aus anderem Principal → 409).
- `kontext_fuer_geraet`/`kontext_fuer_einheit` bleiben unverändert (Tablets und Simulation funktionieren weiter).
- `ui_incident.index`: Anfrage ohne User, aber mit gültigem `ec_gk` → Redirect `/einheit` (Homescreen-Verknüpfung).

### 9.2 Schreibaktionen des Zugangs

Dependency `einheit_darf(aktion)` in `ui_einheit.py` mit Allowlist `ZUGANG_AKTIONEN = {status, meldung, foto, anforderung, antwort, quittierung, ressource_personal, ressource_ausstattung}`; `ressource_*` zusätzlich nur bei `gk_zugang_ressource_pflegen`. Alles andere → `403 zugang_aktion_nicht_erlaubt`. Autor in Chronik/Journal/Audit: `quelle="zugang"`, `author_name=ctx.akteur_name`, Audit-Payload `via="zugang"`, `zugang_id`, `generation`.

### 9.3 Serializer-Prüfung

`/einheit/api/zustand` und `/auftrag/{id}` werden einmalig auf Felder geprüft, die ein Gruppenkommandant nicht sehen soll: keine Telefonnummern oder Namen anderer Einheiten (heute nur „andere Einheiten an der Stelle: Label + Status“), keine Zugangsdaten, keine internen Notizen der Führung außerhalb der Auftragsdaten. Test (14) vergleicht die Schlüssel der JSON-Antworten gegen eine Whitelist.

### 9.4 Echtzeit

- Neuer Kanal `EINHEIT_WS_OFFSET = 30_000_000` in `broadcast.py`; `broadcast_einheit(einheit_id, event)`.
- `broadcast_lage` leitet Events mit `type == "einheit:changed"` und `einheit_id` zusätzlich auf den Einheitskanal (zentrale Stelle, vorhandene 8 Aufrufer bleiben unverändert); Nutzlast bleibt IDs-only.
- `WS /ws/einheit-zugang`: validiert `ec_gk` mit `sitzung_pruefen`, abonniert **nur** den Kanal der eigenen Einheit, **prüft die Sitzung bei jedem `ping`** (Intervall 25 s, Client sendet es bereits) und schließt bei Ungültigkeit mit Code `4401` nach einer Nachricht `{"type":"zugang:widerrufen"}`. Zusätzlich sendet `widerrufe()` diese Nachricht sofort.
- Der Client (`einheit_modus.js`) bekommt einen konfigurierbaren WS-Pfad (`window.EINHEIT_WS_PATH`, gesetzt von der Hülle: `/ws/lage/{id}` für Geräte, `/ws/einheit-zugang` für Zugang). Für Stellenänderungen der Führung ohne `einheit:changed` bleibt der vorhandene 30-s-Polling-Fallback mit ETag zuständig (bekannte, dokumentierte Latenz).
- Keine Push-Benachrichtigung (kein FCM-Token).

### 9.5 Offline (Anforderung 14)

Der Zugang nutzt `einheit_outbox.js`, den Zustandscache und die SW-Regeln des Einheitenmodus unverändert; Anpassungen:

| Anforderung | Umsetzung |
|---|---|
| Geladene Aufträge offline anzeigen | `sw.js` cached `/einheit`, `/einheit/api/zustand`, `/einheit/api/auftrag/{id}` wie bisher; **Cache-Name enthält die Principal-ID** (`einheit-gk-<zugang_id>-v1`), damit zwei Principals im selben Browser nie Daten teilen |
| Rückmeldungen zwischenspeichern | Outbox-DB je Principal: `ec-einheit-gk-<einheit_id>-<zugang_id>` (Geräte: wie bisher; Simulation: `ec-einheit-sim-<id>`) |
| Synchronisieren bei Verbindung | wie bisher (Flush bei `online`, Sichtbarkeit, WS-Open, Timer). Hintergrund-Sync bei geschlossenem Browser gibt es nicht (Android-Worker gilt nur für die App) |
| Doppelte Übermittlung verhindern | `client_uuid` + `EinheitAktion` (unverändert) |
| Konflikte nachvollziehbar | bestehende 409-Codes + Konflikt-UI; neu `zugang_geaendert` (Generation hat gewechselt) wird wie `einheit_gewechselt` behandelt |
| Widerrufene Tokens | Antworten `401 zugang_widerrufen/-abgelaufen/-ungueltig` sind **terminal**: Outbox stoppt, markiert offene Einträge `blockiert_zugang`, **nichts wird verworfen**; Banner „Zugang beendet – n Meldungen nicht übermittelt. Bitte Einsatzleitung informieren.“ Einträge bleiben lesbar und kopierbar (Text), Button „Lokale Daten löschen“. Server: Anfragen mit widerrufener Sitzung werden **vor** jeder Verarbeitung abgewiesen – es gibt keinen Pfad, auf dem eine verspätete Outbox-Aktion angenommen wird |
| Sensible lokale Daten | Cookie `HttpOnly` (Token nie in JS-Reichweite nach Einlösung); lokal nur Auftrags-/Meldungsdaten, keine Telefonnummern fremder Personen; Aufbewahrung höchstens 72 h (Bereinigung beim Start); Löschung bei Abmeldung, terminalem 401, `lage_closed`; Fotos nur als wartende Blobs bis zur Übertragung; SW-Cache wird per `postMessage({type:"einheit-cache-leeren"})` geleert. Ehrliche Grenze: bei Gerätediebstahl ohne Verbindung bleiben lokal gecachte Daten bis zur Bereinigung lesbar – Schutz über Gerätesperre, Kürze der Aufbewahrung, keine Zugangsdaten im Speicher |

Die JS-Tests (`tests/js/*.test.mjs`) erhalten Fälle für terminale 401, Principal-getrennte DB-Namen und Bereinigung.

---

## 10. Ressourcenjournal und Einsatzhistorie

### 10.1 Eine Sicht, keine zweite Struktur

`ressource_karte_service.journal(db, lage, einheit, *, typen=None, site_id=None, vor_id=None, limit=50)` führt zusammen (alles bestehende Tabellen):

| Quelle | Filter | Typen |
|---|---|---|
| `LageJournalEntry` | `einheit_id = X` (neu), Kategorien `ressource`, `ressource_fhr`, `ressource_manuell` | Status, Disposition, Führung, Personal, Ausstattung, Zugang, Abrücken, manuell |
| `SiteLogEntry` | `einheit_id = X` (seit 0263) | Lagemeldungen, Maßnahmen, Notizen, Statusmeldungen `kind="einheit"`, Medien |
| `CommLogEntry` (nach P2-1) | `einheit_id = X` | Anforderungen, Rückfragen, Antworten |
| `EinheitSiteDispatch` (nur Lücken) | Zeitstempel `dispatched_at`, `bestaetigt_at`, `vor_ort_at`, `beendet_at`, `withdrawn_at` | nur für Dispatches, zu denen **kein** `LageJournalEntry` mit `einheit_id` existiert (Altbestand vor Migration) |

Ergebnis: `list[JournalZeile(ts, ereignis_typ, text, quelle, autor, site_id, dispatch_id, ref=(tabelle, id), storniert)]`, nach `ts` absteigend, Paging über `(ts, ref)`. Dedupe-Regel: gleiche `ref` nie doppelt; Dispatch-Ersatzzeilen entfallen, sobald ein echter Eintrag existiert.

### 10.2 Automatische Ereignisse – zentrale Ereignisfunktion

`resource_service._journal(db, lage_id, text, category, author_name, user_id)` wird zu einer Funktion mit zusätzlichen Schlüsselwörtern `einheit_id`, `site_id`, `ereignis_typ`, `quelle`; alle bestehenden Aufrufe in `resource_service` und `einheit_service` übergeben `einheit_id` (ca. 12 Stellen). Die Signatur bleibt abwärtskompatibel.

| Geforderter Eintrag | Ereignistyp | Ausgelöst durch | Stand |
|---|---|---|---|
| Ressource angelegt | `angelegt` | `add_resource` | 🔧 Bezug ergänzen |
| Status geändert | `status` | `set_status` | 🔧 |
| Einheit disponiert | `disponiert` | `dispatch_to_site` | 🔧 |
| Einsatz übernommen | `uebernommen` | `setze_einheit_status(bestaetigt)` | ✅ schreibt heute schon Journal → `einheit_id` ergänzen |
| Einsatz begonnen | `begonnen` | `setze_einheit_status(anfahrt/vor_ort/in_arbeit)` — **ein** Eintrag je Dispatch bei der ersten Aktivierung | 🆕 (heute nur `SiteLogEntry`) |
| Einsatz abgeschlossen | `abgeschlossen` | `setze_einheit_status(abgeschlossen/nicht_durchfuehrbar)` | 🔧 |
| Auftrag zurückgezogen | `zurueckgezogen` | `withdraw_from_site` (mit Grund/Person) | 🔧 |
| Gruppenkommandant zugewiesen / gewechselt | `gk_zugewiesen`, `gk_gewechselt` | `setze_gruppenkommandant` | 🔧 |
| Personalstärke geändert | `personal` | `ressource_pflege_service` | 🆕 Phase 4 |
| Ausstattung geändert | `ausstattung` | `ressource_pflege_service` | 🆕 Phase 4 |
| Unterstützung angefordert | `unterstuetzung` | `funkjournal_service.anforderung_erstellen` (P2-1) | 🆕, abhängig von P2-1 |
| Lagemeldung übermittelt | `lagemeldung` | `site_log_service.add_site_log(kind="lagemeldung", einheit_id=…)` — Journaleintrag zusätzlich zum SiteLog? **Nein:** Quelle ist bereits `SiteLogEntry` (10.1); kein Doppeleintrag | ✅ nur Anzeige |
| Einheit abgerückt | `abgerueckt` | `set_status(abgerueckt)` | 🔧 |
| Zugang ausgestellt/widerrufen/gesendet | `zugang` | `gk_zugang_service` (Text ohne Link; Nummer maskiert) | 🆕 |

Texte der Einträge erwähnen weder Token noch vollständige Telefonnummern (maskiert).

### 10.3 Manuelle Einträge und Nachvollziehbarkeit

- `POST …/einheiten/{id}/journal` (`_can_edit`): Text (1–1 000), optional `site_id` (muss zur Lage gehören) → `LageJournalEntry(category="ressource_manuell", ereignis_typ="manuell", quelle="manuell", einheit_id, site_id, author_name, user_id)`; Broadcast `ressource:changed{einheit_id}`.
- **Kein Bearbeiten und kein hartes Löschen** von Ressourceneinträgen. Korrektur = neuer Eintrag; Fehleintrag = `POST …/journal/{id}/storno` (Grund Pflicht) → `storniert_at/_von/_grund`, Zeile bleibt durchgestrichen sichtbar. Die vorhandene Löschroute lehnt Kategorien `ressource*` ab (409).
- Audit `gsl.ressource.journal_eintrag|journal_storno`.
- Druck/Zeitreise/Bericht: Bestehende Auswertungen lesen `LageJournalEntry` weiter; die neuen Spalten sind additiv. `druck_bericht.html` zeigt Einträge unverändert.

### 10.4 Einsatzhistorie

Der Tab „Einsätze“ (4.6) ist die vollständige Historie der Einheit: aktuelle, offene, abgeschlossene und zurückgezogene Dispositionen (alle Dispatches der Einheit in der Lage, nicht nur aktive). Dauer, Abschlussmeldung und Maßnahmen werden in `ressource_karte_service.einsaetze` berechnet (kein neues Feld). Rückzug speichert künftig Grund und Person (`einheit-abziehen` erhält optionales Feld `grund`; Standardtext „ohne Angabe“). Pro Einsatz zeigt der Aufklapper die einsatzbezogenen Journalzeilen der Einheit (`site_id`-Filter).

---

## 11. Personal, Ausstattung, Verstärken, Teilen

### 11.1 Trennung Stammdaten ↔ Lagezustand

- Stammdaten bleiben unberührt: `Member`, `MemberQualification`, `VehicleMaster`, `AtemschutzGeraet`, `VerleihArtikel`.
- Lagezustand lebt in `lage_einheit_person`, `lage_einheit_ausstattung` und den Aggregatfeldern an `LageEinheit`. Stammdaten dienen nur als **Vorschlag** (Mitgliedersuche, Qualifikationen, Atemschutzgeräte), Änderungen der Lage wirken nie zurück.

### 11.2 Personal

Zwei Betriebsarten je Einheit (`personal_modus`), Umschaltung mit Bestätigung:

- **`summe`** (Standard, schnell im Einsatz): Zahlen `staerke_gesamt`, `staerke_fuehrung`, `staerke_agt`, `staerke_sanitaeter` (+ Bemerkung, weitere Qualifikationen als Freitext). Validierung: Teilsummen ≤ Gesamt.
- **`liste`**: Personenzeilen (Mitglied oder Freitext, Funktion, Qualifikationen). Die vier Zahlen werden **berechnet** und im Formular gesperrt; Berechnung bei jeder Mutation in derselben Transaktion (`ressource_pflege_service._neu_berechnen`).

Funktionen (`ressource_pflege_service`, alle mit Journal `personal` und Broadcast):

| Funktion | Wirkung |
|---|---|
| `personal_setzen(einheit, zahlen…)` | Summenmodus; Delta im Journal („Mannschaft 4 → 6“) |
| `person_hinzufuegen(einheit, member_id|name, funktion, quali, herkunft)` | Listenmodus; `aktiv_key` verhindert Doppelzuordnung — Fehlermeldung nennt die Einheit, bei der das Mitglied aktiv ist |
| `person_entfernen(person, grund)` | `bis_at` setzen, `aktiv_key=NULL` |
| `verstaerken(einheit, anzahl|personen, quelle_einheit?)` | Summenmodus: Delta; Listenmodus: Personenzeilen mit `herkunft="verstaerkung"` |
| `abloesen(person_alt, person_neu)` | alte Zeile beenden, neue anlegen, `abloesung_von_id`; Journal „Ablösung: A → B“ |
| `umbuchen(von_einheit, nach_einheit, personen|anzahl)` | **eine** Transaktion, beide Seiten, gemeinsame `umbuchung_id`; Listenmodus verschiebt Zeilen (alte beenden, neue anlegen), Summenmodus bucht Delta ab/zu; Journal in **beiden** Einheiten; nie ein Zustand mit Doppelzählung |

Kräfteübersicht: Gesamtstärke je Abschnitt/Lage wird aus Einheiten summiert, die `zaehlt_fuer_summen` sind (keine Kinder eines Verbands, siehe 11.4) – keine Doppelzählung.

### 11.3 Ausstattung und Fähigkeiten

Katalog (Konstante `GSL_AUSSTATTUNG_KATALOG` in `ressource_pflege_service.py`, erweiterbar als Freitext `sonstiges`): `tragkraftspritze`, `tauchpumpe`, `stromerzeuger`, `beleuchtung`, `atemschutzgeraete`, `drohne`, `waermebildkamera`, `schlauchmaterial`, `motorsaege`, `sonderausruestung` und Fähigkeiten (`ist_faehigkeit=true`: `hochwasser`, `wasserrettung`, `hoehenrettung`, `gefahrstoff`, `sonstige`).

Funktionen: hinzufügen, entfernen, Menge ändern, Status ändern (`einsatzbereit|eingeschraenkt|defekt|nicht_verfuegbar` + Bemerkung), Vorlagenübernahme **„aus früherer Lage“** (kopiert die Zeilen der letzten `LageEinheit` desselben Fahrzeugs der Org, `stamm_ref_typ="vorlage_lage"`), Referenz auf Stammdaten (`atemschutz_geraet`: Auswahl aus `AtemschutzGeraet`, `verleih_artikel`: Auswahl aus `VerleihArtikel`) – nur Verweis, keine Kopplung. Journal `ausstattung` bei jeder Änderung; `defekt` zusätzlich als Warnchip auf der Übersicht.

### 11.4 Verstärken, Reduzieren, Zusammenfassen, Aufteilen

| Vorgang | Modell | Regeln |
|---|---|---|
| **Verstärken** (Personal) | `verstaerken(...)` | siehe oben; Material: `ausstattung` hinzufügen/Menge erhöhen |
| **Reduzieren** | `umbuchen(von, nach)` oder `person_entfernen` | Material: `ausstattung_umbuchen(von, nach, zeile, menge)` – Menge wird atomar abgezogen/zugebucht |
| **Zusammenfassen** | Neue `LageEinheit(resource_type="verband", label=…)`; die Mitglieder-Einheiten erhalten `verband_id`. Personal und Ausstattung des Verbands werden aus den Kindern **berechnet**, nicht gespeichert | Voraussetzung: Kinder aus derselben Lage, nicht abgerückt, nicht selbst Verband; Kinder dürfen nur aktive Dispositionen zur **selben** Stelle (oder keine) haben, sonst `409` mit Liste. Bestehende Dispositionen der Kinder bleiben **unverändert** an den Kindern. Der Verband erhält eine eigene Führer-Zeile (eigener Zugang möglich); Zugänge der Kinder bleiben bestehen. Disponieren erfolgt künftig am Verband (Kinder sind in Pool/Abschnittsspalten eingeklappt unter dem Verband, nicht einzeln disponierbar). Auflösen setzt `verband_id = NULL` |
| **Aufteilen** | Neue `LageEinheit` je Teil mit `aufgeteilt_von_id`, `vehicle_id = NULL`, eigenem Label/Typ; Personal und Ausstattung werden **verschoben** (nicht kopiert) mit gemeinsamer `umbuchung_id`; die Mutter behält Dispatches (optional wählbar: einzelne Dispatches *kopieren* als neue Dispositionen für die Teile – nur wenn ausdrücklich angehakt) | Berechtigung: `_can_edit` in der Lage; ein Gruppenkommandant kann weder Verbände bilden noch teilen; Untereinheiten erhalten Zugang nur über ihren eigenen Leader |
| Nachvollziehbarkeit | Journal in allen beteiligten Einheiten (`ereignis_typ="personal"`/`"ausstattung"` mit Text „Aufteilung aus <Mutter>“, „Teil von Verband <X>“), Audit `gsl.ressource.zusammenfassen|aufteilen|umbuchen` |

Constraint-Absicherung: Alle Umbuchungen laufen in einer Transaktion mit Zeilensperre auf den betroffenen `lage_einheit`-Zeilen (aufsteigend nach ID gesperrt → keine Deadlocks); `aktiv_key` macht Doppelzuordnung eines Mitglieds technisch unmöglich.

### 11.5 Rechte

Führung: `_can_edit`. Gruppenkommandant (Zugang): nur eigene Einheit, nur `personal_setzen` (Summenmodus), Ausstattung Menge/Status, Bemerkung, und nur bei `gk_zugang_ressource_pflegen`; Journalquelle `zugang`. MCP: Abschnitt 12.

---

## 12. Schnittstellen und MCP

### 12.1 HTTP-Schnittstellen (Führung, JSON/HTMX; alle `require_role(...)`, `_lage_or_404`, `_check_org_access`, CSRF)

Präfix `/lage/{lage_id}/einheiten/{einheit_id}`; Router `ui_ressourcenkarte.py` 🆕.

| Methode + Pfad | Zweck | Rollen |
|---|---|---|
| `GET /karte` | Drawer-Rahmen (Kopf, Tabs, aktiver Tab) | Lese |
| `GET /karte/{tab}` | Tab-Teil (`uebersicht\|einsaetze\|journal\|personal\|ausstattung\|zugang`) | Lese (Zugang: `_can_edit`) |
| `POST /stamm` | Funkruf, Org, BOS, Bereitstellungsraum, Menge/Einheit | `_can_edit` |
| `POST /gruppenkommandant` | zuweisen/wechseln/korrigieren (Telefon) | `_can_edit` |
| `POST /gruppenkommandant/entfernen`, `POST /stellvertreter` | | `_can_edit` |
| `POST /journal`, `POST /journal/{id}/storno` | manuell | `_can_edit` |
| `POST /zugang/link` | Link ausstellen (JSON, no-store) | `_darf_zugang_verwalten` |
| `POST /zugang/senden` | SMS | `_darf_zugang_verwalten` |
| `POST /zugang/widerrufen`, `POST /zugang/verlaengern`, `POST /zugang/sitzung/{sid}/beenden` | | `_darf_zugang_verwalten` |
| `POST /personal/*`, `POST /ausstattung/*` | Phase 4 | `_can_edit` |
| `POST /zusammenfassen`, `POST /aufteilen`, `POST /umbuchen` | Phase 4 | `_can_edit` |

Bestehende Routen bleiben: `/kommandant`, `/fuehrer` (delegieren an `setze_gruppenkommandant`), `/status`, `/sektor`, `/pool`, `/loeschen`, `/einsatz`.

Öffentlich (`ui_gk_zugang.py`): `GET /gk`, `POST /gk/pruefen|pin|einloesen|abmelden` (6.6). Einheitenmodus: Ergänzungen in `ui_einheit.py` (9.2) und `WS /ws/einheit-zugang` in `ws.py`.

### 12.2 MCP-Werkzeuge (`app/mcp/tools/gsl.py`, gemeinsam mit Schwesterplan P3-3)

Gemeinsame Regeln: `MCPContext` (Live-Rollenprüfung in `load_live_context`), `lage.org_id == context.org_id` sonst „Lage nicht gefunden“, `module_check` = GSL-Modul/Feature `mi_feature_ressourcen` der Org (Logik aus `_get_mi_features` in eine Service-Funktion verschoben – Arbeitspaket von P3-3, hier geteilt), **dieselben Services wie die Weboberfläche**, Rollenkonstanten identisch zu den Routen (`READ = incident_leader, admin, recorder, readonly`; `EDIT = incident_leader, admin, recorder`), Schreibtools verlangen eine aktive Lage, Audit mit `via="mcp"`, `author_name="<Name> (MCP)"`, Journalquelle `mcp`.

| Tool | Rollen | Service | Hinweise |
|---|---|---|---|
| `gsl_ressourcen_liste(lage_id, status?, abschnitt_id?, typ?)` | READ | `kraefteuebersicht`/`ressource_karte_service` | kompakte Liste inkl. Gruppenkommandant (Name; Telefon nur EDIT), Zugangsstatus (Punkt), aktueller Einsatz |
| `gsl_ressource_details(lage_id, einheit_id, bereiche?)` | READ | `ressource_karte_service.karte` | Bereiche `allgemein, fuehrer, einsaetze, journal, personal, ausstattung, kommunikation, zugang`; `zugang` liefert nur Status/Zeiten/Versandprotokoll, **nie Token/Link**; ersetzt `gsl_einheit_auftraege` aus P3-3 |
| `gsl_ressource_aktualisieren(lage_id, einheit_id, felder)` | EDIT | `set_status`, `assign_to_sector`, `aktualisiere_einheit_stamm` | Whitelist: `status, abschnitt_id, funkrufname, bereitstellungsraum, org_name, bos, menge, einheit, bemerkung`; Statusübergang `abgerueckt` löst Zugangssperre aus |
| `gsl_ressource_fuehrer_setzen(lage_id, einheit_id, mitglied_id? / name?, telefon?, modus?, stellvertreter?)` | EDIT | `setze_gruppenkommandant` | löst bei aktivem Org-Schalter den Auto-SMS-Pfad aus (Antwort nennt nur `sms: geplant/übersprungen`) |
| `gsl_ressource_zugang_senden(lage_id, einheit_id, bestaetigt: true)` | EDIT | `gk_zugang_service` (SMS-Pfad 7.3) | nur SMS; **kein** Kopieren; `bestaetigt` Pflicht, weil ein Aufruf den bisherigen Link entwertet; Antwort: Versandstatus, nie Link |
| `gsl_ressource_zugang_widerrufen(lage_id, einheit_id)` | EDIT | `widerrufe(grund="manuell")` | |
| `gsl_ressource_journal(lage_id, einheit_id, typen?, seit?, limit?, neuer_eintrag?)` | READ; Schreiben (`neuer_eintrag`) EDIT | `ressource_karte_service.journal` / manuelle Eintragsfunktion | Rollenprüfung für das Schreiben im Handler |
| `gsl_ressource_personal(lage_id, einheit_id, aktion, …)` | READ; Änderung EDIT | `ressource_pflege_service` | `aktion`: `lesen, setzen, hinzufuegen, entfernen, verstaerken, abloesen, umbuchen` |
| `gsl_ressource_ausstattung(lage_id, einheit_id, aktion, …)` | READ; Änderung EDIT | `ressource_pflege_service` | `aktion`: `lesen, hinzufuegen, entfernen, menge, status` |

Namensabgleich mit P3-3: `gsl_lagen_liste`, `gsl_einsatzstelle_lesen`, `gsl_einheit_status_setzen`, `gsl_lagemeldung_erfassen`, `gsl_massnahme_erfassen`, `gsl_unterstuetzung_anfordern`, `gsl_rueckfragen_*`, `gsl_auftrag_*`, `gsl_foto_*` bleiben dort; die Tools dieses Plans ergänzen sie. Wer zuerst umgesetzt wird, legt `gsl.py` und den `module_check` an. Dokumentation: `docs/wiki/Administration-MCP-Server.md`.

### 12.3 Der Zugangs-Token öffnet kein MCP

- MCP authentifiziert Bearer-Tokens, die an `User`-Zeilen gebunden sind (`app/mcp/server.py`, `mcp.py`); der GK-Token/-Cookie ist weder Bearer noch User.
- `load_live_context` verlangt aktiven Nicht-Geräte-User derselben Org; ein GK-Principal besitzt keinen.
- Test: `ec_gk`-Cookie an `/mcp` → 401; GK-Link-Token als Bearer → 401; GK-Cookie an `/api/mcp/uploads/*` und `/api/mcp/downloads/*` → 401/403.

---

## 13. Sicherheits- und Fehlerszenarien

### 13.1 Bedrohungen und Gegenmaßnahmen

| Bedrohung | Gegenmaßnahme |
|---|---|
| Link wird in Chat geteilt/weitergeleitet | genau ein Zugang je Einheit, Sitzungslimit, Anzeige aktiver Sitzungen mit „beenden“, Ablauf, optionale SMS-PIN, Rotation macht Kopien wertlos |
| Link-Vorschau-Bots (WhatsApp, Teams/SafeLinks, SMS-Filter) | Token im Fragment, `GET /gk` verbraucht nichts, Einlösung nur per POST aus JS |
| Token in Logs (nginx, App, Sentry-ähnlich, Fehlerseiten) | Fragment erreicht den Server nicht; POST-Body wird nie geloggt; keine Token in Audit/Exception/Repr; Test mit `caplog` |
| Brute Force des Tokens | 192 Bit, Hash-Lookup, Rate-Limit `10/15minutes` je IP auf `/gk/pruefen|einloesen` (Redis-Storage wie `rate_limit.py`) |
| Brute Force der PIN | 6 Stellen, 10 min, 5 Versuche, Sperre 15 min, Rate-Limit, Hinweis in der Führung |
| ID-/URL-Manipulation | Einheit nur aus Sitzung; Aufträge nur über `einheit_id`-Kette; fremde IDs → 404 |
| Mandantenübergriff | `org_id` an Zugang und Sitzungen, Prüfung 6.3 Nr. 8, Cross-Org-Tests (14) |
| Race Rotation/Widerruf | Zeilensperre + `UNIQUE(einheit_id)`; Sitzungsprüfung gegen `generation` |
| Weitergabe durch QR-/Gerätesession der Führung | Ausgabe nur mit echter Personen-Session (6.7) |
| Fremde Inhalte in der SMS (Namen) | Platzhalterwerte werden gekürzt und bereinigt |
| Admin-Simulation mischt Daten | Simulation stellt keine Zugänge aus; eigene Outbox-DB |
| Datenabfluss über Serializer | Whitelist-Test der JSON-Schlüssel (9.3) |
| Clickjacking/Referer | `Referrer-Policy: no-referrer`, bestehende Security-Header |

### 13.2 Restrisiken (bewusst akzeptiert, dokumentiert)

- Der Link steht in SMS-/Chatverläufen und im Protokoll des SMS-Gateways. Er ist ab Rotation/Ablauf wertlos; bis dahin gilt er als Inhaber-Token – wer ihn zuerst einlöst, hat (innerhalb des Sitzungslimits) Zugriff. Gegenmaßnahmen siehe oben; die SMS-PIN schließt die Lücke vollständig, solange die Telefonnummer korrekt ist.
- Lokal gecachte Auftragsdaten auf einem gestohlenen, entsperrten Gerät (9.5).
- Wer einen laufenden Zugang kopiert, entwertet ihn für den Gruppenkommandanten (UI warnt bei aktiver Sitzung).

### 13.3 Fehlerszenarien

| Szenario | Verhalten |
|---|---|
| SMS-Provider nicht verbunden | Versand `uebersprungen`, keine Rotation, Hinweis; Kopieren möglich |
| Provider-Fehler/Gateway-Timeout | `fehlgeschlagen` bzw. `unklar` (Timeout nach Übergabe), Zuweisung bleibt, „Erneut senden“ rotiert neu |
| Prozess stirbt zwischen Commit und Versand | Versandzeile `geplant` → nach 2 min `fehlgeschlagen`; kein Spinner-Leichenzustand |
| Commit schlägt fehl | Kein Token entstanden (Rollback), keine SMS (Versand nur nach Commit) |
| Gruppenkommandant/Nummer ändert sich während des Versands | `verworfen`, Anzeige „veraltet“ |
| Zwei Führungsbenutzer rotieren gleichzeitig | Zeilensperre; zweiter bekommt `409 zugang_geaendert` + aktuellen Stand |
| Doppelklick/Retry | `bestehender_link` und `auto_schluessel`; kein Doppelversand |
| Link ohne Fragment (App kürzt) | Seite zeigt „Link unvollständig – bitte neuen Link anfordern“ |
| Cookies blockiert | Einlösung meldet „Cookies nötig“ (Test `Set-Cookie` per Folgeabruf) |
| Mehrere Lagen derselben Einheit | je Lage eigener Zugang; ein Browser hält ein Cookie (zweite Einlösung ersetzt die erste Sitzung im Browser, die erste bleibt serverseitig gültig bis Ablauf) |
| Lage wiedereröffnet | Zugänge bleiben widerrufen |
| Einheit wieder bereitgestellt nach „abgerückt“ | neuer Link nötig |
| Uhrzeitabweichung des Clients | alle Prüfungen serverseitig in UTC |
| Fehlende Nummer beim manuellen Senden | 422, keine Rotation |
| SMS-PIN gefordert, aber kein SMS-Anbieter | Einlösung nicht möglich, klare Meldung; Führung kann Schalter ausschalten |

### 13.4 Audit

`gsl.zugang.ausgestellt|widerrufen|eingeloest|abgemeldet|sms_auto|sms_manuell|link_kopiert|pin_angefordert|pin_fehlgeschlagen|sitzung_beendet|verlaengert`, `gsl.ressource.gk_gesetzt|journal_eintrag|journal_storno|personal|ausstattung|zusammenfassen|aufteilen|umbuchen`. Payload enthält `lage_id`, `einheit_id`, `zugang_id`, `generation`, `grund` – nie Token, Link, PIN oder vollständige Nummer.

---

## 14. Tests und Akzeptanzkriterien

Dateien (neu, Muster der bestehenden Tests): `tests/test_ressourcenkarte.py`, `tests/test_gruppenkommandant_service.py`, `tests/test_gk_zugang_service.py`, `tests/test_gk_zugang_api.py`, `tests/test_gk_zugang_sicherheit.py`, `tests/test_gk_auto_sms.py`, `tests/test_ressource_journal.py`, `tests/test_ressource_pflege.py`, `tests/test_mcp_gsl_ressourcen.py`, Migrationstests, Erweiterungen in `tests/test_public_tenant_isolation.py`, `tests/test_gsl_tenant_isolation.py`, `tests/test_einheit_api.py`, `tests/js/*.test.mjs`. SMS-Versand wird mit gemocktem `send_sms` getestet.

### 14.1 Pflichtfälle der Anforderung

| Fall | Testebene | Erwartung |
|---|---|---|
| Bestehende Ressourcenübersicht funktioniert | Integration | `GET /lage/{id}/ressourcen` und `/kraefteuebersicht` rendern wie zuvor (bestehende Tests grün); Inline-Aktionen funktionieren |
| Karte öffnen und bearbeiten | Integration | `GET …/karte` und alle Tabs 200 mit Rolle Lese; `POST /stamm` aktualisiert, antwortet mit Tab-Teil, Broadcast `ressource:changed{einheit_id}` |
| GK zuweisen (Mitglied) | Service | Zeile `LageEinheitLeader`, `leader_assignment_id`, `commander_label`, Journal `gk_zugewiesen`, Telefon aus `Member.phone` |
| Externer GK (Freitext) | Service | `member_id=NULL`, Name Pflicht, Telefon optional |
| Telefonnummer ändern | Service | `phone_version+1`, Zugang gesperrt, Journal (maskiert) |
| Normalisierung | Unit | `0664 123 45 67`, `+43…`, `0043…`, Ausland, ungültig |
| Manueller SMS-Versand | Integration | Rotation + `send_sms` mit gerendertem Text; Versandzeile `gesendet`; `SmsLog` mit geschwärztem Text; Tab zeigt Ergebnis |
| Nachricht/Link kopieren | Integration + JS | `/zugang/link` liefert Link/Text, `no-store`; zweiter Klick mit `bestehender_link` rotiert nicht; JS-Fallbackkette (`ClipboardItem` → `writeText` → Dialog) getestet |
| Auto-SMS an | Integration | Zuweisung → nach Commit genau eine SMS; Versandzeile mit `auto_schluessel` |
| Auto-SMS aus | Integration | keine SMS, kein Token |
| Keine Duplikate | Integration | gleicher GK + Nummer → `aenderung="keine"`, 0 SMS, 0 Rotation; paralleler Doppelaufruf → 1 Versand (UNIQUE) |
| Alter Token bei Nummernwechsel ungültig | Sicherheit | `/gk/einloesen` mit altem Token → beendet/ersetzt |
| Alter Token bei GK-Wechsel ungültig | Sicherheit | dito |
| Aktive Sitzung nach Widerruf gesperrt | Sicherheit | gleiche `ec_gk`-Sitzung: vorher 200, nach Rotation/Wechsel/Telefon/Widerruf/Abrücken `401 zugang_widerrufen` (jeweils eigener Test); WS wird geschlossen |
| Abgeschlossene GSL sperrt | Sicherheit | `close_lage` → Sitzung 401; Wiedereröffnung reaktiviert nicht |
| Fremde Ressourcen | Sicherheit | `GET /einheit/api/auftrag/{id_einer_anderen_einheit}` 404; Statuspost auf fremden Dispatch 404 |
| Fremde Organisationen | Sicherheit/Public | Token Org A zeigt in `/gk/pruefen` nur Org-A-Daten; Sitzung A auf Lage/Einheit B → 404/401; Fälle in `test_public_tenant_isolation.py` für alle `/gk/*`-Routen |
| GK kann keine Führungsfunktion | Sicherheit | Routenvollständigkeitstest: für **jede** Route der App (außer `/gk*`, `/einheit*`, `/ws/einheit-zugang`, statische) liefert eine Anfrage nur mit `ec_gk` weder 200 noch 2xx-Mutation (401/302/403/404); `/ws/lage/{id}` 4401 |
| Bestehende Fahrzeug-Tablets | Integration | alle Tests `test_einheit_api.py`, `test_einheit_gesamtansicht.py`, `test_einheit_seite.py` unverändert grün |
| Einsatzhistorie vollständig | Service | alle 4 Gruppen, Dauer, Abschlussmeldung, Rückzug mit Grund/Person |
| Journal enthält Ereignisse | Service | je ein Test pro Ereignistyp der Tabelle 10.2; Dedupe Dispatch-Ersatzzeilen; Storno statt Löschen; Löschroute lehnt ab |
| Personal/Ausstattung aktualisieren | Service | Summen-/Listenmodus, Umbuchung atomar, Doppelzuordnung → Fehler, Stammdaten unverändert |
| Anzeige ohne Reload | Integration/E2E (auf Anforderung) | Antworten tragen `HX-Retarget`, kein `location.reload`, Broadcast-Event vorhanden; `rg "location.reload" app/templates` ohne neue Treffer |
| SMS-Fehler | Integration | Provider `False`/Exception/Timeout → `fehlgeschlagen`/`unklar`, GK-Zuweisung unverändert, „Erneut senden“ rotiert neu |
| Offline-Aktionen | JS + Integration | Outbox-Flush nach Offline, Replay mit gleicher `client_uuid` ohne Doppelwirkung, terminales 401 stoppt Flush und behält Einträge |
| MCP berücksichtigt Rollen | MCP | `readonly` darf lesen, nicht schreiben; Fremd-Org → „nicht gefunden“; GK-Cookie/-Token → 401; Antworten enthalten nie Token/Link |

### 14.2 Zusätzliche Sicherheitstests (Gate vor Freigabe)

- Token erscheint in keinem Logeintrag, Audit-Payload, `SmsLog.text`, Exception-Text (`caplog`, DB-Scan nach `gkz_`).
- Hash-Speicherung: nach `stelle_zugang_aus` enthält keine Spalte den Klartext.
- `UNIQUE(einheit_id)`: zwei gleichzeitige `stelle_zugang_aus` ergeben genau eine gültige Zeile; alter Token tot.
- Rotation-Atomarität: Fehler nach Hash-Update, vor Commit → Rollback, alter Token weiter gültig.
- Prüfung 6.3 einzeln (neun Bedingungen) durch gezielte DB-Manipulation, ohne den Widerrufsjob zu nutzen (Leader `end_at` setzen, Telefon direkt ändern, Org-Schalter aus, Lage `closed`).
- Rate-Limit, PIN-Sperre, PIN-Replay (`used`), PIN nach Rotation ungültig.
- Sitzungslimit: dritte Einlösung beendet älteste.
- CSRF auf allen `/gk` POSTs.
- Ausstellen verweigert für `is_device`, QR-Session, Simulation, `readonly`.
- Cache-/Header-Test: `/gk*` und Link-Antwort `no-store`, `no-referrer`.
- Serializer-Whitelist (9.3).
- SMS-Text: Bereinigung der Platzhalter, Segmentzähler (GSM-7/UCS-2/Erweiterungszeichen).

### 14.3 CI-Pflicht (CLAUDE.md)

Vor jedem Merge: `ruff check app/`, `mypy app/ --ignore-missing-imports`, `pytest -q`, `npm run test:js` (bei Änderungen an `einheit_*`-JS und neuem `ressourcen_karte.js`); CI testet gegen MariaDB inkl. `alembic upgrade head`. Nie zwei Testläufe parallel. Vor Commit: `rg '[“”„‘’]' app/templates`, `rg "\.strftime\(" app/templates`.

### 14.4 Abnahmekriterien je Phase

- **Phase 1:** Karte aus der Übersicht öffnet auf Desktop und Mobil; GK mit Telefon anlegen/wechseln/korrigieren; Einsatzhistorie vollständig; Journal enthält die Ereignisse 1–9 der Tabelle 10.2 pro Einheit; Legacy-Inline-Feld löst keine Doppelwechsel mehr aus.
- **Phase 2:** Sicherheitsgate 14.2 vollständig grün; Gruppenkommandant öffnet den Link auf einem Smartphone, sieht ausschließlich seine Einheit und kann Status/Lagemeldung/Foto senden; Widerruf wirkt innerhalb eines Requests bzw. ≤ 25 s auf offene WS-Verbindungen.
- **Phase 3:** Einstellungen speichern/lesen; Auto-SMS nur bei Schalter; Vorlage mit Zähler.
- **Phase 4:** Verstärken/Umbuchen/Teilen/Zusammenfassen ohne Doppelzählung.
- **Phase 5:** Kommunikationsblock vollständig (nach P2-1); MCP-Tools; End-to-End-Review.

---

## 15. Arbeitspakete

Jedes Paket ist ein eigener PR (Muster Schwesterplan: ruff/mypy/pytest/js grün). Pakete innerhalb einer Phase sind – wo nicht anders vermerkt – unabhängig mergebar. Abhängigkeiten in Klammern.

### Phase 1 – Ressourcenkarte

| PR | Inhalt | Abnahme |
|---|---|---|
| **GK-1.1** Datenmodell Karte (–) | Migration 0265 (5.1), Modellfelder, `telefon_zu_e164_at/-anzeige/-maske`, `status_at`-Pflege, Funkruf im Fahrzeug-Stammdatenformular | Migration + Backfill-Test auf MariaDB-kompatibler Syntax; bestehende GSL-Tests grün |
| **GK-1.2** Gruppenkommandant-Service (1.1) | `setze_gruppenkommandant`/`-stellvertreter`/`entferne_…`, Änderungserkennung, Umstellung der Legacy-Routen `/kommandant`, `/fuehrer` und des Inline-Felds (nur bei echter Änderung), Journalereignisse `gk_*` | Tests 14.1 (GK, Telefon, extern, „keine Änderung“); keine Doppelzeile bei gleichem Namen |
| **GK-1.3** Ereignisfunktion und Journal-Sicht (1.1) | `_journal` → Ereignisfunktion mit `einheit_id` an allen Aufrufern, `withdraw_from_site(grund)`, `ressource_karte_service.journal/einsaetze`, Lösch-Sperre, Storno, manuelle Einträge (Service) | Journal-Tests je Ereignistyp; Altbestand-Dedupe |
| **GK-1.4** Karten-Router und Drawer (1.2, 1.3) | `ui_ressourcenkarte.py`, Templates `_ressource_karte*.html`, `ressourcen_karte.js`, Klick auf Karte, Deep-Link, `ressource:changed{einheit_id}` in allen Einheiten-Routen, Tabs Übersicht/Einsätze/Journal, Mobil-Sheet, Berechtigungen | Tests 14.1 „Karte öffnen/bearbeiten“; Mobil ≤ 760 px geprüft; kein `location.reload`; Eingaben gehen beim Refresh nicht verloren |

### Phase 2 – Digitaler Zugang (Sicherheitsgate)

| PR | Inhalt | Abnahme |
|---|---|---|
| **GK-2.1** Zugangsmodell + Service (1.2) | Migration 0266, `gk_zugang_service` (Ausstellen/Rotieren/Widerrufen/`sitzung_pruefen`/Versand-Logik ohne Routen), Einstellungs-**Konstanten** mit Defaults über `getattr` (Schalter standardmäßig aus; Spalten kommen in 3.1 – bis dahin steuert eine Konstante `GK_ZUGANG_AKTIV=False`), Widerrufs-Hooks in `setze_gruppenkommandant`, `set_status`, `close_lage` | Service-Tests inkl. Atomarität, `UNIQUE`, neun Prüfbedingungen |
| **GK-2.2** Öffentliche Einlöse-Routen (2.1) | `/gk`, `/gk/pruefen|pin|einloesen|abmelden`, Rate-Limits, Cookie, Seite `gk/einloesen.html`, Header, Cross-Org-Tests in `test_public_tenant_isolation.py` | Tests 14.2 (Rate-Limit, PIN, CSRF, Header, Logging) |
| **GK-2.3** Einheitenmodus-Anbindung (2.1, 2.2) | `Quelle="zugang"`, `kontext_fuer_zugang`, `einheit_kontext`-Zweig, `_einheit_seite`, Aktionsallowlist, `EinheitAktion.zugang_id`, Serializer-Whitelist, `ui_incident.index`-Redirect, `WS /ws/einheit-zugang` + `broadcast_einheit`, JS-Konfiguration `EINHEIT_WS_PATH` | Routenvollständigkeitstest; Widerruf schließt WS; Tablet-Tests unverändert grün |
| **GK-2.4** Offline-Härtung (2.3) | Principal-getrennte Outbox-DB und SW-Cache, terminales 401, Bereinigung (72 h), Banner, JS-Tests | JS- und Integrationstests aus 14.1 „Offline“ |
| **GK-2.5** Zugangs-Tab und manuelle Zustellung (1.4, 2.1) | Tab „Zugang“, `POST /zugang/link|senden|widerrufen|verlaengern|sitzung/{sid}/beenden`, Nachrichtenvorlage (Standardtext aus Konstante), Segmentzähler, geschwärztes `SmsLog`, Kopierlogik mit Fallback, Sitzungsdialog | Tests 14.1 „SMS manuell“, „Kopieren“, „SMS-Fehler“ |
| **GK-2.6** Sicherheitsgate (2.1–2.5) | Reviewlauf (`/security-review`, Abgleich mit 14.2), Doku `docs/wiki/Anwender-GSL-Ressourcenverwaltung.md` (Karte, Zugang) und Abschnitt zur GSL-Konfiguration in `docs/wiki/Administration-Einstellungen.md`, CHANGELOG | Alle Tests 14.2 grün. **Erst danach** darf der Org-Schalter in der Produktion eingeschaltet werden |

### Phase 3 – GSL-Konfiguration

| PR | Inhalt | Abnahme |
|---|---|---|
| **GK-3.1** Einstellungen (2.1, 2.5) | Migration 0267, `OrgSettings`-Spalten, Karte in `gsl_einstellungen.html`, Speichern/Validieren (Platzhalter, Länge, Bereiche), Vorschau + Live-Zähler, Notbremse; Service liest echte Org-Einstellungen statt Konstante; Live-Prüfung Nr. 8 | Tests: Speichern, Validierung, Gesamtschalter aus ⇒ Sitzungen tot |
| **GK-3.2** Automatischer Versand (3.1) | Auto-Pfad in `setze_gruppenkommandant`, `sende_auto_sms` (BackgroundTask), `auto_schluessel`, Aufräumschleife, Übungsschutz, SMS-PIN-Versand (`/gk/pin`) | `tests/test_gk_auto_sms.py` (14.1 Auto-/Duplikat-/Fehlerfälle); PIN-Tests |

### Phase 4 – Ressourcenpflege

| PR | Inhalt | Abnahme |
|---|---|---|
| **GK-4.1** Modell (1.1) | Migration 0268, Modelle, Katalog, `aktiv_key` | Migrationstest, Constraint-Tests |
| **GK-4.2** Personal (4.1, 1.4) | `ressource_pflege_service` Personal (`summe`/`liste`, verstärken, ablösen, umbuchen), Tab „Personal“, Journal, Broadcast | Tests 14.1 „Personal“ |
| **GK-4.3** Ausstattung (4.1, 1.4) | Ausstattung inkl. Fähigkeiten, Vorlage aus früherer Lage, Stammdaten-Verweise, Tab „Ausstattung“ | Stammdaten bleiben unverändert (Test) |
| **GK-4.4** Verband und Teilung (4.2, 4.3) | zusammenfassen/auflösen/aufteilen, Kräfteübersicht-Darstellung (Kinder eingeklappt, Summen ohne Doppelzählung), Dispositionsregeln | Tests Abschnitt 11.4 |
| **GK-4.5** GK pflegt eigene Ressource (4.2, 4.3, 3.1, 2.3) | `/einheit/api/ressource/*`, Tablet-/Zugangs-UI-Formular, Org-Schalter | Zugang ohne Freigabe → 403; mit Freigabe nur erlaubte Felder |

### Phase 5 – Weitere Integration

| PR | Inhalt | Abnahme |
|---|---|---|
| **GK-5.1** Kommunikationsblock (1.4; **P2-1 des Schwesterplans**) | Anzeige letzter Lagemeldung, Statusänderung, offener Anforderungen, nicht quittierter Aufträge, GK-Aktivität; Kategorien `material/personal/abloesung` in `anforderung_erstellen`; Buttons im Einheitenmodus (mit P2-2) | Tests Kommunikation; keine zweite Anforderungslogik |
| **GK-5.2** MCP (4.2, 4.3, 2.5; ggf. P3-3) | `gsl.py` Tools 12.2, Doku, Cross-Org-/Rollen-/Token-Tests | `tests/test_mcp_gsl_ressourcen.py` grün |
| **GK-5.3** Politur und Optimierung (alle) | Abfrageoptimierung der Übersicht (`zugang_status_by_einheit`), Ladezeitmessung, Wiki-Seiten (`Anwender-GSL-Ressourcenverwaltung`, `Administration-Einstellungen`, `Administration-MCP-Server`), CHANGELOG, optionaler E2E `e2e/test_ressourcenkarte.py` (auf Anforderung) | Dokumentation vollständig |

### Abhängigkeitsgraph

```
GK-1.1 ─┬─ GK-1.2 ─┬─ GK-1.4 ─┬─ GK-2.5 ─┐
        └─ GK-1.3 ─┘          │          ├─ GK-2.6 ═══► Freigabe (Org-Schalter produktiv)
GK-1.2 ── GK-2.1 ─┬─ GK-2.2 ─ GK-2.3 ─ GK-2.4 ─┘
                  └────────── GK-2.5
GK-2.1+2.5 ─ GK-3.1 ─ GK-3.2
GK-1.1 ─ GK-4.1 ─ GK-4.2 ─┬─ GK-4.4
                  GK-4.3 ─┘    GK-4.5 (braucht 2.3, 3.1)
GK-1.4 ─ GK-5.1 (braucht P2-1 Schwesterplan) ; GK-5.2 (braucht 4.2/4.3/2.5)
```

Unabhängig voneinander nutzbar: Phase 1 als Ganzes liefert bereits Wert (Karte, GK mit Telefon, Journal). Phase 4 kann parallel zu Phase 2/3 laufen (keine gemeinsamen Dateien außer `ressourcen_karte.js`/Tab-Templates; Konflikte im Drawer-Rahmen beachten – Tabs sind je eigene Partials). **Sicherheitskritisch** sind GK-2.1 bis GK-2.6 und GK-3.2; der Org-Schalter bleibt bis zum bestandenen Gate aus.

---

## 16. Umsetzungsregeln und offene Punkte

### 16.1 Regeln für die Umsetzung (CLAUDE.md, verbindlich)

1. Nur gerade ASCII-Anführungszeichen in Templates, JS und Attributen.
2. Keine Bulk-`update()`/`delete()` auf Tenant-Tabellen; laden, dann mutieren. Öffentliche `/gk*`-Routen scopen über Hash → Zeile → `org_id` und haben Cross-Org-Tests.
3. Kein `location.reload()` nach HTMX; gezielte Swaps (`HX-Retarget`), Broadcasts `ressource:changed{einheit_id}`, `einheit:changed`, `site:card_changed`.
4. Zeiten: DB naive UTC; Anzeige `|local*`; Services mit Zeitausgabe bekommen `org`; Eingaben über `local_input_to_utc`.
5. Mobile Ansicht (≤ 760 px), `_csrf` in allen POST-Formularen.
6. Vor jedem Commit alle vier CI-Checks (14.3); Migrationen müssen auf MariaDB laufen.
7. Wiederverwendung: Services statt Router-Logik; keine zweite Einsatz-, Journal-, Einheiten- oder Statusverwaltung.
8. Router-Datei `ui_major_incident.py` wächst nicht weiter (neue Routen in `ui_ressourcenkarte.py` / `ui_gk_zugang.py`).
9. Nach Merge von Phase 2: CI auf `main` bestätigen; kein voller lokaler E2E-Lauf ohne Anforderung.
10. Codex-Hinweis: Branches/Commits legt der aufrufende Claude an (Codex-Sandbox kann nicht committen); Selbstberichte immer nachprüfen (Tests selbst laufen lassen).

### 16.2 Entscheidungen, die dieser Plan trifft (bei Abweichungswunsch vor GK-2.1 melden)

| # | Entscheidung | Alternative |
|---|---|---|
| E-GK1 | Token im Fragment (`/gk#…`) | Pfad-Token `/gk/<token>` (robuster gegen SMS-Clients, die `#` abschneiden, aber Token in nginx-Logs → bräuchte Log-Maskierung) |
| E-GK2 | Neuausgabe nur bei Bedarf (SMS/Kopieren), Rotation entwertet alten Link; Warnung bei aktiver Sitzung | Verlängern ohne Rotation (ist enthalten); reversibler Tokenspeicher (abgelehnt) |
| E-GK3 | Link bleibt bis Ablauf mehrfach einlösbar (Sitzungslimit 2) | Einmal-Einlösung (bricht bei App-Wechsel WhatsApp-Browser → Chrome) |
| E-GK4 | Standard: Gesamtschalter, Auto-SMS und PIN **aus** | PIN-Standard an für Echtlagen |
| E-GK5 | GK-Aktualisierung über eigenen WS-Kanal + 30-s-Polling (kein Push) | Web-Push für den GK (zusätzliche Abo-/Berechtigungsstrecke) |
| E-GK6 | MCP kann nur per SMS versenden, nie Link ausgeben | Link-Rückgabe (abgelehnt, Transkripte) |
| E-GK7 | Ausgabe des Links durch `recorder` erlaubt (Funker-Praxis), aber nie durch Geräte-/QR-Sitzungen | Nur `incident_leader`/`admin` |

### 16.3 Offen / außerhalb dieses Plans

- Gerätetest des Gruppenkommandanten-Zugangs auf echten Smartphones (Android Chrome, iOS Safari, WhatsApp-/Teams-In-App-Browser): Fragment bleibt erhalten, Cookie wird gesetzt, Kopieren funktioniert.
- Praxisprüfung der SMS-Länge mit dem eingesetzten Gateway (Segmentanzahl, Umlaute).
- Abstimmung mit Schwesterplan-Phase 2 (P2-1: Kategorien der Anforderung, Migrationsnummer).
- Betriebsdoku für die Führung (Wiki): „Wann neuen Link senden?“, „Was bedeutet Rotation?“.

---

## 17. Umsetzungsstand

### Phase 1 – abgeschlossen am 2026-10-10 (PR #495)

GK-1.1 bis GK-1.4: Migration 0265, `setze_gruppenkommandant`, Ressourcenjournal mit Einheitenbezug (inkl. Storno), `ressource_karte_service`, Drawer mit Tabs Übersicht / Einsätze / Journal.

### Phase 2 – Gruppenkommandanten-Zugang (Branch `feat/gsl-gk-phase2`)

GK-2.1 bis GK-2.5 umgesetzt, Gate GK-2.6 durchlaufen. Abweichungen vom Plan:

- Die acht `OrgSettings`-Spalten (Plan 5.3) sind schon in **Migration 0266** enthalten, nicht erst in 0267. Phase 3 liefert nur noch Formular, Validierung und Auto-SMS.
- `lage_einheit_zugang.vorheriger_token_hash` ergänzt: Nur damit kann ein rotierter Link die Meldung „beendet oder ersetzt“ statt „Link ungültig“ zeigen.
- Widerruf wirkt auf offene WebSockets **spätestens beim nächsten Ping (25 s)**; ein Post-Commit-Broadcast `zugang:widerrufen` ist nicht umgesetzt (HTTP-Anfragen werden sofort abgewiesen).
- `zugang_status()` liefert zusätzlich `sitzung_aktiv`; der Client fragt vor einer Rotation nach, wenn der Gruppenkommandant gerade angemeldet ist.
- Die automatische SMS (`sende_auto_sms`) und der Auto-Pfad in `setze_gruppenkommandant` kommen mit GK-3.2.
- Browser-Smoketest (Chromium): `/gk#<token>` → Fragment wird entfernt → Einlösung → `/einheit` mit Banner und Auftrag, WebSocket online.
