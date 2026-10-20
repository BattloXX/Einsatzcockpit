# Katastrophenpläne – MCP-Ausbau nach dem Pilot-Feedback

Stand: 2026-10-20. Grundlage: Rückmeldung eines KI-Clients nach der Aktualisierung des
Hochwasserplans im Test-ECP (27 Elemente, 5 verortet, 21 Quellseiten). Der Plan blieb im
Arbeitsstand.

## 1. Befund und Ursachen

| Prio | Befund | Ursache im Code |
|---|---|---|
| P1 | `daten.bedarf[].menge` verschwindet | `katplan_typen.bereinige_daten` übernimmt in Listeneinträgen nur `str`-Werte. Eine Zahl wie `500` wird stillschweigend verworfen, ebenso unbekannte Schlüssel. |
| P1 | `adresse: null` löscht nichts | `_adresse(None)` liefert `{}`, `entwurf_aendern` mischt das mit dem Bestand. Auch `{"strasse": null}` wird übersprungen (`if adresse.get(k) is not None`). Nur `""` löscht. |
| P1 | Erfolg trotz verworfener Änderungen | Die Schreibantwort enthält nur `element_id`, `kennung` und `version`, aber nicht, was gespeichert, verworfen oder unverändert blieb. |
| P1 | `element_version_id` beim Lesen `null`, Schreiben verlangt `version` | Im Arbeitsstand gibt `element_dict` bewusst `element_version_id = null` aus. Der Sperrzähler `KatplanElement.version` fehlt im Snapshot (`KatplanVersionElement` hat keine Spalte dafür) und damit in jeder Leseantwort. Der Client errät die Version und läuft in Konflikte. |
| P2 | Seitenbezüge fehlen | `quelle_dokument_id`/`quelle_seiten` sind über `felder` schreibbar, aber nicht in der Werkzeugbeschreibung erklärt. Es gibt kein Massenwerkzeug und keine Seitenvorschau in der Leseantwort. |
| P2 | Nur 5 von 19 Kontrollstellen verortet | `verorten` arbeitet nur einzeln, nur mit Straße und nur mit dem ersten Nominatim-Treffer. Für Brücken, Durchlässe und Gerinne ohne Hausadresse fehlt ein Kandidatenmodus. |
| P2 | Fotos der Quellseiten nicht als Anhang | Kein Werkzeug; nur Upload oder Base64. Seitenbilder (`bild_pfad`) und eingebettete Bilder (pypdf `page.images`) sind serverseitig vorhanden. |
| P2 | Kontakte nicht verknüpft | `katplan_link_service.link_anlegen` / `relation_anlegen` gibt es nur in der Oberfläche, MCP liest Verknüpfungen nur. |
| P3 | Plan-Stammdaten nicht schreibbar | Die Stammdatenlogik steckt im Router (`ui_katplan.speichern`) und hat kein Service- oder MCP-Gegenstück. |
| P3 | Abgleich mit der Quelle | `vergleiche_quellen` vergleicht nur Quellversionen untereinander, nicht Elemente mit Seiten. |

Der grundsätzliche Rahmen bleibt: MCP schreibt nur in den Arbeitsstand, gibt nie frei,
protokolliert mit Quelle `mcp` und verortet immer `ungeprueft`.

## 2. Pakete

### M1 – Schreib-/Lesekonsistenz (P1, zuerst)

**Version beim Lesen:**

- `element_dict` im Arbeitsstand: `version` (Sperrzähler) ausgeben. Der Wert wird aus der
  Live-Zeile ergänzt, nicht aus dem Snapshot.
- `element_version_id` bleibt der Freigabe vorbehalten. Die Beschreibung sagt klar:
  „Zum Schreiben `version` aus `fassung=arbeitsstand` verwenden.“
- `katplan_lesen` (Arbeitsstand) gibt `plan_version` aus, für M4.

**Daten ohne stilles Verwerfen:**

- `bereinige_daten` bekommt einen Protokollmodus: `bereinige_daten(typ, daten, verworfen=liste)`.
- Zahlen (`int`/`float`, nicht `bool`) werden in Text- und Listenfeldern zu Text. Damit
  bleibt `menge: 500` als `"500"` erhalten.
- Alles, was noch verworfen wird (unbekannte Felder, falsche Typen, gekürzte Texte, mehr als
  100 Listeneinträge), landet als Pfad mit Grund in der Liste. Beispiel:
  `daten.bedarf[2].lieferant: unbekanntes Feld`.
- UI und KI-Import verhalten sich unverändert. Die Zahlumwandlung gilt überall.

**Adressen löschen:**

| Eingabe | Wirkung |
|---|---|
| `adresse: null` | Alle vier Felder leeren |
| Einzelfeld `null` oder `""` | Dieses Feld leeren |
| Nicht übergebenes Feld | Bleibt |
| `geometrie: null` | Ort entfernen, `geo_status` wird `keine` |

Die Regeln für `null` und `""` gelten analog für `gueltig_bis`, `pruefen_am` und
`kapitel_id` (schon so) sowie `quelle_dokument_id`. Die Werkzeugbeschreibung nennt sie
ausdrücklich.

**Belastbare Antwort von `entwurf_anlegen` und `entwurf_aendern`:**

- `geaendert`: Feldnamen
- `unveraendert`: übergebene Felder ohne Änderung
- `verworfen`: Pfad und Grund
- `element`: das gespeicherte Element nach `flush`, über `element_dict` aus dem Arbeitsstand
  gelesen; ersetzt die Rückfrage per `katplan_element_lesen`
- `streng: bool = false`: bei `true` führt jedes Verwerfen zu einem Fehler mit Rollback statt
  zu einer Teilspeicherung.
- `pruefen: bool = false` (Trockenlauf): validiert und liefert dieselbe Antwort, rollt aber
  zurück. Das deckt Vorschlag 1 (`katplan_element_validieren`) ohne eigenes Werkzeug ab.

**Tests:**

- Zahlenmenge bleibt erhalten.
- Unbekanntes Feld steht in `verworfen`.
- `streng` führt zum Rollback.
- `adresse: null` und Einzelfeld `null` leeren.
- `geometrie: null` entfernt den Ort.
- Die Leseversion passt zur Schreibversion; der Konflikt meldet die aktuelle Version.
- Der Trockenlauf ändert nichts.

### M2 – Verknüpfungen per MCP (P2)

- **`katplan_verknuepfung_setzen`** (`plan_id`, `element_id` oder Planebene, `ziel_typ` ∈
  kontakt/kontakt_organisation/objekt/fahrzeug/wasserstelle, `ziel_id`, `hinweis`) über
  `link_anlegen`.
  - Dubletten werden idempotent abgefangen.
  - Die Antwort zeigt die Ansicht nach der Telefonregel.
- **`katplan_verknuepfung_entfernen`**.
- **`katplan_beziehung_setzen` / `_entfernen`** (Element ↔ Element, `art`) über `relation_anlegen`.
- **Kontaktsuche:** Für die Suche nach Kontakten verweist die Beschreibung auf
  `kontakt_suchen` und `kontakt_organisation_suchen`.
- **Telefonnummern:** Bei vorhandenem Link bekommt `entwurf_aendern` den Hinweis, sie nicht
  in die Beschreibung zu duplizieren. Die Qualitätshinweise melden Telefonnummern im
  Beschreibungstext neu als Hinweis, wenn ein Kontakt verknüpft ist.

### M3 – Quellseiten und Bilder (P2)

**Quellseiten verknüpfen:**

- Neu: `katplan_quellseite_verknuepfen` (`element_id`, `dokument_id`, `seiten` wie „3-4,7“).
- Arbeitet auch als Massenaufruf mit bis zu 50 Paaren je Aufruf und läuft über
  `validiere_element_quelle`.
- `source_references` in der Leseantwort enthält je Seite `seiten_nr`, `thumb_url` und
  `seiten_url` (geschützte Routen, Freigabe-/Arbeitsstand-Regel wie bisher).

**Bilder aus Quellseiten:**

- Neu: `katplan_anhang_aus_quellseite` (`element_id`, `dokument_id`, `seiten_nr`, `modus`):
  - `seite`: Seitenbild als Foto
  - `bilder`: eingebettete Bilder per pypdf `page.images`, Mindestgröße 200 px, Liste mit
    Index zur Auswahl
  - `ausschnitt`: relativer Rahmen `[x0, y0, x1, y1]` aus dem Seitenbild, mit Pillow
    zugeschnitten
- Ergebnis über `anhang_hochladen`, also mit Quota, Vorschau und Protokoll.
- `beschriftung` wird standardmäßig mit „Quelle: <Titel> v<Nr>, S. <n>“ vorbelegt.
- Ein vorgelagerter Lesemodus (`katplan_quellseite_bilder`) listet die eingebetteten Bilder
  mit Größe und Vorschau-URL, damit der Client gezielt wählen kann.

### M4 – Plan-Stammdaten (P3)

- **Service:** Stammdatenlogik aus `ui_katplan.speichern` in
  `katplan_service.stammdaten_aendern(db, plan, user, version, werte, quelle)` verschieben.
  Dazu gehören Protokoll, Rücksetzen von `wiedervorlage_erinnert_am` und
  `in_ueberarbeitung`. Router und MCP nutzen diesen Service.
- **Werkzeug `katplan_stammdaten_aendern`** (`plan_id`, `version`, `felder`):
  - Felder: `titel`, `beschreibung`, `gefahrenarten`, `geltungsbereich`,
    `verantwortliche_stelle`, `verantwortlich_kontakt_id`, `wiedervorlage_am`
  - Die Antwort ist wie in M1 aufgebaut (geändert, unverändert, verworfen, Plan).
- `status` bleibt ausgeschlossen; die Freigabe bleibt beim Menschen.

### M5 – Verortung in Serie (P2)

- **`katplan_elemente_verorten`** (`plan_id`, `element_ids` oder `nur_ohne_ort=true`,
  `anwenden=false`):
  - Liefert je Element bis zu 3 Kandidaten (Nominatim mit Org-Ort, Viewbox um den Org-Sitz).
  - Weitere Kandidaten kommen aus verknüpften Objekten und Wasserstellen mit Koordinaten.
  - Kandidaten zu Titelbegriffen (Brücke, Durchlass, Bach) werden über die vorhandene
    OSM-Suche ermittelt, sofern sie verfügbar ist.
  - Mit `anwenden=true` und einer ausgewählten Kandidaten-ID je Element wird gesetzt, immer
    `ungeprueft`.
- **`verorten`** in `entwurf_anlegen`/`entwurf_aendern` liefert bei mehreren Treffern die
  Kandidaten zurück, statt still den ersten zu nehmen. Bei eindeutigem Treffer bleibt das
  Verhalten.
- **`entwurf_aendern`** akzeptiert `geometrie_von: {"objekt_id"|"wasserstelle_id": n}`.

### M6 – Abgleich mit der Quelle (P3)

- **Neu: `katplan_import_abgleichen`** (`plan_id`, `dokument_id`), nur lesend:
  - Seiten ohne referenzierendes Element
  - Elemente ohne Quellbezug
  - Mögliche Dubletten innerhalb des Plans: gleicher Typ, Titelähnlichkeit über `casefold`
    und `difflib` ≥ 0.85, gleiche Adresse
  - Offene Import-Vorschläge der Quelle
- Zu jeder Fundstelle schlägt das Werkzeug einen nächsten Schritt vor:
  `katplan_quellseite_verknuepfen`, `entwurf_anlegen` oder Zusammenführen in der Oberfläche.
- `katplan_vorschlaege_aus_dokument` warnt vor Vorschlägen, deren Titel bereits als Element
  existiert (gleicher Abgleich).

## 3. Reihenfolge und Abgrenzung

1. **M1** allein, weil alle weiteren Werkzeuge auf demselben Antwortformat aufbauen.
2. **M2 + M4**, weil beide kleine Werkzeuge auf vorhandenen Services sind.
3. **M3**
4. **M5**
5. **M6**

**Je Paket:**

- Codex-Umsetzung, Prüfung, alle vier CI-Checks, PR und Merge.
- Doku: `Anwender-MCP-Assistent.md`, `Administration-MCP-Server.md`,
  `Entwickler-Katastrophenplaene.md`, `CHANGELOG.md`.

**Nicht enthalten:**

- Freigabe per MCP
- Löschen von Elementen per MCP (bleibt in der Oberfläche)
- Automatisches Übernehmen von Kontakten aus Quelltexten ohne Bestätigung

**Nach M1 – erneuter Testlauf mit demselben Client:**

- Mengen und Adresslöschung prüfen.
- `verworfen` muss leer sein.
- Danach mit M2–M5 die restlichen 14 Kontrollstellen verorten, Fotos zuordnen und Kontakte
  verknüpfen.
