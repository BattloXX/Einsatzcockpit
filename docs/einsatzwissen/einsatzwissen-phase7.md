# Einsatzwissen – Phase 7: Abschlussbericht

Stand: 25. Oktober 2026. Die [Gesamtdokumentation](einsatzwissen.md) beschreibt
Bedienung, Fachmodell, Architektur, Freigaben, KI, MCP und Betrieb des gesamten Moduls.

## Implementierte Funktionen

- Durchgehender PDF-Scrollbereich mit Seitenanzeige, Seitensprung, laufender
  aktueller Seitenauswahl, Zoom, Rotation und vorhandener Dokumentfolge.
  Seitengrößen werden vor der Darstellung bestimmt; Canvas-Flächen werden nur
  in Bildschirmnähe gerendert und außerhalb wieder freigegeben.
- Mehrere Anhänge je Maßnahme bleiben unterstützt. Neue Bibliothekszuordnungen
  verwenden dieselben Originalbytes auf Maßnahmen-, Kapitel- oder Planebene,
  auch zwischen Wissenseinträgen derselben Organisation.
- SHA-256-Abgleich neuer Anhänge und vorhandener PDF-Quellen. Identische
  Anhangsuploads benötigen keine neue Datei/Thumbnail/Quote. Identische Quellen
  desselben Plans mit unveränderten Metadaten werden wiederverwendet.
- Sortierung, gezielter Dateiaustausch, Quellseitenbereiche, Originalansicht,
  Prüfdatum, Gültigkeitsende, Prüfintervall und zuständige aktive Person.
- Prüffristen einschließlich Monatsenden, Warnung bei fälliger Dokumentprüfung
  und abgelaufener Gültigkeit. Die vorhandenen Plan-Wiedervorlagen bleiben erhalten.
- Versionierte Dokumentmetadaten, unveränderte historische Freigaben,
  Änderungsprotokolle, Konfliktschutz und referenzbewusste Speicherzählung/Löschung.
- PDF-Originale bleiben unverändert. Bestehende Seitenzahl-, Text-, OCR- und
  Seitenreferenzpipeline wird weiterverwendet; Lesezeichen und erkennbare
  Überschriften ergänzen Seitentitel.
- Bestehender KI-Import mit explizitem Review und Entwürfen bleibt erhalten.
  Ein neuer lokaler, extraktiver Quellenüberblick ergänzt die MCP-Seitenwerkzeuge.
- Neue Webformulare für Dokumentreferenzen, Pflege und Austausch, einschließlich
  mobiler Bedienung. Lange Editortitel umbrechen jetzt innerhalb der Bildschirmbreite.

## Geänderte Komponenten

| Bereich | Komponenten |
| --- | --- |
| Datenmodell | `app/models/katplan.py`, Migration `0311_einsatzwissen_pflege.py` |
| Dokumentpflege | `katplan_anhang_service`, `katplan_quelle_service`, `media_service`, `storage_service` |
| Web | `ui_katplan`, Anhangsvorlagen, gemeinsame Plan-CSS, `katplan_editor.js` |
| Viewer | `dokumentviewer.js`, `dokumentviewer.css`, Assetcache `ec-v22` |
| MCP | `mcp/tools/einsatzwissen.py`, `mcp/server.py`, bestehende Plan-Schreibwerkzeuge und MCP-Datenaufbereitung |
| Dokumentation | `docs/einsatzwissen/einsatzwissen.md`, dieser Bericht, README-Verweise |
| Prüfungen | neue Phase-7-Fachtests, Scroll-/Pflege-Browsertests, MariaDB-Referenz-/Konflikttest, bestehende Regression |

Die neuen Dokumentfunktionen verwenden die vorhandenen Planstatus und
Freigabeservices. `in_ueberarbeitung` bleibt der verbindliche Zustand; es wird
kein paralleler Workflow aufgebaut. Weder KI noch MCP erhalten eine automatische
Freigabe sicherheitsrelevanter Maßnahmen.

## Datenbankänderung

Alembic **0311**, Vorgänger **0310**, ergänzt fünf nullable Spalten an
`katplan_anhang`: `datei_hash` mit Index, `pruefdatum`, `gueltig_bis`,
`pruefintervall_monate`, `zustaendig_user_id` mit Benutzer-FK und `SET NULL`.
`katplan_dokument` bekommt `datei_hash` mit Index.

Keine zusätzlichen Inhaltstabellen, keine Datenkopie und keine Umstellung von
Planarten, Status oder alten Freigabesnapshots. Alte Dateien bleiben am bisherigen
Ort. Die gemeinsame Dateiverwendung entsteht durch neue Referenzen und neue
Uploads. Bestandsdaten werden nicht stillschweigend bereinigt.

SQLite prüft Altbestand, Upgrade, Downgrade und erneutes Upgrade der Migration.
Separate temporäre MariaDB-Container mit **10.11** und **12.3** prüfen jeweils die
vollständige historische Migrationskette bis 0310, eingefügten Altbestand,
Upgrade 0311, Rückbau 0310 und erneutes Upgrade. Es wurde keine vorhandene oder
produktive Datenbank dafür verwendet.

## MCP-Erweiterungen

Neun neue Werkzeuge sind in Registry und HTTP-Server verfügbar:

| Werkzeug | Verhalten |
| --- | --- |
| `einsatzwissen_kategorien` | Sechs Kategorien mit Codes |
| `katplan_dokumentbibliothek` | Berechtigte wiederverwendbare Dateien/Quellen für Bearbeiter |
| `katplan_dokument_referenzieren` | Zuordnung ohne Dateikopie, mit Planversion und optionalem Seitenbereich |
| `katplan_dokument_pflegen` | Sortierung und Prüfangaben im Arbeitsstand; Whitelist und Versionsschutz |
| `katplan_pruefstatus` | Planprüfdaten und sichtbare Dokumentprüfangaben aus der gewählten Fassung |
| `katplan_dokument_zusammenfassung` | Lokale, begrenzte Quellenauszüge mit Seitennummern und Kürzungshinweis |
| `einsatz_dokument_zuordnen` | Bestehende Web-Übernahmeprüfungen für aktive Einsätze/Lagen |
| `einsatz_dokumente_auflisten` | Sichtbare Einsatzfassungen mit Herkunft, Seiten und Revision |
| `dokumentdruck_vorbereiten` | Bestehende geprüfte Druckauswahl als benutzergebundene PDF; kein Druckauftrag |

Bestehende Such-, Plan-, Maßnahmen-, Seiten-, Entwurfs-, Verknüpfungs- und
Prüfwerkzeuge bleiben kompatibel. Schreibende Funktionen verwenden dieselben
Fachservices und Ziel-/Freigabeprüfungen wie die Weboberfläche. Bei Dokumentpflege
können weder Status noch Freigaben als beliebige Felder gesetzt werden.

## Sicherheitsprüfungen

Die automatisierte Abnahme prüft insbesondere:

- Organisationstrennung, fehlende Rollen und deaktivierte Module, einschließlich
  HTTP-Werkzeugliste und verweigerter MCP-Aufrufe nach Modulabschaltung.
- Arbeitsstand gegenüber freigegebenen Versionen und historischen Dateireferenzen.
- Menschliche Freigabe und unveränderliche Einsatzfassungen; kein MCP-/KI-Auto-Publish.
- Vertrauliche Quellen/Elemente sowie zusätzliche Schreibrechte am Zieleinsatz.
- Fremde oder inaktive zuständige Personen, unbekannte Schreibfelder, ungültige
  Daten und Intervalle, Versionskonflikte und Datenbankrollback bei Fehlern.
- Verlustfreie Seitenbereiche, verschachtelte Auszüge, Originalzugriff, fehlende/
  beschädigte Dateien, begrenzte lokale Auszüge und Abweisung externer Druck-URLs.
- Speicherverbrauch und Löschen mehrfach referenzierter Dateien. MCP-Trockenläufe
  entfernen ausschließlich neu erzeugte Dateien, niemals wiederverwendete Originale.
- Reale MariaDB-Sperren mit zwei Sessions: Ein veralteter Arbeitsstand kann nach
  einer committed Änderung nicht mit der alten Planversion weitergeschrieben werden.

Neue Webformulare übertragen die Planversion auch bei Upload/Entfernen.
Mehrfachuploads übernehmen nach jedem erfolgreichen Upload die aktualisierte
Version aus der zurückgegebenen Oberfläche. Alte Clients ohne diesen optionalen
Parameter bleiben kompatibel; neue MCP-Pflege verlangt die Version zwingend.

## Testergebnisse

- **Vollständige Python-/Chromium-Regression:** 6.384 bestanden, 10 übersprungen,
  keine Fehler; 566 Sekunden auf vier xdist-Workern. Umfasst alle Tests unter
  `tests/`, darunter Einsatz-/Katastrophenpläne, KI-Import, Suche, MCP, Dokumente,
  Druck, Berechtigungen, Organisationsexport/-import und bestehende Einsatzabläufe.
- **Separat ausgeführte umgebungsabhängige Prüfungen:** 10 MariaDB-Integrationstests
  auf MariaDB 12.3 bestanden, einschließlich Vektorsuche, FULLTEXT und neuem
  Zwei-Session-Konflikttest. Zusätzlich bestand die ausdrücklich aktivierte
  Kontakt-Druck-Browserprüfung. Die im Gesamtlauf ausgelassenen neun bisherigen
  Datenbankfälle und der optionale Browserfall wurden damit abgedeckt.
- **Dokument-/Druckregression:** 101 bestanden, einschließlich Browserdialogen,
  Seitenauswahl, Druckqualität, Einsatzfassungen und Service-Worker-Prüfungen.
- **Ergänzende Abnahme:** 97 bestanden für neue Wissenspflege, Viewer, Smartphone/
  Tablet/Desktop, Quellimport, historische Freigaben, Dateilöschung und bestehende
  Maßnahmenübernahme. Ein weiterer gezielter Fachlauf bestand mit 136 Tests.
- **Letzte Nachprüfung nach allen Fachänderungen:** 55 bestanden, einschließlich
  der 22 neuen Phase-7-Fachfälle, 12 Viewer-/Pflege-Browserfälle und bestehender
  Anhangsregression. Fehlende bzw. beschädigte Originale erzeugen keine halbe Zuordnung.
- **JavaScript:** 135 bestanden. Vendor-Abgleich: alle 259 Dateien stimmen mit
  den eingebundenen npm-Paketen überein.
- **Statische Prüfung:** Ruff für die gesamte Anwendung, neue Tests und Migration
  ohne Befund; mypy ohne Fehler in 521 Quelldateien; beide JavaScript-Syntaxprüfungen
  und `git diff --check` erfolgreich.
- **Migration:** SQLite mit Altbestand und Rückbau erfolgreich; vollständige
  Migrationsketten und Upgrade/Downgrade/Upgrade auf separaten MariaDB 10.11 und
  12.3 erfolgreich. Genau ein Alembic-Head `0311`.

Die Prüfläufe überlappen; ihre Zahlen werden nicht als unabhängige Tests addiert.
Nach den letzten Ergänzungen an Konfliktbehandlung, modernen Uploadformularen,
Fehlerbehandlung und mobiler Titelbreite wurden die betroffenen Fach- und
Browserfälle nochmals separat ausgeführt. Vorhandene Deprecation-/SQLite-FK-
Zykluswarnungen erscheinen weiterhin in den Laufprotokollen; sie sind keine
fehlgeschlagenen Prüfungen.

Der erste vollständige Lauf machte veraltete Tests sichtbar: bisheriger
Modulname, alter Cache-Stand, Modellvergleiche historischer Migrationen und ein
Objekt-Isolationstest mit Abhängigkeit von vorherigen Tests. Diese Erwartungen
bzw. Testvorbereitungen wurden korrigiert. Fehlerfalltests für unabhängige Dateien
verwenden jetzt verschiedene Originalbytes; unveränderte Seitentexte bleiben für
Druck-/Extraktionsprüfungen gleich. Damit wird die neue Deduplizierung gezielt
geprüft, ohne Auszüge durch geänderte Testtexte zu verfälschen.

Reproduzierbare Prüfungen:

```sh
# Vollständige Suite; Chromium lokal ggf. über ECP_PLAYWRIGHT_EXECUTABLE wählen
.venv/bin/pytest tests/ -n 4 -q --durations=15

# Neue Abnahme einschließlich Smartphone/Tablet/Desktop
.venv/bin/pytest -q tests/test_einsatzwissen_phase7.py tests/test_dokumentviewer_ui.py

# Echte migrierte Test-MariaDB mit VECTOR-Unterstützung, ausschließlich eigene Test-DB
MARIADB_TEST_URL='mysql+pymysql://…/testdatenbank' .venv/bin/pytest -q tests/mariadb/

npm run test:js
npm run vendor:check
.venv/bin/ruff check app/ tests/test_einsatzwissen_phase7.py \
  tests/test_dokumentviewer*.py tests/mariadb/test_einsatzwissen_pflege.py \
  alembic/versions/0311_einsatzwissen_pflege.py
.venv/bin/mypy app/ --ignore-missing-imports
node --check app/static/js/dokumentviewer.js
node --check app/static/js/katplan_editor.js
git diff --check
```

Die erste Pull-Request-Browser-CI verwendete Chromium Headless Shell 156 und
scheiterte nach erfolgreichem Favoriten-POST an der über `route.fulfill`
vermittelten Formularweiterleitung. Der Fehler wurde mit denselben Bibliotheks-
und Browserversionen lokal reproduziert; reguläres Chromium führte dieselben
unveränderten Tests erfolgreich aus. Die gemeinsame Browserfixture verwendet
nun mit `channel="chromium"` reguläres Chromium im Headless-Modus, entsprechend
der lokalen Geräteabnahme. Produktcode und Testschritte bleiben erhalten.

## Bekannte Grenzen und Betrieb

- Die lokale Ollama-Infrastruktur liefert Embeddings. Generativer Import verwendet
  weiterhin den gesondert aktivierbaren bestehenden Anthropic-Dienst. Ein lokales
  generatives Modell, freie generative Dokumentfragen und automatische fachliche
  Verknüpfungsvorschläge werden nicht als implementiert ausgewiesen.
- Der neue Quellenüberblick ist ein extraktiver Überblick: maximal 20 Seiten und
  600 Zeichen je Seite, mit ausdrücklichem Kürzungshinweis. Der bestehende
  strukturierte KI-Import analysiert bis zu 40 Seiten pro Lauf. Fehlende Texte
  erfordern OCR bzw. Originalprüfung.
- Überschriftenerkennung ist heuristisch. Fachliche Bewertung und Sicherheitsprüfung
  bleiben Aufgabe verantwortlicher Menschen.
- Prüfangaben gelten je Dokumentzuordnung. Andere freigegebene Zuordnungen werden
  durch eine Pflegeänderung nicht automatisch umgeschrieben. Dokumentbezogene
  Erinnerungsnachrichten sind nicht ergänzt; Plan-Wiedervorlagen bleiben erhalten.
- Alte Dateien werden nicht automatisch gehasht/zusammengeführt. Neue Quellen
  unterschiedlicher Pläne bleiben eigenständige Quellversionen; für gemeinsame
  Nutzung ist die dokumentierte Referenzfunktion vorgesehen.
- Ein separater Code-Rollback muss die referenzbewusste Lösch- und Speicherlogik
  beibehalten, solange gemeinsame Dateien existieren. Eine ältere Anwendung könnte
  andernfalls eine noch referenzierte Datei entfernen. Migration-Downgrade und
  Code-Rollback sind deshalb keine gleichbedeutenden Betriebsaktionen.
- Browserabnahme ist Chromium-Emulation. Physische Android-/Windows-Geräte,
  Betriebssystemdruckdialog und tatsächlicher Papierdruck benötigen Geräteabnahme.
- Bereits vorhandene separate Änderungen an Druckagent-Websocket und dessen
  Tests werden nicht Bestandteil des Phase-7-Commits.

Abschluss erfolgt mit einem lokalen Git-Commit. Kein Push, keine Produktivmigration
und keine Produktivbereitstellung; diese benötigen eine gesonderte Freigabe.
