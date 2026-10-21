# Atemschutz-Geräteprüfung

← [Zurück zur Startseite](Home)

> URL: `/atemschutz-pruefung` und `/ap/{token}`  
> Zugänglich für: alle angemeldeten Benutzer sowie Erfassung ohne Login via Token/QR-Code

Die Atemschutz-Geräteprüfung erfasst die Prüfung von Pressluftatmern nach einem Einsatz oder einer Übung. Das Prüfprotokoll dokumentiert Gerät, Atemschutzträger, Verwendung, Druckwerte und Einsatzbereitschaft. Bei einem Defekt wird eine Benachrichtigung an die konfigurierten Empfänger ausgelöst.

---

## Erfassungsformular öffnen

Unter **Prüfprotokolle** (`/atemschutz-pruefung`) öffnet **Prüfung erfassen** das Formular. Die Schaltfläche erscheint, sobald ein Organisations-Token hinterlegt ist. Die Einrichtung beschreibt [Atemschutz-Geräteprüfung – Administration](Administration-Atemschutz-Geraetepruefung).

Der Link `/ap/{token}` und der zugehörige QR-Code öffnen dasselbe Formular **ohne Benutzer-Login**, etwa auf einem Tablet im Feuerwehrhaus. Dafür muss das Modul für die Organisation aktiviert sein. Der Token bestimmt die Organisation und damit die angebotenen Geräte und Personen.

Auch angemeldete Benutzer können über diesen Link erfassen. Beim Speichern wird bei angemeldeten Benutzern die eigene Organisation verwendet; der Erfassungsweg wird als **Intern** und der Benutzer als Erfasser gespeichert. Ohne Anmeldung lautet der Erfassungsweg **Öffentlicher Link**.

---

## Erfassungsformular

### Schritt 1 – Stammdaten

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| **Atemschutzgerät** | ✓ | Auswahl der aktiven Geräte der Organisation, sortiert nach Gerätenummer; angezeigt werden Nummer und gegebenenfalls Bezeichnung |
| **Atemschutzträger** | ✓ | Personenauswahl mit aktiven Mitgliedern der Organisation mit der Qualifikation `AGT` |
| **Name des Atemschutzträgers** | alternativ | Über **Person nicht in der Liste – Namen eintippen** einen Namen als Freitext erfassen; entweder Person auswählen oder Namen eintragen |
| **Datum** | ✓ | Datum der Verwendung; mit dem aktuellen Datum vorbelegt |
| **Wo wurde das Gerät eingesetzt?** | | Freitext zum Ort |
| **Übung oder Einsatz?** | ✓ | Auswahl **Übung** oder **Einsatz**; Übung ist vorausgewählt |
| **Einsatz** | bei Einsatz | Zuordnung zu einem Einsatz oder Auswahl **Auf keinem Einsatz zuordenbar** |

### Einsatzzuordnung

Bei Auswahl von **Einsatz** wird die Einsatzliste nachgeladen. Sie enthält bis zu 20 Einsätze der eigenen Organisation aus den letzten drei Tagen, neueste zuerst. Gibt es in diesem Zeitraum keinen Einsatz, wird der letzte vorhandene Einsatz angeboten. Der erste angebotene Einsatz ist vorausgewählt.

Die Einträge zeigen Alarmart, gegebenenfalls Adresse, Beginn und LSt-Nummer. Ist keine Zuordnung möglich, **Auf keinem Einsatz zuordenbar** wählen. Ohne Einsatzwahl oder diese ausdrückliche Auswahl kann eine Prüfung mit Anlass Einsatz nicht gespeichert werden.

Bei einer Zuordnung wird die Prüfung mit dem Einsatz verknüpft und ihre Erfassung in dessen Änderungsprotokoll festgehalten.

### Schritt 2 – Drucküberprüfung nach dem Gebrauch

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| **Wurde die Flasche gewechselt?** | | Checkbox; anfangs nicht angehakt |
| **Flaschendruck** | ✓ | Druck in bar; vorbelegt mit 300; in Ordnung ab 270 bar |
| **Hochdruck-Dichtprüfung** | ✓ | Druckabfall in bar/min; vorbelegt mit 0; in Ordnung bis einschließlich 10 bar/min |
| **Warnsignal-Prüfung** | ✓ | Druck in bar; vorbelegt mit 55; in Ordnung von 50 bis einschließlich 60 bar |

Die Druckwerte werden über Plus-/Minus-Schaltflächen oder Schnellwerte eingestellt. Der Flaschendruck lässt sich in 10-bar-Schritten zwischen 0 und 350 bar ändern, die Hochdruck-Dichtprüfung in 1-bar/min-Schritten zwischen 0 und 30 bar/min und die Warnsignal-Prüfung in 1-bar-Schritten zwischen 0 und 100 bar. Die Bewertung erscheint automatisch beim jeweiligen Wert.

### Schritt 3 – Status-Checks

| Feld | Pflicht | Beschreibung |
|------|:-------:|-------------|
| **Sichtprüfung** | ✓ | **In Ordnung** oder **Nicht in Ordnung**; mit In Ordnung vorbelegt |
| **Gerät einsatzbereit** | ✓ | **In Ordnung** oder **Nicht in Ordnung**; mit In Ordnung vorbelegt. Hinweis im Formular: in der Halterung verstaut, einschließlich Maske, Begurtung, Lungenautomat und Funk |
| **Zusatzinfo zum Defekt** | bei Defekt | Erscheint, sobald mindestens ein Druckwert außerhalb des Sollbereichs liegt oder ein Status-Check nicht in Ordnung ist; Beschreibung des Defekts ist dann Pflicht |

---

## Erfassung abschließen und Defektmeldung

Das Formular speichern. Fehlende oder ungültige Pflichtangaben werden mit einer Fehlermeldung zurückgewiesen. Ein Protokoll ist nur dann insgesamt **In Ordnung**, wenn alle drei Druckprüfungen und beide Status-Checks in Ordnung sind.

Bei erfolgreicher Speicherung erscheint **Prüfung gespeichert** mit Datum und Gerät. Wurde über einen Token-Link erfasst, führt **Weitere Prüfung erfassen** wieder zum Formular.

Bei mindestens einem fehlerhaften Prüfpunkt:

1. Die **Zusatzinfo zum Defekt** muss ausgefüllt sein.
2. Das Protokoll wird mit den betroffenen Prüfpunkten gespeichert.
3. Im Hintergrund wird eine Meldung per E-Mail und/oder Microsoft Teams an die hinterlegten Empfänger gesendet. Ohne konfigurierte Empfänger erfolgt kein Versand.
4. Die Meldung enthält Gerät, fehlerhafte Prüfpunkte, Atemschutzträger, Datum, Ort, Zusatzinfo und einen Link zum Protokoll.

Ein Fehler bei der Benachrichtigung hebt die gespeicherte Prüfung nicht auf. Die Detailansicht zeigt vorhandene Versandprotokolle; der Defekthinweis auf der Bestätigungsseite allein bestätigt keinen erfolgreichen Versand.

---

## Prüfprotokolle einsehen und filtern

Unter **Prüfprotokolle** (`/atemschutz-pruefung`) sehen angemeldete Benutzer die Prüfungen der eigenen Organisation, nach Verwendungsdatum absteigend und bei gleichem Datum nach neuester Protokoll-ID.

| Filter | Beschreibung |
|--------|-------------|
| **Gerät** | Alle Geräte oder ein bestimmtes Gerät; auch inaktive Geräte stehen im Filter zur Verfügung |
| **Status** | Alle, In Ordnung oder Nicht in Ordnung |
| **Von** | Frühestes Verwendungsdatum, einschließlich |
| **Bis** | Spätestes Verwendungsdatum, einschließlich |

Mit **Filtern** die Auswahl anwenden. Die Liste zeigt Datum, Gerät, Atemschutzträger, Anlass und Status. Bei einem Defekt werden die betroffenen Prüfpunkte angezeigt.

---

## Detailansicht und PDF

**Ansehen** öffnet das Prüfprotokoll unter `/atemschutz-pruefung/{id}`. Dort sind die allgemeinen Angaben, eine vorhandene Einsatzverknüpfung, alle Prüfwerte mit Bewertung, Defektangaben, Erfassungszeitpunkt und Erfassungsweg sichtbar.

Wenn Versandprotokolle vorliegen, zeigt **Wart-Benachrichtigung** Kanal, Empfänger, Status **Gesendet** oder **Fehler** und Zeitpunkt.

**PDF** in der Liste öffnet das einzelne Protokoll als PDF unter `/atemschutz-pruefung/{id}/pdf`. In der Detailansicht steht **PDF drucken** zur Verfügung; bei verbundenem Stationsdrucker erscheint die Auswahl zwischen Station und lokalem Druck.

---

## Mehrere Prüfungen als PDF drucken

1. Gewünschte Prüfungen in der Liste per Checkbox markieren. In der Tabellenansicht kann die Checkbox im Tabellenkopf alle angezeigten Prüfungen markieren.
2. **Auswahl als PDF drucken** wählen. Mindestens ein Protokoll muss ausgewählt sein.
3. Bei verfügbarem Stationsdruck den Druckweg wählen. Der lokale Druck erstellt über `/atemschutz-pruefung/druck-pdf` ein gemeinsames PDF der ausgewählten Protokolle, sortiert nach Verwendungsdatum absteigend.

---

## XLSX-Export

**Excel-Export** lädt unter `/atemschutz-pruefung/export.xlsx` die Datei `Atemschutzgeraetepruefung.xlsx` herunter. Der Export berücksichtigt den gewählten Gerätefilter und den Datumsbereich. Der Statusfilter der Liste wird beim Export nicht berücksichtigt.

Die Tabelle enthält Datum, Gerät, Atemschutzträger, Ort, Anlass, Flaschenwechsel, alle Druckwerte und deren Bewertungen, Sichtprüfung, Einsatzbereitschaft, Zusatzinfo und Erfassungszeitpunkt.

---

## Geräteübersicht

**Geräteübersicht** (`/atemschutz-pruefung/geraete`) zeigt alle Geräte der eigenen Organisation nach Gerätenummer, einschließlich inaktiver Geräte. Angezeigt werden Aktivstatus, Datum der letzten Prüfung und deren Ergebnis beziehungsweise die fehlerhaften Prüfpunkte. Ohne vorhandenes Protokoll erscheint **Keine Prüfung**.

Als letzte Prüfung gilt die mit dem jüngsten Verwendungsdatum; bei gleichem Datum entscheidet die höhere Protokoll-ID. **Ansehen** öffnet dieses Protokoll.
