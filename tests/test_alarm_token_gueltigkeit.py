"""Gueltigkeit der oeffentlichen Alarm-Routen nach Einsatzabschluss."""
from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.teams_bot import AlarmToken
from app.services.incident_service import close_incident, create_incident


def _einsatz_anlegen(*, schliessen: bool = True):
    db = SessionLocal()
    set_tenant_context(db, None)
    incident, _ = create_incident(
        db, "T1", primary_org_id=1, lat=47.47, lng=9.73,
        report_text="Token-Test",
    )
    token_plain = incident.alarm_token
    if schliessen:
        close_incident(db, incident)
    db.commit()
    token_id = db.query(AlarmToken.id).filter(AlarmToken.incident_id == incident.id).scalar()
    db.close()
    return token_plain, token_id


def _token_aendern(token_id, **werte):
    db = SessionLocal()
    set_tenant_context(db, None)
    token = db.get(AlarmToken, token_id)
    for name, wert in werte.items():
        setattr(token, name, wert)
    db.commit()
    db.close()


def test_kartenbild_bleibt_nach_abschluss_erreichbar(client, monkeypatch):
    plain, _ = _einsatz_anlegen()
    monkeypatch.setattr("app.services.staticmap_service.render_incident_map_png", lambda *a, **k: b"png")
    antwort = client.get(f"/api/v1/teams/map/{plain}.png")
    assert antwort.status_code == 200
    assert antwort.headers["cache-control"] == "public, max-age=86400"


def test_einsatzinfo_seite_bleibt_nach_abschluss_erreichbar(client):
    plain, _ = _einsatz_anlegen()
    assert client.get(f"/alarm/{plain}").status_code == 200


def test_einsatzinfo_seite_ist_nach_31_tagen_gesperrt(client):
    plain, token_id = _einsatz_anlegen()
    _token_aendern(token_id, created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31))
    assert client.get(f"/alarm/{plain}").status_code == 404


def test_kartenbild_bleibt_nach_31_tagen_erreichbar(client, monkeypatch):
    plain, token_id = _einsatz_anlegen()
    _token_aendern(token_id, created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=31))
    monkeypatch.setattr("app.services.staticmap_service.render_incident_map_png", lambda *a, **k: b"png")
    assert client.get(f"/api/v1/teams/map/{plain}.png").status_code == 200


def test_kartenbild_ist_nach_ablauf_von_expires_at_gesperrt(client):
    plain, token_id = _einsatz_anlegen()
    _token_aendern(token_id, expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1))
    assert client.get(f"/api/v1/teams/map/{plain}.png").status_code == 404


def test_manuell_widerrufener_token_ist_ueberall_gesperrt(client):
    plain, token_id = _einsatz_anlegen()
    _token_aendern(token_id, revoked_at=datetime.now(UTC).replace(tzinfo=None))
    assert client.get(f"/alarm/{plain}").status_code == 404
    assert client.get(f"/api/v1/teams/map/{plain}.png").status_code == 404
