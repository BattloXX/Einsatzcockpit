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
- **Wiederholung:** Identische offene Vorschläge werden wiedererkannt; ein bereits
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
