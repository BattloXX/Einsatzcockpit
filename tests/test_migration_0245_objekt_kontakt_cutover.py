"""Absicherung des finalen Objektkontakt-Cutovers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0245_objekt_kontakt_cutover.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0245", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _upgrade(conn) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        _migration().upgrade()


def _schema(conn, *, kontakt_id: int | None) -> None:
    conn.exec_driver_sql("CREATE TABLE kontakt (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE objekt (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql(
        "CREATE TABLE objekt_kontakt ("
        "id INTEGER PRIMARY KEY, org_id INTEGER, objekt_id INTEGER NOT NULL, kontakt_id INTEGER, "
        "art VARCHAR(50) NOT NULL, name VARCHAR(150) NOT NULL, telefone_json TEXT, "
        "email VARCHAR(200), erreichbarkeit VARCHAR(200), "
        "benachrichtigung_mail BOOLEAN NOT NULL DEFAULT 0)"
    )
    conn.exec_driver_sql("INSERT INTO kontakt (id) VALUES (1)")
    conn.exec_driver_sql("INSERT INTO objekt (id) VALUES (1)")
    conn.execute(
        sa.text(
            "INSERT INTO objekt_kontakt "
            "(id, org_id, objekt_id, kontakt_id, art, name, benachrichtigung_mail) "
            "VALUES (1, 1, 1, :kontakt_id, 'sonstig', 'Alt', 0)"
        ),
        {"kontakt_id": kontakt_id},
    )


def test_cutover_bricht_mit_unmigrierter_zuordnung_ab(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0245_missing.db'}")
    with engine.begin() as conn:
        _schema(conn, kontakt_id=None)
        with pytest.raises(RuntimeError, match="keinen zentralen Kontakt"):
            _upgrade(conn)


def test_cutover_macht_zentralen_kontakt_pflicht_und_entfernt_snapshots(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0245_ok.db'}")
    with engine.begin() as conn:
        _schema(conn, kontakt_id=1)
        _upgrade(conn)
        spalten = {spalte["name"]: spalte for spalte in sa.inspect(conn).get_columns("objekt_kontakt")}
        assert {"name", "telefone_json", "email", "benachrichtigung_mail"}.isdisjoint(spalten)
        assert spalten["kontakt_id"]["nullable"] is False
