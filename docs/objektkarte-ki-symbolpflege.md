# Objektkarte: lagegenaue Symbolpflege mit KI

Die KI erfasst belegbare Symbole aus Objektplänen als Vorschläge. Die produktive
Lagekarte ändert sich erst nach menschlicher Freigabe. Koordinaten niemals erfinden,
aus Textnähe ableiten oder selbst aus Bildpositionen umrechnen: Der Server rechnet
mit geprüften Referenzpunkten. Unleserliche oder nicht sicher verortbare Symbole
bleiben ohne Position und sind nicht freigebbar.

Einsatzkritisch sind `fsd`, `bmz`, `fbf`, `hauptzugang`, `nebenzugang`,
`dlk_stellplatz` sowie alle `gefahr_*` und `hydrant_*`. Bedeutung und Standort
müssen ausdrücklich durch einen Menschen geprüft werden; Warnungen sind keine
Aufforderung zur automatischen Bestätigung.

## Rollen und Rechte

Das Objektmodul muss global und für die Organisation aktiviert sein. Alle Daten
bleiben innerhalb der eigenen Organisation. Einsatz-Leserollen dürfen Karte,
Symbolkatalog, Prüfung, Gebäudeumrisse, Referenzen und Vorschläge lesen.
`objekt_verwalter` (einschließlich berechtigter Admin-Rollen) wird für Dokumenttools,
Referenzen setzen/verwerfen, Vorschläge schreiben und Freigaben benötigt.
`veraltete_markieren=true` ist auch beim Auflisten eine Schreibaktion.
Ein Lesetoken darf keine dieser Änderungen ausführen.

## Ablauf für den KI-Agenten

1. Objekt eindeutig auswählen, etwa mit `objekt_suchen` und `objekt_lesen`.
   Bei mehreren Treffern die Zuordnung klären; die produktive Objekt-ID verwenden.
2. `objekt_karte_lesen` und `objekt_karte_symbolkatalog` aufrufen. Bestand,
   Revisionen, Symbolcodes und offene Arbeitskopie beachten. Legacy-Marker sind
   ungeprüfter Bestand, keine gesicherte Referenz.
3. `objekt_dokumente_auflisten`: aktuelle Planversion auswählen. Seiten mit
   `objekt_dokument_seite_ansehen` visuell sichten, `objekt_dokument_text` ergänzend
   lesen. OCR kann falsch sein. Bei Bedarf Klassifizierung nach Sichtung mit
   `objekt_dokument_seiten_klassifizieren` korrigieren; das georeferenziert nichts.
4. Eindeutig wiedererkennbare, über die Seite verteilte Referenzpunkte bestimmen:
   nummerierte OSM-Ecken aus `objekt_karte_gebaeude_umrisse`, Orthofoto-Klicks oder
   dokumentierte Koordinaten. Herkunft (`osm_gebaeude`, `orthofoto_klick`,
   `dokument_koordinate`, gegebenenfalls `manuell`) und Beschreibung je Punkt angeben.
   Eine Gebäudeecke muss im Plan tatsächlich identifiziert sein.
5. `objekt_karte_referenzpunkte(aktion="setzen", pruefen=true)` mit `dokument_id`,
   `seite` und Punkten `{id, x, y, lat, lng, quelle, beschreibung}` ausführen.
   `x/y` liegen in 0..1, Ursprung oben links auf der **angezeigten, gedrehten Seite**.
   Mindestens 3 Punkte für `aehnlichkeit`, 4 für `affin`, 6 für `homographie`;
   möglichst zusätzliche unabhängige Punkte verwenden. Status und Restfehler prüfen,
   erst dann ohne `pruefen` speichern und die persistierte `georeferenz.id` verwenden.
   IDs aus einem Trockenlauf sind nicht dauerhaft gültig.
6. Symbole als normierte `bild_x/bild_y` erfassen. Symbolcode, Dokumentseite,
   optional `quelle_ausschnitt=[x0,y0,x1,y1]`, Erkennungskonfidenz und Begründung
   dokumentieren. Mit `georef_id` keine `lat/lng` oder Geometrie mitsenden.
   Ohne belastbare Position auch keine Bildkoordinaten ohne Georeferenz verwenden:
   stattdessen einen unverorteten Vorschlag anlegen.
7. `objekt_karte_pruefen` für Bestandsprobleme und bereits offene Vorschläge aufrufen.
   Das Tool ist lesend und prüft keine noch ungesendeten Kandidaten.
8. `objekt_karte_vorschlaege(aktion="anlegen", vorschlaege=[...], pruefen=true)`:
   Serverposition und Warnungen prüfen, danach dieselben Daten ohne `pruefen`
   anlegen (maximal 30 pro Aufruf). Die produktive Karte bleibt unverändert.
   Änderungen/Entfernungen brauchen `basis_marker_id` und `expected_revision`.
   Erneut `objekt_karte_pruefen` aufrufen und Dubletten abgleichen.
9. `objekt_karte_vorschau` liefert Bestand, Vorschläge mit Quellen/Georeferenz und
   `ui_link` zur menschlichen Prüfung. Diesen Link zusammen mit Unsicherheiten
   und den betroffenen Vorschlags-IDs an den Nutzer geben.
10. Erst nach ausdrücklicher Nutzerfreigabe `objekt_karte_freigeben` aufrufen:
    `vorschlag_ids`, `bestaetigung="ICH_BESTAETIGE_FREIGABE"` und pro ID die
    ausdrücklich bestätigten Warnungscodes in `bestaetigte_warnungen`, zum Beispiel
    `{"123": ["einsatzkritisch_freigabe_erforderlich"]}`. Auch ein Freigabe-Trockenlauf
    (`pruefen=true`) benötigt diese Bestätigungen. Er speichert weder Marker noch
    Freigabestatus oder Änderungsaudit. Danach produktive Karte erneut lesen.

## Automatische Eckenzuordnung

`objekt_karte_georef_zuordnen` ersetzt fuer geeignete Gebaeudekonturen die manuelle
Wahl der OSM-Eckennummern in den Schritten 4 und 5. Der KI-Client liefert ausschliesslich
`bild_ecken` als Liste `{x, y}` (normiert 0..1, Ursprung oben links), keine `lat/lng`
und keine geschaetzten Koordinaten. Alle Aussenecken **eines** Gebaeudes entlang seiner
Kontur markieren; Startpunkt und Umlaufrichtung sind beliebig. Zulaessig sind 4 bis
30 Punkte mit mindestens 0.005 Abstand zwischen jedem Punktepaar.

1. Aktuelle Seite mit `objekt_dokument_seite_ansehen` ansehen. `objekt_id`,
   `dokument_id`, `seite` und deren `expected_rotation` verwenden. Bei gedrehter
   Seite zuerst das neue Bild ansehen und die Punkte neu erfassen.
2. Mit `objekt_karte_gebaeude_umrisse` Kandidaten lesen. Bei gleichartigen
   Nachbargebaeuden deren belegte `osm_id` angeben, um genau dieses Gebaeude zu pruefen.
   Standardradius: 150 m; OSM-/Overpass-Freigabe und Objektkoordinate sind erforderlich.
3. `objekt_karte_georef_zuordnen(..., speichern=false)` aufrufen (Standardmodell:
   `aehnlichkeit`). Einsatz-Leserollen duerfen analysieren; dies speichert nichts.
4. **Klasse A:** Eindeutige, tragfaehige Zuordnung. Mit denselben Eingaben und
   `speichern=true` kann die Objektverwaltung ueber den bestehenden Referenzpunktpfad
   speichern. Erst die persistierte `georeferenz.id` fuer Symbolvorschlaege verwenden.
5. **Klasse C:** Fehlende passende Eckenzahl, unzureichende Fehlerwerte, zu wenige
   Ecken fuer die Gegenpruefung oder Mehrdeutigkeit. Es wird auch bei `speichern=true`
   nichts gespeichert. Keine der bis zu drei Hypothesen selbst auswaehlen: `editor_link`
   an den Menschen zur Pruefung geben. Symmetrische Gebaeude und identische Nachbarn
   koennen geometrisch nicht eindeutig unterschieden werden.

Der Server entfernt OSM-Zwischenecken mit Richtungsaenderung unter
`KOLLINEAR_WINKEL_GRAD = 10.0` in lokalen Metern; Original-Eckennummern bleiben
bestehen. Nur vereinfachte Ringe mit gleicher Eckenzahl werden beruecksichtigt.
Er prueft alle zyklischen Verschiebungen und beide Umlaufrichtungen, hoechstens
`MAX_HYPOTHESEN = 2000`; bei mehr Kandidaten `osm_id` angeben oder Radius verkleinern.

Die vorhandene Transformationspruefung prueft Spiegelung, Verzerrung und Restfehler
(RMSE maximal 2 m, Einzelfehler maximal 4 m). Nur gueltige Hypothesen erhalten eine
Leave-one-out-Gegenpruefung: jeden Punkt auslassen, erneut rechnen und den Fehler am
ausgelassenen Punkt in Metern messen. Tragfaehig ist eine gueltige Hypothese mit
`loo_max_m <= MAX_FEHLER_M = 4.0`. Bleiben nach dem Auslassen weniger als
`MIN_PUNKTE[modell]` (3/4/6) Punkte, ist Klasse A ausgeschlossen.

Tragfaehige Hypothesen werden nach `loo_max_m`, dann `rmse_m` sortiert. Eine zweite
tragfaehige Hypothese muss mindestens
`max(beste_loo * EINDEUTIG_FAKTOR, beste_loo + EINDEUTIG_ABSTAND_M)` erreichen.
Die Konstanten `EINDEUTIG_FAKTOR = 2.0` und `EINDEUTIG_ABSTAND_M = 1.0` sind im Pilot
zu kalibrieren. Die Antwort nennt Begruendung, gepruefte Hypothesenzahl, vereinfachte
`eckenzahlen_osm`, bis zu drei Hypothesen, Bildgroesse, Rotation und Editor-Link.
Referenzpunkte werden nur bei Klasse A ausgegeben; Koordinaten stammen aus OSM.

## Richtung und Wiederholung

Fuer Pfeile und Zugaenge `bild_richtung={"dx": 1, "dy": 0}` zusammen mit `georef_id`
und `bild_x/bild_y` senden. Der Vektor beschreibt die sichtbare Richtung im Bild
(Pixelverhaeltnis, also wie angezeigt; `{"dx": 1, "dy": -1}` ist 45 Grad nach rechts oben):
rechts ist +x, unten +y; Komponenten jeweils -1..1, Laenge groesser als 1e-6. `rotation`
darf nicht gleichzeitig angegeben werden. Der Server nutzt die lokale
Transformationsableitung und berechnet die Peilung im Uhrzeigersinn ab Nord.
Die nordorientierte Karte dreht ebenfalls im Uhrzeigersinn. Pfeilstile `pfeil-voll` und
`pfeil-leer` (etwa `hauptzugang`/`nebenzugang`) zeigen bei Rotation 0 nach **Osten**:
Rotation = round(Peilung - 90) modulo 360. Andere Symbole verwenden Basis 0 (Norden).
Ohne gueltige Georef bleibt Rotation 0 mit `richtung_ohne_georef`. Eine kompatible neue
Georef-Revision berechnet Position und Rotation offener Vorschlaege erneut.

`batch_id` ist eine optionale Laufkennung (1-64 Zeichen aus `A-Za-z0-9_.:-`) fuer
`anlegen` und `aendern`, auch bei Wiederholung. `auflisten` kann danach filtern.
Bei Neuanlage mit Georef prueft der Server zuerst produktive Marker: gleicher
Dokument-Hash, gleiche Seite und gleicher Code bei Abstand unter 3 m ergeben
`ergebnis="unveraendert"` mit Marker-ID, ohne neuen Vorschlag oder Audit-Eintrag.
Sonst wird ein offener Vorschlag mit gleichem Hash, Seite, Code und Operation bei
Bildabstand unter 0.01 aktualisiert (`aktualisiert`), auch bei leicht verschobenen
Erkennungen oder geaenderter Laufkennung. Andernfalls entsteht ein Vorschlag (`neu`).
Andere Eingaben behalten den exakten Vergleich (`unveraendert_offen`); explizite
Aenderungen ergeben `aktualisiert`. Die Antwort von `anlegen` zaehlt diese Ergebnisse
in `zusammenfassung`. Der Abgleich gilt innerhalb des eigenen Objekts und der Organisation.
Das Fehlen eines Symbols im neuen Plan ist kein Grund fuer `entfernen`.

## Status und Warnungen

`position_status` beschreibt die Position, unabhängig von `freigabe_status`:

| Position | Bedeutung |
|---|---|
| `ungeprueft` | Legacy-Bestand ohne Positionsprüfung |
| `vorgeschlagen` | Position vorhanden, menschliche Freigabe steht aus |
| `pruefung_erforderlich` | Keine belastbare Position oder unbrauchbare/ungenaue Referenz |
| `bestaetigt` | Menschlich freigegebener produktiver Marker |
| `veraltet` | Quellenstand hat sich geändert; erneute Prüfung erforderlich |

Vorschläge beginnen mit `freigabe_status=offen`. Freigabe setzt `angenommen`,
Verwerfen/Ablehnen setzt `abgelehnt`; konkurrierende offene Änderungen desselben
Markers werden nach Übernahme `ersetzt`. Georeferenzen verwenden `gueltig`,
`abgelehnt` und `veraltet`; auch Altstände `entwurf`/`ungeprueft` sind nicht nutzbar.

Vollständige Warnungscodes aus Vorschlags-, Georeferenz- und Freigabeservice
(inklusive der vom Review unterstützten Altstände):

| Code | Bedeutung / Konsequenz |
|---|---|
| `weit_vom_objekt` | Position mehr als 2.000 m von der Objektkoordinate entfernt |
| `moegliche_dublette` | Gleicher Symbolcode und gleiches nichtleeres Label oder Abstand unter 3 m zu Marker/Vorschlag; Referenz-ID beachten |
| `einsatzkritisch_freigabe_erforderlich` | Einsatzkritisches Ziel- oder Basissymbol ausdrücklich prüfen und bestätigen |
| `position_manuell` | Position wurde im Review manuell korrigiert; ausdrücklich bestätigen |
| `richtung_ohne_georef` | Bildrichtung gespeichert, aber ohne gueltige Georef keine belastbare Kartenrotation |
| `georef_ungenau` | Berechnete Positionsunsicherheit über 4 m; Position muss geprüft werden |
| `georef_ungueltig` | Georeferenz nicht nutzbar; alte Position wird gegebenenfalls nur als `altposition` ausgegeben |
| `georef_veraltet` | Dokument, Rendering oder Rotation passen nicht mehr zur Referenz |
| `georef_abgelehnt` | Referenzberechnung abgelehnt oder Referenz verworfen |
| `georef_entwurf` | Referenz noch Entwurf, keine nutzbare Position |
| `georef_ungeprueft` | Referenz ungeprüft, keine nutzbare Position |

Alle vorhandenen Warnungen müssen je Vorschlag bestätigt sein. Bestätigen hebt
Positions- oder Quellenfehler nicht auf. Fehlende Position ist ein Status/Fehler,
kein zusätzlicher Warnungscode.

## Arbeitskopie und neue Quellenstände

Eine offene Arbeitskopie sperrt jede Freigabe am Basisobjekt (409), weil ihre
spätere Übernahme sonst Marker überschreiben könnte. Arbeitskopie zuerst übernehmen
oder verwerfen, dann Vorschläge und Revisionen neu lesen und prüfen.

Dateihash-Wechsel oder eine neue aktuelle Version derselben Dokumentgruppe machen
quellengebundene Positionen veraltet. Vorschläge und Marker werden dabei nicht
gelöscht. Reine MCP-Leseaufrufe schreiben den Status nicht zurück;
`objekt_karte_vorschlaege(aktion="auflisten", veraltete_markieren=true)` persistiert
die Alterung explizit. Die Review-UI aktualisiert diesen Stand beim Laden.
Legacy-Marker ohne Quellenhash lassen sich so nicht automatisch altern.

Seitenrotation oder geänderte Renderinggröße entwertet die Georeferenz und die
alten Bildpositionen offener Vorschläge. Referenzen **und Bildpositionen** auf der
neu angezeigten Seite erfassen. Bereits freigegebene Geokoordinaten werden durch
Rotation allein nicht verschoben. Eine ungültige Georeferenz liefert keine neue
freigebbare Position; intern erhaltene Altwerte dienen der Nachprüfung.

## Review-UI

Den `ui_link` öffnen und „KI-Vorschläge prüfen“ aktivieren. Filter: Alle, Neu,
Änderungen, Unklar, Dubletten, Veraltet. Violett gestrichelte Symbole sind Vorschläge,
Kreise zeigen die Genauigkeit, transparente Symbole die Altposition; Kandidaten
ohne Position stehen nur in der Liste. Eintrag oder Symbol auswählen, Quelle,
Begründung, Konfidenz, Restfehler und Bestandsmarker vergleichen.

Über „Georeferenz“ Plan und Karte nebeneinander öffnen, Referenzpunkte zuordnen und
Restfehler prüfen. Punktsymbole per Verschieben oder Koordinateneingabe korrigieren,
Drehung/Label bearbeiten und „Korrektur speichern“. Diese manuelle Prüfung löst die
Georeferenzbindung des Vorschlags und erzeugt `position_manuell`. Linien und Flächen
können hier geprüft, freigegeben oder mit Pflichtgrund abgelehnt werden;
Positionskorrektur ist nur für Punktsymbole vorgesehen.

Einzeln „Freigeben“ oder bis zu 30 Vorschläge auswählen und „Auswahl freigeben“.
Im Dialog jede Warnung je Vorschlag ausdrücklich bestätigen. Die gesamte Auswahl
wird atomar übernommen oder vollständig abgewiesen. Nach Erfolg erscheinen Marker
in der produktiven Karte und im Objektblatt/PDF; das Änderungsaudit hält die
Übernahme mit Nutzer und Quelle fest.

## Fehlerbilder und Grenzen

- **Keine/zu wenige Referenzen:** abgelehnte Georeferenz mit `ablehngrund`;
  weitere eindeutig identifizierte, gut verteilte Punkte beschaffen. Keine Position raten.
- **Restfehler zu groß:** Zuordnungen, Seitenrotation und Verteilung prüfen.
  Grenzen: RMSE höchstens **2 m**, maximaler Einzelfehler **4 m**. Eine normierte
  Unsicherheit von 0,005 wird zusätzlich in die Positionsgenauigkeit eingerechnet;
  über **4 m** Positionsunsicherheit ist Prüfung erforderlich.
- **Schlechter Fit trotz vieler Punkte:** Spiegelung, kollineare Punkte, zu kleine
  Hüllfläche (unter 0,02), Verzerrung über Faktor 10 oder Planausdehnung außerhalb
  1..10.000 m werden abgelehnt. Nicht durch komplexeres Modell erzwingen.
- **OSM nicht erreichbar/deaktiviert:** keine Ersatzkoordinaten erfinden.
  Orthofoto oder dokumentierte Koordinaten nutzen oder unverortet belassen.
- **409 bei Freigabe:** Arbeitskopie, geänderte Markerrevision, mehrere ausgewählte
  Änderungen desselben Markers oder bereits abgeschlossener Vorschlag. Neu laden,
  abgleichen, Auswahl neu bilden. Es wurde nichts aus der Auswahl übernommen.
- **422 bei Freigabe:** Position/Quelle ungültig oder Warnung nicht bestätigt.
  Details stehen pro ID in `ergebnisse`; pauschales Bestätigen repariert keine Quelle.
- **Wiederholung:** Planvorschlaege werden ueber den Toleranzabgleich oben wiedererkannt; ein bereits
  angenommener Vorschlag wird beim zweiten Freigeben mit 409 abgewiesen.

Die tatsächliche Genauigkeit bleibt durch die Referenzquelle begrenzt. Kleine
Restfehler belegen nur die gegenseitige Konsistenz der Punkte: Gemeinsam falsch
zugeordnete, gedrehte oder skalierte Referenzen können trotzdem plausibel wirken.
PDF-Statikkarten stellen Punktsymbole vereinfacht als farbige Kreise dar.

## Beispiel-Prompt

> Wähle „Rattpack Werk 2“ eindeutig aus und bereite die lagegenaue Symbolpflege vor.
> Lies Karte und Symbolkatalog, sichte die aktuellen Planseiten und verwende nur
> eindeutig im Plan identifizierte OSM-Ecken, Orthofoto-Klicks oder dokumentierte
> Koordinaten als Referenz. Prüfe die Georeferenz zuerst im Trockenlauf. Erfasse
> Symbole als normierte Bildpositionen; lass unklare Standorte ohne Koordinaten.
> Prüfe und erstelle Vorschläge zuerst im Trockenlauf, danach als offene Vorschläge.
> Gib mir den Review-Link mit IDs, Quellen und Unsicherheiten. FSD/BMZ und andere
> einsatzkritische Symbole brauchen meine ausdrückliche Einzelbestätigung der
> Spezialwarnung. Gib erst frei, wenn ich die konkrete Auswahl und Warnungen
> ausdrücklich bestätigt habe.

Prüfbare Flussabnahme: `tests/test_objekt_karte_e2e.py`; Details zu Transformation,
Freigabe, UI und Rechteprüfung in den benachbarten Georef-/Karten-/MCP-Tests.

## Grafischer Georeferenzierungseditor

Für Pläne ohne belastbare Referenz gibt es eine eigenständige Seite
`/objekte/{id}/dokumente/seite/{seite_id}/georef` (Rolle `objekt_verwalter`).
Links liegt der Plan, rechts das Orthofoto. Der Mensch klickt dieselben vier
Gebäudeecken auf beiden Karten an („Ecke n von 4“; Esc/Rücktaste macht den letzten
Klick rückgängig). Der Server berechnet Transformation, Restfehler und Hinweise
(`wenige_punkte`, `punkte_eng`, `restfehler_hoch`, `nur_freie_klicks`); bei gültigem
Ergebnis liegt der Plan halbtransparent über der Karte (Deckkraftregler) und wird erst
nach Bestätigung gespeichert.

`objekt_karte_referenzpunkte` liefert dazu `editor_link` und `hinweise`. Reicht die
KI-Georeferenz nicht aus, gibt sie den Link an den Menschen weiter, statt Punkte zu raten.
Die Objektliste zeigt pro Planseite den Georef-Status (Badge) und den Button
„Georeferenzieren“.
