# Plan: Georeferenzierte BSP-Ausschnitte als Overlay in der Einsatzkarte

Stand 2026-10-30. Grundlage: Konzept "ECP – Georeferenzierte Brandschutzplan-Ausschnitte in der
Einsatzkarte" (30.10.2026), abgeglichen mit dem echten Code. Dieser Plan ersetzt die im Konzept
nur als Vorschlag genannten Namen und Phasen durch die tatsächlichen Fundstellen.

## 1. Motivation

Einsatzkräfte sollen im Einsatz den Plan eines Objekts (Lageplan, Hallenbereich, Geschoss) **lagegenau
auf der Karte** sehen, statt Plan und Karte im Kopf zu überlagern. Die Georeferenz ist bereits
gepflegt (Referenzpunkte, geprüft), die Symbole sind bereits auf der Karte. Es fehlt nur das Planbild
selbst als Karten-Ebene. Daraus folgen die Leitplanken:

- Nichts doppelt pflegen: Georef, Marker und Druckregeln bleiben die einzigen Quellen.
- Im Einsatz nur Geprüftes: Entwürfe und KI-Vorschläge erscheinen nie auf der Einsatzkarte.
- Die Karte darf nie schlechter werden: bei jedem Fehler normale Einsatzkarte, Marker und Touch vorrangig.
- Bildschirm und Druck sind getrennte Entscheidungen.

## 2. Bestandsaufnahme (Fundstellen)

| Baustein | Bestand | Folge für den Plan |
|---|---|---|
| Georef | `ObjektDokumentGeoref` (`app/models/objekt.py:817`), je Org/Dokument/Seite/Revision, `status` gueltig/abgelehnt, RMSE, `geprueft_von/am`, `dokument_hash`. Rechnung in `app/services/objekt_georef_service.py` (`bild_zu_geo`, Modelle aehnlichkeit/affin/homographie in lokaler Ost/Nord-Ebene, `_abbilden`, `_vorschau_ecken`) | Overlay **referenziert** die Georef (id + revision + dokument_hash), kopiert keine Punkte. Eckpunkte des Ausschnitts rechnet der Server mit `_abbilden`. |
| Seitenbild | `ObjektDokumentSeite.bild_pfad` (150 dpi PNG), Rotation persistent in `rotation`, Auslieferung mit Org-Check `GET /objekt-medien/seite/{id}/bild` (`ui_objekt_dokumente.py:745-815`, `_bild_response`) | Normierte Koordinaten gelten auf der **gedrehten** Seite, genau wie in der Georef. Derivat nutzt dieselbe Drehlogik und denselben Auth-Weg. |
| Georef-Editor | `objekt/georef_editor.html`, `objekt_georef_editor.js` (Leaflet `L.imageOverlay` nur für Editor-Vorschau, Zeile 306), Route `/objekte/{id}/dokumente/seite/{seite_id}/georef` | Ausschnitt-Editor hier anknüpfen, nicht neu bauen. |
| Marker | `ObjektKartenObjekt` mit `position_status`, Quelle Dokument/Seite/Hash, `objekt_karte_service.py`, Auslieferung `/objekte/{id}/karte/objekte.json` | unverändert; Overlay liegt darunter. |
| Einsatzkarte | `incident/lagefuehrung.html` + `app/static/js/lagefuehrung.js` (Layer-Checkboxen `lft-layer-*`, `bindToggle`, Gruppen `layerObjekt` …, Objekt-Daten über `ui_lagefuehrung.py:385 lagefuehrung_objekte`), Einsatz-Info-Karte `einsatz_info.js` (Objekte über `objekt_ids`) | Neue Ebene in beiden; Objektzuordnung nur über `ObjektEinsatz.status == bestaetigt` (ist bereits die "eindeutige Zuordnung"). |
| Geteilter Einsatzzustand | Lageführung: `_log_event` + `manager.broadcast(incident_id, {...})`, `_check_edit_access` | gleicher Mechanismus für Sichtbarkeit/Deckkraft. |
| Druck Lageführung | `/einsatz/{id}/lagefuehrung/karte/druck` (`ui_lagefuehrung.py:229`), WYSIWYG-Leaflet, `_LFT_DRUCK_VALID_LAYERS`, Einstellungen in localStorage (`lagefuehrung.js:906-976`) | `bsp` als zusätzliche Druck-Ebene; Imageoverlay läuft im gleichen Leaflet, kein zweiter Renderer. |
| Druck GSL-Lagekarte | `DOC_LAGE_KARTE`, `render_map_html` (`print_artifact_service.py:822`), Leaflet über Gateway-Chromium | optional gleiche Ebene (Phase 5b). |
| Druckregeln | `PrintRule` (`models/gateway.py:541`), `objekt_elements` (JSON-Liste), `print_dispatcher._jobs_for_rule` (Abschnitt 2 "Objekt-Elemente"), Idempotenz `build_idempotency_key`, `TRIGGER_DOCUMENT_TYPES` | Neues Objekt-Element `bsp_overlay` + neuer Dokumenttyp. **Keine neue Regel-Tabelle.** |
| Statische Karte | `staticmap` (nur Kreise), nur Lagebericht-PDF | bewusst **nicht** erweitert (siehe 8). |
| MCP | `app/mcp/tools/objekt_karte.py`, `objekt_dokumente.py`; Klasse A/C-Regeln in CLAUDE.md | ein Entwurfs-/Lese-Tool ergänzen, keine Freigabe. |
| Audit | `ObjektChange` (Bereich/Feld/before/after) | Freigabe/Änderung hier protokollieren. |

### Wichtige Befunde, die vom Konzept abweichen

1. **Es gibt keinen automatischen Kartendruck für Einzeleinsätze.** `TRIGGER_DOCUMENT_TYPES[einsatz_created]`
   kennt nur Einsatzinfo, Objektblatt, Objekt-Dokument. Der Einsatz-Kartendruck ist ein manueller
   Browser-Druck. Automatisch druckbar ist heute nur das Planblatt selbst. Folge: `MAP_WITH_BSP` und
   `MAP_AND_BSP_SEPARATE` gibt es **manuell**; automatisch gibt es `BSP_EXCERPT_ONLY` (= wirklich neuer
   Dokumenttyp) und die bestehende Original-Seite (`objekt_dokument`). Ein automatischer Kartendruck
   wird nicht nebenbei erfunden.
2. **Leaflet `imageOverlay` kann nur achsenparallele Rechtecke.** Georef-Modelle drehen/scheren. Ein
   georeferenzierter Plan ist fast nie nordgerichtet. Daher serverseitig **rektifizieren**: Ausschnitt
   per PIL in ein nordgerichtetes RGBA-PNG warpen (außerhalb des Ausschnitts transparent) und mit
   Lat/Lng-Bounds ausliefern. Die Bildrechnung der Georef (ENU-Ebene) und Leaflet-Mercator weichen bei
   Objektgröße (< 1 km) um deutlich unter dem Georef-RMSE ab; das wird im Test belegt, nicht angenommen.
3. **Eine Georef gilt je Dokument-Seite und Revision.** Ersetzt jemand das PDF oder korrigiert die
   Georef, muss ein freigegebenes Overlay automatisch **nicht mehr ausgeliefert** werden
   ("veraltet"), bis neu geprüft. Das ist der Sicherheitsanker gegen falsch liegende Pläne.
4. Die Konzeptaussage "Georef nicht kopieren" lässt sich 1:1 umsetzen (Referenz auf `georef_id` +
   `georef_revision` + `dokument_hash`).

## 3. Datenmodell (Migration 0336, nur Ergänzungen)

`objekt_bsp_overlay` (TenantScoped, in `_TENANT_TABLE_NAMES` und `org_export_service` eintragen):

- `id`, `objekt_id` (FK CASCADE), `dokument_id` (FK CASCADE), `seite`, `georef_id` (FK SET NULL),
  `georef_revision`, `dokument_hash`
- `name`, `plan_art` (lageplan/hallenbereich/gefahrenzone/geschoss/sonstig), `bereich`, `geschoss`
- `ausschnitt_json`: `{"art": "seite"|"rechteck", "x0","y0","x1","y1"}` normiert 0..1 auf gedrehter Seite
  (Polygon später)
- `status`: `entwurf` / `freigegeben` / `zurueckgezogen`; `freigabe_nr` (hochgezählt je Freigabe)
- `freigegeben_von`, `freigegeben_am`, `erstellt_von`, `erstellt_am`, `aktualisiert_am`
- Anzeige: `standard_sichtbar` (bool), `standard_deckkraft` (0.1..1.0, Default 0.6), `empfohlen` (bool, max. ein
  Overlay je Objekt; Service erzwingt)
- Druck: `autodruck` (bool, Default false), `druck_deckkraft`
- Derivat: `raster_pfad`, `raster_sha256`, `raster_bounds_json` (SW/NO), `raster_breite/hoehe`, `quelle_text`

`bsp_overlay_einsatz` (TenantScoped): `incident_id`, `overlay_id`, `sichtbarkeit` INHERIT/ON/OFF,
`deckkraft` (nullable), `geaendert_von/am`; Unique (`incident_id`,`overlay_id`).

Kein neues Druckregel-Modell, keine Änderung an `ObjektDokumentGeoref`.

**Gültigkeit ("auslieferbar")**: eine einzige Service-Funktion `overlay_ist_auslieferbar(db, overlay)`:
`status == freigegeben` **und** Dokument `ist_aktuelle_version` und `freigabe_status == freigegeben` **und**
aktuelle Georef (höchste Revision der Seite) hat `status == gueltig` **und** `id/revision/dokument_hash`
stimmen mit dem Overlay überein **und** Raster existiert mit passender Prüfsumme. Alle Auslieferungs-,
Einsatz- und Druckpfade nutzen nur diese Funktion. Sonst: Ebene fehlt, normale Karte, Hinweis im
Objekt ("veraltet, bitte neu prüfen").

## 4. Phasen (jeweils kleine Codex-Pakete, 1 Punkt pro Auftrag)

### P1 – Modell + Rektifizierung (Backend, ohne UI)
- Migration 0336, Modelle, Tenant-/Export-Registrierung.
- `app/services/objekt_bsp_overlay_service.py`: Anlegen/Ändern (Entwurf), Ausschnitt validieren,
  `raster_erzeugen` (Ecken über `_abbilden` ⇒ Lat/Lng-Bounds; PIL `Image.transform` AFFINE bzw. PERSPECTIVE
  bei Homographie; Zielgröße gedeckelt, max. ca. 2048 px Langseite; Alpha-Maske außerhalb), `freigeben`
  (nur `OBJEKT_VERWALTER_ROLES`; verlangt auslieferbare Georef `gueltig`; erhöht `freigabe_nr`),
  `zurueckziehen`, `overlay_ist_auslieferbar`, `overlays_fuer_einsatz(db, incident)`.
- Raster liegt unter `OBJEKT_MEDIA_DIR` (Ablage wie Seitenbilder), nie unter `static`.
- Tests: Geometrie (bekannte Georef ⇒ erwartete Bounds, Eck-Rückprojektion < 0,5 m; Rotation 0/90/180/270;
  affin und homographisch), Mercator-vs-ENU-Abweichung klein, Veralten bei neuer Georef-Revision und bei
  neuer Dokumentversion, Tenant-Isolation, Freigabe nur mit Rolle, Audit-Eintrag in `ObjektChange`.

### P2 – Objektverwaltung (UI + Routen)
- Routen in `ui_objekt_dokumente.py` (Auth `OBJEKT_VERWALTER_ROLES`, CSRF): Liste/Anlegen/Ändern/
  Ausschnitt/Vorschau/Freigeben/Zurückziehen; Rasterauslieferung `GET /objekt-medien/bsp-overlay/{id}/raster`
  (Org-Check wie `seite_bild`, `Cache-Control: private`, ETag = `raster_sha256`, nur wenn auslieferbar).
- UI als **Bereich "BSP-Overlay" im bestehenden Georef-Editor bzw. der Objektkarten-Seite**
  (`objekt/karte.html`, `objekt/georef_editor.html`), kein neuer Hauptmenüpunkt: Ausschnitt per Rechteck
  auf dem bereits dort geladenen Seitenbild ziehen (oder "ganze Seite"), Vorschau auf Luftbild/OSM mit
  Deckkraft-Regler, Daten (Name, Planart, Geschoss, Version, Qualität = RMSE/Status der Georef),
  Statusanzeige inkl. "veraltet", Aktionen. Mobil ≤ 760 px.
- Auto-Vorschlag: existiert schon eine gültige Georef der Seite, wird "Ganze Seite" vorgeschlagen.
- Tests: Rechte, CSRF, 404 bei fremder Org, kein Ausliefern von Entwürfen, Playwright-Smoke später (P7).

### P3 – Einsatzkarte Lageführung
- Neue Route `GET /einsatz/{id}/lagefuehrung/bsp-overlays.json` (Lese-Zugriff wie `objekte.json`): nur
  bestätigt zugeordnete Objekte, nur auslieferbare Overlays; Felder: id, Name, Geschoss/Bereich, Planart,
  `freigabe_nr`, Stand, bounds, `raster_url` (mit `?v=freigabe_nr-sha`), Default-/Einsatzzustand,
  effektive Sichtbarkeit.
- Route `POST …/bsp-overlays/{overlay_id}/einstellung` (`_check_edit_access`): `sichtbarkeit`
  INHERIT/ON/OFF, `deckkraft`; `_log_event`, Broadcast `lagefuehrung.bsp.changed`, Antwort per JSON.
- JS: gemeinsames Modul `app/static/js/bsp_overlay_layer.js` (Leaflet-Pane `bsp` mit `zIndex 250`, also über
  Tiles und unter Vektor-/Marker-Panes; `L.imageOverlay` mit `interactive:false` ⇒ Touch/Pan/Zoom
  unverändert). In `lagefuehrung.js` als Ebene "BSP" in der bestehenden Layer-Steuerung
  (`lft-layer-bsp` + Deckkraft-Slider, Dropdown bei mehreren Overlays mit Geschoss-/Bereichsnamen),
  Opt-out sofort sichtbar neben den Layern. Zwei klar getrennte Schalter: "für alle im Einsatz"
  (serverseitig, Standard bei Berechtigung) und "nur für mich" (localStorage, try/catch). Reihenfolge:
  Grundkarte → BSP → Objektsymbole → Lage/Einsatz → Einheiten.
- Fehlerverhalten: Raster-Ladefehler ⇒ Ebene still entfernt + dezenter Hinweis, nie Fehlerdialog. Objektwechsel
  löst neu auf (WS-Event `objekt_einsatz`-Änderung bzw. Reload der Liste).
- Offline: Service-Worker/Cache wird **nicht** erweitert; Browser-HTTP-Cache mit ETag genügt, alter Stand wird
  mit "Stand <freigabe_nr/Datum>" im Layer-Popover ausgewiesen. (Prüfen, ob ein Tablet-Cache für Einsatzkarten
  existiert, in P3-Recherche; wenn ja anbinden, sonst kein Neubau.)
- Tests: JSON nur bestätigt+freigegeben, Mehrtreffer/Vorschlag-Zuordnung liefert nichts, INHERIT/ON/OFF-Auflösung,
  Rechte (Leser darf nur lesen), WS-Event, JS-Test für Layer-Logik (`npm run test:js` Muster).

### P4 – Weitere Einsatz-Kartenansichten
- `einsatz_info.js` (Einsatz-Info-Karte) und – nach Prüfung, ob dort Objekte vorkommen – GSL-Karte
  (`incident_major/karte.html`) bekommen dieselbe Ebene über das Modul aus P3, **nur lesend** mit lokalem
  Schalter (GSL hat keine Lageführung-Einstellung). Infoscreen bewusst nicht.

### P5 – Druck
- **Manuell Lageführung**: Druckdialog erhält Block "Brandschutzplan" mit Modus
  `MAP_ONLY` / `MAP_WITH_BSP` / `BSP_EXCERPT_ONLY` / `MAP_AND_BSP_SEPARATE`, Overlay-Auswahl (eines/alle),
  Deckkraft; **Vorbelegung** aus dem Bildschirmzustand, aber im Dialog sichtbar und änderbar; Vorschau ist die
  bestehende Druckseite (WYSIWYG). Server-Parameter `bsp_modus`, `bsp_ids`, `bsp_deckkraft` in
  `lagefuehrung_karte_druck`; nur auslieferbare Overlays werden aufgenommen, sonst Hinweis auf dem Blatt
  "BSP-Overlay nicht verfügbar" statt stillem Weglassen. Kopf/Fuß mit Objekt, Einsatznummer, Plan,
  Freigabe-Nr./Stand, Hinweis "BSP-Overlay", Quelle; **keine Maßstabsleiste** bei rektifiziertem Overlay
  (Leaflet-Scale der Grundkarte bleibt unverändert), kein Maßstabswert auf dem Plan.
- **Dokumenttyp `bsp_overlay`** (`DOC_BSP_OVERLAY`, WeasyPrint-Pfad wie `objekt_dokument`): `BSP_EXCERPT_ONLY`
  als Seite mit Titel, Objekt, Planart/Geschoss, Quelle (Dokument/Seite/Version), Freigabe, Datum; Ausschnitt
  aus dem Originalbild (nicht rektifiziert, nicht verzerrt); Graustufen-Warnung nicht hart codiert, sondern
  Druckerstandard `color` beachten (Hinweis in der Regel-UI, wenn Drucker s/w).
- **Automatisch**: neues Objekt-Element `bsp_overlay` in `OBJEKT_ELEMENT_LABELS`/`objekt_element_optionen`;
  `_jobs_for_rule` Abschnitt 2 erzeugt je bestätigt zugeordnetem Objekt und Overlay mit `autodruck=true` und
  auslieferbar einen Job (`artifact_ref = "{overlay_id}:{freigabe_nr}"` ⇒ Doppelauslösung durch den bestehenden
  Idempotenzschlüssel ausgeschlossen, neue Freigabe druckt bewusst neu). Nur wenn die Regel das Element
  ausdrücklich enthält. `TRIGGER_DOCUMENT_TYPES` für Einsatz-Trigger erweitern, `render_job_pdf` verdrahten.
  Renderfehler ⇒ bestehender Fehler-/Fallback-Weg (`mark_job_failed`), andere Jobs laufen weiter.
- `MAP_ONLY`-Default bleibt, weil es keinen automatischen Kartendruck gibt (Befund 1). Falls später ein
  automatischer Karten-Einsatzdruck kommt, hängt `MAP_WITH_BSP` dort an derselben Ebene.
- 5b (optional): GSL `DOC_LAGE_KARTE` mit `bsp=`-Parameter im `artifact_ref` und Ebene in `karte_druck.html`.
- Tests: Modi, Parameter-Validierung, Wegfall bei veraltetem Overlay, Idempotenz (zweimal Dispatch ⇒ ein Job),
  neue Freigabe ⇒ neuer Job, Regel ohne Element ⇒ kein BSP-Job, Bildschirm-OFF ändert Regeldruck nicht,
  Rechte.

### P6 – MCP
- Erweiterung statt neuem Teilsystem: `objekt_karte_lesen`/`objekt_karte_vorschau` liefern Overlay-Liste und
  Status; **ein** neues Tool `objekt_bsp_overlay_entwurf` (anlegen/ändern als Entwurf, Klasse A; Ausschnitt in
  normierten Bildkoordinaten, Server rechnet Bounds) mit `editor_link` für den Menschen. **Kein**
  Freigabe-Tool. Doku in `docs/objektkarte-ki-symbolpflege.md`, CLAUDE.md-Abschnitt BSP ergänzen.
- Tests analog `tests/test_mcp_objekt_georef.py`.

### P7 – Härtung, Doku, Rollout
- Playwright/E2E: Freigabe → Einsatzkarte zeigt Ebene, Opt-out, Marker bleiben klickbar, Druckvorschau.
- Mobile/Tablet-Prüfung, Performance (Rastergröße, Ladezeit), Rechte-/Sicherheitsprüfung (kein Zugriff ohne
  Login, fremde Org 404, Entwurf 404).
- Doku `docs/architecture/bsp-overlay.md`, Wiki-Seite, CHANGELOG, Projekt-Memory. Navigation: kein
  Verwaltungsmenü-Eintrag nötig (Bereich liegt im Objekt); `tests/test_admin_navigation.py` prüfen.
- Pilot mit 404/406 (haben menschlich geprüfte Georef, Memory `bsp_georef`).

## 5. Reihenfolge / Abhängigkeiten

P1 → P2 → P3 (ab hier nutzbar im Einsatz) → P4/P5 parallel möglich → P6 → P7. Jedes Paket ein PR mit
getrennten Commits; Codex-Regeln: kein `ruff format`, kein `--fix`, nichts umformatieren; Tests der
Aufgabenliste abhaken; Branch/Commit macht Claude.

## 6. Qualitätsgrenzen (nicht verhandelbar)

- Keine erfundenen Koordinaten; Bounds nur aus gespeicherter, gültiger Georef.
- Nur `freigegeben` + auslieferbar im Einsatz und im Auto-Druck.
- Kein offenes Bild-URL-Schema: Auth wie Seitenbilder, `private`-Cache, ETag.
- Freigabe immer menschlich; MCP kann nur Entwürfe.
- Georef-Qualität: Freigabe nur bei Status `gueltig`; RMSE und Punktzahl stehen sichtbar in der Freigabe.
  Zusätzlich Warnung (bestätigungspflichtig), wenn nur 3 Punkte oder `nur_freie_klicks` (Hinweise aus
  `_hinweise` werden wiederverwendet).

## 7. Offene Annahmen (vor P1 bestätigen, falls abweichend gewünscht)

1. Overlay lebt an **Dokument-Seite + Georef**, nicht an eigener Georef (keine Doppelpflege).
2. Gemeinsame Einsatz-Sichtbarkeit nur für Nutzer mit Lageführung-Bearbeitungsrecht; alle anderen haben nur den
   lokalen Schalter.
3. Auto-Druck umfasst nur den BSP-Ausschnitt, nicht die Karte (siehe Befund 1).

## 8. Bewusst nicht enthalten

Polygon-Ausschnitte, mehrere empfohlene Standardoverlays, Kachelpyramide (Raster ≤ 2048 px genügt für
Objektgröße), Overlay im Lagebericht-PDF (`staticmap`), Infoscreen, automatische Registrierung des Plans
(siehe `bsp-orthofoto-plan.md`, Studie D gescheitert).
