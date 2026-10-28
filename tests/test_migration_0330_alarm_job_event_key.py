"""Backfill, Duplikate und reversible Schemaerweiterung der Alarmauftraege."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.incident import IncidentAlarmJob
from app.services.alarm_outbox import _event_key


def test_upgrade_downgrade_upgrade_backfill_und_duplikate(tmp_path, caplog):
    pfad = Path(__file__).resolve().parents[1] / "alembic/versions/0330_alarm_job_event_key.py"
    spezifikation = importlib.util.spec_from_file_location("migration_0330", pfad)
    migration = importlib.util.module_from_spec(spezifikation)
    spezifikation.loader.exec_module(migration)
    assert migration.revision == "0330" and migration.down_revision == "0329"
    migration._BATCH_GROESSE = 2
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'alarm.db'}")
    with engine.begin() as verbindung:
        verbindung.exec_driver_sql("CREATE TABLE incident (id INTEGER PRIMARY KEY, lis_operation_number TEXT)")
        verbindung.exec_driver_sql(
            "CREATE TABLE incident_alarm_job (id INTEGER PRIMARY KEY, org_id INTEGER, "
            "incident_id INTEGER, channel VARCHAR(16), variant VARCHAR(40))"
        )
        nummern = [(1, "  F-123  "), (2, None), (3, "f-123"), (4, "L" * 200), (5, "   ")]
        verbindung.execute(sa.text("INSERT INTO incident VALUES (:id, :nummer)"), [
            {"id": kennung, "nummer": nummer} for kennung, nummer in nummern
        ])
        verbindung.exec_driver_sql(
            "INSERT INTO incident_alarm_job VALUES "
            "(1, 7, 1, 'teams', ''), (2, NULL, 2, 'sms', ''), (3, 7, 3, 'teams', ''), "
            "(4, 8, 1, 'teams', 'nach:code:r2'), (5, 7, 4, 'mail', ''), "
            "(6, 7, 5, 'push', ''), (7, NULL, 2, 'sms', '')"
        )
        for durchlauf in range(2):
            with Operations.context(MigrationContext.configure(verbindung)):
                migration.upgrade()
            erwartet = [
                "7:f-123:teams:", "0:inc2:sms:", None, "8:f-123:teams:nach:code:r2",
                f"7:{hashlib.sha1(('l' * 200).encode()).hexdigest()}:mail:", "7:inc5:push:", None,
            ]
            zeilen = verbindung.exec_driver_sql("SELECT * FROM incident_alarm_job ORDER BY id").mappings().all()
            assert [zeile["event_key"] for zeile in zeilen] == erwartet
            assert all(zeile["may_have_sent"] == 0 and zeile["send_started_at"] is None for zeile in zeilen)
            for zeile in zeilen:
                if zeile["event_key"] is not None:
                    einsatz = SimpleNamespace(
                        id=zeile["incident_id"], lis_operation_number=dict(nummern)[zeile["incident_id"]],
                    )
                    assert zeile["event_key"] == _event_key(
                        einsatz, zeile["org_id"], zeile["channel"], zeile["variant"],
                    )
            spalten = {spalte["name"]: spalte for spalte in sa.inspect(verbindung).get_columns("incident_alarm_job")}
            for name in ("event_key", "send_started_at", "may_have_sent"):
                modell = IncidentAlarmJob.__table__.c[name]
                assert spalten[name]["nullable"] == modell.nullable
                assert spalten[name]["type"]._type_affinity == modell.type._type_affinity
            assert spalten["event_key"]["type"].length == 160
            index = next(index for index in sa.inspect(verbindung).get_indexes("incident_alarm_job")
                         if index["name"] == "uq_incident_alarm_job_org_event_key")
            assert index["unique"] and index["column_names"] == ["org_id", "event_key"]
            with pytest.raises(sa.exc.IntegrityError):
                verbindung.exec_driver_sql(
                    "INSERT INTO incident_alarm_job (id, org_id, event_key) VALUES (8, 7, '7:f-123:teams:')"
                )
            modell_constraint = next(
                constraint for constraint in IncidentAlarmJob.__table__.constraints
                if constraint.name == index["name"]
            )
            assert list(modell_constraint.columns.keys()) == index["column_names"]
            if durchlauf == 1:
                break
            with Operations.context(MigrationContext.configure(verbindung)):
                migration.downgrade()
            assert set(spalte["name"] for spalte in sa.inspect(verbindung).get_columns("incident_alarm_job")) == {
                "id", "org_id", "incident_id", "channel", "variant",
            }
            assert verbindung.exec_driver_sql("SELECT count(*) FROM incident_alarm_job").scalar() == 7
    assert "Doppelter Alarm-Ereignisschluessel" in caplog.text
    engine.dispose()
