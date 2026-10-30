"""Gastzugang ueber Alarm-Link: Nachweis, Widerruf, Einsatzbindung und Sperren."""
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.security import hash_pin, sign_session, unsign_session
from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentToken
from app.models.teams_bot import AlarmToken
from app.models.user import AuditLog, User
from app.services import alarmlink_login_service as guard
from app.services.incident_qr_service import ensure_incident_qr_token, get_or_create_qr_principal
from tests.conftest import TestingSession, flatten_routes


def make_alarm(*, org_id=1, pin="1234"):
    raw = uuid4().hex
    with TestingSession() as db:
        set_tenant_context(db, None)
        inc = Incident(primary_org_id=org_id, alarm_type_code="T1", status="active",
                       access_pin_hash=hash_pin(pin) if pin else None)
        db.add(inc)
        db.flush()
        alarm = AlarmToken(incident_id=inc.id, token_hash=hashlib.sha256(raw.encode()).hexdigest())
        db.add(alarm)
        db.commit()
        return inc.id, raw, alarm.id


def login(client, token, **data):
    if "ec_csrf" not in client.cookies:
        client.get(f"/alarm/{token}")
    return client.post(f"/alarm/{token}/anmelden", data={"_csrf": client.cookies["ec_csrf"],
                       "pin": "1234", "display_name": "Maria", **data},
                       follow_redirects=False)


def test_pin_und_name_session_und_audit(client):
    inc_id, token, _ = make_alarm()
    page = client.get(f"/alarm/{token}/anmelden")
    assert page.status_code == 200 and 'name="_csrf"' in page.text
    assert login(client, token, pin="9999").status_code == 403
    assert "session" not in client.cookies
    assert login(client, token, display_name="\n\t").status_code == 422
    response = login(client, token, display_name="  Ma\x00ria\n Muster  ")
    assert response.status_code == 302 and response.headers["location"] == f"/einsatz/{inc_id}"
    session = unsign_session(client.cookies["session"])
    assert session[1:3] == (True, inc_id) and session[4] == "Maria Muster"
    assert "board_pin" not in response.headers.get("set-cookie", "")
    assert client.get(f"/einsatz/{inc_id}", follow_redirects=False).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.get(User, session[0])
        assert user.org_id == 1 and user.role_codes == {"readonly"}
        assert db.query(IncidentToken).filter_by(incident_id=inc_id, issued_by_user_id=user.id).first()
        assert db.query(AuditLog).filter_by(action="auth.alarmlink_login", incident_id=inc_id).first()
    assert client.get(f"/alarm/{token}", follow_redirects=False).headers["location"] == f"/einsatz/{inc_id}/info"


def test_name_max_120(client):
    _, token, _ = make_alarm()
    assert login(client, token, display_name="A" * 150).status_code == 302
    assert unsign_session(client.cookies["session"])[4] == "A" * 120


def test_ohne_pin_nur_konto(client):
    inc_id, token, _ = make_alarm(pin=None)
    for path in (f"/alarm/{token}", f"/alarm/{token}/anmelden"):
        response = client.get(path)
        assert response.status_code == 200
        assert f'/login?next=/einsatz/{inc_id}' in response.text
        assert 'name="pin"' not in response.text
        assert "Mit PIN und Namen anmelden" not in response.text
    assert login(client, token).status_code == 403
    assert "session" not in client.cookies


@pytest.mark.parametrize("reason", ["revoked", "expired", "page_expired", "closed", "qr_revoked"])
def test_ungueltiger_nachweis_beendet_session(client, reason):
    inc_id, token, alarm_id = make_alarm()
    assert login(client, token).status_code == 302
    with TestingSession() as db:
        set_tenant_context(db, None)
        alarm = db.get(AlarmToken, alarm_id)
        now = datetime.now(UTC).replace(tzinfo=None)
        if reason == "revoked":
            alarm.revoked_at = now
        elif reason == "expired":
            alarm.expires_at = now - timedelta(seconds=1)
        elif reason == "page_expired":
            alarm.created_at = now - timedelta(days=36500)
        elif reason == "closed":
            db.get(Incident, inc_id).status = "closed"
        else:
            db.query(IncidentToken).filter_by(incident_id=inc_id).first().revoked_at = now
        db.commit()
    # Keine interne Weiterleitung mehr; Tokenwiderruf ist 404, Einsatzende bleibt oeffentlich lesbar.
    response = client.get(f"/alarm/{token}", follow_redirects=False)
    assert response.status_code in (200, 404)
    assert client.get(f"/einsatz/{inc_id}", follow_redirects=False).status_code in (302, 303, 401)
    if reason != "qr_revoked":
        assert client.get(f"/alarm/{token}/anmelden").status_code in (403, 404)
        assert login(client, token).status_code in (403, 404)


@pytest.mark.parametrize("guest", [True, False])
def test_gast_und_qr_duerfen_nicht_zu_anderem_einsatz(client, guest):
    inc_a, token_a, _ = make_alarm()
    inc_b, token_b, _ = make_alarm()
    if guest:
        assert login(client, token_a).status_code == 302
    else:
        with TestingSession() as db:
            set_tenant_context(db, None)
            principal = get_or_create_qr_principal(db, 1)
            ensure_incident_qr_token(db, db.get(Incident, inc_a), issuing_user_id=principal.id)
            cookie = sign_session(principal.id, qr=True, incident_id=inc_a, display_name="QR")
            db.commit()
        client.cookies.set("session", cookie)
    assert client.get(f"/einsatz/{inc_b}", follow_redirects=False).status_code == 403
    assert client.get(f"/einsatz/{inc_b}/info", follow_redirects=False).status_code == 403
    assert client.get(f"/einsatz/{inc_b}/journal", follow_redirects=False).status_code == 403
    assert client.get(f"/api/v1/einsatz/{inc_b}", follow_redirects=False).status_code == 403
    assert client.get(f"/api/uebernahme/objekte?incident_id={inc_b}").status_code == 403
    assert client.get(f"/medien?incident_id={inc_b}").status_code == 403
    with TestingSession() as db:
        set_tenant_context(db, None)
        from app.models.incident import Task, TaskMedia
        task = Task(incident_id=inc_b, title="Fremder Auftrag")
        db.add(task)
        db.flush()
        media = TaskMedia(incident_id=inc_b, task_id=task.id, kind="image", original_filename="fremd.jpg",
                          storage_path="fremd.jpg", mime_type="image/jpeg")
        db.add(media)
        db.commit()
        media_id = media.id
    assert client.get(f"/medien/datei/{media_id}").status_code == 403
    assert client.get(f"/annotieren/task/{media_id}/original").status_code == 403
    assert client.get(f"/alarm/{token_b}", follow_redirects=False).status_code == 200


def test_konto_wird_direkt_weitergeleitet(client):
    inc_id, token, _ = make_alarm()
    with TestingSession() as db:
        set_tenant_context(db, None)
        principal = get_or_create_qr_principal(db, 1)
        cookie = sign_session(principal.id)
        db.commit()
    client.cookies.set("session", cookie)
    response = client.get(f"/alarm/{token}", follow_redirects=False)
    assert response.status_code in (302, 307) and response.headers["location"] == f"/einsatz/{inc_id}/info"


def test_ip_limit(client):
    _, token, _ = make_alarm()
    for _ in range(5):
        assert login(client, token, pin="bad").status_code == 403
    assert login(client, token).status_code == 429


def test_einsatzweite_sperre_auch_mit_anderem_token(client):
    inc_id, token, alarm_id = make_alarm()
    # Nur IP-Bucket entfernen, damit 20 verteilte Fehlversuche simuliert werden.
    for i in range(20):
        # Der TestClient-Transport liefert die wechselnde Quell-IP an Request.client.
        client._transport.client = (f"192.0.2.{i + 1}", 50000)
        response = login(client, token, pin="bad")
        assert response.status_code == (429 if i == 19 else 403)
    assert guard.gesperrt(inc_id)
    new_token = uuid4().hex
    with TestingSession() as db:
        alarm = db.get(AlarmToken, alarm_id)
        alarm.token_hash = hashlib.sha256(new_token.encode()).hexdigest()
        db.commit()
    client._transport.client = ("192.0.2.100", 50000)
    response = login(client, new_token)
    assert response.status_code == 429 and "15 Minuten" in response.text
    guard._storage().clear(guard._key(inc_id))
    guard._storage().clear(guard._key(inc_id) + ":locked")
    assert login(client, new_token).status_code == 302


def test_anmeldung_braucht_csrf(client):
    _, token, _ = make_alarm()
    response = client.post(f"/alarm/{token}/anmelden", data={"pin": "1234", "display_name": "Maria"})
    assert response.status_code == 403
    assert "session" not in client.cookies


def test_readonly_gast_alle_einsatz_schreibrouten_gesperrt(client):
    from app.main import app
    import re

    inc_id, token, _ = make_alarm()
    assert login(client, token).status_code == 302
    assert client.get(f"/einsatz/{inc_id}").status_code == 200
    # Alle registrierten Einsatz-Mutationen, auch ohne Rollen-Dependency und
    # mit readonly-Ausnahme, muessen vor Validierung/Mutation 403 liefern.
    checked = set()
    for route in flatten_routes(app.routes):
        path = getattr(route, "path", "")
        if "{incident_id}" not in path:
            continue
        path = path.replace("{incident_id}", str(inc_id))
        path = re.sub(r"\{[^}]+\}", "1", path)
        for method in getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}:
            response = client.request(method, path, data={"_csrf": client.cookies["ec_csrf"],
                                      "title": "Gast", "text": "Gast", "message": "Gast"},
                                      follow_redirects=False)
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["detail"] == "Gastzugang erlaubt nur Lesen"
            checked.add(path)
    assert {f"/einsatz/{inc_id}/{suffix}" for suffix in
            ("meldung", "aufgabe", "fahrzeug-hinzufuegen", "log", "funkjournal", "route/neu")} <= checked


def test_bestehende_gast_session_bleibt_recorder(client):
    inc_id, _, alarm_id = make_alarm()
    with TestingSession() as db:
        set_tenant_context(db, None)
        principal = get_or_create_qr_principal(db, 1)
        ensure_incident_qr_token(db, db.get(Incident, inc_id), issuing_user_id=principal.id)
        cookie = sign_session(principal.id, qr=True, incident_id=inc_id,
                              display_name="Bestehender Gast", alarm_token_id=alarm_id)
        db.commit()
    client.cookies.set("session", cookie)
    client.get(f"/einsatz/{inc_id}")
    response = client.post(f"/einsatz/{inc_id}/log", data={"_csrf": client.cookies["ec_csrf"], "text": "Bestand"})
    assert response.status_code == 204
