# Dokumente im Einsatz – Phase 4

Stand: 25.10.2026. Ergänzt [Einsatzwissen](einsatzwissen-phase1.md),
[Dokumentenviewer](dokumentviewer-phase2.md) und [Dokumentendruck](dokumentdruck-phase3.md).
Phase 5 ist nicht Bestandteil dieser Umsetzung. Kein Push oder Produktivdeployment.

## Bedienung

Im Viewer öffnet **Zum Einsatz hinzufügen** den Übernahmedialog. Die zuvor gewählte
einzelne Seite, der Seitenbereich oder die freie Auswahl wird vorgeschlagen.
**Gesamtes Dokument** übernimmt alle Seiten des geöffneten Dokuments beziehungsweise
des geöffneten Auszugs. Unter **Mehrere Dokumente / Sammlung** lassen sich mehrere
Unterlagen markieren und deren Seiten getrennt festlegen.

Die Auswahl umfasst einen aktiven Einsatz oder eine aktive Großschadenslage,
optional einen bestehenden Auftrag und bei Großschadenslagen einzelne Einheiten.
Eine freigegebene Maßnahme bietet zusätzlich **Dokumentensammlung zum Einsatz
hinzufügen**. Die Sammlung stammt aus ihrem Freigabesnapshot und umfasst vorhandene
PDF-/Bildanhänge, Objektdokumentverweise und die hinterlegten Quellseiten. Externe
Links werden nicht heruntergeladen. Nicht verfügbare Unterlagen verhindern die
gesamte Übernahme; es gibt keinen unbemerkten Teilimport.

Unterstützte Quellen sind freigegebene Plananhänge, Planquellen einschließlich
Einzelseiten und Auszügen, freigegebene generierte Plan-PDFs und veröffentlichte,
bereits lokal vorhandene Rettungskarten. Unveröffentlichte Anhänge und Entwürfe
werden auch bei Verwaltern abgewiesen. Ohne Versionsparameter wird die aktuelle
Freigabe gewählt und deren ID im Beleg festgehalten. Die Rettungskarten- und
Planmodulschalter sowie die bestehenden Leserechte gelten beim Übernehmen weiter.

Beide Einsatzboards haben einen aufklappbaren Bereich **Dokumente**. Die Verwaltung
zeigt Titel, Quelle, Dokumenttyp, Übernahmezeit, Originalseiten, Freigabe,
Quellversion, Einsatzfassung, Auftrag und Einheiten. Verfügbar sind Anzeigen,
Drucken und Öffnen der ursprünglichen Verknüpfung. Auftrag und Freigaben können
nachträglich geändert werden. Änderungen mit veraltetem Revisionsstand werden
abgewiesen. Entfernen widerruft alle weiteren Zugriffe und protokolliert die Aktion.

In der mobilen Einheitenansicht steht ein Bereich **Dokumente** mit großen
**[Titel] öffnen**-Schaltflächen. Die Anzeige verwendet denselben Viewer und wird
alle 30 Sekunden erneuert. PDFs werden bei jedem Abruf erneut autorisiert.
Die Viewer-Seitenzählung beginnt beim übernommenen Auszug mit 1; der Beleg im Board
nennt weiterhin die tatsächlichen Originalseiten, auch bei verschachtelten Auszügen.

## Freigaben

| Zugang/Aktion | Regel |
| --- | --- |
| Übernehmen, Zuordnung ändern, Entfernen | Persönlicher aktiver Zugang, eigene Organisation, sichtbarer aktiver Einsatz und bestehende Rolle Einsatzleiter, Administrator, Organisationsadministrator oder Schreiber; GSL-Leseprofil ausgeschlossen |
| Dokumentenverwaltung lesen | Persönlicher Einsatzlesezugang; eigene Dokumentorganisation und sichtbarer Einsatz; vertrauliche Unterlagen zusätzlich mit bestehender Vertraulichkeitsrolle |
| Ohne Empfängerauswahl | Ausschließlich Dokumentenverwaltung; keine Einheitenfreigabe |
| Einzelne Einheiten | Ausdrücklich gewählte Einheiten derselben GSL |
| Auftrag ohne Freigabeschalter | Nur Zuordnung; keine Leseberechtigung für Einheiten |
| Auftrag mit Freigabeschalter | Aktuelle und künftige aktive Dispositionen dieser Einsatzstelle; nach Rückzug oder Beendigung der Disposition entfällt dieser Zugriffsweg |
| Vertrauliche Unterlagen | Zusätzliche ausdrückliche Bestätigung bei jeder Einheiten-/Auftragsfreigabe; generierte Administrator-PDFs mit vertraulichen Inhalten bleiben vertraulich |
| GK-/QR-Einheitenzugang, Einheitengerät | Vorhandener validierter Einheitenkontext; nur Dokumente dieser GSL, Organisation und ausdrücklichen Freigabe; kein Zugriff auf Originalquellen oder allgemeine Dokumentenverwaltung |
| Allgemeiner Board-/Lage-QR-Zugang | Keine Einsatzdokumentrechte über die Berechtigungen des Tokenausstellers |

Bei normalen Einsätzen werden bestehende Board-Aufträge zugeordnet. Die mobile
Einheitenansicht und ihre Empfänger sind im Bestand GSL-bezogen; deshalb werden
Einheitenfreigaben für normale Einsätze nicht vorgetäuscht. Kollaborierende
Organisationen erhalten durch die Übernahme keine pauschale Dokumentfreigabe.

Auszüge entstehen ausschließlich aus der gespeicherten Einsatzfassung. Auch
manipulierte Seitennummern ermöglichen keinen Zugriff auf weitere Originalseiten.
Die Einheitenroute verrät keine Originaladresse. Jeder Abruf durchläuft die
vorhandene Geräte-/GK-Kontextprüfung, einschließlich Widerruf, Ablauf und Einsatzbezug.
Übernahme und Änderungen unterliegen dem bestehenden CSRF-Schutz. Dateien und
Listen verwenden `Cache-Control: no-store`; der Service Worker hält für diese
Endpunkte keine Offlinekopie vor. Bereits angezeigte oder ausgedruckte Inhalte
können durch einen späteren Widerruf nicht zurückgerufen werden.

## Fassungen und Speicherentscheidung

Die Bestandsprüfung umfasste `KatplanVersion`/`KatplanVersionElement`, versionierte
Anhänge und Quellendokumente, Objektdokumentversionen, die bisherige
Maßnahmenübernahme mit Medienkopien, `dokumentviewer_service`, die zentrale Druckkette,
Speicherquoten, Einsatzarchiv und Organisationsbackup/-restore.

Die bisherigen unveränderlichen Freigaben referenzieren Dateien, deren Lebensdauer
weiterhin zur Quelle gehört. Eine bloße URL schützt eine Einsatzfassung nicht vor
physischem Quelldateiverlust oder späterem endgültigem Löschen. Deshalb entstehen
unabhängige, unveränderliche PDF-Fassungen des **übernommenen** Inhalts. Pypdf erhält
Text, Vektoren und Seitengrößen; Bilder werden ohne Viewer-Screenshot als PDF
eingebettet. Quelldateien und Freigabesnapshots werden nicht verändert.

Migration **0309** (Vorgänger **0308**) ergänzt drei Tabellen:

- `einsatz_dokument`: Einsatz-/GSL-Zuordnung, unveränderliche Herkunft und Auswahl,
  Auftrag, Zeit, Urheber, Vertraulichkeit, Revisionsstand und Entfernungszeitpunkt.
- `einsatz_dokument_fassung`: PDF-Inhalt mit SHA-256; eindeutig je Organisation und
  Inhalt. Mehrere Zuordnungen referenzieren dieselbe Fassung. Der Binärinhalt wird
  beim normalen Listenabruf nicht geladen. Unter MariaDB wird `LONGBLOB` verwendet.
- `einsatz_dokument_empfaenger`: ausdrückliche Einheitenfreigaben.

Der Beleg unterscheidet Originalreferenz/-UUID und Dokumentversion, freigegebene
Planversion samt Freigabehash und Einsatzfassung samt Inhaltsprüfsumme. Ein späteres
Quellenupdate ersetzt keine Einsatzfassung. Provenienz und Binärinhalt sind gegen
ORM-Änderungen geschützt; der Abruf prüft zusätzlich den Inhalts-Hash. Dieselbe
Auswahl erzeugt keine weitere Binärkopie je Einsatz. Speicher wird nur für neue
Fassungen reserviert und bei `reconcile_storage` berücksichtigt.

Die Speicherung in der Datenbank nutzt deren Transaktionen und vorhandene
Backup-/Binärserialisierung; es gibt keine zusätzlichen ungeschützten Dateirouten
oder verwaisten Snapshotdateien nach einem abgebrochenen Import. Grenzen sind wie
beim Druck maximal 20 Dokumente, 2.000 Seiten, 100 MiB pro Quelle und 150 MiB
Quellumfang pro Übernahme sowie die Organisationsquote. Die tatsächliche
MariaDB-Paketgrenze wird vor dem Schreiben geprüft; bei Überschreitung erscheint
eine verständliche Meldung. Betrieb und DB-Backups müssen die erwarteten
Dokumentgrößen berücksichtigen.

## Abschluss, Archiv und Wiederherstellung

Einsatzabschluss löscht keine Zuordnung und keinen Binärinhalt. Persönliche
berechtigte Benutzer können die Fassung im abgeschlossenen Board und bei normalen
Einsätzen über **Dokumente des Einsatzes öffnen** im Archiv lesen und drucken.
Änderungen erfordern das bestehende Wiedereröffnen. Einheitenzugänge behalten die
bisherigen Grenzen für aktive Lagen.

Entfernen ist eine protokollierte Ausblendung; Beleg und Fassung bleiben für die
Nachvollziehbarkeit erhalten. Erst beim vorhandenen endgültigen Einsatzlöschen
werden auch diese Belege entfernt. Ein Binärinhalt wird dabei nur gelöscht und
aus der Quote ausgebucht, wenn kein weiterer Einsatz/GSL-Beleg ihn benötigt.
Es gibt keine neue Aufbewahrungsfrist oder automatische Löschroutine.

Alle drei Tabellen sind in der Tenantregistrierung und im generischen
Organisationsexport enthalten. Binärfassungen sind bereits im Datenexport enthalten,
auch ohne zusätzliche Mediendateien. Partielle Backups enthalten nur Zuordnungen
zu mitgesicherten Einsätzen/GSL und die benötigten Fassungen. Der Restore übernimmt
neue IDs für Ziele, Fassungen und Empfänger; historische IDs bleiben im Herkunftsbeleg.
Der Quellenlink löst nach einem Restore über Plan-UUID und Freigabenummer auf.

Der neue GSL-Restoretest deckte einen vorhandenen Fehler auf: SQLAlchemys
Tabellensortierung behandelt den GSL-Zyklus pauschal und konnte eine Einheit vor
ihrer verpflichtenden Lage importieren. Der Import sortiert nun nach verpflichtenden
Fremdschlüsseln und setzt nur nullable Rückverweise später ein. Organisationsbezogene
Primärschlüssel, insbesondere der Speicherzähler, bleiben beim Restore der Zielorg
zugeordnet. Die bisherigen Importtests und der neue Fassungs-/Empfänger-Restoretest
prüfen diese Anpassung.

Vor einem späteren Deployment Datenbank sichern, Migration auf einer aktuellen
Staging-Kopie anwenden und Rechte sowie Dokumentgrößen prüfen. `downgrade 0308`
entfernt die neuen Tabellen samt Einsatzfassungen; vor einem Rückbau müssen diese
Daten gesichert werden. Ein reiner Anwendungsrollback kann die additive Migration
stehen lassen. Die Umsetzung hat keine Produktivmigration ausgeführt.

## Abnahme und Prüfung

Die acht Abnahmeschritte werden durch `tests/test_einsatz_dokumente.py` und
`tests/test_einsatz_dokumente_ui.py` abgedeckt: Rettungskarte/Lehrbehelf, Seiten 4–6,
Boardanzeige, nachträglicher Auftrag, berechtigter GK, Ausschluss anderer Einheiten,
Quellupdate und Viewer-Druckkontext beziehungsweise tatsächlich erzeugte Druck-PDF.
Hinzu kommen Mehrfachübernahme, Maßnahmensammlung, Bilder, Entwurfssperre,
verschachtelte Auszüge, CSRF, vertrauliche Unterlagen, Auftragsrückzug,
Tokenwiderruf, Revisionskonflikte, Audit, Abschluss, letzte Löschung, Quoten,
Deduplizierung und Backup/Restore. Die Browserprüfung läuft bei 1440×900,
1024×768 und 390×844 mit Touch-Unterstützung und ist in der Playwright-CI enthalten.

`tests/test_einsatz_dokumente_migration.py` prüft SQLite-Upgrade, Rückbau, erneutes
Upgrade, Altbestand und den MariaDB-Binärtyp. Zusätzlich wurde die vollständige
Migrationskette auf einer **separaten temporären MariaDB 10.11** ausgeführt:
Altbestand auf 0308, Upgrade 0309, Fassungen/Fremdschlüssel/Deduplizierung,
Quellupdate, Downgrade 0308 mit unverändertem Altbestand und erneutes Upgrade 0309.
Keine vorhandene Datenbank wurde verwendet.

Ein echter Windows-/Android-Systemdruckdialog und die Papierausgabe an einem
angeschlossenen Drucker sind weiterhin manuell abzunehmen.

Reproduzierbare zentrale Prüfungen:

```sh
PYTEST_XDIST_WORKER=phase4 .venv/bin/pytest -q tests/test_einsatz_dokumente*.py \
  tests/test_dokumentdruck.py tests/test_dokumentviewer.py \
  tests/test_org_export_service.py tests/test_org_import_service.py tests/test_storage_quota.py
.venv/bin/ruff check app/ tests/test_einsatz_dokumente*.py alembic/versions/0309_einsatz_dokumente.py
.venv/bin/mypy app/ --ignore-missing-imports
npm run test:js
git diff --check
```

Für ein bereits installiertes Chromium kann `ECP_PLAYWRIGHT_EXECUTABLE` gesetzt
werden.

Ergebnisse am 25.10.2026 (Läufe überschneiden sich):

- Breite Regression: **610 bestanden** – Plan-/Freigabe-/Übernahmefunktionen,
  Einsatzwissen, Viewer/Druck, Einheitenzugang, Archiv, Rechte, Import/Export,
  Quoten und strukturelle Prüfungen.
- Abschließende Backendprüfung nach den Restoreanpassungen: **101 bestanden**,
  einschließlich aller 16 neuen fachlichen Tests und Migration.
- Abschließender Browserlauf: **32 bestanden**, einschließlich vier neuer
  Übernahmeprüfungen und bestehender Viewer-, Druck-, Einsatzwissen- und
  Einheitenbedienungstests. Ein vorhandener Drucktest ermittelt nun die tatsächlich
  gewählte Quelldatei statt einer durch UUID-Sortierung zufälligen Fixturedatei.
- JavaScript: **134 bestanden**. Ruff ohne Befund; mypy ohne Fehler in
  **520 Quelldateien**; JavaScript-Syntax und `git diff --check` erfolgreich.
- MariaDB-Ketten-/Altbestandsprüfung wie oben beschrieben erfolgreich; der
  ausschließlich dafür angelegte Container wurde anschließend entfernt.
