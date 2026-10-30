# ECP – Phase 4 / Auftrag 4.1: Rückmeldungen vereinfachen

Stand: 2026-10-24. Aufbau auf der vorhandenen Einheitenansicht und der geführten Bearbeitung aus 3.2. Keine Migration, zusätzliche Nachrichtentabelle oder neue Statuswerte.

## Bedienung

**Meldung an Einsatzleitung** ist im Bereich **Meldungen** und im geöffneten Auftrag erreichbar. Das Ziel ist immer sichtbar und unter den eigenen zugewiesenen Aufträgen auswählbar. Eine normale Rückmeldung benötigt höchstens vier Eingaben: Meldungen → Meldung an Einsatzleitung → Schnellmeldung → Erfassen. Im Auftrag sind es drei. Freitext und Fotos sind ergänzend möglich; ohne Schnellmeldung ist eine Textangabe erforderlich.

| Erkundung | Abarbeitung |
|---|---|
| Maßnahmen erforderlich | Arbeit läuft |
| Keine Maßnahmen erforderlich | Auftrag erledigt |
| Akute Gefahr festgestellt | Unterstützung benötigt |
| Weitere Erkundung erforderlich | Auftrag nicht durchführbar |

**Auftrag erledigt** öffnet die bestehende Abschlussbestätigung. **Auftrag nicht durchführbar** öffnet die vorhandene Statusaktion mit Pflichtbegründung. Diese Aktionen stehen nur entsprechend den Backend-Freigaben zur Verfügung. Ergänzungen und Fotos sind auch im Abschluss-/Nicht-durchführbar-Dialog möglich. Bereits gewählte Fotos und eingegebener Text werden in diesen Dialog übernommen und erst mit der abschließenden Bestätigung gespeichert. Normale Schnellmeldungen verändern keinen Auftragsstatus und keine Führungspriorität oder Reihenfolge.

Der **Erkundungsabschluss** verlangt eine ausdrückliche Ergebnisauswahl. **Keine Maßnahmen erforderlich** ist eine vollständige Aussage. Bei Maßnahmen, akuter Gefahr oder weiterer Erkundung muss Lage oder notwendige Maßnahme zusätzlich beschrieben werden (mindestens drei Buchstaben/Ziffern zusammen). Fotos bleiben optional. Eine normale Schnellmeldung beendet die Erkundung nicht.

**Diktieren** verwendet die vorhandene `startVoice`-Funktion, wenn die SpeechRecognition-Schnittstelle verfügbar ist. Die native Diktierfunktion der Gerätetastatur bleibt in normalen Textfeldern nutzbar. Spracheingabe und Mikrofonfreigabe hängen vom Browser und Gerät ab. Das `input`-Ereignis übergibt den erkannten Text an Alpine und den Entwurfsspeicher.

## Bestehende Datenwege und Rechte

- Der Katalog in `site_log_service.py` wird ausschließlich über das vorhandene `auftragsart` gewählt und mit den Auftragsdaten auch offline bereitgestellt. Freitext, Maßnahme und Notiz bleiben erreichbar.
- Schnellmeldungen sind kanonisch formatierte **Lagemeldungen** an `/einheit/api/auftrag/{id}/meldung`. Backendseitig werden Auftragsart, Auswahl, Text und Ergebnis geprüft. Chronik, Autor, Einheit, Audit, Lagemeldungsregelkreis und Board-Ereignisse bleiben erhalten.
- Für einen Erkundungsabschluss sendet die bestehende Outbox zuerst den Ergebnisbericht, dann die Statusaktion. `ergebnis_client_uuid` referenziert die bereits erfolgreich gespeicherte Aktion im vorhandenen `EinheitAktion.antwort_json`. Einheit, Organisation, Disposition, Auftragsversion und vorhandener Chronikeintrag müssen passen. Die Prüfung erfolgt unter der bestehenden Statussperre nach den Konflikt- und Übergangsprüfungen. Ein fehlgeschlagener Bericht erlaubt keinen Abschluss.
- Fotos verwenden unverändert die bestehende Foto-API mit der festgehaltenen Dispositions-ID. Meldung und Fotos werden vor dem Versand gemeinsam lokal gesichert. Bei ausgewählten Fotos ist die Einsatzwahl gesperrt; eine laufende Bildverarbeitung darf Fotos auch bei einer späteren Detailaktualisierung keinem anderen Einsatz zuordnen.
- GK-Link, QR und Geräteprofil verwenden unverändert ihre Kontext-, Aktions- und Medienberechtigungen. Es gibt keinen neuen Zugriff auf fremde Aufträge oder Meldungen.

## Darstellung im GSL-Board

**Akute Gefahr festgestellt** wird rot, **Unterstützung benötigt** orange hervorgehoben; Klartext und Symbol tragen die Bedeutung zusätzlich. Der Verlauf behält die Hervorhebung dauerhaft. Auf Boardkarten erscheint die jeweils letzte Lagemeldung einer Einheit, sofern sie eine solche dringende Schnellmeldung ist. Eine spätere normale Lagemeldung derselben Einheit löst den Kartenhinweis ab; sie löscht keine frühere Meldung und erklärt eine Gefahr nicht automatisch für behoben. Die Hinweise verändern keine Einsatzpriorität, erzeugen keinen eigenständigen Gefahrenmarker und ersetzen keinen bestehenden Führungsentscheid.

Die Kartenabfrage lädt gesammelt die letzten Lagemeldungen je Einsatzstelle/Einheit statt vollständiger Chroniken. Bestehende WebSocket-Ereignisse aktualisieren Karten und Verlauf.

## Offline, Wiederholung und Kompatibilität

Ergebnis, Fotos und Abschluss bleiben einzelne Vorgänge der bisherigen Outbox, vor dem ersten Versand in einer gemeinsamen IndexedDB-Transaktion gesichert. Datenbankschema, IndexedDB-Version und API-Pfade bleiben unverändert. Das Paket kann eine vorab erzeugte UUID für die Ergebnisreferenz übernehmen. Lokale Speichersperren verhindern Doppeltippen; serverseitige UUID-Idempotenz verhindert doppelte Übermittlung. Speicherfehler erhalten Formular und Dateiauswahl; ein Einsatzwechsel wird bei einem nicht speicherbaren Textentwurf abgebrochen. Auftragsbezogene Entwürfe, Wiederholung und Konfliktbehandlung bleiben nutzbar.

Bereits lokal gespeicherte alte Erkundungsabschlüsse ohne Ergebnisreferenz werden bewusst abgewiesen und bleiben als Fehler sichtbar. Der aktuelle Auftrag muss geprüft und ein Ergebnis neu erfasst werden. Bereits bestätigte UUIDs werden weiterhin wiederholt beantwortet. Die manuelle Funkstatusfunktion der Einsatzleitung bleibt erhalten; sie ist keine Einheitenmeldung und erhält keine neue Formularpflicht. Ein fehlendes Foto blockiert den Abschluss nicht; der ausstehende Fotoeintrag bleibt sichtbar.

## Regression und Abnahme

`test_einheit_rueckmeldungen.py` prüft alle normalen Schnellmeldungen ohne Text, UUID-Wiederholung, falsche Auftragsarten, leere Ergebnisse, unpassende/veraltete Ergebnisreferenzen, Boarddarstellung, Priorität und GK-/QR-/Gerätegrenzen. `test_einheit_rueckmeldungen_ui.py` prüft echte Browserabläufe bei 360 und 1.024 px: vier Eingaben, Touchflächen, keine Dubletten, Pflichtfelder, Offline-Neustart mit Ergebnis/Fotos/Abschluss, Speicherfehler, Fotozuordnung während der Kompression, Statusdialoge und Diktatübernahme. Ein gehaltener Start-Detailabruf prüft zusätzlich, dass die verspätete Antwort einen bewusst gewählten Meldungsbereich nicht wieder verdeckt. Statusmeldungen mit optionalem Text und übernommenen Fotos werden erst beim Bestätigen versandt. Die bestehenden Übergangs-, Board-, Rechte- und Outbox-Tests bleiben Regression. Der Playwright-CI-Job umfasst die neuen Fälle.

Echte Smartphone-/Tablet-Mikrofone und Safari benötigen eine Geräteabnahme; die automatisierten Browserfälle verwenden Chromium und eine simulierte Sprachschnittstelle.

Validierung am 2026-10-24: **355 Python-/Browsertests**, **100 JavaScript-Tests** und **3 echte MariaDB-Paralleltests** erfolgreich. Ruff für die geänderten Python-Dateien, mypy für die fünf betroffenen Python-Module, JavaScript-Syntaxprüfung und `git diff --check` erfolgreich. MariaDB wurde ausschließlich in einem eigenen kurzlebigen Testcontainer mit bestehenden Migrationen aufgebaut; der Container wurde anschließend entfernt.
