"""Verbindung, Migration, Pool und MariaDB-Langlaeufer ohne SQL-Texte."""
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
