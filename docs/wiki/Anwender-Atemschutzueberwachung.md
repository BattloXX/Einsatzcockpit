# Atemschutzüberwachung

← [Zurück zur Startseite](Home)

Die Abläufe orientieren sich am FAZ-Falter und am Atemschutz-Lehrbehelf des
LFV Vorarlberg: Kontrollen nach einem und zwei Dritteln der erwarteten Einsatzzeit,
Rückzug nach dem doppelten Anmarschverbrauch und Überwachung des gesamten Trupps.

> Die digitale Überwachung ergänzt Tafel und Formular, sie ersetzt sie nicht.

---

## Ansicht öffnen

Im Einsatz-Board: **AS-Überwachung** in der Kopfleiste, oder direkt `/einsatz/{id}/atemschutz`.
Das Modul muss systemweit und für die Organisation aktiviert sein.
Die Ansicht ist für alle Rollen lesbar. Admin, Einsatzleiter, AS-Überwacher und
Bearbeiter dürfen Änderungen durchführen.

---

## Trupp anlegen

1. **+ Trupp anlegen** klicken und Trupp-Bezeichnung, Auftrag und Standort eintragen.
2. Einheit (Funkrufname) und optional Fahrzeug zuweisen.
3. **Überwacher**, **Funkkanal** und **Angriffsweg** festhalten. Mit
   **Überwachung/Details** lassen sich diese Angaben später ändern; **Überwachung
   übernehmen** ordnet den angemeldeten Benutzer als Überwacher zu.
4. **Flasche (Anzeige)** wählen. Die Flaschengröße bestimmt die Einsatzzeit nicht.
   Es gilt die erwartete Einsatzzeit der Organisation (Vorgabe 30 Minuten).
   Bei **manuell** kann die erwartete Einsatzzeit des Trupps angepasst werden.
5. Mindestens zwei Truppmitglieder (Truppführer und Truppmann) mit Anfangsdruck erfassen.
6. Einen vorgesehenen Sicherheitstrupp entsprechend kennzeichnen.

Die früher genannten 33/37/50 Minuten aus der Flaschengröße gelten nicht mehr.
Der Picker bietet 10 bis 300 bar in 10er-Schritten, einschließlich 200 bar.
Die Serverprüfung erlaubt Druckwerte von 0 bis 330 bar. Ein pauschaler Ausschluss
von 200-bar-Flaschen ist daher falsch; die Flaschen-Auswahl dient der Anzeige.

Eine Doppelbelegung derselben Person in einem anderen eingesetzten oder
im Rückzug befindlichen Trupp desselben Einsatzes wird beim Einsetzen gemeldet. Vor dem Fortfahren muss die Belegung
geklärt werden; eine bewusste Übersteuerung verlangt eine Begründung.

---

## Einsatz starten und Druck überwachen

Mit **Einsetzen** beginnen Stoppuhr und Überwachung. Bei Druckabfragen zählt der
**niedrigste Druck des Trupps**. Individuelle Druckwerte können über **Druck**
eingetragen werden und erscheinen mit Zeit und Mitglied in der Chronik.

Bis **Einsatzziel erreicht** gilt die konfigurierte Rückzug-Untergrenze
(Vorgabe: Warneinrichtung bei 60 bar). Am Einsatzziel den Druck jedes Mitglieds
melden. Anschließend berechnet sich dessen Rückzugsdruck so:

```
Anmarschverbrauch = Anfangsdruck − Druck am Einsatzziel
Rückzugsdruck = max(konfigurierte Untergrenze, 2 × Anmarschverbrauch)
Beispiel: 300 bar am Start, 270 bar am Ziel → Rückzug bei 60 bar
Beispiel: 270 bar am Start, 180 bar am Ziel → Rückzug bei 180 bar
```

Die Warneinrichtung (Vorgabe 60 bar) bildet die Mindestgrenze; ihr erstes Signal
bedeutet Rückzug des gesamten Trupps. Der ungünstigste Einzelstatus bestimmt die
Truppwarnung und zeigt die betroffene Person an.

---

## Kontrolle statt Quittieren

Bei **1/3 und 2/3 der erwarteten Einsatzzeit** wird eine Kontrolle fällig,
bei 30 Minuten also nach 10 und 20 Minuten. Über Funk den niedrigsten Druck des
Trupps abfragen und mit **Druck abgefragt** dokumentieren. Eine Kontrolle vor der
jeweiligen Fälligkeit erledigt die spätere Kontrolle nicht.

Besteht kein Funkkontakt, **Kein Funkkontakt** wählen. Einfaches Quittieren,
ein individueller Druckeintrag oder eine Freitext-Lagemeldung ersetzt die fällige
Kontrolle nicht. **Kontrolle (frei)** dokumentiert zusätzliche Abfragen.

Rückzugsdruck und überschrittene Max-Einsatzzeit erscheinen rot. Das Quittieren
einer solchen Warnanzeige dokumentiert eine Bestätigung und ersetzt keine
Rückzugsmaßnahme. Standort, Auftrag und Freitext-Lagemeldung lassen sich separat
aktualisieren; die letzte Meldung erscheint mit Uhrzeit auf der Karte.

---

## Funkverlust und Notfall

**Kein Funkkontakt** bleibt als gelbes Banner sichtbar, bis **Funkkontakt wieder da**
gemeldet wird. Die aufklappbare Hilfe enthält die Schritte nach FAZ-Falter:

- Gruppenkommandant des Trupps verständigen.
- Der Trupp im Innenangriff muss den Rückzug antreten.
- Rettungstrupp bzw. Sicherheitstrupp einsetzen (Entscheidung GRKDT).
- Überwacher und Einsatzleitung informieren.

Die Checkboxen sind eine lokale Merkhilfe und werden nicht gespeichert.
**Zum Sicherheitstrupp** springt zum ersten bereiten Sicherheitstrupp, sofern vorhanden.

**Atemschutznotfall** muss bestätigt werden. Die Karte und der kritische Hinweis
werden rot hervorgehoben. Notfall und Funkverlust lösen sofort Benachrichtigungen
aus. Das Aufheben eines Notfalls ist Einsatzleiter, AS-Überwacher und Admin vorbehalten.

---

## Einsatzhinweise und Übersicht

Über den Karten stehen **Kritische Hinweise**, solange ihre Bedingung zutrifft.
Sie sind nicht schließbar und verschwinden nach Behebung automatisch:

- Ab drei gleichzeitig eingesetzten Trupps (einschließlich Rückzug):
  **Atemschutzsammelplatz (ASSP)** über die Einsatzleitung alarmieren.
  Der ASSP übernimmt nur Logistik; die Überwachung bleibt beim Gruppenkommandanten.
- Ohne bereiten Sicherheitstrupp bei eingesetztem Angriffstrupp:
  **Rettungstrupp ab Einsatzbeginn bereitstellen**. Nur der Status **bereit** zählt.
- Notfall und Funkverlust je Trupp, mit Sprunglink zur betroffenen Karte.

Die Übersichtszeile zählt eingesetzte Trupps einschließlich Rückzug, bereite
Trupps samt Sicherheitstrupps sowie zurückgekehrte Trupps in Erholung.

---

## Benachrichtigungen und Eskalationsstufen

Warnungen und Zustellungen werden serverseitig gespeichert. Der Versand läuft
auch ohne geöffnetes Board weiter und setzt nach einem Watchdog-/Worker-Neustart
auf dem gespeicherten Stand auf. Fristen zählen ab Auslösung der Warnung.

**Überwacher** ist der dem Trupp zugeordnete Benutzer, ersatzweise der Einsatzleiter.
**Leitung** umfasst die konfigurierten aktiven Eskalationsempfänger der primären
Einsatzorganisation und den Einsatzleiter. Ist niemand zugeordnet, werden aktive
AS-Überwacher dieser Organisation verwendet.

| Ereignis | Stufe 0 | Stufe 1 | Stufe 2 |
|---|---|---|---|
| Kontrolle 1/3 oder 2/3 offen | Nach 60 s Push an Überwacher | Nach 180 s Push an Leitung | Nach 300 s SMS an Leitung |
| Rückzugsdruck erreicht | Sofort Push an Überwacher | Nach 60 s Push und SMS an Leitung | – |
| Max-Einsatzzeit überschritten | Sofort Push an Überwacher | Nach konfigurierter Eskalationsfrist (Vorgabe 3 min) Push und SMS an Leitung | – |
| Kein Funkkontakt | Sofort Push an Überwacher sowie Push und SMS an Leitung | – | – |
| Atemschutznotfall | Sofort Push und SMS an Überwacher und Leitung | – | – |
| Kein bereiter Sicherheitstrupp / ASSP | Sofort Push an Leitung | – | – |

SMS setzt den SMS-Schalter, einen eingerichteten Versand und eine Telefonnummer
voraus. Bei Übungen werden SMS unterdrückt. Erledigte Warnungen stoppen noch
wartende Zustellungen. Unter **Benachrichtigungen** auf der Truppkarte stehen für
offene Warnungen Art, Auslösezeit, Stufe, Kanal, Empfänger, Zustellstatus und Sendezeit.
Unterdrückte Zustellungen zeigen den Grund, fehlgeschlagene den Versandfehler.

Push-Alarm und SMS-Eskalation sind Phase 2. Die **Android-App-Pflicht** mit
Überwachungssitzung folgt in Phase 3; der native, abgesicherte **Alarmton** mit
lokaler Überwachung folgt in Phase 4. Die vorhandene Browser-Akustik hängt von
Autoplay-Freigabe und offenem Board ab.

---

## Rückkehr und Erholung

| Von | Nach | Bedeutung |
|---|---|---|
| Bereit | Im Einsatz | Trupp betritt den Gefahrenbereich |
| Im Einsatz | Rückzug | Rückzugsdruck erreicht oder Rückruf |
| Rückzug | Zurück | Trupp hat Gefahrenbereich verlassen |
| Zurück | Erholt | Erholung abgeschlossen |

Nach der Rückkehr **Enddruck** je Mitglied eintragen. Ab der Rückkehr läuft die
Erholzeit (Vorgabe 30 Minuten); die Karte zeigt die verbleibende Zeit.
Beim erneuten Einsatz vor Ablauf erscheint eine Warnung; eine Übersteuerung
verlangt eine Begründung. **Erneut einsetzen** archiviert den bisherigen Zyklus
und übernimmt die neue Besetzung für den nächsten Einsatz.

---

## PDF-Export

**PDF** auf der Truppkarte öffnet das Einzelprotokoll mit Stammdaten, Mitgliedern,
Druckwerten, Zeitachse und Meldungs-Chronik. Auch im Archiv ist ein PDF-Link je
Trupp verfügbar. Der Abschlussbericht enthält die Atemschutz-Protokolle aller
Trupps. Einstellungen zu Fristen und Empfängern:
[Organisations-Einstellungen](Administration-Einstellungen#atemschutzüberwachung).
