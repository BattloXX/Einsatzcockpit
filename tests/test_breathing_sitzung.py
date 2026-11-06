"""Phase 3 Paket 1: App-Sitzung, Tenant-Isolation, Selbsttest und JSON-API."""
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import settings
from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.models.breathing import BreathingGeraetSitzung, BreathingKontrolle
from app.models.incident import Incident
from app.models.master import FireDept, OrgSettings
from app.models.user import DeviceToken, FcmToken, User
from app.services import breathing_sitzung as svc
from tests.test_breathing_routes import breathing_routes  # noqa: F401


@pytest.fixture
def ctx(breathing_routes, client, monkeypatch):
    c = breathing_routes
    user = c.db.query(User).filter(User.org_id == c.troop.incident.primary_org_id).one()
    token = FcmToken(user_id=user.id, token=uuid4().hex, capabilities="native_alarm,asuew", platform="android")
    org_settings = OrgSettings(org_id=user.org_id)
    c.db.add_all([token, org_settings])
    c.db.commit()
    monkeypatch.setattr(settings, "ASUEW_MIN_APP_VERSION", "")
    data = dict(incident_id=c.incident_id, fcm_token=token.token, app_version="2026.11.05",
                selbsttest={"online": True}, akku_prozent=80, laedt=False, geraet_label="Überwachung 1")
    return SimpleNamespace(**{**vars(c), "client": client}, user=user, token=token, settings=org_settings,
                           data=data, now=datetime.now(UTC).replace(tzinfo=None))


def start(c, **changes):
    return c.client.post("/api/asuew/sitzung", json={**c.data, **changes})


def service_start(c, **changes):
    return svc.start_sitzung(c.db, c.user, **{**c.data, **changes}, now=c.now)


def test_start_cookie_and_row(ctx):
    c = ctx
    response = start(c)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["incident_id"] == c.incident_id
    assert payload["server_time"].endswith("Z")
    assert (payload["heartbeat_interval_s"], payload["heartbeat_max_age_s"]) == (15, 60)
    assert payload["ampel"] == "gruen" and payload["probleme"] == []
    cookie = next(v for v in response.headers.get_list("set-cookie") if v.startswith("ec_asuew="))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age=86400" in cookie and "Secure" not in cookie
    row = c.db.query(BreathingGeraetSitzung).filter_by(session_key=payload["session_key"]).one()
    assert row.user_id == c.user.id and row.fcm_token_id == c.token.id
    assert row.platform == "android" and row.geraet_label == "Überwachung 1"
    assert row.gestartet_at.tzinfo is None and row.letzter_heartbeat_at == row.gestartet_at
    assert json.loads(row.selbsttest) == {"online": True}
    assert c.settings.asuew_app_pflicht is False


def test_secure_cookie(ctx):
    c = ctx
    response = c.client.post("https://testserver/api/asuew/sitzung", json=c.data)
    assert response.status_code == 200, response.text
    cookie = next(v for v in response.headers.get_list("set-cookie") if v.startswith("ec_asuew="))
    assert "Secure" in cookie and "HttpOnly" in cookie


@pytest.mark.parametrize("capabilities", [None, "native_alarm", "asuew_extra"])
def test_capability_required(ctx, capabilities):
    c = ctx
    c.token.capabilities = capabilities
    c.db.commit()
    response = start(c)
    assert response.status_code == 403
    assert response.json() == {"error": "app_erforderlich",
                               "message": "Die Atemschutzüberwachung läuft nur in der Einsatzcockpit-App."}
    assert c.db.query(BreathingGeraetSitzung).filter_by(incident_id=c.incident_id).count() == 0


def test_foreign_token_and_unknown_token(ctx):
    c = ctx
    other = User(username=uuid4().hex, display_name="Andere Person", org_id=c.user.org_id)
    c.db.add(other)
    c.db.flush()
    c.token.user_id = other.id
    c.db.commit()
    for token in (c.token.token, "nicht-vorhanden"):
        response = start(c, fcm_token=token)
        assert response.status_code == 403 and response.json()["error"] == "app_erforderlich"


def test_cross_org_and_missing_incident(ctx):
    c = ctx
    foreign = FireDept(slug=uuid4().hex, name="Fremde Organisation")
    c.db.add(foreign)
    c.db.flush()
    incident = Incident(primary_org_id=foreign.id)
    c.db.add(incident)
    c.db.commit()
    # Auch ohne Tenant-Listener-Kontext muss der Service selbst die Org prüfen.
    set_tenant_context(c.db, None)
    with pytest.raises(svc.SitzungFehler) as exc:
        service_start(c, incident_id=incident.id)
    assert exc.value.http_status == 404
    for incident_id in (incident.id, 999999999):
        response = start(c, incident_id=incident_id)
        assert response.status_code == 404
        assert response.json()["message"] == "Einsatz nicht gefunden."
    assert c.db.query(BreathingGeraetSitzung).filter_by(incident_id=incident.id).count() == 0


@pytest.mark.parametrize("cause,status,code", [("inactive", 409, "einsatz_inaktiv"),
                                                ("module", 403, "modul_inaktiv")])
def test_inactive_incident_or_module(ctx, cause, status, code):
    c = ctx
    if cause == "inactive":
        c.troop.incident.status = "closed"
    else:
        c.settings.atemschutz_ueberwachung_modul_aktiv = False
    c.db.commit()
    response = start(c)
    assert response.status_code == status and response.json()["error"] == code


@pytest.mark.parametrize("minimum,version,expected", [
    ("2026.11.05", "2026.11.04", 426), ("2026.11.05", "unbekannt", 426),
    ("2026.11.05", None, 426), ("2026.11.05.1", "2026.11.05", 426),
    ("2026.11.05", "v2026.11.05.0", 200), ("2026.11.05.2", "2026.11.05.10", 200),
    ("", "unbekannt", 200), ("", None, 200),
])
def test_minimum_version(ctx, monkeypatch, minimum, version, expected):
    monkeypatch.setattr(settings, "ASUEW_MIN_APP_VERSION", minimum)
    response = start(ctx, app_version=version)
    assert response.status_code == expected
    if expected == 426:
        assert response.json() == {"error": "app_veraltet", "message": "Bitte die App aktualisieren."}


def test_second_session_replaces_only_same_user_and_incident(ctx):
    c = ctx
    first = service_start(c)
    other_incident = service_start(c, incident_id=c.other_incident_id)
    second = service_start(c)
    assert first.beendet_at == c.now and first.ende_grund == "ersetzt"
    assert second.beendet_at is None and second.session_key != first.session_key
    assert other_incident.beendet_at is None
    assert svc.validiere_sitzung(c.db, first.session_key, c.incident_id, c.user.id, c.now) is None
    c.db.commit()
    response = c.client.post("/api/asuew/heartbeat", json={}, headers={"X-ECP-ASUEW": first.session_key})
    assert response.status_code == 409 and response.json()["error"] == "sitzung_beendet"


def test_heartbeat_update_and_end(ctx):
    c = ctx
    response = start(c)
    key = response.json()["session_key"]
    row = c.db.query(BreathingGeraetSitzung).filter_by(session_key=key).one()
    before = row.letzter_heartbeat_at
    response = c.client.post("/api/asuew/heartbeat", json={"akku_prozent": 9, "laedt": False,
                                                           "selbsttest": {"alarmton_getestet": False}})
    assert response.status_code == 200 and response.json()["ok"] is True
    assert response.json()["ampel"] == "rot"
    assert response.json()["server_time"].endswith("Z")
    c.db.refresh(row)
    assert row.letzter_heartbeat_at >= before and row.akku_prozent == 9
    assert json.loads(row.selbsttest) == {"alarmton_getestet": False}
    assert c.client.post("/api/asuew/heartbeat", json={}).json()["ampel"] == "rot"
    response = c.client.post("/api/asuew/sitzung/beenden")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert "Max-Age=0" in response.headers["set-cookie"]
    c.db.refresh(row)
    assert row.beendet_at is not None and row.ende_grund == "beendet"
    response = c.client.post("/api/asuew/heartbeat", json={}, headers={"X-ECP-ASUEW": key})
    assert response.status_code == 409 and response.json()["error"] == "sitzung_beendet"


@pytest.mark.parametrize("age,start_age,valid", [(60, 0, True), (61, 0, False),
                                                (0, 24 * 3600, True), (0, 24 * 3600 + 1, False)])
def test_expiry_boundaries(ctx, age, start_age, valid):
    c = ctx
    row = service_start(c)
    row.letzter_heartbeat_at = c.now - timedelta(seconds=age)
    row.gestartet_at = c.now - timedelta(seconds=max(age, start_age))
    c.db.flush()
    assert (svc.validiere_sitzung(c.db, row.session_key, c.incident_id, c.user.id, c.now) is not None) is valid
    assert svc.hat_frischen_heartbeat(c.db, c.incident_id, c.now) is valid
    if not valid:
        with pytest.raises(svc.SitzungFehler) as exc:
            svc.heartbeat(c.db, row.session_key, c.user, None, None, None, c.now)
        assert exc.value.http_status == 409 and exc.value.code == "sitzung_beendet"
    else:
        svc.heartbeat(c.db, row.session_key, c.user, 70, True, None, c.now)
        assert row.letzter_heartbeat_at == c.now and row.laedt is True


def test_session_user_binding_and_end_idempotent(ctx):
    c = ctx
    row = service_start(c)
    other = User(username=uuid4().hex, display_name="Andere", org_id=c.user.org_id)
    c.db.add(other)
    c.db.flush()
    assert svc.validiere_sitzung(c.db, row.session_key, c.incident_id, other.id, c.now) is None
    with pytest.raises(svc.SitzungFehler):
        svc.beende_sitzung(c.db, row.session_key, other, c.now)
    svc.beende_sitzung(c.db, row.session_key, c.user, c.now)
    svc.beende_sitzung(c.db, row.session_key, c.user, c.now + timedelta(seconds=1))
    assert row.beendet_at == c.now and row.ende_grund == "beendet"
    assert not svc.hat_frischen_heartbeat(c.db, c.incident_id, c.now)


@pytest.mark.parametrize("test,battery,charging,ampel", [
    (None, None, None, "gruen"), ({"unbekannt": False}, 20, False, "gruen"),
    ({"benachrichtigung": "granted"}, 100, False, "gruen"),
    ({"benachrichtigung": "denied"}, 100, True, "rot"),
    ({"benachrichtigung": "default"}, None, None, "gelb"),
    ({"online": False}, None, None, "rot"), ({"uhr_abweichung_s": 5}, None, None, "gruen"),
    ({"uhr_abweichung_s": 5.1}, None, None, "gelb"), ({"uhr_abweichung_s": 30}, None, None, "gelb"),
    ({"uhr_abweichung_s": 30.1}, None, None, "rot"), ({"uhr_abweichung_s": -31}, None, None, "rot"),
    ({"uhr_abweichung_s": "kaputt"}, None, None, "gruen"),
    ({"alarmton_getestet": False}, None, None, "gelb"), ({"exakte_wecker": False}, None, None, "gelb"),
    ({"vollbild": False}, None, None, "gelb"), ({"akku_optimierung_ausgenommen": False}, None, None, "gelb"),
    ({}, 9, False, "rot"), ({}, 10, False, "gelb"), ({}, 19, False, "gelb"), ({}, 9, True, "gruen"),
])
def test_selftest(test, battery, charging, ampel):
    result = svc.bewerte_selbsttest(test, battery, charging)
    assert result["ampel"] == ampel
    assert bool(result["probleme"]) is (ampel != "gruen")
    assert all(isinstance(text, str) and text for text in result["probleme"])


def test_selftest_severity_and_all_problems():
    result = svc.bewerte_selbsttest({"benachrichtigung": "denied", "online": False,
                                    "alarmton_getestet": False}, 9, False)
    assert result["ampel"] == "rot" and len(result["probleme"]) == 4


def test_state_requires_correct_session_and_serializes(ctx):
    c = ctx
    url = f"/api/asuew/{c.incident_id}/zustand"
    response = c.client.get(url)
    assert response.status_code == 403
    assert response.json()["detail"] == "Atemschutzüberwachung nur in der Einsatzcockpit-App."
    start(c, incident_id=c.other_incident_id)
    assert c.client.get(url).status_code == 403
    start(c)
    c.settings.asuew_app_pflicht = True
    c.troop.entry_at = c.now
    c.troop.notfall_at = c.now
    c.troop.planned_duration_min = 30
    c.db.add(BreathingKontrolle(troop_id=c.troop.id, stufe="one_third", art="funk",
                              erledigt_at=c.now, niedrigster_druck=230))
    c.db.commit()
    response = c.client.get(url)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["incident_id"] == c.incident_id and payload["asuew_app_pflicht"] is True
    assert set(payload["settings"]) == {"erwartete_einsatzzeit_min", "rueckzug_untergrenze_bar",
                                        "gelb_prozent", "erholzeit_min"}
    troop = payload["troops"][0]
    assert set(troop) == {"id", "name", "status", "is_sicherheitstrupp", "entry_at", "planned_duration_min",
                          "one_third_seconds", "two_third_seconds", "max_seconds", "withdraw_press_calc",
                          "notfall_at", "funk_verloren_at", "last_meldung_at", "warnings", "kontrollen", "members",
                          "last_pressure_at", "max_time_acked"}
    assert troop["id"] == c.troop.id and troop["entry_at"].endswith("Z")
    assert troop["funk_verloren_at"] is None and "notfall" in troop["warnings"]
    assert troop["one_third_seconds"] == 600 and troop["two_third_seconds"] == 1200
    assert troop["kontrollen"] == [{"stufe": "one_third", "art": "funk",
                                     "erledigt_at": c.now.isoformat() + "Z", "niedrigster_druck": 230.0}]
    assert troop["last_pressure_at"] is None and troop["max_time_acked"] is False
    assert len(troop["members"]) == 2
    assert set(troop["members"][0]) == {"id", "name", "role", "start_press", "current_press", "withdraw_press"}


@pytest.mark.parametrize("path", ["/sitzung", "/heartbeat", "/sitzung/beenden", "/1/zustand"])
def test_unauthenticated(ctx, path):
    c = ctx
    c.client.cookies.clear()
    response = c.client.get("/api/asuew" + path) if path.endswith("zustand") else c.client.post(
        "/api/asuew" + path, json=c.data if path == "/sitzung" else {})
    assert response.status_code == 401


def test_bearer_auth_and_header_session(ctx):
    c = ctx
    raw = uuid4().hex
    c.db.add(DeviceToken(user_id=c.user.id, token_hash=hash_api_key(raw), label="Testgerät"))
    c.db.commit()
    c.client.cookies.clear()
    headers = {"Authorization": "Bearer " + raw}
    response = c.client.post("/api/asuew/sitzung", json=c.data, headers=headers)
    assert response.status_code == 200, response.text
    key = response.json()["session_key"]
    c.client.cookies.clear()
    headers["X-ECP-ASUEW"] = key
    assert c.client.post("/api/asuew/heartbeat", json={}, headers=headers).status_code == 200
    assert c.client.get(f"/api/asuew/{c.incident_id}/zustand", headers=headers).status_code == 200
    assert c.client.post("/api/asuew/sitzung/beenden", headers=headers).status_code == 200


def test_csrf_origin_and_other_routes_still_protected(ctx, monkeypatch):
    c = ctx
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://einsatz.example")
    assert c.client.post("/api/asuew/sitzung", json=c.data,
                         headers={"Origin": "https://fremd.example"}).status_code == 403
    assert c.client.post("/api/asuew/sitzung", json=c.data,
                         headers={"Origin": "https://einsatz.example"}).status_code == 200
    response = c.client.post(f"/einsatz/{c.incident_id}/atemschutz/{c.troop.id}/meldung", data={"text": "Test"})
    assert response.status_code == 403 and "CSRF" in response.text


@pytest.mark.parametrize("data", [{"akku_prozent": 101}, {"selbsttest": []}, {"incident_id": "kaputt"}])
def test_invalid_json_data(ctx, data):
    assert start(ctx, **data).status_code == 422


def test_migration_upgrade_downgrade(tmp_path):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0349_asuew_phase3.py"
    spec = importlib.util.spec_from_file_location("migration_0349", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0349", "0348")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'phase3.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for name in ("org_settings", "incident", "user", "fcm_token"):
            conn.exec_driver_sql(f'CREATE TABLE "{name}" (id BIGINT PRIMARY KEY)')
            conn.exec_driver_sql(f'INSERT INTO "{name}" (id) VALUES (1)')
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            inspector = sa.inspect(conn)
            table = BreathingGeraetSitzung.__table__
            columns = inspector.get_columns(table.name)
            assert {column["name"] for column in columns} == set(table.c.keys())
            for actual in columns:
                expected = table.c[actual["name"]]
                assert actual["nullable"] == expected.nullable
                assert actual["type"]._type_affinity == expected.type._type_affinity
                if isinstance(expected.type, sa.String):
                    assert actual["type"].length == expected.type.length
                if expected.name == "id" or expected.name.endswith("_id"):
                    assert isinstance(expected.type, sa.BigInteger)
            fks = inspector.get_foreign_keys(table.name)
            assert len(fks) == 3
            for fk in fks:
                expected = next(iter(table.c[fk["constrained_columns"][0]].foreign_keys))
                assert fk["referred_table"] == expected.column.table.name
                assert fk["options"]["ondelete"] == expected.ondelete
            assert inspector.get_unique_constraints(table.name)[0]["column_names"] == ["session_key"]
            assert inspector.get_indexes(table.name)[0]["column_names"] == [
                "incident_id", "beendet_at", "letzter_heartbeat_at",
            ]
            flag = next(col for col in inspector.get_columns("org_settings") if col["name"] == "asuew_app_pflicht")
            assert flag["nullable"] is False and flag["default"] == "0"
            assert conn.exec_driver_sql("SELECT asuew_app_pflicht FROM org_settings").scalar() == 0
            insert = ("INSERT INTO breathing_geraet_sitzung "
                      "(id, incident_id, user_id, fcm_token_id, session_key, platform, gestartet_at, letzter_heartbeat_at) "
                      "VALUES (:id, 1, 1, 1, 'key', 'android', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            conn.execute(sa.text(insert), {"id": 1})
            with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
                conn.execute(sa.text(insert), {"id": 2})
            conn.exec_driver_sql("DELETE FROM fcm_token WHERE id = 1")
            assert conn.exec_driver_sql("SELECT fcm_token_id FROM breathing_geraet_sitzung").scalar() is None
            conn.exec_driver_sql("DELETE FROM incident WHERE id = 1")
            assert conn.exec_driver_sql("SELECT count(*) FROM breathing_geraet_sitzung").scalar() == 0
            conn.exec_driver_sql("INSERT INTO incident (id) VALUES (1)")
            conn.exec_driver_sql("INSERT INTO fcm_token (id) VALUES (1)")
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert table.name not in sa.inspect(conn).get_table_names()
            assert [col["name"] for col in sa.inspect(conn).get_columns("org_settings")] == ["id"]
    engine.dispose()


@pytest.mark.parametrize("foreign_org", [False, True])
def test_session_cannot_be_used_by_other_user(ctx, foreign_org):
    c = ctx
    key = start(c).json()["session_key"]
    org_id = c.user.org_id
    if foreign_org:
        org = FireDept(slug=uuid4().hex, name="Andere Org")
        c.db.add(org)
        c.db.flush()
        org_id = org.id
    user = User(username=uuid4().hex, display_name="Andere Aufsicht", org_id=org_id, active=True)
    c.db.add(user)
    c.db.flush()
    raw = uuid4().hex
    c.db.add(DeviceToken(user_id=user.id, token_hash=hash_api_key(raw), label="Fremdes Gerät"))
    c.db.commit()
    c.client.cookies.clear()
    headers = {"Authorization": "Bearer " + raw, "X-ECP-ASUEW": key}
    response = c.client.get(f"/api/asuew/{c.incident_id}/zustand", headers=headers)
    assert response.status_code == 403
    assert "troops" not in response.json()
    for path in ("/heartbeat", "/sitzung/beenden"):
        response = c.client.post("/api/asuew" + path, json={}, headers=headers)
        assert response.status_code == 409 and response.json()["error"] == "sitzung_beendet"
    row = c.db.query(BreathingGeraetSitzung).filter_by(session_key=key).one()
    assert row.beendet_at is None


def test_selbsttest_json_ist_begrenzt():
    from app.services.breathing_sitzung import SitzungFehler, _selbsttest_json

    assert _selbsttest_json(None) is None
    assert _selbsttest_json({"online": True}) == '{"online": true}'
    with pytest.raises(SitzungFehler) as excinfo:
        _selbsttest_json({"x": "a" * 5000})
    assert excinfo.value.code == "selbsttest_zu_gross" and excinfo.value.http_status == 422


@pytest.mark.parametrize("value,ampel,message", [
    (0, "rot", "Die Alarmlautstärke ist auf 0 gestellt."),
    (1, "gelb", "Die Alarmlautstärke ist sehr niedrig."),
    (29, "gelb", "Die Alarmlautstärke ist sehr niedrig."),
    (30, "gruen", None),
    (False, "gruen", None),
    (True, "gruen", None),
    ("0", "gruen", None),
])
def test_selbsttest_alarm_lautstaerke(value, ampel, message):
    result = svc.bewerte_selbsttest({"alarm_lautstaerke_prozent": value}, None, None)
    assert result == {"ampel": ampel, "probleme": [message] if message else []}
