# Plan: MCP-gestützte BSP-Georeferenzierung und Kartenvorschläge

> **Fortsetzung:** Der Pilot Senderstraße (2026-10-29) hat gezeigt, dass die Lagepläne Luftbilder sind. Der Nachfolgeplan [bsp-orthofoto-plan.md](bsp-orthofoto-plan.md) ersetzt die OSM-Eckenzuordnung als Hauptweg durch Orthofoto-Paare; die Phasen 1–4 unten bleiben bestehen.

Stand: 2026-10-29. Grundlage: Konzept "ECP – Vollautomatische BSP-Georeferenzierung und KI-gestützte Lagekartenpflege",
geprüft gegen `main` (4882756b).

**Entscheidung:** Keine serverseitige KI-Engine, keine Job-Queue. Der KI-Client (Claude, ChatGPT) übernimmt über MCP
das Sehen (Ecken, Symbole, Pfeile im Planbild). ECP rechnet ausschließlich deterministisch: Zuordnung zu OSM-Ecken,
Transformation, Qualitätsklasse, Mehrdeutigkeit, Rotation, Dubletten, Freigabe. Produktive Marker entstehen weiterhin
nur über `objekt_karte_freigeben` mit ausdrücklicher Bestätigung.

## 1. Abgleich Konzept gegen Code

| Konzept | Code-Befund | Bewertung |
|---|---|---|
| MCP-Tools `objekt_karte_*` (Umrisse, Referenzpunkte, Vorschläge, Prüfen, Vorschau, Freigeben) | `app/mcp/tools/objekt_karte.py`, Services `objekt_georef_service`, `objekt_karte_vorschlag_service`, `objekt_karte_freigabe_service` | vorhanden |
| Transformation serverseitig, Modelle, Schwellen | `transformation_berechnen`: aehnlichkeit>=3, affin>=4, homographie>=6 Punkte; `MAX_RMSE_M=2.0`, `MAX_FEHLER_M=4.0`, Hüllflächen-, Verzerrungs- und Ausdehnungsprüfung | vorhanden, deckt sich mit Konzept |
| Georef-Versionierung | `ObjektDokumentGeoref` (Migration 0332): Revision je Dokument/Seite, `dokument_hash`, `status`; veraltet bei neuem Hash, geänderter Rotation oder Bildgröße (`georef_pruefen`, `seitenstand_pruefen`) | vorhanden |
| Vorschläge mit `georef_id` + `bild_x/bild_y`, Server rechnet lat/lng | `ObjektKartenVorschlag`, `bild_zu_geo`; georef plus lat/lng gleichzeitig verboten; `quelle_dokument_hash`, `expected_revision`, `erkennungs_konfidenz`, `warnungen_json` | vorhanden |
| Freigabe nur mit Bestätigung | `ICH_BESTAETIGE_FREIGABE`, Rolle `objekt_verwalter`, Warnungen je Vorschlag, Trockenlauf | vorhanden |
| Dublettenprüfung | `_dublette` (gleicher Code und gleiches Label oder < `DUBLETTEN_ABSTAND_M`), `pruefe_karte` | vorhanden; nicht idempotent je Lauf/Planversion |
| OSM-Gebäudeecken | `objekt_gebaeude_service.gebaeude_umrisse`: Overpass `way["building"]`, Cache, Circuit-Breaker, max. 100 Umrisse, `osm_id` + nummerierte Ecken | vorhanden; kein Datenstand, keine amtlichen Quellen |
| Seitenbild für den Client | `objekt_dokument_seite_ansehen(dokument_id, seite, bild, max_kante_px)` | vorhanden; **kein Ausschnitt/Zoom** |
| Dokumentversionen | `ObjektDokument.dokument_gruppe_id`, `versionsnummer`, `ist_aktuelle_version` | vorhanden; Übernahme alter Georef auf neue Version fehlt (wird `veraltet`) |
| Symbolkatalog | `symbolkatalog_lesen`, `EINSATZKRITISCHE_SYMBOLTYPEN` | vorhanden; keine Aliase, keine dokumentierte Nullrichtung |
| Symbolrotation | `ObjektKartenVorschlag.rotation` (Grad, manuell), Darstellung `objekt_karte.js` `objektSymbolHtml` (Winkel mod 360) | vorhanden; keine Ableitung aus Bildrichtung + Georef |
| Automatische Zuordnung Bild-Ecken zu OSM-Ecken | fehlt; heute muss das Modell selbst die OSM-Eckennummer wählen | **Kernlücke** |
| MCP-Prompts (Ablaufvorlage) | im MCP-Server keine Prompt-Registrierung gefunden | Ablauf über Tool-Beschreibungen bzw. ein Lese-Tool |

Fazit: Prüf-, Versions- und Freigabemechanik stehen. Neu sind wenige deterministische Hilfswerkzeuge, die die
fehleranfälligen Schritte (Eckenzuordnung, Rotation, Wiederholung) vom Modell auf den Server verlagern.

## 2. Aufgabenteilung

| KI-Client (über MCP) | ECP-Server (deterministisch) |
|---|---|
| Arbeitsliste lesen, Objekt/Dokument wählen | Arbeitsliste aus Dokumentversionen und Georef-Status |
| Planseite und Ausschnitte ansehen | Seitenbild, Ausschnitt, stabile Koordinatenbasis (Rotation, Bildgröße) |
| Gebäudekontur-Ecken im Bild markieren (normiert 0..1, Reihenfolge entlang der Kontur) | Zuordnung zu OSM-Ecken über alle Hypothesen, Transformation, Leave-one-out, Mehrdeutigkeit, Qualitätsklasse |
| Symbole und Pfeile erkennen, Katalogcode wählen | Lat/lng aus `georef_id` + Bildpunkt, Kartenrotation aus Bildvektor, Dubletten, Idempotenz |
| Ergebnis erklären, Freigabe beim Menschen einholen | Freigabe atomar mit Bestätigungswert und Rollenprüfung |

Kein Automatismus beim Upload: Ein Mensch stößt die Bearbeitung im Chat an ("arbeite die BSP-Arbeitsliste ab").

## 3. Neue und erweiterte MCP-Tools

### 3.1 `objekt_karte_georef_zuordnen` (neu, Kern)
- Eingabe: `objekt_id`, `dokument_id`, `seite`, `expected_rotation`, `bild_ecken` (Liste normierter Punkte entlang der
  Gebäudekontur, mind. 4), optional `osm_id`, `radius_m`, `modell` (Standard `aehnlichkeit`), `speichern` (Standard false).
- Server: OSM-Umrisse laden (`gebaeude_umrisse`), für jedes Kandidatengebäude mit passender Eckenzahl alle zyklischen
  Verschiebungen und beide Umlaufrichtungen (Spiegelung) testen; Transformation über `transformation_berechnen`.
  Leave-one-out je Hypothese (größter Fehler des zurückgehaltenen Punkts). Bei abweichender Eckenzahl: Teilmengen
  zusammenhängender Ecken, begrenzt, um Kombinatorik klein zu halten.
- Qualitätsklasse:
  - A eindeutig: beste Hypothese erfüllt Schwellen inkl. Leave-one-out, zweitbeste deutlich schlechter (Faktor/Abstand als Konstante, im Pilot kalibriert).
  - C mehrdeutig/unzureichend: Schwellen verfehlt oder zwei Hypothesen gleichwertig (symmetrische Halle) – nichts speichern.
- Rückgabe: bis zu 3 Hypothesen (osm_id, Eckzuordnung, RMSE, max. Fehler, Leave-one-out), Klasse, Begründung, Editor-Link
  mit vorbelegten Kandidaten.
- `speichern=true` und Klasse A: Speicherung über den bestehenden Pfad `referenzpunkte(...)`, Rolle wie heute beim Setzen.
- Lat/lng stammen ausschließlich aus OSM-Ecken; der Client liefert nie Koordinaten.

### 3.2 `objekt_karte_georef_uebernehmen` (neu)
- Neue Planversion: Client markiert 4 bis 30 identische, gut verteilte Punkte in alter (`von_dokument_id`, Seite) und neuer Seite.
- Server bildet die gültige alte Georef über Plan-zu-Plan-Ähnlichkeit auf die neue Seite ab, erzeugt Referenzpunkte und
  validiert mit denselben Schwellen. Nur innerhalb derselben Dokumentgruppe (`dokument_gruppe_id or id`),
  Ziel muss aktuell sein. Juengste gespeichert gueltige Quellrevision mit passender Rotation/Bildgroesse verwenden.
- Isotrope Plan-zu-Plan-Rechnung mit eigenem Seitenverhaeltnis; Spiegelung ablehnen. Paarfehler <= 0.01,
  Leave-one-out <= 0.02 in neuer Seitenhoehe (im Pilot kalibrieren). Gespeicherte Referenzen abbilden,
  ausserhalb liegende verwerfen; keine synthetischen Punkte. Klasse A nur mit gueltiger neuer Georef.
  Analyse mit Rollback; Speichern nur fuer Objektverwaltung, bei Klasse C Editor-Link der neuen Seite.

### 3.3 `objekt_dokument_seite_ansehen` erweitern
- Optionaler `ausschnitt` ([x0,y0,x1,y1] normiert, Breite/Hoehe mindestens 0.02) für Zoom auf kleine Symbole; Rückgabe gibt den Ausschnitt und die
  Umrechnung auf Seitenkoordinaten mit an, damit der Client gesamtseiten-normierte `bild_x/bild_y` meldet.
- Bild vor Ausschnitt und Verkleinerung wie im Editor im Uhrzeigersinn drehen, auch Thumbnail-Fallback.
- Metadaten: Bildgroesse der gedrehten Gesamtseite, Rotation als `expected_rotation`, Ausschnitt und
  Umrechnung `bild_x=x0+u*(x1-x0)`, `bild_y=y0+v*(y1-y0)`.

### 3.4 `objekt_karte_vorschlaege` erweitern
- `bild_richtung` ({dx, dy} im normierten Bild): Server berechnet `rotation` über die lokale Ableitung der Transformation
  (`parameter_json`), Nullrichtung und Drehsinn gemäß `objekt_karte.js`; `rotation` und `bild_richtung` gleichzeitig verboten.
- `bild_richtung_x/_y` speichern den normierten Bildvektor; y zeigt nach unten. Pfeilstile
  `pfeil-voll`/`pfeil-leer` zeigen ohne Rotation nach Osten (Basis 90 Grad), andere Symbole nach Norden.
  Kartenrotation = gerundete Peilung minus Basis, modulo 360; ungueltige Georef ergibt 0 und Warnung.
- `batch_id` (Client-gewaehlt, 1-64 Zeichen aus A-Za-z0-9_.:-) kennzeichnet einen Lauf und filtert Listen.
  Idempotenz bei Neuanlage mit Georef: produktiver Marker mit gleichem Hash, Seite und Code unter 3 m
  ergibt `unveraendert`; sonst aktualisiert ein offener Vorschlag mit gleichem Hash, Seite, Code und Operation
  bei Bildabstand unter `BILD_TOLERANZ = 0.01` den Bestand (`aktualisiert`). Sonst `neu`.
  Fuer andere Faelle bleibt der exakte Vergleich (`unveraendert_offen`). `zusammenfassung` zaehlt die Ergebnisse.
- Entfernen nur als Prüfhinweis, nie aus bloßem Fehlen im neuen Plan.

### 3.5 `objekt_karte_bsp_arbeitsliste` (neu, lesend)
- Fertige aktuelle Lageplan-/Brandschutzplan-Seiten von Basisobjekten der eigenen Org; je Seite
  Georef-Status, offene Vorschlaege, Marker aus dem Planhash und passende Vorversion.
- Prioritaet: georef_uebernehmen, georef_zuordnen, vorschlaege_pruefen, symbole_erfassen, aktuell.
  `nur_offen=true` blendet aktuell aus; Limit 1..200 mit Gesamtzahl und Abschneidehinweis.
- Enthält in der Tool-Beschreibung den Ablauf (Ersatz für fehlende MCP-Prompts):
  Arbeitsliste → Seite ansehen → `georef_zuordnen` (bzw. `georef_uebernehmen`) → Symbole mit `bild_richtung` → `objekt_karte_pruefen` → `objekt_karte_vorschau` → Freigabe durch Menschen.

### 3.6 `objekt_karte_symbolkatalog` erweitern
- `aliase`/Legendenbegriffe je Systemcode (eigene Codes: leer), `traegt_richtung` und
  `basisrichtung_grad`: Pfeilstile 90 Grad, andere Stile 0. Keine Migration.

## 4. Phasen

Regeln aus CLAUDE.md gelten durchgehend (DB-Routen synchron, Tenant-Scoping, Icons, Zeitzonen, vier CI-Checks,
Doku/Wiki/CHANGELOG im selben PR).

### Phase 1 – Eckenzuordnung (`objekt_karte_georef_zuordnen`)
- Reiner Service `objekt_georef_zuordnung_service` ohne I/O (testbar mit synthetischen Umrissen), Tool-Hülle in `objekt_karte.py`.
- Tests: Rechteck, L-Form, gedreht, gespiegelt, verschoben, symmetrische Halle (muss mehrdeutig sein), Nachbargebäude,
  falsch markierte Ecke (darf nicht als A durchgehen), Cross-Org, Rollen, Overpass-Ausfall.
- Abnahme: synthetische Fälle korrekt; mehrdeutige Fälle speichern nichts.

### Phase 2 – Richtung und Idempotenz (`objekt_karte_vorschlaege`)
- Rotation aus Bildvektor ueber lokale Transformationsableitung; nichtquadratisches Bild beruecksichtigen.
  Bei kompatibler neuer Georef-Revision Position und Richtung offener Vorschlaege neu berechnen.
- Migration 0334: `bild_richtung_x`, `bild_richtung_y`, `batch_id` an `objekt_karten_vorschlag`,
  Index auf org/objekt/batch_id. Toleranzabgleich statt gerundetem Detektionsschluessel.
- Abnahme: zweiter identischer Lauf erzeugt null neue Vorschläge; Rotation stimmt in der Kartenanzeige.

### Phase 3 – Seitenausschnitt und Versionsübernahme
- Ausschnitt und korrigierte Seitenrotation in `objekt_dokument_seite_ansehen`;
  `objekt_karte_georef_uebernehmen` mit Paar-/LOO-Pruefung und bestehender Georef-Validierung.
- Abnahme: neue Version mit Zuschnitt/Neuscan erhält gültige Georef aus Vorversion; Wechsel der Dokumentgruppe abgelehnt.

### Phase 4 – Arbeitsliste und Katalog; weitere Schritte offen
- Umgesetzt: `objekt_karte_bsp_arbeitsliste`, Katalog-Aliase und Richtungsfelder.
- Offen, nicht Teil dieses PRs: nach Upload einer neuen Lageplan-Version Hinweis an
  Objektverwaltung ("Lageplan braucht Kartenabgleich").
- Offen, nicht Teil dieses PRs: Editor-Link aus `georef_zuordnen` mit vorbelegten Kandidaten.
  Der bestehende Link oeffnet weiterhin den normalen Georef-Editor.

### Phase 5 – Pilot und Doku
- 5–10 Objekte Wolfurt mit Ground Truth; Lauf mit Claude.ai und ChatGPT; Fehler in m und Korrekturquote messen,
  Mehrdeutigkeitsschwelle kalibrieren.
- Doku `docs/` (Tool-Verträge, Ablauf, Qualitätsklassen), Wiki-Seite, CHANGELOG, Memory.

## 5. Nicht-Ziele
- Keine serverseitige KI-Analyse, keine Job-Queue, keine automatische Verarbeitung beim Upload.
- Keine vom Client gelieferten Koordinaten; lat/lng nur aus OSM-Ecken oder bestehender Georef.
- Keine automatische Freigabe, keine Löschung produktiver Marker wegen fehlender Erkennung.
- Geschossplan-Symbole nicht automatisch nach außen projizieren.

## 6. Offene Punkte
1. Amtliche Geodaten (VoGIS Orthofoto/Kataster) als zweite Referenzquelle – später entscheiden, nach Pilot.
2. Pilotobjekte und Ground Truth festlegen.
3. Schwelle "zweitbeste Hypothese deutlich schlechter" im Pilot kalibrieren.
