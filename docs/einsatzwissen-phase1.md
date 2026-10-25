# Einsatzwissen – Phase 1

## Bestandsaufnahme und Wiederverwendung

| Bereich | Vorhandene Komponenten | Verwendung in Phase 1 |
| --- | --- | --- |
| Einsatz- und Maßnahmenpläne | `KatplanPlan`, `katplan_typen.PLANARTEN`, `katplan_service`, `/plaene` | Dieselben Datensätze, IDs, Planarten, Kapitelvorlagen und Phasen; Kategorie unabhängig von der technischen Planart |
| Katastrophenpläne | Dieselbe Planverwaltung, Gefahrenarten, Karte/Geometrien, Akteure, Verständigungslisten, Einsatzübernahme | Vollständig erhalten; automatische fachliche Zuordnung zu Katastrophenschutz |
| Führungswissen, Fachwissen und Lehrbehelfe | Kein eigenständiges Inhaltsmodell; bestehende Info-, Ablauf-, Maßnahmen-/Checklisten- und Medienelemente können diese Inhalte abbilden | Kategorien derselben Planverwaltung; Erfassung über bestehenden Editor und Freigabeprozess |
| Nachschlagewerk | `ui_nachschlagewerke`, `gefahrgut_service`, `rettungskarten_service`, `RettungskartenKatalog`, `RettungsdatenblattCache`, Browser-/Service-Worker-Cache | Verlinkung von Gefahrgut, UN-Nummern und Rettungskarten; bestehende Routen, globale Kataloge, Offlinefunktionen und Modulschalter bleiben |
| Sondereinsatzmittel | Kein separates Wissensmodell; `VehicleMaster`, Wasserstellen und Objektstammdaten; Elementtyp `ressource` mit Materialbedarf/Lagerort; `KatplanLink` | Wissenseinträge mit Kategorie Sondereinsatzmittel, vorhandene Ressourcenverweise ohne Stammdatenkopien |
| Dokumentenverwaltung | `KatplanAnhang`, `KatplanDokument`, `KatplanDokumentSeite`, `katplan_anhang_service`, `katplan_quelle_service`, PDF-/OCR-Pipeline, `quelle_seiten`; Verweise auf `ObjektDokument` | Upload, Quellseiten, Seitenbereiche, Anhänge, Vertraulichkeit und unveränderliche Versionsreferenzen werden weiterverwendet |
| Objektverwaltung | `Objekt`, `ObjektDokument`, `ObjektDokumentSeite`, eigene Status-/Freigaberegeln und Offline-Synchronisation | Bestehende Links/Seitenverweise und Zugriffsprüfung; keine Übertragung oder Duplizierung von Objektdaten |
| Beziehungen | `KatplanRelation` über Element-UUIDs, auch zwischen Plänen; `KatplanLink` zu Kontakt, Kontaktorganisation, Objekt, Fahrzeug und Wasserstelle | Bestehender Beziehungseditor und Rechteprüfung bleiben |
| Suche | `katplan_suche_service`, `/plaene/suche`, Suchtext und Freigabeelemente, zentrale Suche unter `services/suche` mit Index/Outbox/Resolver | Übersicht ergänzt Suche nach Metadaten und verwendet dieselbe Suche für Maßnahmen und Dokumentseiten; erweiterte Suche bleibt erreichbar |
| MCP | `mcp/tools/katplan.py`, `katplan_schreiben.py`, `suche.py`, Objekt-/Dokumentwerkzeuge | Werkzeugnamen, Parameter und bestehende Rückgabefelder bleiben; Metadaten ergänzen Plankopf und Listen; bestehende Schreibwerkzeuge nehmen Metadaten optional an |
| Rollen/Freigaben | `katplan_zugriff`, `core/permissions`, `KatplanVersion`, `KatplanVersionElement`, `KatplanAenderung`, `KatplanPruefung` | Unveränderte Berechtigungscodes, Tenantgrenzen, Vertraulichkeit und unveränderliche Freigaben; UI-Bezeichnungen aktualisiert |
| Favoriten/Verlauf | Keine wiederverwendbare Favoritenfunktion oder persönlicher Planverlauf gefunden | Keine neue Favoritenverwaltung; persönlicher letzter Zugriff als Referenz auf vorhandene Pläne ergänzt |

## Bedienung

Der Moduleinstieg heißt **Einsatzwissen** und liegt unter `/einsatzwissen`.
Die sechs Kategorien sind Einsatzpläne, Führungswissen, Fachwissen & Lehrbehelfe,
Katastrophenschutz, Sondereinsatzmittel und Nachschlagewerk. Unterkategorien sind
optionale Freitexte und werden als Filter angeboten. Bestehende Einsatz- und
Maßnahmenpläne erscheinen ohne Datenänderung unter Einsatzpläne, Katastrophenpläne
unter Katastrophenschutz. Eine ausdrücklich gewählte Kategorie hat Vorrang.

Die Übersicht startet mit freigegebenen Inhalten. Berechtigte Bearbeiter können
auf Arbeitsstände umschalten. Alle bisherigen Pläne einschließlich Archiv bleiben
über `/plaene` zugänglich. Neue Wissenseinträge verwenden denselben Editor;
die technische Planart und ihre Vorlagen bleiben wählbar. Für neue Einträge aus
der Übersicht wird ein Maßnahmenplan vorausgewählt, für Katastrophenschutz ein
Katastrophenplan. Die fachliche Kategorie kann unabhängig davon gewählt werden.

Die Übersicht durchsucht Titel, Unterkategorie, Schlagwörter und Kurzbeschreibung
sowie die bestehende Maßnahmen-/Dokumentseitensuche. Die Trefferbegrenzung der
bestehenden Inhaltssuche bleibt bestehen (100); die erweiterte Suche erlaubt die
weitere Eingrenzung. Gefahrgut und Rettungskarten behalten ihre jeweiligen
Spezialsuchen. Der Suchbegriff wird beim Öffnen der Gefahrgutsuche übernommen.

„Zuletzt verwendet“ zeigt bis zu acht aktuell zugängliche Einträge des angemeldeten
Benutzers in seiner Organisation. Ein Zugriff auf die Plandetailseite aktualisiert
diesen Verlauf. Titel und Metadaten werden bei jedem Abruf erneut aus der sichtbaren
Fassung bezogen. Archivierung oder entzogene Freigaben entfernen Inhalte damit
auch aus der Verlaufsanzeige. Andere Benutzer und Organisationen sehen diesen
Verlauf nicht. Die Übersicht funktioniert ohne JavaScript; ihre Karten und
Kategorielinks passen sich Smartphone- und Tabletbreiten an.

## Datenmodell und Freigabeschutz

Nur vier optionale Felder werden in `katplan_plan` ergänzt:

- `kategorie` (30 Zeichen), `unterkategorie` (100 Zeichen)
- `schlagwoerter_json` (höchstens 20 Schlagwörter mit je 50 Zeichen)
- `kurzbeschreibung` (500 Zeichen)

Gefahrenarten, Beschreibung, Maßnahmen, Dokumente/Seiten, Beziehungen, Status,
Freigabeversionen, Prüftermin/Intervall, Prüfverantwortlicher, verantwortlicher
Kontakt und Geltungsbereich existieren bereits. Dafür werden keine weiteren
Stammdatenfelder angelegt. `KatplanNutzung` speichert ausschließlich Benutzer-,
Organisations- und Planreferenz mit letztem Zugriffszeitpunkt; pro Kombination
existiert höchstens eine Zeile. Die Tabelle ist in der Tenant-Registrierung
enthalten und wird vom generischen Organisationsexport erfasst.

Die neuen Metadaten werden Bestandteil zukünftiger Freigabesnapshots und des
Inhaltshashs. Änderungen setzen freigegebene Pläne über den bisherigen Service
in Überarbeitung und werden feldgenau protokolliert. Leser sehen auch während
einer Überarbeitung ausschließlich Metadaten des letzten Freigabesnapshots.
Historische Snapshots ohne neue Felder erhalten ihre Kategorie ausschließlich
bei der Anzeige aus ihrer damaligen Planart; sie werden nicht umgeschrieben.

MCP verwendet weiterhin `katplan_*`. Neue optionale Schreibfelder sind `kategorie`,
`unterkategorie`, `kurzbeschreibung`, `schlagwoerter` (kommagetrennter Text).
Alte UI-/API-Aufrufe ohne diese Felder erhalten die vorhandenen Metadaten.
Ausdrücklich leere Felder löschen die jeweilige optionale Angabe.

## Rollen und Modulschalter

Die Codes `katplan_verwalter` und `katplan_freigeber` bleiben unverändert.
Bearbeiten/Freigeben/Lesen und vertrauliche Inhalte verwenden dieselben Rollenmengen
wie bisher. System-/Organisationsadministratoren behalten ihre bestehenden Rechte.
Es gibt keine neue, pauschale Einsatzwissen-Berechtigung.

`katplan_module_enabled` und `OrgSettings.katplan_modul_aktiv` steuern weiterhin die
Planinhalte. Das Nachschlagewerk behält seine beiden bisherigen Schalter und seine
Leserollen (einschließlich `fahrtenbuch_admin`). Der gemeinsame Einstieg ist verfügbar,
wenn mindestens einer dieser Bereiche zugänglich ist. Ein deaktivierter Bereich
wird durch die Zusammenführung nicht freigeschaltet. Die bisher getrennten
Navigationseinträge werden zu einem Moduleintrag zusammengeführt.

## Migration und Betrieb

Alembic **0308**, Vorgänger **0307**, ergänzt ausschließlich nullable Spalten sowie
die neue Verlaufstabelle. Kein Plan, Element, Dokument, Freigabesnapshot, Schlüssel,
Dateipfad oder Berechtigungscode wird geändert oder gelöscht. Es gibt keinen
Datenbackfill und keine automatische inhaltliche Umklassifizierung des Bestands.

Vor einem späteren, separat freizugebenden Produktiveinsatz:

1. Datenbank und Medien über die bestehenden Backupverfahren sichern.
2. Migration zuerst auf einer aktuellen Staging-Kopie ausführen (`alembic upgrade head`).
3. Plananzahlen, IDs, Dokumentzugriffe, Freigaben und Zugänge der Organisationen prüfen.
4. Erst danach Anwendung und Migration im regulären Deployment gemeinsam ausrollen.

`alembic downgrade 0307` entfernt ausschließlich die neuen Spalten und den Verlauf;
ursprüngliche Planinhalte bleiben erhalten. Dabei würden neu erfasste Metadaten und
Zugriffszeitpunkte verloren gehen: vor einem solchen Rückbau diese Daten sichern.
Ein Anwendungsrollback kann die additive Migration zunächst stehen lassen.

In dieser Umsetzung wurden weder Produktivmigration noch Push oder Deployment
ausgeführt. Phase 2 wurde nicht begonnen.

## Prüfung

Automatisierte Abnahme in `tests/test_einsatzwissen.py` und
`tests/test_einsatzwissen_ui.py`: alle sechs Kategorien, Unterkategorien,
Metadatensuche, bestehende Inhaltssuche, Freigabesicht trotz geänderter Arbeitsstände,
MCP, Altclients, alte Snapshots, Feldlöschung, Verlauf, Archivierung, Tenantgrenzen,
separate Modulschalter und Migration mit unverändertem Altbestand.
Chromium prüft Bedienung, Navigation und horizontale Überläufe bei 360, 768 und
1024 Pixeln. Die Browsertests sind auch in den bestehenden Playwright-CI-Schritt
aufgenommen.

Ergebnisse der lokalen Prüfung:

- **1.065 Tests bestanden**: Einsatzwissen einschließlich Chromium, alle vorhandenen
  `test_katplan*`, `test_mcp_katplan*`, `test_nachschlagewerke*`, `test_suche*`,
  Modulnavigation, Berechtigungen, Organisationsexport/-import, synchrone DB-Routen
  und Jinja-Kommentare. Ein KI-Importtest war im ersten Lauf einmal auffällig;
  sowohl der gezielte Wiederholungslauf als auch der vollständige abschließende
  Lauf waren erfolgreich.
- Abschließender Lauf nach der mobilen Layoutanpassung: **24 Tests bestanden**
  (drei Chromium-Ansichten und 21 vorhandene Tenant-Isolationstests).
- **MariaDB 10.11 in einem separaten temporären Docker-Container**: komplette
  Migrationskette bis 0307, Einfügen eines alten Plans und Freigabesnapshots,
  Upgrade 0308, Prüfung unveränderter IDs/Planart/Beschreibung/Snapshot,
  Downgrade 0307 und erneutes Upgrade 0308 erfolgreich. Der Testcontainer wurde
  anschließend entfernt. Keine bestehende Datenbank wurde dafür verwendet.
- Migration 0308 separat auf SQLite einschließlich Altbestand und Rückbau geprüft.
  Ein vollständiger Aufbau aller historischen Migrationen unter SQLite scheitert
  an bestehendem MySQL-spezifischem `ADD COLUMN IF NOT EXISTS`; deshalb erfolgte
  die vollständige Kettenprüfung auf MariaDB.
- Ruff für alle geänderten/neuen Python-Anwendungsdateien und die neuen Tests sowie
  `git diff --check` erfolgreich.

Reproduzierbarer Hauptprüflauf (lokal ggf. `ECP_PLAYWRIGHT_EXECUTABLE` auf das
vorhandene Chromium setzen):

```sh
.venv/bin/pytest -q tests/test_einsatzwissen.py tests/test_einsatzwissen_ui.py \
  tests/test_katplan*.py tests/test_mcp_katplan*.py tests/test_modul_navigation.py \
  tests/test_nachschlagewerke*.py tests/test_suche*.py tests/test_permissions.py \
  tests/test_org_export_service.py tests/test_org_import_service.py \
  tests/test_async_routes_sync_db.py tests/test_template_jinja_kommentare.py
```
