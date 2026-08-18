"""Separate Datenbank für die Wetterstations-Zeitreihe.

Die historischen Messwerte lokaler Wetterstationen (Davis/Meteobridge) werden
bewusst NICHT in der operativen Haupt-DB gehalten, sondern in einer eigenen DB
(`einsatzleiter_weather`) mit eigenem Connection-Pool:

- Die Haupt-DB (und ihr Pool) bleibt für einsatzrelevante Funktionen reserviert
  → Wetter-Last konkurriert nie mit dem operativen Betrieb (Einsatz hat Vorrang).
- Die große Zeitreihen-Tabelle bläht die operative DB nicht auf; Retention löscht
  alte Werte unabhängig.
- Backup/Restore der Wetterdaten ist von der operativen DB entkoppelt.

Aktiviert nur, wenn `settings.WEATHER_DATABASE_URL` gesetzt ist. Ist die Variable
leer, liefert `weather_db_enabled()` False und die Zeitreihen-Persistenz entfällt
(der Ist-Stand in der Haupt-DB bleibt davon unberührt).

Org-Isolation: Die Modelle hier hängen an einer EIGENEN `WeatherBase`, daher greift
der globale Tenant-Listener (do_orm_execute) nicht automatisch. Jede Abfrage MUSS
deshalb im Service explizit nach `org_id` filtern.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger("einsatzleiter.weather")


class WeatherBase(DeclarativeBase):
    """Eigene Declarative-Base für die Wetter-DB (getrennt von app.db.Base)."""


_engine = None
_SessionLocal = None


def weather_db_enabled() -> bool:
    """True, wenn eine separate Wetter-DB konfiguriert ist."""
    return bool(settings.WEATHER_DATABASE_URL)


def _ensure_engine():
    global _engine, _SessionLocal
    if _engine is None:
        if not weather_db_enabled():
            raise RuntimeError(
                "Wetter-DB nicht konfiguriert (WEATHER_DATABASE_URL leer)."
            )
        _engine = create_engine(
            settings.WEATHER_DATABASE_URL,
            pool_pre_ping=True,
            pool_recycle=300,
            # Kleinerer Pool als die Haupt-DB: Wetter ist unkritisch und soll
            # keine DB-Verbindungen vom operativen Betrieb wegnehmen.
            pool_size=3,
            max_overflow=5,
        )
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_weather_engine():
    return _ensure_engine()


def get_weather_session():
    """Neue Session auf der Wetter-DB. Aufrufer ist für close() verantwortlich.

    Setzt zur Sicherheit current_org_id=None, damit der globale Tenant-Listener
    auch dann nicht fail-closed wirft, falls künftig ein tenant-Modell berührt wird.
    """
    _ensure_engine()
    session = _SessionLocal()
    session.info["current_org_id"] = None
    return session


def run_weather_query(fn, default):
    """Fuehrt einen unkritischen Lesezugriff auf der Wetter-DB aus.

    Ist die separate Wetter-DB nicht konfiguriert oder nicht erreichbar, wird
    der angegebene Standardwert geliefert. Der operative Ist-Stand in der
    Haupt-DB bleibt damit unabhaengig von der Wetter-Zeitreihe verfuegbar.
    """
    if not weather_db_enabled():
        return default
    session = None
    try:
        session = get_weather_session()
        return fn(session)
    except Exception as exc:
        logger.warning("Wetter-DB-Lesezugriff fehlgeschlagen: %s", exc)
        return default
    finally:
        if session is not None:
            session.close()


def init_weather_db() -> None:
    """Erstellt das Schema der Wetter-DB (idempotent) beim App-Start.

    Bewusst per create_all statt Alembic: die Wetter-DB ist von der Haupt-Alembic-
    Kette entkoppelt und enthält nur wenige, additive Tabellen.
    """
    if not weather_db_enabled():
        logger.info("Wetter-DB deaktiviert (WEATHER_DATABASE_URL leer) – kein Schema-Setup.")
        return
    # Modelle importieren, damit sie an WeatherBase.metadata registriert sind.
    from app.models import weather as _weather_models  # noqa: F401

    engine = _ensure_engine()
    WeatherBase.metadata.create_all(engine)
    logger.info("Wetter-DB-Schema initialisiert.")
