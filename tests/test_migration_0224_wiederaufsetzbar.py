"""Migration 0224 muss nach einem Abbruch erneut startbar sein.

Vorfall 2026-08-26 (Testsystem): MySQL committet DDL sofort ("Will assume
non-transactional DDL"). Der Constraint-Tausch scheiterte mit Fehler 1553
("Cannot drop index ...: needed in a foreign key constraint"), weil der
Fremdschluessel auf incident_id den alten Unique-Index als Deckung brauchte.
Die Spalten-Drops davor waren da bereits committet, alembic_version stand aber
noch auf 0223 - ein zweiter Lauf lief in "Unknown column benachrichtigung_sms".

Diese Tests halten fest, dass jeder Schritt der Migration pruefbar uebersprungen
wird und die Migration daher wiederaufsetzbar ist.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0224_objekt_kontakt_telefon_freigabe.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0224", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture()
def engine(tmp_path):
    return sa.create_engine(f"sqlite:///{tmp_path / 'm0224.db'}")


def _basis_tabellen(conn, *, mit_altspalten: bool):
    conn.exec_driver_sql("CREATE TABLE incident (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql(
        "CREATE TABLE objekt (id INTEGER PRIMARY KEY"
        + (", kontakt_info_enabled BOOLEAN NOT NULL DEFAULT 0" if mit_altspalten else "")
        + ")"
    )
    conn.exec_driver_sql(
        "CREATE TABLE objekt_kontakt (id INTEGER PRIMARY KEY, objekt_id INTEGER, telefone_json TEXT,"
        " benachrichtigung_mail BOOLEAN NOT NULL DEFAULT 0"
        + (", benachrichtigung_sms BOOLEAN NOT NULL DEFAULT 0, benachrichtigung_telefon VARCHAR(30)"
           if mit_altspalten else "")
        + ")"
    )
    conn.exec_driver_sql(
        "CREATE TABLE objekt_kontakt_benachrichtigung ("
        " id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL REFERENCES incident(id),"
        " objekt_kontakt_id INTEGER, kanal VARCHAR(10) NOT NULL, empfaenger VARCHAR(200) NOT NULL,"
        " CONSTRAINT uq_objekt_kontakt_benachrichtigung UNIQUE (incident_id, objekt_kontakt_id, kanal))"
    )


def _upgrade(engine, modul, male: int = 1):
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            for _ in range(male):
                modul.upgrade()


def test_upgrade_ist_mehrfach_ausfuehrbar(engine):
    """Zweiter Lauf darf nicht an bereits geloeschten Spalten scheitern."""
    modul = _migration()
    with engine.begin() as conn:
        _basis_tabellen(conn, mit_altspalten=True)
        conn.exec_driver_sql(
            "INSERT INTO objekt_kontakt (id, objekt_id, telefone_json, benachrichtigung_sms,"
            " benachrichtigung_telefon) VALUES (1, 1,"
            " '[\"Mobil beruflich: +43 664 1\", \"+43 555 2\"]', 1, '0043-664/1')"
        )

    _upgrade(engine, modul, male=2)

    with engine.connect() as conn:
        spalten = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(objekt_kontakt)")}
        assert "benachrichtigung_sms" not in spalten
        assert modul._unique_spalten(
            conn, "objekt_kontakt_benachrichtigung", modul._UQ
        ) == ["incident_id", "objekt_kontakt_id", "kanal", "empfaenger"]
        eintraege = modul._eintraege(
            conn.exec_driver_sql("SELECT telefone_json FROM objekt_kontakt").scalar()
        )
    # Label korrekt abgetrennt, Freigabe auf der normalisiert passenden Nummer
    assert eintraege[0] == {"nummer": "+43 664 1", "label": "Mobil beruflich", "sms": True}
    assert eintraege[1]["sms"] is False


def test_upgrade_setzt_nach_abbruch_beim_constraint_tausch_auf(engine):
    """Exakter Zustand des Testsystems: Spalten weg, telefone_json konvertiert,
    Unique-Constraint aber noch der alte Dreispalter."""
    modul = _migration()
    with engine.begin() as conn:
        _basis_tabellen(conn, mit_altspalten=False)
        conn.exec_driver_sql(
            "INSERT INTO objekt_kontakt (id, objekt_id, telefone_json, benachrichtigung_mail)"
            " VALUES (1, 1, '[{\"nummer\": \"+43 664 1\", \"label\": null, \"sms\": true}]', 1)"
        )

    _upgrade(engine, modul)

    with engine.connect() as conn:
        assert modul._unique_spalten(
            conn, "objekt_kontakt_benachrichtigung", modul._UQ
        ) == ["incident_id", "objekt_kontakt_id", "kanal", "empfaenger"]
        # Die bereits konvertierten Daten duerfen nicht angefasst werden
        eintraege = modul._eintraege(
            conn.exec_driver_sql("SELECT telefone_json FROM objekt_kontakt").scalar()
        )
    assert eintraege == [{"nummer": "+43 664 1", "label": None, "sms": True}]
