# Katastrophenpläne (Entwicklung)

## Datenmodell und Freigaben

Migrationen 0294–0299 ergänzen Pläne, Kapitel, Phasen, Elemente und Prüfprotokoll, Freigabeversionen, Verknüpfungen, Anhänge, versionierte Quellen mit Seiten sowie KI-Importvorschläge. Alle Daten werden nach Organisation abgegrenzt.

`KatplanPlan` und `KatplanElement` bilden den veränderlichen Arbeitsstand. Eine Freigabe schreibt `KatplanVersion.plan_json` und `KatplanVersionElement` als unveränderlichen Snapshot einschließlich Linkdaten, Quellen und Anhangreferenzen. Ein ORM-Wächter verhindert Updates der Versionstabellen. Änderungen nach Freigabe markieren den Plan zur Überarbeitung. Optimistische Versionsnummern verhindern das Überschreiben konkurrierender Änderungen.

## Services und Rechte

| Service | Verantwortung |
|---------|---------------|
| `katplan_service`, `katplan_typen` | Arbeitsstand, Validierung, Suchtext, Kapitelbaum und Typregister |
| `katplan_zugriff`, `katplan_flags` | Rollen, vertrauliche Inhalte, Modulschalter |
| `katplan_freigabe_service` | Snapshots, Workflow, Versionsvergleich, Wiedervorlage |
| `katplan_link_service`, `katplan_ziel_service` | Tenantgebundene Live-Ziele, Kontaktrechte und Snapshotvergleich |
| `katplan_geo_service`, `katplan_suche_service` | Geometrie und kombinierte Suche |
| `katplan_anhang_service`, `katplan_quelle_service` | Geschützte Downloads, Quota, Quellenversionen und Seitenvergleich |
| `katplan_ki_service` | Seitenanalyse und ausdrückliche Vorschlagsübernahme |
| `katplan_mcp_service` | Gemeinsame rechtegeprüfte Aufbereitung der Fassungen |
| `katplan_qs_service` | Dynamische Hinweise im Arbeitsstand; keine QS-Daten im Snapshot |
| `katplan_pdf_service` | HTML- und PDF-Offlinefassung über `fassung_laden`, WeasyPrint mit xhtml2pdf-Fallback |

`ui_katplan` verwendet synchrone DB-Routen. Downloads prüfen Plan, Organisation, Version und Nutzerrechte. Der PDF-Export nutzt dieselben Element-/Telefon-/Objektrechte wie MCP. Beschreibungs-HTML wird vor der Ausgabe erneut bereinigt; Bildvorschaudaten werden ausschließlich aus lokalen, nicht vertraulichen Anhängen eingebettet. HTML für die PDF-Tests ist über `render_katplan_html` separat prüfbar.

## Quellenvergleich und Qualität

`vergleiche_quellen` prüft die Bindung beider Dokumente an Plan und Gruppe, normalisiert den Volltext und vergleicht gleiche Seitennummern mit `difflib`. Neue und entfallene Seiten werden gesondert ausgewiesen; Textvergleiche sind auf 60 Zeilen begrenzt. Betroffene Arbeitsstandelemente stammen aus derselben Quellgruppe und berühren geänderte/entfallene Seiten. Das ausdrückliche Umstellen protokolliert die Referenzänderung und markiert die Überarbeitung; Seiten und Inhalt bleiben bestehen.

KI-Prüfung wird ohne Migration aus `KatplanAenderung` abgeleitet: Ein UI-Eintrag `geaendert` nach Anlage bestätigt die Prüfung. MCP-Einträge tun das nicht. Hinweise sperren die Freigabe nicht.

## Tests

`tests/test_katplan_k12.py` prüft die einzelnen QS-Codes, Summen, Freigabe trotz Hinweisen, UI-/MCP-Prüfung, PDF-Rechte und Snapshotstabilität, Quellenvergleich und ausdrücklichen Quellenwechsel sowie die öffentliche Funktionsseite. Zugehörige Regressionen: `test_katplan_k3b.py`, `test_katplan_k7c.py`, `test_mcp_katplan.py`, `test_css_klassen_definiert.py`, `test_async_routes_sync_db.py` und `test_public*.py`. Die Async-DB-Baseline darf nicht wachsen.

Siehe auch: [Anwendung](Anwender-Katastrophenplaene), [Administration](Administration-Katastrophenplaene).
