"""Phase 2 Paket 1: persistentes Schema und org-bezogene Eskalationseinstellungen."""
import importlib.util
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.breathing import WARNUNG_KINDS, BreathingWarnung, BreathingZustellung
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import AuditLog, Role, User, UserRole
from app.services.breathing_service import get_eskalation_empfaenger_ids, parse_user_id_csv
from tests.test_breathing_routes import breathing_routes  # noqa: F401


def test_migration_consistency_and_downgrade(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0348_asuew_phase2.py"
    spec = importlib.util.spec_from_file_location("migration_0348", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0348", "0347")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'phase2.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for name in ("org_settings", "incident", "breathing_troop", "user"):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)')
            conn.exec_driver_sql(f'INSERT INTO "{name}" (id) VALUES (1)')
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            inspector = sa.inspect(conn)
            for model in (OrgSettings, BreathingWarnung, BreathingZustellung):
                table = model.__table__
                columns = inspector.get_columns(table.name)
                if model is not OrgSettings:
                    assert {c["name"] for c in columns} == set(table.c.keys())
                for actual in columns:
                    if actual["name"] == "id" and model is OrgSettings:
                        continue
                    expected = table.c[actual["name"]]
                    assert actual["nullable"] == expected.nullable
                    assert actual["type"]._type_affinity == expected.type._type_affinity
                    if isinstance(expected.type, sa.String):
                        assert actual["type"].length == expected.type.length
                    if expected.server_default is not None:
                        assert actual["default"].strip("'\"") == str(expected.server_default.arg)
                    if expected.name == "id" or expected.name.endswith("_id"):
                        assert isinstance(expected.type, sa.BigInteger)
                actual_fks = inspector.get_foreign_keys(table.name)
                expected_fks = list(table.foreign_keys) if model is not OrgSettings else []
                assert len(actual_fks) == len(expected_fks)
                for fk in actual_fks:
                    expected_fk = next(iter(table.c[fk["constrained_columns"][0]].foreign_keys))
                    assert fk["referred_table"] == expected_fk.column.table.name
                    assert fk["referred_columns"] == [expected_fk.column.name]
                    assert fk["options"].get("ondelete") == expected_fk.ondelete
            assert inspector.get_unique_constraints("breathing_warnung") == []
            assert inspector.get_unique_constraints("breathing_zustellung")[0]["column_names"] == [
                "warnung_id", "stufe", "kanal", "user_id",
            ]
            for name, columns in (
                ("breathing_warnung", ["troop_id", "kind", "erledigt_at"]),
                ("breathing_zustellung", ["status", "naechster_versuch_at"]),
            ):
                assert inspector.get_indexes(name)[0]["column_names"] == columns
            assert conn.exec_driver_sql(
                "SELECT asuew_eskalation_user_ids, asuew_eskalation_sms FROM org_settings"
            ).one() == (None, 1)
            conn.exec_driver_sql(
                "INSERT INTO breathing_warnung (id, incident_id, troop_id, kind, ausgeloest_at) "
                "VALUES (1, 1, 1, 'withdraw', CURRENT_TIMESTAMP)"
            )
            conn.exec_driver_sql(
                "INSERT INTO breathing_zustellung (id, warnung_id, stufe, kanal, created_at) "
                "VALUES (1, 1, 0, 'push', CURRENT_TIMESTAMP)"
            )
            conn.exec_driver_sql("DELETE FROM breathing_warnung WHERE id = 1")
            assert conn.exec_driver_sql("SELECT count(*) FROM breathing_zustellung").scalar() == 0
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert "breathing_warnung" not in sa.inspect(conn).get_table_names()
            assert "breathing_zustellung" not in sa.inspect(conn).get_table_names()
            assert [c["name"] for c in sa.inspect(conn).get_columns("org_settings")] == ["id"]
    engine.dispose()


@pytest.mark.parametrize(("raw", "expected"), [
    (None, []), ("", []), (" 2,1,2,+3,1.0,no,4e2,,--2,١ ", [2, 1, 3]),
])
def test_parse_user_id_csv(raw, expected):
    assert parse_user_id_csv(raw) == expected


@pytest.fixture
def escalation(breathing_routes):
    ctx = breathing_routes
    db = ctx.db
    user = db.query(User).filter(User.org_id == ctx.troop.incident.primary_org_id).one()
    role = db.query(Role).filter(Role.code == "admin").one()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    other = FireDept(slug=f"phase2-{uuid4().hex}", name="Andere Org")
    db.add(other)
    db.flush()
    users = []
    for org_id, active in ((user.org_id, True), (user.org_id, False), (other.id, True)):
        item = User(username=uuid4().hex, display_name=uuid4().hex, org_id=org_id, active=active)
        db.add(item)
        users.append(item)
    db.commit()
    return ctx, user, users


def test_recipients_defaults_and_org_scope(escalation):
    ctx, user, (active, inactive, foreign) = escalation
    assert get_eskalation_empfaenger_ids(ctx.db, user.org_id) == ([], True)
    row = OrgSettings(org_id=user.org_id, asuew_eskalation_user_ids=(
        f"{active.id}, {user.id},{foreign.id},{inactive.id},invalid,{active.id},999999999999999999999"
    ), asuew_eskalation_sms=False)
    ctx.db.add(row)
    ctx.db.commit()
    assert get_eskalation_empfaenger_ids(ctx.db, user.org_id) == ([active.id, user.id], False)


def test_warning_roundtrip_dedupe_and_cascade(escalation):
    ctx, user, _ = escalation
    now = datetime.now(UTC).replace(tzinfo=None)
    assert set(WARNUNG_KINDS) == {
        "withdraw", "max_exceeded", "one_third_due", "two_third_due", "funk_verloren", "notfall",
        "kein_sicherheitstrupp", "assp", "kein_heartbeat",
    }
    for kind in WARNUNG_KINDS:
        warning = BreathingWarnung(incident_id=ctx.incident_id, troop_id=ctx.troop.id,
                                   kind=kind, zyklus_start=now, ausgeloest_at=now)
        delivery = BreathingZustellung(stufe=0, kanal="push", user_id=user.id, empfaenger_label="Test")
        warning.zustellungen.append(delivery)
        ctx.db.add(warning)
        ctx.db.flush()
        ctx.db.expire_all()
        assert warning.zustellungen[0].warnung is warning
        assert warning.eskalation_stufe == 0
        assert delivery.status == "pending" and delivery.versuche == 0
        assert delivery.created_at and delivery.lease_bis is None
        assert warning.zyklus_start == now
        duplicate_warning = BreathingWarnung(incident_id=ctx.incident_id, troop_id=ctx.troop.id,
                                             kind=kind, zyklus_start=now, ausgeloest_at=now)
        ctx.db.add(duplicate_warning)
        ctx.db.flush()
        with pytest.raises(sa.exc.IntegrityError), ctx.db.begin_nested():
            ctx.db.add(BreathingZustellung(warnung_id=warning.id, stufe=0, kanal="push", user_id=user.id))
            ctx.db.flush()
        delivery_id = delivery.id
        ctx.db.delete(warning)
        ctx.db.flush()
        assert ctx.db.get(BreathingZustellung, delivery_id) is None
    ctx.db.rollback()


def test_settings_marker_validation_audit_and_html(escalation, client):
    ctx, user, (active, inactive, foreign) = escalation
    page = client.get("/admin/settings")
    assert page.status_code == 200
    assert {u.id for u in page.context["asuew_users"]} == {user.id, active.id}
    assert active.display_name in page.text
    assert foreign.display_name not in page.text and inactive.display_name not in page.text
    sms = re.search(r'<input[^>]*name="asuew_eskalation_sms_raw"[^>]*>', page.text)
    assert sms and "checked" in sms.group()
    assert 'name="asuew_eskalation_form" value="1"' in page.text
    data = {"asuew_eskalation_form": "1", "asuew_eskalation_user_ids": [
        active.id, user.id, active.id, inactive.id, foreign.id, 999999999999999999999,
    ]}
    assert ctx.post("/admin/settings/org", data).status_code == 303
    ctx.db.expire_all()
    row = ctx.db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).one()
    expected = ",".join(str(value) for value in sorted([active.id, user.id]))
    assert (row.asuew_eskalation_user_ids, row.asuew_eskalation_sms) == (expected, False)
    audit = ctx.db.query(AuditLog).filter_by(action="breathing.asuew_eskalation", org_id=user.org_id).one()
    payload = json.loads(audit.payload_json)
    assert payload["alt"] == {"asuew_eskalation_user_ids": None, "asuew_eskalation_sms": True}
    assert payload["neu"] == {"asuew_eskalation_user_ids": expected, "asuew_eskalation_sms": False}
    assert audit.user_id == user.id
    assert ctx.post("/admin/settings/org", {"asuew_eskalation_sms_raw": "1"}).status_code == 303
    ctx.db.refresh(row)
    assert (row.asuew_eskalation_user_ids, row.asuew_eskalation_sms) == (expected, False)
    flag = ctx.db.get(SystemSettings, "breathing_enabled")
    flag.value = "false"
    ctx.db.commit()
    assert ctx.post("/admin/settings/org", {"asuew_eskalation_form": "1"}).status_code == 303
    ctx.db.refresh(row)
    assert row.asuew_eskalation_user_ids == expected
    flag.value = "true"
    ctx.db.commit()
    assert ctx.post("/admin/settings/org", {"asuew_eskalation_form": "1", "asuew_eskalation_sms_raw": "1"}).status_code == 303
    ctx.db.refresh(row)
    assert (row.asuew_eskalation_user_ids, row.asuew_eskalation_sms) == (None, True)
