# Katastrophenplan-Modul – Pilot (K13)

Stand: 2026-10-20. Lokaler Abnahmelauf auf einer SQLite-Demo mit dem echten
Pilot-PDF „HOCHWASSER – Maßnahmenplan“ der Marktgemeinde Wolfurt (21 Seiten mit Textebene).
Das PDF liegt bewusst nicht im Repository, weil es Telefonnummern und Namen enthält.

## (a) Blackout-Plan vollständig nativ (Akzeptanz A)

Plan „Blackout – großflächiger Stromausfall“, ohne Anhang, im Editor erfasst:

- 3 Kapitel (Lage und Alarmierung, Maßnahmen Feuerwehrhaus, Versorgung und Ressourcen),
  4 Phasen (Vorwarnung, Ausfall bis 8 h, Ausfall über 8 h, Wiederherstellung).
- 6 Elemente in 6 Typen: Ablauf mit Wenn/Dann-Bedingungen, Verständigung mit Kontaktlink,
  Maßnahme mit Schrittliste und Priorität 1, Kontrollstelle mit geprüfter Verortung,
  Materialbedarf (Diesel, Kanister), Sicherheitshinweis.
- Erfassung bis zur Freigabe im Skript etwa 30 Sekunden, keine offenen Qualitätshinweise.

Ergebnis Leser (readonly, Smartphone 390 px):

- Alle Inhalte sichtbar, kein horizontales Scrollen.
- Die Telefonnummer des nicht einsatzrelevanten Kontakts ist korrekt ausgeblendet.
- Die Karte zeigt die Kontrollstelle, Suche und PDF-Offlinefassung funktionieren.

**Akzeptanz A erfüllt.**

## (b) Import „HOCHWASSER – Maßnahmenplan“

Ablauf:

1. Plan anlegen und PDF als Planquelle hochladen. Verarbeitung etwa 8 s, 21 Seiten, Volltext aus der Textebene.
2. Ein Assistent liest die Seiten (`katplan_dokument_auszug`) und reicht über
   `katplan_vorschlaege_aus_dokument` 19 strukturierte Vorschläge ein:
   - Seite 2: Anwendungsbereich
   - Seiten 3, 18, 19: Maßnahmen und Alarmierung
   - Seiten 5–16: 12 Kontrollstellen
   - Seite 17: zweitrangige Stellen
   - Seite 20: Materialbedarf, als Freitext bzw. Bedarfsliste ohne Mengen
3. Review: 4 Vorschläge mit Kontaktabgleich (Bauhof, VKW) einzeln mit Verknüpfung übernommen,
   15 per Sammelübernahme. Kapitel und Phasen werden per Name zusammengeführt. Review etwa 9 s im Skript.
4. Freigabe. Ergebnis für Leser:
   - 12 Kontrollstellen, 19 Quellenangaben mit Seitenlink, Herkunft „KI-Vorschlag“
   - Suche findet „Gasleitung“ in Elementen und Quellseiten
   - PDF-Offlinefassung vorhanden
   - MCP-Lesetools liefern Quellseite, Typdaten und Warnungen

Telefonnummern aus dem Dokument wurden nicht übernommen. Verknüpft wurden nur Kontakte,
die im Kontaktverzeichnis eindeutig gefunden und per Häkchen bestätigt wurden.

## Im Pilot gefundene und behobene Punkte

| Befund | Behebung |
|---|---|
| Phasen- und Kapitelnamen mit Umlauten („Stufe 2 – Übergehen“) wurden bei der Übernahme mehrfach angelegt, weil SQL-`LOWER()` unter SQLite keine Umlaute faltet. Der Objektabgleich hatte dasselbe Muster. | Vergleich in Python mit `casefold()`, Regressionstest |
| KI- und MCP-Vorschläge konnten keine typspezifischen Angaben liefern (Kontrollhinweise, Gefahren, Bedarf, Schritte). | Optionales Feld `daten` (über `bereinige_daten`), im Prompt und in der Tool-Beschreibung dokumentiert, in der Review-Karte angezeigt |
| Beschreibung und Schrittliste doppelten sich bei Maßnahmen. | Prompt-Hinweis: Inhalte aus `daten` nicht in der Beschreibung wiederholen |
| Das Formular „Neue Phase“ stand unter allen bestehenden Phasen und hatte die Reihenfolge 0. Wer weitertippt, benennt leicht eine bestehende Phase um. | Formular „Neue Phase“ steht zuerst, Reihenfolge wird mit dem nächsten freien Wert vorbelegt |

## Offene Punkte für den Echtbetrieb

1. **Verortung:** Die 12 Kontrollstellen haben nur Straßenangaben. Die Karte im PDF (Seite 4)
   enthält nur nummerierte Punkte ohne Koordinaten. Die Orte sind im Editor zu setzen
   (Adresssuche oder Marker) und vor Ort zu bestätigen. Bis dahin meldet die Qualitätsanzeige
   „12 ohne Geometrie“.
2. **KI-Hinweis nach Freigabe:** Das Badge „KI-Vorschlag“ bleibt auch in der freigegebenen
   Fassung für Leser sichtbar, bis ein Bearbeiter das Element einmal in der Oberfläche speichert.
   Zu entscheiden ist, ob Leser es nach der Freigabe überhaupt sehen sollen.
3. **Echter KI-Lauf:** Der serverseitige KI-Import (K9) wurde mit simulierten Antworten geprüft.
   Ein Lauf mit echtem Schlüssel überträgt Seitentext und Seitenbilder einschließlich
   Telefonnummern an den KI-Anbieter. Der Org-Schalter bleibt aus, bis das freigegeben ist.
4. **Kontakte:** Baggerunternehmen und Kranfirmen aus der Material- und Telefonliste stehen
   nicht im Kontaktverzeichnis. Sie sind dort anzulegen (Telefonnummern prüfen) und danach
   zu verknüpfen.
5. **Mengen:** Die Mengen der Materialliste wurden bewusst nicht übernommen (Konzept: nie als
   geprüft). Sie sind bei der Bestandsaufnahme im Lager einzutragen.
6. **Kapitelreihenfolge:** Kapitel entstehen in der Reihenfolge der Übernahme. „Allgemeines“
   landete im Pilot am Ende und ist per Drag-and-drop nach vorne zu ziehen.
7. **Produktivsetzung:** Migrationen 0294–0299 einspielen, System- und Org-Schalter aktivieren,
   Rollen `katplan_verwalter` und `katplan_freigeber` vergeben, Leserkreis prüfen.
   Danach diesen Ablauf mit echten Benutzern und Geräten wiederholen.
