# Neue Suchquelle anbinden

Die Suche verwendet `app/services/suche/abfrage.py` für UI und MCP gemeinsam.
Indexquellen bestehen aus einem Adapter und einer Live-Prüfung. Neue Quellen zunächst
mit einer expliziten Feld-Whitelist in einem `adapter_*.py` ergänzen und in
`quellen.py` registrieren. Keine generische ORM-Serialisierung: Kontakte, Personendaten,
Telefonnummern, Arbeitskopien und nicht freigegebene Inhalte ausschließen. Titel in
jeden Chunk aufnehmen, damit MariaDB Wörter aus Titel und Seitentext gemeinsam findet.

In `resolver.py` aktuelle Rechte, Freigabe, Status und Ziel-URL prüfen. In `outbox.py`
die betroffenen Modelle auf Quell-IDs abbilden, auch bei Änderungen an Kindmodellen
und Freigabeentzug. Neue Indexquellen auch in `abfrage.INDEX_TYPEN`, Vertrag,
UI-Filtern/Labels und `admin.QUELLEN_LABELS` aufnehmen. Die Neuaufbauplanung verwendet
die Adapter-Registry. Jede Query explizit auf `org_id` begrenzen; Mutationen gefiltert
laden. Neue DB-Routen synchron schreiben, POSTs mit CSRF und gezieltem HTMX-Swap.

Tests für Whitelist, Löschung, Idempotenz, Cross-Org-Isolation, Live-Rechte,
Outbox-Zuordnung und UI/MCP-Vertrag ergänzen. SQLite prüft den LIKE-Pfad;
echtes FULLTEXT braucht MariaDB und die Migrationen inklusive FULLTEXT-Indizes.

## MariaDB-Integrationstest

```sh
MARIADB_TEST_URL="$DATABASE_URL" .venv/bin/python -m pytest tests/mariadb -q -p no:xdist
```

Nur eine separate, bereits migrierte Testdatenbank verwenden. Der Test legt eigene
Orgs mit eindeutigen Slugs an und löscht sie anschließend samt Indexdaten per FK-Cascade.
`tests/conftest.py` setzt `DATABASE_URL` auf SQLite. Der Integrationstest nutzt deshalb
ausschließlich `MARIADB_TEST_URL` und eine eigene Engine; `tests/mariadb/conftest.py`
überschreibt die geerbten Autouse-Fixtures, einschließlich SQLite-Setup und Seed.
Ohne `MARIADB_TEST_URL` greift `skipif`, auch im normalen SQLite-Job mit xdist.
Der Migrationsjob führt den Test nach Migration und Seed aus. Geprüft werden Synonyme,
Präfixe, Titel plus Text im Chunk, kurze Tokens, Boolean-Escaping, Org-Trennung und
FULLTEXT-Nutzung mit EXPLAIN. Ein lokaler Skip ersetzt keinen MariaDB-Erfolgslauf.
