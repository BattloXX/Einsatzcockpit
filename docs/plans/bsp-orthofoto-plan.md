# Plan: BSP-Georeferenzierung über Orthofoto – Nachfolgeplan zum Pilot Senderstraße

Stand: 2026-10-29. Baut auf [bsp-autogeoref-plan.md](bsp-autogeoref-plan.md) (Phasen 1–4, PR #801–#809) auf.
Anlass: Pilot-Feedback „automatische Gebäudezuordnung funktioniert nicht zuverlässig“.

## 1. Befund (belegt, Stand 29.10.2026, lesend gegen das Live-System geprüft)

| Befund | Beleg |
|---|---|
| **Die Lagepläne der Pilotobjekte sind Luftbilder, keine gezeichneten Grundrisse.** 404 und 406: BSP mit basemap.at-Orthofoto (Quellenzeile „basemap.at, BEV, DKM Stand 01.04.2026“) und eingezeichneten Symbolen, Plan-Raster 50 m, Nordpfeil. 198 und 203: „EUS Übersichtsbilder“ = Google-Satellitenscreenshot (Stand 2014) mit rotem Pin und Straßennamen, 856×695 bzw. 1052×788 px, **keine** Gebäudeumrisse. | Seitenbilder `objekt_dokument_seite_ansehen` (404 Dok. 683 S. 3, 406 Dok. 680 S. 3, 203 Dok. 179, 198 Dok. 154) |
| Der Mensch hat bei 406 deshalb **Orthofoto-Klicks** gesetzt, nicht OSM-Ecken: 5 Punkte, Ähnlichkeit, RMSE 1,62 m, max. 2,42 m, Hinweise `punkte_eng` und `restfehler_hoch`. | `objekt_karte_referenzpunkte` lesen, Georef 1 |
| Daraus sind 8 Vorschläge freigegeben worden (FBF, FSD, BMZ, Gas); 4 Zugangspfeile (Rotation 270/90) und der Sammelplatz #25 sind offen. Die Kette Georef → Vorschlag → menschliche Freigabe funktioniert. | `objekt_karte_vorschau` 406 |
| ECP lädt im Georef-Editor schon `bmaporthofoto30cm` von basemap.at (Quelle `orthofoto_klick`). | `app/static/js/objekt_georef_editor.js:309` |
| OSM-Eckenzuordnung (#801) passt nur zu Plänen mit gezeichneten Gebäudeumrissen. Für Foto-Pläne muss ein Sprachmodell Umrisse in einem Foto markieren – das ist die eigentliche Fehlerquelle. | Folge der obigen Befunde |
| Zuordnungsversuche werden nirgends gespeichert. Klasse-A/C-Statistik, Hypothesenabstände und die Regel „höchstens 2 Versuche je Seite“ sind nur im Prompt verankert, nicht prüfbar. | Code: `objekt_karte_georef_zuordnen` schreibt nur bei Klasse A + `speichern` |
| Idempotenz („unveraendert“): Der Fehler ist mit #806 behoben und getestet (`tests/test_vorschlag_idempotenz.py`). Vorschlag #25 wurde um 15:40 angelegt, vor dem Deploy von #806. Eine Wiederholung gegen das Live-System steht aus. | `erstellt_am` 2026-10-29T15:40:37 |
| Umfang: org-weit stehen 402 Lageplan-Seiten ohne Georef an. | `objekt_karte_bsp_arbeitsliste` |

### Machbarkeitsmessung vollautomatische Registrierung (Prototyp, **nicht validiert**)
Plan-Foto 406 gegen basemap.at-Kacheln (Zoom 18, 0,40 m/px), nur numpy und Pillow:
- Kachelabruf: 49 Kacheln in 2,3 s.
- Der gefundene Maßstab (0,34 m/px) stimmt exakt mit der menschlichen Georef überein. Das ist ein echtes Signal.
- Die Feinausrichtung gelingt noch nicht: RMS 9–11 m gegenüber der menschlichen Georef (Korrelation 0,16–0,26, Patch-RANSAC 4 von 26 Inliern), mit 1100-px-Vorschau, Kantenstärke-Korrelation, Farbmaske.
- Ein Lauf braucht ohne Optimierung bis zu 4 Minuten.
Folgerung: Vollautomatische Registrierung ist ein Forschungsrisiko und braucht Vollauflösung, bessere Merkmale und eine Go/No-Go-Entscheidung. Sie darf nicht der einzige Weg sein.

## 2. Grundsätze (unverändert)
- Keine erfundenen oder vom Client gelieferten Weltkoordinaten; Bildpositionen normiert 0..1, Ursprung oben links auf der gedrehten Seite; `expected_rotation` prüfen.
- Speichern nur bei Klasse A; Klasse C nie automatisch, immer `editor_link`.
- Vorschläge bleiben offen bis zur menschlichen Freigabe; keine Entfernung wegen fehlender Symbole; keine Geschossplan-Symbole als Außensymbole.
- Kein Sprachmodell auf dem Server. Der Server rechnet und zeichnet deterministisch; der KI-Client sieht und markiert.

## 3. Neuer Ansatz: Orthofoto-Paare statt Gebäudeumrisse
Der Server rendert einen **Orthofoto-Ausschnitt** mit Koordinatengitter und stellt ihn dem Client wie die Planseite bereit. Der Client markiert dieselben Landmarken (Gebäudeecken, Gleiskreuzungen, Straßeneinmündungen) in Plan und Orthofoto, jeweils normiert. Der Server rechnet die Orthofoto-Position in Weltkoordinaten um. Der Client liefert also weiterhin nur Bildpositionen. Der Server verfeinert jedes Paar lokal und bewertet es.

Warum das besser ist:
- Es entspricht dem, was der Mensch heute im Editor tut.
- Es funktioniert für BSP-Fotopläne und Google-Screenshots, auch ohne gezeichnete Gebäude.
- Es braucht keine gleiche Eckenzahl und keine symmetrie-empfindliche Hypothesensuche.
- Das lokale Verfeinern (Template-Matching in ±5 m um jede Markierung) gleicht die Pixelungenauigkeit des Sprachmodells aus, und das ist ein deutlich kleineres Problem als die Globalregistrierung.

## 4. Phasen mit Abnahmekriterien

### Phase A – Versuchsprotokoll, Versuchslimit, Pilotbericht
- Migration 0336 `objekt_georef_versuch` (Org, Objekt, Dokument, Seite, Methode, Klasse A/C, RMSE, max. Fehler, LOO, Hypothesenabstand, Anzahl Punkte, gespeichert, `batch_id`, Benutzer, Zeit). Org-gebunden, Tenant-Regeln.
- `objekt_karte_georef_zuordnen` und `_uebernehmen` schreiben jeden Versuch (auch Klasse C und Fehlschläge). Optionaler Parameter `pilot_batch_id`: erlaubt höchstens 2 Versuche je Seite und Batch, serverseitig.
- Lesendes Tool `objekt_karte_bsp_pilot_bericht(batch_id)`: Planseiten gefunden, Versuche, Klasse A/C je Objekt, RMSE/Max/LOO/Hypothesenabstand, Vorschläge neu/aktualisiert/unverändert (aus Audit), Symbole je Code, offener Nacharbeitsbedarf mit `editor_link`. Das beantwortet die Erfolgskriterien aus dem Feedback ohne Handarbeit.
- Abnahme: Wiederholter Versuch Nr. 3 wird abgewiesen; Bericht stimmt mit den gespeicherten Daten überein; keine Marker- oder Georef-Änderung durch den Bericht.

### Phase B – Orthofoto-Werkzeuge
- Neuer Dienst `orthofoto_service`: basemap.at-Kacheln abrufen (ehrlicher User-Agent, Attribution „Datenquelle: basemap.at“, Plattencache mit TTL, Rate-Limit, Gesamtbudget, Fehlercodes `ORTHO_*` über `ExternerDienstFehler`), Mosaik zuschneiden, Gitter, Maßstabsbalken und normierte Koordinatenmarken zeichnen.
- Tool `objekt_karte_orthofoto_ansehen(objekt_id, mitte_dx_m, mitte_dy_m, breite_m)`: Ausschnitt **relativ zur Bezugskoordinate des Objekts** (Radius begrenzt, z. B. 400 m), damit Weltkoordinaten ausschließlich aus dem ECP-Bestand kommen. Antwort: Bild, `ausschnitt_id` (deterministisch aus den Parametern), Metadaten (Meter pro Pixel, Bildgröße).
- Tool `objekt_karte_georef_orthofoto_zuordnen(objekt_id, dokument_id, seite, expected_rotation, paare=[{plan_x, plan_y, ortho_x, ortho_y}], ausschnitt, speichern=False)`: Server rechnet die Orthofoto-Position in lat/lng, verfeinert jedes Paar lokal (Vorlage aus dem Plan, Suche im Orthofoto, nur annehmen bei ausreichender Korrelation und Eindeutigkeit), prüft RMSE, max. Fehler, Leave-one-out und Punktverteilung, ordnet Klasse A/C zu. Quelle `orthofoto_auto` (Herkunft im Protokoll). Speichern nur bei Klasse A über den bestehenden `referenzpunkte`-Pfad. Mindestens 4 Paare, gut verteilt.
- Abnahme (Pilot): Objekt 406 wird mit den neuen Werkzeugen mit ≤ 2 m RMSE reproduziert; mindestens ein Foto-Plan (198 oder 203) erreicht Klasse A oder endet begründet in Klasse C; Tests ohne Netz (Kacheln gemockt).

### Phase C – Überlagerungsvorschau
- Tool `objekt_karte_georef_kandidaten_vorschau`: zeichnet das Plan-Foto über den Orthofoto-Ausschnitt (halbtransparent) nach einer berechneten oder gespeicherten Transformation; optional zusätzlich OSM-Hypothesen als Umriss. Antwort: Bild, Warnungen, empfohlener nächster Schritt, `editor_link`.
- So kann der Client sein Ergebnis vor dem Speichern visuell prüfen (Versuch 2), und der Mensch sieht im Bericht, ob Klasse C berechtigt ist.
- Abnahme: bekannte Transformation ergibt deckungsgleiche Überlagerung (Test mit synthetischem Bild); fehlerhafte Transformation ist sichtbar versetzt.

### Phase D – Machbarkeitsstudie automatische Vorregistrierung (Go/No-Go)
- Ziel: Der Server schlägt eine Anfangsabbildung selbst vor, der Client bestätigt nur noch. Nur wenn die Messung trägt.
- Eingabe: Plan in Vollauflösung statt Vorschau, Farbmaske für Symbole, Merkmalsabgleich (zuerst eigene numpy-Implementierung; OpenCV nur bei klarem Nutzen als neue Abhängigkeit, getrennt entscheiden), Maßstabsangabe und Raster aus dem Plan als Vorwissen. Optimierung auf unter 30 s je Seite.
- Ground Truth: bestehende menschliche Georeferenzen (heute belegt: 406). Für die Studie 8–10 weitere Pläne mit Orthofoto-Hintergrund von Menschen referenzieren lassen.
- Kriterium: mindestens 7 von 10 Plänen unter 2,5 m RMS gegenüber der menschlichen Georef, ohne einen Fehlalarm der als sicher eingestuft wäre. Sonst bleibt es bei Phase B/C.

### Phase E – Ablauf und Symbole
- Georef-Antworten (A gespeichert) enthalten einen `weiter`-Block: nächster Schritt Symbolerfassung, Katalogaliase, Prioritätsliste (Zugänge, FSD, BMZ, FBF, Hydranten, Aufstellflächen, Gefahrensymbole), Hinweis `bild_richtung`.
- **Kein serverseitiger Orchestrator** (`bsp_pilot_ausfuehren`): Er könnte nur den deterministischen Teil, und der steckt bereits in Arbeitsliste, Ablaufbeschreibung und Pilotbericht. Ein Server-Ablauf, der „erkennt“, bräuchte serverseitige KI und widerspricht der Entscheidung vom 29.10.
- Idempotenz: Live-Wiederholung des identischen Vorschlags beim nächsten Pilotlauf als Abnahmepunkt.

### Phase F – Doku, Regeln, Memory
- `docs/objektkarte-ki-symbolpflege.md`, Wiki, CHANGELOG, Pflichtabschnitt in `CLAUDE.md` (Orthofoto-Quelle, Quelle `orthofoto_auto`, Attribution), Projekt-Memory.

## 5. Reihenfolge und Empfehlung
A → B → C → (D parallel als Studie) → E → F. A zuerst, weil sie ohne neue Datenquelle die Messbarkeit liefert, die der Pilot braucht. B/C bringen die eigentliche Zuverlässigkeit für Foto-Pläne.

## 6. Risiken und offene Entscheidungen
- **basemap.at serverseitig:** Der Editor lädt Kacheln aus dem Browser. Serverseitiger Abruf bleibt klein (Plattencache, nur Objektumfeld), braucht aber Attribution, ehrlichen User-Agent, Rate-Limit und einen Ausfallweg (→ Klasse C, `editor_link`). Die Nutzungsbedingungen von basemap.at (CC BY 4.0) vor dem Betrieb gegenlesen.
- **Verschiedene Bildquellen:** Google-Screenshot 2014 gegen BEV-Orthofoto 2026 unterscheidet sich (Dächer, Fahrzeuge, Bewuchs). Markierte Landmarken müssen dauerhaft sein (Gebäudeecken, Straßen, Gleise); das Verfeinern lehnt unsichere Paare ab.
- **Genauigkeit des Sprachmodells:** Ein Modell trifft Pixelpositionen nur auf etwa 1–2 % genau. Ausschnitte (Zoom) und lokales Verfeinern sind deshalb Teil von B, nicht optional.
- **OSM-Ecken bleiben** für Pläne mit gezeichneten Umrissen; die Werkzeuge aus #801 werden nicht entfernt.
- **Nicht umgesetzt:** serverseitige KI, automatische Symbolerkennung durch den Server.
