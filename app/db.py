import logging
import re
import time
import traceback
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    hide_parameters=True,  # SQL-Fehler duerfen keine SMS-Inhalte oder Tokenwerte protokollieren.
    pool_recycle=300,
    pool_size=settings.DB_POOL_SIZE,
    # Audit PR 7: DB-lastige Routen laufen als sync def im anyio-Threadpool
    # (Default: 40 Threads). Der Standard deckt diese 40 ab; bei mehreren Workern
    # kleinere Pools gegen das gesamte DB-Verbindungsbudget abwaegen.
    max_overflow=settings.DB_MAX_OVERFLOW,
)

_slow_query_logger = logging.getLogger("einsatzleiter.slow_query")
_db_logger = logging.getLogger("einsatzleiter.db")


def supports_mariadb_snapshot_isolation(server_version: str) -> bool:
    """Erkennt MariaDB ab 11.6.2, auch mit altem 5.5.5-Kompatibilitaetspraefix."""
    version = re.search(r"(\d+)\.(\d+)\.(\d+)-MariaDB", server_version, re.IGNORECASE)
    return version is not None and tuple(map(int, version.groups())) >= (11, 6, 2)


def apply_snapshot_isolation_off(dbapi_connection, enabled: bool) -> None:
    """Stellt fuer Locking-Reads das MariaDB-Verhalten vor 11.6.2 wieder her.

    Auch fuer Connect-Listener anderer Engines verwendbar. Der Schalter laesst
    bei false die Servervorgabe unveraendert; Fehler verhindern keinen Connect.
    """
    if not enabled:
        return
    try:
        get_server_info = getattr(dbapi_connection, "get_server_info", None)
        if get_server_info is None or not supports_mariadb_snapshot_isolation(get_server_info()):
            return
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_snapshot_isolation=OFF")
    except Exception as exc:
        if exc.args and exc.args[0] == 1193:
            _db_logger.warning("innodb_snapshot_isolation unbekannt (1193); Servervorgabe bleibt aktiv.")
        else:
            # Keine Treibertexte protokollieren: diese koennen Zugangsdaten enthalten.
            _db_logger.warning("Snapshot-Isolation konnte nicht deaktiviert werden (%s).", type(exc).__name__)


def register_slow_query_logging(target_engine, threshold_ms: int) -> None:
    """Loggt Queries oberhalb der Schwelle als WARNING (Audit B7).

    Messbasis fuer gezielte Index-/N+1-Fixes: erst messen, dann optimieren.
    Landet ueber den Log-Buffer auch in der Sysadmin-Log-Ansicht.
    """
    if threshold_ms <= 0:
        return

    @event.listens_for(target_engine, "before_cursor_execute")
    def _sq_start(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("sq_start", []).append(time.monotonic())

    @event.listens_for(target_engine, "after_cursor_execute")
    def _sq_end(conn, cursor, statement, parameters, context, executemany):
        starts = conn.info.get("sq_start")
        if not starts:
            return
        elapsed_ms = (time.monotonic() - starts.pop()) * 1000
        if elapsed_ms >= threshold_ms:
            aufrufer = ""
            app_pfad = Path(__file__).resolve().parent
            for frame in reversed(traceback.extract_stack()):
                pfad = Path(frame.filename).resolve()
                if pfad == app_pfad / "db.py" or not pfad.is_relative_to(app_pfad):
                    continue
                aufrufer = f" aus {pfad.relative_to(app_pfad.parent)}:{frame.lineno}"
                break
            _slow_query_logger.warning(
                "Langsame Query: %.0f ms%s — %s", elapsed_ms, aufrufer, statement[:500],
            )


register_slow_query_logging(engine, settings.SLOW_QUERY_LOG_MS)


# Setzt die Session-Zeitzone auf UTC, damit naive datetimes (die wir als UTC
# behandeln) niemals doppelt konvertiert werden – unabhängig davon, was in
# der globalen MariaDB-/MySQL-Konfiguration eingestellt ist.
@event.listens_for(engine, "connect")
def _set_db_timezone_utc(dbapi_connection, connection_record):
    try:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
    except Exception:
        _db_logger.debug("Session-Zeitzone nicht gesetzt; Backend unterstuetzt SET time_zone nicht.")
    apply_snapshot_isolation_off(dbapi_connection, settings.DB_SNAPSHOT_ISOLATION_OFF)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# Tenant-Filter-Listener global registrieren (einmalig beim Modul-Import)
from app.core.feed_rev import register_feed_rev_listener  # noqa: E402
from app.core.tenant import register_tenant_listener  # noqa: E402

register_tenant_listener()
register_feed_rev_listener()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
