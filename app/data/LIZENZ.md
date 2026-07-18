# Gefahrgut-Daten (`bam_gefahrgut.csv`)

Diese Datei liefert die Offline-Anreicherung von Objekt-Gefahren per UN-Nummer
(Stoffname, Gefahrklasse, Klassifizierungscode, Gefahrnummer/Kemler, Verpackungsgruppe).

## Herkunft / Lizenz

Die vollständige Datengrundlage stammt aus der **„Datenbank GEFAHRGUT" der BAM**
(Bundesanstalt für Materialforschung und -prüfung), veröffentlicht als offene Daten
unter der **Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2.0)**
(https://www.govdata.de/dl-de/by-2-0). Bei Nutzung ist die Quelle zu nennen:
**„Datenbank GEFAHRGUT, BAM"**.

## Hinweis

Die hier mitgelieferte CSV ist der **vollständige ADR-Stoffbestand** (2347 UN-Nummern),
aufbereitet aus dem **Gefahrgutdatenservice der BAM** (ADR im UN-Nummern-System,
Datei `ADR25_csv.txt`, Stand 2026-07). Damit läuft die Offline-Suche sofort ohne Sync.
Je UN-Nummer wird die erste Spezifikation übernommen; Stoffnamen sind die amtlichen
ADR-Benennungen (Großschreibung wie auf der Kennzeichnung), ggf. um die Spezifikation
ergänzt (z. B. „AMMONIUMPIKRAT, trocken").

Der Parser (`app/services/gefahrgut_service.py`) ordnet die Spalten tolerant über die
Kopfzeile zu (UN-Nummer, Benennung/Stoffname, Klasse, Klassifizierungscode,
Gefahrnummer/Kemler, Verpackungsgruppe) und erkennt sowohl dieses kompakte `;`-Seed-Format
als auch das rohe BAM-Datenservice-Format (TAB-getrennt, Spalten `S_UNNR`/`S_NAME`/… ,
auch als ZIP mit `ADR25_csv.txt`), das der tägliche Sync direkt verarbeitet.

Bezug der Rohdaten: <https://tes.bam.de/datenbank-gefahrgut/produkte/gefahrgutdatenservice>
(ADR, UN-Nummern-System, ZIP). Kostenlos seit 23.07.2025, dl-de/by-2.0.

**Vorrang der gesyncten Datei:** Liegt unter `NACHSCHLAGEWERK_DATA_DIR/bam_gefahrgut.csv`
(Standard `app_storage/nachschlagewerk/`) die vollständige, per täglichem Sync bezogene
BAM/ADR-Datei, wird diese statt des Seeds verwendet (siehe `gefahrgut_service._csv_pfad()`).
Dieser Seed hier wird dabei **nicht** überschrieben und bleibt Offline-Fallback.
