# Einsatzleiter-Hilfswerkzeug – Konzept „Tenant-Sichtbarkeit & UX-Erweiterungen" & Claude-Code-Plan

**Ziel:** (1) Die nach Umsetzung des Multi-Tenancy-Konzepts verbliebenen **Sichtbarkeits-Lecks vollständig schließen** – Benutzer einer Organisation sehen in sämtlichen Listen, Übersichten und Dashboards ausschließlich Daten der eigenen Org. (2) Neun **funktionale Erweiterungen** umsetzen (Personen-Schnellanlage, verschiebbare Spalten, Auftrag-Statusabfrage, Spaltentypen, QR-PIN, Karten-Aktionen, Benutzerprofil, Funkjournal-Ausbau nach österreichischer Funkordnung).

**Stand:** 12.06.2026 · Baut auf `docs/multi-tenancy-konzept.md` (umgesetzt) auf

---

## 1. Ist-Analyse: Warum die Lecks trotz Multi-Tenancy bestehen

**Befund:** Die Zugriffsprüfung (Defense-Linie 1, `core/permissions.py`) greift – fremde Einsätze sind nicht öffenbar. Aber **Listen- und Aggregat-Endpoints liefern weiterhin org-übergreifende Daten**: Einsatzübersicht, Medien, Statistik-Dashboard, Fahrzeuge, Mitglieder, Qualifikationen, Auftrags-/Meldungsvorlagen, Lage-Hinweise, Defaultmeldungen, Benutzer, Geräte-Logins, Push, API-Keys, Lagekarte-Tokens, Audit-Logs.

Das automatische ORM-Scoping (Defense-Linie 3, `do_orm_execute`-Listener + `TenantScoped`-Mixin) hat **vier systematische blinde Flecken**, die exakt die gemeldete Leck-Liste erklären:

| # | Ursachenkategorie | Betroffene Entitäten | Warum der Listener nicht greift |
|---|---|---|---|
| A | **Abweichender Spaltenname** | `Incident` (`primary_org_id` statt `org_id`), `VehicleMaster` (`dept_id`) | `with_loader_criteria(TenantScoped, …)` filtert nur Modelle mit dem Mixin und der Spalte `org_id`. Incident und VehicleMaster tragen das Mixin nicht → Einsatzübersicht und Fahrzeugliste sind ungefiltert. Medienlisten hängen über `incident_id` an Incident → ebenfalls offen. |
| B | **Nullable `org_id`** | `User` (NULL = system_admin), `AuditLog` (NULL = systemweit) | Mixin verlangt `NOT NULL` – diese Modelle wurden bewusst ausgenommen und nie nachgezogen → Benutzerliste und Audit-Log zeigen alle Orgs. |
| C | **Raw-SQL / Core-Selects** | Statistik-Dashboard (`ui_stats.py`), Archiv (`ui_archive.py`), Zähler-Abfragen | Der ORM-Listener filtert nur ORM-Selects. `text()`-Queries und `select()` ohne ORM-Entität laufen ungefiltert durch. |
| D | **Fehlender Org-Kontext im Request** | Diverse Listen-Endpoints, Geräte-Logins, Push-Verwaltung, API-Keys, Lagekarte-Tokens | Endpoints ohne `CurrentOrg`-Dependency setzen `session.info["current_org_id"]` nie → der Listener läuft als **No-Op** durch, statt zu blockieren. Das ist ein **Fail-Open-Design** und der gefährlichste Punkt. |

Hinzu kommt: `Member`, `Qualification`, `TaskSuggestion`, `MessageSuggestion`, `LageHint`, `DefaultMessage` tragen zwar `org_id`, aber sofern das Mixin nicht auf allen Modellen nachgerüstet wurde bzw. Kategorie D zutrifft, bleiben auch sie sichtbar.

**Konsequenz:** Kein Einzelflicken pro Endpoint, sondern drei strukturelle Korrekturen – Fail-Closed, Alias-Scoping, Aggregat-Scoping – plus eine **Sichtbarkeits-Testmatrix**, die Lecks dauerhaft als CI-Gate verhindert.

---

## 2. Zielbild Sichtbarkeit

### 2.1 Fail-Closed statt Fail-Open

1. **Middleware setzt den Org-Kontext immer.** Eine Request-Middleware (bzw. globale Dependency) löst `current_org_id` aus Session-User oder API-Key auf, bevor irgendein Router-Code läuft – nicht erst, wenn ein Endpoint `CurrentOrg` deklariert. System-Admin ohne aktiven Org-Wechsel ⇒ Kontext `SYSTEM` (auditiert).
2. **Listener wirft statt zu schweigen.** Selektiert Code ein tenant-pflichtiges Modell ohne gesetzten Org-Kontext und ohne `include_all_tenants=True`, wird eine `TenantContextMissing`-Exception geworfen (HTTP 500 + Server-Log). Ein vergessener Filter fällt damit im ersten Test auf, statt still Daten zu leaken.

```python
@event.listens_for(Session, "do_orm_execute")
def _add_tenant_filter(execute_state):
    if not execute_state.is_select:
        return
    opts = execute_state.execution_options
    if opts.get("include_all_tenants"):
        return
    org_id = execute_state.session.info.get("current_org_id", _MISSING)
    if org_id is _MISSING and _touches_tenant_models(execute_state.statement):
        raise TenantContextMissing(...)   # Fail-Closed
    if org_id not in (None, _MISSING):    # None = SYSTEM-Kontext (nur system_admin)
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(TenantScoped, lambda c: c.org_id == org_id, include_aliases=True),
            with_loader_criteria(Incident, lambda c: or_(
                c.primary_org_id == org_id,
                c.id.in_(select(IncidentOrg.incident_id).where(IncidentOrg.org_id == org_id)),
            ), include_aliases=True),
            with_loader_criteria(VehicleMaster, lambda c: c.dept_id == org_id, include_aliases=True),
            with_loader_criteria(User, lambda c: c.org_id == org_id, include_aliases=True),
            with_loader_criteria(AuditLog, lambda c: c.org_id == org_id, include_aliases=True),
        )
```

### 2.2 Sichtbarkeitsregeln je Entität

| Bereich | Sichtbarkeit für Org-Benutzer | Mechanik |
|---|---|---|
| **Einsatzübersicht** | `primary_org_id == org` **oder** angenommene Einladung (`IncidentOrg`) | Incident-Loader-Criteria (s. o.); gilt automatisch auch für Archiv-Listen, sofern ORM |
| **Medien** (Task/Message/Person/Lage) | nur Medien sichtbarer Einsätze | Media-Modelle erhalten zusätzlich denormalisiertes `org_id` (TenantScoped) – robuster als Join-Filterung und konsistent mit der Pfadstruktur `{org_id}/{incident_id}/…` aus PR 6 des Vorkonzepts |
| **Statistik-Dashboard** | nur eigene Einsätze/Aufträge/Meldungen; Kooperationseinsätze zählen bei der führenden Org, eingeladene Orgs sehen sie in einer separaten Kennzahl „Unterstützungseinsätze" | alle Aggregat-Queries in `ui_stats.py` erhalten expliziten `WHERE`-Org-Filter (Kategorie C ist ORM-Listener-resistent) |
| **Fahrzeuge** | `dept_id == org` | Loader-Criteria + Sweep `ui_settings.py` |
| **Mitglieder, Qualifikationen, Auftragsvorlagen, Meldungsvorlagen, Lage-Hinweise, Defaultmeldungen** | `org_id == org` | TenantScoped-Mixin flächendeckend verifizieren/nachrüsten |
| **Benutzer** | nur Benutzer der eigenen Org; `system_admin`-Konten (org_id NULL) erscheinen **nie** in Org-Listen | Loader-Criteria User (NULL fällt automatisch raus) |
| **Geräte-Logins, Push (DeviceToken/FcmToken/PushSubscription)** | nur eigene Org | org-Bindung läuft teils über User → nach User-Scoping verifizieren, direkte `org_id` ergänzen wo nötig |
| **API-Keys, Lagekarte-Tokens, SMS-Gateway** | nur eigene Org | ApiKey/SmsGatewayToken sind org-gebunden → Mixin anwenden; `IncidentToken` erbt Sichtbarkeit über Incident, Verwaltungsliste explizit scopen |
| **Audit-Log** | `org_id == org`; systemweite Einträge (NULL) nur system_admin | Loader-Criteria AuditLog |
| **Push-Nachrichten-Versand & WebSocket** | Push wird **ausschließlich innerhalb der eigenen Org** versendet; bei Kooperationseinsätzen versendet jede beteiligte Org nur an die eigenen Benutzer (Entscheidung 12.06.2026) | `org:{id}`-Kanäle (PR 7 Vorkonzept) verifizieren; Versandlisten strikt org-scopen |

### 2.3 Sichtbarkeits-Testmatrix als CI-Gate

Die bestehende Isolationstest-Fixture („zwei Orgs") wird um **Listen-Assertions** erweitert: Bisher wurde geprüft, dass Org B auf Org-A-Ressourcen 403/404 erhält. Neu wird für **jeden Listen-/Übersichts-/Dashboard-Endpoint** geprüft, dass **kein Org-A-Identifikator im Response-Body von Org B auftaucht** (IDs, Namen, Kennzeichen mit eindeutigen Test-Markern wie `ORGA-MARKER-…`). Parametrisiert über alle 17 Router; rote Tests blockieren den Merge.

---

## 3. Funktionale Erweiterungen – Spezifikation

### E1 – Personen-Schnellanlage im Einsatz

- Wizard-Schritt 1 zeigt nur **Name** (Pflichtfeld) und einen sofort aktiven Button **„Speichern"** sowie **„Weiter (Details erfassen)"**.
- „Speichern" legt die Person unmittelbar an (alle übrigen Felder NULL/Default) und schließt den Wizard; die restlichen Schritte (Status, Verletzungsmuster, Verbleib, Medien …) bleiben über „Bearbeiten" jederzeit nachtragbar.
- Backend: Pflichtfeld-Validierung auf `name` reduzieren; Schema-Defaults prüfen (NOT-NULL-Spalten ggf. nullable machen oder Defaults setzen, Alembic-Migration).
- Akzeptanz: Person mit nur einem Namen in < 3 Sekunden / 2 Klicks angelegt; Detail-Nacherfassung verlustfrei möglich.

### E2 – Verschiebbare Spalten (Desktop)

- Spalten im Einsatz-Board per **Drag & Drop am Spaltenkopf** umsortierbar (SortableJS o. ä.; nur Desktop-Viewport, Touch-Boards unverändert).
- Persistenz: Spaltenmodell erhält `sort_order` (Integer); Reihenfolge gilt **je Einsatz für alle Benutzer gleich** – jeder hat dieselbe Sicht (Entscheidung 12.06.2026).
- Die `sort_order` ist die **einzige Quelle der Spaltenreihenfolge** und bestimmt damit auch die Reihenfolge in den **mobilen Dropdown-Menüs** (Spaltenauswahl/Navigation auf Mobilgeräten folgt automatisch der Desktop-Sortierung).
- Änderung wird per WebSocket an alle verbundenen Clients gebroadcastet (org-gescopter Kanal), optimistisches UI mit Server-Bestätigung.
- Akzeptanz: Reihenfolge überlebt Reload, ist auf zweitem Client binnen 1 s sichtbar und identisch in den mobilen Dropdowns.

### E3 – Auftrag-Statusabfrage (Reminder-Popup)

- Beim Anlegen/Bearbeiten eines Auftrags: Checkbox **„Statusabfrage"** (Default aus) + Minutenfeld (Default **10**, frei änderbar, min. 1).
- Nach Ablauf erscheint **beim Ersteller des Auftrags** (Entscheidung 12.06.2026) ein Popup: Auftragstitel, Einheit, vergangene Zeit, Aktionen **„Status setzen"** (öffnet Auftrag) und **„Erneut in X min"** (Snooze, Default = eingestellte Zeit).
- Die Abfrage wiederholt sich im eingestellten Intervall und **endet ausschließlich, wenn der Auftrag auf „erledigt" gesetzt wird** (Entscheidung 12.06.2026) – Zwischenstatuswechsel beenden sie nicht. Deaktivieren ist nur über die Checkbox im Auftrag-Bearbeiten-Dialog möglich.
- Technik: Felder `status_check_enabled`, `status_check_minutes`, `status_check_next_at`, `created_by_user_id` am Auftrag. Server-Scheduler (bestehende Scheduler-Infrastruktur aus `autoclose.py` mitnutzen) prüft minütlich fällige Abfragen und sendet ein WS-Event auf den **User-Kanal des Erstellers**; ist kein Client verbunden, Fallback Web-Push. Quelle der Wahrheit ist der Server – Client-Timer allein wären nach Reload/Gerätewechsel verloren.
- Akzeptanz: Popup erscheint ±30 s genau; nur beim Ersteller; Snooze wirkt serverseitig; Abfrage endet bei „erledigt", nicht bei Zwischenstatus.

### E4 – Spalte „Nachalarmierung" entfernen

- Spaltentyp „Nachalarmierung" wird aus UI, Seeds und Code entfernt (expand→contract: erst ausblenden/deprecaten, im Folge-Release Code löschen).
- **Bestandsdaten werden ignoriert** (Entscheidung 12.06.2026) – keine Migration in andere Spalten; vorhandene Einträge entfallen mit der Spalte.
- Akzeptanz: Weder neue noch bestehende Einsätze zeigen die Spalte; keine toten Codepfade.

### E5 – Spaltentyp bei Spaltenanlage wählbar

- Beim Hinzufügen einer Spalte wählt der Benutzer den Typ: **Einheiten / Personen / Aufträge / Hinweise** (`column_kind`-Enum am Spaltenmodell).
- Das **„+"-Element im Spaltenkopf** richtet sich nach dem Typ: + Einheit → Einheiten-Dialog, + Person → Schnellanlage-Wizard (E1), + Auftrag → Auftragsdialog inkl. Auftragsvorlagen, + Hinweis → Hinweisdialog inkl. Lage-Hinweisen.
- Migration: bestehende Spalten erhalten `column_kind` anhand ihres bisherigen impliziten Typs; unklare Fälle → „Hinweise".
- Akzeptanz: Jede neue Spalte hat genau einen Typ; das +-Element öffnet typgerecht den richtigen Dialog mit den org-eigenen Vorschlägen.

### E6 – QR-Code-PIN für den Einsatzlink

- Der QR-Code auf den Einsatz führt nach dem Scan auf eine **PIN-Abfrageseite** (4-stellig). Erst nach korrekter Eingabe wird die Einsatzansicht freigegeben.
- **PIN-Quellen:** `OrgSettings.qr_pin_hash` (Org-Default, von org_admin gepflegt) – kann **je Einsatz** durch `Incident.qr_pin_hash` überschrieben werden (änderbar im Einsatz durch Einsatzleitung/org_admin). Der QR-Code selbst bleibt unverändert – der PIN steht **nie in der URL**.
- **Härtung:** PIN gehasht gespeichert (bcrypt); Brute-Force-Schutz: 5 Fehlversuche je IP+Einsatz → 15 min Sperre (bestehende `rate_limit.py`-Infrastruktur); nach Erfolg signiertes, einsatzgebundenes Session-Cookie (Gültigkeit bis Einsatzende + 24 h). PIN-Wechsel im Einsatz invalidiert bestehende Cookies.
- **Scope (Entscheidung 12.06.2026):** nur der QR-Einsatzlink (`public.py`-Route); Lagekarte-Token-Links bleiben unverändert.
- Akzeptanz: Ohne PIN keine Einsatzdaten (auch nicht per API-Probing); PIN-Wechsel sperrt alte Sitzungen; Sperre nach 5 Fehlversuchen greift.

### E7 – Aufträge/Meldungen direkt von der Einheiten-Karte im Board

- Auf der **Karte einer Einheit** (Board und Lagekarten-Popup) erscheinen die Aktionen **„+ Auftrag"** und **„+ Meldung"**.
- Es öffnen sich dieselben Dialoge wie über das Spalten-+-Element (E5) – inklusive der org-eigenen Auftrags-/Meldungsvorlagen –, **vorbefüllt** mit der Einheit und (auf der Lagekarte) deren Position.
- Akzeptanz: Auftrag/Meldung von der Einheiten-Karte aus in ≤ 2 Klicks bis zum Vorlagen-Vorschlag; Ergebnis identisch zum Spalten-Workflow.

### E8 – Benutzerprofil (Selbstverwaltung)

- Klick auf den Benutzernamen im Header öffnet **„Mein Profil"**: Name, E-Mail-Adresse, Telefonnummer, Passwort, Profilbild.
- **Passwortwechsel** nur mit Eingabe des aktuellen Passworts. **E-Mail-Änderung ausschließlich via Bestätigungslink** an die neue Adresse (Entscheidung 12.06.2026): Die neue Adresse wird erst nach Klick auf den Link aktiv, bis dahin bleibt die alte gültig; Link-Gültigkeit 24 h, Anstoß erfordert zusätzlich das aktuelle Passwort.
- **Profilbild:** Upload mit quadratischem Zuschnitt, serverseitig auf 256 px verkleinert (Pillow-Pipeline aus `media_service.py` wiederverwenden), max. 2 MB Rohgröße, zählt auf die Org-Speicher-Quota; Anzeige im Header und künftig in Journalen/Audit sinnvoll.
- Alle Änderungen werden org-gescoped ins Audit-Log geschrieben.
- Akzeptanz: Benutzer ändert alle fünf Attribute ohne Admin; falsches Altpasswort blockiert; org_admin-Benutzerverwaltung bleibt unberührt.

### E9 – Funkjournal: Ausbau Großschadenslage + Einbau in den Normaleinsatz

**Fachlicher Rahmen (österreichische Funkvorschriften):** Das ÖBFV-Fachschriftenheft 5 „Feuerwehrfunk" und die Landes-Funksprechordnungen (z. B. LFV Steiermark 2022, OÖLFV AID Funkordnung) unterscheiden als Nachrichtenarten das **Gespräch** (Standard-Nachrichtenaustausch, formlos), den **Spruch** (förmliche Nachricht: wird **wörtlich niedergeschrieben**, mit Absender, Empfänger, Aufgabezeit und laufender Nummer dokumentiert und vom Empfänger **quittiert**) und die **Durchsage** (an mehrere Stellen, z. B. Sammelruf). Befehle und dringende Nachrichten haben Vorrang und sind vom Empfänger zur Bestätigung zu wiederholen. Diese Systematik wird wie folgt auf die geforderten Funktypen abgebildet:

| Funktyp (Button) | Bedeutung im Journal | Besondere Logik |
|---|---|---|
| **Befehl** | Anweisung mit Vorrang | Kennzeichnung „Vorrang"; Checkbox „vom Empfänger wiederholt" (Bestätigungspflicht); Schnellaktion „→ Auftrag erzeugen" (übernimmt Text, Einheit, Zeit) |
| **Frage** | erwartet Antwort | Status „offen", bis eine Antwort-Zeile verknüpft wird; offene Fragen als Badge am Journal |
| **Meldung** | Standard-Lagemeldung | Default-Typ; Checkbox **„lagerelevant"** (s. u.) |
| **Spruch** | förmliche, wörtlich dokumentierte Nachricht | Pflichtfelder: Absender, Empfänger, wörtlicher Text; automatische **laufende Spruchnummer je Einsatz**; Feld „Aufgabezeit" (Default jetzt); Quittiert-Haken mit Zeitstempel – unquittierte Sprüche als Badge |

**UI-Änderungen (Großschadenslage):**
- **Richtung** Eingehend / Ausgehend / Intern: drei **Buttons** statt Dropdown (Toggle-Gruppe, Tastatur 1/2/3) – beschleunigt die Erfassung im Funkbetrieb erheblich.
- **Funktyp** Befehl / Frage / Meldung / Spruch: ebenfalls Button-Gruppe (Tastatur Q/W/E/R), Default „Meldung".
- Bestehende Journalzeilen erhalten per Migration Richtung wie bisher und Funktyp „Meldung".

**Einbau in den Normaleinsatz – Nutzungsvorschlag:**
- Das Funkjournal wird als **Tab „Funkjournal"** neben den Meldungen in jeden Einsatz eingebaut (gleiche Komponente, gleiche Buttons – kein Parallelcode zur Großschadenslage).
- **Sinnvolle Nutzung im Normaleinsatz:** Das Journal dient als schlankes **Einsatztagebuch (ETB light)** für den Funkdienst/Fahrzeugfunker: Jede Funkkommunikation mit Leitstelle, Einsatzleitung und Trupps wird mit einem Klick (Richtung + Typ + Kurztext) festgehalten. Aus **Befehlen** entstehen per Schnellaktion direkt Aufträge im Board (Rückverweis Journal ↔ Auftrag), **Fragen** bleiben sichtbar offen, bis sie beantwortet sind, und **Sprüche** (z. B. Lagemeldungen an die LWZ, Nachforderungen) sind revisionssicher mit Spruchnummer und Quittierung dokumentiert. Damit ist das Journal nach dem Einsatz zugleich die chronologische Grundlage für Einsatzbericht und Nachbesprechung – ohne separates Papierprotokoll.
- **Lagerelevante Meldungen:** Wird eine Journalzeile als **„lagerelevant"** markiert, erzeugt sie automatisch einen **Hinweis im Board** (hervorgehoben, mit Quell-Verweis ins Journal). Der Hinweis trägt den Status **„unbestätigt"**, bis ihn ein Benutzer per Klick **als gelesen bestätigt** – **eine Bestätigung genügt** (Entscheidung 12.06.2026), protokolliert mit Benutzer und Zeitstempel (Read-Receipt im Hinweis sichtbar, Eintrag im Audit-Log). Unbestätigte lagerelevante Hinweise werden im Board-Header gezählt (Badge), damit nichts untergeht.
- Akzeptanz: Erfassung einer Journalzeile ohne Maus (nur Tastatur) in < 5 s; Spruchnummern lückenlos je Einsatz; lagerelevante Meldung erscheint binnen 1 s als unbestätigter Hinweis bei allen Clients; Bestätigung mit Name/Zeit nachvollziehbar.

---

## 4. Implementierungsplan für Claude Code (12 PRs)

Reihenfolge: **Sicherheitslecks zuerst** (Phase A), dann UX (Phase B), dann Funkjournal (Phase C). Jeder PR eigenständig deploybar, Migrationen expand→migrate→contract. Vor PR 1: Branch `feature/visibility-ux`, Staging-Kopie der Produktiv-DB.

### Phase A – Sichtbarkeit dichtmachen (🔴 sicherheitsrelevant)

**PR 1 – Fail-Closed-Tenant-Kontext & Sichtbarkeits-Testmatrix**
- Request-Middleware setzt `current_org_id` für **jeden** Request (Session-User / API-Key / SYSTEM)
- Listener-Härtung: `TenantContextMissing`-Exception bei tenant-pflichtigem Select ohne Kontext (Fail-Closed)
- Loader-Criteria-Registry für Sondermodelle: `Incident` (primary_org **oder** IncidentOrg), `VehicleMaster` (dept_id), `User` (org_id, NULL ausgeblendet), `AuditLog` (org_id)
- Isolationstest-Fixture erweitern: **Listen-Assertions** („Org-A-Marker darf in keinem Org-B-Response-Body auftauchen"), parametrisiert über alle GET-Listen-Endpoints
- *Akzeptanz:* Vorsätzlich eingebauter ungefilterter Endpoint schlägt im Test fehl statt Daten zu liefern.

**PR 2 – Einsätze, Medien, Statistik scopen**
- Einsatzübersicht & Archiv (`ui_archive.py`): Incident-Criteria wirksam, Raw-SQL-Stellen auf expliziten Org-Filter umstellen
- Medien: `org_id` (TenantScoped) auf TaskMedia/MessageMedia/PersonMedia/Lage-Medien denormalisieren; **Backfill aus Incident.primary_org_id**, danach NOT NULL; Medien-Listen-Endpoints prüfen
- Statistik-Dashboard (`ui_stats.py`): jede Aggregat-Query org-filtern; Kennzahl „Unterstützungseinsätze" für angenommene Einladungen
- *Akzeptanz:* Org-B-Benutzer sieht in Übersicht, Archiv, Medien und Statistik ausschließlich Org-B-Daten; Kooperationseinsatz erscheint bei beiden korrekt.

**PR 3 – Verwaltungs- und Stammdatenlisten scopen**
- Sweep über `ui_settings.py`, `ui_admin.py` und betroffene Router: Fahrzeuge, Mitglieder, Qualifikationen, Auftragsvorlagen, Meldungsvorlagen, Lage-Hinweise, Defaultmeldungen, Benutzer, Geräte-Logins (`DeviceToken`/`FcmToken`/`PushSubscription` – direkte org_id ergänzen wo nur User-indirekt), API-Keys, Lagekarte-Tokens (`IncidentToken`-Verwaltungsliste), SMS-Gateway, Audit-Log-Ansicht
- Push-Versand & WebSocket-Broadcast-Empfängerlisten verifizieren (`ws.py`, `broadcast.py`)
- Testmatrix aus PR 1 um alle diese Endpoints erweitern
- *Akzeptanz:* Komplette Leck-Liste aus Abschnitt 1 ist in der CI-Matrix grün.

### Phase B – UX-Erweiterungen

**PR 4 – Personen-Schnellanlage (E1)**
- Wizard-Umbau, Validierung auf `name` reduzieren, Alembic für nullable Felder/Defaults

**PR 5 – Spaltentypen & Nachalarmierung (E4 + E5)**
- `column_kind`-Enum + Migration der Bestandsspalten; typabhängiges +-Element mit org-eigenen Vorschlagslisten
- Spaltentyp „Nachalarmierung" deprecaten und entfernen; **Bestandsdaten werden ignoriert** (keine Daten-Migration); Code-/Seed-Entfernung im contract-Schritt

**PR 6 – Verschiebbare Spalten (E2)**
- `sort_order` + Drag&Drop (Desktop) + WS-Broadcast; Reihenfolge je Einsatz für alle Benutzer gleich, mobile Dropdown-Menüs übernehmen dieselbe Sortierung

**PR 7 – Auftrag-Statusabfrage (E3)**
- Auftragsfelder + Checkbox/Minutenfeld im Dialog; Scheduler-Job; WS-Event auf User-Kanal des Erstellers + Web-Push-Fallback; Snooze serverseitig; Abfrage endet ausschließlich bei Status „erledigt"

**PR 8 – QR-PIN (E6)**
- `OrgSettings.qr_pin_hash`, `Incident.qr_pin_hash`; PIN-Seite vor `public.py`-Einsatzroute; bcrypt, Rate-Limit-Sperre, signiertes Einsatz-Cookie, Cookie-Invalidierung bei PIN-Wechsel; org_admin-UI (Org-Default) + Einsatz-UI (Override)

**PR 9 – Karten-Aktionen auf Einheiten (E7)**
- „+ Auftrag"/„+ Meldung" auf Einheiten-Karte in Board & Lagekarte; Dialoge aus E5 wiederverwenden, Vorbefüllung Einheit/Position

**PR 10 – Benutzerprofil (E8)**
- `/profile`-Seite (Name, Mail, Telefon, Passwort, Profilbild); Re-Auth-Pflicht; E-Mail-Wechsel nur via Bestätigungslink (Token, 24 h gültig); Profilbild-Pipeline + Quota-Anrechnung; Audit-Einträge

### Phase C – Funkjournal

**PR 11 – Funkjournal-Ausbau Großschadenslage (E9 Teil 1)**
- Richtung & Funktyp als Button-Gruppen mit Tastatur-Shortcuts; Datenmodell: `radio_type`-Enum, Spruchfelder (Absender, Empfänger, Aufgabezeit, lfd. Nummer je Einsatz, quittiert_at/von), Frage-Antwort-Verknüpfung, Befehl→Auftrag-Schnellaktion
- Migration: Bestand → Funktyp „Meldung"

**PR 12 – Funkjournal im Normaleinsatz + Lagerelevanz (E9 Teil 2)**
- Journal-Komponente als Tab in den Normaleinsatz (gemeinsame Komponente, kein Fork)
- „lagerelevant"-Flag → automatischer Board-Hinweis mit Status „unbestätigt", Lesebestätigung (Benutzer + Zeitstempel, Audit), Badge für unbestätigte Hinweise; WS-Broadcast org-gescoped
- Doku: Wiki-Kapitel „Funkjournal nach österreichischer Funkordnung" + CHANGELOG

### Hinweise für die Claude-Code-Sessions

- **Ein PR pro Session**, Start jeweils: „Lies `docs/sichtbarkeit-und-ux-konzept.md` Abschnitt X und implementiere PR n. Führe vor dem Commit `pytest` aus."
- Konzeptdatei als `docs/sichtbarkeit-und-ux-konzept.md` ins Repo legen.
- Die **Sichtbarkeits-Testmatrix aus PR 1 in jedem Folge-PR** um neue Endpoints erweitern lassen – explizit in jeden Prompt schreiben.
- Nach PR 2 (Medien-Backfill) Migrationstest gegen Kopie der Produktiv-DB, bevor weitergebaut wird.
- Phase A vor Phase B mergen und deployen – die Lecks sind produktiv und haben Vorrang vor jedem Feature.

---

## 5. Entschieden / Offen

**Entschieden (12.06.2026):**

1. Konzept im gleichen Stil **mit PR-Plan** für Claude-Code-Sessions.
2. Status-Popup (E3) erscheint **nur beim Ersteller des Auftrags** und endet **ausschließlich**, wenn der Auftrag auf **„erledigt"** gesetzt wird.
3. QR-PIN (E6) schützt **nur den QR-Einsatzlink**; Lagekarte-Token-Links bleiben unverändert.
4. **Push-Nachrichten** werden immer nur **innerhalb der eigenen Organisation** versendet.
5. **Spaltenreihenfolge (E2)** ist **für alle Benutzer gleich** (je Einsatz); sie bestimmt auch die Reihenfolge in den **mobilen Dropdown-Menüs**.
6. **Nachalarmierung-Bestandsdaten (E4)** werden **ignoriert** – keine Daten-Migration.
7. **E-Mail-Änderung (E8)** ist **nur via Bestätigungslink** möglich.
8. **Lagerelevante Hinweise (E9):** **eine** Lesebestätigung genügt.

**Noch offen:** keine.
