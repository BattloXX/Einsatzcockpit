"""Registrierbare SELECT-Pruefungen mit Zeit-, Zeilen- und Ausgabebudget."""
from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

MAX_IDS = 50
MAX_ROWS = 2000
CHECK_TIMEOUT = 10.0


@dataclass
class Finding:
    kern: Any
    titel: str
    schweregrad: str = "warn"
    evidenz: dict = field(default_factory=dict)
    betroffene_ids: dict = field(default_factory=dict)
    ursache_hypothese: str = "Hypothese: Datenbestand oder Teilkomponente ist unvollstaendig."
    empfehlung: str = "Systemadministration soll die betroffenen IDs manuell pruefen."

    def key(self, check_id: str) -> str:
        def normalize(value):
            if isinstance(value, str):
                return unicodedata.normalize("NFKC", value).strip()
            if isinstance(value, dict):
                return {normalize(k): normalize(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalize(v) for v in value]
            return value
        normalized = json.dumps(normalize(self.kern), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256((check_id + ":" + normalized).encode()).hexdigest()


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)
    vollstaendig: bool = True
    hinweis: str | None = None


@dataclass(frozen=True)
class Check:
    id: str
    modul: str
    beschreibung: str
    kosten: str
    fn: Callable
    read_only: bool = True


class CheckRegistry(Mapping[str, Check]):
    """Deklarativer Katalog mit unveraenderlichen Snapshots, kein Laufzeitspeicher.

    Nach dem Import aller Check-Module versiegelt: Jeder Worker hat dieselben
    Definitionen; Laeufe, Limits und Befunde liegen ausschliesslich in der DB.
    """
    def __init__(self):
        self._entries: Mapping[str, Check] = MappingProxyType({})
        self._sealed = False

    def __getitem__(self, key: str) -> Check:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def register(self, check: Check):
        if self._sealed or check.id in self._entries:
            raise ValueError("Check bereits registriert oder Katalog bereits versiegelt")
        self._entries = MappingProxyType({**self._entries, check.id: check})

    def seal(self):
        self._sealed = True


CHECKS = CheckRegistry()


def register_check(id: str, modul: str, beschreibung: str, kosten: str = "leicht", read_only: bool = True):
    if not read_only or kosten not in ("leicht", "mittel", "schwer"):
        raise ValueError("Nur lesende Diagnose-Checks mit gueltigen Kosten erlaubt")

    def decorate(fn):
        CHECKS.register(Check(id, modul, beschreibung, kosten, fn))
        return fn
    return decorate


class Budget:
    def __init__(self, seconds: float = CHECK_TIMEOUT, rows: int = MAX_ROWS):
        self.deadline = time.monotonic() + seconds
        self.remaining = rows
        self.complete = True
        self.runtime: dict = {}

    def tick(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Diagnose-Zeitbudget erreicht")

    def rows(self, db: Session, query, page_size: int | None = None):
        self.tick()
        limit = min(self.remaining + 1, page_size) if page_size else self.remaining + 1
        rows = db.execute(query.limit(limit).execution_options(include_all_tenants=True)).all()
        if len(rows) > self.remaining:
            self.complete = False
        result = rows[:self.remaining]
        self.remaining -= len(result)
        return result


def execute_check(bind, check: Check, seconds: float = CHECK_TIMEOUT, runtime: dict | None = None) -> CheckResult:
    """Eigene Verbindung: SQL-Sperre erfasst ORM, Core und direkte Connection-Aufrufe.

    SQLite bricht laufende SELECTs per Progress-Handler ab; MariaDB bekommt fuer
    jedes SELECT ein serverseitiges Statement-Zeitlimit. Listener bleiben lokal.
    """
    budget = Budget(seconds)
    budget.runtime = runtime or {}
    with bind.connect() as connection:
        dialect = connection.dialect
        raw = connection.connection.driver_connection
        if dialect.name == "sqlite":
            raw.set_progress_handler(lambda: int(time.monotonic() >= budget.deadline), 1000)

        def guard(conn, cursor, statement, parameters, context, executemany):
            budget.tick()
            # Auch WITH/PRAGMA/CALL und SELECT INTO/Locking sind absichtlich verboten.
            upper = statement.lstrip().upper()
            if (not upper.startswith("SELECT ") or ";" in statement
                    or any(word in upper for word in (" INTO ", "FOR UPDATE", "LOCK IN SHARE", "OUTFILE"))):
                raise PermissionError("Diagnose erlaubt ausschliesslich SELECT")
            if dialect.name in ("mysql", "mariadb"):
                if not getattr(dialect, "is_mariadb", dialect.name == "mariadb"):
                    raise RuntimeError("DB-Zeitbudget fuer diesen Dialekt nicht verfuegbar")
                remaining = max(0.001, budget.deadline - time.monotonic())
                return f"SET STATEMENT max_statement_time={remaining:.3f} FOR {statement}", parameters
            return statement, parameters

        with Session(bind=connection, autoflush=False) as db:
            db.info["current_org_id"] = None
            event.listen(connection, "before_cursor_execute", guard, retval=True)
            try:
                result = check.fn(db, budget)
                budget.tick()
                result.vollstaendig &= budget.complete
                return result
            finally:
                event.remove(connection, "before_cursor_execute", guard)
                # Das abgelaufene SELECT-Budget darf den ROLLBACK nicht abbrechen.
                # SQLAlchemy kann eine invalidierte Verbindung bereits geschlossen haben.
                if dialect.name == "sqlite" and not connection.invalidated:
                    raw.set_progress_handler(None, 0)
                db.rollback()


def _process_check(sender, url: str, check: Check, seconds: float, runtime: dict):
    """Separater Prozess mit eigener DB-Verbindung; keine geerbten Pools/Sessions."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    # Keine Snapshot-Anpassung: execute_check verbietet Locking-Reads und Mutationen.
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        try:
            sender.send(execute_check(engine, check, seconds, runtime))
        except Exception:
            sender.send(None)  # Keine SQL-/Dateipfade oder Exceptiontexte uebertragen.
    finally:
        sender.close()
        engine.dispose()


def execute_isolated(bind, check: Check, seconds: float = CHECK_TIMEOUT) -> CheckResult:
    """Harte Prozessgrenze beendet auch blockierte Dateizugriffe oder Python-Checks.

    Die Gesamtfrist umfasst Prozessstart, Connect und SELECTs. Nach dem Timeout
    erst Prozessende bestaetigen, dann den naechsten Check zulassen.
    """
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    engine = bind.engine
    from app.services import ws_bus

    runtime = {"redis": ws_bus.status(), "pool": pool_metrics(engine.pool)}
    process = context.Process(target=_process_check,
        args=(sender, engine.url.render_as_string(hide_password=False), check, seconds, runtime), daemon=True)
    started = False
    deadline = time.monotonic() + seconds
    try:
        process.start()
        started = True
        sender.close()
        if not receiver.poll(max(0, deadline - time.monotonic())):
            raise TimeoutError("Diagnose-Zeitbudget erreicht")
        result = receiver.recv()
        if not isinstance(result, CheckResult):
            raise RuntimeError("Diagnose-Check fehlgeschlagen")
        return result
    finally:
        receiver.close()
        sender.close()
        if started:
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join()
            process.close()



def pool_metrics(pool) -> dict:
    size = getattr(pool, "size", None)
    return {"belegt": getattr(pool, "checkedout", lambda: 0)(),
            "kapazitaet": size() + max(0, getattr(pool, "_max_overflow", 0)) if callable(size) else 0}
