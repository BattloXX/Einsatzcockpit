"""Vertrag, Datenschutz und Grenzen des App-Geraeteberichts."""
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.security import hash_api_key, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.app_geraet import AppGeraet, AppGeraetFehler
from app.models.user import DeviceToken, User
from app.routers.device_api import router
from app.services.app_geraet_service import bereinige_alte_berichte, maskiere_text, validiere_bericht

URL = "/api/v1/device/diagnose-report"


@pytest.fixture
def konto(client):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = User(username=f"diagnose-{uuid4()}", password_hash="unused", display_name="Diagnose-Test", active=True)
        db.add(user)
        db.commit()
        uid = user.id
    client.cookies.set("session", sign_session(uid))
    return uid


@pytest.fixture
def bericht():
    return {"schema": 1, "install_id": str(uuid4()),
            "app": {"version_name": "1.2.3", "version_code": 123, "build_variant": "standard"},
            "android": {"version": "16", "sdk_int": 36, "sicherheitspatch": "2026-10-01"},
            "geraet": {"hersteller": "Test", "modell": "Testmodell", "klasse": "handy", "akku_prozent": 80},
            "display": {"breite_px": 1080, "hoehe_px": 2400, "dpi": 420},
            "einstellungen": {"auto_update": True, "el_alarm_volume_percent": 70}}


def test_sitzung_ohne_token_upsert(client, konto, bericht):
    response = client.post(URL, json=bericht)
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"ok", "geraet_id", "server_time"}
    assert result["ok"] and result["server_time"].endswith("Z")
    bericht["app"]["version_name"] = "1.2.4"
    assert client.post(URL, json=bericht).json()["geraet_id"] == result["geraet_id"]
    with SessionLocal() as db:
        row = db.query(AppGeraet).filter_by(user_id=konto).one()
        assert row.app_version == "1.2.4"
        assert row.org_id is None and row.device_token_id is None
        assert row.aufloesung == "1080x2400" and row.sdk_int == 36
        assert row.last_report_at >= row.first_seen_at
        assert row.geraet_details["geraet"]["akku_prozent"] == 80


@pytest.mark.parametrize("bearer", [True, False])
def test_tatsaechliches_token(client, konto, bericht, bearer):
    with SessionLocal() as db:
        for raw in ("diagnose-echt", "diagnose-anderes"):
            db.add(DeviceToken(user_id=konto, label=raw, token_hash=hash_api_key(f"{raw}-{konto}")))
            db.flush()
            if raw == "diagnose-echt":
                token_id = db.query(DeviceToken).filter_by(user_id=konto, label=raw).one().id
        db.commit()
    client.cookies.clear()
    if bearer:
        headers = {"Authorization": f"Bearer diagnose-echt-{konto}"}
    else:
        headers = {}
        client.cookies.set("session", sign_session(konto, device=True, device_token_id=token_id))
    response = client.post(URL, json=bericht, headers=headers)
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.get(AppGeraet, response.json()["geraet_id"]).device_token_id == token_id
        token = db.get(DeviceToken, token_id)
        assert (token.app_version, token.platform, token.os_version, token.device_model) == (
            "1.2.3", "android", "16", "Testmodell")
        assert db.query(DeviceToken).filter_by(user_id=konto, label="diagnose-anderes").one().app_version is None


def test_normale_sitzung_ordnet_kein_fremdes_geraet_zu(client, konto, bericht):
    with SessionLocal() as db:
        db.add(DeviceToken(user_id=konto, label="nicht verwendet", token_hash=hash_api_key(str(uuid4()))))
        db.commit()
    response = client.post(URL, json=bericht)
    with SessionLocal() as db:
        assert db.get(AppGeraet, response.json()["geraet_id"]).device_token_id is None


def test_whitelist_und_kaputte_felder(client, konto, bericht):
    bericht.update({"imei": "verboten", "tokens": ["geheim"]})
    bericht["geraet"].update({"seriennummer": "geheim", "akku_prozent": 101, "ram_mb_free": True})
    bericht["android"]["sdk_int"] = "kaputt"
    bericht["einstellungen"].update({"token": "geheim", "auto_update": "true", "base_url_host": "https://u:p@host/?x=1"})
    bericht["display"].update({"hz": [42], "dpi": 10**1000})
    assert client.post(URL, json=bericht).status_code == 200
    with SessionLocal() as db:
        row = db.query(AppGeraet).filter_by(user_id=konto).one()
        assert row.sdk_int is None
        assert row.einstellungen == {"el_alarm_volume_percent": 70}
        assert set(row.geraet_details["geraet"]) == {"hersteller", "modell", "klasse"}
        assert "imei" not in str(row.geraet_details)


@pytest.mark.parametrize("text,geheim", [
    ("Anruf +43 664 1234567 fehlgeschlagen", "+43 664 1234567"),
    ("Token " + "abcdef0123456789" * 3, "abcdef0123456789"),
    ("user@example.at", "user@example.at"),
    ("https://alice:pass@example.at/path?token=secret#fragment", "secret"),
    ("Bearer abc.def.ghi", "abc.def.ghi"),
    ("token=kurzesgeheimnis", "kurzesgeheimnis"),
])
def test_maskierung(text, geheim):
    masked = maskiere_text(text)
    assert geheim not in masked
    assert "alice:pass" not in masked


def test_fehler_aggregation_und_grenze(client, konto, bericht):
    jetzt = datetime.now(UTC) - timedelta(minutes=5)
    fehler = {"ts": jetzt.isoformat(), "komponente": "poller", "code": "timeout",
              "text": "+436641234567 user@example.at " + "a" * 40, "anzahl": 2}
    bericht["fehler"] = [fehler]
    for _ in range(2):
        assert client.post(URL, json=bericht).status_code == 200
    with SessionLocal() as db:
        gid = db.query(AppGeraet).filter_by(user_id=konto).one().id
        row = db.query(AppGeraetFehler).filter_by(geraet_id=gid).one()
        assert row.anzahl == 2
        assert "43664" not in row.text and "example.at" not in row.text and "a" * 40 not in row.text
    # Die App meldet kumulierte Anzahlen: neuer Zeitstempel uebernimmt den Wert, kein Addieren.
    fehler["ts"] = (jetzt + timedelta(seconds=1)).isoformat()
    fehler["anzahl"] = 5
    client.post(URL, json=bericht)
    with SessionLocal() as db:
        assert db.query(AppGeraetFehler).filter_by(geraet_id=gid).one().anzahl == 5
    fehler["ts"] = (jetzt - timedelta(seconds=1)).isoformat()
    fehler["anzahl"] = 9
    client.post(URL, json=bericht)
    with SessionLocal() as db:
        assert db.query(AppGeraetFehler).filter_by(geraet_id=gid).one().anzahl == 5
    fehler["anzahl"] = 2
    for batch in range(5):
        bericht["fehler"] = [{**fehler, "code": f"e-{batch}-{i}",
                               "ts": (jetzt + timedelta(seconds=2 + batch * 50 + i)).isoformat()}
                              for i in range(50)]
        assert client.post(URL, json=bericht).status_code == 200
    with SessionLocal() as db:
        rows = db.query(AppGeraetFehler).filter_by(geraet_id=gid).all()
        assert len(rows) == 200
        assert not any(row.code == "timeout" or row.code.startswith("e-0-") for row in rows)


def test_auth_und_body_limit(client, bericht):
    assert client.post(URL, json=bericht).status_code == 401


def test_groesser_64kb(client, konto, bericht):
    assert client.post(URL, content=b"x" * (64 * 1024 + 1)).status_code == 413
    assert client.post(URL, content=iter([b"x" * 40000, b"x" * 40000])).status_code == 413


@pytest.mark.parametrize("data", [{}, {"schema": True, "install_id": str(uuid4())},
                                  {"schema": 2, "install_id": str(uuid4())},
                                  {"schema": 1, "install_id": "kaputt"}, []])
def test_ungueltige_pflichtfelder(client, konto, data):
    assert client.post(URL, json=data).status_code == 400


def test_rate_limit_pro_installation(client, konto, bericht):
    for _ in range(30):
        assert client.post(URL, json=bericht).status_code == 200
    assert client.post(URL, json=bericht).status_code == 429
    with SessionLocal() as db:
        row = db.query(AppGeraet).filter_by(user_id=konto).one()
        row.report_zeiten = [ts - 3601 for ts in row.report_zeiten]
        db.commit()
    assert client.post(URL, json=bericht).status_code == 200
    bericht["install_id"] = str(uuid4())
    assert client.post(URL, json=bericht).status_code == 200


def test_retention(client, konto, bericht):
    response = client.post(URL, json=bericht)
    gid = response.json()["geraet_id"]
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        for alter in (89, 91):
            ts = jetzt - timedelta(days=alter)
            db.add(AppGeraetFehler(geraet_id=gid, komponente="test", code=str(alter), text="test",
                                  anzahl=1, first_at=ts, last_at=ts))
        db.commit()
        assert bereinige_alte_berichte(db, jetzt) == 1
        assert db.query(AppGeraetFehler).filter_by(geraet_id=gid).one().code == "89"
        db.get(AppGeraet, gid).last_report_at = jetzt - timedelta(days=91)
        assert bereinige_alte_berichte(db, jetzt) == 1
        db.commit()
        assert db.get(AppGeraet, gid) is None
        assert db.query(AppGeraetFehler).filter_by(geraet_id=gid).count() == 0


def test_keine_einstellungsroute():
    """Schreibschutz: keine Route zum Setzen/Ausliefern von App-Einstellungen."""
    for route in router.routes:
        assert not any(wort in route.path.lower() for wort in ("einstellung", "setting", "config"))
        if route.path.endswith("/diagnose-report"):
            assert route.methods == {"POST"}


def test_fehler_maximal_50(bericht):
    item = {"ts": datetime.now(UTC).isoformat(), "komponente": "test", "code": "a"}
    bericht["fehler"] = [item] * 51
    assert len(validiere_bericht(bericht)["fehler"]) == 50


def test_migration_roundtrip():
    spec = importlib.util.spec_from_file_location("migration0339", Path("alembic/versions/0339_app_geraet.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0339" and module.down_revision == "0338"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for name in ("user", "device_token", "fire_dept"):
            conn.exec_driver_sql(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        assert {"app_geraet", "app_geraet_fehler"} <= set(sa.inspect(conn).get_table_names())
        assert {index["name"] for index in sa.inspect(conn).get_indexes("app_geraet")} == {
            "ix_app_geraet_last_report_at", "ix_app_geraet_sdk_int", "ix_app_geraet_app_version"}
        module.downgrade()
        assert "app_geraet" not in sa.inspect(conn).get_table_names()
        module.upgrade()
    engine.dispose()


def test_fehler_wiederholung_bei_leicht_vorgehender_uhr(client, konto, bericht):
    bericht["fehler"] = [{"ts": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
                           "komponente": "test", "code": "uhr", "anzahl": 3}]
    for _ in range(2):
        assert client.post(URL, json=bericht).status_code == 200
    with SessionLocal() as db:
        gid = db.query(AppGeraet).filter_by(user_id=konto).one().id
        assert db.query(AppGeraetFehler).filter_by(geraet_id=gid).one().anzahl == 3


@pytest.mark.parametrize("typ", ["int", "bigint", "unsigned_int", "unsigned_bigint"])
def test_migration_uebernimmt_mariadb_fk_typ(monkeypatch, typ):
    from types import SimpleNamespace

    from sqlalchemy.dialects import mysql

    spec = importlib.util.spec_from_file_location("migration0339typen", Path("alembic/versions/0339_app_geraet.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    klasse = mysql.BIGINT if "bigint" in typ else mysql.INTEGER
    pk_typ = klasse(unsigned=typ.startswith("unsigned"))
    monkeypatch.setattr(module, "op", SimpleNamespace(
        get_context=lambda: SimpleNamespace(as_sql=False), get_bind=lambda: None))
    monkeypatch.setattr(module.sa, "inspect", lambda _: SimpleNamespace(
        get_columns=lambda _: [{"name": "id", "type": pk_typ}]))
    for tabelle in ("user", "device_token", "fire_dept"):
        assert module._fk_typ(tabelle).compile(dialect=mysql.dialect()) == pk_typ.compile(dialect=mysql.dialect())


LOC = "/api/v1/device/location"


@pytest.fixture
def geraet(client, konto):
    with SessionLocal() as db:
        db.add(DeviceToken(user_id=konto, label="standort", token_hash=hash_api_key(f"standort-{konto}"),
                           platform="android"))
        db.commit()
    client.cookies.clear()
    return {"Authorization": f"Bearer standort-{konto}"}, konto


def _zustand(konto):
    with SessionLocal() as db:
        t = db.query(DeviceToken).filter_by(user_id=konto, label="standort").one()
        return t.last_lat, t.last_location_at, t.last_location_status, t.last_location_error, t.last_location_source


def test_standort_ueber_bearer_ohne_einsatz_mit_messzeit(client, geraet):
    headers, konto = geraet
    gemessen = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    r = client.post(LOC, headers=headers, json={"lat": 47.5, "lng": 9.7, "accuracy": 20, "measured_at": gemessen,
                                                 "source": "periodic", "status": "ok"})
    assert r.status_code == 200
    lat, at, status, _, quelle = _zustand(konto)
    assert lat == 47.5 and status == "ok" and quelle == "periodic"
    assert abs((datetime.now(UTC).replace(tzinfo=None) - at).total_seconds() - 7200) < 120  # Messzeit, nicht Empfang


def test_standort_fehlstatus_und_ungueltige_eingabe(client, geraet):
    headers, konto = geraet
    assert client.post(LOC, headers=headers, json={"status": "permission_denied", "source": "periodic"}).status_code == 200
    assert _zustand(konto)[2:4] == ("permission_denied", None)
    assert client.post(LOC, headers=headers, json={"lat": 200, "lng": 0}).status_code == 400
    assert client.post(LOC, headers=headers, json={"lat": 1, "lng": 1, "measured_at": "kaputt"}).status_code == 400
    assert client.post(LOC, json={"lat": 1, "lng": 1}).status_code in (401, 403)


def test_standort_migration_spalten_existieren():
    cols = {c["name"] for c in sa.inspect(SessionLocal().get_bind()).get_columns("device_token")}
    assert {"last_location_received_at", "last_location_attempt_at", "last_location_status",
            "last_location_error", "last_location_source", "last_location_accuracy_m"} <= cols
