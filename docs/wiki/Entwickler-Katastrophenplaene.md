# Katastrophenpläne (Entwicklung)

Planungsgrundlage: `docs/plans/katastrophenplan-plan.md` (Pakete K1–K13, Umsetzungsstand in §12),
Pilot-Auswertung: `docs/plans/katastrophenplan-pilot.md`.

## Datenmodell und Migrationen

| Migration | Inhalt |
|---|---|
| `0294_katplan_basis` | `katplan_plan`, `katplan_abschnitt` (Kapitelbaum), `katplan_phase`, `katplan_element`, `katplan_element_phase`, `katplan_aenderung` (Protokoll), `katplan_pruefung`; Org-/Systemschalter, Rollen |
| `0295_katplan_versionen` | `katplan_version` (`plan_json`), `katplan_version_element` (Snapshot je Element) |
| `0296_katplan_verknuepfungen` | `katplan_link` (Kontakt, Kontakt-Organisation, Objekt, Fahrzeug, Wasserstelle), `katplan_relation` (Element ↔ Element); Snapshotspalten |
| `0297_katplan_anhaenge` | `katplan_anhang` (Foto, Datei, Link, Objektdokument; soft delete) |
| `0298_katplan_quellen` | `katplan_dokument` (versionierte PDF-Quellen, Gruppe/Versionsnummer), `katplan_dokument_seite` |
| `0299_katplan_ki_import` | `OrgSettings.katplan_ki_import_enabled`, KI-Status je Quelle, `katplan_import_vorschlag` |

Alle Tabellen sind `TenantScoped` und in `_TENANT_TABLE_NAMES` eingetragen.

**Arbeitsstand und Freigabe:**

- `KatplanPlan`, `KatplanAbschnitt`, `KatplanPhase` und `KatplanElement` bilden den
  veränderlichen Arbeitsstand.
- `freigabe_service.freigeben` schreibt einen unveränderlichen Snapshot:
  - `KatplanVersion.plan_json`: Kopf, Kapitel, Phasen, Plan-Links, Plan-Anhänge, Quellen
  - je Element eine `KatplanVersionElement`-Zeile mit `links_json`, `relationen_json`,
    `anhaenge_json`, `phasen_json`, `gsl_json`
- Ein `before_update`-Listener verhindert Änderungen an Versionstabellen.
- Elemente behalten über alle Versionen ihre `element_uuid`. `element_version_id` ist die
  ID der Versionszeile.
- Optimistisches Sperren über `version` an Plan und Element (`KatplanKonflikt`).
- Inhaltsänderungen an einem freigegebenen Plan setzen `in_ueberarbeitung`
  (`markiere_ueberarbeitung`).

Status: `entwurf` → `in_pruefung` → `freigegeben` → `in_ueberarbeitung` → … sowie `archiviert`.

**Herkunft** eines Elements: `manual`, `imported` (aus Quellseite) oder `ai_suggested`
(KI-Import oder MCP). **Ort**: `geometry_json` plus `lat`/`lng` als Bezugspunkt;
`geo_status` ist `keine`, `ungeprueft` oder `geprueft`.

## Services

| Service | Verantwortung |
|---------|---------------|
| `katplan_flags`, `katplan_zugriff` | Modulschalter; Rollen (`darf_lesen/bearbeiten/freigeben`, `sieht_vertraulich`) und FastAPI-Dependencies |
| `katplan_typen` | Typregister `ELEMENT_TYPEN` (Felder, Geometrieart), Vorlagen, `bereinige_daten`, `FELD_LABELS` |
| `katplan_service` | Kennungen, Suchtext, Kapitelbaum, Sortierung, `lege_element_an`, `lege_abschnitt_an`, `lege_phase_an`, gemeinsame Feldvalidierung `validiere_element_felder` (UI und MCP), Protokoll |
| `katplan_freigabe_service` | Snapshot, Workflow, Versionsvergleich, Wiedervorlage |
| `katplan_link_service`, `katplan_ziel_service` | Live-Ziele mit Tenant- und Modulprüfung, Telefonregel (`link_ansicht`), Snapshotvergleich „seit Freigabe geändert“ |
| `katplan_geo_service` | Geometrieprüfung je Typ (über `road_closure_geo_service.validate_geometry`), Bezugspunkt |
| `katplan_anhang_service` | Upload, Quota, Snapshots, geschützte Auslieferung |
| `katplan_quelle_service` + `pdf_seiten_pipeline` | Quellen hochladen und versionieren, Seiten zerlegen (Poppler/Tesseract, gemeinsam mit Objektdokumenten), Seitenvergleich `vergleiche_quellen` |
| `katplan_suche_service` | Kombinierte Suche (Freigabe/Arbeitsstand, LIKE mit Escape, Umkreis), auch für MCP |
| `katplan_ki_service` | KI-Seitenanalyse, Parser (`_parse_antwort` inkl. `daten`), Kontakt-/Objektabgleich, Review-Übernahme |
| `katplan_mcp_service` | Rechtegeprüfte Aufbereitung einer Fassung (`fassung_laden`, `element_dict`) |
| `katplan_qs_service` | Qualitätshinweise im Arbeitsstand (`freigabe_links` einmal je Plan laden) |
| `katplan_pdf_service` | PDF-Offlinefassung über `fassung_laden`; `typdaten_fuer_druck`, WeasyPrint mit xhtml2pdf-Fallback |

## Oberfläche

`app/routers/ui_katplan.py` (Präfix `/katastrophenplaene`). Alle DB-Routen sind `def`.

- `/suche` steht vor allen `/{plan_id}`-Routen.
- Lesen: Liste, `/{id}`, `/{id}/karte` (+ `.geojson`), `/{id}/versionen[/{vid}]`, `/{id}/vergleich`, `/{id}/quellen[/{dok}]`, `/{id}/pdf`.
- Editor: `/{id}/bearbeiten?element=<eid>`, dazu Kapitel, Phasen, Elemente, Sortierung,
  Verschieben, Duplizieren, Links, Beziehungen, Anhänge und Quellen.
  - Antworten kommen als HTMX-Fragment über `_editor_antwort.html`.
  - Die Baumansicht wird out-of-band mitgeschickt.
- Workflow: `einreichen`, `zurueckweisen`, `freigeben`, `archivieren`, `reaktivieren`.
- Import: `/{id}/quellen/{dok}/ki-analyse`, `/{id}/import` mit Einzel- und Sammelübernahme.
- Geschützte Dateien: `/anhang/{uuid}[/thumb]`, `/quelle/{uuid}/original`, `/quelle/{uuid}/seite/{nr}/{bild|thumb|pdf|text}`.
  - Leser erhalten nur Dateien der aktuellen Freigabe.
  - Ältere Freigaben sind über `?version_id=` dieses Plans erreichbar.

Templates liegen in `app/templates/katplan/` im Fahrtenbuch-Design (`kp-*`-Klassen in
`_design.html`).

- Eigenständig gerenderte Fragmente binden `_design.html` ein oder werden **statisch**
  eingebunden. `test_css_klassen_definiert.py` folgt keinen dynamischen Includes.
- Checkboxen nie mit `form-control` oder `width: auto` gestalten: Tailwind-Forms setzt
  `appearance: none`. Stattdessen `label.kp-check` verwenden.
- JavaScript: `katplan_editor.js` (Sortable, Quill, Lightbox), `katplan_karte.js`
  (Geoman-Editor, Adresssuche mit Org-Ort, Plankarte), `katplan_suche.js` (Adresse und
  Standort für die Umkreissuche).

Die PDF-Vorlage liegt in `app/templates/pdf/katplan.html`:

- WeasyPrint mit `target-counter` und `leader()` für das Inhaltsverzeichnis.
- `hyphens: auto` mit `lang="de"` (pyphen), `overflow-wrap: break-word`.
- Typdaten nie als JSON ausgeben, sondern über `typdaten_fuer_druck`.

## MCP

- Lesend in `app/mcp/tools/katplan.py`, schreibend in `app/mcp/tools/katplan_schreiben.py`.
  Für jedes Werkzeug gibt es einen `@server.tool`-Wrapper in `app/mcp/server.py`.
- `adresse` ist dort als `KatplanAdresse` (TypedDict) typisiert, damit Clients die Felder im
  Schema sehen. `_adresse` weist unbekannte Schlüssel ab.
- `_verorten` geokodiert über `geocoding.search_addresses` und ergänzt fehlende Orte aus
  `FireDept.city`. Das Ergebnis ist immer `ungeprueft`.
- Schreibwerkzeuge laufen über den Dekorator `_tool`: Rechte, Modul, Audit `mcp.<name>`,
  Commit/Rollback, Versionskonflikt.
- Download von Quellen: `mcp_download_service` mit `art="katplan_quelle"` und erneuter
  Live-Rechteprüfung beim Abruf.

## Fallstricke

- **Namensvergleiche** (Kapitel, Phasen, Objekte) in Python mit `casefold()` durchführen.
  SQL-`LOWER()` faltet unter SQLite keine Umlaute.
- **Keine Bulk-Updates oder -Deletes** auf Katplan-Tabellen (Tenant-Listener). Zeilen gefiltert
  laden und einzeln ändern.
- **`quelle_dokument_id`** hat bewusst keinen FK, weil Versionen alte Dokument-IDs
  referenzieren; die Prüfung erfolgt im Service.
- **KI-Prüfstatus** wird ohne eigene Spalte aus `katplan_aenderung` abgeleitet: Ein Eintrag
  `geaendert` mit `quelle="ui"` nach der Anlage gilt als Prüfung.

## Tests

| Datei | Schwerpunkt |
|---|---|
| `test_katplan_k1a/k1b` | Modell, Rechte, Modulschalter, Kachel, Liste |
| `test_katplan_k2a/k2b` | Editor, Typen, Vorlagen, Sortierung, Konflikte |
| `test_katplan_k3a/k3b` | Snapshots, Workflow, Versionen, Vergleich, Wiedervorlage |
| `test_katplan_k4a/k4b` | Verknüpfungen, Telefonregel, Beziehungen |
| `test_katplan_k5a/k5b` | Geometrie, Plankarte, Adresssuche |
| `test_katplan_k6a/k6b` | Anhänge, Quota, Auslieferung |
| `test_pdf_seiten_pipeline`, `test_katplan_k7b/k7c` | Seiten-Pipeline, Quellen, Seitenansicht |
| `test_katplan_k8` | Suche |
| `test_katplan_k9` | KI-Import und Review (KI gemockt) |
| `test_mcp_katplan`, `test_mcp_katplan_schreiben` | MCP lesend/schreibend, Adresse und Verortung (Geokodierung gemockt), Schema |
| `test_katplan_k12` | Qualitätshinweise, PDF (HTML über `render_katplan_html`, Typdaten), Quellenvergleich |

Lokal gezielt starten mit `.venv/bin/python -m pytest -q tests/test_katplan_*.py tests/test_mcp_katplan*.py`.
Vor einem Merge laufen alle vier CI-Checks. Die Async-DB-Baseline darf nicht wachsen.

Siehe auch: [Anwendung](Anwender-Katastrophenplaene), [Administration](Administration-Katastrophenplaene).
