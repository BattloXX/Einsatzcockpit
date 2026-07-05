# Objektverwaltung – Implementierungsplan

Stand: 2026-07-05. Modul für die Verwaltung einsatzrelevanter Objekte (BMA-Objekte, Wohnanlagen, öffentliche Gebäude, landwirtschaftliche Betriebe) inkl. PDF-Dokumentenpipeline, Objekt-Lagekarte, Alarm-Matching, Einsatz- und Infoscreen-Ansicht sowie Druck (Objektblatt). Fachliche Referenz: EUS-Altsystem (fweus.at) der FF Wolfurt — Datenstruktur und bewährte Details übernommen, UI neu.

## Leitplanken (bestehende Konventionen)

- Alle neuen Tabellen `TenantScoped` (Mixin zuerst: `class Objekt(TenantScoped, Base)`), Eintrag in `_TENANT_TABLE_NAMES` (`app/core/tenant.py`), Modellmodul eager in `app/models/__init__.py` importieren (bekannter Produktionsbug bei Versäumnis).
- Feature-Flag nach UAS-Muster: `SystemSettings`-Key `objekt_module_enabled` UND `OrgSettings.objekt_module_enabled`, effektiv = beides an. Helfer `objekt_system_enabled`/`objekt_effective_enabled` in `app/services/objekt_service.py`, `request.state.objekt_enabled` in `_resolve_current_org` (`app/core/dependencies.py`), Route-Guard `require_objekt_enabled` (404) analog `app/routers/ui_uas.py`.
- Deutsch in UI/Kommentaren, gerade ASCII-Anführungszeichen, naive UTC in DB + Org-Zeitzone via Jinja-Filter (`local_datetime` etc.), CSRF `_csrf` in POST-Formularen, kein `location.reload` nach HTMX.
- Kein npm am Dev-Rechner: neues CSS händisch in `app/static/css/tailwind.input.css` UND `app/static/css/app.css` (identischer Inhalt).

---

## 1. Datenmodell

Alle Modelle in **`app/models/objekt.py`** (ein Modul; bei Wachstum später `objekt_dokument.py` abspalten). FK-Kaskaden wie im Bestand (CASCADE für Kind-Tabellen des Objekts).

### 1.1 Kern (Migration 0124)

**`objekt_kategorie`** (Katalog je Org): id, org_id (TenantScoped), name String(100), sort Integer default 0, aktiv Boolean default True. Unique `(org_id, name)`. Seed-Defaults: Gewerbe/Industrie, Wohnanlage, Öffentliches Gebäude, Landwirtschaft, Sonderobjekt.

**`objekt`**:

| Spalte | Typ | Bemerkung |
|---|---|---|
| id | Integer PK | |
| org_id | FK fire_dept.id | via TenantScoped |
| nummer | Integer | org-intern laufend, Service vergibt `MAX(nummer)+1` je Org transaktional |
| name | String(200) | Pflicht |
| vulgoname | String(200) null | Alias/Hausname |
| kategorie_id | FK objekt_kategorie SET NULL | |
| strasse / hausnummer / plz / ort | String(200/20/10/100) null | |
| lat / lng | Float null | Geocoding als Background-Task (Muster `_geocode_incident`, api_v1.py) |
| informationen | Text null | Freitext |
| anfahrtsweg | Text null | Freitext |
| status | String(20) default `entwurf` | `entwurf` / `freigegeben` / `in_ueberarbeitung` / `archiviert` |
| revision_datum | Date null | Nachfassung |
| revision_erinnert_am | Date null | Sent-Marker (Muster verleih_erinnerung) |
| erstellt_am / aktualisiert_am | DateTime UTC naiv | |
| erstellt_von_id / aktualisiert_von_id | FK user null | |

Unique `(org_id, nummer)`; Indizes `(org_id, name)`, `(org_id, status)`, `(org_id, revision_datum)`.

Vollständigkeits-Indikator wird **nicht persistiert**, sondern in `objekt_service.berechne_vollstaendigkeit(objekt)` berechnet (Punkte: Adresse+Koordinaten, Kategorie, ≥1 Kontakt, Gefahren gepflegt, BMA-Block falls BMA, ≥1 Dokument, Lagekarte ≥1 Symbol, Revision gesetzt). Liste: Bulk-Counts per `group_by(objekt_id)`, kein N+1.

**`objekt_zusatzadresse`** (Stiegen/Zugänge mit eigener Adresse): id, org_id, objekt_id FK CASCADE, bezeichnung String(100) („Stiege 2", „Zufahrt Nord"), strasse/hausnummer/plz/ort, lat/lng null, sort. Index `(org_id, objekt_id)`.

**`objekt_bma`** (1:1 optional): id, org_id, objekt_id FK unique CASCADE, bma_nummer String(50), rfl_nummer String(50), bmz_standort String(300), fbf_standort String(300), laufkarten_ablageort String(300), uebertragungseinrichtung String(200), schluesselsafe_vorhanden Boolean, schluesselsafe_standort String(300), schluesselsafe_inhalt String(300), benachrichtigung_sms String(100), benachrichtigung_email String(200). Index `(org_id, bma_nummer)` — zentral fürs Alarm-Matching.

**`objekt_change`** (Änderungsprotokoll): **Kopie des IncidentChange-Musters** (`write_incident_change`, feldgenaues before_json/after_json, Timeline-Template `incident/history.html` als Vorlage). AuditLog zusätzlich für grobe Aktionen (Freigabe, Löschung, Upload) via `write_audit(..., entity_type="objekt")`. Spalten: id, org_id, objekt_id FK CASCADE, user_id null, bereich String(50) (stammdaten/bma/gefahren/kontakte/dokumente/karte/status), feld String(100), before_json Text, after_json Text, erstellt_am. Index `(org_id, objekt_id, erstellt_am)`.

**`org_settings`**: + `objekt_module_enabled` Boolean NOT NULL server_default `0`.

### 1.2 Kataloge, Gefahren, Merkmale, Kontakte, Wohnanlage (Migration 0125)

**`gefahren_katalog`**: id, org_id, name String(100), piktogramm_typ String(30) (`ex`, `gas`, `chemie`, `hochspannung`, `pv`, `nh3`, `brandlast`, `sonstig` — steuert Piktogramm), sort, aktiv. Unique `(org_id, name)`. Seeds.

**`objekt_gefahr`**: id, org_id, objekt_id FK CASCADE, gefahr_id FK RESTRICT, un_nummer String(10) null (bei `chemie`), detail Text null, sort. Index `(org_id, objekt_id)`.

**`merkmal_katalog`**: id, org_id, code String(40) null (stabil für Seeds/Badges: `schluesselbox`, `brandschutzplan`, `dlk_stellplatz`, `objektfunk`, `tiefgarage`, `pv`, `feuerwehraufzug`, `sammelplatz`, `gas`, `sprinkler`, `rwa`, …), name String(100), icon String(40) null, sort, aktiv. Unique `(org_id, name)`.

**`objekt_merkmal`**: id, org_id, objekt_id FK CASCADE, merkmal_id FK RESTRICT, hinweis String(300) null (z. B. Standort Schlüsselbox). Unique `(objekt_id, merkmal_id)`.

**`objekt_kontakt`**: id, org_id, objekt_id FK CASCADE, art String(50) (`brandschutzbeauftragter`, `betreiber`, `hausverwaltung`, `schluesseltraeger`, `sonstig`), name String(150), telefone_json Text null (JSON-Liste; UI rendert jede Nummer als `tel:`-Button), email String(200), erreichbarkeit String(200), sort. Index `(org_id, objekt_id)`.

**`objekt_wohnanlage`** (1:1 optional): id, org_id, objekt_id FK unique CASCADE, wohneinheiten Integer, geschosse Integer, stiegen Integer, hausverwaltung_kontakt_id FK objekt_kontakt SET NULL, hinweise Text. UI zeigt dauerhaften DSGVO-Hinweis (nur einsatztaktische Hinweise, sparsam/sachlich).

### 1.3 Dokumente (Migration 0126)

**`objekt_dokument`** (Original-PDF): id, org_id, objekt_id FK CASCADE, dateiname_original String(255), pfad String(500) (relativ), mime String(100), groesse_bytes Integer, seitenzahl Integer, status String(20) (`neu`/`verarbeitung`/`fertig`/`fehler`), fehler_text String(500), hochgeladen_von_id, hochgeladen_am.

**`objekt_dokument_seite`**: id, org_id, objekt_id (denormalisiert), dokument_id FK CASCADE, seiten_nr Integer (1-basiert), einzel_pdf_pfad String(500) (pypdf-Einzelseite, verlustfrei für Sammel-PDF), bild_pfad String(500) (Hi-Res ~150 dpi), thumb_pfad String(500) (~240 px), dokumentart String(30) null (`bma_datenblatt`, `bma_melderplan`, `brandschutzplan`, `gefahrgutdatenblatt`, `lageplan`, `objektinformation`), titel String(200), melderlinien String(100) (kommagetrennt, LIKE-Suche), stand Date, bei_einsatz_drucken Boolean default False, klassifiziert_von_id, klassifiziert_am. Indizes `(org_id, objekt_id, dokumentart)`, `(org_id, objekt_id, dokument_id, seiten_nr)`.

Dokumentart als fixe Python-Konstantenliste in `objekt_dokument_service.py` (fachlich stabile Taxonomie).

### 1.4 Lagekarte (Migration 0127)

**`objekt_karten_objekt`**: id, org_id, objekt_id FK CASCADE, typ String(40) (Symbolcode: `fsd`, `schluesselbox`, `bsp`, `bmz`, `fbf`, `dlk_stellplatz`, `objektfunk`, `sammelplatz`, `feuerloescher`, `hauptzugang`, `nebenzugang`, `stiege`, `aufzug`, `gefahr_ex`, `gefahr_gas`, `gefahr_chemie`, `gefahr_strom`, `gefahr_pv`, `hydrant_ueberflur`, `hydrant_unterflur`), lat/lng Float null, geometry_json Text null (GeoJSON für Linien/Flächen, Muster `Sector.geometry`), label String(100), sort. Index `(org_id, objekt_id)`.

Symbolkatalog client-seitig als Inline-SVG-JS (`objektSymbolHtml(typ)` analog `taktSymbolHtml` in `incident_major/karte.html`) — konsistent mit GSL-Karteneditor, kein Server-Katalog. Wichtig: Die interne Karten-Infrastruktur lebt im GSL-Domain (`incident_major/karte.html`, Leaflet+Geoman-Assets unter `/static/js/`); wiederverwendet werden **Assets + Muster**, nicht die GSL-Tabellen.

### 1.5 Einsatzverknüpfung + Matching (Migration 0128)

**`objekt_einsatz`**: id, org_id, objekt_id FK CASCADE, incident_id FK CASCADE, quelle String(20) (`bma`/`adresse`/`geo`/`manuell`), status String(20) (`bestaetigt`/`vorschlag`), distanz_m Integer null, erstellt_am, bestaetigt_von_id null. Unique `(incident_id, objekt_id)`, Indizes `(org_id, incident_id)`, `(org_id, objekt_id, erstellt_am)`. Lösen = Zeile löschen (+ AuditLog). BMA-/Adress-Treffer → `bestaetigt`, Geo-Treffer → immer `vorschlag`.

**`org_settings`**: + `objekt_geo_match_radius_m` Integer NOT NULL server_default `75`.

### 1.6 Infoscreen (Migration 0129)

**`alarm_infoscreen_token`** (Muster `WeatherDashboardToken`): id, org_id, token String(64) unique (secrets.token_urlsafe), name String(100), aktiv, erstellt_am.

**`org_settings`**: + `alarm_infoscreen_idle_modus` String(20) server_default `'uhr'` (`uhr`/`wetter`/`einsatzliste`), + `alarm_infoscreen_alarm_dauer_min` Integer server_default `60`.

### 1.7 KI-Klassifikation (Migration 0130)

**`objekt_seite_ki_vorschlag`**: id, org_id, seite_id FK CASCADE, dokumentart, titel, melderlinien, stand, begruendung String(300), status (`offen`/`uebernommen`/`verworfen`), erstellt_am, entschieden_von_id, entschieden_am. Index `(org_id, status)`.

**`org_settings`**: + `objekt_ki_klassifikation_enabled` Boolean server_default `0`.

### 1.8 Migrationsübersicht

| Nr | Datei | Inhalt | PR |
|---|---|---|---|
| 0124 | `0124_objekt_grunddaten.py` | objekt_kategorie, objekt, objekt_zusatzadresse, objekt_bma, objekt_change; OrgSettings-Flag | PR1 |
| 0125 | `0125_objekt_kataloge_kontakte.py` | gefahren_katalog, objekt_gefahr, merkmal_katalog, objekt_merkmal, objekt_kontakt, objekt_wohnanlage | PR2 |
| 0126 | `0126_objekt_dokumente.py` | objekt_dokument, objekt_dokument_seite | PR3 |
| 0127 | `0127_objekt_lagekarte.py` | objekt_karten_objekt | PR4 |
| 0128 | `0128_objekt_einsatz_matching.py` | objekt_einsatz; geo_match_radius | PR5 |
| 0129 | `0129_infoscreen_alarm.py` | alarm_infoscreen_token; Idle/Dauer-Settings | PR6 |
| 0130 | `0130_objekt_ki_vorschlag.py` | objekt_seite_ki_vorschlag; KI-Opt-in | PR8 |

Alle Migrationen sind reine Expand-Migrationen (neue Tabellen/Spalten mit server_default; kein Migrate/Contract nötig). down_revision-Kette ab `0123_gsl_alarm`.

---

## 2. Architektur

### 2.1 Neue Dateien

**Modelle**: `app/models/objekt.py` (+ Import in `app/models/__init__.py`, Tabellen in `_TENANT_TABLE_NAMES`).

**Services**:
- `app/services/objekt_service.py` — Flags, Nummernvergabe, CRUD-Helfer, Vollständigkeit, `write_objekt_change`, Status-Workflow, Listen-Query mit Filtern.
- `app/services/objekt_dokument_service.py` — Upload (Muster `store_upload_for_uas_medien` aus `media_service.py`: Magic-Byte-MIME via `filetype`, Quota via `storage_service.reserve_storage`/`release_storage`, UUID-Namen), Seiten-Split (pypdf), Rasterung (gekapselt in `_render_page_png(pdf_path, seiten_nr, dpi)`), Sammel-PDF-Assembly (pypdf-Merge, A4/A3), Löschkaskade inkl. Storage-Freigabe. Ablage: `app_storage/objekt_media/{org_id}/{objekt_id}/{dokument_uuid}/` (original.pdf, `seite_0001.pdf`, `seite_0001.png`, `seite_0001_thumb.jpg`).
- `app/services/objekt_matching_service.py` — BMA-Regex, Adress-Matching (nutzt `normalize_address` aus `app/services/lis/lis_mapping.py`), Geo-Fallback (Haversine gegen objekt + Zusatzadressen), Persistenz `objekt_einsatz`, `broadcast_org`-Events.
- `app/services/objekt_pdf_service.py` — Objektblatt via Jinja `pdf/objektblatt.html` im `pdf_service`-Renderpfad (WeasyPrint + xhtml2pdf-Fallback; NICHT das String-HTML-Muster aus uas_pdf.py), statische Karte, QR, Sammelmappe. QR-Helfer aus `ui_settings.py` (`_generate_qr_datauri`) in kleines `app/services/qr_service.py` extrahieren.
- `app/services/objekt_ki_service.py` (PR8) — Vision-Prompt, Aufruf `ai_service.complete_vision(...)`, Vorschlags-Persistenz.
- **Erweiterung `app/services/ai_service.py`**: `complete_vision(system, user_text, images, org_id, ...)` — gleiche BYOK-/Quota-/Fehlerlogik wie `complete`, Content um Anthropic-Image-Blocks erweitert (Bilder auf ~1024 px begrenzen wegen Tokenkosten). Erste Vision-Nutzung im Projekt.
- **Erweiterung `app/services/task_reminder.py`**: täglicher Check (Datumswechsel im 30s-Loop, `set_tenant_context(db, None)`) für `objekt.revision_datum <= heute` und `revision_erinnert_am` leer → WS-Hinweis + Sent-Marker; Objektliste-Filter „Revision fällig".

**Router**:
- `app/routers/ui_objekt.py` — Pflege-UI (Liste, Detail, HTMX-Abschnitte, Kontakte/Gefahren/Merkmale, Karten-JSON-API, Katalog-Admin), Guard `require_objekt_enabled`.
- `app/routers/ui_objekt_dokumente.py` — Upload, Galerie, Klassifikation, Viewer, Downloads, geschützte Dateiauslieferung (`FileResponse` nach Org-Check, Muster `ui_media.py`-UAS-Variante), KI-Review (PR8).
- `app/routers/ui_infoscreen_alarm.py` — öffentliche Token-Route `/infoscreen/alarm/{token}` (Muster `ui_weather.py::weather_infoscreen`), Token-Verwaltung in Einstellungen, WS `/ws/infoscreen/{token}` (abonniert nach Token-Prüfung den Org-Kanal, `ORG_WS_OFFSET` aus `broadcast.py`). WebSocket statt Polling — Infrastruktur existiert, Reconnect-Logik aus `app.js` übernehmbar.
- Registrierung in `app/main.py`, Nav-Eintrag „Objekte" in `base.html` nur bei `request.state.objekt_enabled`.

**Templates** (`app/templates/objekt/`): `liste.html`, `detail.html` + Partials (`_stammdaten.html`, `_gefahren.html`, `_bma.html`, `_merkmale.html`, `_kontakte.html`, `_wohnanlage.html`, `_zusatzadressen.html`, `_protokoll.html`), `dokumente.html`, `_dokument_galerie.html`, `_klassifikation_modal.html`, `viewer.html`, `karte.html`, `_karte_readonly.html`, `einsatz.html`, `infoscreen_alarm.html`, `admin_kataloge.html`; Board-Partial `app/templates/incident/_objekt_panel.html`; `app/templates/pdf/objektblatt.html`.

**Statisch**: `app/static/js/objekt_karte.js` (Leaflet + Geoman, vorhandene Assets; Palette mit `objektSymbolHtml`), `app/static/js/objekt_viewer.js` (Fullscreen-Viewer: Swipe/Pfeiltasten/Zoom, Alpine + Pointer-Events, kein neues Framework).

### 2.2 Rollen & Rechte

- **Neue Rolle `objekt_verwalter` (Gewicht 60)** in `app/core/permissions.py` + Seed (Muster `fahrtenbuch_admin`): Objekte anlegen/bearbeiten/freigeben, Dokumente, Lagekarte. Begründung: typische Beauftragten-Aufgabe unterhalb org_admin; Gewicht unter incident_leader (70).
- **org_admin**: zusätzlich Katalog-Verwaltung, Modul-Flag, Infoscreen-Tokens, KI-Opt-in.
- **Alle angemeldeten Org-Nutzer**: Lesen freigegebener Objekte, Einsatzansicht, Viewer. Entwürfe nur objekt_verwalter+.
- **Match bestätigen/lösen im Einsatz**: incident_leader ODER objekt_verwalter.
- Infoscreen: unauthentifiziert via Token (wie Wetter-Infoscreen); Inhalt reduziert — keine Wohnanlagen-Hinweise am Wandmonitor.

### 2.3 PDF-Pipeline — Technikentscheidung

- **Split**: pypdf (vorhanden) → verlustfreie Einzelseiten-PDFs.
- **Rasterung**: **pdf2image + Poppler** (entschieden 2026-07-05). Produktion läuft auf Debian → `apt install poppler-utils`, keine AGPL-Frage wie bei PyMuPDF, stabil und bewährt. Neue Dependency `pdf2image` in pyproject; Deployment-Doku um poppler-utils ergänzen. Rasterung gekapselt in `_render_page_png(pdf_path, seiten_nr, dpi)` → Backend-Tausch bliebe lokal. Am Windows-Dev-Rechner läuft die Testsuite mit injizierter/gemockter Renderfunktion (Poppler dort optional).
- **Große PDFs**: Upload-Antwort sofort (`status=verarbeitung`), Split+Rasterung als Background-Task (Muster `_geocode_incident`), HTMX-Polling auf Dokumentstatus. Limit 300 Seiten / 100 MB je Datei (SystemSettings-konfigurierbar). Quota zählt Original + Renderings.

### 2.4 Alarm-Matching — Ablauf

Hooks: `create_incident_api` (`app/routers/api_v1.py`, nach `db.commit()`, neben `_geocode_incident`), `app/services/lis/lis_sync.py` nach `_get_or_link_incident`, sowie manueller UI-Create-Pfad.

1. **BMA-Nummer**: Regex auf `incident.report_text` (kein eigenes BMA-Feld am Incident): `(?:bmz|bma|rfl)[\s:.\-/]*(\d{2,6})` case-insensitive; Treffer gegen `objekt_bma.bma_nummer`/`rfl_nummer` der Org → `quelle=bma, status=bestaetigt`.
2. **Adresse**: `normalize_address` auf incident.address_* gegen objekt- + Zusatzadressen; Gleichheit Straße+Hausnr+Ort → `bestaetigt` (mehrere Treffer → alle als `vorschlag`).
3. **Geo-Fallback**: nur wenn 1+2 leer und lat/lng vorhanden; Haversine < Radius → nächstes Objekt als `vorschlag`. Da Geocoding asynchron läuft: Stufe 3 wird am Ende des `_geocode_incident`-Tasks erneut angestoßen.

Nach Persistenz: `broadcast_org(org_id, {"type": "objekt_match", ...})` → Board-Panel (HTMX-Trigger) + Infoscreen-Alarmansicht.

### 2.5 Wiederverwendung vs. Neu

| Baustein | Wiederverwendet | Neu |
|---|---|---|
| Storage/Quota/MIME | `media_service`-Muster, `storage_service` | Baum `objekt_media/`, Rasterfunktion |
| Karte | Leaflet/Geoman-Assets, GeoJSON-in-Text, `karte_druck.html`-Druckmuster | Symbolset, Tabelle, Editor-Template |
| PDF-Druck | `pdf_service` (WeasyPrint+Fallback), `staticmap_service` (Erweiterung: mehrere Marker) | `pdf/objektblatt.html`, Sammel-PDF-Merge |
| KI | `ai_service` BYOK/Quota/Fehler | `complete_vision`, Review-Queue |
| Infoscreen | Token-Muster + Wetter-Partials aus `ui_weather.py` | eigene Route/Template/WS |
| Erinnerung | `task_reminder`-Loop, Sent-Marker | Revision-Check |
| Protokoll | IncidentChange-Muster, `history.html`-Timeline | `objekt_change` |
| Seeds | `SeedTemplate`/seed_service | Typen objekt_kategorie, gefahren_katalog, merkmal_katalog |

---

## 3. PR-Phasenplan

Anpassung gegenüber ursprünglichem Vorschlag: **BMA-Block in PR1** (Stammdatum + Voraussetzung fürs Matching-Design); Flag/Guard/Rolle/Nav als „PR0-Anteil" mit in PR1 (klein genug). PR6 und PR7 parallelisierbar.

| PR | Inhalt | Tests (`tests/test_objekt_pr{n}.py`) | Aufwand |
|---|---|---|---|
| **PR1 – Grundmodul** | Migration 0124, Modelle, Tenant-Registrierung, Flags/Guard, Rolle `objekt_verwalter`, Objektliste (Suche, Status-/Kategoriefilter), Detail mit HTMX-Abschnitten Stammdaten/BMA/Zusatzadressen, Nummernvergabe, Status-Workflow, Geocoding-Task, `objekt_change` + Protokoll-Tab, Vollständigkeit v1, Kategorien-CRUD + Seed | Flag system/org/effektiv, Guard 404, CRUD, Nummer unique je Org, Statusübergänge, Change-Log, Tenant-Isolation (Zwei-Org), Rollen-Gates | 5 T |
| **PR2 – Kataloge & Kontakte** | Migration 0125, Gefahren-/Merkmal-Katalog + CRUD + Seeds, Gefahren-Zuordnung (UN-Nr, Detail), Merkmale mit Hinweis, Kontakte (Mehrfach-Telefone), Wohnanlage + DSGVO-Hinweis, Badge-Spalten (BMA/Schlüssel/BSP-Ampeln), Filter Merkmale + „Revision fällig", Revisions-Erinnerung | Katalog-CRUD+Seeds, Unique-Zuordnung, telefone_json-Roundtrip, Badge-Logik, Erinnerung (kein Doppelversand), Isolation | 4 T |
| **PR3 – Dokumente** | Migration 0126, Upload (Drag&Drop, mehrere, Quota, MIME), Split+Rasterung (Background, Statusanzeige), Galerie Multi-Select + Bulk-Klassifikation, Dokumentart-Filter mit Zählern, Suche Titel/Melderlinie, Fullscreen-Viewer, Einzeldownload + Sammel-PDF (A4/A3), geschützte Auslieferung, Löschung | Upload happy/ungültig/Quota, Split-Seitenzahl, Bulk, Filter/Suche, fremde Org 404, Sammel-PDF-Reihenfolge, Löschkaskade | 7 T |
| **PR4 – Objekt-Lagekarte** | Migration 0127, voller Editor (Palette Drag&Drop, Label, Löschen, Linien/Flächen via Geoman), Symbolset, Readonly-Einbettung, JSON-API | CRUD, GeoJSON-Roundtrip, Isolation, Rollen-Gate; UI manuell | 5 T |
| **PR5 – Matching & Einsatzansicht** | Migration 0128, Matching-Service (3 Stufen), Hooks api_v1/LIS/manuell, Board-Sidebar-Panel mit Vorschlag-Bestätigung, manuell verknüpfen/lösen, Einsatzhistorie-Tab, mobile Einsatzansicht (Melderpläne über Dokumentart-Kacheln, keine automatische Melderlinien-Erkennung aus dem Alarmtext — entschieden) | Regex-Varianten, Adress-Matching inkl. Zusatzadressen, Geo-Radius, vorschlag/bestätigt, Unique, Hook nach Geocoding, Isolation | 5 T |
| **PR6 – Alarm-Infoscreen** | Migration 0129, Token-Verwaltung, Route + WS, Alarm-Layout (Stichwort/Adresse groß, Piktogramme, Karte, FSD/BMZ/FBF), Idle-Modi (Uhr/Wetter-Partials/letzte Einsätze), Rückfall nach Dauer | Token gültig/ungültig/inaktiv, Alarm-Payload, Idle-Konfig, keine sensiblen Daten im Payload | 4 T |
| **PR7 – Druck** | Objektblatt A4 (Kopf, Piktogramme, BMA/FSD, Kontakte, statische Karte mit Symbolen, QR), Anhang „bei Einsatz drucken" als Sammel-PDF, Batch-Druck aus Liste, Druck aus Einsatzansicht | PDF beide Renderer-Pfade, QR-Inhalt, Anhang-Auswahl, Batch | 4 T |
| **PR8 – KI-Klassifikation** | Migration 0130, `complete_vision`, Prompt (Dokumentart+Titel+Melderlinien+Stand als JSON), Review-Queue (übernehmen/korrigieren/verwerfen, einzeln+bulk), Org-Opt-in, nie Auto-Apply | Vision Quota/BYOK/Fehler (gemockt), Vorschlags-Statusmaschine, Opt-in-Gate, Übernahme schreibt Seite+change | 4 T |
| **PR9 – Offline-Precaching Android-App** | Server: Sync-API `GET /api/objekte/sync` (Manifest aller freigegebenen Objekte mit `aktualisiert_am` + Datei-Hashes für Einsatzansicht-JSON, Thumbs, Hi-Res-Seiten und Einzel-PDFs; Auth via bestehendem Session-/Token-Mechanismus der App) + Delta-Downloads. Android (Repo BattloXX/Einsatzcockpit-Android, Capacitor): periodischer Background-Sync (WorkManager, nur WLAN konfigurierbar), lokaler Cache der Objektinfos inkl. PDFs, Auslieferung aus Cache wenn offline, Aktualisierung bei Manifest-Änderung | Server: Manifest-Inhalt/Delta (nur freigegebene Objekte, Org-Scope, 404 fremde Org), Hash-Stabilität; Android: manueller Test Sync + Flugmodus | 5 T |

**Summe: ~43 Personentage** (+ ~15 % Puffer für Integrationsreibung: WeasyPrint/xhtml2pdf, Poppler, WS, Android-Sync).

---

## 4. UI-Beschreibung je Ansicht

### 4.1 Pflege (Desktop)

- **Objektliste** (`/objekte`): Suchfeld (Name/Vulgo/Adresse/Nummer), Filterleiste (Kategorie, Status, Merkmale-Multiselect, „Revision fällig"), Tabelle: Nummer, Name/Vulgo, Kategorie, Adresse, Badge-Chips (BMA gelb, Schlüssel grün, BSP rot — EUS-Ampellogik als moderne Chips), Vollständigkeits-Balken (Tooltip mit fehlenden Punkten), Status-Chip, Revisionsdatum (rot bei überfällig). „Neues Objekt", Multi-Select für Batch-Druck. HTMX-Filter ohne Full-Reload.
- **Objekt-Detail** (`/objekte/{id}`): eine Seite, links Sticky-Abschnittsnav (Stammdaten, Gefahren, BMA & Schlüssel, Merkmale, Kontakte, Wohnanlage, Lagekarte, Dokumente, Einsätze, Protokoll), rechts Cards mit Stift-Icon → HTMX-Inline-Formular, Speichern je Abschnitt. Kopf: Nummer, Name, Status-Chip mit Workflow-Buttons (rollenabhängig), Vollständigkeit, Buttons „Objektblatt (PDF)" und „Einsatzansicht öffnen".
- **Dokumente**: Dropzone (mehrere PDFs), Filter-Chips je Dokumentart mit Zählern (wie EUS-Dropdown „Alle (150)/…"), Thumbnail-Galerie, unklassifizierte Seiten gelb umrandet, Multi-Select → Bulk-Modal (Dokumentart, Titel, Melderlinien, Stand, „bei Einsatz drucken"). Klick → Viewer.
- **Protokoll-Tab**: Timeline nach `incident/history.html`-Vorbild (wer, wann, Bereich, Feld, alt → neu).
- **Katalog-Admin** (`/objekte/kataloge`, org_admin): Tabs Kategorien/Gefahren/Merkmale, CRUD mit Sortierung und Aktiv-Toggle.

### 4.2 Einsatzansicht (Mobile/Tablet first, `/objekte/{id}/einsatz`)

Read-only, eine Spalte, Touch-Ziele ≥44 px, Priorität von oben: (1) Gefahren-Chips mit Piktogramm+UN-Nr, (2) gelber BMA/FSD-Block (BMZ, FBF, FSD-Standort+Inhalt, Laufkarten-Ablageort), (3) Melderpläne/Laufkarten als eigene prominente Kachel (Filter Dokumentart `bma_melderplan`, Suche nach Melderlinie im Viewer; keine automatische Linien-Erkennung aus dem Alarmtext — entschieden), (4) Kontakte als Karten mit vollflächigen `tel:`-Buttons, (5) Lagekarte readonly (Pinch-Zoom), (6) Dokumente als Dokumentart-Kacheln → 1 Tap Fullscreen-Viewer (Swipe, Doppeltipp-Zoom), (7) Anfahrt/Infos, (8) Einsatzhistorie (letzte 10). Einstieg: Objekt-Panel als Sidebar-Box am Board (`incident/board.html`) mit Objektname, Gefahren-Mini-Chips, Vorschlag-Bestätigung. Offline: Browser-PWA bekommt das SW-Muster (`/^\/objekte\/\d+\/einsatz$/` network-first mit Cache-Fallback, Thumbs stale-while-revalidate); **vollständiges Offline-Precaching inkl. PDFs übernimmt die Android-App via Sync-API (PR9)**.

### 4.3 Alarmansicht Infoscreen (`/infoscreen/alarm/{token}`)

Fullscreen, dunkel, hoher Kontrast, keine Interaktion. Alarm (via WS `incident_created`/`objekt_match`): oben Stichwort + Adresse sehr groß, links Objektname + Gefahren-Piktogramme, Mitte Karte (non-interactive) mit Objektsymbolen, rechts FSD/BMZ/FBF-Standorte. Ohne Match: Stichwort/Adresse/Karte. Nach Ablaufdauer zurück in Idle (Uhr / Wetter-Partials serverseitig eingebunden — keine Duplizierung, kein iframe / letzte Einsätze). Der bestehende `/wetter/infoscreen/{token}` bleibt unverändert.

### 4.4 Druck

Objektblatt A4 hoch, 1–2 Seiten: Kopf (Org, Nummer, Name, Adresse, Stand), Gefahren-Piktogrammleiste, gelber BMA/FSD-Kasten, Kontakttabelle, Merkmale-Zeile, statische Karte mit Symbolen, QR zur Einsatzansicht, Fußzeile Erstellt/Revision. Optional Anhang „bei Einsatz drucken"-Seiten (A4/A3). Aufrufbar aus Detail, Liste (Multi-Select → Mappe) und Einsatzansicht.

---

## 5. Testplan

- Struktur: flach in `tests/`, `test_objekt_pr1.py` … `test_objekt_pr9.py` (UAS-Schema), sqlite-Conftest, `pytest tests/ -v`.
- **Tenant-Isolation**: Zwei-Org-Fixture; je PR Tests, dass Org B keine Objekte/Dokumente/Seiten/Kartenobjekte/Matches von Org A sieht und Datei-Auslieferung fremder Org 404 liefert. Neue Tabellen müssen den `_TENANT_TABLE_NAMES`-Coverage bestehen.
- **Flag/Guard**: System aus → 404 trotz Org-Flag; Org aus → 404; Nav verschwindet.
- **Rollen**: Lesen ok / Schreiben 403 für normale Nutzer; objekt_verwalter schreibt; org_admin Kataloge.
- **Service-Units**: Nummernvergabe (zwei Orgs parallel), Vollständigkeits-Punktmatrix, parametrisierte Regex-Tabelle Matching (inkl. Negativfälle), Adress-Normalisierung, Haversine-Grenzfälle, Sammel-PDF-Reihenfolge, Quota-Reserve/Release bei Fehler mitten im Split.
- **Pipeline**: 3-Seiten-Fixture-PDF; Split erzeugt 3 Zeilen+Dateien; Rasterfunktion injizierbar/gemockt (CI ohne Poppler lauffähig).
- **KI**: gemockter Anthropic-Client (Quota, BYOK, ungültiges JSON → verworfen).
- **Erinnerung**: due gestern → Erinnerung+Marker; zweiter Lauf ohne Doppelversand.
- **Druck**: beide Renderer-Pfade (WeasyPrint auf Windows gemockt), QR-Data-URI vorhanden.

---

## 6. Offene Fragen — vom Betreiber entschieden (2026-07-05)

1. **PDF-Rasterung**: **pdf2image + Poppler** (Produktion läuft auf Debian → `apt install poppler-utils`; keine AGPL-Frage). Kapselung in `_render_page_png` bleibt.
2. **Rollenmodell**: **Ja** — `objekt_verwalter` (60) + org_admin; Match-Bestätigung im Einsatz zusätzlich ab incident_leader.
3. **Dokumentart**: **fix als Konstantenliste**, beobachten.
4. **Große PDFs**: **Limit 300 Seiten / 100 MB**, Background-Verarbeitung mit Status, per SystemSettings anhebbar.
5. **Storage-Quota**: **Ja** — Renderings zählen zur Org-Quota.
6. **Offline**: **Precaching in der Android-App** (Repo BattloXX/Einsatzcockpit-Android) für Objektinformationen **inkl. PDFs**, mit regelmäßiger Aktualisierung → eigener **PR 9** (Sync-API serverseitig + Background-Sync in der App). Browser-PWA bekommt zusätzlich das leichte cache-first-Muster für zuletzt besuchte Einsatzansichten.
7. **Wetter-Infoscreen**: **OK** — Alarm-Infoscreen eigenständig, Idle-Modus „wetter" bindet bestehende Partials serverseitig ein; alter Wetter-Infoscreen bleibt.
8. **Melderlinien-Erkennung aus dem Alarmtext**: **entfällt** (weggelassen). Das Feld `melderlinien` an der Dokumentseite bleibt für manuelle Klassifizierung, Suche und Viewer-Filter erhalten.
9. **Geo-Radius**: **75 m** Default, org-konfigurierbar.
10. **DSGVO Wohnanlagen-Hinweise**: **OK** — Infoscreen nie (fest); Objektblatt per Checkbox „Hinweise andrucken" (Default aus).
11. **Nummernkreis**: **fortlaufend je Org**, Anzeige „OBJ-0042".

---

## 7. Aufwandsschätzung

| PR | Titel | Tage |
|---|---|---|
| 1 | Grundmodul | 5 |
| 2 | Kataloge, Kontakte, Wohnanlage, Erinnerung | 4 |
| 3 | Dokumentenpipeline | 7 |
| 4 | Objekt-Lagekarte | 5 |
| 5 | Matching + Einsatzansicht | 5 |
| 6 | Alarm-Infoscreen | 4 |
| 7 | Druck | 4 |
| 8 | KI-Klassifikation | 4 |
| 9 | Offline-Precaching Android-App (Sync-API + App-Sync) | 5 |
| | **Gesamt** | **43** (+15 % Puffer) |
