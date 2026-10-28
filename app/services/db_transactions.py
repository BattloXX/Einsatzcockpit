"""Aktuelle Queue-Lesesichten und begrenzte Wiederholung reiner DB-Transaktionen."""

import logging
import secrets
import time
from collections.abc import Callable, Collection
from threading import Lock

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = logging.getLogger("einsatzleiter.db_transactions")


_WIEDERHOLBARE_CODES = (1020, 1205, 1213)
_retry_zaehler: dict[int, int] = {}
_retry_lock = Lock()


def ist_wiederholbar(exc: BaseException, *, codes: Collection[int] = _WIEDERHOLBARE_CODES) -> bool:
    """Erkennt MariaDB-Konflikte und zaehlt sie auch ohne erlaubte Wiederholung.

    Nur der numerische DBAPI-Code wird protokolliert, keine SQL-/Parameterdaten.
    Mit codes kann der Aufrufer seine bisherige, engere Auswahl beibehalten.
    """
    if not isinstance(exc, OperationalError):
        return False
    args = getattr(exc.orig, "args", ())
    if not args or args[0] not in _WIEDERHOLBARE_CODES:
        return False
    code = args[0]
    with _retry_lock:
        _retry_zaehler[code] = _retry_zaehler.get(code, 0) + 1
    logger.warning("Wiederholbarer MariaDB-Fehler (code=%s)", code)
    return code in codes


def retry_statistik() -> dict[int, int]:
    """Liefert eine unabhaengige Kopie der pro Prozess erkannten Konflikte."""
    with _retry_lock:
        return _retry_zaehler.copy()


def mit_wiederholung[T](
    db: Session, funktion: Callable[[], T], versuche: int = 3,
    codes: Collection[int] = _WIEDERHOLBARE_CODES,
) -> T:
    """Wiederholt ausschliesslich vollstaendige DB-Versuche ohne Aussenwirkung.

    Der Callback besitzt einen erforderlichen Commit selbst und muss nach Rollback
    alle Daten neu lesen. Keine Wiederholung nach Versand, Dateiuebergabe oder
    Broadcast; unbekannte Commit-Ausgaenge sind keine wiederholbaren Konflikte.
    Andere Fehler bleiben unveraendert, inklusive der Rollback-Verantwortung.
    """
    if versuche < 1:
        raise ValueError("Mindestens ein Versuch erforderlich")
    for versuch in range(versuche):
        try:
            return funktion()
        except OperationalError as exc:
            if not ist_wiederholbar(exc, codes=codes):
                raise
            db.rollback()
            if versuch == versuche - 1:
                raise
            time.sleep(0.01 * (versuch + 1) + secrets.randbelow(10) / 1000)
    raise AssertionError("DB-Wiederholung ohne Ergebnis")


def retry_claim[T](db: Session, operation: Callable[[], T]) -> T:
    """Nur eigenstaendige DB-Claims ohne Aussenwirkung und mit eigenem Commit.

    Die Operation muss ihre Schluessel vor dem ersten Versuch sichern und darf
    keine Aenderungen des Aufrufers enthalten: Rollback verwirft den ganzen Versuch.
    SQLite und andere Fehler bleiben unveraendert.
    """
    if db.get_bind().dialect.name not in ("mysql", "mariadb"):
        return operation()
    return mit_wiederholung(db, operation, codes=(1020,))


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
            if not ist_wiederholbar(error, codes=(1020, 1213)) or attempt == 2:
                raise
            logger.warning(
                "DB-Transaktion wiederholen (operation=%s, attempt=%s)", label, attempt + 1
            )
            time.sleep(0.02 * (attempt + 1))
        except BaseException:
            db.rollback()
            raise
    raise RuntimeError("DB-Transaktion ohne Ergebnis beendet")
