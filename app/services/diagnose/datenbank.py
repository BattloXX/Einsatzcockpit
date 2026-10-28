"""Verbindung, Migration, Pool und MariaDB-Langlaeufer ohne SQL-Texte."""
import os
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.services.diagnose.registry import CheckResult, Finding, pool_metrics, register_check


@register_check("db.zustand", "datenbank", "Verbindung, Pool und Migrationsstand")
def check(db, budget):
    result = CheckResult()
    db.execute(text("SELECT 1"))
    # Keine Schema-Inspektion: SQLite-PRAGMA waere kein SELECT.
    revisions = sorted(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
    heads = sorted(ScriptDirectory(str(Path(__file__).resolve().parents[3] / "alembic")).get_heads())
    if revisions != heads:
        result.findings.append(Finding("migration", "Migrationsstand weicht vom Code ab",
                                       evidenz={"migration_aktuell": False}))
    bind = db.get_bind()
    pool = budget.runtime.get("pool", pool_metrics(bind.engine.pool))
    checkedout, capacity = pool["belegt"], pool["kapazitaet"]
    if capacity and checkedout / capacity >= 0.9:
        result.findings.append(Finding("pool", "DB-Pool stark ausgelastet", evidenz=pool))
    if bind.dialect.name not in ("mysql", "mariadb"):
        result.hinweis = "MariaDB-Prozess-/Transaktionspruefung: nicht verfuegbar"
        return result
    # Keine Benutzer, Hosts, DB-Namen oder Query-Texte lesen.
    rows = db.execute(text("SELECT ID, TIME FROM information_schema.processlist "
        "WHERE COMMAND <> 'Sleep' AND TIME > 30 ORDER BY TIME DESC LIMIT 50")).all()
    if rows:
        result.findings.append(Finding("langlaeufer", "Lange laufende DB-Abfragen", evidenz={"anzahl": len(rows)},
                                       betroffene_ids={"db_prozess": [int(r[0]) for r in rows]}))
    rows = db.execute(text("SELECT trx_mysql_thread_id FROM information_schema.innodb_trx "
                           "WHERE trx_state = 'LOCK WAIT' LIMIT 50")).all()
    if rows:
        result.findings.append(Finding("locks", "Wartende DB-Transaktionen", evidenz={"anzahl": len(rows)},
                                       betroffene_ids={"db_prozess": [int(r[0]) for r in rows]}))
    return result


@register_check("db.snapshot_isolation", "datenbank", "Snapshot-Isolation und worker-lokale DB-Konflikte")
def check_snapshot_isolation(db, budget):
    from app.config import settings
    from app.db import supports_mariadb_snapshot_isolation
    from app.services.db_transactions import retry_statistik

    # Isolierte Checks bekommen den Stand des ausfuehrenden App-Workers,
    # nicht die stets leeren Zaehler des kurzlebigen Diagnoseprozesses.
    statistik = budget.runtime.get("db_retries")
    if statistik is None:
        statistik = retry_statistik()
    zaehler = {str(code): statistik.get(code, 0) for code in (1020, 1205, 1213)}
    pid = budget.runtime.get("worker_pid", os.getpid())
    detail = f"Retry-Zaehler pro Worker (prozesslokal, PID {pid}, seit Prozessstart): {zaehler}."
    result = CheckResult()
    gesamt = sum(zaehler.values())
    if gesamt:
        # Ein einzelner Konflikt ist bei Parallelbetrieb erwartbar. Ab zehn
        # erkannten Konflikten seit Prozessstart warnen; keine Fehlerrate ableiten.
        result.findings.append(Finding(
            "retry_konflikte", "Worker hat DB-Konflikte erkannt",
            schweregrad="warn" if gesamt >= 10 else "info",
            evidenz={"worker_pid": pid, "zaehler": zaehler, "warnschwelle": 10},
            ursache_hypothese="Hypothese: Parallele DB-Transaktionen konkurrieren um dieselben Zeilen.",
            empfehlung="Zaehlerentwicklung und DB-Konfliktlogs dieses Workers pruefen; Fehler 1020 beachten.",
        ))
    dialect = db.get_bind().dialect
    if (dialect.name not in ("mysql", "mariadb")
            or getattr(dialect, "is_mariadb", None) is False):
        result.hinweis = "Snapshot-Isolation: uebersprungen (keine MariaDB). " + detail
        return result
    version = str(db.execute(text("SELECT @@version")).scalar_one())
    if not supports_mariadb_snapshot_isolation(version):
        result.hinweis = f"Snapshot-Isolation: uebersprungen (Version {version}, keine MariaDB >= 11.6.2). " + detail
        return result
    global_wert, session_wert, isolation = db.execute(text(
        "SELECT @@GLOBAL.innodb_snapshot_isolation, @@SESSION.innodb_snapshot_isolation, "
        "@@transaction_isolation"
    )).one()
    global_on = str(global_wert).upper() in ("1", "ON")
    session_on = str(session_wert).upper() in ("1", "ON")
    schalter = settings.DB_SNAPSHOT_ISOLATION_OFF
    result.hinweis = (
        f"MariaDB {version}; innodb_snapshot_isolation GLOBAL={'ON' if global_on else 'OFF'}, "
        f"SESSION={'ON' if session_on else 'OFF'}; transaction_isolation={isolation}; "
        f"DB_SNAPSHOT_ISOLATION_OFF={schalter}. " + detail
    )
    if session_on or not schalter:
        result.findings.append(Finding(
            "snapshot_regel", "Snapshot-Isolation: Risiko fuer Fehler 1020",
            schweregrad="warn" if schalter else "info",
            evidenz={"global_on": global_on, "session_on": session_on, "schalter": schalter},
            ursache_hypothese=("Hypothese: Session weicht von der zentralen Snapshot-Regel ab."
                              if schalter else "Die zentrale Snapshot-Regel ist per Konfiguration deaktiviert."),
            empfehlung=("DB_SNAPSHOT_ISOLATION_OFF und Worker-Neustart pruefen. Bei aktiver Snapshot-Isolation "
                        "koennen Locking-Reads und Updates mit Fehler 1020 scheitern; auch Servervorgabe pruefen."),
        ))
    return result
