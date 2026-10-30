# ECP – Phase 3 / Auftrag 3.2: Geführte Auftragsbearbeitung

Fortschreibung: Die verpflichtende Ergebnisauswahl und Abschlussprüfung aus [Auftrag 4.1](gsl-einheitenansicht-rueckmeldungen-2026-10-24.md) ergänzen die folgenden Regeln.

Stand: 2026-10-24. Baut auf [Mehrfachübernahme 3.1](gsl-einheitenansicht-mehrfachuebernahme-2026-10-24.md) auf.

## Verbindliche Bedienung

Eine eigene, beim Scrollen erreichbare Aktionsleiste zeigt Einsatzstelle, **Einheitenstatus** der Ressource und **Auftrag** als bestätigten Serverstand. Die große Hauptschaltfläche ist mindestens 56 px hoch. Nebenaktionen bleiben als zurückhaltende Buttons beziehungsweise unter **Weitere Status** erreichbar. Smartphone und Tablet verwenden dieselbe Logik.

| Auftragsart | Geführte Standardfolge |
|---|---|
| Abarbeitung | Auftrag übernehmen → Anfahrt melden → Vor Ort melden → In Arbeit setzen → Fertig melden |
| Erkundung | Auftrag übernehmen → Anfahrt melden → Vor Ort melden → Erkundungsergebnis erfassen → Erkundung abschließen |

Die Folge legt UI-Bezeichnungen und eine bevorzugte Reihenfolge fest. `erlaubte_status` aus dem Backend entscheidet über jede tatsächliche Aktion. Die UI wählt ausschließlich freigegebene nächste Schritte und erhält erlaubte direkte Sprünge als Nebenaktionen. **Erkundungsergebnis** ist ein Formularschritt, kein neuer Serverstatus. Es gibt keine neue Statusmaschine, Datenfelder oder Datenbankmigration.

Ohne freigegebenen Fortschritt bleibt eine eindeutig beschriftete, gesperrte Hauptschaltfläche sichtbar, etwa **Freigabe ausstehend**, **Fertig gemeldet**, **Erkundet**, **Auftrag zurückgezogen** oder **Nur lesen**. Unbekannte oder fehlende Statusdaten führen zu **Auftrag prüfen**, einem reinen Leseabgleich im selben Auftrag. Verbotene Schritte werden weder angeboten noch aus einem fehlenden Wert erraten.

## Entscheidungen und Schutz vor Fehlbedienung

- Übernahme, Anfahrt, Vor Ort und In Arbeit benötigen keine zusätzliche Bestätigung. Fertigmeldung, Unterbrechung sowie Wechsel eines aktiven Auftrags verlangen eine ausdrückliche Entscheidung. Bei **Nicht durchführbar** bleibt ein Grund erforderlich.
- Das Ergebnisformular nennt die Einsatzstelle. Die Ergebnisauswahl ist seit Auftrag 4.1 verpflichtend; bei Maßnahmen, Gefahr oder weiterer Erkundung muss eine Beschreibung ergänzt werden. Fotos bleiben optional. Öffnen schreibt keinen Status. Die abschließende Formularaktion beendet die Erkundung ohne weiteren Bestätigungsdialog.
- Status- und Ergebnisaktionen besitzen eine synchrone Speichersperre. Nach dem lokalen Speichern blockiert eine kurze Tipp-Sperre ein unmittelbar folgendes zweites Tippen desselben Auftrags; ausstehende Statusmeldungen sperren weitere Schritte bis zur Klärung. Backend-Idempotenz und Versions-/Statusprüfungen aus Auftrag 3.1 bleiben wirksam.
- Dialoge bewahren die beim Öffnen gelesene Auftragsversion und den Ausgangsstatus. Führungsänderung, Statusänderung, entzogene Phasenfreigabe, Abschluss oder Rückzug während des Dialogs sperren die überholte Aktion. Ein bewusster Neuaufruf verwendet den aktuellen Stand.
- Statusaktionen und laufende Aktualisierungen verändern weder URL noch gewählten Bereich automatisch. Der abgeschlossene Auftrag bleibt sichtbar. Navigationswechsel geschehen nur auf ausdrückliche Bedienung.
- Der Gesamtstatus der Einheit wird aus dem vorhandenen `kopf.ampel` gelesen. Die Auftragsaktionen verwenden ausschließlich die Disposition; die Oberfläche sendet keine Fahrzeugstatus-/FMS-Aktionen.

## Offline und Erkundungsergebnis

Der bestätigte Serverstand bleibt unter **Auftrag** sichtbar. Ein lokaler Wunsch erscheint separat als **Lokal vorgemerkt … noch nicht übermittelt · nur auf diesem Gerät**. Die Hauptschaltfläche lautet **Übermittlung ausstehend**; sie verspricht keinen bereits bestätigten Fortschritt. Bei einem fachlichen Konflikt führt **Auftrag prüfen** zum aktuellen Stand, ohne automatischen Statuswechsel.

Die Outbox speichert Ergebnis, Fotodaten und Abschluss in einer gemeinsamen IndexedDB-Transaktion, bevor irgendein Teil versandt wird. Scheitern das Lesen einer Datei oder das Speichern, wird das gesamte Paket verworfen; Formulartexte und Dateiauswahl bleiben für einen bewussten neuen Versuch erhalten. Die Sequenznummern entstehen in derselben Transaktion, auch bei mehreren Tabs. Die bisherigen Einzelaktionen und die vorhandenen Object Stores bleiben kompatibel; die IndexedDB-Version bleibt unverändert.

Nach erfolgreichem lokalen Speichern bleiben Ergebnis, Fotos und Status eigenständige Übermittlungen mit eigener Serverbestätigung. Ein ausstehendes Foto oder ein abgelehntes Ergebnis wird weiterhin als offene beziehungsweise fehlerhafte Übermittlung angezeigt. Es gibt keine neue serverseitige Paket-API. Seit Auftrag 4.1 sind die Ergebnisauswahl und bei Maßnahmen, Gefahr oder weiterer Erkundung eine kurze Beschreibung verpflichtend; der Abschluss referenziert das erfolgreich gespeicherte Ergebnis.

Textentwürfe werden mit dem vorhandenen Entwurfspeicher je Disposition abgelegt. Der Formularaufruf lädt nur den passenden Entwurf; eine verspätete Antwort darf keine andere Auswahl überschreiben. Ältere Entwurfsversionen bleiben mit Prüfhinweis erhalten. Ungespeicherte Fotos werden nicht automatisch als dauerhafter Entwurf heruntergeladen oder übertragen.

## Prüfungen

- API-Vertrag: alle 98 Kombinationen der sieben bisherigen Statuswerte für Erkundung und Abarbeitung; erlaubte und verbotene Übergänge einschließlich bestehender Noops. Priorität, Reihenfolge und Führungs-Auftragsversion bleiben unverändert.
- GSL-Board: überholte Tablet-Aktion nach einem Funkstatus bleibt Konflikt; die geöffnete Ansicht aktualisiert ihre Hauptaktion im selben Auftrag. Ein Board-Statuswechsel während des Abschlussdialogs verhindert dessen veralteten Abschluss. Die vorhandenen Konflikt-, Phasen- und MariaDB-Paralleltests aus 3.1 bleiben Teil der Regression.
- Browser: vollständige Folgen bei 360 px Smartphone und 1.024 px Tablet, Mindestgröße und Erreichbarkeit beim Scrollen, normale Schritte ohne Dialog, kritische Entscheidungen, kein Status beim Öffnen, kein automatischer Seitenwechsel, Doppeltippen, Offline/Reload/Synchronisation, Phasensperre, Rückzug, Abschluss und lesende Simulation.
- Erkundung: echter Bericht-/Foto-/Statusversand, wiederholtes Tippen, simulierter Speicherfehler mit erhaltenen Eingaben und Dateien, anschließender einzelner Paketversuch, auftragsbezogene Entwürfe.
- JavaScript: Darstellung sämtlicher Teilmengen der Backend-Freigaben, fehlende/ungültige Werte, vollständiger lokaler Paketbestand vor Versand, Transaktionsabbruch ohne Restdaten, unlesbare Datei und parallele Sequenzvergabe.

Dateien: `einheit_ablauf.js` (reine UI-Auswahl), `_auftragsfuehrung.html`, `einheit_modus.js`, `einheit_outbox.js`; Tests in `test_einheit_ablauf.py`, `test_einheit_ablauf_ui.py` und `tests/js/`. Die CI führt die neuen Browserfälle im vorhandenen Playwright-Job aus. Safari und echte Geräte benötigen weiterhin eine gesonderte Abnahme.
