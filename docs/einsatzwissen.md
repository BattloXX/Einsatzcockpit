# Einsatzwissen – Gesamtdokumentation

Stand: 25. Oktober 2026, einschließlich Phase 7. Einstieg: `/einsatzwissen`.

## Zweck und fachlicher Umfang

Einsatzwissen bündelt Einsatzpläne, Führungswissen, Fachwissen & Lehrbehelfe,
Katastrophenschutz, Sondereinsatzmittel und Nachschlagewerk. Die Kategorien ordnen
bestehende Inhalte; technische Planarten bleiben Alarm-/Einsatzplan,
Maßnahmenplan und Katastrophenplan. Plan-IDs, Geometrien, Phasen, Akteure,
Checklisten, Verständigungen und bestehende Einsatzübernahmen bleiben erhalten.

Es gibt ein gemeinsames Inhaltsmodell auf Grundlage der Planverwaltung.
Kontakt-, Objekt-, Fahrzeug- und Wasserstellenstammdaten werden referenziert.
Gefahrgut und Rettungskarten behalten ihre eigenen Kataloge und Modulschalter.

## Nutzung im Einsatz

Die Übersicht zeigt standardmäßig Freigaben, mit Kategorien, Unterkategorien,
Schlagwörtern, Gefahrarten, Suche, persönlichen Favoriten und Zugriffsverlauf.
Bearbeiter können ausdrücklich zu Arbeitsständen wechseln.

Die kompakte Einsatzansicht `/einsatzwissen/{id}` gliedert Freigaben in
Sofortmaßnahmen, Gefahren/Sicherheit, weitere Maßnahmen, Dokumente, Kontakte/
Sondereinsatzmittel und verwandte Informationen. Sie führt zu den vollständigen
Planansichten, Quellen, Karten und vorhandenen Übernahmedialogen.

Der gemeinsame PDF-/Bildviewer bietet Dokumentfolge, durchgehendes PDF-Scrollen,
Seitenwahl, Miniaturen, Zoom, temporäre Rotation, Vollbild, Original, Download,
Druckauswahl und Einsatzübernahme. Scrollen aktualisiert Seitennummer und die
aktuelle Seitenauswahl. Explizit gewählte Druckbereiche bleiben bestehen.
PDF-Seiten werden erst in Bildschirmnähe gerendert; entfernte Canvas-Flächen
werden freigegeben. Gemischte Seitenformate behalten ihre Größenverhältnisse.
PDF.js samt Worker und Hilfsdateien wird lokal ausgeliefert.

## Inhalte pflegen

1. Wissenseintrag unter `/einsatzwissen` anlegen oder vorhandenen Eintrag bearbeiten.
2. Kategorie und Metadaten pflegen. Kapitel, Phasen, Maßnahmen und Akteure im
   bestehenden Editor verwalten. Verantwortliche Person und Prüfintervall am Plan setzen.
3. Mehrere Anhänge auf Plan-, Kapitel- oder Maßnahmenebene hochladen oder verlinken.
4. Unter **Vorhandenes Dokument mehrfach zuordnen** eine bereits verfügbare Datei
   oder PDF-Quelle auswählen. Optional einen Seitenbereich wie `3-5,8` angeben.
   Dieselben Originalbytes können mehreren Maßnahmen und Wissenseinträgen dienen.
5. Unter **Bearbeiten** die Reihenfolge als Zahl, Beschriftung, Urheber, Prüfdatum,
   Gültigkeitsende, Prüfintervall in Monaten und zuständige aktive Person ändern.
   Die Zuordnungen sind getrennte redaktionelle Referenzen auf gemeinsame Bytes.
6. Unter **Dokument austauschen** eine neue Datei für genau diese Zuordnung
   auswählen. Andere Zuordnungen behalten ihre bisherige Datei. Prüfangaben der
   ersetzten Datei werden nicht als Prüfung der neuen Datei übernommen.
7. Änderungshistorie und Qualitätsübersicht prüfen und den bestehenden Prüf-/
   Freigabeablauf durchlaufen.

Die Reihenfolge wird aufsteigend ausgewertet. Ein Prüfintervall erzeugt aus dem
Prüfdatum einen nächsten Prüftermin, mit korrekter Behandlung von Monatsenden.
Die Anzeige kennzeichnet fällige Prüfungen und abgelaufene Gültigkeit. Ein Datum
ist eine redaktionelle Angabe, keine automatisch erteilte fachliche Freigabe.
Planbezogene Wiedervorlagen und Erinnerungen bestehen unverändert weiter.
Dokumentbezogene Fristen erscheinen in Ansicht und MCP; ein eigener
Benachrichtigungsdienst für jede Dokumentreferenz ist nicht Bestandteil von Phase 7.

## Versionen, Prüfung und Freigabe

Verbindlich bleiben die vorhandenen Zustände:

| Code | Bedeutung |
| --- | --- |
| `entwurf` | Entwurf |
| `in_pruefung` | In Prüfung |
| `freigegeben` | Freigegeben |
| `in_ueberarbeitung` | In Überarbeitung; fachlich entspricht dies erforderlicher Überarbeitung |
| `archiviert` | Archiviert |

Es wird kein konkurrierender Status „Überarbeitung erforderlich“ eingeführt.
Änderungen an einer Freigabe markieren über den vorhandenen Service die
Überarbeitung. Leser sehen weiterhin die letzte freigegebene Fassung. Eine neue
Datei, neue Metadaten oder KI-Vorschläge erzeugen keine Freigabe.

`KatplanVersion` und `KatplanVersionElement` sind unveränderliche Snapshots.
Dokumentzuordnungen einschließlich Sortierung und Prüfangaben werden in neuen
Snapshots und Inhaltshashes berücksichtigt. Bestehende Snapshots werden nicht
umgeschrieben. Historische Snapshots ohne Prüfangaben zeigen keine erfundenen Werte.

Austauschen erzeugt eine neue Anhang-UUID und entfernt die bisherige Zuordnung aus
dem Arbeitsstand. Wenn eine ältere Freigabe sie verwendet, bleibt die alte Zeile
mit Datei erhalten. Gemeinsam verwendete Dateien werden erst entfernt, wenn keine
weitere Anhang- oder PDF-Quellenreferenz besteht. Protokolle dokumentieren Benutzer,
Zeitpunkt, Aktion, alte/neue Werte und Ursprung `ui` beziehungsweise `mcp`.

Die neue Pflege, Zuordnung, Upload-/Entfernungsformulare und der Austausch verwenden die Planversion als
Konfliktschutz. Veraltete Formulare liefern einen Konflikt; MCP verlangt erneutes
Lesen des Arbeitsstands. Bestehende ältere Schnittstellen ohne Versionsparameter
bleiben kompatibel. Bestehende Elementbearbeitung behält ihren Versionsschutz.

## Zentrale Dateien und Speicher

`KatplanAnhang` bleibt zugleich Zuordnung und geschützter Dateieinstieg. Mehrere
Zeilen können denselben unveränderlichen Speicherpfad verwenden. Ein neuer
Datei-Master mit konkurrierenden Zugriffskontrollen wird nicht eingeführt.
Jede Referenz behält eine eigene UUID, Plan-/Maßnahmenzuordnung, Beschriftung,
Seitenbegrenzung, Sortierung, Prüfangaben und Vertraulichkeit.

Neue Uploads identischer Bytes werden innerhalb derselben Organisation anhand
SHA-256 abgeglichen und teilen Original/Thumbnail. Ein PDF-Anhang kann auch die
bereits gespeicherte PDF-Quelle verwenden. Identische neue Quellenuploads desselben
Plans werden wiederverwendet; ein ausdrücklich beauftragter Quellversionswechsel
behält den bisherigen Versionierungsablauf. Eine Datei wird weder organisations-
übergreifend geteilt noch anhand fremder Hashes auffindbar gemacht.

Die Speicherzählung berechnet gemeinsame Anhangspfade einmal. Liegt die Datei
bereits als Planquelle vor, zählt die Quelle Original und ihre Ableitungen;
die Anhangsreferenzen erhöhen den Speicherverbrauch nicht erneut.
Alte Uploads erhalten keinen automatischen Hash-Backfill und werden nicht
nachträglich verschoben oder zusammengeführt. Sie sind ausdrücklich mehrfach
referenzierbar. Unterschiedliche PDFs bleiben auch bei ähnlichem Inhalt getrennte
Originale. Unabhängige Einsatzfassungen sind beabsichtigte, nachvollziehbare Kopien.

## PDF-Lehrbehelfe und Quellen

Umfangreiche PDFs im Bereich **Quellen** hochladen. Der bestehende Import prüft
Dateityp, Größe, Seitenlimits und Speicherquote, speichert das Original unverändert
und bestimmt die Seitenzahl. Die Hintergrundpipeline erstellt Einzel-PDFs,
Seitenbilder und Miniaturen. Vorhandener PDF-Text wird extrahiert; bei Bedarf folgt
der bestehende Tesseract-OCR-Fallback. Fehler und Wiederverarbeitung bleiben sichtbar.

PDF-Lesezeichen dienen als Kapitel-/Seitentitel. Fehlen sie, werden kurze,
nummerierte oder vollständig großgeschriebene Zeilen als Überschriftenkandidaten
verwendet. Diese Erkennung ist heuristisch und verändert keine Maßnahmen. Quellen-
seiten, Seitentexte, Originale und Seitenbereiche bleiben über geschützte Endpunkte
aufrufbar. Keine Uploadbytes werden durch eine neu generierte PDF ersetzt.

Eine neue Quellversion überschreibt die alte Datei nicht. Der vorhandene
Seitenvergleich zeigt Änderungen und betroffene Maßnahmen; Quellenreferenzen
werden ausdrücklich umgestellt. Seitenbezüge bleiben an der ursprünglichen
Dokumentversion, bis ein Bearbeiter sie ändert.

## KI und semantische Suche

Die Bestandsprüfung bestätigt zwei getrennte bestehende Dienste:

- **Lokale semantische Suche:** `services/suche/embedding.py` verwendet Ollama
  (`SEARCH_EMBED_URL`, standardmäßig `http://localhost:11434`) mit
  `SEARCH_EMBED_MODEL=embeddinggemma`. Semantik ist über
  `SEARCH_SEMANTIC_ENABLED` optional. SQL-/Volltextsuche, Synonyme und der
  bestehende Rückfall bleiben verfügbar. Resolver prüfen Treffer erneut gegen
  aktuelle Organisation, Module, Rollen, Vertraulichkeit und sichtbare Fassungen.
- **Generativer Planimport:** `katplan_ki_service` verwendet den vorhandenen
  `ai_service` mit Anthropic und gesonderter Organisationsaktivierung
  `katplan_ki_import_enabled`. Das lokale Embeddingmodell ist kein generatives
  Sprachmodell. Phase 7 ändert diese Providerwahl nicht und aktiviert keinen
  externen Dienst automatisch.

Der bestehende KI-Import erzeugt strukturierte Vorschläge mit Dokument- und
Seitenherkunft. Er analysiert derzeit bis zu 40 Seiten pro Lauf. Vorschläge
müssen im Review ausdrücklich übernommen werden; sie bleiben Entwürfe mit
KI-Herkunft. Menschliche Freigabe ist weiter zwingend.

Der neue MCP-Überblick `katplan_dokument_zusammenfassung` arbeitet vollständig
lokal und extraktiv: bis zu 20 berechtigte Quellseiten pro Abruf, höchstens 600
Zeichen pro Seite, mit Seitennummern und Kürzungshinweis. Er liefert Quellenauszüge,
keine generierte fachliche Aussage. Fehlende OCR-/Textseiten werden ausgewiesen.
Für lange Lehrbehelfe mehrere Bereiche abrufen und das Original prüfen.

Freie generative Dokumentfragen, automatisch erkannte fachliche Lücken und
KI-Verknüpfungsvorschläge werden nicht als fertige Funktionen ausgewiesen.
Die bestehende Suche, Seitenwerkzeuge und Quellenreferenzen liefern dafür
berechtigtes Material. Es gibt keine automatische Veröffentlichung oder
Änderung sicherheitsrelevanter Maßnahmen durch KI.

## MCP-Schnittstellen

Die bestehenden `katplan_*`-Namen bleiben kompatibel. Alle neuen Werkzeuge sind
in Registry und HTTP-Server registriert und unterliegen dem Planmodulschalter.

| Aufgabe | Werkzeuge |
| --- | --- |
| Wissen suchen | `katplan_suchen`, bestehende zentrale MCP-Suche |
| Kategorien | **neu:** `einsatzwissen_kategorien` |
| Eintrag lesen | `katplan_liste`, `katplan_lesen` |
| Maßnahmen | `katplan_elemente`, `katplan_element_lesen` |
| Quellen und Seiten | `katplan_dokument_auszug`, `katplan_quellseite_bilder` |
| Quellenauszüge | **neu:** `katplan_dokument_zusammenfassung` |
| Dokumentbibliothek | **neu:** `katplan_dokumentbibliothek` (nur Bearbeiter) |
| Dokument zu Maßnahmen zuordnen | **neu:** `katplan_dokument_referenzieren` |
| Dokumentmetadaten/Sortierung | **neu:** `katplan_dokument_pflegen` |
| Verknüpfungen | `katplan_verknuepfungen`, `katplan_verknuepfung_setzen/_entfernen`, `katplan_beziehung_setzen/_entfernen` |
| Quellenbezüge bearbeiten | `katplan_quellseite_verknuepfen` |
| KI-Entwürfe | `katplan_vorschlaege_aus_dokument`, bestehende Entwurfswerkzeuge |
| Einsatz zuordnen | **neu:** `einsatz_dokument_zuordnen` |
| Einsatzdokumente | **neu:** `einsatz_dokumente_auflisten` |
| Druck vorbereiten | **neu:** `dokumentdruck_vorbereiten` |
| Prüfstatus | **neu:** `katplan_pruefstatus`, bestehendes `katplan_ueberpruefung_bestaetigen` |

Beispiel für eine Zuordnung: Bibliothek lesen, Arbeitsstand mit `katplan_lesen`
lesen, dann `katplan_dokument_referenzieren(plan_id=…, version=plan_version,
referenz="anhang:UUID", element_id="M-1", seiten="3-5")` aufrufen.

`katplan_dokument_pflegen` akzeptiert ausschließlich Beschriftung, Reihenfolge,
Prüfdatum, Gültigkeitsende, Prüfintervall und zuständige Person. Status/Freigabe
sind keine schreibbaren Felder. Die Planversion ist Pflicht.

`einsatz_dokument_zuordnen` verwendet dasselbe Schema und denselben Service wie
`/einsatz-dokumente/uebernehmen`. Zusätzlich zu Quellenrechten sind Schreibrechte
am aktiven Zieleinsatz nötig. Vertrauliche Freigaben müssen ausdrücklich bestätigt
werden. Einsatzfassungen behalten Quelle, Freigabeversion, Auswahl und Auditverlauf.

`dokumentdruck_vorbereiten` verwendet das bestehende Webschema und den bestehenden
Druckservice. Es liefert eine benutzergebundene, zeitlich begrenzte PDF-Auswahl.
Beim PDF-Abruf werden Quellenrechte und Fingerprints erneut geprüft. Das Werkzeug
sendet keinen Auftrag an einen Drucker.

## Rechte und Schutzgrenzen

- Bestehende Rollen `katplan_verwalter`, `katplan_freigeber`, Leserollen und
  Vertraulichkeitsrollen bleiben verbindlich. System-/Organisationsadministratoren
  behalten ihre bisherigen Rechte.
- Organisation und Plan-/Elementzugehörigkeit werden serverseitig geprüft.
  Bibliothek und gemeinsame Pfade sind kein öffentlicher Dateizugriff.
- Das Verlinken übernimmt Vertraulichkeit der ausgewählten Quelle einschließlich
  eines vertraulichen Elternelements. Änderungen verwenden die vorhandene
  redaktionelle Vertraulichkeits- und Freigabelogik.
- MCP lädt Benutzer, Rollen, Organisation und Modulzustand bei jedem Aufruf neu.
  Gerätezugänge vertreten keinen persönlichen MCP-Benutzer.
- Dateipfade sind auf den konfigurierten Speicher beschränkt; Druck akzeptiert
  ausschließlich bekannte interne Dokumentreferenzen, keinen URL-Proxy.
- PDF-JavaScript/XFA wird im Viewer nicht als interaktiver Inhalt ausgeführt.
  Inhalte und KI-Ausgaben bleiben HTML-bereinigt bzw. als Text dargestellt.
- Neue Dateireferenzen und Metadaten werden protokolliert. Datums-/Personenwerte,
  Seitenbereiche und Schreibfelder werden validiert. Schreibfehler rollen die
  Datenbanktransaktion zurück; MCP räumt ausschließlich neu erzeugte Uploaddateien auf.

## Offline, Druck und Einsatzfassungen

Die ausdrückliche Offlinefreigabe der Phase 6 bleibt auf nicht vertrauliche
Kerntexte beschränkt: verschlüsselt, höchstens 15 Minuten, 20 Einträge und 2 MB.
Dokumente, Kontakte, Übernahmen und Druck bleiben online. Rechteentzug während
vollständiger Netztrennung wird spätestens bei Wiederverbindung bzw. Ablauf
wirksam. Es gibt keinen allgemeinen Offlinecache für Plan-PDFs.

Druckpakete behalten Seitenformate und geprüfte Quellen. Einsatzdokumente
verwenden unabhängige, organisationsintern deduplizierte Fassungen, damit
spätere Planänderungen einen übernommenen Einsatzstand nicht verändern.

## Komponenten und Datenbank

Fachmodelle: `app/models/katplan.py`, `einsatz_dokument.py`.
Fachservices: `katplan_*`, `einsatzwissen_service`, `dokumentviewer_service`,
`dokumentdruck_service`, `einsatz_dokument_service`, `services/suche/*`.
Weboberfläche: `ui_einsatzwissen`, `ui_katplan`, gemeinsame Viewer-/Druckskripte,
`templates/einsatzwissen`, `templates/katplan`.
MCP: bestehende Planwerkzeuge und neu `app/mcp/tools/einsatzwissen.py` mit
HTTP-Registrierung in `app/mcp/server.py`.

Migration **0311**, Vorgänger **0310**, ergänzt nullable:

| Tabelle | Felder |
| --- | --- |
| `katplan_anhang` | `datei_hash` (SHA-256, Index), `pruefdatum`, `gueltig_bis`, `pruefintervall_monate`, `zustaendig_user_id` (FK auf Benutzer, `SET NULL`) |
| `katplan_dokument` | `datei_hash` (SHA-256, Index) |

Keine neuen Inhaltstabellen, keine Statusumstellung, kein Verschieben alter Dateien,
kein Hash-Backfill, kein Umschreiben von Freigabesnapshots. Service-Worker-Assetcache:
`ec-v22`. Dokumentauslieferung behält die bestehenden Zugriffsprüfungen.

Vor einer gesondert freizugebenden Produktivbereitstellung Datenbank und Medien
sichern, Migration im Staging anwenden und bestehende Freigaben/Dateien prüfen.
Rückbau auf 0310 entfernt ausschließlich die neuen Spalten und Indizes; vorher
neu erfasste Prüfangaben sichern. Physische Dateifreigabe benötigt den neuen
referenzbewussten Lösch-/Speicherzählungscode: Ein Code-Rollback auf eine ältere
Version darf gemeinsam verwendete Dateien nicht mit der alten Löschlogik bearbeiten.
Eine Produktivmigration, ein Push und ein Deployment gehören nicht zu diesem Abschluss.

## Tests und Betriebsgrenzen

Konkrete Ergebnisse, Regressionen und verbleibende Abnahmen stehen im
[Abschlussbericht Phase 7](einsatzwissen-phase7.md).
Browserprüfungen emulieren Smartphone, Tablet und Desktop auf Chromium. Ein echter
Android-/Windows-Gerätetest, Betriebssystemdruckdialog und physischer Drucker
benötigen eine separate Geräteabnahme. OCR-Qualität und fachliche Richtigkeit der
Lehrbehelfe bleiben menschlich zu prüfen. Ausfall lokaler Such-/OCR-/KI-Dienste
wird über die bestehenden Fehler-/Rückfallpfade behandelt.

Weiterführend: [Phase 1](einsatzwissen-phase1.md),
[Viewer Phase 2](dokumentviewer-phase2.md), [Druck Phase 3](dokumentdruck-phase3.md),
[Einsatzdokumente Phase 4](einsatzdokumente-phase4.md),
[Einsatzoberfläche Phase 6](einsatzwissen-phase6.md).
