"""Versiegelter, typisierter Aktionskatalog; keine dynamischen Ausführungsbefehle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import event
from sqlalchemy.orm import Session


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


@dataclass(frozen=True)
class Vorschau:
    daten_hash: str
    objekte: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "daten_hash": self.daten_hash,
            "objekte": self.objekte,
            "ausfuehrbar": sum(x["ausfuehrbar"] for x in self.objekte),
        }


@dataclass(frozen=True)
class Ergebnis:
    geaendert: int = 0
    uebersprungen: int = 0

    def public(self) -> dict:
        return {"geaendert": self.geaendert, "uebersprungen": self.uebersprungen}


@dataclass(frozen=True)
class RepairContext:
    run_id: int
    user_id: int
    korrelations_id: str
    daten_hash: str = ""
    progress: Callable[[Ergebnis], None] = lambda result: None


class Implementation(Protocol):
    def vorbedingungen(self, db: Session, scope: Scope) -> None: ...
    def simulieren(self, db: Session, scope: Scope) -> Vorschau: ...
    def ausfuehren(self, db: Session, scope: Scope, ctx: RepairContext) -> Ergebnis: ...
    def verifizieren(self, db: Session, scope: Scope) -> Ergebnis: ...


@dataclass(frozen=True)
class Action:
    id: str
    titel: str
    risiko: str
    scope_schema: type[Scope]
    beschreibung: str
    einsatzsicher: bool
    impl: Implementation


class ActionRegistry(Mapping[str, Action]):
    def __init__(self):
        self._entries: Mapping[str, Action] = MappingProxyType({})
        self._sealed = False

    def __getitem__(self, key: str) -> Action:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def register(self, action: Action):
        if self._sealed or action.id in self._entries:
            raise ValueError("Aktionskatalog versiegelt oder Aktion bereits registriert")
        self._entries = MappingProxyType({**self._entries, action.id: action})

    def seal(self):
        self._sealed = True


ACTIONS = ActionRegistry()


def register_action(
    id: str, titel: str, risiko: str, scope_schema: type[Scope], beschreibung: str, einsatzsicher: bool = False
):
    if risiko not in ("low", "medium", "high", "critical"):
        raise ValueError("Ungueltiges Risiko")

    def decorate(cls):
        ACTIONS.register(Action(id, titel, risiko, scope_schema, beschreibung, einsatzsicher, cls()))
        return cls

    return decorate


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@contextmanager
def readonly(db: Session):
    """SQL-Sperre auch für Core/Connection; keine impliziten Flushes im Dry-Run."""
    connection = db.connection()

    def guard(conn, cursor, statement, parameters, context, executemany):
        upper = statement.lstrip().upper()
        if (
            not upper.startswith("SELECT ")
            or ";" in statement
            or any(x in upper for x in (" INTO ", "FOR UPDATE", "LOCK IN SHARE", "OUTFILE"))
        ):
            raise PermissionError("Simulation erlaubt ausschliesslich SELECT")

    event.listen(connection, "before_cursor_execute", guard)
    try:
        # Feste Connection statt Session-Pool: auch nach commit()/rollback() einer
        # fehlerhaften neuen Aktion bleibt die SQL-Sperre an derselben Verbindung.
        with Session(bind=connection, autoflush=False, join_transaction_mode="rollback_only") as reader:
            reader.info["current_org_id"] = None
            yield reader
            if reader.new or reader.dirty or reader.deleted:
                raise PermissionError("Simulation darf keine Daten aendern")

    finally:
        event.remove(connection, "before_cursor_execute", guard)


def simulate(db: Session, action: Action, scope: Scope) -> Vorschau:
    with readonly(db) as reader:
        action.impl.vorbedingungen(reader, scope)
        return action.impl.simulieren(reader, scope)
