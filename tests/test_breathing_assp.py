"""ASSP: reine Logistik, Zugriff, Persistenz und Watchdog-Hinweise."""
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.breathing import BreathingAssp, BreathingDeployment, BreathingTroop, BreathingWarnung, TroopMember
from app.models.incident import Incident, IncidentChange
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import User, UserRole
from app.services.breathing_service import (
    AsuewSettings,
    _collect_new_breathing_warnings,
    assp_uebersicht,
    dashboard_summary,
    get_or_none_assp,
    incident_hinweise,
)
from tests.test_breathing_hinweise import add_troop, ctx, warnings  # noqa: F401
from tests.test_breathing_routes import breathing_routes  # noqa: F401

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def make_troop(tid, status, **kw):
    return BreathingTroop(id=tid, name=f"Trupp {tid}", status=status, members=[
        TroopMember(free_text_name=f"Person {tid}", start_press=300),
        TroopMember(free_text_name=f"Partner {tid}", start_press=290),
    ], **kw)


@pytest.mark.parametrize("rest", [0, 30, 60])
def test_overview_groups_counts_recovery_and_no_monitoring(rest):
    ready = make_troop(1, "bereit", is_sicherheitstrupp=True, unit_name="Feuerwehr A")
    deployed = make_troop(2, "im_einsatz", entry_at=NOW - timedelta(minutes=10), planned_duration_min=30)
    withdrawing = make_troop(3, "rueckzug", entry_at=NOW.replace(tzinfo=None) - timedelta(minutes=5))
    returned = make_troop(4, "zurueck", back_at=NOW.replace(tzinfo=None) - timedelta(minutes=20))
    recovered = make_troop(5, "erholt", deployments=[BreathingDeployment(
        lfd_nr=1, status="zurueck", back_at=NOW - timedelta(minutes=70),
    )])
    result = assp_uebersicht([ready, deployed, withdrawing, returned, recovered],
                            AsuewSettings(30, 60, 75, rest), BreathingAssp(flaschen_voll=12, flaschen_leer=4), NOW)
    assert result["zaehler"] == {"bereit": 1, "im_einsatz": 2, "erholung": 2,
                                 "agt_bereit": 2, "agt_im_einsatz": 4, "agt_erholung": 4,
                                 "sicherheitstrupp_bereit": 1}
    assert result["bereit"][0] == {"name": "Trupp 1", "unit_name": "Feuerwehr A", "is_sicherheitstrupp": True,
                                    "members": [{"name": "Person 1", "start_press": 300},
                                                {"name": "Partner 1", "start_press": 290}]}
    assert result["im_einsatz"][0]["laufzeit_s"] == 600
    assert result["im_einsatz"][0]["geplant_min"] == 30
    assert set(result["im_einsatz"][0]) == {
        "name", "is_sicherheitstrupp", "unit_name", "entry_at", "laufzeit_s", "geplant_min",
    }
    assert result["erholung"][0]["wieder_einsatzbereit_at"] == NOW + timedelta(minutes=rest - 20)
    assert result["erholung"][0]["erholt_fertig"] is (rest <= 20)
    assert result["erholung"][1]["erholt_fertig"] is True
    assert result["flaschen"] == {"voll": 12, "leer": 4}
    assert result["wieder_einsatzbereit_at"] == (NOW + timedelta(minutes=rest - 20) if rest > 20 else None)


def test_missing_return_time_and_empty_defaults():
    result = assp_uebersicht([make_troop(1, "erholt")], AsuewSettings(30, 60, 75, 30), None, NOW)
    assert result["erholung"][0]["wieder_einsatzbereit_at"] is None
    assert result["erholung"][0]["erholt_fertig"] is False
    assert result["flaschen"] == {"voll": 0, "leer": 0}


@pytest.mark.parametrize("active", [False, True])
def test_active_assp_suppresses_only_assp_hint_and_dashboard(active):
    troops = [make_troop(i, "im_einsatz") for i in range(1, 4)]
    assert ("assp" in incident_hinweise(troops, active)) is (not active)
    assert "kein_sicherheitstrupp" in incident_hinweise(troops, active)
    assert dashboard_summary(troops, AsuewSettings(30, 60, 75, 30), NOW, active)["hinweise"]["assp"] is (not active)


def test_assp_get_html_board_link_and_threshold(breathing_routes):  # noqa: F811
    c = breathing_routes
    base = f"/einsatz/{c.incident_id}/atemschutz"
    c.troop.status, c.troop.entry_at = "im_einsatz", NOW.replace(tzinfo=None)
    for index in (2, 3):
        c.db.add(BreathingTroop(incident_id=c.incident_id, name=f"Aktiv {index}", status="im_einsatz"))
    c.db.commit()
    page = c.client.get(base + "/assp")
    assert page.status_code == 200
    for text in ("Bereit am ASSP", "Im Einsatz", "Erholung", "Volle Flaschen", "Leere Flaschen",
                 "Info an die Einsatzleitung", "Ab 3 Trupps: ASSP alarmieren", "zuständigen Gruppenkommandanten"):
        assert text in page.text
    for text in ("Rückzugsdruck", "Kontrolle fällig", "Max-Zeit", "pressure_logged", "location.reload"):
        assert text not in page.text
    deployed = page.text.split('id="asspDeployed"')[1].split('</section>')[0]
    assert "bar" not in deployed and "Druck" not in deployed
    assert f'href="{base}/assp">ASSP</a>' in c.client.get(base).text
    fragment = c.client.get(base + "/assp", headers={"HX-Request": "true"})
    assert fragment.status_code == 200 and '<html' not in fragment.text
    assert fragment.text.count('id="asspContent"') == 1
    assert c.post(base + "/assp", {"aktiv": "true"}).status_code == 204
    assert "Ab 3 Trupps: ASSP alarmieren" not in c.client.get(base + "/assp").text
    assert "Ab 3 Trupps: ASSP alarmieren" not in c.client.get(base + "/kachel").text


def test_assp_partial_update_audit_broadcast_and_no_app_requirement(breathing_routes):  # noqa: F811
    c = breathing_routes
    c.db.add(OrgSettings(org_id=c.troop.incident.primary_org_id, asuew_app_pflicht=True))
    c.db.commit()
    url = f"/einsatz/{c.incident_id}/atemschutz/assp"
    assert get_or_none_assp(c.db, c.incident_id) is None
    response = c.post(url, {"aktiv": "true", "leiter_name": " Leiter ", "standort": "ALF", "notiz": "Pool",
                            "flaschen_voll": "8", "flaschen_leer": "2"})
    assert response.status_code == 204 and response.headers["HX-Trigger"] == "assp-refresh"
    row = get_or_none_assp(c.db, c.incident_id)
    assert row.aktiv and row.aktiviert_at and row.updated_by_user_id
    timestamp = row.aktiviert_at
    assert (row.leiter_name, row.standort, row.notiz, row.flaschen_voll, row.flaschen_leer) == (
        "Leiter", "ALF", "Pool", 8, 2,
    )
    audit = c.db.query(IncidentChange).filter_by(incident_id=c.incident_id, action="assp.updated").one()
    assert json.loads(audit.after_json)["flaschen_voll"] == 8
    assert audit.user_id == row.updated_by_user_id
    c.broadcast.assert_awaited_with(c.incident_id, {"type": "assp_updated"})
    assert c.post(url, {"notiz": ""}).status_code == 204
    assert c.post(url, {"aktiv": "false"}).status_code == 204
    assert c.post(url, {"aktiv": "true"}).status_code == 204
    c.db.refresh(row)
    assert row.leiter_name == "Leiter" and row.notiz is None
    assert row.aktiviert_at == timestamp
    assert row.flaschen_voll == 8
    page = c.client.get(url)
    assert "eingerichtet" in page.text and 'id="assp-voll" aria-live="polite">8' in page.text


@pytest.mark.parametrize("field,value", [
    ("flaschen_voll", "-1"), ("flaschen_leer", "1000"), ("flaschen_voll", "1.5"),
    ("flaschen_voll", "foo"), ("flaschen_voll", ""), ("leiter_name", "a" * 151),
    ("standort", "a" * 201), ("notiz", "a" * 301), ("aktiv", "vielleicht"),
])
def test_invalid_post_is_plain_400_without_mutation(breathing_routes, field, value):  # noqa: F811
    c = breathing_routes
    result = c.post(f"/einsatz/{c.incident_id}/atemschutz/assp", {field: value})
    assert result.status_code == 400 and result.headers['content-type'].startswith('text/plain')
    assert get_or_none_assp(c.db, c.incident_id) is None
    c.broadcast.assert_not_awaited()


def test_bottle_deltas_clamp_and_validate(breathing_routes):  # noqa: F811
    c = breathing_routes
    url = f"/einsatz/{c.incident_id}/atemschutz/assp/flaschen"
    for data, expected in [({"delta_voll": "-1"}, (0, 0)), ({"delta_voll": "1"}, (1, 0)),
                           ({"delta_leer": "3", "delta_voll": "-1"}, (0, 3)),
                           ({"delta_leer": "-999"}, (0, 0))]:
        assert c.post(url, data).status_code == 204
        row = get_or_none_assp(c.db, c.incident_id)
        c.db.refresh(row)
        assert (row.flaschen_voll, row.flaschen_leer) == expected
    for data in ({"delta_voll": "1.5"}, {"delta_leer": "1000"}):
        assert c.post(url, data).status_code == 400
    assert c.post(url, {"delta_voll": "999"}).status_code == 204
    assert c.post(url, {"delta_voll": "1"}).status_code == 400
    row = get_or_none_assp(c.db, c.incident_id)
    c.db.refresh(row)
    assert row.flaschen_voll == 999


def test_assp_module_and_tenant_scope(breathing_routes):  # noqa: F811
    c = breathing_routes
    foreign_org = FireDept(slug=uuid4().hex, name="Fremd")
    c.db.add(foreign_org)
    c.db.flush()
    foreign = Incident(primary_org_id=foreign_org.id)
    c.db.add(foreign)
    c.db.commit()
    url = f"/einsatz/{foreign.id}/atemschutz/assp"
    assert c.client.get(url).status_code == 404
    assert c.post(url, {"aktiv": "true"}).status_code == 404
    assert c.post(url + "/flaschen", {"delta_voll": "1"}).status_code == 404
    assert get_or_none_assp(c.db, foreign.id) is None
    flag = c.db.get(SystemSettings, "breathing_enabled")
    flag.value = "false"
    c.db.commit()
    url = f"/einsatz/{c.incident_id}/atemschutz/assp"
    assert c.client.get(url).status_code == 404
    assert c.post(url, {"aktiv": "true"}).status_code == 404
    assert c.post(url + "/flaschen", {"delta_voll": "1"}).status_code == 404


def test_read_without_write_role(breathing_routes):  # noqa: F811
    c = breathing_routes
    user = c.db.query(User).filter_by(org_id=c.troop.incident.primary_org_id).one()
    for role in c.db.query(UserRole).filter_by(user_id=user.id):
        c.db.delete(role)
    c.db.commit()
    url = f"/einsatz/{c.incident_id}/atemschutz/assp"
    result = c.client.get(url)
    assert result.status_code == 200
    assert "ASSP eingerichtet</button>" not in result.text
    assert c.post(url, {"aktiv": "true"}).status_code == 403
    assert c.post(url + "/flaschen", {"delta_voll": "1"}).status_code == 403


@pytest.mark.parametrize("initially_active", [False, True])
def test_watchdog_active_assp_prevents_and_closes_warning(ctx, initially_active):  # noqa: F811
    c = ctx
    for _ in range(3):
        add_troop(c)
    add_troop(c, "bereit", safety=True)
    if not initially_active:
        assert any(event[2] == 'assp' for event in _collect_new_breathing_warnings(c.db))
    c.db.add(BreathingAssp(incident_id=c.incident.id, aktiv=True))
    c.db.commit()
    assert _collect_new_breathing_warnings(c.db) == []
    c.db.expire_all()
    assert not c.db.query(BreathingWarnung).filter_by(incident_id=c.incident.id, kind='assp', erledigt_at=None).all()
    if not initially_active:
        assert warnings(c)[0].erledigt_at is not None
        assert all(row.status == 'suppressed' for row in warnings(c)[0].zustellungen)


def test_assp_migration_roundtrip(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/0354_asuew_phase7.py'
    spec = importlib.util.spec_from_file_location('migration_0354', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ('0354', '0353')
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'assp.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        for name in ('incident', 'user'):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)')
            conn.exec_driver_sql(f'INSERT INTO "{name}" (id) VALUES (1)')
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            inspector = sa.inspect(conn)
            table = BreathingAssp.__table__
            columns = inspector.get_columns('breathing_assp')
            assert {c['name'] for c in columns} == set(table.c.keys())
            for actual in columns:
                expected = table.c[actual['name']]
                assert actual['nullable'] == expected.nullable
                assert actual['type']._type_affinity == expected.type._type_affinity
                if isinstance(expected.type, sa.String):
                    assert actual['type'].length == expected.type.length
                if expected.server_default is not None:
                    assert actual['default'].strip("'\"") == str(expected.server_default.arg)
                if expected.name == 'id' or expected.name.endswith('_id'):
                    assert isinstance(expected.type, sa.BigInteger)
            assert inspector.get_unique_constraints('breathing_assp')[0]['column_names'] == ['incident_id']
            for fk in inspector.get_foreign_keys('breathing_assp'):
                expected_fk = next(iter(table.c[fk['constrained_columns'][0]].foreign_keys))
                assert fk['referred_table'] == expected_fk.column.table.name
                assert fk['options']['ondelete'] == expected_fk.ondelete
            conn.exec_driver_sql(
                "INSERT INTO breathing_assp (id,incident_id,updated_by_user_id,updated_at) "
                "VALUES (1,1,1,CURRENT_TIMESTAMP)"
            )
            assert conn.exec_driver_sql(
                'SELECT aktiv,flaschen_voll,flaschen_leer FROM breathing_assp'
            ).one() == (0, 0, 0)
            with pytest.raises(sa.exc.IntegrityError):
                conn.exec_driver_sql(
                    "INSERT INTO breathing_assp (id,incident_id,updated_at) VALUES (2,1,CURRENT_TIMESTAMP)"
                )
            conn.exec_driver_sql('DELETE FROM "user" WHERE id=1')
            assert conn.exec_driver_sql('SELECT updated_by_user_id FROM breathing_assp').scalar() is None
            conn.exec_driver_sql('DELETE FROM incident WHERE id=1')
            assert conn.exec_driver_sql('SELECT count(*) FROM breathing_assp').scalar() == 0
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert 'breathing_assp' not in sa.inspect(conn).get_table_names()
            for name in ('incident', 'user'):
                conn.exec_driver_sql(f'INSERT INTO "{name}" (id) VALUES (1)')
    engine.dispose()
