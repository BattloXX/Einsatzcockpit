"""SQLite-Test fuer den Objektkontakt-Backfill zu zentralen Kontakten."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import sessionmaker

from app.core.tenant import set_tenant_context
from app.services import kontakt_service

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0240_objekt_kontakte_zentral_backfill.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0240", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _lauf(conn, funktion) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        funktion()


def _schema(conn) -> None:
    conn.exec_driver_sql("CREATE TABLE fire_dept (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE objekt (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("""CREATE TABLE objekt_kontakt (
        id INTEGER PRIMARY KEY, org_id INTEGER, objekt_id INTEGER NOT NULL, kontakt_id INTEGER,
        art VARCHAR(50) NOT NULL, name VARCHAR(150) NOT NULL, telefone_json TEXT, email VARCHAR(200),
        erreichbarkeit VARCHAR(200), benachrichtigung_mail BOOLEAN NOT NULL DEFAULT 0
    )""")
    conn.exec_driver_sql("""CREATE TABLE kontakt (
        id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER, typ VARCHAR(20) NOT NULL,
        anzeigename VARCHAR(150) NOT NULL, vorname VARCHAR(100), nachname VARCHAR(100), funktion VARCHAR(150),
        organisation VARCHAR(200), email VARCHAR(200), erreichbarkeit TEXT, notizen TEXT, bild_pfad VARCHAR(500),
        aktiv BOOLEAN NOT NULL, archiviert BOOLEAN NOT NULL, version INTEGER NOT NULL,
        erstellt_am DATETIME NOT NULL, aktualisiert_am DATETIME NOT NULL,
        erstellt_von_id INTEGER, aktualisiert_von_id INTEGER
    )""")
    conn.exec_driver_sql("""CREATE TABLE kontakt_telefon (
        id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER, kontakt_id INTEGER NOT NULL,
        nummer VARCHAR(100) NOT NULL, nummer_normalisiert VARCHAR(100) NOT NULL, label VARCHAR(100),
        sort INTEGER NOT NULL, bevorzugt BOOLEAN NOT NULL, sms_eignung BOOLEAN
    )""")
    conn.exec_driver_sql("""CREATE TABLE objekt_kontakt_freigabe (
        id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER, objekt_kontakt_id INTEGER NOT NULL,
        kanal VARCHAR(10) NOT NULL, ziel_wert VARCHAR(200) NOT NULL, aktiv BOOLEAN NOT NULL,
        UNIQUE (objekt_kontakt_id, kanal, ziel_wert)
    )""")
    # get_kontakt() lädt auch Kategorien mit selectinload.
    conn.exec_driver_sql("CREATE TABLE kontakt_kategorie (id INTEGER PRIMARY KEY, org_id INTEGER, name VARCHAR(100))")
    conn.exec_driver_sql("""CREATE TABLE kontakt_kategorie_zuordnung (
        id INTEGER PRIMARY KEY, org_id INTEGER, kontakt_id INTEGER, kategorie_id INTEGER
    )""")


def test_objekt_kontakte_werden_idempotent_zentral_migriert_und_sind_lesbar(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0240.db'}")
    modul = _migration()
    with engine.begin() as conn:
        _schema(conn)
        conn.execute(sa.text("INSERT INTO fire_dept (id) VALUES (1), (2)"))
        conn.execute(sa.text("INSERT INTO objekt (id) VALUES (1), (2)"))
        conn.execute(sa.text("""INSERT INTO kontakt
            (id, org_id, typ, anzeigename, aktiv, archiviert, version, erstellt_am, aktualisiert_am)
            VALUES (99, 2, 'person', 'Schon vorhanden', 1, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""))
        zeilen = [
            {"id": 1, "org_id": 1, "objekt_id": 1, "kontakt_id": None, "art": "betreiber", "name": "  Anna Beispiel  ",
             "telefone_json": json.dumps([{"nummer": "00 43 664 123456", "label": "Mobil", "sms": True}]),
             "email": " Anna@Beispiel.at ", "erreichbarkeit": "tagsueber", "benachrichtigung_mail": True},
            {"id": 2, "org_id": 2, "objekt_id": 2, "kontakt_id": None, "art": "hausverwaltung",
             "name": "Hausverwaltung GmbH",
             "telefone_json": json.dumps(["Mobil beruflich: +43 664 777 888"]), "email": None,
             "erreichbarkeit": None, "benachrichtigung_mail": False},
            {"id": 3, "org_id": 1, "objekt_id": 1, "kontakt_id": None, "art": "sonstig", "name": "Ohne Telefon",
             "telefone_json": None, "email": None, "erreichbarkeit": None, "benachrichtigung_mail": False},
            {"id": 4, "org_id": 2, "objekt_id": 2, "kontakt_id": 99, "art": "betreiber", "name": "Nicht duplizieren",
             "telefone_json": json.dumps([{"nummer": "+43 1 123", "sms": True}]), "email": "x@y.at",
             "erreichbarkeit": None, "benachrichtigung_mail": True},
        ]
        conn.execute(sa.text("""INSERT INTO objekt_kontakt
            (id, org_id, objekt_id, kontakt_id, art, name, telefone_json, email, erreichbarkeit, benachrichtigung_mail)
            VALUES (:id, :org_id, :objekt_id, :kontakt_id, :art, :name, :telefone_json, :email, :erreichbarkeit,
                    :benachrichtigung_mail)"""), zeilen)

        _lauf(conn, modul.upgrade)
        assert conn.execute(sa.text("SELECT count(*) FROM kontakt")).scalar() == 4
        kontakte = conn.execute(sa.text("SELECT org_id, anzeigename FROM kontakt WHERE id != 99 ORDER BY id")).all()
        assert kontakte == [(1, "Anna Beispiel"), (2, "Hausverwaltung GmbH"), (1, "Ohne Telefon")]
        telefone = conn.execute(sa.text(
            "SELECT org_id, nummer, nummer_normalisiert, label, sms_eignung FROM kontakt_telefon ORDER BY id"
        )).all()
        assert telefone == [
            (1, "00 43 664 123456", "+43664123456", "Mobil", None),
            (2, "+43 664 777 888", "+43664777888", "Mobil beruflich", None),
        ]
        freigaben = conn.execute(sa.text(
            "SELECT org_id, objekt_kontakt_id, kanal, ziel_wert FROM objekt_kontakt_freigabe ORDER BY id"
        )).all()
        assert freigaben == [(1, 1, "sms", "+43664123456"), (1, 1, "mail", "anna@beispiel.at")]
        verknuepfungen = conn.execute(sa.text("SELECT id, kontakt_id FROM objekt_kontakt ORDER BY id")).all()
        assert all(kontakt_id is not None for _, kontakt_id in verknuepfungen[:3])
        assert verknuepfungen[3] == (4, 99)

        _lauf(conn, modul.upgrade)
        assert conn.execute(sa.text("SELECT count(*) FROM kontakt")).scalar() == 4
        assert conn.execute(sa.text("SELECT count(*) FROM kontakt_telefon")).scalar() == 2
        assert conn.execute(sa.text("SELECT count(*) FROM objekt_kontakt_freigabe")).scalar() == 2

        Session = sessionmaker(bind=conn)
        db = Session()
        set_tenant_context(db, 1)
        try:
            kontakt_id = verknuepfungen[0][1]
            kontakt = kontakt_service.get_kontakt(db, kontakt_id)
            assert kontakt is not None
            assert kontakt.anzeigename == "Anna Beispiel"
            assert [telefon.nummer_normalisiert for telefon in kontakt.telefone] == ["+43664123456"]
        finally:
            db.close()


def test_backfill_leerer_tisch_ist_noop(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0240_empty.db'}")
    with engine.begin() as conn:
        _schema(conn)
        modul = _migration()
        assert modul.backfill_objekt_kontakte(conn)["migriert"] == 0
        _lauf(conn, modul.upgrade)
