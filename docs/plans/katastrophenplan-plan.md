# Katastrophenplan-Modul – Umsetzungsplan

Stand: 2026-10-19 · Grundlage: Konzept „ECP Katastrophenplan-Modul“ v1.1 (19.10.2026)
Basis: `origin/main` @ `0f3c64ef` (letzte Migration `0293`)

Ziel von Phase 1: eigenständiges Planmodul mit nativer Erfassung, Verknüpfungen zu zentralen
Stammdaten, Karte, Anhängen, Dokumentimport, Suche und MCP. GSL-Übernahme wird nur
vorbereitet (Felder), nicht gebaut.

---

## 1. Ist-Zustand: was wiederverwendet wird

| Konzept-Baustein | Vorhanden im ECP | Verwendung im Planmodul |
|---|---|---|
| Modul-Schalter | `SystemSettings`-Key + `OrgSettings.*_modul_aktiv`, Muster `app/services/planspiel_flags.py` | `katplan_flags.py`: `katplan_module_enabled` (System) AND `OrgSettings.katplan_modul_aktiv` |
| Rollen | `app/core/permissions.py` (`ROLES`, `require_role`, Rollensets wie `STRASSENSPERREN_LESE_ROLLEN`) | neue Rollen `katplan_verwalter`, `katplan_freigeber`; Lesen über bestehende Einsatzrollen |
| Status/Freigabe | Objekt: Entwurf → Freigegeben ⇄ In Überarbeitung → Archiviert, Arbeitskopie-Muster (`objekt-arbeitskopie-plan.md`) | gleiche Statuswerte und Labels; Freigabe als unveränderlicher Snapshot (s. §3) |
| Optimistisches Sperren | `Kontakt.version` + Prüfung in `kontakt_service.py:204` | `version`-Spalte an Plan und Element, Formular schickt `version` mit |
| Audit | `app/core/audit.py::write_audit`, feldgenaue Protokolle wie `ObjektChange` | `write_audit` + `katplan_aenderung` (Muster `ObjektChange`) |
| Rich Text | Quill 2.0.3 (vendored), `app/core/html_utils.py::sanitize_html` (nh3) | Beschreibungsfelder: Quill im Editor, serverseitig immer `sanitize_html` |
| Sortierung | SortableJS 1.15.7 (vendored, z. B. `admin/vehicles.html`) | Drag-and-drop im Kapitel-/Elementbaum |
| Kontakte | `Kontakt`, `KontaktOrganisation`, Suche/Duplikatprüfung, `kontakt_access_service.contact_summary(full=…)` | nur `kontakt_id`/`kontakt_organisation_id` referenzieren; Anzeige über `contact_summary` mit den bestehenden Sichtbarkeitsregeln |
| Objekte | `Objekt` (+ `lat/lng`), `ObjektDokument`, `objekt_effective_enabled` | `objekt_id` referenzieren; Objektdokumente als Anhang **verlinken**, nicht kopieren |
| Material/Ressourcen | `VehicleMaster` (Fahrzeuge), `Wasserstelle` | typisierte Links `fahrzeug`, `wasserstelle`; Material **nur als Freitext** (Entscheidung 2026-10-19), kein Verleih-Bezug, kein eigenes Inventar |
| Karte | Leaflet 1.9.4 + Geoman 2.20.2; Editor-Muster `road_closure_editor.js`, `objekt_karte.js`; Geometrie-Prüfung `road_closure_geo_service.validate_geometry` (shapely) | GeoJSON je Element; Validierung über `validate_geometry` (nach `geo_service` heben, s. K5) |
| Geocoding | `geocoding.search_addresses`, `address_autocomplete.py` | Adressfeld je Element mit Autocomplete |
| Dokument-Pipeline | `objekt_dokument_service.py`: Upload, Quota (`storage_service`), Split pypdf, Rendering Poppler, OCR Tesseract, Volltext je Seite | Kern in einen generischen Baustein ziehen, Planmodul nutzt ihn mit eigenen Tabellen (K7) |
| Fotos | `media_service._process_image` (Thumbs, EXIF), `_detect_mime`, Quota | als öffentliche Helfer exportieren und für Plananhänge nutzen |
| KI-Vorschläge | `ObjektSeiteKiVorschlag`/`ObjektStammdatenVorschlag` (Review-Queue, nie Auto-Apply), `ai_service.complete/complete_vision`, Prompt-Verwaltung `AIPromptVersion` | `katplan_import_vorschlag` nach demselben Muster |
| Suche | kein FULLTEXT, überall `LIKE` (z. B. `ui_objekt_dokumente.py:92`) | `suchtext`-Spalte (normalisiert) + `LIKE`, läuft auf SQLite und MariaDB |
| MCP | `app/mcp/registry.register_tool(required_roles, module_check)`, Live-Kontext `load_live_context`, Dokument-Tools in `mcp/tools/objekt_dokumente.py` | `mcp/tools/katplan.py` nach gleichem Muster |
| Erinnerungen | Objekt-Revision (`revision_datum`, `revision_erinnert_am`), Muster `verleih_erinnerung` | Wiedervorlage je Plan |
| PDF | `objekt_pdf_service.py`, `pdf_service.py` | Druck-/Offlinefassung aus freigegebener Version |
| GSL-Auftragsart | `AUFTRAGSART_WERTE = ("erkundung", "abarbeitung")` | Vorlagefeld `gsl_auftragsart` nimmt diese Werte (+ später Erweiterung) |

**Nicht vorhanden** und bewusst nicht neu gebaut: zentrale Dateiverwaltung über Module hinweg,
Vektordatenbank, Offline-Sync für Pläne.

---

## 2. Grundsatzentscheidungen (Empfehlungen, vor K1 bestätigen)

1. **Name/Präfix:** Modul „Katastrophenpläne“, Route `/katastrophenplaene`, Tabellen `katplan_*`,
   Services `katplan_*_service.py`, Templates `templates/katplan/`.
2. **Rollen:** `katplan_verwalter` (anlegen, bearbeiten, zur Prüfung einreichen) und
   `katplan_freigeber` (prüfen, freigeben, archivieren). Org-Admins dürfen beides.
   Lesen: `readonly`, `recorder`, `incident_leader`, `objekt_verwalter`, plus beide neuen Rollen
   (`KATPLAN_LESE_ROLLEN`). Leser sehen **nur freigegebene Versionen**.
3. **Kein Vier-Augen-Prinzip** (Entscheidung 2026-10-19): wer `katplan_freigeber` ist, darf
   auch eigene Änderungen freigeben. Die Freigabe wird protokolliert.
4. **Versionierung (vereinfacht gegenüber Konzept §9):** Es gibt genau einen veränderbaren
   Arbeitsstand je Plan (Tabellen `katplan_abschnitt`, `katplan_element` …). „Freigeben“ friert
   ihn in unveränderliche Tabellen ein (`katplan_version`, `katplan_version_element`).
   - `element_uuid` ist stabil über alle Versionen.
   - `element_version_id` = ID der Zeile in `katplan_version_element`.
   - Damit gibt es keine Doppelpflege von Live- und Versionstabellen beim Bearbeiten, und alle
     Konzeptanforderungen (unveränderliche Freigabe, Referenz Plan-/Elementversion, Snapshot
     der verknüpften Stammdaten) sind erfüllt.
5. **Mehrfachnutzung von Elementen über Pläne:** in Phase 1 **nicht**. Ein Element gehört zu
   einem Plan. Stattdessen gibt es Relationen „verweist auf Element aus Plan X“ und die Funktion
   „Duplizieren in anderen Plan“.
6. **Vertrauliche Inhalte:** Flag `vertraulich` an Element und Anhang. Diese sehen nur
   `katplan_*`, `incident_leader` und Admins, in UI, Suche, PDF und MCP gleich.
7. **Kein eigenes REST-JSON-API in Phase 1.** UI-Routen (HTMX, `def`, `get_db`) plus MCP decken
   alle Zugriffe ab. Die REST-Liste aus Konzept §10.1 wird auf UI-Routen abgebildet (§5).

---

## 3. Datenmodell (Migrationen ab 0294, Alembic-IDs nur Nummern)

Alle Tabellen `TenantScoped`, in `_TENANT_TABLE_NAMES` (`app/core/tenant.py`) registrieren.
FKs auf `user.id`/`objekt.id` = BigInteger. FK-Typen vor dem Schreiben gegen die Zieltabelle
prüfen (MariaDB-INT-FK-Falle aus dem Planspiel).

**Arbeitsstand (veränderbar)**

| Tabelle | Wichtige Spalten |
|---|---|
| `katplan_plan` | `uuid`, `titel`, `beschreibung_html`, `gefahrenarten_json`, `geltungsbereich`, `verantwortliche_stelle`, `verantwortlich_kontakt_id?`, `status` (entwurf/in_pruefung/freigegeben/in_ueberarbeitung/archiviert), `aktuelle_version_id?`, `wiedervorlage_am` (date), `wiedervorlage_erinnert_am`, `version` (optimistisch), `archiviert`, Audit-Felder |
| `katplan_abschnitt` | `plan_id`, `parent_id?` (Baum), `titel`, `beschreibung_html`, `sort` |
| `katplan_phase` | `plan_id`, `name`, `beschreibung`, `ausloeser`, `sort`, `farbe` |
| `katplan_element` | `uuid` (stabil), `plan_id`, `abschnitt_id?`, `kennung` (lesbar, z. B. „KS-06“, unique je Plan), `typ`, `titel`, `beschreibung_html`, `daten_json` (typspezifisch, §3.1), `prioritaet`, `status`, `tags_json`, `gueltig_bis?`, `pruefen_am?`, `vertraulich`, Adresse (`strasse`, `hausnummer`, `plz`, `ort`), `lat`/`lng` (Punkt), `geometry_json` (Linie/Fläche), `geo_status` (keine/ungeprueft/geprueft), `herkunft` (manual/imported/ai_suggested), `quelle_dokument_id?`, `quelle_seiten` (z. B. „6“ oder „13-14“), GSL-Vorlagefelder (§3.2), `suchtext` (normalisiert), `sort`, `version`, Audit |
| `katplan_element_phase` | `element_id`, `phase_id` (N:M) |
| `katplan_relation` | `von_element_id`, `zu_element_uuid`, `art` (folgt_auf/abhaengig_von/siehe_auch/eskaliert_zu), `hinweis` |
| `katplan_link` | `element_id?` / `plan_id?`, `ziel_typ` (kontakt/kontakt_organisation/objekt/fahrzeug/wasserstelle), `ziel_id`, `rolle` (zu_verstaendigen/verantwortlich/zustaendig/material/…), `menge?`, `hinweis` |
| `katplan_anhang` | Bindung `plan_id` + optional `abschnitt_id`/`element_id`; `art` (foto/datei/link/objekt_dokument/katplan_dokument); `pfad`, `thumb_pfad`, `mime`, `groesse_bytes`, `url`, `objekt_dokument_id?`, `katplan_dokument_id?`, `seiten?`, `beschriftung`, `urheber`, `aufnahme_datum`, `vertraulich`, `sort` |
| `katplan_dokument` | importierte Quelldatei, Struktur wie `ObjektDokument` (Versionierung über `dokument_gruppe_id`, `status`, `belegt_bytes`, `seitenzahl`) |
| `katplan_dokument_seite` | wie `ObjektDokumentSeite` (Bild, Thumb, `volltext`, `text_quelle`) |
| `katplan_import_vorschlag` | `dokument_id`, `seite_von/bis`, `vorschlag_json` (Typ, Titel, Text, Adresse, Kontakte-Rohtext, Material-Rohtext), `abgleich_json` (gefundene Kontakt-/Objekt-Kandidaten), `status` (offen/uebernommen/verworfen), `element_id?` nach Übernahme, Entscheider |
| `katplan_aenderung` | feldgenaues Protokoll nach Muster `ObjektChange` |
| `katplan_pruefung` | `plan_id`, `version_id?`, `aktion` (eingereicht/zurueckgewiesen/freigegeben/archiviert), `kommentar`, `user_id`, Zeit |

**Freigegebene Versionen (unveränderlich, nur INSERT)**

| Tabelle | Inhalt |
|---|---|
| `katplan_version` | `plan_id`, `versionsnummer`, `freigegeben_am/von`, `geprueft_von`, `aenderungsnotiz`, `plan_json` (Kopf, Abschnitte, Phasen), `inhalt_hash` |
| `katplan_version_element` | `version_id`, `element_uuid`, `kennung`, `typ`, `titel`, `beschreibung_html`, `daten_json`, `prioritaet`, `vertraulich`, `lat/lng`, `geometry_json`, `geo_status`, `phasen_json`, `links_json` (inkl. **Snapshot** der verknüpften Stammdaten zum Freigabezeitpunkt: Name, Telefon, Objektname …), `relationen_json`, `anhaenge_json` (Verweise), `quellen_json`, `gsl_json`, `suchtext` |

Ein Service-Wächter (und Test) stellt sicher, dass `katplan_version*` nie aktualisiert wird.
Löschen nur über Archivierung bzw. Löschung des ganzen Plans durch Admins.

### 3.1 Elementtypen (`daten_json`, Validierung im Service, Typ-Registry in `katplan_typen.py`)

| Code | Label | Zusatzfelder |
|---|---|---|
| `info` | Information / Hinweis | `kategorie`, `warnhinweis` |
| `massnahme` | Maßnahme / Checkliste | `schritte[]` (`text`, `erledigt_hinweis`), `erwartetes_ergebnis` |
| `kontrollstelle` | Kontroll-/Gefahrenstelle | `gefahren[]`, `kontrollhinweise`, `zugaenglichkeit` |
| `gefahrenbereich` | Gefahrenbereich / Kontrollstrecke | `raeumliche_hinweise` (Geometrie Linie/Polygon) |
| `ablauf` | Ablauf / Entscheidungshilfe | `ausloeser`, `bedingungen[]` (`wenn`, `dann`) |
| `ressource` | Ressourcen-/Materialbedarf | `bedarf[]` (`bezeichnung`, `menge`, `einheit` – Freitext), `lagerort` |
| `medien` | Dokument-/Medienverweis | keine (Anhänge tragen den Inhalt) |
| `zustaendigkeit` | Kontakt-/Zuständigkeitsverweis | `ausloeser` (Links tragen Kontakte) |
| `objektverweis` | Objektverweis | `hinweis` (Link trägt das Objekt) |

Neue Typen ergänzt man nur in der Registry (Label, Icon, Feldschema, Formular-Partial).

### 3.2 GSL-Vorbereitung (nur Felder, keine Funktion)

`gsl_vorlage` (bool), `gsl_auftragsart` (aus `AUFTRAGSART_WERTE`), `gsl_prioritaet`,
`gsl_ressourcentypen_json`, `gsl_dauer_min`, `gsl_voraussetzungen`. Im Editor als eingeklappter
Bereich „GSL-Vorlage (vorbereitet)“. Herkunftsreferenz für später:
`element_uuid` + `katplan_version_element.id`.

---

## 4. Rechte und Sichtbarkeit (gilt für UI, Downloads, Suche, PDF und MCP gleich)

Ein zentraler Service `katplan_zugriff.py` (Muster `planspiel_zugriff.py`) bietet:
`darf_lesen`, `darf_bearbeiten`, `darf_freigeben`, `sieht_vertraulich`, `sichtbare_version(plan)`.

- Leser: nur `plan.aktuelle_version_id`, ohne vertrauliche Elemente/Anhänge (sofern keine Rolle).
- Kontaktdetails nur über `contact_summary(full=…)` nach den bestehenden Kontakt-Leserechten
  (`KONTAKT_LESE_ROLLEN`). Ohne Recht: Name und Funktion ohne Telefonnummer.
- Objektlinks: Objektname immer, Details/Dokumente nur bei aktivem Objektmodul und Objekt-Leserecht.
- Datei-Auslieferung immer über Route mit Rechteprüfung, nie statischer Pfad.

---

## 5. Routen und Oberfläche

Router `app/routers/ui_katplan.py` (+ `ui_katplan_dokumente.py` ab K7), alle DB-Routen `def`.

| Route | Zweck |
|---|---|
| `GET /katastrophenplaene` | Liste (Filter: Gefahr, Status, Wiedervorlage fällig), Kachel „Neuer Plan“ |
| `GET/POST /katastrophenplaene/neu` | Plan anlegen (Titel, Gefahrenarten, Beschreibung) |
| `GET /katastrophenplaene/{id}` | Lesesicht freigegebene Version (Kapitelnavigation, Phasenfilter, Karte, Druck) |
| `GET /katastrophenplaene/{id}/bearbeiten` | Editor: links Baum (SortableJS), rechts Detail-Formular (HTMX-Swap) |
| `POST …/abschnitte`, `…/abschnitte/{aid}` | Kapitel anlegen/ändern/sortieren |
| `POST …/phasen` | Phasen pflegen |
| `POST …/elemente`, `GET/POST /katastrophenplaene/elemente/{eid}` | Element anlegen (Schnellerfassung: Typ + Titel) / bearbeiten mit `version` |
| `POST …/elemente/{eid}/duplizieren`, `…/verschieben`, `…/sort` | Duplizieren (auch in anderen Plan), Sortierung |
| `POST …/elemente/{eid}/links`, `DELETE …/links/{lid}` | Verknüpfungen |
| `POST …/elemente/{eid}/anhaenge`, `DELETE …` | Anhänge |
| `POST /katastrophenplaene/{id}/einreichen`, `…/freigeben`, `…/zurueckweisen`, `…/archivieren` | Workflow |
| `GET /katastrophenplaene/{id}/versionen`, `…/versionen/{vid}` | Historie, Vergleich zweier Versionen |
| `GET /katastrophenplaene/{id}/karte` | Kartenansicht (Filter Phase/Typ/Prio/Status) |
| `GET /katastrophenplaene/suche` | kombinierte Suche (§7) |
| `GET /katastrophenplaene/{id}/pdf` | Druck-/Offlinefassung der freigegebenen Version |
| `…/dokumente/…`, `…/import/…` | Dokumentimport und Review (K7/K9) |

UI-Regeln aus `CLAUDE.md`: Mobilansicht ≤760px (Lesesicht vollwertig, Editor nutzbar für kleine
Änderungen), `_csrf` in allen Formularen, HTMX-Swap statt Reload, Menü *Module* in `base.html`
(Desktop + Mobil) und Kachel in `index.html` (`test_module_kacheln.py`), nur gerade Quotes.

**Qualitätsstatus im Editor** (Badges am Element und Summen am Plan): keine Geometrie bei
`kontrollstelle`/`gefahrenbereich`, `geo_status=ungeprueft`, Link-Ziel archiviert/gelöscht,
Kontakt seit Freigabe geändert, `pruefen_am` überschritten, KI-Herkunft noch nicht geprüft.

---

## 6. Dokumente und Anhänge

- **Fotos/Dateien:** über generalisierte Helfer aus `media_service` (Bild verkleinern, Thumb,
  MIME-Erkennung, Quota über `storage_service.reserve_storage`). Ablage unter
  `settings.KATPLAN_MEDIA_DIR/{org_id}/{plan_uuid}/…`.
- **Objektdokumente verlinken:** Anhang-Art `objekt_dokument` mit `objekt_dokument_id` und
  optional Seiten. Ausgeliefert wird über die bestehende Objekt-Dokumentroute samt deren Rechten.
- **Importierte Planquellen:** `katplan_dokument` mit generalisierter Seiten-Pipeline (K7a).
  Originale bleiben unverändert und versioniert; eine neue Version ersetzt die freigegebene
  nicht stillschweigend.
- **Dokumentvergleich (K12):** `difflib` über den Seiten-Volltext alter/neuer Version. Elemente,
  deren `quelle_dokument_id`/`quelle_seiten` betroffene Seiten referenzieren, bekommen einen
  Prüfhinweis. Es wird nichts automatisch überschrieben.

---

## 7. Suche

- Native Inhalte: `suchtext` je Element (Titel, Kennung, Text ohne HTML, `daten_json`-Texte,
  Adresse, Tags) wird bei jedem Speichern neu gebaut, also sofort durchsuchbar. Für Leser wird
  `katplan_version_element.suchtext` durchsucht, für Bearbeiter wahlweise auch der Arbeitsstand.
- Dokumentseiten: `katplan_dokument_seite.volltext` (nach Verarbeitung).
- Verknüpfungen: Treffer über Kontakt-/Objektnamen per Join auf `katplan_link` (live Name),
  ohne Stammdaten zu kopieren.
- Filter: Gefahr, Phase, Typ, Priorität, Status, Plan, Ort (Text), „nur geprüfte Orte“.
- Räumlich: Umkreis um Punkt (bbox-Vorfilter + Distanz in Python, Muster
  `distance_point_to_geometry_m`). Polygon-/Streckensuche erst bei Bedarf.
- Ergebnisliste gemischt (Element/Dokumentseite), jedes Ergebnis verlinkt direkt dorthin.
- Embeddings: **nicht** in Phase 1.

---

## 8. MCP

Datei `app/mcp/tools/katplan.py`, `module_check=katplan_effective_enabled`, Rollen über
`KATPLAN_LESE_ROLLEN` bzw. `katplan_verwalter`. Antworten folgen Konzept §10.2:
`plan_id`, `plan_version_id`, `element_id` (UUID), `element_version_id`, `type`, `title`, `body`
(Text ohne HTML), `geometry`, `linked_entities`, `attachments`, `source_references`,
`review_status`, `warnings`. Fehlende Werte sind `null` mit Hinweis in `warnings`.

Lesend (K10): `katplan_liste`, `katplan_lesen`, `katplan_suchen`, `katplan_elemente`,
`katplan_element_lesen`, `katplan_orte_suchen`, `katplan_dokument_auszug`,
`katplan_verknuepfungen`. Namen auf Deutsch, passend zum bestehenden MCP-Katalog.

Schreibend nur Arbeitsstand (K11): `katplan_element_entwurf_anlegen`,
`katplan_element_entwurf_aendern`, `katplan_anhang_upload_vorbereiten`/`_uebergeben` (Muster
`objekt_dokument_upload_vorbereiten`), `katplan_vorschlaege_aus_dokument`.
Kein Tool kann einreichen, freigeben oder freigegebene Versionen ändern. Herkunft = `ai_suggested`.

---

## 9. Arbeitspakete (je ein PR, Codex-Aufträge klein halten)

| Paket | Inhalt | Konzept-AP |
|---|---|---|
| **K1 Grundgerüst** | Flags (System/Org + Admin-Schalter), Rollen inkl. Rollenkatalog/Seed, Migration 0294 (Plan, Abschnitt, Phase, Element, Element-Phase, Änderung, Prüfung), `katplan_zugriff.py`, Menü/Kachel, Liste + Plan anlegen/bearbeiten, Tests (Rechte, Tenant, Modulschalter, Kachel) | A |
| **K2 Editor** | Zweigeteilte Ansicht, Abschnittsbaum mit Drag-and-drop, Typ-Registry + Formulare je Typ, Quill + `sanitize_html`, Schnellerfassung, Elementvorlagen (im Code definiert: „Gewässer-Kontrollstelle“, „Verständigung“, „Materialbereitstellung“, „Sicherheitshinweis“), Duplizieren, `suchtext`, optimistisches Sperren, GSL-Vorlagefelder (eingeklappt) | A |
| **K3 Versionen & Freigabe** | Migration (Version, Version-Element), Workflow (ohne Vier-Augen), Snapshot inkl. Link-Snapshot, Lesesicht freigegebene Version, Versionshistorie + Vergleich, Wiedervorlage-Erinnerung (Loop nach Objekt-Revision), Unveränderlichkeits-Test | A, E |
| **K4 Verknüpfungen** | `katplan_link` + `katplan_relation`, Suchdialoge (bestehende Kontakt-/Objekt-/Fahrzeug-/Wasserstellen-Suchen), Live-Anzeige mit Rechteprüfung, Marker „seit Freigabe geändert“ und „Ziel fehlt“ | B |
| **K5 Karte** | `validate_geometry` nach `geo_service` heben (Straßensperren weiter grün), Geoman-Editor im Element, Adress-Autocomplete, `geo_status`, Plankarte mit Filtern, Klick öffnet Element | B |
| **K6 Anhänge** | Media-Helfer öffentlich machen, `katplan_anhang` (Foto/Datei/Link/Objektdokument), Galerie mit Beschriftung, Download-Route mit Rechten, Quota | B |
| **K7a Pipeline-Refactor** | Seiten-Pipeline aus `objekt_dokument_service.py` in `dokument_seiten_pipeline.py` ziehen, Objektverhalten unverändert (alle Objekt-Dokumenttests grün, keine Funktionsänderung) | C |
| **K7b Planquellen** | `katplan_dokument(_seite)`, Upload, Verarbeitung, Seitenansicht, Versionen | C |
| **K8 Suche** | Kombinierte Suche + Filter + Umkreis, Suchfeld in Liste und Lesesicht | C |
| **K9 KI-Import** | `katplan_import_vorschlag`, Seitenanalyse über `ai_service` (Prompt in `AIPromptVersion`), Abgleich mit Kontakten (Duplikatprüfung) und Objekten, Review-Ansicht (einzeln/gesammelt übernehmen, bearbeiten, verwerfen), Herkunft + Quellseite am Element. Telefonnummern, Koordinaten und Mengen werden nie als geprüft übernommen | C |
| **K10 MCP lesend** | 8 Tools, strukturierte Antworten, Tests auf Datenabfluss (Fremd-Org, Leser ohne Kontaktrecht, vertraulich, Entwurf für Leser unsichtbar) | D |
| **K11 MCP Entwurf** | 4–5 Schreibtools nur auf Arbeitsstand, Audit `quelle=mcp` | D |
| **K12 QS & Ausgabe** | Qualitätsstatus-Badges, PDF-Export, Dokumentvergleich mit Prüfhinweisen, Wiki (Anwender/Admin/Entwickler), Website-Eintrag/Roadmap, CHANGELOG | E |
| **K13 Pilot** | (a) Blackout- oder Sturmplan komplett nativ ohne Anhang (Akzeptanz A), (b) Import „HOCHWASSER – Maßnahmenplan“ Wolfurt, Kontrollstellen S. 5–17 als `kontrollstelle` mit Quellseite, Abgleich VKW/Bauhof-Kontakte, Material S. 20 als Freitext-Bedarf; Auswertung der Lücken | E |

Reihenfolge: K1 → K2 → K3 bilden den MVP (Akzeptanztest A). K4–K6 können danach parallel
laufen. K7a muss vor K7b liegen. K10 braucht K3 (Versionen) und sinnvollerweise K8.

**Optional nach Phase 1 (nicht eingeplant):** schreibgeschützte Plan-Ebene auf der GSL-Lagekarte;
Pegel-Bezug (`abfluss_service`) als Auslöser einer Hochwasser-Phase; Embeddings;
Offline-Paket für Tablets; GSL-Übernahme als Auftrag (eigene Spezifikation).

---

## 10. Tests je Paket (Pflicht im Codex-Auftrag)

- Tenant: Org A sieht keine Pläne, Elemente, Anhänge, Dokumentseiten oder Suchtreffer von Org B
  (UI, Download, MCP).
- Rechte: Leser sieht nur freigegebene Version; Leser ohne Kontaktrecht sieht keine Telefonnummer;
  `vertraulich` greift in UI, Suche, PDF und MCP.
- Workflow: Freigabe erzeugt Version; Version-Tabellen werden nie geändert; Bearbeiten nach
  Freigabe setzt `in_ueberarbeitung`.
- Versionskonflikt: veraltete `version` → 409 mit Hinweis.
- Sanitizing: `<script>`/`onerror` in Beschreibung wird entfernt.
- Geometrie: ungültiges GeoJSON → 422.
- KI: Vorschläge erzeugen nie Elemente ohne Bestätigung; MCP kann nicht freigeben.
- Bestehende Suiten bleiben grün: Objekt-Dokumente (K7a), Straßensperren-Geo (K5),
  `test_module_kacheln.py`, `test_async_routes_sync_db.py` (Baseline darf nicht wachsen).

Codex-Auftragsregeln (aus Erfahrung): kein `ruff format`, kein `--fix`, nichts umformatieren,
Templates nicht minifizieren, nur gezielte Testdateien laufen lassen, Testliste im Diff abhaken,
`--model gpt-6.1-sol`.

---

## 11. Entscheidungen des Auftraggebers (2026-10-19)

1. Rollen `katplan_verwalter` + `katplan_freigeber` wie in §2 – ja; **kein Vier-Augen-Prinzip**.
2. Leserkreis: alle Einsatzrollen, nur freigegebene Versionen – ja.
3. Material: **nur Freitext**, keine Verknüpfung zum Verleih-Artikelstamm.
4. Pilot-PDF für K13 liefert der Auftraggeber später.
5. Offline in Phase 1: PDF-Export genügt – ja.

---

## 12. Umsetzungsstand

| Paket | PR | Stand |
|---|---|---|
| K1 Grundgerüst | (dieser PR) | Modulschalter, Rollen, Migration 0294, Plan-Stammdaten/Liste/Protokoll; Editor folgt in K2 |
| K2 Editor | (dieser PR) | Kapitel/Phasen/Elemente aller 9 Typen, Drag-and-drop + ↑/↓, Quill (h3/h4), Vorlagen, Duplizieren, GSL-Felder; Browser-Check ok |
| K3 Versionen & Freigabe | (dieser PR) | Migration 0295, Snapshot-Versionen (unveränderlich), Workflow ohne Vier-Augen, Lesesicht, Historie, Vergleich, Wiedervorlage-Mail, Übersicht-Badge |
| K4–K13 | – | offen |
