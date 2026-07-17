import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=10,
    max_overflow=20,
)

_slow_query_logger = logging.getLogger("einsatzleiter.slow_query")


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
            _slow_query_logger.warning(
                "Langsame Query: %.0f ms — %s", elapsed_ms, statement[:500],
            )


register_slow_query_logging(engine, settings.SLOW_QUERY_LOG_MS)


# Setzt die Session-Zeitzone auf UTC, damit naive datetimes (die wir als UTC
# behandeln) niemals doppelt konvertiert werden – unabhängig davon, was in
# der globalen MariaDB-/MySQL-Konfiguration eingestellt ist.
@event.listens_for(engine, "connect")
def _set_db_timezone_utc(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET time_zone = '+00:00'")
        cursor.close()
    except Exception:
        pass  # SQLite und andere Backends ignorieren diesen Befehl

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# Tenant-Filter-Listener global registrieren (einmalig beim Modul-Import)
from app.core.tenant import register_tenant_listener  # noqa: E402

register_tenant_listener()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
