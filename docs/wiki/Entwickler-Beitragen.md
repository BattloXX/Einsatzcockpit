# Beitragen

← [Zurück zur Startseite](Home)

## Branch-Strategie

```
main           – Stabile, produktionsreife Version
develop        – Integrations-Branch für Features
feature/xyz    – Feature-Branches (von develop abzweigen)
fix/xyz        – Bugfix-Branches (von main oder develop)
```

## Arbeitsablauf

```bash
# Feature:
git checkout develop
git pull origin develop
git checkout -b feature/atemschutz-verbesserung

# ... Entwicklung ...

git add -p    # Interaktiv, nur relevante Änderungen
git commit -m "feat: Rückzugsdruck-Warnung akustisch verbessern"
git push origin feature/atemschutz-verbesserung

# Pull Request: feature/* → develop
# Nach Review und CI: merge
# Für Releases: develop → main
```

## Commit-Format (Conventional Commits)

```
<typ>: <beschreibung>

[optionaler body]
```

| Typ | Bedeutung |
|-----|-----------|
| `feat` | Neues Feature |
| `fix` | Bugfix |
| `docs` | Nur Dokumentation |
| `refactor` | Code-Umstrukturierung ohne Feature/Fix |
| `test` | Tests hinzugefügt oder verbessert |
| `chore` | Build-System, Dependencies |
| `style` | Formatierung, kein funktionaler Einfluss |

**Beispiele:**
```
feat: QR-Code-Gültigkeit an Einsatzdauer binden
fix: Rückzugsdruck bei 0-bar-Anfangsdruck korrekt berechnen
docs: Atemschutz-Wiki-Seite ergänzen
test: Integrations-Tests für API-Idempotenz
```

## Code-Qualität

Vor dem Commit:

```bash
ruff check app/ --fix     # Lint + Auto-Fix
mypy app/ --ignore-missing-imports   # Type-Check
pytest tests/ -v          # Tests
```

CI schlägt fehl bei Lint-Fehlern oder fehlgeschlagenen Tests.

## Pull Request Checkliste

- [ ] Branch von `develop` abgezweigt
- [ ] Commits im Conventional-Commit-Format
- [ ] `ruff check` ohne Fehler
- [ ] `mypy` ohne neue Fehler
- [ ] Tests hinzugefügt/aktualisiert
- [ ] `pytest tests/` läuft durch
- [ ] Bei Datenbankänderungen: Alembic-Migration vorhanden

## Datenbankmigrationen

Bei neuen/geänderten Models immer eine Migration erstellen:

```bash
alembic revision --autogenerate -m "beschreibung"
```

Die generierte Datei in `alembic/versions/` überprüfen (autogenerate erkennt nicht alles korrekt) und committen.

## Feature-Flag-Pattern (Opt-in-Module)

Neue optionale Module (wie UAS oder Geräteverleih) werden zweistufig gesichert:

1. **System-Flag** (`SystemSettings`-Key, z.B. `uas_module_enabled`): System-Admin schaltet das Modul systemweit frei.
2. **Org-Flag** (`OrgSettings`-Spalte, z.B. `uas_module_enabled`): Org-Admin aktiviert das Modul für die eigene Org.
3. **Guard**: FastAPI-Dependency `require_xxx_enabled` prüft `request.state.xxx_module_enabled` → HTTP 404 wenn deaktiviert.
4. **State-Propagation**: `request.state.xxx_module_enabled` wird in `_resolve_current_org()` (Global-Dependency) gesetzt.
5. **Navigation**: `{% if request.state.xxx_module_enabled %}...{% endif %}` in `base.html`.

Vorbild: `app/routers/ui_uas.py` (`require_uas_enabled`).

---

## Design-Prinzipien

- **Einfachheit vor Cleverness**: Das Tool wird im Stress-Szenario bedient. Lieber eine Funktion weniger als eine unverständliche.
- **Keine Dependencies ohne klaren Nutzen**: Jede neue Dependency erhöht die Komplexität.
- **Keine Mocks in Tests**: Echte Datenbank-Tests verhindern Divergenz.
- **Server is Source of Truth**: Client-State ist nur für UI-Komfort, nie für Entscheidungen.

## Datenbankmigrationen — Regeln

Beim Schreiben neuer Migrationen immer beachten:

**BIGINT-Regel:** `fire_dept.id` und alle anderen PKs sind `BIGINT`. FK-Spalten, die darauf zeigen, müssen ebenfalls `BIGINT` sein — nicht `INT`. MariaDB erzeugt bei Typabweichung errno 150.

**Idempotenz:** Migrationen müssen bei Retry-Ausführung nicht scheitern. `ADD COLUMN IF NOT EXISTS` verwenden, FK-Anlage mit INFORMATION_SCHEMA-Prüfung absichern. Referenzbeispiel: `0044_multitenancy_pr1_infrastructure.py`.

**FK-Cleanup vor ALTER TABLE:** Wenn eine referenzierte Tabelle strukturell geändert wird (DROP PRIMARY KEY, Spaltentyp ändern), müssen vorher alle FKs, die auf sie zeigen, entfernt werden — sonst errno 150 beim internen Table-Rename. Muster: `_drop_all_fks_referencing(conn, "tabellenname")` über `INFORMATION_SCHEMA.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_NAME = :t`.

Detaillierte Hinweise: [docs/MIGRATION_RUNBOOK.md](../MIGRATION_RUNBOOK.md)

## Issues und Bugs

Issues auf GitHub: https://github.com/BattloXX/Einsatzcockpit/issues

Bug-Report enthält:
1. Beschreibung was erwartet wird
2. Beschreibung was tatsächlich passiert
3. Schritte zum Reproduzieren
4. Browser, OS, Einsatz-ID (falls relevant)
