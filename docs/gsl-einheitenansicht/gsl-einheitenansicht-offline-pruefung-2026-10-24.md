# ECP – Phase 6 / Auftrag 6.1: Offline- und Synchronisationsprüfung

Stand: 2026-10-24. Geprüft wurde die nach den Phasen 1–5 umgebaute Standardansicht. Korrekturen verwenden dieselbe IndexedDB-Outbox, APIs, UUIDs und Statusmaschine. Keine neue Synchronisationsarchitektur und keine Datenbankmigration.

## Fachliche Abnahme

| Ablauf | Nachweis und Verhalten |
|---|---|
| Status offline | Der lokale Wunschstatus bleibt getrennt vom Serverstand. Die nächste Statusaktion desselben Auftrags wartet auf Bestätigung. Die bisherigen Tests prüfen alle erlaubten/verbotenen Backend-Übergänge; Browserfälle prüfen das vorgemerkte Verhalten und die spätere Übernahme. |
| Mehrfachübernahme | Drei zugewiesene Aufträge, zwei offline bestätigte Aufträge, Offline-Neustart und automatische spätere Übernahme. Beide Bestätigungen beginnen keine aktive Bearbeitung; Priorität und Reihenfolge bleiben erhalten. Bestehende Paralleltests sichern weiterhin höchstens einen aktiven Auftrag. |
| Lagemeldung und Foto | Meldung und Fotodaten überleben einen echten Offline-Neustart. Fotos bleiben dem ursprünglichen Auftrag zugeordnet. Erst die Wiederverbindung erzeugt Servermeldung und Serverfoto. |
| Erkundungsabschluss | Ergebnis, Fotos und Abschluss sind vor dem ersten POST gemeinsam lokal gespeichert. Die Ergebnisreferenz bleibt über Neustart und Retry erhalten. Ein unveränderter Auftrag wird korrekt beendet; ein inzwischen geänderter Auftrag erhält keinen Abschluss mit der alten Version. Das Ergebnis kann als Rückmeldung mit Prüfhinweis erhalten bleiben. |
| Wiederverbindung | Browser-Online, sichtbarer Tab, WebSocket-Reconnect und vorhandener Resync verwenden den bisherigen Abgleich. Vor dem Versand werden Zugang, Einheitenkontext und CSRF-Cookie über die bestehende Zustands-API geprüft/erneuert. Nach bestätigter Wiederverbindung werden Netzrückstände sofort versucht. |
| Geänderter Auftrag / Boardstatus | Ursprüngliche Version und Ausgangsstatus bleiben in der ausstehenden Aktion erhalten. Die Synchronisation überschreibt keine neuere Weisung und keinen neueren Boardstatus; der Eintrag bleibt als Konflikt zur bewussten Prüfung erhalten. |
| Rückzug | Die alte Offline-Bestätigung wird abgelehnt. Bereits erfasste Meldungen/Fotos behalten die bestehenden Backend-Regeln, einschließlich Kennzeichnung nach Rückzug; eine Rückmeldung aktiviert keinen zurückgezogenen Auftrag. |
| Doppeltippen / verlorene Antwort | Wiederholte Klicks erzeugen keine unbeabsichtigten Pakete. Geht eine Antwort nach Servercommit verloren, verwendet der Retry dieselbe UUID. Status und Fotos erzeugen dabei jeweils nur eine Serverwirkung. Zwei Tabs erzeugen zur selben offenen Aktion nur eine lokale Quittung. |
| GK / QR abgelaufen | Vor dem Versand wird der abgelaufene Zugang erkannt. Ausstehende Einträge samt Fotos bleiben lokal gesperrt; keine automatische Wiederholung mit neuem Zugang. Bekannte ungültige Lesecaches werden entfernt. Verspätete Antworten dürfen keine Erfolgsmeldung oder Wiederfreigabe erzeugen. |
| WebSocket-Reconnect | Ein echter Browser-Socket wird im Test geschlossen. Der Reconnect liest eine zwischenzeitlich geänderte Weisung neu, ohne Status zu ändern oder die Auftragsversion automatisch als gelesen zu markieren. |

## Gefundene Fehler und Korrekturen

1. **Unbegrenzt hängende Übertragungen:** Ein POST beziehungsweise dessen JSON-Antwort konnte eine Statusspur unbegrenzt aufhalten. Requests sind jetzt einschließlich Antwortkörper auf 15 Sekunden begrenzt, Foto-/Materialfoto-Uploads auf 60 Sekunden. Danach bleibt der Eintrag unbestätigt und wiederholbar. Foto- und Statusspuren bleiben wie bisher unabhängig.
2. **Sofort-Retry bei laufenden Fotos:** Ein fälliger, bereits laufender Foto-Retry konnte erneut einen Timer mit null Wartezeit planen. Laufende UUIDs werden bei der nächsten Retry-Frist ausgeschlossen; alte asynchrone Timerpläne dürfen neuere nicht ersetzen.
3. **Zugangsende und späte Antworten:** Zugangssperre stoppt und bricht laufende Requests ab, bevor Lesecaches bereinigt werden. Eine verspätete Antwort darf gesperrte Einträge nicht überschreiben, löschen oder bestätigen. Auch ein manueller Wiederholungsaufruf entsperrt sie nicht. Ein Fehler allein beim Cache-Aufräumen verhindert die Sperre nicht.
4. **Doppelte lokale Quittung:** Bestätigen prüft und entfernt den noch offenen Eintrag innerhalb derselben IndexedDB-Transaktion wie das Eintragen der Quittung. Ein zweiter Tab findet keinen offenen Eintrag mehr und erzeugt keine zweite Quittung.
5. **Falscher Einheitenkontext:** Die Outbox sendet nur Einträge ihrer ausdrücklich zugeordneten Einheit. Alte beziehungsweise nicht eindeutig zugeordnete Einträge werden nicht für eine neue Einheit versandt. Zusätzlich prüft die bestehende API den optionalen Header `X-EC-Einheit-Kontext` gegen den authentifizierten Kontext. Er ist nur eine Vorbedingung und erteilt keine Berechtigung. Alte Clients ohne Header bleiben kompatibel. Auch ein frischer Zustandsabruf muss Lage und Einheit der geöffneten Ansicht entsprechen.
6. **UUID für falschen Auftrag oder falsche Aktionsart:** Eine UUID konnte zuvor allein aufgrund desselben Zugangs eine fremde Aktionsquittung desselben Geräts zurückgeben. Replay prüft nun Organisation, Einheit, Auftrag, Quelle und Aktionsart sowie den bisherigen Principal. Das gilt auch beim parallelen Unique-Key-Konflikt und für Foto-/Materialfoto-Replay. Eine unpassende UUID liefert keine scheinbare Bestätigung.
7. **Wiederverbindung vor Token-Erneuerung:** Der Versand lief teilweise vor dem lesenden Wiederabgleich. Er folgt jetzt auf den erfolgreichen Abgleich. Die bekannte CSRF-Ablehnung bleibt außerdem wiederholbar und wird nicht als fachlicher Auftragskonflikt festgeschrieben. Andere Berechtigungs-/Auftragskonflikte werden nicht automatisch zurückgesetzt.
8. **Offline-Neustart einer per JavaScript geöffneten Detail-URL:** `pushState` erzeugt keinen HTML-Request. Beim Offline-Neuladen dieser URL fehlte deshalb die Shell. Der bestehende Service Worker darf für eine solche Navigation die bereits geladene `/einheit`-Shell derselben Einheiten-/Simulationsauswahl verwenden. API-Requests erhalten niemals HTML als Ersatz. Bekannte Zugangsfehler entfernen auch gecachte Detailseiten.
9. **Offline-Banner über der Abschlussaktion:** Der globale Banner überdeckte im echten Offline-Betrieb eine Dialogaktion. Die Einheitenansicht verwendet ihre eigenen Hinweise; im Dialog steht der Offline-Hinweis sichtbar beim Auftrag. Die Speicher-/Abschlussaktionen bleiben per Touch erreichbar.
10. **Kurzzeitig alte Erfolgsmeldung:** Bei verzögertem Lesen der Outbox konnte nach dem Speichern eines neuen Fotos noch die alte leere Liste samt „Alles übermittelt“ sichtbar sein. Nach dauerhaftem Commit meldet die vorhandene Outbox neue Einträge unmittelbar als reine Metadaten an die Ansicht, noch vor dem ersten POST. Späte ältere Schnappschüsse werden verworfen. Binärdaten werden dabei nicht in den UI-Zustand kopiert.

## Verbindliche Anzeige der Übermittlung

- **Übermittelt** setzt eine erfolgreiche Serverantwort mit `aktion_id` voraus. Antworten aus dem Offline-Cache, leere/falsche Antworten oder ausdrücklich negative Antworten bestätigen nichts. Eine echte Serverprojektion im Meldungsverlauf ist ebenfalls ein Servernachweis; ein allein lokaler Eintrag ist keiner.
- **Nur lokal gespeichert** bezeichnet dauerhaft vorhandene, noch unbestätigte Daten. Die Erfassung im Browser ersetzt keine Serverquittung. Während des lokalen Speicherns steht ein Fortschrittshinweis statt einer Erfolgsmeldung.
- **Übertragung fehlgeschlagen** steht bei fehlgeschlagenem Versand mit einem verständlichen Wiederholungs- beziehungsweise Prüfhinweis. Fachliche Konflikte zeigen zusätzlich den konkreten Grund. Bei Zugangssperre steht ausdrücklich, dass die Serverbestätigung fehlt.
- **Keine offenen Übermittlungen** ist der neutrale Zustand ohne ausstehende Einträge. **Alles übermittelt** wird nur bei vorhandenen eigenen Serverquittungen und leerer eigener Outbox verwendet.

Nach einer verlorenen Antwort kann der Server eine Aktion bereits ausgeführt haben, während lokal noch eine Bestätigung fehlt. Der Retry derselben UUID klärt genau diesen Zustand. Das lokale Löschen einer unbestätigten Aktion ist kein Beweis, dass ein zuvor begonnener Versand nie den Server erreicht hat.

## Bestehende Architektur und Grenzen

`einheit_outbox.js` verwendet unverändert die bisherigen Stores und Datenbankversion 1. Reihenfolge, ursprüngliche UUID, Auftragsversion und Ausgangsstatus werden beim Retry nicht ersetzt. Rückoff bleibt 2, 4, 8 … bis maximal 60 Sekunden. Nur Netzrückstände werden nach einem erfolgreichen Wiederabgleich sofort erneut versucht; fachliche Konflikte und Zugangssperren bleiben bestehen. Hintergrundfehler beim lokalen Quittieren werden nicht zu einem unbemerkten Erfolgszustand.

Fotos laufen unabhängig von der Statusspur. Ein bestätigter Auftragsabschluss ist deshalb keine automatische Bestätigung aller Fotos; jede Übermittlung behält ihren eigenen Stand. Nach 72 Stunden werden weiterhin nur alte Quittungen/Entwürfe bereinigt, keine unbestätigten Outbox-Einträge. Bereits bekannte, berechtigte Daten bleiben offline lesbar. Nie geladene Inhalte, Kartenkacheln, externe Navigation und ein erstmaliger Online-Zugang werden dadurch nicht offline verfügbar. Abgelaufene/entzogene Berechtigungen werden bei Wiederverbindung serverseitig geprüft; offline wird kein neuer Zugangsbeweis erzeugt.

Konkrete Änderungen: `app/static/js/einheit_outbox.js`, `einheit_modus.js`, `einheit_verlauf.js`, `app/static/sw.js`, `app/routers/ui_einheit.py` sowie die bestehenden Übermittlungs-, Dialog- und Stil-Templates. Die GSL-Board-Statusmaschine und ihre Phasen-/Aktivitätsregeln bleiben erhalten.

## Automatische Nachweise

- `tests/test_einheit_offline_ui.py`: elf Browserfälle mit tatsächlich aktivem Service Worker, realem Offline-Netzwerkzustand, Offline-Reload, IndexedDB-Fotos, automatischem Abgleich, erneuertem CSRF-Cookie, verlorener Serverantwort, Konflikten, GK/QR und WebSocket-Reconnect. Der lokale HTTP-Testserver verwendet die echten ECP-Templates/APIs; sein WebSocket-Transport liefert Ping/Pong. Die produktiven WebSocket-Berechtigungen werden zusätzlich durch die bestehenden Routertests geprüft.
- `tests/test_einheit_sync_ui.py`: gezielt verzögerter Outbox-Schnappschuss nach einer früheren Quittung; ein neues Offline-Foto darf dabei keine alte Erfolgsmeldung behalten.
- `tests/test_einheit_sync_api.py`: UUID-Replay, falsche Aktionsart, anderer Auftrag, Foto-Deduplizierung und erwarteter Einheitenkontext ohne Schreibwirkung bei Abweichung.
- `tests/js/einheit_outbox.test.mjs`: Timeouts einschließlich Antwortkörper, Foto-Retry, späte Antworten, Sperre, zwei Tabs, Einheitenwechsel, CSRF, sofortiger Wiederholungsversuch nach bestätigter Verbindung und Anzeige erst nach lokalem Commit.
- `tests/mariadb/test_einheit_sync.py`: drei parallele Unique-Key-Fälle mit derselben beziehungsweise einer anderen Einheit/Aktionsart. Nur die richtige Originalaktion erhält die Quittung; es entsteht genau eine persistierte Wirkung.

Die neuen Browserfälle sind im vorhandenen Playwright-CI-Job eingetragen. Testdatenbanken verwenden eigene Worker-Namen; MariaDB lief ausschließlich in einer eigenen temporären Testinstanz mit den vorhandenen Migrationen. Es wurden keine neuen Migrationen angelegt.

Validierung am 2026-10-24: **454 Fälle im breiten Python-/Browser-Regressionslauf** erfolgreich. Nach der ergänzten Absicherung gegen die kurzzeitig alte Erfolgsmeldung und der sofortigen Wiederaufnahme bestanden **80 Browserfälle** für Offline, Synchronisationsanzeige, Übersicht, Bearbeitung, Rückmeldungen und Verlauf. **131 JavaScript-Tests**, **7 echte MariaDB-Tests**, Ruff, mypy, JavaScript-Syntaxprüfung und `git diff --check` sind ebenfalls erfolgreich. Der temporäre MariaDB-Container wurde entfernt. Die Prüfungen verwenden Chromium mit browserseitig abgeschaltetem Netzwerk; sie ersetzen keine physische Geräte-/Funknetzabnahme.
