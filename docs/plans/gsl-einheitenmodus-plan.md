# GSL-Einheitenmodus für Fahrzeug-Tablets – Implementierungsplan

Stand: 2026-10-09 · Basis: `main` @ `a9bb2705` (Einsatzcockpit) und `main` (Einsatzcockpit-Android)
Status: **Phase 1 umgesetzt (2026-10-09)**, Phase 2/3 offen · Entscheidungen E1–E7 am 2026-10-09 getroffen (Abschnitt 13) · Umsetzungsstand: Abschnitt 14

> **Leitlinie:** Der Einheitsführer erkennt auf dem Tablet ohne Suchen sofort, welchen Einsatz
> er bearbeiten soll, welche Informationen vorliegen und wie er Status, Lagemeldung oder Fotos
> mit minimalem Aufwand an die Einsatzleitung übermittelt.
> **Grundprinzip:** Die Einsatzleitung disponiert und koordiniert, die Einheit erhält Aufträge
> und meldet Ergebnisse zurück. Der Einheitenstatus hebt die Einsatzstelle höchstens auf „in Arbeit“
> (E3); Abschluss und Abbruch einer Einsatzstelle entscheidet ausschließlich die Führung.
> Kritische Nachrichten gehen immer über Funk (E6).

Legende in allen Tabellen:

| Kürzel | Bedeutung |
|---|---|
| ✅ **vorhanden** | bereits implementiert, wird unverändert genutzt |
| 🔧 **anpassen** | vorhanden, muss erweitert oder umgebaut werden |
| 🆕 **neu** | muss neu entwickelt werden |

---

## Inhalt

1. [Ist-Analyse](#1-ist-analyse)
2. [Gap-Analyse](#2-gap-analyse)
3. [Architektur](#3-architektur)
4. [Datenmodell](#4-datenmodell)
5. [Backend](#5-backend)
6. [Frontend (Tablet + Führung)](#6-frontend)
7. [Android-App](#7-android-app)
8. [MCP](#8-mcp)
9. [Sicherheit, Audit, Mandantentrennung](#9-sicherheit)
10. [Tests](#10-tests)
11. [Migration und Rückwärtskompatibilität](#11-migration)
12. [Umsetzungsphasen und Arbeitspakete](#12-umsetzungsphasen)
13. [Entscheidungen](#13-entscheidungen)

---

## 1. Ist-Analyse

### 1.1 Datenmodell der Großschadenslage (`app/models/major_incident.py`)

| Element | Zweck heute | Bewertung |
|---|---|---|
| `MajorIncident` | Lage, `status` standby/active/closed, `is_exercise` | ✅ |
| `IncidentSite` | Einsatzstelle: Adresse, `lat`/`lng`, `einsatzgrund` (Stichwort/Meldung), `phase` (`SitePhase`), `priority` (`SitePriority` 1–4), `sort_index`, `naechste_lagemeldung_at`, `incident_id` (→ Einsatz → `objekt_links`) | ✅ Phase: automatische Anhebung auf `in_arbeit` durch die Einheit (E3), alles andere bleibt Führungsentscheidung |
| `LageEinheit` | **Einzige Ressourcen-Registry** (`resource_service.py`-Docstring). `vehicle_id` → `VehicleMaster`, `status` angefordert/bereitgestellt/im_einsatz/abgerueckt, `incident_site_id` (Zeiger auf aktuelle Vor-Ort-Stelle), `sector_id`, Führer-Historie `LageEinheitLeader` | ✅ Anker für „meine Einheit“ |
| `EinheitSiteDispatch` | Mehrfach-Disposition Einheit↔Stelle: `dispatched_at`, `vor_ort_at`, `withdrawn_at`, `dispatched_by`, `author_name` | 🔧 **wird zum „Auftrag“** – fehlt: Auftragstext, Einheitenstatus, Reihenfolge, Version |
| `SiteResourceAssignment` | Ältere freie Ressourcenzuordnung (vehicle/member/free_text) | ✅ unverändert, nicht für den Einheitenmodus verwenden |
| `SiteLogEntry` | Chronik der Stelle; `kind` note/lagemeldung/massnahmen + System-Kinds status/prio/resource/media (`SITE_LOG_KIND_LABEL`, `SITE_LOG_USER_KINDS`, `SITE_LOG_RESET_KINDS`) | 🔧 Bezug zur Einheit fehlt |
| `SiteMedia` | Fotos je Stelle (`media_type` image/pdf/video, faktisch nur image) | 🔧 Bezug zur Einheit, Kommentar fehlen |
| `CommLogEntry` | Funkjournal: `direction` in/out/int, `is_request`, `handled`, `auto_kind` (`lagemeldung_faellig`), `related_site_id` | 🔧 wird Kommunikationskanal Einheit↔Führung; Einheitenbezug, Quittierung, Antwortbezug fehlen |
| `LageJournalEntry` | Stab-Einsatzjournal (Kategorien entscheidung/anweisung/meldung/lagemeldung/sonstiges; `resource_service._journal` schreibt zusätzlich `ressource`) | ✅ für führungsrelevante Ereignisse mitnutzen |
| `VehiclePosition` | GPS-Historie je Fahrzeug und Lage | ✅ eigene Position |
| `CrossSiteMarker` | Übergreifende Lageinfos (u. a. `strasse_gesperrt`) | ✅ Gefahren in der Umgebung |

Die GSL-Tabellen sind **nicht** `TenantScoped` (`app/core/tenant.py::_TENANT_TABLE_NAMES` enthält sie nicht).
Scoping erfolgt in jedem Router über `_lage_or_404()` + `_check_org_access()`
(`app/routers/ui_major_incident.py:187/206`). Neue Abfragen im Einheitenmodus müssen dieses
Muster explizit fortführen.

### 1.2 Disposition und Lagemeldungs-Regelkreis

- `app/services/resource_service.py`
  - ✅ `dispatch_to_site()` (Z. 229) – legt `EinheitSiteDispatch` an, setzt Einheit auf `im_einsatz`, Journal „DISPONIERT“.
  - ✅ `set_vor_ort_at_site()` (Z. 280) – setzt `vor_ort_at`, `LageEinheit.incident_site_id`, erkennt Vor-Ort-Konflikt an anderer Stelle.
  - ✅ `resolve_vor_ort_conflict()` (Z. 347) – zieht von alter Stelle ab (Führungsentscheidung „Verlegung“).
  - ✅ `withdraw_from_site()` (Z. 385) – `withdrawn_at`.
  - ✅ `get_dispatch_counts_for_sites()` (Z. 447) – Zähler alarmiert/vor Ort für Board-Karten.
  - `withdrawn_at.is_(None)` als Aktiv-Kriterium kommt an **11 Stellen** vor (`resource_service.py`, `lagemeldung_service.py`, `gsl_live_service.py`, Template `druck_bericht.html`) → 🔧 wird zentralisiert (siehe 4.2).
- `app/services/lagemeldung_service.py`: ✅ `ensure_timer()`, `register_lagemeldung()` (setzt Timer zurück, schließt `lagemeldung_faellig`-Aufträge), `has_active_resource()`.
- `app/services/gsl_lagemeldung_reminder.py`: ✅ Reminder-Loop erzeugt bei Fälligkeit `CommLogEntry(auto_kind="lagemeldung_faellig")` – heute nur für die Führung sichtbar, **keine Zustellung an die Einheit**.

### 1.3 Führungs-Routen (`app/routers/ui_major_incident.py`, 5 196 Zeilen)

| Route | Zeile | Relevanz |
|---|---|---|
| `POST /lage/{id}/stellen/{site}/einheit-disponieren` | 1048 | ✅ Zuweisung; 🔧 Auftragstext + Push ergänzen |
| `POST …/einheit-vor-ort` | 1085 | ✅ Führung kann weiter „vor Ort“ setzen |
| `POST …/einheit-abziehen` | 1162 | ✅ = „Zuteilung zurückziehen“; 🔧 Push/WS an Einheit |
| `POST …/log` | 1267 | 🔧 Logik inline im Router → in Service extrahieren |
| `POST …/medien` | 1554 | 🔧 Logik inline, **kein WS-Broadcast** nach Upload (Board-Fotozähler aktualisiert sich nicht) |
| `POST /lage/{id}/funkjournal` | 2915 | 🔧 inline, **kein WS-Broadcast** |
| `POST /lage/{id}/funkjournal/{e}/erledigt` | 2995 | 🔧 Toggle `handled`, kein Broadcast |
| `POST …/phase`, `…/prio` | 593/652 | ✅ bleiben ausschließlich Führungsaktionen |

Rollenprüfung: `require_role("incident_leader","admin","org_admin","recorder"[,"readonly"])`
(`app/core/permissions.py:46`). Geräte-Benutzer (`User.is_device`) bekommen bei der Anlage frei wählbare
Rollen (`ui_admin.py` ~Z. 3080 ff.). **Ein Tablet mit `recorder`-Rolle kann heute jede Einsatzstelle jeder
Lage seiner Org ändern** – inkl. Phase, Priorität, Disposition. Das widerspricht Abschnitt 10 der
Anforderung und muss im Einheitenmodus geschlossen werden (siehe 9.2).

### 1.4 Geräte-Pairing und Sessions

| Baustein | Ort | Stand |
|---|---|---|
| `DeviceToken` (`label`, `token_hash`, `user_id` → eigener Geräte-User, `vehicle_master_id`, `revoked_at`, `duty_active`, `last_lat/lng`, Pairing-PIN) | `app/models/user.py:154` | ✅ |
| Geräteanlage mit Fahrzeug und Rollen | `ui_admin.py` (Geräte-Login, ~Z. 3014–3130) | ✅ |
| Fahrzeug nachträglich umhängen | `POST /admin/geraete-login/{id}/fahrzeug` (`ui_admin.py:3415`), auditiert | ✅ |
| Auto-Login `/geraet-login`, PIN-Pairing | `app/routers/auth.py:112/163`, `app/services/device_login_service.py` | ✅ |
| Session-Cookie bindet `device_token_id` („t“), Widerruf wirkt sofort pro Request | `app/core/security.py:187`, `app/main.py:569–601` → `request.state.device_token_id` | ✅ |
| Bearer-Auth für native Worker | `device_api.py::_resolve_user_via_bearer_token` | ✅ |
| `_get_device_token(user_id)` nimmt „neuestes aktives Token des Users“ statt des Session-Tokens | `device_api.py:61` | 🔧 für den Einheitenmodus Session-Token bevorzugen |
| WebSocket-Handshake prüft nur `User.active`, **nicht** den Geräte-Widerruf | `app/routers/ws.py::_resolve_user` (~Z. 96) | 🔧 Sicherheitslücke für den Einheitenmodus |

### 1.5 Fahrzeugbezogene GSL-Warteschlange und Widget

- `build_my_lage_queue()` (`app/services/gsl_live_service.py:49`):
  DeviceToken → `vehicle_master_id` → `LageEinheit` (Status `im_einsatz`, Lage `active`, neueste) → aktive
  Dispatches sortiert nach `dispatched_at` → `current` = erster, `upcoming` = max. 2 (`GSL_QUEUE_MAX_UPCOMING`),
  `remaining_count`.
  Schwächen: Reihenfolge rein nach Dispositionszeit; „current“ ist nicht der tatsächlich bearbeitete Auftrag;
  `lage_url` zeigt auf das Führungsboard `/lage/{id}`; keine Auftrags-/Statusinformation.
- Ausgeliefert über `GET /api/v1/device/duty-state` (`device_api.py:377`), Feld `my_lage_queue`.
- Android: `GslQueueState.kt` (parst `my_lage_queue`, max. 2 upcoming), `EcpWidgetSupport.kt`
  (`saveGslQueue`, `renderGsl` – Klick öffnet `lageUrl`, Maps-Button `gmapsUrl`),
  `EinsatzLivePoller.kt`/`DutyStateFetcher.kt` holen den Zustand.

### 1.6 Echtzeit und Push

| Baustein | Ort | Stand |
|---|---|---|
| `/ws/lage/{id}` org-gebunden, `broadcast_lage()`; worker-übergreifend via `ws_bus` | `ws.py:212`, `app/services/broadcast.py:124` | ✅ |
| Board reagiert auf `site:card_changed`, `site_phase_changed`, `funkjournal:changed`, `vehicle:position` | `app/static/js/lage_board.js:185–235` | ✅ |
| `notify_vehicle()` – Push (Web + FCM) an alle Tablets eines `VehicleMaster` | `app/services/push_service.py:582` | ✅ bisher nur für Einsatz-Aufträge (`incident_service.py:646`) genutzt |
| `notify_org_fcm_wake_only()` – stiller Wake-Push | `push_service.py:511` | ✅ |
| `notify_gsl_live()` – Lage-Zähler per WS/Push | `app/services/gsl_live_notify.py` | ✅ unverändert |
| FCM-Empfang, Preload der Ziel-URL in den SW-Cache | Android `EinsatzFirebaseMessagingService.kt`, `EinsatzPreloadWorker.kt` | ✅ |

### 1.7 Offline

| Baustein | Stand |
|---|---|
| `app/static/sw.js`: network-first-Cache für `/einsatz/<id>`, Objekt-Seiten, Hydranten; Offline-Banner | ✅ nur **lesend** |
| Android: Offline-Start ohne Login-Roundtrip (`www/index.html`), Objekt-/Kontakt-Precache (`ObjektOfflineSyncWorker`, Room-DB `KontaktDatabase`) | ✅ lesend |
| Schreib-Queue (Outbox), Idempotenzschlüssel, Konfliktbehandlung | 🆕 **existiert nirgends** (weder Web noch Android) |

### 1.8 Medien, Diktat, Navigation, Straßensperren

- `app/static/js/media-upload.js`: ✅ XHR-Upload mit echtem Fortschritt, clientseitige Kompression (2560 px / 0.85, Fallback 1920 px), `compressAndSubmit()`.
- `app/services/lage_media_service.py::upload_site_media()`: ✅ nur Bilder (MIME-Prüfung, Quota `reserve_storage`). Video: 🆕 (Limit `MAX_UPLOAD_BYTES_VIDEO` existiert in `config.py:131`).
- `app/static/js/app.js::startVoice()` (~Z. 975): Web Speech API – **in der Android-WebView nicht verfügbar**. Praktikabel ist das Mikrofon der Bildschirmtastatur (Gboard/Samsung).
- Navigation: `gmaps_url` in `build_my_lage_queue` ✅; Android-Widget öffnet sie ✅.
- Straßensperren: `app/services/road_closure_incident_service.py` ✅ `relevant_closures(db, org_id, route_geom, dest)` (funktioniert auch ohne Route nur mit Ziel), ✅ `evaluate_route()` (Live-Route ohne Persistenz, nur wenn `EINSATZ_ROUTING_ENABLED`, aktuell **aus**), Provider ORS mit Sperrflächen bzw. OSRM (`app/services/einsatz_routing/`).

### 1.9 MCP

`app/mcp/tools/`: fahrtenbuch, kontakt, objekt, objekt_dokumente, organisation, strassensperren, wasserstelle, whoami.
**Keine GSL-Tools.** `app/mcp/context.py::load_live_context` lehnt Geräte-Benutzer explizit ab
(„Geräte-Benutzer dürfen MCP nicht verwenden“). Upload/Download-Muster: `mcp_upload_service.py`, `upload_router.py`.

### 1.10 Design

Kein Stitch-Mockup (Entscheidung E5). Grundlage ist das **bestehende Fahrtenbuch-Design** – die
bereits für Tablets im Fahrzeug optimierte Erfassungsmaske:

| Baustein | Ort | Verwendung im Einheitenmodus |
|---|---|---|
| `fab-page`, `fab-header` (Org-Logo, Eyebrow, Titel, Untertitel) | `app/templates/fahrtenbuch/neu.html` (Inline-`<style>`), Kopie in `fahrtenbuch/verwaltung/korrektur.html` | Kopf: Eyebrow = Lage, Titel = Fahrzeug |
| `fab-grid` mit `fab-grid__main`/`__side` (ab 980 px 12-Spalten-Raster 8/4, darunter einspaltig) | `neu.html` | links aktueller Einsatz bzw. Detail, rechts weitere Aufträge bzw. Aktionsleiste (sticky) |
| `fab-card` mit Akzentleiste `--fab-accent`, `fab-card__head/__icon/__title/__body`, `fab-subcard` | `neu.html` | Auftragskarten; Akzent = Priorität bzw. Einheitenstatus |
| `fab-actions` (sticky Aktionsleiste unten, ab 980 px statisch) | `neu.html` | Primärbutton „nächster Status“ |
| `form-control` mit `min-height:48px`, `btn--primary btn--lg` | `neu.html` | alle Eingaben |
| `person-flyout` (Vollbild unter 600 px, große Trefferflächen) | `neu.html` | Flyouts für Lagemeldung, Unterstützung, Gefahr |
| Entwurfs-Hinweis „Entwurf wiederherstellen / Verwerfen“ (`draft-hinweis`, `fahrt_draft_v1:*`) | `neu.html` Z. 108, 452 ff. | gleiches Muster für Meldungsentwürfe (Speicher: IndexedDB statt `localStorage`, siehe 6.5) |

Ergänzend aus der GSL: Prioritätsfarben `SITE_PRIORITY_COLOR`, `res-badge--onsite/--alarmed`.
Die `fab-*`-Styles liegen heute **doppelt inline** (`neu.html`, `korrektur.html`). Sie werden in ein gemeinsames
Partial `app/templates/_fab_styles.html` ausgelagert (Paket P1-4) und von Fahrtenbuch und Einheitenmodus
eingebunden – keine dritte Kopie.

---

## 2. Gap-Analyse

| # | Anforderung | Stand | Lücke / Maßnahme |
|---|---|---|---|
| G1 | Tablet erkennt eigene Einheit | ✅ Logik in `build_my_lage_queue` | 🔧 als `einheit_service.resolve_einheit_kontext()` herauslösen, Session-Token statt „neuestes Token“ |
| G2 | Alle eigenen Zuweisungen inkl. Kategorien aktuell/weitere/abgeschlossen/zurückgezogen | 🔧 nur aktive, max. 3 | 🆕 vollständige Auftragsliste, Kategorisierung |
| G3 | Konkreter Auftragstext der Führung | fehlt | 🆕 `EinheitSiteDispatch.auftrag` |
| G4 | Einheitenstatus 1–7 pro Einheit und Stelle | nur disponiert/vor Ort/zurückgezogen | 🆕 `einheit_status` + Zeitstempel; „vor Ort“ bleibt kompatibel |
| G5 | Genau **ein** aktueller Einsatz trotz Mehrfachzuweisung | `LageEinheit.incident_site_id` nur bei „vor Ort“ | 🔧 Zeiger bei Anfahrt/Vor Ort/In Arbeit setzen, Exklusivität serverseitig |
| G6 | Reihenfolge/Priorisierung durch Führung | fehlt (nur `dispatched_at`) | 🆕 `EinheitSiteDispatch.reihenfolge` |
| G7 | Lagemeldung/Maßnahme/Notiz durch Einheit | ✅ `SiteLogEntry` | 🔧 `einheit_id`, Service statt Router-Inline-Code |
| G8 | Foto-Upload mit Kommentar, Galerie | ✅ Upload/Kompression | 🔧 `einheit_id`, `kommentar`, Broadcast; Video 🆕 (Phase 3) |
| G9 | Unterstützung anfordern mit Rückmeldestatus | `CommLogEntry.is_request/handled` | 🔧 Art, Einheit, „in Bearbeitung“ |
| G10 | Rückfragen/Anweisungen bidirektional, Quittierung, kritische Nachrichten per Funk | Funkjournal ohne Empfänger | 🔧 `CommLogEntry.einheit_id`, `antwort_auf_id`, `quittierung_erforderlich`, `quittiert_at`, `kritisch`, `funk_uebermittelt_at` |
| G11 | Push bei Neuzuteilung/Änderung | `notify_vehicle` vorhanden, nicht für GSL | 🔧 aufrufen nach Commit |
| G12 | Lagemeldungs-Erinnerung an die Einheit | Reminder erzeugt nur Funkjournal-Eintrag | 🔧 zusätzlich Push/WS an disponierte Einheiten |
| G13 | Offline-Outbox, Idempotenz, Konflikte | fehlt komplett | 🆕 IndexedDB-Outbox + `einheit_aktion`-Tabelle |
| G14 | Tablet schreibt nur auf eigene Aufträge, Gesamtansicht nur lesend | Gerät mit Rolle sieht/ändert alles | 🆕 Geräteprofil „Einheit“ + Lese-Allowlist auf Führungsrouten |
| G15 | Widerruf wirkt auch auf WebSocket | nicht geprüft | 🔧 `ws.py::_resolve_user` |
| G16 | Führung sieht Einheitenstatus, neue Meldungen, offene Anforderungen, „keine Rückmeldung“ | nur Zähler disponiert/vor Ort | 🔧 Board-Karte, Site-Detail, Kräfteübersicht |
| G17 | Navigation mit Sperrenhinweis | Maps-Link ✅, Sperren-Services ✅ | 🔧 Sperren am Ziel anzeigen (Phase 1), Route mit Sperren (Phase 3) |
| G18 | Objektinfo/Einsatzpläne | `IncidentSite.incident_id → Incident.objekt_links` ✅, Objekt offline ✅ | 🔧 Link in Detailseite |
| G19 | MCP-Tools für Einheiten | keine GSL-Tools | 🆕 Phase 3, auf denselben Services |
| G20 | Einstieg: Tablet öffnet automatisch Einheitenmodus | Startseite `/` zeigt GSL-Kachel | 🔧 Redirect für Einheit-Geräte; Widget-Link auf `/einheit` |

---

## 3. Architektur

### 3.1 Grundentscheidungen

1. **Kein paralleles Datenmodell.** Der „Auftrag“ einer Einheit *ist* der bestehende
   `EinheitSiteDispatch`. Rückmeldungen *sind* `SiteLogEntry`/`SiteMedia`, Kommunikation *ist*
   `CommLogEntry` (Funkjournal). Es entsteht genau **eine** neue Tabelle (`einheit_aktion`) für
   Idempotenz und Geräteprotokoll.
2. **Eine Geschäftslogik für drei Eingänge.** Führungs-UI, Tablet-API und MCP rufen dieselben
   Service-Funktionen auf. Dafür wird die heute im Router liegende Logik (Log, Medien, Funkjournal)
   in Services verschoben.
3. **Berechtigung über die Beweiskette Gerät → Fahrzeug → LageEinheit → Dispatch**, bei jedem
   Request neu aufgelöst. Es werden keine Rechte im Token gespeichert. So wirken Fahrzeugwechsel,
   Widerruf und Rückzug sofort.
4. **Tablet-UI als Alpine-Komponente auf JSON-Zustand.** HTMX-Swaps würden offene Eingaben
   überschreiben und funktionieren offline nicht. Die Seite `/einheit` wird serverseitig als
   Hülle gerendert (Jinja, Design-Tokens, CSRF) und rendert Karten aus `GET /einheit/api/zustand`.
   Live-Updates ersetzen nur den Datenzustand, nie offene Formulare.
5. **Outbox im Web-Frontend (IndexedDB)**, weil die UI in der Capacitor-WebView läuft und dieselbe
   Seite auch im Browser funktionieren soll. Android ergänzt in Phase 3 nur das Hintergrund-Flushen
   über das bewährte Headless-WebView-Muster (`ObjektOfflineSyncWorker`).
6. **Gesamtansicht nur lesend (E1).** Einheit-Tablets dürfen jederzeit in die bestehende Führungsansicht
   (Board, Lagekarte, Einsatzstellen-Details, Funkjournal) wechseln – dort aber nichts ändern. Umgesetzt
   über eine Lese-Allowlist auf den bestehenden Routen statt einer zweiten Gesamtansicht (9.2).
7. **Einheiten ohne Tablet (E2)** werden über Funk zentral instruiert. Ihr Status und ihre Rückmeldungen
   werden von der Führung bzw. dem Funker **stellvertretend** über dieselben Services erfasst
   (`quelle="funk"`, 5.1). Es gibt also ein Datenmodell für Tablet- und Funk-Einheiten; die Führung sieht
   beide in derselben Darstellung.
8. **Admin-Simulation (E7).** Admins können den Einheitenmodus für jede Einheit einer Lage im Browser
   öffnen – dieselbe Oberfläche, dieselben Routen und Services, nur ein anderer Kontext-Resolver (5.2a).
   In Übungslagen dürfen sie damit auch schreiben, in Echtlagen nur ansehen.

### 3.2 Komponentenübersicht

```
Tablet (Capacitor-WebView)                     Server (FastAPI)
┌──────────────────────────────┐   HTTPS   ┌────────────────────────────────────────┐
│ /einheit  (einheit.html)     │──────────▶│ app/routers/ui_einheit.py  🆕          │
│  Alpine: einheit_modus.js 🆕 │  JSON     │   Depends(require_einheit_geraet) 🆕   │
│  Outbox: einheit_outbox.js 🆕│◀──────────│        │                               │
│  media-upload.js ✅          │           │        ▼                               │
│  native-bridge.js ✅ (GPS)   │           │ app/services/einheit_service.py 🆕     │
└────────────┬─────────────────┘           │   resolve_einheit_kontext()            │
             │ WS /ws/lage/{id} ✅          │   auftraege_fuer_einheit()             │
             │ Event einheit:changed 🆕     │   setze_einheit_status()               │
             ▼                              │        │ nutzt                         │
   FCM ← notify_vehicle() ✅                │        ▼                               │
                                            │ resource_service ✅🔧                  │
Führung (Board, Site-Detail, Funkjournal)   │ site_log_service 🆕 (aus Router)       │
┌──────────────────────────────┐           │ lage_media_service ✅🔧                 │
│ ui_major_incident.py ✅🔧     │──────────▶│ funkjournal_service 🆕 (aus Router)    │
│ lage_board.js ✅🔧            │           │ lagemeldung_service ✅                 │
└──────────────────────────────┘           │ push_service.notify_vehicle ✅         │
                                            │ broadcast_lage ✅                      │
MCP (Phase 3) app/mcp/tools/gsl.py 🆕 ─────▶│ road_closure_incident_service ✅       │
                                            └────────────────────────────────────────┘
```

### 3.3 Einheiten-Kontext (Kern der Autorisierung)

```python
@dataclass(frozen=True)
class EinheitKontext:
    device_token: DeviceToken
    vehicle: VehicleMaster
    einheit: LageEinheit
    lage: MajorIncident
    org_id: int
```

`resolve_einheit_kontext(db, request) -> EinheitKontext | None`:

1. `request.state.is_device` muss wahr sein; `device_token_id` aus der Session
   (`request.state.device_token_id`), sonst Bearer-Token (`_resolve_user_via_bearer_token`). Fallback auf
   `_get_device_token(user.id)` nur für Alt-Cookies ohne „t“.
2. `DeviceToken.revoked_at IS NULL`, `vehicle_master_id` gesetzt, `gsl_profil == "einheit"` (siehe 4.1).
3. `LageEinheit` mit `vehicle_id == vehicle_master_id`, `status IN (bereitgestellt, im_einsatz)`, Lage
   `active`, `lage.org_id == user.org_id`. Mehrere Treffer (Fahrzeug in zwei Lagen): neueste Lage,
   UI zeigt Hinweis und Auswahl (`?lage=`), Auswahl wird gegen dieselbe Bedingung geprüft.
4. Kein Treffer → `None` → `/einheit` zeigt „Keine aktive Großschadenslage für <Fahrzeug>“ und
   einen Link zur normalen Startseite.

`require_einheit_geraet` (FastAPI-Dependency) wirft 403, wenn kein Kontext existiert.
`require_einheit_auftrag(dispatch_id)` lädt den Dispatch **nur** über
`EinheitSiteDispatch.einheit_id == ctx.einheit.id` und `site.major_incident_id == ctx.lage.id`.
Fremde IDs ergeben 404 (keine Existenzpreisgabe).

### 3.4 „Aktueller Einsatz“ bei Mehrfachzuweisung

- Server-Wahrheit: `LageEinheit.incident_site_id` (existiert, wird heute in `set_vor_ort_at_site` gesetzt).
- Wird gesetzt, wenn die Einheit auf einem Auftrag **Anfahrt**, **Vor Ort** oder **In Arbeit** meldet.
  Pro Einheit darf nur ein Auftrag in diesen drei Status sein.
- Startet die Einheit einen anderen Auftrag, während einer läuft, antwortet der Server mit `409 aktiver_auftrag`
  und nennt den laufenden. Das Tablet fragt einmal: „<Stelle A> unterbrechen und zu <Stelle B> wechseln?“
  Bei Bestätigung (`unterbrechen=true`) geht A auf `bestaetigt` zurück (SiteLogEntry „Auftrag unterbrochen“),
  B wird aktiv. **Kein `withdrawn_at`**: Abziehen bleibt Führungsentscheidung (`resolve_vor_ort_conflict`
  bleibt für die Führung unverändert).
- Ohne aktiven Auftrag: „nächster Auftrag“ = offener Auftrag mit kleinster `reihenfolge`, dann
  höchster Priorität (`IncidentSite.priority`), dann `dispatched_at`.
- Abgeschlossene/nicht durchführbare Aufträge geben den Zeiger frei (`incident_site_id = None`).

### 3.5 Echtzeitfluss

| Auslöser | Server | Tablet | Führung |
|---|---|---|---|
| Führung disponiert/ändert/zieht zurück | Service → Commit → `broadcast_lage(einheit:changed{einheit_id, dispatch_id, site_id, grund})` + `site:card_changed` → `notify_vehicle()` (nach Commit) | WS-Event mit eigener `einheit_id` → `GET /einheit/api/zustand`; Push öffnet `/einheit/auftrag/{id}` | Karte wird neu geladen ✅ |
| Einheit meldet Status/Lagemeldung/Foto | Service → Commit → `site:card_changed` + `einheit:changed` (+ `funkjournal:changed` bei Anforderung/Antwort) | Outbox-Eintrag „übermittelt ✓“ erst nach 2xx | Karte, Detail, Funkjournal aktualisieren sich |
| Reminder: Lagemeldung fällig | `gsl_lagemeldung_reminder` → zusätzlich `einheit:changed` + Push an disponierte Einheiten | Banner „Lagemeldung angefordert“ | wie bisher |

Die Events tragen nur IDs (keine Inhalte). Das Tablet holt Inhalte über seine eigene, autorisierte API.
Damit muss der bestehende Lage-Kanal nicht aufgeteilt werden.

---

## 4. Datenmodell

Eine Alembic-Migration je Phase (nächste Nummer ab `0263`). Alle neuen Spalten sind nullable oder
haben Server-Defaults. Bestehende Zeilen bleiben gültig.

### 4.1 Erweiterungen bestehender Tabellen

#### `einheit_site_dispatch` 🔧 (= Auftrag)

| Spalte | Typ | Zweck |
|---|---|---|
| `auftrag` | `Text NULL` | Konkreter Auftrag der Führung („Keller auspumpen, Strom prüfen“) |
| `einheit_status` | `String(20) NOT NULL DEFAULT 'zugewiesen'` | `zugewiesen`, `bestaetigt`, `anfahrt`, `vor_ort`, `in_arbeit`, `abgeschlossen`, `nicht_durchfuehrbar` |
| `status_at` | `DateTime NULL` | letzte Statusänderung |
| `bestaetigt_at` | `DateTime NULL` | Auftrag bestätigt |
| `beendet_at` | `DateTime NULL` | abgeschlossen oder nicht durchführbar |
| `beendet_grund` | `Text NULL` | Pflicht bei „nicht durchführbar“, optional bei Abschluss |
| `reihenfolge` | `Integer NULL` | Priorisierung durch die Führung (klein = zuerst) |
| `version` | `Integer NOT NULL DEFAULT 1` | wird bei jeder **Führungs**-Änderung (Auftrag, Reihenfolge, Rückzug) erhöht → Konflikterkennung |
| `geaendert_at` | `DateTime NULL` | letzte Führungsänderung (Tablet-Hinweis „Auftrag geändert“) |
| `letzte_rueckmeldung_at` | `DateTime NULL` | letzte Aktion der Einheit (Status, Meldung, Foto) → „keine Rückmeldung seit …“ |

`vor_ort_at` und `withdrawn_at` bleiben mit **unveränderter Bedeutung**. „Zurückgezogen“ ist
**kein** `einheit_status`, sondern weiterhin `withdrawn_at IS NOT NULL`. So bleiben die Führungsaktionen
und alle bestehenden Abfragen gültig. Statusübergang „vor Ort“ setzt zusätzlich `vor_ort_at`, falls noch leer.

Index: `ix_esd_einheit_aktiv (einheit_id, withdrawn_at, beendet_at)`.

#### `site_log_entry` 🔧

| Spalte | Zweck |
|---|---|
| `einheit_id` `Integer NULL FK lage_einheit.id ON DELETE SET NULL` | Meldung stammt von dieser Einheit (Badge, Filter, Auswertung) |
| `erfasst_at` `DateTime NULL` | Erfassungszeit am Gerät (offline erfasst). `ts` bleibt Server-Eingang |

Neuer System-Kind `einheit` (passt in `String(16)`) für Statuswechsel der Einheit, z. B.
„TLF Wolfurt: Vor Ort“. In `SITE_LOG_KIND_LABEL` aufnehmen, **nicht** in `SITE_LOG_USER_KINDS`.

#### `site_media` 🔧

| Spalte | Zweck |
|---|---|
| `einheit_id` `Integer NULL FK` | Herkunft |
| `kommentar` `String(500) NULL` | optionaler Bildkommentar |
| `erfasst_at` `DateTime NULL` | Aufnahmezeit am Gerät |

#### `comm_log_entry` 🔧 (Funkjournal = Kommunikationskanal)

| Spalte | Zweck |
|---|---|
| `einheit_id` `Integer NULL FK` | Absender (direction `in`) bzw. Empfänger (direction `out`) |
| `art` `String(24) NULL` | `rueckfrage`, `anweisung`, `antwort`, `unterstuetzung`, `gefahr`, `lagemeldung_angefordert`. NULL = klassischer Funkjournaleintrag |
| `kategorie` `String(24) NULL` | nur bei `unterstuetzung`: `mannschaft`, `fahrzeug`, `material`, `spezialkraefte`, `sonstiges` |
| `dringend` `Boolean NOT NULL DEFAULT 0` | dringende Unterstützung bzw. Gefahr von der Einheit |
| `kritisch` `Boolean NOT NULL DEFAULT 0` | kritische Nachricht der Führung → muss per Funk übermittelt werden (E6) |
| `funk_uebermittelt_at` `DateTime NULL`, `funk_uebermittelt_von` `String(120) NULL` | Funkübermittlung bestätigt (kritische Nachrichten, Funk-Einheiten, per Funk bestätigte Tablet-Meldungen) |
| `quittierung_erforderlich` `Boolean NOT NULL DEFAULT 0` | Führung verlangt ausdrückliche Bestätigung am Tablet (bei `kritisch` immer gesetzt) |
| `quittiert_at` `DateTime NULL`, `quittiert_von` `String(120) NULL` | Bestätigung durch die Einheit |
| `antwort_auf_id` `Integer NULL FK comm_log_entry.id ON DELETE SET NULL` | Antwort auf Rückfrage |
| `in_bearbeitung_at` `DateTime NULL` | Anforderungsstatus „in Bearbeitung“ |

Status einer Anforderung wird abgeleitet: Zeile vorhanden = **eingegangen**, `in_bearbeitung_at` =
**in Bearbeitung**, `handled` = **erledigt** (bestehendes Feld). `is_request` bleibt für
„Antwort/Erledigung erwartet“. `auto_kind` bleibt den System-Aufträgen vorbehalten.

#### `device_token` 🔧

| Spalte | Zweck |
|---|---|
| `gsl_profil` `String(12) NULL` | `einheit` = Einheitenmodus mit eingeschränktem GSL-Zugriff, `fuehrung` = bisheriges rollenbasiertes Verhalten (z. B. KDO-Tablet der Einsatzleitung), `NULL` = Altbestand, wird wie `fuehrung` behandelt |

Bei Neuanlage mit Fahrzeug ist `einheit` vorbelegt. Bestehende Geräte bleiben ohne Admin-Aktion
unverändert (siehe 11).

### 4.2 Neue Tabelle `einheit_aktion` 🆕 (Idempotenz + Geräteprotokoll)

```python
class EinheitAktion(TenantScoped, Base):
    __tablename__ = "einheit_aktion"
    id:              Mapped[int]  = mapped_column(BigInteger, primary_key=True)
    client_uuid:     Mapped[str]  = mapped_column(String(36), unique=True)   # vom Gerät erzeugt (UUIDv4)
    device_token_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("device_token.id", ondelete="SET NULL"))
    einheit_id:      Mapped[int | None] = mapped_column(Integer, ForeignKey("lage_einheit.id", ondelete="SET NULL"))
    dispatch_id:     Mapped[int | None] = mapped_column(Integer, ForeignKey("einheit_site_dispatch.id", ondelete="SET NULL"))
    aktion:          Mapped[str]  = mapped_column(String(24))   # status|lagemeldung|massnahme|notiz|foto|anforderung|antwort|quittierung
    ergebnis:        Mapped[str]  = mapped_column(String(16))   # ok|konflikt|abgelehnt
    entity_type:     Mapped[str | None] = mapped_column(String(32))
    entity_id:       Mapped[int | None] = mapped_column(BigInteger)
    antwort_json:    Mapped[str | None] = mapped_column(Text)   # gespeicherte Antwort für Replays
    erfasst_at:      Mapped[datetime | None]
    empfangen_at:    Mapped[datetime]
```

- In `_TENANT_TABLE_NAMES` aufnehmen.
- Ein Request mit bekannter `client_uuid` (gleiches Gerät) bekommt **dieselbe** gespeicherte Antwort
  zurück, ohne erneute Ausführung. Das ist der Schutz vor Doppeleinträgen bei Retry nach Timeout.
- Gleiche `client_uuid` von einem anderen Gerät → `409`.
- Ersetzt keine Audit-Einträge (`write_audit` bleibt Pflicht), dient aber als Sync-Protokoll
  („welche Geräteaktionen kamen wann an“) und für Auswertungen in Phase 3.

### 4.3 Zentraler Aktiv-Filter 🔧

Neu in `resource_service.py`:

```python
def dispatch_aktiv_filter():
    """Disposition belegt die Einheit: nicht zurückgezogen und nicht beendet."""
    return and_(EinheitSiteDispatch.withdrawn_at.is_(None), EinheitSiteDispatch.beendet_at.is_(None))
```

Alle 11 Verwendungen von `withdrawn_at.is_(None)` werden geprüft und bewusst entschieden:

| Verwendung | Neue Semantik |
|---|---|
| Dispatch-Zähler Board (`get_dispatch_counts_for_sites`) | aktiv **ohne** beendete; zusätzlich Zähler `fertig` für „Einheit fertig, Stelle offen“ |
| `dispatch_to_site` Duplikatprüfung | aktiv (eine beendete Disposition darf erneut disponiert werden → neue Zeile) |
| Vor-Ort-Konflikt `set_vor_ort_at_site` | aktiv **und** `einheit_status IN (anfahrt, vor_ort, in_arbeit)` |
| `lagemeldung_service.has_active_resource` | aktiv ohne beendete (fertige Einheiten lösen keine Lagemeldungspflicht aus) |
| `build_my_lage_queue` | über `einheit_service` (siehe 5.2) |
| `druck_bericht.html` | unverändert: Bericht zeigt alle Dispositionen mit Status |

---

## 5. Backend

### 5.1 Services

| Service | Art | Inhalt |
|---|---|---|
| `app/services/einheit_service.py` | 🆕 | `resolve_einheit_kontext()`, `auftraege_fuer_einheit(db, ctx) -> EinheitZustand` (Kategorien `aktuell`, `weitere`, `abgeschlossen`, `zurueckgezogen`, Zähler, `server_time`, `version` je Auftrag), `auftrag_detail(db, ctx, dispatch_id)`, `setze_einheit_status(db, ctx, dispatch, neuer_status, *, grund, unterbrechen, erfasst_at, client_version)`, Statusübergangstabelle, `ERLAUBTE_UEBERGAENGE`, Labels/Farben `EINHEIT_STATUS_LABEL/_COLOR` |
| `app/services/site_log_service.py` | 🆕 (Extraktion) | `add_site_log(db, site, kind, text, *, user_id, author_name, einheit_id=None, erfasst_at=None) -> SiteLogEntry`, inkl. `register_lagemeldung()` bei `SITE_LOG_RESET_KINDS`. Wird von `ui_major_incident.site_log_add` (Z. 1267), Tablet-API und MCP genutzt. Strukturierte Lagemeldung → `format_lagemeldung(felder) -> str` (Abschnitte „Lage vor Ort“, „Gefahren“, „Maßnahmen“, „Fortschritt“, „Freitext“; leere Felder entfallen) |
| `app/services/lage_media_service.py` | 🔧 | `upload_site_media(..., einheit_id=None, kommentar=None, erfasst_at=None)`; neue Funktion `speichere_site_foto(db, site, file, ...)` bündelt Upload + `SiteLogEntry(kind="media")` (heute im Router Z. 1554) |
| `app/services/funkjournal_service.py` | 🆕 (Extraktion) | `add_comm_entry(...)` (heute `funkjournal_add` Z. 2915 inkl. Spiegelung in `SiteLogEntry`), `toggle_handled(...)`, neu: `sende_an_einheit(...)` (Rückfrage/Anweisung/Lagemeldung anfordern), `anforderung_erstellen(...)`, `antwort_erstellen(...)`, `quittieren(...)`, `setze_in_bearbeitung(...)` |
| `app/services/resource_service.py` | 🔧 | `dispatch_to_site(..., auftrag=None, reihenfolge=None)`; neu `aendere_auftrag(db, dispatch, *, auftrag, reihenfolge)` (erhöht `version`), `oeffne_auftrag_wieder(...)` (Führung setzt beendeten Auftrag zurück), `dispatch_aktiv_filter()`; `withdraw_from_site` erhöht `version` und setzt `geaendert_at` |
| `app/services/gsl_live_service.py` | 🔧 | `build_my_lage_queue()` wird dünner Adapter auf `einheit_service.auftraege_fuer_einheit()`: `current` = aktueller oder nächster Auftrag, `upcoming` = die nächsten 2 nach Reihenfolge, Ausgabeformat unverändert plus additive Felder `einheit_url`, `auftrag`, `einheit_status`. `lage_url` zeigt für Einheit-Geräte auf `/einheit` (alte APKs öffnen damit automatisch den Einheitenmodus) |
| `app/services/einheit_notify.py` | 🆕 | `notify_einheit(db, einheit, titel, text, url)` → `push_service.notify_vehicle()` **nach Commit** als BackgroundTask (Muster `gsl_notify.py`). Ohne `vehicle_id` kein Push, nur WS |
| `app/services/gsl_lagemeldung_reminder.py` | 🔧 | bei neuem Auto-Auftrag zusätzlich `CommLogEntry(art="lagemeldung_angefordert", einheit_id=…)` je aktiver Disposition mit Tablet, Event `einheit:changed`, Push (Phase 2) |

#### Statusübergänge (Einheit)

| von \ nach | bestaetigt | anfahrt | vor_ort | in_arbeit | abgeschlossen | nicht_durchfuehrbar |
|---|---|---|---|---|---|---|
| zugewiesen | ✓ | ✓ | ✓ | – | – | ✓ (Grund) |
| bestaetigt | = | ✓ | ✓ | – | – | ✓ |
| anfahrt | ✓ (unterbrochen) | = | ✓ | – | – | ✓ |
| vor_ort | ✓ (unterbrochen) | – | = | ✓ | ✓ | ✓ |
| in_arbeit | ✓ (unterbrochen) | – | ✓ | = | ✓ | ✓ |
| abgeschlossen / nicht_durchfuehrbar | nur Führung (`oeffne_auftrag_wieder`) |||||||

- Vorwärtssprünge sind erlaubt, weil Schritte im Einsatz oft übersprungen werden.
- Gleicher Status = idempotentes No-Op (`200`, keine neue Logzeile).
- `zurueckgezogen` (Dispatch mit `withdrawn_at`): Statusänderungen → `409 auftrag_zurueckgezogen`.
  Lagemeldungen, Notizen und Fotos werden **trotzdem angenommen** (Informationen gehen nie verloren)
  und in der Chronik mit „(nach Rückzug eingegangen)“ markiert.
- Jede Statusänderung: `SiteLogEntry(kind="einheit")`, `write_audit("gsl.einheit.status", …)`,
  `letzte_rueckmeldung_at`, bei `vor_ort`/`in_arbeit` `lagemeldung_service.ensure_timer(site)`
  (gleich wie `site_einheit_vor_ort`). Bei `bestaetigt`, `abgeschlossen` und `nicht_durchfuehrbar`
  zusätzlich `LageJournalEntry(category="ressource")` über `resource_service._journal`.
- **Auswirkung auf den Status der Einsatzstelle (E3).** Meldet eine Einheit `vor_ort` oder `in_arbeit`
  (vom Tablet, per Funk oder über MCP), wird `IncidentSite.phase` auf `in_arbeit` gesetzt – **nur
  vorwärts** und nur aus `eingegangen`, `erkundung`, `bewertet` oder `disponiert`. Steht die Stelle bereits
  auf `in_arbeit`, `erledigt` oder `abgebrochen`, passiert nichts. Damit gilt:
  - Mehrere Einheiten an derselben Stelle stören sich nicht: die erste „Vor Ort“-Meldung hebt die Phase,
    alle weiteren sind No-Ops.
  - Ein offline erfasstes, verspätet eintreffendes „Vor Ort“ setzt eine inzwischen von der Führung
    abgeschlossene Stelle **nicht** wieder auf „in Arbeit“.
  - `abgeschlossen` und `nicht_durchfuehrbar` einer Einheit ändern die Phase nie. Abschluss und Abbruch
    entscheidet weiterhin die Führung. Melden alle aktiven Dispositionen `abgeschlossen`, zeigt die
    Board-Karte den Hinweis „Alle Einheiten fertig – Stelle abschließen?“.
  - Die Anhebung läuft über dieselbe Logik wie der manuelle Phasenwechsel: die heute im Router liegende
    Logik aus `site_phase_change` (`ui_major_incident.py:593`: `SiteLogEntry(kind="status")`, Audit
    `major_incident.site.phase_changed`, `lagemeldung_service.ensure_timer`/`clear_timer`) wandert in
    `major_incident_service.setze_site_phase(db, site, neue_phase, *, user_id, author_name, ausloeser)`.
    Der Router und `einheit_service` rufen sie auf. Logzeile bei automatischer Anhebung:
    „Phase: Disponiert → In Arbeit (TLF Wolfurt vor Ort)“. Nach dem Commit folgen wie bisher
    `site_phase_changed` per WS und `notify_gsl_live(reason="counts")`.

#### Stellvertretende Erfassung für Einheiten ohne Tablet (E2)

- `setze_einheit_status()`, `add_site_log()`, `anforderung_erstellen()` usw. bekommen den Parameter
  `quelle: Literal["tablet", "funk", "mcp", "simulation"]`. Bei `funk` ist der Akteur ein Führungs-/Funker-Benutzer
  (`require_role("incident_leader","admin","org_admin","recorder")`), `EinheitKontext.device_token` ist `None`.
  Gleiche Statusmatrix, gleiche Logzeilen – Text-Suffix „(per Funk)“, `author_name` = Funker,
  Audit-Payload `quelle`.
- `hat_tablet(einheit)` = es existiert ein aktives `DeviceToken` mit `vehicle_master_id == einheit.vehicle_id`
  und `gsl_profil == "einheit"`. Steuert nur die Darstellung: ohne Tablet zeigt die Führungs-UI statt
  „Push gesendet“ den Hinweis „📻 per Funk übermitteln“ mit Button „per Funk übermittelt“ – der schreibt
  einen Funkjournal-Eintrag (`direction="out"`, `channel="Funk"`, `einheit_id`) und markiert Auftrag bzw.
  Rückfrage als zugestellt. Kein Push, kein Tablet-Banner.
- Fallback auch für Einheiten **mit** Tablet (Tablet defekt, kein Netz): die Führung kann jederzeit
  stellvertretend erfassen. Konflikte mit später eintreffenden Tablet-Aktionen löst die Statusmatrix
  (idempotent bzw. Server-Reihenfolge); beide Einträge bleiben in der Chronik sichtbar.

#### Kritische Nachrichten immer über Funk (E6)

- **Führung → Einheit:** Eine Rückfrage oder Anweisung mit `kritisch=True` wird immer als
  Funkauftrag angelegt: `CommLogEntry(direction="out", art=…, kritisch=True, channel="Funk", einheit_id=…)`
  mit dem Zustand **„Funk ausstehend“**, bis der Funker `funk_uebermittelt_at`/`funk_uebermittelt_von`
  setzt (Button „per Funk übermittelt“ – derselbe Mechanismus wie bei Funk-Einheiten, E2). Hat die Einheit
  ein Tablet, erscheint die Nachricht dort **zusätzlich** als Banner mit Hinweis „wird per Funk übermittelt“.
  Eine Quittierung am Tablet ersetzt die Funkübermittlung nicht; beide Zeitpunkte werden protokolliert.
- **Offene kritische Funkaufträge** sind im Board-Kopf („📻 n kritisch – Funk ausstehend“), im Funkjournal
  (oben angeheftet, rot) und auf der Board-Karte der Stelle sichtbar, bis sie als übermittelt markiert sind.
- **Einheit → Führung:** Dringende Unterstützung und Gefahrmeldungen vom Tablet werden gespeichert und
  angezeigt wie alle anderen Meldungen. Das Tablet zeigt nach dem Absenden aber immer den Hinweis
  „⚠ Zusätzlich sofort über Funk melden!“. Kommt die Meldung per Funk an, verknüpft der Funker sie mit dem
  Tablet-Eintrag (Button „auch per Funk erhalten“ → `funk_uebermittelt_at`), damit keine Doppelbearbeitung entsteht.
- Push bleibt eine Ergänzung. Es gibt **keinen** eigenen Benachrichtigungskanal für kritische Nachrichten
  und keinen DND-Bypass.

### 5.2 Tablet-API (`app/routers/ui_einheit.py`) 🆕

Prefix `/einheit`, alle Routen mit `Depends(require_einheit_geraet)`. JSON-POSTs mit CSRF-Header
(`app/static/js/csrf.js`-Muster) oder Bearer (native Worker). Jede schreibende Route verlangt
`client_uuid` und `erfasst_at` und optional `auftrag_version`.

| Methode + Pfad | Zweck | Service |
|---|---|---|
| `GET /einheit` | Tablet-Hülle (HTML) | – |
| `GET /einheit/auftrag/{dispatch_id}` | Deep-Link (HTML-Hülle, öffnet Detail) | – |
| `GET /einheit/api/zustand` | Kopf + alle Aufträge (Kategorien, Zähler, offene Nachrichten, `server_time`, `etag`) | `auftraege_fuer_einheit` |
| `GET /einheit/api/auftrag/{id}` | Detail: Stelle, Auftrag, andere Einheiten an der Stelle (Label + Status), Chronik (`SiteLogEntry`, letzte N), Fotos, Nachrichten, Gefahren (`CrossSiteMarker` im Umkreis), Objekt-Link, Sperren am Ziel | `auftrag_detail` |
| `POST /einheit/api/auftrag/{id}/status` | `{status, grund?, unterbrechen?}` | `setze_einheit_status` |
| `POST /einheit/api/auftrag/{id}/meldung` | `{art: lagemeldung\|massnahmen\|note, text?, felder?, unterstuetzung?}` | `site_log_service.add_site_log` (+ `anforderung_erstellen`, falls `unterstuetzung` gesetzt) |
| `POST /einheit/api/auftrag/{id}/foto` | multipart `file`, `kommentar?`, `client_uuid`, `erfasst_at` | `lage_media_service.speichere_site_foto` |
| `POST /einheit/api/auftrag/{id}/anforderung` | `{kategorie, text?, dringend}` | `funkjournal_service.anforderung_erstellen` |
| `POST /einheit/api/auftrag/{id}/gefahr` | `{text}` → `CommLogEntry(art="gefahr", dringend=True)` + `SiteLogEntry` | `funkjournal_service` |
| `POST /einheit/api/nachricht/{comm_id}/antwort` | `{text}` | `antwort_erstellen` |
| `POST /einheit/api/nachricht/{comm_id}/quittieren` | – | `quittieren` |
| `GET /einheit/api/karte` | GeoJSON nur der eigenen Stellen + letzte eigene Position | – |
| `GET /einheit/medien/{media_id}[/thumb]` | Bildauslieferung nur für Medien eigener Stellen (bestehende Auslieferung `/lage-medien/{id}` ist Führungsroute) | `lage_media_service.site_media_path` |

**Antwortvertrag für Schreibaktionen:**
`200 {ok: true, aktion_id, entity_id, server_time, auftrag_version, hinweis?}`.
Fehlerfälle: `409 {code: auftrag_zurueckgezogen | aktiver_auftrag | einheit_gewechselt | ungueltiger_uebergang, …}`,
`403` (kein Einheiten-Kontext), `404` (fremder/unbekannter Auftrag), `413` (Datei zu groß).
`hinweis = "auftrag_geaendert"`, wenn die Aktion angenommen wurde, aber `auftrag_version` veraltet war.

**Validierung:** Textlängen (Meldung ≤ 4 000, Kommentar ≤ 500), `kategorie` aus Whitelist,
`erfasst_at` maximal 24 h in der Vergangenheit und maximal 5 min in der Zukunft (sonst Serverzeit + Hinweis),
Bild-MIME/Größe über die bestehende `upload_site_media`-Prüfung, Speicherquota (`reserve_storage`).

### 5.2a Simulation des Einheitenmodus für Admins (E7) 🆕

Zweck: Einheitenmodus vorführen, schulen und testen, ohne ein Tablet zu koppeln, und im Einsatz
nachvollziehen, was ein bestimmtes Fahrzeug gerade auf seinem Tablet sieht.

- **Einstieg:** Button „📱 Als Einheit ansehen“ je Einheit in der Ressourcenübersicht (`ressourcen.html`)
  und in der Dispositionsliste im Site-Detail. Öffnet `/einheit?sim=<einheit_id>` in einem neuen Tab.
- **Kontext:** `resolve_einheit_kontext()` erhält einen zweiten Weg `simulierter_kontext(db, user, einheit_id)`:
  - nur für Benutzer mit `admin`/`org_admin` (bzw. `system_admin`), **nie** für Geräte-Benutzer
    oder QR-Sessions;
  - `LageEinheit` muss zu einer aktiven Lage der eigenen Org gehören (`lage.org_id == user.org_id`,
    sonst 404);
  - `EinheitKontext` bekommt `simulation=True` und `device_token=None`. Alle Leserechte sind dieselben wie
    beim echten Tablet: der Admin sieht exakt die Sicht der Einheit, nicht mehr.
- **Übertragung der Einheit-ID:** Die Hülle schreibt `sim` in den Alpine-Store, `einheit_modus.js`
  hängt bei jedem API-Aufruf den Header `X-EC-Einheit-Sim: <id>` an. Es gibt kein Cookie und keine
  Session-Umschaltung, der Admin bleibt im anderen Tab ganz normal angemeldet.
- **Schreiben:**
  - **Übungslage** (`MajorIncident.is_exercise`): alle Aktionen wie am Tablet erlaubt (Status inkl.
    E3-Phasenanhebung, Meldungen, Fotos, Anforderungen, Quittierungen). Sie laufen mit `quelle="simulation"`,
    `author_name = "<Admin> (Simulation <Einheit>)"` und Audit `gsl.einheit.*` mit `simulation=true`.
  - **Echtlage:** nur lesend. Schreibende Endpunkte antworten `403 simulation_nur_lesend`, die UI blendet
    die Aktionsleiste aus und zeigt „Echtlage – Simulation nur zur Ansicht“.
- **Kennzeichnung:** dauerhaft sichtbares oranges Banner „SIMULATION – TLF Wolfurt · Admin: <Name>“ mit
  „Simulation beenden“ (schließt den Tab bzw. führt zurück zur Ressourcenübersicht). In Chronik und Funkjournal
  sind Simulationseinträge mit 🧪 markiert.
- **Keine Nebenwirkungen nach außen:** Aktionen aus der Simulation lösen keinen Push an echte Tablets
  des Fahrzeugs aus. WS-Events laufen normal, damit die Führungsansicht der Übung live mitläuft.
- **Outbox:** eigene IndexedDB-Datenbank je simulierter Einheit (`ec-einheit-sim-<id>`), damit sich
  mehrere Simulationen im selben Browser und echte Tablet-Daten nie vermischen. Die Offline-Funktion lässt sich
  so auch im Browser vorführen (DevTools → offline).
- **Audit:** `gsl.einheit.simulation_gestartet` beim ersten Laden je Einheit und Tag.

### 5.3 Führungsseitige Erweiterungen (`ui_major_incident.py`) 🔧

| Route | Änderung |
|---|---|
| `…/einheit-disponieren` (1048) | optionale Formularfelder `auftrag`, `reihenfolge`; nach Commit `notify_einheit("Neuer Auftrag", …)` + `einheit:changed` |
| 🆕 `POST …/stellen/{site}/einheit/{dispatch}/auftrag` | Auftragstext/Reihenfolge ändern → `aendere_auftrag`, Push „Auftrag geändert“ |
| 🆕 `POST …/stellen/{site}/einheit/{dispatch}/wiedereroeffnen` | beendeten Auftrag zurücksetzen |
| 🆕 `POST …/stellen/{site}/einheit/{dispatch}/status` | stellvertretender Statuswechsel „per Funk“ (`quelle="funk"`), Statusbuttons im Site-Detail |
| 🆕 `POST …/stellen/{site}/einheit/{dispatch}/funk-zugestellt` | Auftrag per Funk übermittelt (Funkjournal-Eintrag) |
| 🆕 `POST /lage/{id}/funkjournal/{e}/funk-uebermittelt` | kritische Nachricht bzw. Rückfrage per Funk übermittelt, oder Tablet-Meldung „auch per Funk erhalten“ (setzt `funk_uebermittelt_at`) |
| `…/einheit-abziehen` (1162) | zusätzlich `einheit:changed` + Push „Auftrag zurückgezogen“ |
| `…/log` (1267), `…/medien` (1554) | auf Services umstellen; Medien-Upload broadcastet künftig `site:card_changed` |
| `/funkjournal` (2915) | auf Service umstellen; optional Empfänger-Einheit, `art`, `kritisch` (→ Funkauftrag, E6); Broadcast `funkjournal:changed` |
| `…/phase` (593) | Logik nach `major_incident_service.setze_site_phase` verschieben (gemeinsam mit der E3-Anhebung) |
| 🆕 `POST /lage/{id}/funkjournal/{e}/in-bearbeitung` | Anforderung „in Bearbeitung“ (Einheit sieht Status) |
| 🆕 `POST …/stellen/{site}/einheit/{dispatch}/lagemeldung-anfordern` | manuell Lagemeldung anfordern (Push + Banner) |
| `ressourcen` / `kraefteuebersicht` (4672/4771) | Einheitenstatus, aktueller Auftrag, letzte Rückmeldung je Einheit |

Alle neuen Führungsrouten: `require_role("incident_leader","admin","org_admin","recorder")`,
`_check_org_access`, CSRF, HTMX-Swap auf `#siteDetailContent` und Broadcast, wie im CLAUDE.md verlangt.

### 5.4 Einstieg / Startseite 🔧

- `ui_incident.py::index` (Z. 322): Ist der Benutzer ein Gerät mit `gsl_profil="einheit"` und liefert
  `resolve_einheit_kontext()` einen Kontext → `302 /einheit`. Ohne aktive GSL bleibt die Startseite
  unverändert. Ein normaler Einsatz (`IncidentVehicle` aktiv) hat Vorrang: Läuft für das Fahrzeug parallel ein
  Einzeleinsatz, zeigt `/` wie bisher die Einsatzansicht, `/einheit` ist über eine Kachel erreichbar.
- `/einheit` hat oben rechts „Normale Ansicht“ (→ `/?klassisch=1`, keine Weiterleitung für diese Session).

---

## 6. Frontend

### 6.1 Neue Dateien

| Datei | Inhalt |
|---|---|
| `app/templates/einheit/einheit.html` 🆕 | Hülle: Kopf, Listen-/Kartenumschaltung, Detail-Panel, Formular-Sheets; `extends "base.html"` mit reduzierter Navigation (Gerät) |
| `app/templates/einheit/_auftrag_karte.html` 🆕 | Alpine-`<template>` für Auftragskarten |
| `app/static/js/einheit_modus.js` 🆕 | Alpine-Store: Zustand laden, WS (`/ws/lage/{id}`, Filter auf `einheit:changed` mit eigener `einheit_id` sowie `site:card_changed` eigener Stellen), Ping/Reconnect, Polling-Fallback 30 s, ETag, Detailansicht, Entwurfsspeicher |
| `app/static/js/einheit_outbox.js` 🆕 | IndexedDB-Outbox (siehe 6.5) |
| `app/templates/_fab_styles.html` 🆕 (Extraktion) | gemeinsame `fab-*`-/`person-flyout`-Styles aus `fahrtenbuch/neu.html` und `verwaltung/korrektur.html`; beide Fahrtenbuch-Templates binden das Partial ein (Regression: Fahrtenbuch sieht unverändert aus) |
| `app/templates/einheit/_einheit_styles.html` 🆕 | nur Ergänzungen: Status-Chips, Outbox-Anzeige, Akzentfarben je Priorität/Status, Touch-Flächen ≥ 56 px für Statusbuttons |
| `app/static/sw.js` 🔧 | `/einheit`, `/einheit/auftrag/<id>` network-first in `BOARD_CACHE`; `/einheit/api/zustand` + `/einheit/api/auftrag/<id>` network-first mit Cache-Fallback (Antwort mit Header `X-EC-Offline: 1` kennzeichnen); `/einheit/medien/thumb/<id>` cache-first. Keine POSTs abfangen (die Outbox macht das) |

### 6.2 Übersicht „Meine Einsätze“ (Landscape 10–11″)

Aufbau im Fahrtenbuch-Raster: `fab-header` (Eyebrow Lage, Titel Fahrzeug, rechts Online-/Sync-/Outbox-Status
und Umschalter **Meine Einsätze | Gesamtansicht**), darunter `fab-grid` – `fab-grid__main` (8 Spalten) mit der
`fab-card` des aktuellen Einsatzes, `fab-grid__side` (4 Spalten) mit den weiteren Aufträgen als kompakte
`fab-card`s. Unter 980 px (Hochformat) einspaltig wie das Fahrtenbuch.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ TLF Wolfurt · Hochwasser Rheintal 10/2026   ● Online  Sync 14:32   3 offen · 2 erledigt │
│                     ⏳ 2 ausstehend  [Liste|Karte]  [Meine Einsätze|Gesamtansicht]   │
├───────────────────────────────────────┬──────────────────────────────────────┤
│ AKTUELLER EINSATZ                     │ WEITERE AUFTRÄGE                     │
│ ┌───────────────────────────────────┐ │ ┌──────────────────────────────────┐ │
│ │ #12  ● SOFORT      IN ARBEIT      │ │ │ 2. #17 Dringend  Zugewiesen  NEU │ │
│ │ Bahnhofstr. 4, Wolfurt            │ │ │ Keller auspumpen · seit 14:05    │ │
│ │ Keller überflutet                 │ │ └──────────────────────────────────┘ │
│ │ Auftrag: Pumpe setzen, Strom prüfen│ │ ┌──────────────────────────────────┐ │
│ │ seit 13:50 · 💬 1 Rückfrage        │ │ │ 3. #21 Normal  Bestätigt         │ │
│ │ [Navigation] [Lagemeldung] [📷]   │ │ └──────────────────────────────────┘ │
│ └───────────────────────────────────┘ │ ▸ Abgeschlossen (2)  ▸ Zurückgezogen (1)│
└───────────────────────────────────────┴──────────────────────────────────────┘
```

- Kopf: Fahrzeug (`VehicleMaster.code/name`), Lage (`MajorIncident.name`, Übungsbadge), Online-Status
  (WS verbunden + letzter erfolgreicher Request), „Sync HH:MM“ = letzte **Server**-Antwort, Zähler offen/erledigt,
  Outbox-Zähler „⏳ n ausstehend“ / „⚠ n fehlgeschlagen“ (antippbar → Outbox-Liste).
- Aktueller Einsatz links groß und hervorgehoben. Rechts weitere Aufträge in Reihenfolge, darunter
  eingeklappt abgeschlossene und zurückgezogene Aufträge.
- Karte: Leaflet (`leaflet.min.js`, `map-config.js` ✅), nur eigene Stellen nummeriert nach Reihenfolge,
  eigene Position (`VehiclePosition` bzw. lokale GPS-Position aus `native-bridge.js`), CrossSiteMarker
  vom Typ Sperre/Gefahr im Umkreis.
- Neue Zuweisung: Karte erscheint animiert mit Badge „NEU“ und Ton (vorhandenes `alert.mp3`), ohne Reload.
- Portrait-Fallback (≤ 760 px laut CLAUDE.md-Checkliste): einspaltig.

### 6.3 Detailansicht

Zweispaltig: links Informationen, rechts eine feste Aktionsleiste.

- **Info:** Nummer/Bezeichnung, Adresse + Koordinaten, Stichwort/Meldung (`einsatzgrund`),
  Priorität, **Auftrag** (hervorgehoben, mit „geändert HH:MM“-Marker), andere Einheiten an der Stelle mit
  deren Status, Gefahren (CrossSiteMarker im Umkreis von 300 m, Lagemeldungen mit „Gefahren“),
  Chronik (eigene und fremde Einträge, eigene markiert), Fotogalerie (`lightbox.js` ✅), Objekt-Button
  (über `incident_id → Incident.objekt_links → /objekte/{id}`, offline aus dem Android-Precache ✅).
- **Statusleiste:** großer Primärbutton mit dem jeweils nächsten logischen Status
  („Auftrag bestätigen“ → „Anfahrt“ → „Vor Ort“ → „In Arbeit“ → „Abgeschlossen“), daneben „Nicht durchführbar“.
  Kein Bestätigungsdialog außer bei „Abgeschlossen“, „Nicht durchführbar“ (Grund-Pflichtfeld) und beim
  Wechsel des aktiven Auftrags.
- **Schnellaktionen (je 1 Tap bis zum Formular):** 📷 Foto, 📝 Lagemeldung, 🛠 Maßnahme, 🆘 Unterstützung,
  ⚠ Gefahr melden, 🧭 Navigation.
- **Nachrichten:** Rückfragen und Anweisungen der Führung als Banner oben. Kritische Nachrichten
  (`kritisch`) als nicht wegklickbares rotes Banner mit dem Zusatz „📻 wird per Funk übermittelt“ sowie
  „Verstanden“ (= quittieren) und „Antworten“. Nach dem Absenden einer dringenden Unterstützung oder
  einer Gefahrmeldung: Hinweis „⚠ Zusätzlich sofort über Funk melden!“ (E6).

### 6.4 Formulare

- **Lagemeldung:** ein großes Freitextfeld ist sofort fokussiert, dazu ausklappbare Felder
  (Lage vor Ort / Gefahren / Maßnahmen / Fortschritt) und die Checkbox „Weitere Unterstützung notwendig“
  (öffnet die Kategorieauswahl). Eine kurze Freitextmeldung reicht. Diktat über das Tastaturmikrofon;
  ein 🎤-Button wird nur angezeigt, wenn `SpeechRecognition` verfügbar ist (Browser) oder das native
  Plugin vorhanden ist (Phase 3).
- **Foto:** `<input type="file" accept="image/*" capture="environment" multiple>`. Die Capacitor-WebView
  öffnet bei `capture` die Kamera, die `CAMERA`-Permission ist in `android-permissions.xml` vorhanden.
  Kompression über `compressUploadFiles()` aus `media-upload.js` (Funktion für den Einheitenmodus
  exportieren). Pro Bild ein Outbox-Eintrag. Fortschritt je Bild, optionaler Kommentar.
- **Unterstützung:** sechs große Kacheln (Mannschaft, Fahrzeug, Material/Gerät, Spezialkräfte, Sonstiges,
  **Dringend** in Rot), optionaler Text, Absenden. Danach Statuszeile je Anforderung:
  „ausstehend (Gerät)“ → „eingegangen“ → „in Bearbeitung“ → „erledigt“.
- **Entwürfe:** Jede Eingabe wird laufend in IndexedDB gespeichert (`entwuerfe`, Schlüssel `dispatch_id+art`).
  Live-Updates, Auftragswechsel oder App-Neustart verwerfen keine Entwürfe. Eine Auftragsänderung
  während eines offenen Entwurfs blendet „Auftrag wurde geändert – bitte prüfen“ über dem Formular ein,
  der Entwurf bleibt erhalten.

### 6.5 Outbox (`einheit_outbox.js`)

IndexedDB `ec-einheit`, Stores: `outbox`, `entwuerfe`, `zustand_cache`.

```js
{ client_uuid, typ, dispatch_id, einheit_id, auftrag_version, payload, blob?,
  erfasst_at, versuche, status: "ausstehend"|"sendet"|"fehler"|"konflikt",
  letzter_fehler, server_bestaetigt_at? }
```

- **Sendet streng in Erfassungsreihenfolge je Auftrag** (Statusfolgen wie Anfahrt → Vor Ort bleiben korrekt).
  Fotos laufen in einer eigenen Spur, damit große Uploads keine Statusmeldungen blockieren.
- Auslöser: sofort nach Erfassung, `online`-Event, WS-Reconnect, `visibilitychange`, Timer mit Backoff
  (2 s, 4 s, 8 s … max. 60 s), manueller Button „Jetzt senden“.
- **Erfolg** nur bei `2xx` mit `aktion_id`. Erst dann wird der Eintrag gelöscht und das Element mit
  „✓ übermittelt HH:MM“ angezeigt. Vorher zeigt die UI immer „⏳ nur auf diesem Gerät“.
- `409 auftrag_zurueckgezogen` bei einer Statusmeldung → Eintrag `konflikt`, Hinweis
  „Auftrag wurde zurückgezogen, Status nicht übernommen“. Meldungen und Fotos kommen trotzdem an (siehe 5.1).
- `409 einheit_gewechselt` / `403` → `konflikt`, der Inhalt bleibt sichtbar und kopierbar und wird nie
  automatisch gelöscht. Verwerfen nur manuell mit Rückfrage.
- Netzfehler/5xx → `fehler`, automatischer Retry. 4xx (außer 409/413) → `fehler` ohne Auto-Retry, mit
  „Erneut senden“ und „Verwerfen“.
- Foto-Abbruch mitten im Upload: der Blob bleibt in IndexedDB, Retry sendet die Datei komplett neu mit
  derselben `client_uuid`. Hat der Server die Datei bereits gespeichert, liefert er die gespeicherte Antwort
  (Idempotenz), es entsteht kein Doppelbild.
- `navigator.storage.persist()` beim ersten Start anfordern. Speicherwarnung ab 80 % der Quota.
- Optimistische Anzeige: Statusbutton zeigt den neuen Status sofort mit Uhr-Symbol (ausstehend). Die
  serverseitige Auftragsliste wird nie überschrieben, solange keine Bestätigung vorliegt.

### 6.6 Führungsbereich 🔧

| Ort | Erweiterung |
|---|---|
| `_site_card.html` | je disponierter Einheit ein farbiger Status-Chip (Kürzel + Status) statt nur „n alarmiert / n vor Ort“; 🆕-Badge „neue Lagemeldung“ (Einträge mit `einheit_id`, jünger als die letzte Ansicht → clientseitig über `localStorage` je Stelle); 📷-Zähler (bestehend) aktualisiert sich jetzt per Broadcast; 🆘-Chip bei offener Anforderung (rot bei `dringend`); ⏱-Chip „keine Rückmeldung seit n min“, wenn `letzte_rueckmeldung_at` älter als das Lagemeldungsintervall der Org (`interval_minutes_for`); Hinweis „Alle Einheiten fertig“ |
| `_site_detail.html` (Z. 310 ff.) | Dispositionsliste mit Einheitenstatus, Kennzeichnung 📱 Tablet / 📻 Funk, stellvertretende Statusbuttons für Funk-Einheiten, Zeitstempeln (zugewiesen/bestätigt/vor Ort/beendet), Auftragstext inline editierbar, Reihenfolge, Buttons „Rückfrage senden“, „Lagemeldung anfordern“, „Wiedereröffnen“; Chronik markiert Einheiten-Einträge |
| `funkjournal.html` / `_funkjournal_rows.html` | Spalte Einheit, Art-Badge (Rückfrage/Anforderung/Gefahr/Antwort), Quittierstatus, Buttons „In Bearbeitung“/„Erledigt“, Filter „offene Anforderungen“; Antworten unter der Rückfrage eingerückt |
| `ressourcen.html`, `_kraefteuebersicht.html` | Spalte aktueller Auftrag + Einheitenstatus + letzte Rückmeldung |
| `board.html` Kopf (`_lage_kopf_oob.html`) | Zähler „🆘 n offene Anforderungen“ und „📻 n kritisch – Funk ausstehend“ als Links auf die Funkjournal-Filter |
| `lage_board.js` | `einheit:changed` → Karte `site_id` neu laden (gleich wie `site:card_changed`) |
| `_lage_layout.html` / `board.html` (Gesamtansicht auf Einheit-Tablets) | `can_edit`/`can_manage`/`can_note` = `False` → bestehende Bedingungen blenden Bearbeitungselemente aus; Kopfbutton „← Meine Einsätze“; eigene Stellen auf Board und Lagekarte hervorgehoben (Rahmen in Einheitenfarbe); Banner „Nur-Lese-Ansicht“ |

---

## 7. Android-App

Die App ist ein Capacitor-Wrapper. Der Einheitenmodus läuft vollständig in der WebView und funktioniert
**ohne neues APK**. Android-Änderungen sind Komfort und Betriebssicherheit.

| Bereich | Phase | Art | Änderung |
|---|---|---|---|
| Widget-Link | 1 | ✅ ohne APK | Backend setzt `my_lage_queue.lage_url = "/einheit"` für Einheit-Geräte, `EcpWidgetSupport.renderGsl` öffnet damit den Einheitenmodus |
| Widget-Inhalt | 2 | 🔧 | `GslQueueState.kt`/`GslSiteInfo`: additive Felder `einheit_url`, `auftrag`, `einheit_status` parsen; `renderGsl` zeigt Status-Chip und Auftrag; Klick auf eine Zeile öffnet `/einheit/auftrag/{id}` statt der Lage |
| Push | 2 | ✅/🔧 | `notify_vehicle()` liefert FCM bereits mit `url`; `EinsatzPreloadWorker` lädt die Ziel-URL vor ✅. Neu: `data.kind = "einheit_auftrag"` → `EinsatzWidgetRefreshWorker` sofort anstoßen. Kein eigener Kanal für kritische Nachrichten – diese gehen immer per Funk (E6), Push ist nur Ergänzung |
| Kamera | 1 | ✅ | WebView-Dateiauswahl mit `capture` (Capacitor `BridgeWebChromeClient`), `CAMERA`-Permission vorhanden. Auf dem Zielgerät verifizieren (siehe Tests) |
| Diktat | 3 | 🆕 optional | `@capacitor-community/speech-recognition` + `RECORD_AUDIO`. Bis dahin Tastaturmikrofon |
| Hintergrund-Sync der Outbox | 3 | 🆕 | `EinheitOutboxWorker` (WorkManager, periodisch 15 min + bei Netzrückkehr per `NetworkCallback`) lädt `/einheit?flush=1` in einer Headless-WebView (Muster `ObjektOfflineSyncWorker.kt`). Status meldet er über ein per `addJavascriptInterface` injiziertes Interface (Lehre aus Android-PR #43: **nicht** über Capacitor-Plugins, die im Headless-Kontext fehlen) in `OfflineCacheStatusStore` → sichtbar in „Über die App“ |
| Keep-Awake im Einheitenmodus | 3 | ✅/🔧 | `ELNative.keepAwake(true)` auf `/einheit` (Plugin vorhanden) |
| Standort | 1 | ✅ | `native-bridge.js` sendet bei `should_track` (Fahrzeug als LageEinheit `im_einsatz`, `device_api.py` Z. 432 ff.) an `/api/v1/device/location` → `VehiclePosition` + `vehicle:position` |
| Offline-Start | 1 | ✅/🔧 | `www/index.html` springt offline auf `/`. 🔧 Bei gespeichertem Flag „letzte Ansicht Einheitenmodus“ direkt `/einheit` öffnen (aus dem SW-Cache) |

Release über CalVer-Tag wie gewohnt (`build-apk.yml`). Phase 1 benötigt kein Release.

---

## 8. MCP

Bestand: keine GSL-Tools. Geräte-Benutzer sind von MCP ausgeschlossen (`context.py`), das bleibt so.
MCP-Nutzer sind Personen (Einsatzleitung, Stab, Einheitsführer mit eigenem Login), die im Namen einer
Einheit handeln. Neue Datei `app/mcp/tools/gsl.py` (Phase 3), `module_check` = GSL-Modul der Org aktiv
(Logik aus `_get_mi_features`, `ui_major_incident.py:105`, in eine Service-Funktion verschieben).

| Tool | Rollen | Service |
|---|---|---|
| `gsl_lagen_liste` | Leserollen | `gsl_live_service.build_gsl_live_payload` |
| `gsl_einheit_auftraege` (`lage_id`, `einheit_id`) | Leserollen | `einheit_service.auftraege_fuer_einheit` (Kontext ohne Gerät: `EinheitKontext` aus `einheit_id` nach Org-Prüfung) |
| `gsl_einsatzstelle_lesen` | Leserollen | `einheit_service.auftrag_detail` / Site-Serializer |
| `gsl_einheit_status_setzen` | `incident_leader`, `recorder` | `setze_einheit_status` |
| `gsl_lagemeldung_erfassen`, `gsl_massnahme_erfassen` | `incident_leader`, `recorder`, `readonly` (wie `site_log_add`) | `site_log_service.add_site_log` |
| `gsl_unterstuetzung_anfordern` | wie oben | `funkjournal_service.anforderung_erstellen` |
| `gsl_rueckfragen_offen`, `gsl_rueckfrage_beantworten` | wie oben | `funkjournal_service` |
| `gsl_auftrag_bestaetigen`, `gsl_auftrag_abschliessen` | `incident_leader`, `recorder` | Kurzformen von `setze_einheit_status` |
| `gsl_foto_upload_vorbereiten` | wie Lagemeldung | Muster `mcp_upload_service.py` (Upload-URL + Token), Abschluss über `lage_media_service.speichere_site_foto` |

Regeln: dieselben Rollenkonstanten wie die Routen (keine eigene Rechte-Matrix); `org_id` aus `MCPContext`;
jede Mutation mit `write_audit(…, payload={"via": "mcp", …})`; `author_name = f"{user.display_name} (MCP)"`;
schreibende Tools ändern `IncidentSite.phase` nur über die E3-Anhebung in `setze_einheit_status` und nie `priority`; kritische Nachrichten über MCP erzeugen ebenfalls einen Funkauftrag (E6). Zusätzlicher Test in
`tests/test_mcp_*`-Muster: Cross-Org-Zugriff auf `einheit_id` → Fehler.

---

## 9. Sicherheit

### 9.1 Autorisierung im Einheitenmodus

- Jeder Request löst `EinheitKontext` neu auf (3.3). Es gibt keinen Cache der Berechtigung.
- Lesen im Einheitenmodus (`/einheit/api/*`): Stellen mit einer Disposition der eigenen Einheit (inkl.
  beendeter und zurückgezogener, damit die Historie sichtbar bleibt).
- Lesen in der Gesamtansicht (E1): die ganze Lage über die bestehenden Führungsrouten, **nur lesend**
  (9.2). Damit ist auch die Medienauslieferung `/lage-medien/{id}` lesend erlaubt.
- Schreiben: ausschließlich über `/einheit/api/*` und nur auf eigene Dispositionen.
- Nicht erlaubt (serverseitig, es gibt schlicht keine Route dafür): Phase/Priorität der Stelle ändern,
  disponieren, abziehen, Lage beenden, löschen, fremde Einheiten ändern, Reihenfolge ändern.

### 9.2 Bestehende Lücke schließen: Geräte mit Rollen im Führungsbereich 🔧

Router-weite Dependency `einheit_geraet_nur_lesen` auf `ui_major_incident.router`, `ui_gsl_staff`,
`ui_lagedokument`. Gilt nur, wenn `request.state.is_device` und `gsl_profil == "einheit"`:

- **Allowlist statt Denylist.** `EINHEIT_GESAMTANSICHT_ROUTEN` (in `einheit_service.py`) listet die
  erlaubten `GET`-Routen per Route-Name: Board `/lage/{id}`, Kopf, Phasen-Inhalt, Board-Karte,
  Einsatzstellen-Detail, Druck einer Stelle, Lagekarte inkl. `karte-sites`/`-sektoren`/`-cross-markers`,
  `fahrzeuge/positionen`, Funkjournal + Zeilen, Stab-Tafel/-Journal (lesend), Ressourcen/Kräfteübersicht,
  Übergreifende Meldungen (Panel/Spalte), `/lage-medien/*`, Lagedokument-Druck.
  Alles andere → `403`; insbesondere **jede** nicht-`GET`-Methode, Token-ausgebende Routen
  (`/qr`, `/qr-login`, `/qr-pin`, `/meldungen/qr`, `/meldungen/token`), Bearbeitungsformulare
  (`/bearbeiten`, `/lage/neu`) und der Lagedokument-Editor samt Collab-WebSocket.
  Neue Führungsrouten sind damit für Einheit-Tablets automatisch gesperrt, bis sie bewusst freigegeben werden.
- Templates erhalten `can_edit=can_manage=can_note=False` (zentral in `_can_edit()`/`_can_note()`/
  `_can_manage()`, `ui_major_incident.py:234 ff.`), damit keine Bedienelemente erscheinen, die 403 liefern würden.
- Die öffentlichen Routen `/melden/*` laufen ohne Benutzer und sind nicht betroffen.
- Geräte mit `fuehrung`/`NULL` behalten das bisherige Verhalten (KDO-Tablet der Einsatzleitung).

### 9.3 Sessions, Widerruf, Gerätewechsel

| Szenario | Verhalten |
|---|---|
| Tablet verloren | Admin widerruft das `DeviceToken` → nächster HTTP-Request 401 (bestehend `main.py:577`); 🔧 WS-Handshake prüft künftig ebenfalls `device_token_id` gegen `revoked_at` (`ws.py::_resolve_user`), bestehende Sockets werden beim nächsten Broadcast-Fehler oder spätestens beim Reconnect getrennt; 🆕 zusätzlich `broadcast_org(type="device:revoked", device_token_id)`, das Tablet schließt den Socket und leert `zustand_cache` |
| Tablet in anderes Fahrzeug | `POST /admin/geraete-login/{id}/fahrzeug` ✅ → Kontext wechselt sofort; Outbox-Einträge der alten Einheit → `409 einheit_gewechselt` (6.5) |
| Einheit bekommt anderes Fahrzeug (`LageEinheit.vehicle_id` geändert) | Kontext folgt automatisch; altes Tablet verliert den Zugriff mit dem nächsten Request |
| Zwei Tablets im selben Fahrzeug | beide sehen dieselbe Einheit; Statuswechsel last-write-wins in Server-Reihenfolge; die Chronik zeigt das Gerätelabel als Autor (`author_name` = `DeviceToken.label`, ggf. + Einheitsführer aus `LageEinheitLeader`) |
| Token-Ablauf | Geräte-Sessions laufen bewusst nicht ab (`security.py`); Kontrolle ausschließlich über Widerruf |
| Lage beendet | Kontext `None` → Tablet zeigt „Lage beendet“; ausstehende Outbox-Einträge → `409 lage_beendet`, bleiben lokal sichtbar |

### 9.4 Audit und Nachvollziehbarkeit

- `write_audit` für jede Mutation: `gsl.einheit.status`, `gsl.einheit.meldung`, `gsl.einheit.foto`,
  `gsl.einheit.anforderung`, `gsl.einheit.antwort`, `gsl.einheit.quittierung`, `gsl.auftrag.geaendert`,
  `gsl.auftrag.wiedereroeffnet`. Payload: `lage_id`, `site_id`, `dispatch_id`, `einheit_id`,
  `device_token_id`, `client_uuid`, `erfasst_at`.
- Fachliche Spur: `SiteLogEntry` (Stellenchronik), `LageJournalEntry` (Stab), `CommLogEntry` (Funkjournal),
  `einheit_aktion` (Geräteprotokoll). Zeitreise (`/lage/{id}/zeitreise`) und Druckbericht zeigen die
  neuen Einträge über die bestehenden Tabellen ohne Zusatzarbeit; 🔧 `druck_bericht.html` um Einheitenstatus
  je Disposition ergänzen.

### 9.4a Simulation (E7)

- Nur Admin-Rollen, nie Geräte- oder QR-Sessions; Header `X-EC-Einheit-Sim` wird bei allen anderen
  Benutzern ignoriert und protokolliert (`gsl.einheit.simulation_abgelehnt`).
- Org-Prüfung über die Lage der Einheit; fremde `einheit_id` → 404.
- Schreibzugriff nur in Übungslagen; jede Simulationsaktion ist in Chronik, Funkjournal, `einheit_aktion`
  (`device_token_id = NULL`, `quelle = simulation`) und Audit eindeutig als Simulation erkennbar.

### 9.5 Mandantentrennung

- Kontextauflösung filtert `lage.org_id == user.org_id`; `einheit_aktion` ist `TenantScoped`.
- Die Tablet-Routen sind nicht öffentlich (Session/Bearer), CLAUDE.md verlangt den Cross-Org-Test
  nur für Public-Routen. Trotzdem neue Fälle in `tests/test_gsl_tenant_isolation.py`: Gerät Org A
  mit `dispatch_id`/`media_id`/`comm_id` aus Org B → 404.
- Bilder: Speicherpfad über `_site_dir(site_id, org_id)` ✅.

---

## 10. Tests

Pflicht vor Merge: `ruff`, `mypy`, `pytest` und ab P1-4 zusätzlich der neue JS-Job (10.5).
Browser-E2E nur auf ausdrückliche Anforderung (Projektregel).

### 10.1 Unit/Service (`tests/test_einheit_service.py` 🆕)

- Kontextauflösung: kein Fahrzeug, Fahrzeug ohne LageEinheit, Lage nicht aktiv, `gsl_profil` ≠ einheit,
  widerrufenes Token, zwei aktive Lagen.
- Statusmatrix vollständig (erlaubt/verboten/idempotent), `unterbrechen`, Exklusivität des aktiven Auftrags,
  `incident_site_id`-Zeiger, Timer (`ensure_timer`) bei vor_ort/in_arbeit.
- E3: `vor_ort`/`in_arbeit` hebt `phase` aus `eingegangen`/`erkundung`/`bewertet`/`disponiert` auf
  `in_arbeit` (inkl. Logzeile, Audit, Timer, `site_phase_changed`); aus `in_arbeit`/`erledigt`/`abgebrochen`
  keine Änderung; `abgeschlossen`/`nicht_durchfuehrbar`/`bestaetigt`/`anfahrt` ändern die Phase nie;
  verspätetes Offline-„Vor Ort“ nach `erledigt` → Phase bleibt `erledigt`.
- Regression: manueller Phasenwechsel über `…/phase` verhält sich nach der Extraktion identisch.
- Kategorisierung und Sortierung (Reihenfolge > Priorität > Zeit).
- `build_my_lage_queue` liefert für Bestandsdaten dasselbe Format wie vorher (Regression zu
  `tests/test_device_duty_state_live.py`, `tests/test_gsl_live.py`).
- `dispatch_aktiv_filter`: Zähler, Duplikatprüfung, Vor-Ort-Konflikt, `has_active_resource` mit beendeten Dispositionen.
- Idempotenz: gleiche `client_uuid` zweimal → eine Zeile, identische Antwort; fremdes Gerät → 409.

### 10.2 Integration/API (`tests/test_einheit_api.py` 🆕, `tests/test_gsl_tenant_isolation.py` 🔧)

- Fremdes Tablet greift auf nicht zugewiesene Stelle zu (GET/POST Status/Meldung/Foto/Medien) → 404.
- Einheit-Gerät: `GET /lage/{id}`, Stellen-Detail, Lagekarte → 200 ohne Bearbeitungselemente;
  `…/phase`, `…/prio`, `…/einheit-disponieren`, `/lage/{id}/beenden`, `/lage/{id}/qr` → 403.
- Vollständigkeitstest: iteriert über `app.routes` aller GSL-Router und prüft, dass jede Route für
  Einheit-Geräte entweder in `EINHEIT_GESAMTANSICHT_ROUTEN` steht (und `GET` ist) oder 403 liefert.
- Funk-Stellvertretung: Führung setzt Status einer Einheit ohne Tablet → gleiche Logzeile mit „(per Funk)“,
  Audit `quelle=funk`; Benutzer mit nur `readonly` → 403; danach eintreffende Tablet-Aktion derselben
  Einheit wird korrekt eingeordnet.
- Führungs-Gerät (`gsl_profil=fuehrung`) behält den Zugriff (Regression).
- Widerrufenes Token: HTTP 401, WS-Handshake wird abgelehnt (`tests/test_lage_ws_cleanup.py`-Muster).
- Broadcasts: Statuswechsel → `site:card_changed` + `einheit:changed`; Medien-Upload → `site:card_changed` (neu).
- Push: `notify_vehicle` wird nach Commit aufgerufen (Mock), bei Rollback nicht.
- Startseite: Einheit-Gerät + aktive GSL → 302 `/einheit`; ohne GSL → unverändert; parallel aktiver Einzeleinsatz → unverändert.

- Simulation (E7): `recorder`/`incident_leader` ohne Admin → 403; Geräte-User mit Sim-Header → Header
  wird ignoriert; Einheit aus Org B → 404; Echtlage: GET 200, POST 403 `simulation_nur_lesend`;
  Übungslage: POST 200 mit `quelle=simulation`, Autor-Suffix „(Simulation …)“, kein `notify_vehicle`-Aufruf;
  die simulierte Sicht enthält exakt dieselben Aufträge wie der Zustand eines echten Tablets der Einheit.

### 10.3 Pflichtszenarien

| # | Szenario | Testebene | Erwartung |
|---|---|---|---|
| S1 | Zwei Einheiten bearbeiten dieselbe Stelle | API | unabhängige `einheit_status`; Karte zeigt zwei Chips; erste „Vor Ort“-Meldung hebt die Phase auf `in_arbeit`, die zweite ändert nichts |
| S2 | Drei neue Einsätze nacheinander | API + WS-Mock | Zustand enthält 3 Aufträge in Reihenfolge; 3 `einheit:changed`; Widget-Queue current + 2 upcoming |
| S3 | Auftrag ändert sich während offener Lagemeldung | API + JS-Unit | Meldung mit alter `auftrag_version` angenommen, `hinweis=auftrag_geaendert`; Entwurf bleibt (JS) |
| S4 | Rückzug während Offline-Phase | API | Statusaktion → 409 `auftrag_zurueckgezogen`; Lagemeldung/Foto → 200 mit Rückzugsvermerk |
| S5 | Foto-Upload bricht ab | API | zweiter Upload mit gleicher `client_uuid` → keine zweite `SiteMedia`, gleiche Antwort |
| S6 | Neustart mit mehreren unsynchronisierten Aktionen | JS-Unit (10.5) + E2E | Outbox überlebt Reload, Reihenfolge bleibt erhalten, Status „ausstehend“ bis 2xx |
| S7 | Einheit abgeschlossen, Stelle offen | API + Template | `abgeschlossen`, `phase` bleibt `in_arbeit`, Karte zeigt „Alle Einheiten fertig“ |
| S8 | Fremdes Tablet manipuliert | API | 404/403, keine Datenänderung, kein Audit-Eintrag mit Erfolg |
| S9 | Kritische Rückfrage + Quittierung | API | `kritisch` → Funkauftrag „Funk ausstehend“ im Kopfzähler; Tablet zeigt Pflichtbanner mit Funkhinweis; Tablet-Quittierung setzt `quittiert_at`, der Funkauftrag bleibt offen bis „per Funk übermittelt“; Führung sieht beide Zeitpunkte |
| S10 | Tablet wechselt Fahrzeug/Einheit mitten in der Lage | API | neuer Kontext sofort; Outbox-Altaktion → 409 `einheit_gewechselt` |

### 10.4 Offline / Mehrgeräte / E2E

- `e2e/test_einheit_modus.py` 🆕 (Playwright, `seed_board_ci.py` erweitern): Board-Fenster + Tablet-Fenster
  (Geräte-Cookie, Viewport 1280×800). Ablauf: disponieren → Karte erscheint ohne Reload → Status
  Vor Ort → Board-Chip ändert sich → `context.set_offline(True)` → Lagemeldung + Foto → UI „ausstehend“
  → Reload → weiterhin ausstehend → online → „übermittelt“ → Board zeigt Meldung.
  Offene Eingabe bleibt bei WS-Update erhalten (analog `e2e/test_board_kein_reload.py`).
- JS-Unit für die Outbox siehe 10.5.
- Manuelle Gerätetests (Checkliste im PR): Kamera über `capture` auf dem Zieltablet, Upload im Funkloch
  (Flugmodus während des Uploads), App-Kill mit ausstehender Outbox, zwei Tablets im selben Fahrzeug,
  Widget-Klick öffnet `/einheit`, Push bei Neuzuteilung.

### 10.5 JS-Tests für die Offline-Outbox (Vorschlag zu E4) 🆕

Ziel: Die heikelste Logik (nichts geht verloren, nichts wird doppelt gesendet, nichts wird fälschlich als
übermittelt angezeigt) wird bei jedem PR in Sekunden geprüft – ohne Browser und ohne Docker.

**Werkzeuge – bewusst minimal:**

| Baustein | Wahl | Begründung |
|---|---|---|
| Testrunner | eingebauter `node --test` (Node ≥ 20) | kein Framework, keine Konfiguration |
| IndexedDB | `fake-indexeddb` (einzige neue devDependency) | echte IndexedDB-Semantik in Node; „Neustart“ = neue Outbox-Instanz auf derselben Fake-DB |
| Netz | injizierte `fetch`-Funktion (Fake mit Skript: 200, 500, Timeout, 409, abgebrochener Upload) | deterministisch |
| Zeit | injizierte `now()`/`sleep()` | Backoff ohne echte Wartezeit |

**Code-Struktur, damit das testbar ist:** `app/static/js/einheit_outbox.js` wird als ES-Modul mit reiner
Logik geschrieben – `createOutbox({ idb, fetch, now, sleep, uuid })` – ohne DOM-Zugriff. Die
Verdrahtung mit `window.indexedDB`, `fetch`, `online`-Events und der Alpine-UI passiert in
`einheit_modus.js` (Einbindung per `<script type="module">`, kein Build-Schritt; die App bleibt ohne Node
deploybar wie heute).

**Dateien:**

- `package.json` 🔧: `"test:js": "node --test tests/js/"`, devDependency `fake-indexeddb`.
- `tests/js/einheit_outbox.test.mjs` 🆕
- `.github/workflows/ci.yml` 🔧: vierter Job `js` (`actions/setup-node` mit Node 20, `npm ci`,
  `npm run test:js`, Laufzeit < 30 s).
- `CLAUDE.md` 🔧: Abschnitt „Vor jedem Commit“ um `npm run test:js` ergänzen (nur nötig, wenn JS unter
  `app/static/js/einheit_*` geändert wurde).

**Testfälle:**

1. Eintrag bleibt nach „Neustart“ (neue Instanz, gleiche DB) erhalten, Status `ausstehend`.
2. Reihenfolge je Auftrag: Anfahrt → Vor Ort → In Arbeit werden strikt nacheinander gesendet, auch wenn
   der erste Versuch fehlschlägt.
3. Foto-Spur blockiert die Status-Spur nicht (großer Upload hängt, Statusmeldung geht trotzdem raus).
4. Erfolg nur bei 2xx **mit** `aktion_id`; 200 ohne `aktion_id`, 204, Netzwerkfehler oder SW-Cache-Antwort
   (`X-EC-Offline: 1`) gelten nicht als übermittelt.
5. Retry nach Timeout verwendet dieselbe `client_uuid` (Idempotenz serverseitig).
6. 409 `auftrag_zurueckgezogen` → `konflikt`, kein Auto-Retry, Inhalt bleibt lesbar.
7. 409 `einheit_gewechselt` / 403 → `konflikt`, wird nie automatisch gelöscht.
8. 4xx → `fehler` ohne Auto-Retry, manuelles „Erneut senden“ funktioniert; 5xx → Backoff 2/4/8 … 60 s.
9. Abgebrochener Foto-Upload: Blob bleibt gespeichert, Neuversuch sendet vollständig erneut.
10. Zähler „ausstehend/fehlgeschlagen“ stimmen in jedem Zustand.

Der Browser-Teil (echte WebView, Service Worker, Reload) bleibt im E2E `e2e/test_einheit_modus.py`
und in der manuellen Gerätecheckliste (10.4).

---

## 11. Migration

### 11.1 Datenbank

- `0263_gsl_einheitenmodus.py` (Phase 1): Spalten an `einheit_site_dispatch`, `site_log_entry`,
  `site_media`, `device_token.gsl_profil`; Tabelle `einheit_aktion`; Index.
  Backfill: `einheit_status = 'vor_ort'` wo `vor_ort_at IS NOT NULL`, sonst `'zugewiesen'`;
  `status_at = COALESCE(vor_ort_at, dispatched_at)`. `device_token.gsl_profil` bleibt NULL
  (Altbestand = bisheriges Verhalten).
- `0264_gsl_einheit_kommunikation.py` (Phase 2): Spalten an `comm_log_entry`.
- Muss in CI gegen **MariaDB** laufen (`alembic upgrade head`): Server-Defaults als `sa.text("'zugewiesen'")`,
  Boolean-Defaults `sa.false()`, keine SQLite-spezifischen Konstrukte, Backfill per gefiltertem `UPDATE`
  (eine Lage-unabhängige Tabelle ohne Tenant-Listener, daher unkritisch, im Migrationskommentar begründen).
- Downgrade entfernt nur die neuen Spalten/Tabellen.

### 11.2 Rückwärtskompatibilität

| Bereich | Maßnahme |
|---|---|
| `duty-state.my_lage_queue` | Format bleibt, nur additive Felder; ältere APKs funktionieren weiter (öffnen über `lage_url` den Einheitenmodus) |
| Bestehende Geräte | `gsl_profil = NULL` → keine Verhaltensänderung bis der Admin „Einheit“ setzt. Admin-UI zeigt bei Geräten mit Fahrzeug den Hinweis „Für den GSL-Einheitenmodus auf ‚Einheit‘ stellen“ |
| Führungs-UI | Disponieren ohne Auftragstext bleibt möglich; Chips fallen auf „disponiert/vor Ort“ zurück, wenn kein Tablet beteiligt ist |
| Laufende Lagen beim Deploy | Backfill macht bestehende Dispositionen sofort nutzbar |
| `SiteLogEntry.kind="einheit"` | ältere Templates zeigen unbekannte Kinds bereits roh an; Label in `SITE_LOG_KIND_LABEL` ergänzen |

---

## 12. Umsetzungsphasen

Jedes Paket ist ein eigener PR mit grünem `ruff`/`mypy`/`pytest`. Abhängigkeiten in Klammern.

### Phase 1 – MVP

| PR | Inhalt | Akzeptanzkriterien |
|---|---|---|
| **P1-1** Datenmodell | Migration 0263, Modellfelder, `EinheitAktion`, `dispatch_aktiv_filter` inkl. Umstellung aller 11 Stellen, Labels/Farben | Migration läuft auf MariaDB in CI; alle bestehenden GSL-Tests grün; Backfill-Test |
| **P1-2** Service-Extraktion (P1-1) | `site_log_service`, `speichere_site_foto`, `funkjournal_service.add_comm_entry`; Router auf Services umstellen; Broadcast nach Medien-Upload und Funkjournal | Kein Verhaltensunterschied in der Führungs-UI (bestehende Tests), neue Broadcasts getestet |
| **P1-3** Einheiten-Kontext + Sicherheit (P1-1) | `einheit_service` (Kontext, Auftragsliste, Statusmaschine inkl. `quelle`), `major_incident_service.setze_site_phase` + E3-Anhebung, `require_einheit_geraet`, `einheit_geraet_nur_lesen` mit Allowlist (Gesamtansicht), WS-Widerrufsprüfung, `gsl_profil` in der Geräte-Admin-UI, `build_my_lage_queue` als Adapter | S1, S7, S8, S10 grün; Einheit-Gerät sieht die Gesamtansicht ohne Bearbeitungselemente und erhält 403 auf alle schreibenden Führungsrouten; Routen-Vollständigkeitstest grün; Widget-Format unverändert |
| **P1-4** Tablet-UI (P1-2, P1-3) | Admin-Simulation (5.2a, Einstieg „Als Einheit ansehen“), `_fab_styles.html` aus dem Fahrtenbuch extrahieren, `ui_einheit.py`, `einheit.html` im Fahrtenbuch-Design, Umschalter Meine Einsätze/Gesamtansicht, JS-Testjob (10.5), `einheit_modus.js`, `einheit_outbox.js` (persistente Outbox mit manuellem und automatischem Retry, Idempotenz), Status/Lagemeldung/Maßnahme/Notiz/Foto, Startseiten-Redirect, SW-Regeln | S2–S6 auf API-Ebene und Outbox-Testfälle 1–10 grün; Simulationstests grün; der Einheitenmodus lässt sich in einer Übungslage vollständig per Simulation durchspielen; Fahrtenbuch optisch unverändert; offline erfasste Aktionen gehen bei Reload nicht verloren und werden nie als übermittelt angezeigt; Live-Update ohne Verlust offener Eingaben |
| **P1-5** Führungsansicht (P1-3) | Chips auf Board-Karte, Dispositionsliste im Site-Detail mit Status, Auftragstext und 📱/📻-Kennzeichnung, stellvertretende Statuserfassung „per Funk“, Disponieren mit Auftragstext, `einheit:changed` in `lage_board.js`, Kräfteübersicht | Führung sieht Statuswechsel einer Einheit ohne Reload; „keine Rückmeldung seit“ erscheint nach Intervall; Funk-Einheiten lassen sich vollständig ohne Tablet führen |

Optional am Ende von Phase 1: E2E `e2e/test_einheit_modus.py` (auf Anforderung).

### Phase 2 – Erweiterte Kommunikation

| PR | Inhalt | Akzeptanzkriterien |
|---|---|---|
| **P2-1** Kommunikationsmodell (P1-2) | Migration 0264, `funkjournal_service` (Rückfrage, Anweisung, Antwort, Quittierung, Anforderung mit Status, Gefahr, kritische Nachricht als Funkauftrag) | S9 grün; Anforderungsstatus eingegangen → in Bearbeitung → erledigt sichtbar am Tablet |
| **P2-2** Tablet-Kommunikation (P1-4, P2-1) | Nachrichtenbanner, Pflichtquittierung, Antworten, Unterstützungs-Kacheln, Gefahr melden, Auftrag bestätigen als Primäraktion | max. 2 Taps von der Detailseite bis „Unterstützung gesendet“ |
| **P2-3** Führung-Kommunikation (P2-1) | Funkjournal-Spalten/Filter, „Rückfrage senden“ (Tablet: Push/Banner, Funk-Einheit: „per Funk übermittelt“), „Lagemeldung anfordern“, Auftrag ändern/Reihenfolge, Kopfzähler offene Anforderungen | offene dringende Anforderung ist auf Board-Karte und im Kopf sichtbar |
| **P2-4** Push + Erinnerungen (P2-1) | `einheit_notify` bei Neuzuteilung, Änderung, Rückzug, Rückfrage; Reminder-Loop benachrichtigt Einheiten; erweiterte Journal-Einträge | Push nur nach Commit; Reminder erzeugt keine Doppelbenachrichtigung (Dedup wie `auto_kind`) |
| **P2-5** Android-Widget (P1-3) | `GslQueueState.kt`/`renderGsl`: Status, Auftrag, Deep-Links; FCM `kind=einheit_auftrag` → Widget-Refresh; Release | CI-Build grün; Gerätetest Widget + Push |

### Phase 3 – Betriebssicherheit und Zusatzfunktionen

| PR | Inhalt | Akzeptanzkriterien |
|---|---|---|
| **P3-1** Offline vollständig | Outbox-Konflikt-UI (Liste, Kopieren, Verwerfen), Speicherpersistenz/-warnung, Offline-Start direkt in `/einheit`, Android `EinheitOutboxWorker` (Headless-WebView) | Flugmodus-Szenario inkl. App-Kill auf echtem Gerät ohne Datenverlust |
| **P3-2** Navigation mit Sperren | Phase 1 zeigt bereits Sperren am Ziel (`relevant_closures(dest=…)`) + Hinweis „Externe Navigation berücksichtigt keine ECP-Sperren“. Phase 3: `evaluate_route()` von eigener Position (bei `EINSATZ_ROUTING_ENABLED`), betroffene Sperren + Ausweichstraßen, Kartenlayer `road_closure_map.js`; optional Google-Maps-Link mit Wegpunkten der Alternative | Sperre auf der Route wird angezeigt; ohne Routing-Provider bleibt der deutliche Hinweis |
| **P3-3** MCP-Tools | `app/mcp/tools/gsl.py` laut Abschnitt 8, Doku `docs/wiki/Administration-MCP-Server.md` | Cross-Org-Test, Rollen identisch zu den Routen, Audit `via=mcp` |
| **P3-4** Android-Komfort | Keep-Awake, Sprachdiktat-Plugin, Video-Upload (`media_type="video"`, `MAX_UPLOAD_BYTES_VIDEO`) | Gerätetest |
| **P3-5** Auswertung | Einheiten-Zeitstrahl je Stelle, Reaktionszeiten (zugewiesen → bestätigt → vor Ort), Abschnitt im Druckbericht/Lagebericht | Bericht enthält Einheitenstatus und Zeiten |

### Abhängigkeitsgraph

```
P1-1 ─┬─ P1-2 ─┬─ P1-4 ─ P2-2 ─ P3-1
      │        └─ P2-1 ─┬─ P2-3
      └─ P1-3 ─┬─ P1-4  ├─ P2-4
               ├─ P1-5  └─ P2-2
               └─ P2-5
P3-2, P3-3 benötigen nur Phase 1; P3-4/P3-5 sind unabhängig.
```

---

## 13. Entscheidungen

Am 2026-10-09 getroffen:

| # | Frage | Entscheidung | Auswirkung im Plan |
|---|---|---|---|
| E1 | Dürfen Einheit-Tablets die gesamte Lage sehen? | **Ja**, Wechsel in die Gesamtansicht ist erlaubt – nur lesend | 3.1 (6), 6.6, 9.1, 9.2 (Allowlist), 10.2 |
| E2 | Einheiten ohne Tablet | werden **über Funk zentral instruiert**; Führung/Funker erfasst stellvertretend | 3.1 (7), 5.1 „Stellvertretende Erfassung“, 5.3, 6.6, P1-5, P2-3 |
| E3 | Ändert der Einheitenstatus den Status der Einsatzstelle? | **Ja:** `vor_ort`/`in_arbeit` hebt die Phase automatisch auf `in_arbeit` (nur vorwärts). Abschluss/Abbruch bleibt Führungsentscheidung | 5.1, 5.3, 8, 10.1, S1, S7 |
| E4 | JS-Testinfrastruktur für die Outbox | **Vorschlag:** `node --test` + `fake-indexeddb`, Outbox als reine Logik mit injizierten Abhängigkeiten, vierter CI-Job `js` | 10.5, P1-4 |
| E5 | Stitch-Mockup | **Nein** – bestehendes Fahrtenbuch-Design verwenden, `fab-*`-Styles in ein gemeinsames Partial auslagern | 1.10, 6.1, 6.2, P1-4 |
| E3a | Stelle automatisch „erledigt“, wenn alle Einheiten fertig sind? | **Nein.** Die Board-Karte zeigt „Alle Einheiten fertig – Stelle abschließen?“, die Führung schließt ab | 5.1, S7 |
| E6 | Wie werden kritische Nachrichten übermittelt? | **Immer per Funk.** Kritische Nachrichten der Führung werden Funkaufträge („Funk ausstehend“ bis zur Bestätigung), Tablet und Push sind nur Ergänzung; das Tablet fordert bei dringenden Meldungen zur zusätzlichen Funkmeldung auf. Kein eigener Kanal, kein DND-Bypass | 4.1, 5.1, 5.3, 6.3, 6.6, 7, S9 |
| E7 | Einheitenmodus ohne Tablet ausprobieren? | **Admin-Simulation** je Einheit im Browser: Übungslage schreibend, Echtlage nur lesend, keine Pushs an echte Tablets, klar gekennzeichnet | 3.1 (8), 5.2a, 9.4a, 10.2, P1-4 |

---

## 14. Umsetzungsstand

### Phase 1 – abgeschlossen am 2026-10-09

| Paket | PR | Inhalt |
|---|---|---|
| P1-1 | #479 | Datenmodell, Migration 0263, `einheit_aktion`, `dispatch_aktiv_filter` |
| P1-2 | #480 | Services `site_log_service`, `funkjournal_service`, `speichere_site_foto`; Broadcasts nach Foto/Funkjournal |
| P1-3a | #481 | `einheit_service` (Kontext, Auftragsliste, Statusmaschine, E3), `setze_site_phase` |
| P1-3b | #482, #484 | Gesamtansicht nur lesend (Allowlist), Geräte-Widerruf für WebSockets, `gsl_profil` im Admin, Widget-Adapter |
| P1-4a | #485 | Tablet-API `/einheit/api/*` inkl. Idempotenz und Admin-Simulation (E7); JSON-Fehler unter `/einheit/api/` |
| P1-4c | #486 | Offline-Outbox `einheit_outbox.js` + JS-Tests, CI-Job „JS-Tests (Outbox)“ |
| P1-4b | #487 | Tablet-Oberfläche `/einheit`, Startseiten-Weiterleitung, Service Worker, `_fab_styles.html` |
| P1-5a | #488 | Stellen-Detail der Führung: Einheitenstatus, Funk-Stellvertretung (E2), Auftrag bearbeiten, Wiedereröffnen, Sim-Link (P1-4d) |
| P1-5b | #489 | Status-Chips auf Board-Karten, „Einheiten fertig“, „keine Rückmeldung seit“, Kräfteübersicht |

Abweichungen vom Plan:

- P1-4c wurde vor P1-4b umgesetzt, damit die Oberfläche direkt auf der Outbox aufbaut.
- P1-4d (Simulations-Einstieg) ist in P1-5a/P1-5b enthalten.
- `korrektur.html` des Fahrtenbuchs behält ihre eigene kompakte Style-Kopie: Das Bento-Grid des gemeinsamen Partials würde das Korrekturformular zerlegen.

### Offen vor dem Echteinsatz

- Gerätetest auf einem Android-Tablet: Kamera über `capture`, Upload im Funkloch, App-Neustart mit ausstehender Outbox, zwei Tablets im selben Fahrzeug, Widget-Klick auf `/einheit`.
- Admin: bestehende Fahrzeug-Tablets auf `gsl_profil = "einheit"` umstellen. Altgeräte (`NULL`) behalten das bisherige Verhalten.
- Phase 2 (Kommunikation, Push, Erinnerungen) und Phase 3 (Offline-Hintergrundsync, Navigation mit Sperren, MCP, Auswertung) laut Abschnitt 12.

