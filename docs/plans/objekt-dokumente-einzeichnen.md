# Objektdokumente einzeichnen und beschriften

Stand: 2026-11-03

## Ziel

In der Objektverwaltung können Seiten von Objektdokumenten (Brandschutzpläne,
Einsatzpläne, Fotos als A4-PDF …) direkt bearbeitet werden: freihand zeichnen,
Pfeile/Linien/Rechtecke/Ellipsen/X, Text schreiben, Flächen und **taktische Zeichen
aus der Symbolbibliothek der Lageführung** (`static/tz/tz-manifest.json`) einfügen.
Bedienung und Werkzeuge sind identisch mit dem Bild-Editor an Aufträgen, Meldungen,
Personen, GSL-Einsatzstellen und Übungen (`/annotieren/{media_typ}/{media_id}`).

Zusätzlich kann der Editor (überall, auch im Einsatz) die **Objekt-Symbole** der
Organisation (`objekt_symbol`-Katalog, z. B. BMZ, FSD, Hydrant) einfügen.

## Grundsatzentscheidungen

1. **Kein zweiter Editor.** Die Objektseite wird ein weiterer `media_typ`
   (`"objekt_seite"`) in der bestehenden Annotation-Registry
   (`app/services/annotation_service.py`). Editor, Autosave, Offline-Puffer,
   Soft-Lock, Versionsarchiv (`media_annotation_version`) und Quota werden
   wiederverwendet. Keine neue Tabelle, keine Migration (`media_typ` ist
   `String(16)`, ohne Check-Constraint).
2. **Nicht-destruktiv.** `bild_pfad`, `thumb_pfad`, `einzel_pdf_pfad` und das
   Original-PDF bleiben unverändert. Die Einzeichnung erzeugt drei
   Zusatzdateien neben dem Seitenbild:
   - `seite_NNNN_annotated.png` – flaches Bild (Seite + Einzeichnung)
   - `seite_NNNN_annotated_thumb.jpg` – Miniatur
   - `seite_NNNN_annotated.pdf` – Einzelseiten-PDF: Original-Vektorseite mit
     aufgestempelter transparenter Einzeichnungsebene (Text/Pläne bleiben scharf)
3. **Gespeichert wird in der ungedrehten Seitenlage.** Der Editor arbeitet auf
   der angezeigten (gedrehten) Seite. Der Server dreht flaches PNG und
   Overlay-PNG vor dem Speichern um `-seite.rotation` zurück. Dadurch funktioniert
   `_bild_response(pfad, …, seite.rotation)` für Original und Einzeichnung gleich,
   und das Overlay passt auf die (ungedrehte) Einzelseiten-PDF.
   `annotation_json` wird zusammen mit der Rotation gespeichert, bei der es
   entstanden ist (`{"rotation": r, "stage": <Konva-JSON>}` – Lesecode
   akzeptiert weiterhin reines Konva-JSON für die Bestandstypen).
4. **Drehen einer eingezeichneten Seite ist gesperrt** (409, Hinweis „Seite
   enthält Einzeichnungen – vor dem Einzeichnen drehen oder Einzeichnung
   entfernen“). Der Dreh-Button ist dann deaktiviert.
5. **Wer darf was?** Einzeichnen/Entfernen: `objekt_verwalter` (und höher,
   wie `seite_bearbeiten`). Ansehen: alle, die die Seite heute sehen
   (`EINSATZ_LESE_ROLLEN`, Modul Objekte aktiv, Org-Scoping wie
   `_seite_fuer_user`). Das externe Objektpflege-Portal bekommt die Funktion
   in diesem Plan nicht.
6. **Lesende Nutzung zeigt die Einzeichnung, maschinelle Nutzung das Original.**
   - Mit Einzeichnung: Objekt-Viewer, Galerie-Thumbs, `/objekt-medien/seite/{id}/bild|thumb|pdf`,
     Sammel-PDF, Objektblatt mit Anhang, Einsatzdruck (`print_artifact_service`,
     `dokumentdruck_service`), Einheitenmodus (`ui_einheit.py` Seitenauslieferung,
     `einheit_vorschau_service`), Übernahme in Auftrag/Meldung (`takeover_service`),
     MCP `objekt_dokument_seite_ansehen` / `einsatz_objekt_dokument_seite_ansehen`.
   - Immer Original: OCR/Volltext, KI-Klassifizierung, Georeferenzierung,
     BSP-Overlay-Raster, Katplan-Quellbilder, Original-Datei-Download
     (`/objekt-medien/dokument/{id}/original`).
   - Jede lesende Route akzeptiert `?original=1` für die unbearbeitete Seite.
7. **Neue Dokumentversion** (Pflegeauftrag/Freigabe): Einzeichnungen bleiben an der
   alten Version und werden **nicht** automatisch übernommen. (Übernahme aus der
   Vorversion ist ein möglicher späterer Ausbau.)
8. **„Erneut verarbeiten“** erzeugt neue Seitenzeilen mit denselben Dateinamen. Bestehende
   Annotationen werden per `seiten_nr` auf die neuen Seiten-IDs umgehängt; Seiten,
   die es nicht mehr gibt, verlieren ihre Annotation (inkl. Dateien und Quota).

## Phasen (je ein Codex-Paket)

### P1 – Backend: media_typ `objekt_seite`

- Registry-Eintrag in `annotation_service._build_registry()`: Modell
  `ObjektDokumentSeite`, `kind_of` → `"image"` wenn `bild_pfad` gesetzt,
  `abs_path` → `absolute_pfad(seite.bild_pfad)`, `access` → Org-Scoping wie
  `_seite_fuer_user` + Modul Objekte aktiv, `thumb_path` → `None` (eigene Miniatur,
  siehe unten, das Original-Thumb wird nie überschrieben), `org_of` → `seite.org_id`.
- `can_write` für `objekt_seite`: `has_role(user, "objekt_verwalter")`
  (Hierarchie wie bei `require_role("objekt_verwalter")`).
- `MEDIA_TYPEN` in `app/models/media_annotation.py` ergänzen.
- `ui_annotation.py`: Rollenlisten der Editor-/Lese-/Schreib-/Lock-Routen um
  `objekt_verwalter` erweitern (Zugriff entscheidet weiter `can_read`/`can_write`),
  Guard analog `_guard_probe`: `objekt_seite` nur bei aktivem Objektmodul, sonst 404.
- Neuer Service `app/services/objekt_seite_annotation.py`:
  - `speichere_objekt_seite(db, user, seite, annotation_json, png, overlay_png)`:
    ruft `save_annotation` NICHT für die Datei-Erzeugung auf, sondern eigene Logik:
    PNG und Overlay dekodieren, um `-rotation` zurückdrehen, `_annotated.png`
    schreiben, Miniatur `_annotated_thumb.jpg` (gleiche Größe wie bestehende Thumbs),
    `_annotated.pdf` = Einzelseiten-PDF + Overlay (Overlay-PNG per WeasyPrint als
    1-Seiten-PDF exakt in MediaBox-Größe der Einzelseite rendern, mit pypdf
    `merge_page` aufstempeln). Quota-Delta über alle drei Dateien wie in
    `save_annotation` (reserve vor dem Schreiben). Versionsarchiv und
    `annotated_at/by` wie gehabt.
  - `entferne_objekt_seite_annotation(db, seite)`: Dateien löschen, Quota freigeben,
    Zeile + Versionen löschen.
  - Pfad-Helfer (einzige Quelle für alle Verbraucher):
    `seite_bild_datei(db, seite, original=False)`,
    `seite_thumb_datei(...)`, `seite_pdf_datei(...)` → `Path | None`,
    liefern Einzeichnung, falls vorhanden und Datei existiert, sonst Original.
    Dazu Massenabfrage `annotierte_seiten(db, seiten_ids) -> dict[int, int]`
    (Seiten-ID → annotated_at-Zeitstempel als Cache-Buster).
- `annotate_save` verzweigt bei `objekt_seite` in den neuen Service;
  `AnnotationPayload` bekommt optional `overlay_png`.
- Neue Route `DELETE /api/annotation/objekt_seite/{id}` („Einzeichnung entfernen“,
  nur `can_write`).
- `total_annotated_bytes` berücksichtigt alle drei Dateien von `objekt_seite`.
- Aufräumen: `delete_dokument` (Objektdokument löschen) ruft
  `entferne_objekt_seite_annotation` für alle Seiten vor dem Kaskadenlöschen;
  „Erneut verarbeiten“ hängt Annotationen per `seiten_nr` um (Entscheidung 8).
- `seite_drehen`: 409 bei vorhandener Einzeichnung.
- Tests: neue Datei `tests/test_objekt_seite_annotation.py` (Speichern inkl. Dateien,
  Rotation 90° → gespeichertes PNG ungedreht, PDF hat 1 Seite und gleiche MediaBox,
  Quota-Buchung, Rechte: readonly darf lesen aber nicht speichern, fremde Org 404,
  Modul aus 404, Drehen gesperrt, Entfernen, Dokument löschen räumt auf,
  Neuverarbeitung hängt um).

### P2 – Verbraucher auf die Pfad-Helfer umstellen

Alle in Entscheidung 6 als „mit Einzeichnung“ gelisteten Stellen verwenden die
Helfer aus P1; `?original=1` an den drei `/objekt-medien/seite/…`-Routen und der
Einheiten-Route. Einzeichnungs-Dateien werden mit `Cache-Control: no-cache`
ausgeliefert. Maschinelle Nutzer bleiben bewusst auf den Originalpfaden (im Code mit
kurzem Kommentar markieren). Tests: Sammel-PDF/Einsatzdruck/Übernahme verwenden
die Einzeichnung, `?original=1` das Original, OCR/Georef unverändert.

### P3 – Oberfläche

- Objekt-Viewer (`templates/objekt/viewer.html`): Button „Einzeichnen“
  (`icon("pencil")`, nur mit Schreibrecht) → `/annotieren/objekt_seite/{seite_id}`;
  Badge „Bearbeitet“ + Umschalter „Original anzeigen“ bei Seiten mit Einzeichnung;
  „Einzeichnung entfernen“ mit Bestätigung; Dreh-Buttons bei bearbeiteten Seiten
  deaktiviert mit Tooltip. Bild-URLs mit `?v=<annotated_at>` gegen Browser-Cache.
- Galerie (`objekt/_dokumente.html`): Stift-Badge auf bearbeiteten Thumbs,
  Aktion „Einzeichnen“ im Seitenmenü.
- Editor (`annotate/editor.html` + `annotate.js`): Rücksprung für `objekt_seite`
  zum Viewer an derselben Seite (statt `document.referrer`), Titel „Dokumentseite
  bearbeiten“; beim Speichern zusätzlich Overlay-PNG (nur Annotation-Layer,
  transparenter Hintergrund, gleiche Pixelgröße wie das Seitenbild) senden;
  `annotation_json` im Format `{"rotation", "stage"}` lesen/schreiben (nur für
  `objekt_seite`).
- Mobil (≤760 px) und Tablet prüfen (Buttons ≥ 44 px, Toolbar umbrechend).

### P4 – Symbolbibliothek im Editor

- TZ-Panel heißt „Symbole“ und hat zwei Reiter: **Taktische Zeichen (Lageführung)**
  aus `tz-manifest.json` (wie bisher, inkl. Flächen, Suche, Zuletzt verwendet) und
  **Objekt-Symbole** aus `/objekte/karten-symbole.json`
  (`symbol_katalog_json`), nur wenn das Objektmodul aktiv ist. Bildsymbole über ihre
  geschützte URL, Text-/Stil-Symbole als gerenderte Kachel (wie in `objekt_karte.js`).
- Eingefügte Objekt-Symbole sind wie taktische Zeichen Gruppen mit
  `annoType="symbol"`, skalier-/dreh-/beschriftbar, und werden über `hydrate`
  wiederhergestellt.
- Gilt für alle Editor-Aufrufe (Einsatz, GSL, Übung, Objekt).

### P5 – Doku

`CHANGELOG.md` (Unreleased), Wiki-Seite zur Objektverwaltung (Dokumente →
„Einzeichnen“), Hinweis in `docs/einsatzwissen/dokumentviewer-phase2.md`.

## Offene Punkte nach Umsetzung

- Gerätetest Tablet/Handy (Stift, Pinch-Zoom, Symbole).
- Später möglich: Einzeichnungen aus Vorversion übernehmen; Einzeichnen im
  Objektpflege-Portal; Drehen trotz Einzeichnung (Vektor-Transformation).
