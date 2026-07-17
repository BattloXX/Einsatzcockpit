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

Die hier mitgelieferte CSV ist ein **kleiner, redaktioneller Auszug** häufiger ADR-Stoffe
(**Seed**), damit die Funktion offline sofort läuft. Der Parser
(`app/services/gefahrgut_service.py`) ordnet die Spalten tolerant über die Kopfzeile zu
(UN-Nummer, Benennung/Stoffname, Klasse, Klassifizierungscode, Gefahrnummer/Kemler,
Verpackungsgruppe).

**Vorrang der gesyncten Datei:** Liegt unter `NACHSCHLAGEWERK_DATA_DIR/bam_gefahrgut.csv`
(Standard `app_storage/nachschlagewerk/`) die vollständige, per täglichem Sync bezogene
BAM/ADR-Datei, wird diese statt des Seeds verwendet (siehe `gefahrgut_service._csv_pfad()`).
Dieser Seed hier wird dabei **nicht** überschrieben und bleibt Offline-Fallback.
