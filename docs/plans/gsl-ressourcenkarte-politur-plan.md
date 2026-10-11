# Plan: GSL-Ressourcenkarte – Rückbau & Politur

Umsetzung durch Codex **nach** Abschluss des Mail-Eingang-Features (M3–M6).

## Anforderungen
1. **Entfernen in der Oberfläche** (GSL-Einheit/Ressourcenkarte): Verband („Mit weiteren Einheiten zu Verband zusammenfassen“, „Einheit aufteilen“), Tabs/Blöcke **Personal** und **Ausstattung**, Block „Mannschaft und Ausstattung“ in der Übersicht, Personal/Ausstattung/Verband in der Schnellanlage. Services, Modelle, MCP und Migrationen bleiben unverändert (nur UI/Routen-Verlinkung entfernen; Tests anpassen, Daten bleiben erhalten).
2. **Abschnitt-Fehler**: Übersicht zeigt `<app.models.major_incident.Sector object at …>`. Ursache: `ressource_karte_service.py:262` liefert `einheit.sector` (Relationship-Objekt) statt des Namens → `einheit.sector.name if einheit.sector else None`. Alle weiteren Stellen prüfen (Journal, Kräfteübersicht, MCP, Druck), Regressionstest.
3. **Darstellung optimieren**: Karte/Seitenpanel ohne Überlappung oder abgeschnittene Inhalte (Tabs-Leiste umbrechen/scrollen, lange Werte umbrechen, Formularfelder volle Breite, Sticky-Header nicht über Inhalt, Mobil ≤ 480 px). Prüfung per Playwright-Screenshots (Desktop 1920/1280, Mobil 390).
4. **Button „QR anzeigen“ funktioniert nicht** (`_ressource_karte_zugang.html:144`, `static/js/ressourcen_zugang.js:122ff`, Route `/zugang/qr/vorschau`): Ursache ermitteln (Route/Statuscode, Handler nach HTMX-Swap, `zone.dataset.basis`, Fehlerbehandlung fehlt im `fetch`), beheben, Fehlermeldung bei Fehlschlag anzeigen, Test (Route liefert QR-SVG/Datenuri für Leader/Admin, 403 sonst; Playwright-Klick).
5. Abnahme: ruff, mypy, JS-Tests, volle Suite, Playwright; Wiki/CHANGELOG aktualisieren (Doku zu Personal/Ausstattung/Verband in der Oberfläche streichen).
