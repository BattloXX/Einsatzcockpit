# ELVis: Abnahme im Parallelbetrieb (Phase 4)

Ziel: Vor dem Wechsel auf `alarmquelle` bestätigen, dass echte ELVis-Aufrufe zu den
DIBOS-/LIS-Einsätzen passen. Bis zur dokumentierten Entscheidung bleibt der Modus
`protokollieren`; DIBOS/LIS bleiben die bestehenden Alarmquellen.

Diese Liste ist eine Anleitung, kein Nachweis einer bereits erfolgten Abnahme.
Windows-Dienst, ELVis-Konfiguration, Batch-Quoting, lokale Outbox und Transportwechsel
müssen Menschen am tatsächlichen Windows-PC prüfen. Cloud-Tests ersetzen das nicht.
Grundlagen: [Windows-Agent und Cloud-Protokoll](print-agent-architektur.md#elvis-einsatz-events-cloud-phase-1).

## Voraussetzungen

- [ ] Verantwortliche Person, Organisation, Agent-ID, Datum und Agent-Release notiert: __________.
- [ ] Release mit ELVis-Batch-Anbindung, dauerhafter Outbox und HTTP-Fallback installiert;
  konkrete Release-Version und mitgelieferte Batch-Anleitung vor Ort geprüft.
- [ ] Agent gekoppelt und online; Agent-Detailseite zeigt ELVis-Panel und empfangene Events.
- [ ] Modus `protokollieren` gespeichert und Übernahme durch den Agent geprüft.
- [ ] DIBOS/LIS laufen wie bisher; Uhren von Server und Windows-PC synchronisiert.
- [ ] Akzeptable Verzögerung vor dem Test festgelegt: maximal ______ s.

## 1. Batch-Datei-Test (am Windows-PC)

- [ ] Den vom installierten Agent-Release vorgesehenen Batch-Aufruf in ELVis eintragen.
  Keine geratenen Dateipfade oder Parameterreihenfolgen verwenden.
- [ ] Einmal kontrolliert als Test auslösen (expliziter Testmarker oder F30).
- [ ] Auf der Agent-Detailseite erscheint ein Testevent ohne Abgleich und ohne neuen Einsatz/Alarm.
- [ ] Mit ELVis-Rohdaten vergleichen: Nr, Zeit, ESW, Adresse und Kommentar kommen richtig an.

## 2. Parameter-Prüfung (am Windows-PC)

- [ ] Umlaute und ß prüfen, etwa `Müllerstraße` und `Ölspur`.
- [ ] Kommentar mit geraden Anführungszeichen prüfen: `Zugang "Hof" benutzen`.
- [ ] Lange Texte prüfen: Cloud begrenzt Kommentar auf 2000, andere Felder auf 500 Zeichen;
  innerhalb dieser Grenzen kein Verlust durch Batch-Quoting oder Encoding.
- [ ] Leere optionale Felder inmitten der Parameter prüfen: Nachfolgefelder dürfen nicht verrutschen.
- [ ] Leere/0-Nr bleibt Test; unplausible nichtleere Nr wird bei echten Events als unplausibel angezeigt.
  Für diese Prüfungen Testmarker verwenden und im Protokolliermodus bleiben.

## 3. Mehrfachaufrufe pro Einsatz beobachten

- [ ] Gleiche Nr mit mehreren Aufrufen, unverändertem und geändertem Kommentar beobachten.
- [ ] Separate Aufrufe sind eigene Eventzeilen; ein Transport-Retry derselben Event-ID erzeugt keine neue Zeile.
- [ ] Alle echten Aufrufe derselben Nr verweisen nach DIBOS-/LIS-Anlage auf denselben Einsatz;
  keine zusätzlichen Einsätze oder ELVis-Alarmierungen im Protokolliermodus.

## 4. Mindestens fünf echte Einsätze vergleichen

- [ ] Auf Agent-Detailseite die Spalte **Abgleich** und Kurz-Auswertung beobachten (automatisch alle 15 s).
- [ ] Mindestens fünf verschiedene echte Leitstellennummern anhand der DIBOS-/LIS-Originaldaten bestätigen.
  Testevents zählen nicht; Mehrfachaufrufe sind keine zusätzlichen Einsätze.
- [ ] Für jeden Einsatz Nr, Einsatzlink, Quelle und Delta festhalten:

| Nr | Einsatzlink | Quelle / Originaldaten geprüft | Delta (s) | Befund |
| --- | --- | --- | --- | --- |
| | | | | |
| | | | | |
| | | | | |
| | | | | |
| | | | | |

Die Auswertung umfasst nur die letzten 20 Events dieses Agents. Ältere Ergebnisse
rechtzeitig im Abnahmeprotokoll sichern. Matching verwendet ausschließlich Nr im
Parserformat Buchstabe + acht Ziffern, unabhängig von Groß-/Kleinschreibung, gegen
`Incident.lis_operation_number` oder die Leitstellennummer eines anderen, mit einem
Einsatz verbundenen Ingests derselben Organisation. `external_key`, Einsatznummer
und interne ELVis-ID sind keine Ersatzschlüssel.

Delta = Cloud-Empfang (`received_at`) minus tatsächliche Einsatzanlage (`created_at`),
nicht Alarmzeit oder Windows-Aufrufzeit. Negativ bedeutet ELVis früher; positiv später.
Der Median zählt zugeordnete Events, einschließlich Mehrfachaufrufen. Er misst keine
reine Transportlaufzeit und beweist keine Alarmierungsdauer.

Quelle zeigt LIS bei vorhandener LIS-GUID, sonst die andere Ingest-Quelle (W&T und
andere serielle Eingänge als `seriell`). Ohne solchen Nachweis wird das gemeinsame
DIBOS-/LIS-Nummernfeld als **DIBOS*** eingeordnet: eine Annahme, keine belegte
Anlagehistorie. Die Originalquelle vor Ort bestätigen.

- [ ] `nur ELVis` aufklären: DIBOS/LIS kann verspätet eintreffen oder fehlen, oder Nr bedeutet
  etwas anderes als angenommen. Nach erneutem automatischem Abgleich Befund festhalten.
- [ ] Jede unplausible Nr aufklären; keine Zuordnung über Adresse oder Einsatznummer erzwingen.

## 5. Ausfalltest Dienst (am Windows-PC)

- [ ] Windows-Agent-Dienst gezielt stoppen, im Protokolliermodus einen Testaufruf auslösen.
- [ ] Dienst starten: Das während des Stopps ausgelöste Event muss nachgeliefert werden.
- [ ] Aufrufzeit, Neustartzeit, Empfang und Anzahl der Eventzeilen dokumentieren.
  Scheitert schon die lokale dauerhafte Ablage bei gestopptem Dienst, gilt dieser Test als
  fehlgeschlagen; die Cloud kann ein lokal verlorenes Event nicht nachliefern.

## 6. Netzausfall und HTTP-Fallback (am Windows-PC / Netzwerk)

- [ ] Netzverbindung unterbrechen, Testevent auslösen, Netz wiederherstellen:
  Outbox liefert nach, kein Verlust und keine doppelte Eventzeile.
- [ ] Separat nur WebSocket-Verbindung blockieren, HTTPS erreichbar lassen und Test auslösen.
  An Agent-/Transportlogs nachweisen, dass HTTP-Fallback verwendet wurde und Cloud das Event empfängt.
- [ ] Verbindung wieder freigeben; Retry über WebSocket erzeugt kein zweites Event.
  Ein vollständiger Netzausfall allein beweist keinen HTTP-Fallback.

## 7. Entscheidung für `alarmquelle`

- [ ] Nr-Annahme für mindestens fünf echte Einsätze bestätigt; alle Abweichungen geklärt.
- [ ] Kein Doppeleinsatz im Parallelbetrieb; Mehrfachaufrufe korrekt demselben Einsatz zugeordnet.
- [ ] Verzögerung innerhalb des vorher festgelegten Grenzwerts; Ausreißer einzeln geprüft.
- [ ] Dienst-Ausfall, Nachlieferung, Netzausfall und HTTP-Fallback erfolgreich am Windows-PC geprüft.
- [ ] Bewusst festgehalten: Protokolliermodus beweist noch keine fehlerfreie aktive Alarmierung.
  Nach Umschaltung den ersten echten Einsatz auf Zusammenführung und Alarmnachlauf begleiten.
- [ ] Verantwortliche Person bestätigt Freigabe, Zeitpunkt und Beobachtung: __________.
- [ ] Erst jetzt auf der Agent-Detailseite `alarmquelle` speichern und Agent-Konfigurationsübernahme prüfen.

## 8. Rückweg

- [ ] Bei unklarer Nr, Doppeleinsatz, Verlust oder unakzeptabler Verzögerung sofort auf
  `protokollieren` zurückstellen, speichern und Übernahme am Agent prüfen.
- [ ] Bestehenden DIBOS-/LIS-Alarmpfad und dessen Funktion bestätigen; Vorfall dokumentieren.
- [ ] Bereits angelegte Einsätze/versandte Alarmierungen werden durch den Moduswechsel nicht rückgängig gemacht.
- [ ] Falls auch Protokollierung gestoppt werden muss: Modus `aus` verwirft neue Events;
  deshalb erst nach Sicherung der nötigen Befunde wählen.
- [ ] Neue Freigabe erst nach Ursachenklärung und Wiederholung der betroffenen Abnahmeschritte.
