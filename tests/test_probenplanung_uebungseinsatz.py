"""Phase 9: Probe -> sicher abgeschirmter Uebungseinsatz."""

from unittest.mock import AsyncMock, patch

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentLog
from app.models.master import Member, OrgSettings, SystemSettings
from app.models.probenplanung import Probeart
from app.models.teilnahme import Teilnahme, Termin
from tests.test_probenplanung_checkliste import ORG_ID, _flags, _login, _probe_anlegen, _probeart, _user


def _probe(client, username: str, role: str = "probenverwalter") -> tuple[str, int]:
    _user(username, role)
    _flags()
    csrf = _login(client, username)
    probeart_id = _probeart(0, f"Phase9 {username}")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Probeart, probeart_id).uebungseinsatz_erlaubt = True
        db.commit()
    finally:
        db.close()
    return csrf, _probe_anlegen(client, csrf, probeart_id, f"Vollprobe {username}")


def _incident_for(termin_id: int) -> Incident:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        termin = db.get(Termin, termin_id)
        assert termin and termin.exercise_incident_id
        incident = db.get(Incident, termin.exercise_incident_id)
        assert incident
        db.expunge(incident)
        return incident
    finally:
        db.close()


def test_erstellung_ohne_uebernahmen_ist_uebung(client):
    csrf, termin_id = _probe(client, "phase9_ohne")
    response = client.post(
        f"/probenplanung/{termin_id}/uebungseinsatz",
        data={"_csrf": csrf, "alarm_type_code": "T1"}, follow_redirects=False,
    )
    assert response.status_code == 303
    incident = _incident_for(termin_id)
    assert incident.is_exercise is True
    assert incident.address_street is None
    assert incident.report_text is None


def test_erstellung_mit_uebernahmen_und_hinweis(client):
    csrf, termin_id = _probe(client, "phase9_mit")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        termin = db.get(Termin, termin_id)
        termin.objekt = "Übungsobjekt 12"
        termin.ort = "Teststadt"
        termin.alarmtext = "Rauchentwicklung im Übungsobjekt"
        termin.besondere_gefahren = "Gasflaschen"
        termin.besondere_hinweise = "Nur Übungsnebel"
        db.commit()
    finally:
        db.close()
    response = client.post(
        f"/probenplanung/{termin_id}/uebungseinsatz",
        data={
            "_csrf": csrf, "alarm_type_code": "T1", "objekt_adresse": "1",
            "alarmtext": "1", "gefahren_hinweise": "1",
        }, follow_redirects=False,
    )
    assert response.status_code == 303
    incident = _incident_for(termin_id)
    assert (incident.address_street, incident.address_city) == ("Übungsobjekt 12", "Teststadt")
    assert incident.report_text == "Rauchentwicklung im Übungsobjekt"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        log = db.query(IncidentLog).filter(IncidentLog.incident_id == incident.id).one()
        assert "Gasflaschen" in log.text and "Nur Übungsnebel" in log.text
    finally:
        db.close()


def test_lagekarte_kennzeichnet_nur_uebungseinsatz(client):
    csrf, termin_id = _probe(client, "phase9_lagekarte", "incident_leader")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        system = db.get(SystemSettings, "lagefuehrung_modul_aktiv")
        if system is None:
            db.add(SystemSettings(key="lagefuehrung_modul_aktiv", value="true"))
        else:
            system.value = "true"
        settings = db.query(OrgSettings).filter(OrgSettings.org_id == ORG_ID).one()
        settings.lagefuehrung_modul_aktiv = True
        real_incident = Incident(primary_org_id=ORG_ID, alarm_type_code="T1", status="active")
        db.add(real_incident)
        db.commit()
        real_incident_id = real_incident.id
    finally:
        db.close()

    client.post(f"/probenplanung/{termin_id}/uebungseinsatz", data={"_csrf": csrf})
    exercise_incident = _incident_for(termin_id)

    exercise_page = client.get(f"/einsatz/{exercise_incident.id}/lagefuehrung")
    real_page = client.get(f"/einsatz/{real_incident_id}/lagefuehrung")

    assert exercise_page.status_code == 200
    assert "ÜBUNG / SIMULATION" in exercise_page.text
    assert real_page.status_code == 200
    assert "ÜBUNG / SIMULATION" not in real_page.text


def test_doppelanlage_verlinkt_und_weiterer_braucht_sonderaktion(client):
    csrf, termin_id = _probe(client, "phase9_doppel")
    url = f"/probenplanung/{termin_id}/uebungseinsatz"
    assert client.post(url, data={"_csrf": csrf}, follow_redirects=False).status_code == 303
    first = _incident_for(termin_id)
    blocked = client.post(url, data={"_csrf": csrf}, follow_redirects=False)
    assert blocked.status_code == 303
    assert f"bereits_vorhanden={first.id}" in blocked.headers["location"]
    assert client.post(f"{url}/weiterer", data={"_csrf": csrf}, follow_redirects=False).status_code == 400
    created = client.post(
        f"{url}/weiterer", data={"_csrf": csrf, "bestaetigt": "true"}, follow_redirects=False,
    )
    assert created.status_code == 303
    second = _incident_for(termin_id)
    assert second.id != first.id
    page = client.get(f"/probenplanung/{termin_id}/uebungseinsatz")
    assert f'/einsatz/{second.id}' in page.text


def test_statussync_start_und_abschluss_sowie_best_effort(client):
    csrf, termin_id = _probe(client, "phase9_sync", "incident_leader")
    client.post(f"/probenplanung/{termin_id}/uebungseinsatz", data={"_csrf": csrf})
    incident = _incident_for(termin_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Termin, termin_id).status = "vorbereitung_abgeschlossen"
        db.commit()
    finally:
        db.close()
    started = client.post(
        f"/probenplanung/{termin_id}/uebungseinsatz/starten",
        data={"_csrf": csrf}, follow_redirects=False,
    )
    assert started.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Termin, termin_id).status == "durchfuehrung_laeuft"
    finally:
        db.close()
    closed = client.post(f"/einsatz/{incident.id}/abschliessen", data={"_csrf": csrf}, follow_redirects=False)
    assert closed.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Termin, termin_id).status == "durchgefuehrt"
    finally:
        db.close()

    csrf2, termin_id2 = _probe(client, "phase9_syncfehler", "incident_leader")
    client.post(f"/probenplanung/{termin_id2}/uebungseinsatz", data={"_csrf": csrf2})
    incident2 = _incident_for(termin_id2)
    with patch("app.services.probe_exercise_service.einsatzabschluss_synchronisieren", side_effect=RuntimeError):
        response = client.post(
            f"/einsatz/{incident2.id}/abschliessen", data={"_csrf": csrf2}, follow_redirects=False,
        )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Incident, incident2.id).status == "closed"
    finally:
        db.close()


def test_erneuter_einsatzabschluss_belaesst_abgeschlossene_probe(client):
    csrf, termin_id = _probe(client, "phase9_abgeschlossen", "incident_leader")
    client.post(f"/probenplanung/{termin_id}/uebungseinsatz", data={"_csrf": csrf})
    incident = _incident_for(termin_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Termin, termin_id).status = "abgeschlossen"
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/einsatz/{incident.id}/abschliessen", data={"_csrf": csrf}, follow_redirects=False,
    )

    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Termin, termin_id).status == "abgeschlossen"
    finally:
        db.close()


def test_einsatzabschluss_belaesst_abgesagte_probe(client):
    csrf, termin_id = _probe(client, "phase9_abgesagt", "incident_leader")
    client.post(f"/probenplanung/{termin_id}/uebungseinsatz", data={"_csrf": csrf})
    incident = _incident_for(termin_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(Termin, termin_id).status = "abgesagt"
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/einsatz/{incident.id}/abschliessen", data={"_csrf": csrf}, follow_redirects=False,
    )

    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Termin, termin_id).status == "abgesagt"
    finally:
        db.close()


def test_teilnehmeruebernahme_ueberspringt_freitext(client):
    csrf, termin_id = _probe(client, "phase9_teilnehmer")
    client.post(f"/probenplanung/{termin_id}/uebungseinsatz", data={"_csrf": csrf})
    incident = _incident_for(termin_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        member = Member(org_id=ORG_ID, firstname="Mit", lastname="Zuordnung", active=True)
        db.add(member)
        db.flush()
        db.add_all([
            Teilnahme(org_id=ORG_ID, bezug_typ="einsatz", bezug_id=incident.id, mitglied_id=member.id),
            Teilnahme(org_id=ORG_ID, bezug_typ="einsatz", bezug_id=incident.id, freitext_name="Ohne Zuordnung"),
        ])
        db.commit()
        member_id = member.id
    finally:
        db.close()
    response = client.post(
        f"/probenplanung/{termin_id}/uebungseinsatz/teilnehmer-uebernehmen",
        data={"_csrf": csrf}, follow_redirects=False,
    )
    assert response.status_code == 303 and "uebernommen=1" in response.headers["location"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        rows = db.query(Teilnahme).filter(Teilnahme.bezug_typ == "uebung", Teilnahme.bezug_id == termin_id).all()
        assert [(row.mitglied_id, row.status) for row in rows] == [(member_id, "anwesend")]
    finally:
        db.close()


def test_exercise_guard_blockiert_alle_produktiven_side_effects(client):
    csrf, termin_id = _probe(client, "phase9_guard")
    with (
        patch("app.services.sms_dispatch_service.dispatch_einsatzinfo", new_callable=AsyncMock) as sms,
        patch("app.services.incident_notify._send_incident_push", new_callable=AsyncMock) as push,
        patch("app.services.teams_alarm_service.post_incident_card", new_callable=AsyncMock) as teams,
        patch("app.routers.ui_incident._create_neighbor_invitations") as nachbar,
        patch("app.services.lis.lis_sync.push_vehicle_status_to_lis", new_callable=AsyncMock) as lis,
        patch("app.services.wordpress_report_service.post_incident_report", new_callable=AsyncMock) as wordpress,
        patch("app.services.broadcast.broadcast_org", new_callable=AsyncMock) as websocket,
    ):
        client.post(f"/probenplanung/{termin_id}/uebungseinsatz", data={"_csrf": csrf})
        response = client.post(
            f"/probenplanung/{termin_id}/uebungseinsatz/starten",
            data={"_csrf": csrf}, follow_redirects=False,
        )
    assert response.status_code == 303
    assert sms.call_count == push.call_count == teams.call_count == 0
    assert nachbar.call_count == lis.call_count == wordpress.call_count == 0
    assert websocket.call_count == 1
    assert websocket.call_args.args[1]["alarm_erlaubt"] is False
