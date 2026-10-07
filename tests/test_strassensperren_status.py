"""Statusansicht und oeffentlicher Infoscreen fuer Straßensperren."""

from datetime import UTC, datetime, timedelta

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import AlarmInfoscreenToken
from tests.test_strassensperren_ui import _closure, _login, _setup_user


def _token(raw: str, *, active: bool = True) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(AlarmInfoscreenToken(org_id=1, token_hash=hash_api_key(raw), name=raw, aktiv=active))
        db.commit()
    finally:
        db.close()


def test_statusansicht_zeigt_nur_aktuelle_sperren(client):
    user = _setup_user("readonly")
    _closure(title="Aktuelle Status-Sperre")
    _closure(title="Abgelaufene Status-Sperre", valid_from=datetime(2020, 1, 1), valid_until=datetime(2020, 1, 2))
    _login(client, user)
    response = client.get("/strassensperren/status")
    assert response.status_code == 200
    assert "1 aktiv" in response.text
    assert "Aktuelle Status-Sperre" in response.text
    assert "Abgelaufene Status-Sperre" not in response.text


def test_statusansicht_ist_bei_deaktiviertem_modul_nicht_verfuegbar(client):
    user = _setup_user("readonly", enabled=False)
    _login(client, user)
    assert client.get("/strassensperren/status").status_code == 404


def test_oeffentlicher_infoscreen_und_karte_pruefen_token_und_modul(client):
    _setup_user("readonly")
    current = _closure(
        title="Oeffentliche aktive Sperre",
        geometry_geojson='{"type":"Point","coordinates":[9.7,47.5]}',
    )
    _closure(
        title="Oeffentlich abgelaufen",
        valid_from=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2),
        valid_until=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
    )
    _token("status-public")
    _token("status-disabled", active=False)
    response = client.get("/infoscreen/strassensperren/status-public")
    assert response.status_code == 200
    assert "Oeffentliche aktive Sperre" in response.text
    assert 'href="/strassensperren/' not in response.text
    assert client.get("/infoscreen/strassensperren/unknown").status_code == 401
    assert client.get("/infoscreen/strassensperren/status-disabled").status_code == 401
    payload = client.get("/infoscreen/strassensperren/status-public/karte.json").json()
    assert [item["properties"]["id"] for item in payload["features"]] == [current.id]
    assert "url" not in payload["features"][0]["properties"]

    disabled = _setup_user("readonly", enabled=False)
    assert disabled.org_id == 1
    assert client.get("/infoscreen/strassensperren/status-public").status_code == 404
