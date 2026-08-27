"""Portabilitaets- und Wiederaufsetzbarkeitstests fuer Migration 0226."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PFAD = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0226_verleih_autodruck_zu_druckregel.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0226", _PFAD)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _schema(conn):
    conn.exec_driver_sql(
        "CREATE TABLE org_settings (org_id INTEGER PRIMARY KEY, "
        "verleih_autodruck BOOLEAN NOT NULL DEFAULT 0)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE gateway (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, "
        "device_token_hash VARCHAR(255))"
    )
    conn.exec_driver_sql(
        "CREATE TABLE printer (id INTEGER PRIMARY KEY, gateway_id INTEGER NOT NULL, "
        "name VARCHAR(150), aktiv BOOLEAN NOT NULL, defaults TEXT)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE print_rule (id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER NOT NULL, "
        "name VARCHAR(150) NOT NULL, aktiv BOOLEAN NOT NULL, trigger VARCHAR(30) NOT NULL, "
        "filters TEXT, documents TEXT, objekt_elements TEXT, printer_ids TEXT, "
        "fallback_printer_id INTEGER, options TEXT, sort_order INTEGER NOT NULL, "
        "erstellt_am DATETIME, aktualisiert_am DATETIME, UNIQUE(org_id, name))"
    )


def _lauf(conn, funktion):
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        funktion()


def test_upgrade_migriert_alle_varianten_und_ist_wiederaufsetzbar(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm0226.db'}")
    modul = _migration()
    with engine.begin() as conn:
        _schema(conn)
        for org_id, aktiv in [(1, 1), (2, 1), (3, 0), (4, 1), (5, 1), (6, 1)]:
            conn.execute(sa.text(
                "INSERT INTO org_settings (org_id, verleih_autodruck) VALUES (:org, :aktiv)"
            ), {"org": org_id, "aktiv": aktiv})
        # Org 1: mehrere Gateways; nur der erste gekoppelte zaehlt. Standarddrucker gewinnt.
        conn.exec_driver_sql("INSERT INTO gateway VALUES (10, 1, 'token'), (11, 1, 'token')")
        conn.exec_driver_sql(
            "INSERT INTO printer VALUES "
            "(100, 10, 'A Backup', 1, '{\"role\":\"backup\"}'),"
            "(101, 10, 'B Standard', 1, '{\"role\":\"standard\",\"duplex\":\"long\"}'),"
            "(102, 11, 'A Anderes Gateway', 1, '{\"role\":\"standard\"}')"
        )
        # Org 4 hat bereits einen vollwertigen Ersatz.
        conn.execute(sa.text(
            "INSERT INTO print_rule (org_id,name,aktiv,trigger,documents,printer_ids,sort_order) "
            "VALUES (4,'Bestehend',1,'verleih_created',:docs,:drucker,1)"
        ), {"docs": json.dumps(["verleih_schein"]), "drucker": json.dumps([9])})
        # Org 5: unwirksam und Namenskollision, daher muss eine zweite Regel entstehen.
        conn.execute(sa.text(
            "INSERT INTO print_rule (org_id,name,aktiv,trigger,documents,printer_ids,sort_order) "
            "VALUES (5,:name,0,'verleih_created',:docs,:drucker,1)"
        ), {"name": "Verleihschein automatisch", "docs": json.dumps([]), "drucker": json.dumps([])})
        conn.exec_driver_sql("INSERT INTO gateway VALUES (50, 5, 'token')")
        conn.exec_driver_sql("INSERT INTO printer VALUES (500, 50, 'Drucker', 1, '{}')")
        # Org 6 hat nur ein ungekoppeltes Gateway und bleibt sichtbar, aber inaktiv.
        conn.exec_driver_sql("INSERT INTO gateway VALUES (60, 6, NULL)")

        _lauf(conn, modul.upgrade)
        _lauf(conn, modul.upgrade)

        spalten = {s["name"] for s in sa.inspect(conn).get_columns("org_settings")}
        assert "verleih_autodruck" not in spalten
        regeln = list(conn.execute(sa.text(
            "SELECT org_id,name,aktiv,documents,printer_ids,options FROM print_rule ORDER BY org_id,id"
        )).mappings())

        org1 = next(r for r in regeln if r["org_id"] == 1)
        assert json.loads(org1["documents"]) == ["verleih_schein"]
        assert json.loads(org1["printer_ids"]) == [101]
        assert json.loads(org1["options"])["duplex"] == "long"
        assert bool(org1["aktiv"]) is True
        assert not any(r["org_id"] == 3 for r in regeln)
        assert len([r for r in regeln if r["org_id"] == 4]) == 1
        org5 = [r for r in regeln if r["org_id"] == 5]
        assert len(org5) == 2 and org5[1]["name"] == "Verleihschein automatisch (2)"
        org2 = next(r for r in regeln if r["org_id"] == 2)
        org6 = next(r for r in regeln if r["org_id"] == 6)
        assert json.loads(org2["printer_ids"]) == [] and not bool(org2["aktiv"])
        assert json.loads(org6["printer_ids"]) == [] and not bool(org6["aktiv"])

        _lauf(conn, modul.downgrade)
        assert "verleih_autodruck" in {s["name"] for s in sa.inspect(conn).get_columns("org_settings")}
        assert conn.execute(sa.text(
            "SELECT verleih_autodruck FROM org_settings WHERE org_id=1"
        )).scalar_one() in (0, False)
        conn.execute(sa.text(
            "UPDATE org_settings SET verleih_autodruck=1 WHERE org_id IN (1,4,5)"
        ))
        _lauf(conn, modul.upgrade)
        assert conn.execute(sa.text(
            "SELECT COUNT(*) FROM print_rule WHERE org_id=1"
        )).scalar_one() == 1
