# ELVis-Anbindung über den Windows-Agent

← [Zurück zur Startseite](Home)

ELVis (LWZ Vorarlberg) kann Einsatzmeldungen über einen lokalen Batch-Aufruf an
Einsatzcockpit (ECP) weitergeben. Das ergänzt die Anbindungen über
[DIBOS](Administration-DIBOS-Elvis-Anbindung) und [LIS](Administration-LIS-Anbindung).
Ein gekoppelter Windows-Agent und eine Agent-Version mit ELVis-Unterstützung sind nötig.

## Funktionsweise und Grenzen

Der Weg ist **ELVis → `ecp-elvis.cmd` → Inbox des Windows-Agents → ECP**.
Der Agent übernimmt die Einsatzfelder, puffert die Weiterleitung und sendet sie mit
einer stabilen Event-ID über seine Cloud-Verbindung; HTTP dient als Rückfallweg.
ECP ordnet das Event der Organisation des gekoppelten Agents zu. Wiederholungen
werden über Event-ID und Inhalt erkannt. Die Agent-Detailseite zeigt empfangene
Events mit Zeitpunkt, Testkennzeichnung und Verarbeitungsstatus.

**Noch nicht am Echtgerät verifiziert:** Wie ELVis die Parameter an die Batch-Datei
übergibt und wann beziehungsweise wie oft es diese aufruft. Die folgenden Schritte
beschreiben den vorgesehenen Einrichtungsweg; ein erfolgreicher künstlicher Test
ersetzt die Prüfung eines echten ELVis-Aufrufs nicht.

**Offene Annahme:** Das ELVis-Feld `Nr` ist die lesbare Leitstellennummer wie bei
DIBOS/LIS (Buchstabe plus acht Ziffern). Das muss an einem echten Einsatz bestätigt
werden. `ID` wird als interne ELVis-ID behandelt und ersetzt die Leitstellennummer
nicht. Ohne verlässliche gemeinsame Nummer besteht das Risiko doppelter Einsätze,
insbesondere bei parallelem Betrieb mit DIBOS oder LIS. Auch die vorhandene
Dublettenerkennung ersetzt diesen Vergleich nicht.

## 1. Windows-Agent installieren oder aktualisieren

1. Den Windows-Agent nach [Print & Alarm Gateway einrichten](Installation-Print-Alarm-Gateway#windows-druckagent-ohne-docker)
   installieren und mit der richtigen Organisation koppeln. Bei bestehender Kopplung
   den Installer beziehungsweise Updateweg des Agents verwenden und die Kopplung erhalten.
2. Ein Paket mit ELVis-Unterstützung verwenden. Prüfen, dass `ecp-elvis.cmd` und der
   ELVis-Inbox-Eingang vorhanden sind; ein älteres reines Druckagent-Paket genügt nicht.
   In dieser Anleitung ist keine bestimmte Release-Version als am Echtgerät abgenommen bestätigt.
3. Den tatsächlich vom Agent verwendeten Inbox-Ordner in seiner Konfiguration beziehungsweise
   Diagnose ermitteln. Batch-Aufruf und Agent müssen denselben Ordner verwenden.
   Das Windows-Konto von ELVis benötigt Schreibrechte, das Agent-Dienstkonto Lese- und
   Verarbeitungsrechte. Pfade aus der installierten Version übernehmen.
4. Im Installationsordner ein künstliches Testereignis erzeugen:

   ```powershell
   .\EinsatzcockpitPrintAgent.exe elvis-event --test
   ```

5. Auf der Agent-Detailseite in ECP kontrollieren, dass das Ereignis als **Test**
   angekommen ist. Es darf keinen Einsatz und keine Alarmierung auslösen.

## 2. ECP zunächst auf Protokollieren stellen

Unter **Gateway / Druck → gewünschter Windows-Agent → ELVis-Einsatzmeldungen**
den **ELVis-Modus** auswählen und speichern. Die Einstellung gilt je Agent.

| Modus | Wirkung |
|---|---|
| `aus` | Events werden quittiert und verworfen; kein neuer Eintrag in der Eventliste. |
| `protokollieren` | Standard und Empfehlung für die Inbetriebnahme: Events speichern, keine Einsätze oder Alarmierungen auslösen. |
| `alarmquelle` | Echte Events können Einsätze anlegen/ergänzen und den bestehenden Alarm-/Drucknachlauf auslösen. Erkannte Test-/Dummy-Aufrufe bleiben reine Protokolleinträge. |

Testmarker und F30-Proberufe werden auch im Modus `alarmquelle` nur protokolliert.
Ein Testereignis ist daher kein Nachweis für eine produktive Alarmierung.

## 3. Batch-Aufruf in ELVis einrichten

1. In ELVis den **Einsatz-Tab** öffnen und dort die Einstellung **Batch-Datei** aufsuchen.
2. Die mit dem Agent installierte `ecp-elvis.cmd` als Batch-Datei auswählen.
   Den vollständigen Pfad der eigenen Installation verwenden, bei Leerzeichen die
   Pfadbehandlung im ELVis-Dialog prüfen.
3. **Batch-Datei Test** ausführen.
4. Im Inbox-Ordner beziehungsweise in den Agent-Logs prüfen, ob der Aufruf übernommen
   wurde. Anschließend auf der ECP-Agent-Detailseite den Eingang und die Kennzeichnung
   **Test** kontrollieren. Ist der Aufruf nicht als Test erkennbar, im Modus
   `protokollieren` bleiben und die übergebenen Felder prüfen.
5. Parameterübergabe und mögliche Mehrfachaufrufe mit der tatsächlich eingesetzten
   ELVis-Version vor Ort prüfen. Keine unbestätigte Parameterliste ergänzen.

## 4. Echten Einsatz vergleichen und Alarmquelle freigeben

Zunächst bei echten Einsätzen im Modus `protokollieren` die empfangenen Felder mit
ELVis und dem vorhandenen DIBOS-/LIS-Einsatz vergleichen: insbesondere `Nr`,
Einsatzzeit, Stichwort, Meldebild und Adresse. Bestätigen, dass `Nr` tatsächlich
zur gemeinsamen Leitstellennummer passt. Auch Folgenachrichten und Mehrfachaufrufe
beobachten.

Erst nach dieser Prüfung bewusst auf `alarmquelle` wechseln. Parallel aktive
Alarmquellen können bei abweichenden oder fehlenden Nummern **Doppel-Einsätze und
zusätzliche Alarmierungen** verursachen. Die ersten echten Eingänge begleiten und
bei ungeklärten Abweichungen auf `protokollieren` zurückstellen.

## Dienstüberwachung

Die [Dienstüberwachung](Administration-Dienstueberwachung) zeigt
**ELVis-Weiterleitung** für gekoppelte Windows-Agents mit Modus ungleich `aus`:
Modus und letzten Cloud-Empfang in der lokalen Organisationszeit, einschließlich
**Test/echt**. Ohne Event ist der Zustand `unknown`, nach einem Event `ok`.
Das Alter eines Events löst **keinen Ausfallalarm** aus: Einsätze sind selten,
Stille ist kein Fehlernachweis. `ok` bestätigt einen gespeicherten Empfang und
keine aktuell geprüfte Verbindung. Der Dienstschalter kann diese Anzeige ausnehmen;
er ändert den ELVis-Modus nicht. Die Erreichbarkeit des Agents wird separat geprüft.

## Fehlersuche

| Beobachtung | Prüfung |
|---|---|
| Kein Event nach Batch-Test | Stimmt der Pfad zu `ecp-elvis.cmd`? Batch-Aufruf im ELVis-Einsatz-Tab, Parameterübergabe, Inbox-Pfad und Schreibrechte des ELVis-Kontos prüfen. Den direkten Agent-Test zum Eingrenzen verwenden. |
| Dateien bleiben in der Inbox | Läuft der Agent-Dienst? Stimmen Inbox-Ordner und Rechte des Dienstkontos? Agent-Logs auf Verarbeitungs- oder Übertragungsfehler prüfen. |
| Tray gelb | Statusdetails öffnen und konkrete Warnung prüfen; Cloud-Verbindung, ausgehendes HTTPS/WSS, ausstehende Weiterleitung und Agent-Logs kontrollieren. Gelb allein beweist keinen ELVis-Fehler. |
| Dateien im `rejected`-Ordner | Abgelehnte Dateien und zugehörige Logmeldung prüfen (z. B. ungültige Felder/Format). Ursache beheben; nicht ungeprüft wiederholt einspeisen. |
| Agent sendet, ECP zeigt nichts | Richtige Organisation und gekoppelten Agent prüfen; bei Modus `aus` wird nichts gespeichert. Agent-Logs und serverseitige Gateway-/ELVis-Logs zeitlich vergleichen. |
| Event sichtbar, kein Einsatz | Modus und Testkennzeichnung sowie Verarbeitungsstatus prüfen. `protokollieren` und erkannte Tests erzeugen absichtlich keinen Einsatz. |
| Doppel-Einsätze | Leitstellennummer `Nr`, parallele DIBOS-/LIS-Quellen und Mehrfachaufrufe vergleichen; bis zur Klärung auf `protokollieren` zurückstellen. |

Die konkreten Inbox-, `rejected`- und Logpfade hängen von Agent-Version und
Installation ab; sie aus der lokalen Konfiguration/Diagnose ablesen.
