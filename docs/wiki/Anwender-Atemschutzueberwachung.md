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

## App-Sitzung und Selbsttest (Phase 3)

Die Einsatzcockpit-App startet beim Öffnen der Überwachung eine an Benutzer,
Einsatz und Gerät gebundene App-Sitzung. Alle 15 Sekunden sendet sie einen
Heartbeat; dieser gilt 60 Sekunden. Ohne frischen Heartbeat ist eine Sitzung
nicht mehr für Eingaben gültig. Sitzungen enden spätestens nach 24 Stunden oder
wenn der Einsatz beendet ist.

Ist **Überwachung nur in der App erlauben** für die Organisation eingeschaltet,
zeigt der Browser eine **Nur-Lese-Ansicht** mit Hinweis zum Öffnen der App.
Druckwerte, Kontrollen und andere Änderungen benötigen dann eine gültige
App-Sitzung. **Notfall melden und Funkverlust melden bleiben auch ohne App
möglich.** Zum Aufheben ist wieder eine gültige App-Sitzung erforderlich.
Ohne diesen optionalen Schalter bleibt die Bedienung im Browser möglich.

Die **Selbsttest-Ampel** zeigt Grün ohne gemeldetes Problem, Gelb bei
Einschränkungen und Rot bei schwerwiegenden Problemen. Sie prüft die verfügbaren
Angaben zu Benachrichtigungsberechtigung, Serververbindung, Uhrabweichung und Akku.
Nicht erlaubte Benachrichtigungen, fehlende Verbindung, mehr als 30 Sekunden
Uhrabweichung oder weniger als 10 % Akku ohne Ladegerät ergeben Rot; noch nicht
erlaubte Benachrichtigungen, mehr als 5 Sekunden Uhrabweichung oder weniger als
20 % Akku ergeben Gelb. Fehlende Geräteangaben sind keine bestätigte Prüfung.
Alarmton-Test, exakte Wecker, Vollbildalarme, Alarmlautstärke und die Ausnahme von
der Akku-Optimierung werden mit dem nativen Wächter in der App geprüft.

Bei App-Pflicht und mindestens einem Trupp im Einsatz oder Rückzug erscheint ohne
frischen Heartbeat **Kein Überwachungsgerät aktiv** unter **Kritische Hinweise**.
Die Leitung erhält sofort einen Push-Alarm und nach weiteren 3 Minuten eine SMS
(nach den eingestellten SMS-Regeln). Es erfolgt keine Zustellung an die Gruppe
Überwacher des ausgefallenen Geräts. Überwachung in der App öffnen oder
Funkkontrolle der Trupps durchführen. Ein neuer Heartbeat beendet die Warnung und
unterdrückt noch wartende Zustellungen.

Der Organisationsschalter ist standardmäßig aus. Er darf erst aktiviert werden,
wenn alle Überwachungsgeräte die aktuelle App mit der Fähigkeit `asuew` haben;
diese kommt mit [Android-PR #96](https://github.com/BattloXX/Einsatzcockpit-Android/pull/96).
Eine vom Betrieb gesetzte Mindestversion (`ASUEW_MIN_APP_VERSION`) kann beim
Sitzungsstart ein App-Update verlangen.

---

## Nativer Wächter in der App

Die App-Funktionen kommen mit [Android-PR #97](https://github.com/BattloXX/Einsatzcockpit-Android/pull/97)
und benötigen ein entsprechendes **App-Release** auf dem Überwachungsgerät.
Nach erfolgreichem Sitzungsstart läuft der Foreground-Dienst
**Atemschutzüberwachung**. Seine Dauerbenachrichtigung zeigt die Trupp-Uhren;
Antippen öffnet das Board. Solange das Board offen ist, bleibt der Bildschirm an,
auch bei aktiviertem Energiesparmodus der App.

Der Wächter gleicht den Zustand regelmäßig mit dem Server ab und überwacht
bekannte lokale Fristen auch ohne Netz. Exakte Wecker lösen Kontrollen,
Max-Einsatzzeit und das Ende einer zurückgestellten Warnung unabhängig von der
Webansicht aus. Der Alarmton läuft auf dem Android-Alarm-Kanal mit Vibration und,
bei erteilter Berechtigung, Vollbildanzeige. Offene Alarme wiederholen sich alle
60 Sekunden, bei Notfall alle 30 Sekunden bis zur Quittierung.
**Quittieren** in der Benachrichtigung bestätigt den Alarm; die erforderliche
Funkkontrolle oder Rückzugsmaßnahme bleibt durchzuführen.

Nach 20 Sekunden ohne erfolgreiche Serververbindung warnt das Gerät mit
Vibration, nach 60 Sekunden mit Alarm. Bei weniger als 20 % Akku erscheint eine
Warnung, unter 10 % eine kritische Akku-Warnung: Ladegerät anschließen oder die
Überwachung übergeben. Der gespeicherte Überwachungszustand und die lokalen
Wecker werden nach Geräteneustart und App-Update wiederhergestellt.
Ohne Netz können nur bereits bekannte Fristen überwacht werden.

Im Kopfzeilenstatus zeigt **Wächter** die native Verbindung und den Akkuzustand.
Der aufklappbare **Geräte-Check** steht auch in der Nur-Lese-Ansicht der App zur
Verfügung. Er zeigt die Ampel und die Probleme des letzten Sitzungsstatus.
Geprüft werden:

- Benachrichtigungen, Online-Verbindung und Uhrabweichung zum Server.
- Bestätigter Alarmton-Test, exakte Wecker und Vollbildalarme.
- Ausnahme von der Akku-Optimierung, Akkustand und Ladezustand.
- Alarmlautstärke: 0 % ergibt Rot, 1 bis 29 % Gelb.

Mit **Alarmton testen** den Ton anhören und **Ton gehört?** nur bei hörbarem Ton
mit **Ja** bestätigen. Danach wird der Sitzungsstatus sofort aktualisiert.
Bei fehlenden Freigaben öffnen **Akku-Optimierung**, **Exakte Wecker**,
**Vollbild-Alarm** und **Benachrichtigungen** die passenden Android-Einstellungen.
Die App von der Akku-Optimierung ausnehmen und exakte Wecker sowie Vollbildalarme
erlauben; anschließend zum Board zurückkehren und den Geräte-Check erneut prüfen.
Die verfügbaren Einstellungen hängen von der Android-Version ab.
Ohne natives Plugin wird der Geräte-Check nicht angezeigt.

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
| Kein Überwachungsgerät aktiv (bei App-Pflicht) | Sofort Push-Alarm an Leitung | Nach 180 s SMS an Leitung | – |

SMS setzt den SMS-Schalter, einen eingerichteten Versand und eine Telefonnummer
voraus. Bei Übungen werden SMS unterdrückt. Erledigte Warnungen stoppen noch
wartende Zustellungen. Unter **Benachrichtigungen** auf der Truppkarte stehen für
offene Warnungen Art, Auslösezeit, Stufe, Kanal, Empfänger, Zustellstatus und Sendezeit.
Unterdrückte Zustellungen zeigen den Grund, fehlgeschlagene den Versandfehler.

Push-Alarm und SMS-Eskalation sind Phase 2. Die optionale **Android-App-Pflicht**
mit Überwachungssitzung und Heartbeat ist ab Phase 3 verfügbar; der native **Alarmton** mit
lokaler Überwachung kommt in Phase 4 mit dem oben genannten App-Release. Die vorhandene Browser-Akustik hängt von
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
