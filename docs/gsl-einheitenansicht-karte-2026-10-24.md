# ECP – Phase 5 / Auftrag 5.1: Einheitenkarte

Stand: 2026-10-24. Baut auf der vorhandenen Leaflet-Karte und den Phasen 1–4 auf. Keine neue Kartenbibliothek, Statuslogik, Datenfelder oder Migrationen.

## Bedienung und Abnahme

- Die Karte zeigt alle offenen eigenen Aufträge. Der aktive Auftrag hat einen größeren Marker mit weißem Außenring und Klartext **Aktuell**; ohne aktiven Auftrag ist der nächste vorausgewählt. Auftragsart und Priorität bleiben getrennt: orange Lupe **ERKUNDUNG**, blaues Werkzeug **AUFTRAG**, eigenständiger Prioritätsring und Prioritätstext.
- **Ziel wählen** in der vollständigen Auftragsliste oder Antippen eines Markers wählt das Kartenziel. Die Zielkarte zeigt Einsatzstelle, Adresse, Auftrag, Bearbeitungsstand sowie bekannte Gefahren und Sperren. Die Auswahl bestätigt oder startet keinen Auftrag. Ein zurückgezogener Auftrag verschwindet; die Auswahl fällt auf den aktuellen beziehungsweise nächsten eigenen Auftrag zurück.
- **Navigation starten** öffnet die bestehende Google-Zielnavigation in einem separaten Fenster. **Auftrag öffnen** führt unmittelbar zum vollständigen Auftrag; **Zur Einsatzübersicht** und die bestehende Bereichsnavigation führen zurück. In der Auftragsbearbeitung bleibt Navigation eine Nebenaktion zur primären Statusaktion.
- Deckungsgleiche Positionen tragen eine Anzahl. Ihr gemeinsames Kartenfenster enthält alle eigenen Aufträge dieser Position mit jeweils eigener Öffnen-Aktion. Die Liste bleibt zusätzlich per Touch und Tastatur erreichbar. Es werden keine Positionen künstlich versetzt.
- **Alle Einsatzstellen zeigen** passt den Ausschnitt an. Gleichbleibende Auftragsdaten, Polling und GPS verschieben den Ausschnitt nicht. Die Auftragsnummern und Luftlinien folgen den Serverrängen; GPS sortiert keine Aufträge um. Die Verbindungslinie ist ausdrücklich keine Route.
- Fehlende, nicht endliche oder außerhalb der gültigen Bereiche liegende Koordinaten erzeugen keinen Einsatzmarker. Adresse und Hinweis bleiben in Zielkarte und Liste erreichbar. Navigation verwendet dann die Adresse; fehlen beide, erscheint eine Erklärung ohne ausführbaren Navigationslink.
- Navigation, Öffnen, Zielauswahl, Standort und Zoomtasten haben mindestens 48 × 48 px. Smartphone und Tablet verwenden dieselben beschrifteten Aktionen. Auf breiten Tablets stehen Zielkarte und Karte nebeneinander; auf schmalen Ansichten untereinander. Die Legende steht unter der Karte und verdeckt keine Kartenbedienung.

## Daten, Abhängigkeiten und konkrete Stellen

| Stelle | Änderung / verwendete Abhängigkeit |
|---|---|
| `app/static/js/einheit_karte.js` | Bestehende Leaflet-/OSM-Integration; größere aktive Marker, gemeinsame Popups, Wiederverwendung unveränderter Marker, explizites Einpassen und Zielzentrierung, Zielhinweise als eigene Leaflet-Ebene. Gemeinsame Koordinatenprüfung und Navigation ausschließlich aus Zielkoordinaten beziehungsweise Adresse. |
| `app/static/js/einheit_kartenziel.js` | Lesende Auswahl und Laden der Zielhinweise über die vorhandene Detail-API; Antworten gegen aktuelle Auswahl prüfen; Offline-Hinweise im vorhandenen Outbox-Metaspeicher. |
| `app/static/js/einheit_modus.js` | Verknüpfung mit berechtigtem Zustand, Bereichswechsel und Polling. Sperren werden auch bei HTTP 304 des Auftragszustands aktualisiert. Standortbeobachtung beim Verlassen, Hintergrundwechsel und Zugangsende stoppen. |
| `app/templates/einheit/einheit.html`, `_einheit_styles.html` | Zielkarte, große Navigation, Gefahren-/Sperrentexte, Liste und Rückkehr; bestehende ECP-Komponenten und Symbole, versioniertes Modul, Touchgrößen einschließlich Leaflet. |
| `app/services/einheit_karten_service.py` | Vorhandene Sperren-Featureflags, `relevant_closures`, aktive und freigegebene Organisationsdaten sowie bestehende GeoJSON-Validierung. Additive Felder `geometry` und `strassensperren_status` im bisherigen Detail. |
| `app/routers/ui_einheit.py`, `einheit_service.py` | Bestehender Einheitenkontext und Auftragszugriff. Gefahren derselben Lage im vorhandenen 300-m-Bereich erhalten ID und Koordinaten; ungültige Einsatzkoordinaten werden auch im bestehenden GeoJSON-Endpunkt ausgeschlossen. |

Sperren werden wie bisher im 500-m-Zielbereich über die vorhandene Bounding-Box-Abfrage ermittelt; es entsteht kein zusätzliches Routing. Nur das gewählte Ziel lädt Details, nicht jeder Auftrag einzeln. Die Projektion enthält Titel, Einschränkung und validierte Geometrie, keine internen Sperren-Metadaten. Fehlerhafte Geometrie entfernt den bekannten Warntext nicht. Deaktiviertes Modul, fehlende Koordinaten und Abruffehler werden von einer erfolgreichen Abfrage ohne bekannte Sperren unterschieden.

## Rechte, Offline und Grenzen

Geräteprofil, Benutzer-/Simulationskontext und GK-/QR-Zugang behalten ihre bisherigen Grenzen. Die Karte übernimmt nur die eigenen Dispositionen aus dem berechtigten Zustand. Zielhinweise passieren erneut die vorhandene Auftragsprüfung. Fremde Aufträge bleiben 404; Sperren einer anderen Organisation erscheinen nur bei bestehender expliziter Freigabe. Gefahren bleiben an die eigene Lage gebunden. Die Auswahl lädt keine zusätzliche Sicht auf andere Einheiten oder deren Meldungen.

Der Zielhinweis-Cache liegt im vorhandenen profilgebundenen Outbox-Speicher unter Lage, Einheit und Auftrag. Offline-Fallback verlangt dieselben Zielkoordinaten; eine verlegte Einsatzstelle erhält keine Sperren der alten Position. Gefahren kommen aus dem aktuellen beziehungsweise erkennbar gespeicherten Gesamtzustand. Beim Zielwechsel werden alte Sperren sofort ausgeblendet. 401/403/404 verwenden keinen lokalen Ersatz und leeren die Zielhinweise. Verspätete Antworten dürfen eine andere Auswahl oder einen beendeten Zugang nicht wieder befüllen. Offline-Anzeige und unbekannte Zufahrt werden ausdrücklich gekennzeichnet.

**Mein Standort** ist die einzige Stelle, die die Browserfreigabe anfordert. Die Position bleibt im Arbeitsspeicher dieser Seite; sie wird weder in Outbox/Cache gespeichert noch an die ECP-API gesendet. **Standort aus**, Verlassen der Karte, Hintergrundwechsel und Zugangsende beenden die Beobachtung und entfernen die Anzeige. Späte Standortantworten werden verworfen. Eine verweigerte oder fehlende Freigabe lässt Karte und Zielnavigation bedienbar.

Die externe Navigation umgeht ECP-Sperren **nicht automatisch**. Zielkarte und Kartenfenster sagen das ausdrücklich; die Zufahrt muss anhand der Hinweise beziehungsweise mit der Einsatzleitung geklärt werden. Die Darstellung ermittelt keine sichere Fahrtroute. Kartengrundlage und externer Navigationsanbieter benötigen gegebenenfalls Netz; bereits bekannte Auftrags- und Zielhinweise ersetzen keine offline verfügbare Straßenkarte. Die bestehenden Sperren-Featureflags und Freigaben bleiben Voraussetzung.

## Regression

- API: aktive/eigene/geteilte Sperren gegen fremde, entfernte, abgelaufene und aufgehobene; Geometriefehler mit erhaltenem Warntext; fehlende/ungültige Koordinaten mit Adressfallback; Geräte-, GK- und QR-Grenzen.
- Browser: mehrere Aufträge bei 390/768/1.024 px; größere aktive Marker, Auftragsart/Priorität, Gefahren und Sperren; gleichzeitige Aufträge derselben Position; direktes Öffnen, Rückkehr, Popup bei unverändertem Polling und Touchgrößen. Standort nur nach Benutzeraktion, Ablehnung, Abschalten und Verlassen; keine Umsortierung oder Schreibaktion.
- Offline und Konflikte: gespeicherte Sperren nach Neustart; keine Übernahme vom anderen Ziel; verzögerte Antworten nach Zielwechsel; zurückgezogener Auftrag; Sperrenänderung trotz unverändertem Zustands-ETag.
- JavaScript: Koordinatengrenzen einschließlich Nullmeridian, sichere Navigationsziele, asynchrone Auswahl, falscher Cache-Kontext, veränderte Zielposition und verweigerter Zugriff. Bestehende Reihenfolge-, Art-, Status- und Outbox-Tests bleiben erhalten.

Neue Browserfälle stehen im vorhandenen Playwright-CI-Job. Die automatisierte Darstellung prüft Chromium mit Smartphone-/Tablet-Viewport; eine Abnahme auf physischen Geräten ist damit nicht behauptet.

Validierung am 2026-10-24: **415 Python-/Browserfälle** im breiten Einheiten-, Board-, Outbox-, Zugangs- und Sperren-Regressionslauf erfolgreich. Die abschließenden Layoutprüfungen bestanden mit **32 Browserfällen** für Karte/Übersicht und anschließend **10 Kartenfällen** einschließlich lesbarer Markerbeschriftungen. Zusätzlich bestanden **12 Service-Worker-Regressionen**, **120 JavaScript-Tests**, Ruff, mypy, JavaScript-Syntaxprüfung und `git diff --check`. Die Python-Läufe verwendeten getrennte Testdatenbanken über den bestehenden Worker-Namensraum. Es wurden keine neuen Migrationen angelegt.
