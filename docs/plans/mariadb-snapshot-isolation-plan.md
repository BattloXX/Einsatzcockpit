# Plan: MariaDB 12.3 und Snapshot-Isolation (Fehler 1020) – durchgehend beheben

Stand: 2026-10-28. Status: Phasen 1-5 erledigt. Produktion und CI laufen mit MariaDB 12.3 (`docker-compose.yml`: `mariadb:12.3`).

## Ausgangslage

- Seit MariaDB 11.6.2 ist `innodb_snapshot_isolation=ON` Standard. Unter REPEATABLE READ
  lehnt InnoDB einen `UPDATE`, `DELETE` oder `SELECT ... FOR UPDATE` mit Fehler 1020
  (`ER_CHECKREAD`) ab, wenn die Zeile nach dem Snapshot der Transaktion geändert wurde.
  Früher las InnoDB hier stillschweigend die neueste Version.
- Der Code wurde für das alte Verhalten geschrieben: `with_for_update` kommt 106-mal in
  43 Dateien vor, dazu bedingte `UPDATE ... WHERE status=...`-Claims.
- Bisher punktuell behoben, jeweils mit eigenem Mechanismus:
  - #759: `retry_claim` (Alarm-Outbox) und `SET STATEMENT ... FOR SELECT` (Druck-Dedup).
  - `db_transactions.py`: Retry mit READ COMMITTED für Artefaktaufbau.
  - SMS-/Routing-Sessions: `READ COMMITTED` über Engine-Ableger.
  - Eigene Retries in `storage_service`, `objekt_dokument_service`, `katplan_*`,
    `gateway_api`, `resource_service`, `repair/runner`.
- Ergebnis: Flickenteppich. Jede nicht abgedeckte Stelle kann in Produktion unter Last
  mit HTTP 500 oder einem verlorenen Claim enden. Nur MariaDB-Tests decken das ab,
  SQLite-Tests zeigen es nie.

## Zielbild

Eine zentrale, getestete Regel statt verstreuter Einzelfixes:

1. Anwendungsverbindungen verhalten sich wie vor 11.6.2 (kein 1020 bei Locking-Reads).
2. Wo trotzdem Konflikte möglich bleiben (Deadlock 1213, Lock-Timeout 1205), gibt es eine
   einzige Retry-Hilfe statt acht Kopien.
3. Ein MariaDB-Test pro Muster beweist das Verhalten gegen die echte Version.

## Entscheidung A: Wie wird 1020 global entschärft?

| Option | Wirkung | Risiko |
|---|---|---|
| A1 `SET SESSION innodb_snapshot_isolation=OFF` im `connect`-Event von `app/db.py` | Genau das alte Verhalten für alle Anwendungs-Sessions; Server-Konfiguration unverändert | Gering. Variable existiert nur ab 11.6.2, also nur dort setzen oder Fehler abfangen |
| A2 Engine-weit `isolation_level="READ COMMITTED"` | Jede Query bekommt frischen Snapshot, kein 1020 | Verhaltensänderung für alle Lesezugriffe (Nicht-wiederholbares Lesen), größere Testfläche |
| A3 `innodb_snapshot_isolation=OFF` in `my.cnf`/Compose | Wirkt für alle Clients inkl. Alembic und Skripte | Eingriff in die DB-Konfiguration, nicht im Repo-Code nachvollziehbar |

**Empfehlung: A1**, ergänzt um A3 als dokumentierte Option für Betreiber. A1 ändert nur
das, was MariaDB selbst geändert hat, und bleibt im Repo versioniert. Die bereits
bestehenden READ-COMMITTED-Ableger bleiben unverändert.

## Phasen

### Phase 1: Zentrale Session-Einstellung

- `app/db.py`: im vorhandenen `connect`-Listener nach `SET time_zone` zusätzlich
  `SET SESSION innodb_snapshot_isolation=OFF` ausführen, nur für MariaDB-Dialekt.
  Versions-Check über `dbapi_connection.get_server_info()`; ältere Server und MySQL
  überspringen. Fehler 1193 (unbekannte Variable) abfangen und protokollieren.
- Neue Einstellung `DB_SNAPSHOT_ISOLATION_OFF` (Standard `true`) in `app/config.py` und
  `deploy/docker.env.example`, damit der Schalter im Notfall ohne Code-Änderung zurückgenommen
  werden kann.
- Gilt auch für `db_weather.py`? Nein, eigene Datenbank, keine Locking-Reads; im Plan nur
  prüfen und im Code kommentieren.
- Test (`tests/mariadb`): zwei Verbindungen, A liest, B ändert und committet, A führt
  `SELECT ... FOR UPDATE` und bedingten `UPDATE` aus. Erwartung: kein 1020, A sieht die
  neueste Version. Zweiter Test: Schalter `false` liefert wieder 1020 (Beleg, dass der
  Test das richtige Problem trifft).

### Phase 2: Eine Retry-Hilfe statt acht Kopien

- `app/services/db_transactions.py` zur einzigen Quelle ausbauen: `ist_wiederholbar(exc)`
  (1020, 1205, 1213), `mit_wiederholung(db, funktion, versuche=3)` mit Rollback, kurzer
  Wartezeit mit Jitter und Logging.
- Bestehende Kopien umstellen, Verhalten pro Stelle beibehalten:
  `storage_service`, `objekt_dokument_service`, `katplan_anhang_service`,
  `katplan_quelle_service`, `gateway_api`, `resource_service`, `repair/runner`,
  `alarm_outbox`.
- Bewusst NICHT automatisch wiederholen: alles nach externer Wirkung (SMS gesendet,
  Datei übergeben, Broadcast), und Commits mit unbekanntem Ausgang.
- Tests: bestehende Tests der Stellen bleiben grün; ein Unit-Test für die Hilfe
  (Fehlercodes, Versuchsgrenze, kein Retry bei anderem Fehler).

### Phase 3: Inventur gegen die echte DB absichern

- Codex-Inventur der ca. 40 `FOR UPDATE`-Pfade liegt vor (aus #759). Mit Phase 1 sind sie
  strukturell entschärft; die Inventur dient jetzt als Testliste.
- Konfliktbezogene Tests mit zwei Verbindungen für die Hochrisikopfade ergänzen:
  SMS-Outbox, Gateway-Heartbeat, Dokumentverarbeitung (Worker-Claim), Reparatur-Runner,
  Einheiten-Übernahme. Jeweils: Gewinner eindeutig, kein 500, keine Doppelverarbeitung.
- Lint-Regel als Test: neuer `with_for_update`-Aufruf ohne Eintrag in der Inventurliste
  lässt einen Test fehlschlagen und erinnert an MariaDB-Test und Retry-Entscheidung.

### Phase 4: Betrieb und Diagnose

- Diagnose-Check im Admin-MCP/Systemdiagnose: liest `@@version`,
  `@@innodb_snapshot_isolation` (global und Session) und `@@transaction_isolation`,
  meldet Abweichungen vom Sollzustand.
- Zähler für 1020/1213/1205 in `db_transactions` (Logzeile und Metrik), damit Restfälle in
  Produktion sichtbar werden, statt als 500 zu verschwinden.
- Wiki: Abschnitt in der Administrationsdoku (Hintergrund, Schalter, Diagnose,
  Notfall-Rücknahme). `docs/multi-worker/multi-worker-phase6.md` auf die neue Regel verweisen.

### Phase 5: Flaky Tests im selben Zug

- `test_08_folgenachrichten_nachalarm_und_manuelle_adresse` (assert 10 == 9):
  Ursache klären (wahrscheinlich Zeit-/Reihenfolge-Abhängigkeit), deterministisch machen.
- `test_sqlite_timeout_and_row_budget` (0,001-s-Timeout): Timeout-Race durch
  kontrollierte Verzögerung oder Fake-Uhr ersetzen.
- Abnahme: je 30 Wiederholungen lokal grün und drei CI-Läufe ohne Rerun.

## Reihenfolge und Umfang

| Phase | PR | Aufwand | Abhängigkeit |
|---|---|---|---|
| 1 Session-Einstellung + Tests | 1 | klein | – |
| 2 Retry-Hilfe vereinheitlichen | 1 | mittel | 1 |
| 3 Hochrisiko-Tests + Inventurtest | 1 | mittel | 1 |
| 4 Diagnose + Wiki | 1 | klein | 1 |
| 5 Flaky Tests | 1 | klein | – |

Umsetzung wie üblich: Codex implementiert pro Phase, ich verifiziere (ruff, mypy,
pytest -n auto, `tests/mariadb` gegen frische MariaDB 12.3), committe, pushe und merge nach
grüner CI.

## Risiken

- Session-Variable wirkt nicht auf bereits offene gepoolte Verbindungen: nach Deploy ist
  der Neustart der Worker ohnehin Standard, `pool_recycle=300` räumt den Rest ab.
- `connect`-Event greift nur für diese Engine. Skripte mit eigener Engine
  (`diagnose/registry.py`, `cli`) prüfen und bei Bedarf mit derselben Hilfsfunktion
  ausstatten.
- Mit ausgeschaltetem Snapshot-Schutz kehren Lost-Update-Muster zurück, wo Code nicht
  sperrt (lesen, ändern, schreiben ohne `FOR UPDATE`). Das war vor 11.6.2 identisch
  und wird nicht schlechter; Phase 3 benennt die auffälligsten Stellen.

## Offene Entscheidungen für dich

1. A1 (Session-Variable im Code) wie empfohlen, oder lieber A3 (Serverkonfiguration)?
2. Retry-Vereinheitlichung (Phase 2) jetzt mitnehmen oder erst nach Beobachtung der Zähler
   aus Phase 4?
3. Flaky-Tests (Phase 5) im selben Vorhaben?
