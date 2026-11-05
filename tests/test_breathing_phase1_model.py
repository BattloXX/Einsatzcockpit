"""ASUEW-Org-Einstellungen, Kontrollzyklen und reversibles Schema."""
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.tenant import set_tenant_context
from app.models.breathing import BreathingDeployment, BreathingKontrolle, BreathingTroop, TroopMember
from app.models.master import OrgSettings
from app.services.breathing_service import AsuewSettings, get_asuew_settings, redeploy_troop
from tests.conftest import TestingSession


@pytest.mark.parametrize(("row", "expected"), [
    (None, (30, 60, 75, 30)),
    (OrgSettings(), (30, 60, 75, 30)),
    (OrgSettings(asuew_erwartete_einsatzzeit_min=0, asuew_rueckzug_untergrenze_bar=0,
                 asuew_gelb_prozent=0, asuew_erholzeit_min=-1), (5, 30, 50, 0)),
    (OrgSettings(asuew_erwartete_einsatzzeit_min=999, asuew_rueckzug_untergrenze_bar=999,
                 asuew_gelb_prozent=999, asuew_erholzeit_min=999), (120, 100, 95, 120)),
    (OrgSettings(asuew_erwartete_einsatzzeit_min=25, asuew_rueckzug_untergrenze_bar=55,
                 asuew_gelb_prozent=80, asuew_erholzeit_min=45), (25, 55, 80, 45)),
    (OrgSettings(asuew_erwartete_einsatzzeit_min=None, asuew_rueckzug_untergrenze_bar=55,
                 asuew_gelb_prozent=None, asuew_erholzeit_min=0), (30, 55, 75, 0)),
])
def test_settings_defaults_and_clamping(row, expected):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = row
    result = get_asuew_settings(db, 42)
    assert isinstance(result, AsuewSettings)
    assert result == expected
    db.query.assert_called_once_with(OrgSettings)
    condition = db.query.return_value.filter.call_args.args[0]
    assert condition.left.name == "org_id" and condition.right.value == 42


def test_kontrollen_relationship_and_redeploy(monkeypatch):
    monkeypatch.setattr("app.services.breathing_service.write_incident_change", lambda *args, **kwargs: None)
    db = TestingSession()
    set_tenant_context(db, None)
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        troop = BreathingTroop(
            incident_id=1, name="Kontrolltrupp", status="erholt", ueberwacher_user_id=1,
            ueberwacher_name="Aufsicht", angriffsweg="Stiege", funkkanal="Kanal 1",
            funk_verloren_at=now, notfall_at=now, notfall_von_user_id=1,
        )
        member = TroopMember(troop=troop, free_text_name="Person A", start_press=300)
        db.add(troop)
        db.flush()
        later = BreathingKontrolle(
            troop=troop, stufe="two_third", erledigt_at=now, art="kein_kontakt", note="Kein Funk",
        )
        earlier = BreathingKontrolle(
            troop=troop, stufe="one_third", faellig_at=now - timedelta(minutes=10),
            erledigt_at=now - timedelta(minutes=9), niedrigster_druck=200,
            troop_member_id=member.id, user_id=1, art="funk",
        )
        db.add_all([later, earlier])
        db.flush()
        db.expire(troop, ["kontrollen"])
        assert troop.kontrollen == [earlier, later]
        assert earlier.troop is troop
        assert troop.active_kontrollen == [earlier, later]
        assert earlier.created_at is not None

        roster = [
            {"free_text_name": "Person B", "start_press": 300},
            {"free_text_name": "Person C", "start_press": 300},
        ]
        redeploy_troop(db, troop, roster)
        first = troop.deployments[0]
        assert earlier.deployment_id == later.deployment_id == first.id
        assert member.deployment_id == first.id
        assert troop.active_kontrollen == []
        for field, expected in (
            ("ueberwacher_name", "Aufsicht"), ("angriffsweg", "Stiege"), ("funkkanal", "Kanal 1"),
            ("funk_verloren_at", now), ("notfall_at", now),
        ):
            assert getattr(first, field) == expected
        assert (troop.ueberwacher_name, troop.angriffsweg, troop.funkkanal) == ("Aufsicht", "Stiege", "Kanal 1")
        assert troop.ueberwacher_user_id == 1
        assert troop.funk_verloren_at is troop.notfall_at is troop.notfall_von_user_id is None

        troop.status = "erholt"
        current = BreathingKontrolle(troop=troop, stufe="frei", erledigt_at=now, art="funk")
        db.add(current)
        db.flush()
        redeploy_troop(db, troop, roster)
        second = troop.deployments[1]
        assert current.deployment_id == second.id
        assert earlier.deployment_id == later.deployment_id == first.id
        assert second.notfall_at is second.funk_verloren_at is None
        db.expire(troop, ["kontrollen"])
        assert troop.active_kontrollen == []
        assert len(troop.kontrollen) == 3
        db.delete(troop)
        db.flush()
        assert db.get(BreathingKontrolle, earlier.id) is None
        assert db.get(BreathingKontrolle, current.id) is None
    finally:
        db.rollback()
        db.close()


def test_migration_model_consistency_and_downgrade(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0347_asuew_phase1.py"
    spec = importlib.util.spec_from_file_location("migration_0347", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0347", "0346")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'asuew.db'}")
    with engine.begin() as conn:
        for table in ("user", "org_settings", "breathing_troop", "breathing_deployment", "troop_member"):
            conn.exec_driver_sql(f'CREATE TABLE "{table}" (id BIGINT PRIMARY KEY)')
        conn.exec_driver_sql("INSERT INTO org_settings (id) VALUES (1)")
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            for model in (OrgSettings, BreathingTroop, BreathingDeployment, BreathingKontrolle):
                table = model.__table__
                columns = sa.inspect(conn).get_columns(table.name)
                for column in columns:
                    if column["name"] == "id":
                        continue
                    expected = table.c[column["name"]]
                    assert column["nullable"] == expected.nullable
                    assert column["type"]._type_affinity == expected.type._type_affinity
                    if isinstance(expected.type, sa.String):
                        assert column["type"].length == expected.type.length
                    if expected.server_default is not None:
                        assert column["default"].strip("'\"") == expected.server_default.arg
                if model is BreathingKontrolle:
                    assert {c["name"] for c in columns} == set(table.c.keys())
                actual_fks = sa.inspect(conn).get_foreign_keys(table.name)
                for fk in actual_fks:
                    expected_fk = next(iter(table.c[fk["constrained_columns"][0]].foreign_keys))
                    assert fk["referred_table"] == expected_fk.column.table.name
                    assert fk["referred_columns"] == [expected_fk.column.name]
                    assert fk["options"].get("ondelete") == expected_fk.ondelete
                names = {c["name"] for c in columns}
                for column in table.c:
                    if column.name.endswith("_id") and column.name in names:
                        assert isinstance(column.type, sa.BigInteger)
            indexes = sa.inspect(conn).get_indexes("breathing_kontrolle")
            assert indexes[0]["column_names"] == ["troop_id", "deployment_id"]
            assert conn.exec_driver_sql(
                "SELECT asuew_erwartete_einsatzzeit_min, asuew_rueckzug_untergrenze_bar, "
                "asuew_gelb_prozent, asuew_erholzeit_min FROM org_settings"
            ).one() == (30, 60, 75, 30)
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert "breathing_kontrolle" not in sa.inspect(conn).get_table_names()
            for table in ("org_settings", "breathing_troop", "breathing_deployment"):
                assert [c["name"] for c in sa.inspect(conn).get_columns(table)] == ["id"]
    engine.dispose()
