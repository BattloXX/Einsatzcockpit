# Dokumentendruck – Phase 3

Stand: 25.10.2026. Ergänzung zum [zentralen Dokumentenviewer](dokumentviewer-phase2.md).
Keine Produktionsbereitstellung. Phase 4 wurde nicht begonnen.

## Bedienung

Im Viewer öffnet **Drucken** die zentrale Auswahl. Verfügbar sind aktuelle Seite,
gesamtes Dokument, Seitenbereich, freie Seiten und mehrere Dokumente. Eingaben wie
`1`, `3–6`, `2,4,8` und `3–5,9,12–14` verwenden denselben Parser wie der Viewer.
Seiten werden innerhalb eines Dokuments aufsteigend und ohne Duplikate übernommen.
Bei mehreren Dokumenten bestimmen die Pfeile deren Druckreihenfolge; jedes
markierte Dokument hat eine eigene Seiteneingabe. Der Viewer bleibt darunter geöffnet.

**PDF vorbereiten** stellt die Auswahl zusammen und bietet PDF-Öffnung und Download
an. **Auf diesem Gerät drucken** fordert über ein Same-Origin-PDF den Browser- bzw.
Systemdruckdialog an. Drucker, Kopien und gerätespezifische Einstellungen wählt man
dort. Kann ein Browser den PDF-Druck im eingebetteten Fenster nicht auslösen, bleiben
**PDF öffnen** und **PDF herunterladen** als Fallback sichtbar. Ein lokaler
Druckabschluss wird niemals automatisch behauptet.

**Über Stationsdrucker drucken** zeigt bestehende Druckziele mit aktueller
Bereitschaft. Erlaubt sind 1–20 Kopien, Originalformat oder A4/A3 sowie Original-,
Hoch- und Querformat. Offlineziele sind sichtbar; Senden bleibt gesperrt. Eine
erneute serverseitige Prüfung schützt auch vor einem Ausfall nach Öffnen des Dialogs.
Nach dem Senden sind Änderungen und erneutes Senden gesperrt; der Status wird
abgefragt und kann bei Verbindungsproblemen manuell geprüft werden.

## Untersuchte und wiederverwendete Druckarchitektur

Die Bestandsaufnahme umfasst [Druckarchitektur](../konzepte/print-agent-architektur.md),
`Gateway`, `Printer`, `PrinterAgentRoute`, `PrintJob`, Druckregeln, Dispatcher,
signierte Artefakte, WebSocket-/Lease-Übertragung sowie beide vorhandenen Clients:

- Windows-Agent, Stand
  [`df3a820a`](https://github.com/BattloXX/Einsatzcockpit-Windows-Gateway/tree/df3a820a0020a3db55968f399c8feedcba95d801):
  Protokoll `PrintOptions`, `DriverPrintSubmitter`, IPP-Druckpfad,
  Papierwahl je Auftrag, Routenoptionen für Ausrichtung, Skalierung und Auflösung.
- Linux-Gateway, Stand
  [`fdfb4a22`](https://github.com/BattloXX/Einsatzcockpit-gateway/tree/fdfb4a22cb35f15a67031bfd77d894a1013c3c50):
  `ecpg/print_manager.py`, CUPS-Optionen und IPP-Auftragsstatus.

Es gibt keine zweite Warteschlange und keinen neuen Druckclient. Stationsdruck
erzeugt den bestehenden manuellen `PrintJob` mit `document_type=dokumentpaket`.
Die vorhandene JSON-Spalte `options` enthält Dokumentreferenzen, Seitenwahl,
Fingerprints, Papieroptionen und eine Auftragskennung. Keine neue Tabelle und keine
Datenmigration sind erforderlich. Die Originaldateien werden nicht verändert;
Druck-PDFs entstehen bei Bedarf im Speicher.

Linux verwendet den bestehenden Push-Dispatcher, Windows die vorhandene
Lease-/Intent-Kette. Beide erhalten signierte Artefaktadressen und nur ihre bekannten
öffentlichen Druckoptionen. Interne Dokumentreferenzen werden nicht als
Clientoptionen übertragen. Für Windows setzt der einzelne Auftrag die vorhandenen
Routenoptionen auf automatische Ausrichtung, Einpassen und sortierte Kopien. Die
gespeicherte Route und ihre Auflösung bleiben erhalten.

## Papierformat und Qualität

PDF-Seiten werden mit pypdf direkt aus den Quellen zusammengesetzt. Vektoren,
Text und eingebettete Originalbilder bleiben im erzeugten PDF erhalten. Es werden
keine Viewer-Screenshots verwendet. Bilder werden ohne vorherige Verkleinerung
in eine PDF-Seite eingebettet; ihre DPI-Angabe bestimmt die physische Größe
(ohne Angabe: 96 DPI).

Beim lokalen **Originalformat** bleiben unterschiedliche Seitengrößen einschließlich
A3 und bestehender Seitendrehung erhalten. Eine ausdrücklich gewählte Umwandlung
nach A4/A3 passt den vollständigen Seiteninhalt mit 6,35 mm Rand ein; der Dialog
kennzeichnet die mögliche Skalierung. Hoch-/Querformat dreht den Inhalt, ohne ihn
an einem unveränderten Ausschnitt abzuschneiden.

Stationsdruck erhält auch beim Originalformat einen Sicherheitsrand von 6,35 mm,
ohne das Papierformat zu wechseln. Das schützt vor randnahem Inhalt auch beim
vorhandenen CUPS-Client ohne eigenen Fit-Schalter. Windows kann zusätzlich in den
gemeldeten Druckbereich einpassen. Der Dialog weist darauf hin. Ein A3-Plan bleibt
auf A3; es gibt keinen stillen Rückfall auf A4. Tatsächliche Druckränder und
Treiberverhalten müssen am eingesetzten Drucker geprüft werden.

**Grenze der vorhandenen Clients:** Papier wird pro Auftrag eingestellt. Ein
Stationsauftrag mit gemischten Originalgrößen oder Sonderformaten wird deshalb
mit verständlicher Meldung abgelehnt. Man wählt ausdrücklich ein unterstütztes
A4/A3-Format oder druckt das gemischte Original-PDF lokal. Unterschiedliche
Seitengrößen bleiben im lokalen PDF erhalten. Die bestehende Windows-Treiberausgabe
rastert mit der konfigurierten Routenauflösung (Standard 300 DPI); diese Phase
ersetzt diesen Clientpfad nicht.

## Berechtigungen und nachvollziehbare Aufträge

- Vorbereitung, lokaler PDF-Abruf, Stationsauftrag und signierter Artefaktabruf
  prüfen die jeweils aktuellen Dokumentrechte serverseitig. Die vorhandenen
  Plancontroller bzw. gemeinsamen Objekt-Dateihelfer prüfen Organisation,
  Leserechte, Freigabeversion, vertrauliche Inhalte und Modulschalter.
- Quellen sind ausschließlich bekannte interne Dokumentadressen. Es gibt keinen
  HTTP-Proxy, keinen freien Dateipfad und keinen Zugriff auf externe URLs.
- Die lokale Auswahl ist 15 Minuten signiert, an Benutzer und Organisation
  gebunden und ersetzt keine Leseberechtigung. Dateien werden über SHA-256
  abgeglichen. Generierte Pläne verwenden einen Fingerprint der vollständigen
  HTML-Druckvorlage mit festgehaltener Druckzeit, damit variable PDF-Metadaten
  nicht als Dokumentänderung gelten.
- Stationsdruck übernimmt die bestehenden Rechte `recorder` samt Rollenvererbung
  und System-/Organisationsfreischaltung des Gatewaymoduls. Lesende Benutzer
  behalten den lokalen Druck, erhalten dadurch keine neuen Stationsrechte.
- JSON-Schreibzugriffe benötigen den vorhandenen CSRF-Header. PDF und
  Druckstatus werden nicht gecacht. Der PDF-Endpunkt darf nur im bestehenden
  Same-Origin-Kontext eingebettet werden.
- Die vom Dialog erzeugte UUID wird mit Benutzer und Organisation über die
  bestehende eindeutige Idempotenzspalte abgesichert. Wiederholung derselben
  Übertragung findet denselben Auftrag; andere Optionen mit derselben Kennung
  werden abgelehnt. Doppeltes Klicken sendet keinen zweiten Auftrag.
- `queued`, `sent` und `printing` sind kein Erfolg. Nur ein durch die bestehende
  Druckkette bestätigtes `done` ergibt `confirmed=true`. `failed` und `uncertain`
  bleiben ausdrücklich Fehler bzw. unklarer Ausgang; Statusabfrage löst keinen
  erneuten Druck aus. Eine technische Dienstbestätigung ist kein Nachweis, dass
  ein Benutzer die Papierseiten tatsächlich entnommen hat.

Grenzen je Auswahl: 20 Dokumente, 2000 Seiten, 100 MiB je Quelldokument und
150 MiB Quellumfang insgesamt. Fehlende, geänderte, beschädigte und geschützte
Dokumente ergeben verständliche Meldungen.

## Schnittstellen

| Endpunkt unter `/druck/viewer` | Funktion |
| --- | --- |
| `POST /vorbereiten` | `documents: [{url, pages}]`, `format`, `orientation`; liefert Token, PDF-Adresse, Seitenzahl und Formate |
| `GET /pdf?paket=…` | Geprüftes PDF; `download=true` setzt Download-Disposition |
| `GET /drucker` | Aktuell erlaubte Druckziele und Bereitschaft |
| `POST /auftrag` | Token, Drucker-ID, Kopien, UUID; bestehender manueller PrintJob |
| `GET /auftrag/{id}` | Nur eigener Auftrag innerhalb der Organisation; technischer Status |

Bestehende Viewer-Ereignisse, Plan-PDFs, Original-/Auszugslinks und andere
Druckfunktionen bleiben erhalten. Alte Medienaufrufe außerhalb der unterstützten
Dokumentquellen behalten ihren bisherigen Druckpfad. „Zum Einsatz hinzufügen“
wurde nicht erweitert.

## Tests und Abnahmestand

| Abnahmetest | Automatisierter Nachweis / verbleibender Gerätetest |
| --- | --- |
| 1. Einzelne PDF-Seite unter Windows | Auswahl und PDF-Inhalt geprüft; Browserdruckaufruf und Fallback geprüft. Echter Windows-Systemdialog und Papierausgabe offen. |
| 2. Seiten 2–4 | PDF-Inhalt, Anzahl und Reihenfolge serverseitig und im Browser geprüft. |
| 3. Seiten 1, 5, 8 | Freie Auswahl, Originaltext und Seitengeometrie serverseitig und im Browser geprüft. |
| 4. Zwei Dokumente zusammenstellen | Unterschiedliche Seitenauswahl und umgeordnete Dokumente im Browser bis zur erzeugten PDF geprüft; zusätzlich Bild und Objekt-/Planpaket geprüft. |
| 5. Stationsdrucker auswählen | Echter PrintJob, Agent-Lease/Intent, Kopien/A3, signiertes PDF und Statusabfrage geprüft; Agentenrückmeldung simuliert. Echter Druckdienst-/Druckertest offen. |
| 6. Drucker offline | Aktuelle Bereitschaft, verständliche Meldung und kein neuer Auftrag geprüft. |
| 7. A3 ohne ungewollten Beschnitt | A3-Medienbox, Text/Vektorerhalt, Sicherheitsrand und ausdrückliche A4-Umwandlung geprüft. Papierausgabe, Druckränder und Treibereinstellung am A3-Gerät offen. |
| 8. Android-Tablet Systemdialog | Touchfähige Chromium-Ansichten 1024×768 und 390×844, Druckaufruf und Downloadfallback geprüft. Echter Android-Systemdruckdialog offen. |

Es standen kein physisches Windows-/Android-Testgerät und kein angeschlossener
Drucker zur Verfügung. Die Browsertests ersetzen diese Geräteabnahme nicht.
Sie sind im bestehenden Playwright-CI-Schritt ergänzt.

Lokales Ergebnis am 25.10.2026:

- Hauptregression: **234 bestanden**, einschließlich PDF-/Viewer-Browsertests
  und vorhandener Druckkette.
- Abschließende Drucksuite: **27 bestanden**, einschließlich zusätzlichem
  Linux-Push-Test und Prüfung der vier A3-Seitenecken innerhalb des Sicherheitsrandes.
- Nach Korrektur der erneuten Vorbereitung geänderter Dokumente:
  alle **6 Druck-Browsertests** erneut bestanden.
- Ergänzende bestehende Plan-PDF-/Freigabe-/Kontakt-/Objekttests: **99 bestanden**.
- JavaScript: **134 bestanden**; Vendor-Abgleich: **259 Dateien identisch**.
- Ruff ohne Befund; mypy ohne Fehler in **517 Quelldateien**;
  JavaScript-Syntaxprüfung und `git diff --check` erfolgreich.

Die genannten Testläufe überschneiden sich teilweise. Bestehende Warnungen
betreffen unter anderem SQLAlchemy-/pypdf-Abkündigungen und die lokale
WeasyPrint-Fontbibliothek; es gab keine fehlgeschlagenen Tests.

Nach dem Push zeigte die Browser-CI einen Fehler in der Testzuordnung: Der
Änderungsfall bearbeitete den ersten Upload statt des tatsächlich geöffneten
Dokuments. Der Test ermittelt die Datei nun über die aktive Viewer-Referenz;
dadurch hängt er nicht von der Reihenfolge der freigegebenen Dokumente ab.
Die vollständige Python-CI fand außerdem noch eine Dashboard-Erwartung auf den
alten Modulnamen „Nachschlagewerke“. Sie prüft nun den gemeinsamen Einstieg
„Einsatzwissen“ sowohl bei aktivierten als auch deaktivierten Modulen.

Reproduzierbare Prüfbefehle (eigene Workerkennung verhindert Konflikte mit anderen
lokalen SQLite-Testläufen):

```sh
PYTEST_XDIST_WORKER=dokumentdruck_phase3 .venv/bin/pytest -q tests/test_dokumentdruck.py tests/test_dokumentviewer.py
PYTEST_XDIST_WORKER=dokumentdruck_phase3 .venv/bin/pytest -q tests/test_dokumentdruck_ui.py tests/test_dokumentviewer_ui.py
.venv/bin/ruff check app/
.venv/bin/mypy app/ --ignore-missing-imports
npm run test:js
npm run vendor:check
git diff --check
```

Für ein bereits installiertes Chromium kann `ECP_PLAYWRIGHT_EXECUTABLE` gesetzt
werden. Die breitere Regression umfasst zusätzlich Gateway/Dispatcher,
Windows-Agent, Fallback, Watchdog, PDF-Renderfehler und bestehende Plan-PDFs.

Browsergrundlagen:
[Window.print](https://developer.mozilla.org/en-US/docs/Web/API/Window/print),
[afterprint](https://developer.mozilla.org/en-US/docs/Web/API/Window/afterprint_event).
Ein geschlossenes Druckfenster bzw. `afterprint` gilt nicht als Druckbestätigung.
