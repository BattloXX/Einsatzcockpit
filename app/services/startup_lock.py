"""DB-weiter Lock fuer den Schema-/Seed-Aufbau beim Worker-Start."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

logger = logging.getLogger("einsatzleiter.startup_lock")


@contextmanager
def startup_lock(engine: Engine) -> Iterator[None]:
    """Dedizierte Connection halten: GET_LOCK ist unabhaengig von Transaktionen."""
    if engine.dialect.name not in ("mysql", "mariadb"):
        yield
        return

    with engine.connect() as connection:
        acquired = connection.execute(text("SELECT GET_LOCK('ecp_bootstrap', 60)")).scalar()
        if acquired != 1:
            logger.warning("Startup-Lock ecp_bootstrap nicht erhalten (result=%s); Start wird fortgesetzt", acquired)
        try:
            yield
        finally:
            if acquired == 1:
                connection.execute(text("SELECT RELEASE_LOCK('ecp_bootstrap')"))
