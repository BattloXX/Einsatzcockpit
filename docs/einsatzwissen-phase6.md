# Einsatzwissen – Phase 6: Einsatzoptimierte Benutzeroberfläche

## Bedienung und Umfang

Die Karten unter `/einsatzwissen` öffnen jetzt unmittelbar die kompakte
Einsatzansicht `/einsatzwissen/{plan_id}`. Sie zeigt für alle Leser einschließlich
Bearbeitern die aktuelle Freigabefassung. Arbeitsstände bleiben über die bisherige
Fassungsauswahl und den bestehenden Planeditor erreichbar.

Die Ansicht enthält sechs offene Bereiche mit direkten Sprunglinks:

1. **Sofortmaßnahmen**: Maßnahmen und Abläufe mit hoher Priorität oder aus einem
   Kapitel „Erstmaßnahmen“/„Sofortmaßnahmen“, einschließlich Unterkapiteln.
2. **Gefahren und Sicherheitshinweise**: Gefahrenarten, Gefahren-/Kontrollstellen,
   Hinweise mit Warntext oder Kategorie Sicherheit sowie Informationen aus
   Gefahren-/Sicherheitskapiteln.
3. **Weitere Maßnahmen**: übrige Maßnahmen und Abläufe.
4. **Dokumente**: Medienverweise, Plan-, Kapitel- und Maßnahmenanhänge sowie
   freigegebene PDF-Quellen. Die gemeinsame Dokumentfolge erlaubt Wechsel ohne
   Schließen des Viewers. Versions- und Seitenparameter bleiben erhalten.
5. **Kontakte und Sondereinsatzmittel**: Zuständigkeiten, Ressourcen und die
   vorhandenen, rechtegeprüften Verknüpfungen.
6. **Verwandte Informationen**: weitere Elemente, Plan-/Kapitelbeschreibungen,
   bestehende Beziehungen und direkter Zugang zum Führungswissen.

Jedes Element wird genau einem Bereich zugeordnet. Dokumente und Verknüpfungen
stehen zusätzlich gesammelt bereit. Innerhalb eines Bereichs steht hohe Priorität
zuerst. Es werden keine Maßnahmen erfunden oder Planinhalte verändert. Eine leere
Rubrik bedeutet fehlende Angaben, keine fachliche Entwarnung.

Die Ansicht verwendet ECP-Farben und Designvariablen, mindestens 48 Pixel große
Hauptbedienelemente und vollständig lesbare Maßnahmentexte. Die vollständige
Planansicht ist mit einem Klick erreichbar und verlinkt zur Einsatzansicht zurück.
Der bestehende Viewer behält Seitenwahl, Druck und Einsatzübernahme. Browser-Zurück
und die entsprechende Android-Geste schließen zunächst den Viewer; der Plan bleibt
geöffnet. Schließen und Escape stellen den Fokus wieder her.

## Schnellzugriff und Suche

Favoriten sind persönliche Referenzen auf bestehende Pläne. „Zuletzt verwendet“
zeigt weiterhin acht Einträge. „Häufig benötigte Einsatzpläne“ sortiert die acht am
häufigsten geöffneten Einsatz-/Katastrophenschutzpläne nach persönlicher Nutzung,
bei Gleichstand nach letztem Zugriff. Es werden keine globalen Beliebtheitswerte
oder erfundenen Empfehlungen angezeigt. Archivierte, nicht freigegebene oder nicht
mehr zugängliche Inhalte verschwinden aus allen Schnellzugriffen.

Die Übersicht verwendet die bestehende ECP-Plansuche und damit bei aktivierter
zentraler Suche deren Engine; der vorhandene SQL-Rückfall bleibt erhalten.
Titel, Kurzbeschreibung, Stichwörter, Kategorienamen und Gefahrenarten werden über
die Metadaten der sichtbaren Fassung gefunden. Maßnahmen, verknüpfte Objektbezeichnungen,
Dokumentinhalte und OCR-/PDF-Seiten verwenden die vorhandene Inhaltssuche.
Elementtreffer springen in der Freigabesicht direkt zum Element der kompakten
Ansicht, Seitentreffer zur vorhandenen Dokumentseite. Trefferausschnitte markieren
Suchbegriffe sicher. Die Begrenzung auf 100 Inhaltstreffer wird ausgewiesen.
Kategorie, Unterkategorie, Gefahrenart, persönliche Auswahl und Fassung sind
serverseitig kombinierbar. Auch Bearbeiter erhalten bei einer Freigabesuche nur
freigegebene Quellfassungen mit Versionsbezug.

## Offlinearchitektur und Schutzgrenzen

Bestandsprüfung: Der Service Worker speichert statische Assets, ausgewählte
Einsatz-/Objektansichten und Nachschlagewerke. Androids Objekt-Synchronisierung
hat einen eigenen Cache. Einsatzdokumente verlangen bereits bei jedem Abruf eine
aktuelle Berechtigung. Ein pauschaler Plan-/Viewer-Cache wäre deshalb ungeeignet.

Phase 6 ergänzt eine ausdrücklich ausgelöste **kurzzeitige Offlinefreigabe für
Kerntexte**. „Kerninhalte lokal bereithalten“ lädt ausschließlich die aktuell
freigegebenen, nicht vertraulichen Texte der ersten drei Bereiche. Auch Benutzer
mit Vertraulichkeitsrechten erhalten keine vertraulichen Offlineelemente.
Dokumente, Bilder, Kontakte, Objektstammdaten, Beziehungen, Übernahmen und Druck
bleiben online. Temporäre QR-Zugänge erhalten kein Offlinepaket.

Die Freigabe gilt für höchstens **15 Minuten**, begrenzt durch die konfigurierten
Sessionfenster, und für höchstens **20 Einträge bzw. 2 MB Klartext**. Der Browser
speichert ein AES-GCM-verschlüsseltes Paket in `localStorage`; der zufällige
256-Bit-Schlüssel liegt ausschließlich in `sessionStorage` des Tabs. Chiffretext
und Base64 benötigen zusätzlich Speicher. Ein neuer Browser-/Gerätestart ohne
Tab-Schlüssel kann den Bestand nicht öffnen. Eine andere aktive Tabsitzung kann
die einzelne lokale Freigabe ersetzen; andere Tabs verlieren dann ihren Schlüssel.
Dies ist kein dauerhafter Offline-Dokumentbestand.

Die Offlineoberfläche enthält selbst keine Benutzerdaten und wird als statisches
Asset vom vorhandenen Service Worker bereitgestellt. Sie kennzeichnet die Kopie
ausdrücklich als möglicherweise veraltet und zeigt Version, Synchronisationszeit,
Ablaufzeit, Speicherbedarf und fällige Inhaltsprüfung an. Abgelaufene Freigaben,
zurückgestellte Gerätezeit oder beschädigte Chiffretexte sperren die Anzeige.
Kontowechsel, Logout, Rollen-/Modul-/Gerätekontextwechsel löschen bzw. sperren
vorherige Inhalte; Speicheränderungen erreichen andere offene Tabs.

Beim Öffnen, bei Wiederverbindung und alle 60 Sekunden während einer Verbindung
werden die gewählten Einträge vollständig neu mit aktuellen Serverrechten geprüft.
Fehlt nach einer Offline-Neunavigation das Online-Ereignis des Browsers, versucht
die Oberfläche die Wiederverbindung zusätzlich alle fünf Sekunden.
Eine neue Freigabe ersetzt die lokale Fassung. Verweigerte Zugriffe, Archivierung,
fehlende Einträge oder eine Loginweiterleitung verwerfen das Paket. Nur ein
Verbindungsfehler erlaubt die Verwendung bis zur ursprünglichen Ablaufzeit.
Der Service Worker verwendet für `/einsatzwissen`, `/plaene` und die alte
Planadresse ausschließlich das Netz; er liefert keine früheren HTML- oder
Dokumentkopien als Berechtigungsersatz aus.

**Unvermeidbare Grenze:** Ein Rechteentzug oder ein neues Release während einer
vollständigen Netzunterbrechung kann erst bei Wiederverbindung erkannt werden.
Die zuvor ausdrücklich erteilte Offlinefreigabe endet spätestens nach 15 Minuten;
sie ist keine Zusage eines sofortigen Offlinewiderrufs. Der Browser ist kein
Schutz gegen einen kompromittierten Benutzeraccount oder manipulierte Clientsoftware.

## Migration und Betrieb

Alembic **0310**, Vorgänger **0309**, ergänzt ausschließlich `favorit` und `aufrufe`
in der vorhandenen Tenanttabelle `katplan_nutzung`. Bestehende Verläufe bekommen
einen initialen Aufruf; neue Zeilen ohne Öffnung starten bei null. Planinhalte,
Dokumente und Freigabesnapshots bleiben unverändert. Die vorhandene Registrierung
für Organisationsexport/-import übernimmt auch diese Spalten.

Vor dem regulären Deployment Datenbank sichern und Migration im Staging anwenden.
`downgrade 0309` entfernt nur die neuen persönlichen Präferenzen/Zähler; der letzte
Zugriffszeitpunkt bleibt erhalten. Der Service-Worker-Assetcache steigt auf `ec-v21`.
Diese Umsetzung führt keine Produktivmigration, keinen Push und kein Deployment aus.
Phase 7 ist nicht Bestandteil dieser Änderung.

## Prüfung und verbleibende Geräteabnahme

Automatisierte Fachtests prüfen Bereichsreihenfolge, Freigabesicht, Vertraulichkeit,
Favoriten und Nutzung pro Benutzer/Organisation, Suchbegriffe/Filter/Sprungziele,
Offlineumfang und Ablaufgrenzen, Archiv-/Modulsperre sowie Migration mit Altbestand,
Rückbau und erneutem Upgrade. Die bestehenden Plan-, Such-, Viewer-, Druck-,
Übernahme-, Rechte- und Export-/Importtests dienen als Regression.

Chromium prüft Smartphone (390×844, Touch), Tablet (1024×768, Touch) und Desktop
(1440×900), zusätzlich die vorhandenen Größen 360×800 und 768×1024. Das sind
Browseremulationen auf dem vorhandenen Linux-Chromium, **keine physischen Android-
oder Windows-Geräte**. Geprüft werden Bereichswechsel, Favoriten, Dokumentfolge,
PDF-Seitensprung, Seitenauswahl/Druckkontext, Einsatzübernahme und Rücknavigation.
Ein lokaler HTTP-Test mit echtem Service Worker prüft vollständige Netztrennung,
Öffnen und Neuladen der Offlineoberfläche, verweigerten Plan-Cache-Fallback und
Rechteentzug bei Wiederverbindung.

Für die Geräteabnahme dieselben Abläufe auf Android-Tablet, Android-Smartphone
und Windows-Browser ausführen. Besonders System-Zurück, Betriebssystemdruckdialog,
angeschlossenen Drucker, Lichtverhältnisse und die verwendete Android-WebView-Version
manuell prüfen. Eine physische Geräte-/Papierabnahme ist in der Entwicklungsumgebung
nicht möglich und wird nicht als bestanden ausgewiesen.

Reproduzierbare Hauptprüfungen:

```sh
PYTEST_XDIST_WORKER=phase6 .venv/bin/pytest -q tests/test_einsatzwissen*.py \
  tests/test_katplan*.py tests/test_mcp_katplan*.py tests/test_suche*.py \
  tests/test_dokumentviewer*.py tests/test_dokumentdruck*.py tests/test_einsatz_dokumente*.py
npm run test:js
.venv/bin/ruff check app/ tests/test_einsatzwissen*.py alembic/versions/0310_einsatzwissen_schnellzugriff.py
.venv/bin/mypy app/ --ignore-missing-imports
git diff --check
```

Für ein vorhandenes Chromium `ECP_PLAYWRIGHT_EXECUTABLE` auf dessen ausführbare
Datei setzen. Die neuen Browserfälle sind im bestehenden Playwright-CI-Schritt enthalten.

Ergebnisse am 25.10.2026 (überlappende Läufe):

- Breite Regression: **1.053 bestanden** für Planverwaltung, MCP, Suche, Rechte,
  Dokumente/Druck, Organisationsexport/-import und strukturelle Prüfungen.
- Abschließende Fachprüfung nach Such-/Schnellzugriffsanpassungen: **80 bestanden**,
  einschließlich beider Suchengines, Freigabequellen, Offlinepaketen und Migration.
- Abschließende Browserprüfung: **33 bestanden**, einschließlich echter
  Service-Worker-Netztrennung, Wiederverbindung/Entzug, Ablauf und Kontowechsel.
- JavaScript: **135 bestanden**. Ruff ohne Befund; mypy ohne Fehler in
  **520 Quelldateien**; Syntaxprüfung und `git diff --check` erfolgreich.
- Migration 0310 mit Altbestand, Rückbau und erneutem Upgrade unter SQLite
  geprüft; Alembic meldet genau einen Head `0310`. Keine Produktivmigration.
