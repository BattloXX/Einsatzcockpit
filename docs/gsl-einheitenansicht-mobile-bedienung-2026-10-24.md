# ECP – Phase 5 / Auftrag 5.2: Mobile Standardbedienung

Stand: 2026-10-24. Die vorhandene Einheitenansicht wird vereinfacht. Es entsteht kein zusätzlicher Einfachmodus. APIs, Statusmaschine, Bestätigungen kritischer Aktionen, Rechte und Outbox bleiben maßgeblich; keine Datenbankänderungen oder Migrationen.

## Ergebnis und Bedienung

Die drei Bereiche bleiben **Einsätze**, **Karte** und **Meldungen**. Die aktive Bereichswahl wird durch Fläche, Rand und Unterstreichung kenntlich, ohne eine weitere rote Hauptaktion darzustellen. Auf schmalen Geräten stehen Symbol und vollständiger Bereichsname untereinander; insbesondere „Meldungen“ wird nicht mitten im Wort umgebrochen.

| Ansicht | Dominante Hauptaktion | Weitere Bedienung |
|---|---|---|
| Einsätze | Hervorgehobenen Auftrag öffnen | Andere eigene Aufträge, Navigation, beendete/zurückgezogene Aufträge und berechtigte Personal-/Ausstattungspflege bleiben erreichbar. Pflegeaktionen sind optisch nachgeordnet. |
| Auftragsdetail | Nächster erlaubter Schritt aus der bestehenden Statusmaschine | **Meldung an Einsatzleitung** und **Foto** stehen unmittelbar beim Auftrag. **Weitere Status** bietet die anderen erlaubten Übergänge. **Weitere Aktionen** enthält Nicht durchführbar, Lagemeldung, Maßnahme, Notiz und bei Freigabe Material. |
| Karte | **Navigation starten** für das ausgewählte Ziel | Auftrag öffnen, andere eigene Ziele, Standort, Ausschnitt, Legende und Einsatzübersicht bleiben erhalten. |
| Meldungen | **Meldung an Einsatzleitung** | Übermittlungen, **Jetzt synchronisieren**, Wiederholen, Prüfen, Kopieren und bewusstes Verwerfen bleiben verfügbar. Synchronisieren ist als Nebenaktion gestaltet. |
| Formular / notwendige Bestätigung | Speichern beziehungsweise die gewählte kritische Aktion | Schließen und optionale Angaben bleiben nachgeordnet. Keine zusätzlichen normalen Bestätigungsdialoge. |

**Weitere Funktionen** im Kopf enthält Gesamtansicht, vorhandene Einheitenwechsel und Bedienhinweise. Bei GK/QR stehen dort die berechtigten Zugangsaktionen, insbesondere Abmelden und die optionale Mobilnummer-Bestätigung. Ein Übungs-/Simulationshinweis bleibt sichtbar und wird nicht in dieses Menü verlagert.

## Orientierung und Gefahren

Die haftende Auftragsbearbeitung zeigt Auftragsart, Einsatzstelle, Einheitenstatus, Auftragsstand und die große Hauptaktion. Beim Scrollen langer Auftragstexte bleiben Einsatzstelle und bekannte Gefahrentitel sichtbar. Der ausgeschriebene Einsatzstellenname steht zusätzlich vollständig im Detail; nur der wiederholte Name im haftenden Kopf ist auf zwei Zeilen begrenzt.

Umgebungsgefahren und bereits berechtigte Objektgefahren werden gemeinsam aus den vorhandenen Detaildaten dargestellt. Namen, UN-Nummern, Details und Herkunft bleiben erhalten; gleichnamige Hinweise werden nicht still zusammengefasst. Der vollständige Gefahrenbereich steht vor Karte und Auftragstext. Die rote Gefahrenleiste führt per Antippen, Klick oder Tastatur direkt dorthin. Bei sehr vielen beziehungsweise langen Gefahrentiteln bleibt die Leiste innerhalb von 26 Prozent der Ansichtshöhe scrollbar; es wird kein Gefahrentitel per Textkürzung entfernt. Der vollständige Bereich ist weiterhin unmittelbar über den beschrifteten Link erreichbar.

Offline-Stand und vorgemerkte Statusaktionen sind auch in der haftenden Bearbeitung gekennzeichnet. Ein lokaler Wunschstatus ersetzt nicht den Serverstand. Berechtigte Sperren, Zufahrten, Objekthinweise, Bilder, Pläne, andere Einheiten, Material und Meldungsverlauf sind weiterhin enthalten. Der Auftragstext bleibt vollständig lesbar.

## Lesbarkeit und Touch

- Text und Formularfelder verwenden grundsätzlich 16 px, ausreichend Zeilenabstand und umbrechende lange Inhalte. Ergänzende Hinweise bleiben größer als zuvor; Art und Status werden zusätzlich als Klartext angezeigt.
- Schaltflächen, sichtbare Formularfelder, Zusammenfassungen zum Aufklappen, Bild-/Planlinks und Kartenbedienung haben mindestens 48 × 48 px. Auch OSM-Link, Kartenquellen und die tatsächliche Dateiauswahl-Schaltfläche werden berücksichtigt. Die Detailkarte ist für größere Beschriftungen und Quellenlinks höher.
- Nebenaktionen erhalten gut lesbare Schrift statt des bisherigen stark gedämpften Texts. Tastaturfokus ist deutlich umrandet. Priorität und Auftragsart bleiben unabhängig voneinander erkennbar.
- Die Oberfläche folgt dem hellen beziehungsweise dunklen Farbschema des Geräts/Browsers. Es gibt dafür keinen zusätzlichen Bedienmodus und keinen weiteren Umschalter. Textfarben für Gefahren, Übermittlung, Auftragsart und Status werden jeweils passend gesetzt. Erhöhter Kontrast und reduzierte Bewegung werden über die vorhandenen Geräteeinstellungen berücksichtigt.
- Wesentliche Aktionen benötigen weder Hover noch Wischen oder langes Drücken. Aufklappbereiche sind beschriftete, direkt bedienbare Elemente.

## Formulare, Fehler und Rückkehr

Der Dialog zeigt Titel, Auftragsart, Einsatzstelle und Adresse außerhalb des scrollenden Eingabebereichs. Fehler stehen ebenfalls außerhalb dieses Bereichs und verschwinden beim Scrollen nicht. Die Speicherschaltfläche normaler Meldungen bleibt im Dialogfuß erreichbar.

Während eines Dialogs ist der Hintergrund `inert`; Tab und Umschalt+Tab bleiben im Formular. Anfangsfokus liegt auf der Überschrift, sodass keine Bildschirmtastatur ungefragt aufgeht. Antippen des Hintergrunds und Escape verwerfen keine Eingaben. Das vorhandene ausdrückliche Schließen bleibt verfügbar und ist während des Speicherns gesperrt.

Nach erfolgreichem Speichern beziehungsweise bewusstem Schließen werden Fokus und Scrollposition der unveränderten Ausgangsansicht wiederhergestellt. Die Rückkehr wartet auf das Ende laufender Speicherung, damit eine vorübergehend gesperrte Schaltfläche nicht ins Leere fokussiert wird. Verspätete Fokuswiederherstellung wird bei einem neuen Dialog oder einem anderen Auftrag/Bereich verworfen. Besteht das ursprüngliche Element nicht mehr oder ist es nach Abschluss gesperrt, dient die Auftragsorientierung als Fokusziel. Statusaktionen wechseln weder Auftrag noch Bereich automatisch.

Fehlgeschlagene Speicherung lässt den Dialog samt Text und gewählten Fotos offen. Eine lokal gespeicherte Offline-Meldung bleibt anhand der bisherigen Outbox-Zustände als noch nicht übermittelt erkennbar. Die vorhandene Pflichtfeldprüfung, Fotozuordnung und Sicherung gegen Doppeltippen bleiben erhalten.

## Änderungsstellen und Regression

- `app/templates/einheit/einheit.html`: vereinfachter Kopf, nachrangige Funktionen, zentrale Detailaktionen, früh sichtbare Gefahren, Dialogorientierung und gesperrter Hintergrund.
- `app/templates/einheit/_auftragsfuehrung.html`: haftende Auftragsart, Gefahrenleiste, Offline-Hinweis und stabiles Fokusziel.
- `app/templates/einheit/_uebermittlungen.html`: Synchronisieren als Nebenaktion; Übermittlungen und Fehleraktionen bleiben enthalten.
- `app/templates/einheit/_einheit_styles.html`: Schrift, Kontraste, Geräteschema, Touchflächen, Fokus, mobile Bereichswahl, Karte und Formulardarstellung, jeweils auf die Einheitenansicht begrenzt.
- `app/static/js/einheit_bedienung.js`, `einheit_modus.js`: reine Anzeigeprojektion der vorhandenen Gefahren und Dialogfokus/Rückkehr; das neue Modul ist im bestehenden Import-Mapping versioniert.
- `tests/test_einheit_bedienung_ui.py`: Smartphone 360/390 px und Fahrzeugtablet 1.024 × 600 px, beide Farbschemata, vergrößerte Kontraste, genau eine Hauptaktion, Touchprüfung einschließlich Kartenbedienung, lange Aufträge mit Objektgefahr, Tastaturbedienung, Speicherfehler mit Fotoerhalt, Offline-Wiederholung, Rückkehr und GK-/QR-Funktionen. Die relevanten Texte werden auf mindestens 4,5:1 Kontrast geprüft.
- `tests/js/einheit_bedienung.test.mjs`: vollständige Gefahrenprojektion einschließlich Herkunft, UN-Nummer, Altdaten und gleichnamiger Gefahren. Bestehende Browserfälle prüfen weiterhin alle Status-, Melde-, Karten- und Boardabläufe; ihre Öffnungswege entsprechen der vereinfachten Standardoberfläche.

Der neue Browser-Test ist im vorhandenen Playwright-CI-Job eingetragen. Farbschemata und Kontraste werden automatisiert im Browser geprüft. Die Betrachtung in realer Sonne, bei Nacht im Fahrzeug und mit Handschuhen benötigt eine separate Geräteabnahme; die Browserprüfung behauptet diese physische Prüfung nicht.

Validierung am 2026-10-24: **440 unterschiedliche Python-/Browserfälle** über den breiten Einheiten-/Board-/Outbox-/Rechte-/Sperren-/Service-Worker-Regressionslauf und die abschließende Bedienabnahme erfolgreich geprüft. Die **13 neuen UI-Abnahmefälle** bestehen vollständig; **122 JavaScript-Tests**, Ruff, JavaScript-Syntaxprüfung und `git diff --check` ebenfalls. Testläufe verwenden getrennte Datenbanken im bestehenden Worker-Namensraum.
