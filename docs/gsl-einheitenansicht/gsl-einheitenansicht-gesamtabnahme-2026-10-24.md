# ECP – Phase 6 / Auftrag 6.2: Gesamtabnahme

Stand: 2026-10-24. Geprüft: gemeinsame Arbeitsfassung der Phasen 1–6 auf Basis von `c2ef2454` (PR #718). Keine neuen Datenbankmigrationen. Automatische technische Abnahme; physische Geräte-/Produktionsfreigabe bleibt offen.

## Durchgängige Einsatzfolge

`tests/test_einheit_gesamtabnahme_ui.py` verwendet echte Templates, Alpine, Leaflet, IndexedDB, Service Worker und HTTP-APIs. Die EL disponiert/ändert mit getrenntem Client. Externe SMS/Pushs sind unterdrückt. Der Test-WebSocket liefert Ping/Pong; Reconnect lädt den Serverstand nach. Produktive WebSocket-Rechte prüfen separate Routertests.

| Schritte | Geprüftes Verhalten |
|---|---|
| 1–2: Drei Aufträge, alle ansehen | EL disponiert eine Erkundung und zwei Abarbeitungen. Vollständiges Öffnen erzeugt keinen Status-POST; alle bleiben zugewiesen. |
| 3: Zwei bestätigen | Zwei `bestaetigt`, dritter `zugewiesen`; noch keine primäre Einsatzstelle. Die EL-Reihenfolge bleibt erhalten. |
| 4: Erkundung durchführen | Bestätigen → Anfahrt → Vor Ort; die Stellenphase bleibt `erkundung`. Gefahren sind im Auftragskopf sichtbar. |
| 5: Gefahr mit Foto | Genau eine Lagemeldung und ein Foto zur Erkundungsstelle. Das Board hebt die akute Gefahr hervor. |
| 6: Ergebnis und Abschluss | Ohne aussagekräftiges Ergebnis bleibt die Abschlussaktion gesperrt. Mit Ergebnis wird der Auftrag `abgeschlossen`/„Erkundet“, die Stelle `bewertet`. |
| 7: Abarbeitung starten | Zweiter Auftrag über Anfahrt/Vor Ort zu `in_arbeit`; nur dieser ist primär aktiv. Board und Ressourcenampel zeigen den aktuellen Stand. |
| 8–9: Dringend und geändert | Neue Menschenrettung mit Priorität „Sofort“ und EL-Reihenfolge 1; anderer Auftrag mit geänderter Zufahrt und Version 2. Reconnect lädt beides nach, ohne den laufenden Auftrag automatisch zu wechseln. |
| 10–12: Offline und Wiederverbindung | Unterstützungsmeldung lokal speichern, echten Offline-Neustart ausführen, automatisch nach Wiederverbindung senden. Vorher kein Servereintrag und keine Erfolgsmeldung; danach genau eine bestätigte Meldung. |
| 13–14: Fertig und EL-Projektionen | Abarbeitung `abgeschlossen`, Stellenphase `fertig_gemeldet`; endgültiger Abschluss bleibt bei der EL. Die anderen Aufträge bleiben zugewiesen. Wichtige Meldungen bleiben zentral verfügbar und korrekt zugeordnet. |

Je Variante: **13 eindeutige Schreibaktionen**, darunter drei Lagemeldungen und ein Foto; keine Dubletten durch Doppeltippen. Ergänzende Tests prüfen Rückzug, Versions-/Statuskonflikte und verlorene Serverantworten.

Bestehende Fachlogik: Ressourcenstatus **Im Einsatz** kann nach einer Fertigmeldung bestehen bleiben. Fertige Dispositionen werden automatisch abgezogen; ihr Abschluss bleibt im Stellendetail/Verlauf erhalten.

## Zugänge und Darstellung

Matrix: **Geräteprofil, eingelöster GK-Link und QR-Zugang × Echteinsatz/Übung × 390 × 844 / 1024 × 768**. Hinzu kommen schreibende Übungs- und lesende Echtlagen-Simulationen: 16 Varianten. Fremde Dispositionen liefern 404; fremde Einheitenmeldungen bleiben im eigenen zentralen Verlauf ausgeschlossen.

Weitere Tests: Zugangsablauf/Widerruf, Profilwechsel, Objekt-/Plan-/Medienfreigaben, Mandantengrenzen, Rollen, Nachladen und Statuskonflikte. Mobile Prüfungen: lange Texte, mehrere Stellen, fehlende Koordinaten, 48-px-Touchflächen, Hell/Dunkel, Kontrast, Fokus und Eingabeerhalt. Start-/Detailscreenshots wurden ergänzend gesichtet.

## Befunde und Änderungen dieser Abnahme

- **F4 behoben:** Freitext verdrängte ohne Schnellmeldung strukturierte Gefahrenangaben. `ui_einheit.py` verbindet jetzt beide Inhalte vor der Längenprüfung. API-Test und zusätzlicher echter Offline-Neustart sichern den vollständigen einzelnen Servereintrag ab.
- **F13 behoben:** `ws.py` prüft den verbundenen Lagekanal mit frischer DB-Session erneut, auch nach 25 Sekunden ohne Nachricht. Widerruf, Benutzerdeaktivierung oder fehlender Organisations-/Rollenzugriff führen zu `zugang:widerrufen`, Close 4401/4403 und Broadcast-Abmeldung. Sieben neue Tests prüfen Geräte-/Führungsprofil und stille Clients. Die Einheitenansicht verwendet ihre bestehende Zugangssperre.
- **Veralteten Test korrigiert:** `test_gsl_zielbild.py` erfasst das seit Phase 4 notwendige Erkundungsergebnis. Ohne Ergebnis: HTTP 422, unveränderte Phase, kein Abschlussjournal.
- **CI ergänzt:** Gesamtabnahme im bestehenden Playwright-Job; GK-/QR-Testkonfiguration wird vor der Ausstellung persistiert. Keine Änderung der Statusmaschine.

| Komponenten der Phasen 1–6 | Zweck |
|---|---|
| `templates/einheit/*`, `einheit_modus.js`, `einheit_ablauf.js`, `einheit_bedienung.js` | Übersicht, einheitliche Auftragsarten, Hauptaktion, mobile Bedienung und Orientierung |
| `ui_einheit.py`, `einheit_service.py`, `resource_service.py` | Bestehende Rechte/Statusmaschine, Mehrfachbestätigung, Versionskonflikte und serialisierte Aktivierung |
| `einheit_vorschau_service.py`, `einheit_karten_service.py`, `einheit_karte.js`, `einheit_kartenziel.js` | Berechtigte vollständige Vorschau, Zielkarte, Gefahren/Sperren und Navigation |
| `site_log_service.py`, `einheit_verlauf_service.py`, `einheit_verlauf.js`, Boardrouter/-templates | Schnellmeldungen, nachvollziehbare Zuordnung, Verlauf und dringende Boardhinweise |
| `einheit_outbox.js`, `sw.js`, bestehender Metaspeicher | Ein vorhandener Übermittlungsweg, Offline-Neustart, UUID-Retry und getrennte lokale/Serverbestätigung |
| `routers/ws.py` | Erneute Berechtigungsprüfung im bereits verbundenen Lagekanal, einschließlich stiller Clients |

Pfadpräfixe: Python unter `app/`, JavaScript unter `app/static/js/`, Service Worker `app/static/sw.js`. Details und frühere Korrekturen stehen in [Bestandsanalyse](gsl-einheitenansicht-bestandsanalyse-2026-10-24.md), [Bedienkonzept](gsl-einheitenansicht-bedienkonzept-2026-10-24.md) und [Offline-Prüfbericht 6.1](gsl-einheitenansicht-offline-pruefung-2026-10-24.md).

## Testergebnisse

| Prüfgruppe | Ergebnis |
|---|---|
| Neue Gesamtszenarien: Zugänge, Echteinsatz/Übung/Simulation, Smartphone/Tablet | **16 bestanden** |
| Einheiten-/UI-, Planspiel- und MCP-GSL-Regressionen | **320 bestanden**, separate Gesamtszenarien ausgenommen |
| GSL-Board, Ressourcen, GK/QR, Zugriffstrennung, Sperren und Service Worker | **484 bestanden** |
| WebSocket-Router, Bus, Reconnect, Widerruf und Cleanup nach F13-Korrektur | **58 bestanden**, einschließlich sieben neuer Fälle |
| Meldungs-API/-Bedienung nach F4-Korrektur, einschließlich zusätzlichem Offline-Neustart | **62 bestanden** |
| JavaScript einschließlich Outbox, Karte, Bearbeitung und Verlauf | **131 bestanden** |
| Echte MariaDB: parallele Aktivierung/Bestätigung, UUID-Rennen und Verlauf | **7 bestanden** |

Prüfgruppen überschneiden sich; Zahlen nicht summieren. Insgesamt **17 neue Browserfälle**. Beide Produktfehler wurden zunächst durch fehlschlagende Tests reproduziert und anschließend gezielt erfolgreich nachgeprüft.

Ruff (`app/`, neue Tests), mypy (beide Router), JavaScript-Syntax und `git diff --check`: erfolgreich. Zusätzlicher Alttest-Lint: sieben bestehende Stilbefunde in `test_einheit_benutzer.py`/`test_einheit_tablet_zugang.py`, außerhalb des `app/`-CI-Lints. Testwarnungen: bestehende Python-/Shapely-Abkündigungen und SQLite-Cleanup; keine übersprungenen Abnahmeszenarien.

Isolierte Datenbanken/Medien; temporäre MariaDB mit bestehenden Migrationen bis `0307` wieder entfernt. Kernlauf mit installiertem Chromium (optional `ECP_PLAYWRIGHT_EXECUTABLE`):

```bash
PYTEST_XDIST_WORKER=ecp_abnahme .venv/bin/pytest -q tests/test_einheit_*.py tests/test_planspiel_gsl_*.py tests/test_mcp_gsl_*.py
PYTEST_XDIST_WORKER=ecp_ws .venv/bin/pytest -q tests/test_ws_*.py tests/test_*_ws.py tests/test_lage_ws_cleanup.py
node --test tests/js/*.test.mjs
```

MariaDB: `tests/mariadb/test_einheit_*.py` mit eigener Testdatenbank in `MARIADB_TEST_URL`. Bestehende Python-/Playwright-/MariaDB-CI bleibt erhalten.

## Verbleibende Einschränkungen

- **Geräte:** iOS/Safari, konkrete Android-WebViews/Fahrzeugtablets, Sonne, Handschuhe, Funklöcher, Kamera/Mikrofon und Energiesparen benötigen physische Versuche.
- **Offline:** Nur zuvor geladene Daten sind verfügbar. Entzogene Rechte werden bei Wiederverbindung erkannt. Lage-WebSocket-Prüfung erfolgt spätestens nach 25 Sekunden Stille; GK/QR weiterhin beim Client-Ping.
- **GK/QR:** Allgemeine EL-Notizen und fremde Einheiteninformationen bleiben gesperrt. Individuell adressierte Rückfragen brauchen ein gesondertes Berechtigungskonzept.
- **Führung/Navigation:** Externe Navigation übernimmt ECP-Sperren nicht automatisch. Schnellmeldungen ändern keine Priorität und erzeugen keinen Gefahrenmarker. Eine spätere normale Meldung ersetzt den dringenden Boardkartenhinweis; die frühere Gefahr bleibt im Verlauf.
- **Konflikte:** Versions-/Statuskonflikte und Rückzüge erfordern Prüfung. Zugangssperren erhalten unbestätigte lokale Daten. Bei verlorener Antwort kann eine Aktion bereits erfolgt sein; Retry mit derselben UUID klärt dies.
- **Transport:** Reale SMS/Pushs und produktive Mehrprozess-WebSockets bleiben gesondert abzunehmen; bestehende Versand-/Broadcasttests prüfen die Integrationsgrenzen.
