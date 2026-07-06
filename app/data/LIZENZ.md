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

Die hier mitgelieferte CSV ist ein **kleiner, redaktioneller Auszug** häufiger ADR-Stoffe,
damit die Funktion sofort läuft. Für den Produktivbetrieb die **vollständige BAM-CSV**
(`BAM-Gefahrgutdaten.csv`, `;`-getrennt) von OffeneDaten.de / BAM beziehen und diese Datei
ersetzen. Der Parser (`app/services/gefahrgut_service.py`) ordnet die Spalten tolerant über
die Kopfzeile zu (UN-Nummer, Benennung/Stoffname, Klasse, Klassifizierungscode,
Gefahrnummer/Kemler, Verpackungsgruppe).
