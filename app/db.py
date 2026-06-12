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
