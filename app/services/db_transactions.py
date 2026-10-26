"""Aktuelle Queue-Lesesichten und begrenzte Wiederholung reiner DB-Transaktionen."""

import logging
import time
from collections.abc import Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger("einsatzleiter.db_transactions")


def current_read_session(factory: Callable[[], Session]) -> Session:
    """Nur neue Queue-Sessions umstellen, niemals eine laufende Transaktion aendern."""
    db = factory()
    try:
        bind = db.get_bind()
        if bind.dialect.name in ("mysql", "mariadb"):
            # Session-lokaler Engine-Ableger: nach commit/rollback wird dieselbe
            # Isolation erneut angewendet, ohne Engine/Pool anderer Sessions zu aendern.
            db.bind = bind.execution_options(isolation_level="READ COMMITTED")
        return db
    except BaseException:
        db.close()
        raise


def commit_db_operation[T](db: Session, operation: Callable[[], T], *, label: str) -> T:
    """Operation muss frei von Datei-/Netz-/Versandwirkungen sein. Commit gehoert hierher.

    Nur 1020/1213 bedeuten hier einen verworfenen DB-Versuch. Nach erfolgreichem
    Commit kehrt die Funktion sofort zurueck; nachgelagerte Wirkungen nie wiederholen.
    """
    for attempt in range(3):
        try:
            result = operation()
            db.commit()
            return result
        except OperationalError as error:
            db.rollback()
            args = getattr(error.orig, "args", ())
            code = args[0] if args else None
            if code not in (1020, 1213) or attempt == 2:
                raise
            logger.warning(
                "DB-Transaktion wiederholen (operation=%s, code=%s, attempt=%s)", label, code, attempt + 1
            )
            time.sleep(0.02 * (attempt + 1))
        except BaseException:
            db.rollback()
            raise
    raise RuntimeError("DB-Transaktion ohne Ergebnis beendet")
