"""Manuelles Teams-Nachsenden bleibt mandantensicher und einmalig."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog
from app.models.user import AuditLog, Role, UserRole
from app.services import alarm_outbox as alarm
from app.services.teams_alarm_service import TeamsDispatchResult
from tests.test_alarm_outbox import _aufraeumen, _db_incident
from tests.test_einsatzinfo_pr11 import _login, _setup_incident


@pytest.fixture
def teams_auftrag():
    db, einsatz = _db_incident()
    beginn = alarm._now()
    auftrag = IncidentAlarmJob(
        incident_id=einsatz.id, org_id=1, channel="teams", variant="", status="uncertain",
        may_have_sent=True, send_started_at=beginn, finished_at=beginn,
        lease_until=beginn, last_error="Timeout", context={"source": "test"},
    )
    db.add(auftrag)
    db.commit()
    yield db, einsatz, auftrag
    _aufraeumen(db, einsatz)


@pytest.mark.parametrize("status", ["uncertain", "failed"])
def test_nachsenden_setzt_nur_versandfreigabe_und_protokolliert(teams_auftrag, status):
    db, einsatz, auftrag = teams_auftrag
    auftrag.status = status
    db.commit()
    beginn = auftrag.send_started_at
    assert alarm.teams_manuell_erneut(db, einsatz.id, 1, 1) == "ok"
    db.refresh(auftrag)
    assert auftrag.status == "pending"
    assert auftrag.next_attempt_at <= alarm._now()
    assert auftrag.may_have_sent and auftrag.send_started_at == beginn
    assert auftrag.finished_at is auftrag.lease_until is auftrag.last_error is None
    assert auftrag.context == {"source": "test", "manual_resend": True}
    assert db.query(IncidentLog).filter_by(
        incident_id=einsatz.id, text="Teams-Alarmkarte manuell erneut angefordert",
    ).count() == 1
    audit = db.query(AuditLog).filter_by(
        incident_id=einsatz.id, action="incident.alarm_teams_resend",
    ).one()
    assert audit.org_id == 1 and audit.user_id == 1
    assert json.loads(audit.payload_json) == {"job_id": auftrag.id, "manual_resend": True}


@pytest.mark.parametrize("status", ["sent", "pending", "sending", "suppressed", "retry"])
def test_andere_status_bleiben_unveraendert(teams_auftrag, status):
    db, einsatz, auftrag = teams_auftrag
    auftrag.status = status
    db.commit()
    assert alarm.teams_manuell_erneut(db, einsatz.id, 1, 1) == "nicht_noetig"
    db.refresh(auftrag)
    assert auftrag.status == status
    assert not db.query(AuditLog).filter_by(
        incident_id=einsatz.id, action="incident.alarm_teams_resend",
    ).count()


def test_nachalarm_und_andere_org_bleiben_unberuehrt(teams_auftrag):
    db, einsatz, auftrag = teams_auftrag
    assert alarm.teams_manuell_erneut(db, einsatz.id, 999, 1) == "nicht_gefunden"
    auftrag.variant = "nach:test"
    db.commit()
    assert alarm.teams_manuell_erneut(db, einsatz.id, 1, 1) == "nicht_gefunden"
    db.refresh(auftrag)
    assert auftrag.status == "uncertain"


def test_zwei_parallele_anforderungen_nur_einmal_ok(teams_auftrag):
    db, einsatz, auftrag = teams_auftrag
    einsatz_id = einsatz.id
    schranke = Barrier(2)

    def anfordern():
        with SessionLocal() as sitzung:
            set_tenant_context(sitzung, None)
            def nach_lesen(sitzung, objekt):
                if isinstance(objekt, IncidentAlarmJob):
                    schranke.wait(timeout=5)
            event.listen(sitzung, "loaded_as_persistent", nach_lesen)
            return alarm.teams_manuell_erneut(sitzung, einsatz_id, 1, 1)

    with ThreadPoolExecutor(max_workers=2) as arbeiter:
        ergebnisse = list(arbeiter.map(lambda _: anfordern(), range(2)))
    assert sorted(ergebnisse) == ["nicht_noetig", "ok"]
    assert db.query(AuditLog).filter_by(
        incident_id=einsatz_id, action="incident.alarm_teams_resend",
    ).count() == 1


async def test_genau_ein_weiterer_teams_post(teams_auftrag, monkeypatch):
    db, einsatz, auftrag = teams_auftrag
    auftrag.context = {**auftrag.context, "base_url": "https://example.test"}
    db.commit()
    aufrufe = []

    async def senden(*args, **kwargs):
        aufrufe.append(1)
        return TeamsDispatchResult(sent=True)

    monkeypatch.setattr("app.services.exercise_guard.darf_extern", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.teams_alarm_service.post_incident_card", senden)
    assert alarm.teams_manuell_erneut(db, einsatz.id, 1, 1) == "ok"
    await alarm.process_incident_alarm(einsatz.id)
    await alarm.process_incident_alarm(einsatz.id)
    db.refresh(auftrag)
    assert aufrufe == [1]
    assert auftrag.status == "sent"


def test_1020_wiederholt_db_versuch_ohne_doppeltes_audit(teams_auftrag, monkeypatch):
    db, einsatz, auftrag = teams_auftrag
    ausfuehren = db.execute
    versuche = []

    def mit_konflikt(*args, **kwargs):
        if getattr(args[0], "is_update", False):
            versuche.append(1)
            if len(versuche) == 1:
                raise OperationalError("UPDATE", {}, Exception(1020, "Konflikt"))
        return ausfuehren(*args, **kwargs)

    monkeypatch.setattr(db, "execute", mit_konflikt)
    assert alarm.teams_manuell_erneut(db, einsatz.id, 1, 1) == "ok"
    assert len(versuche) == 2
    assert db.query(AuditLog).filter_by(
        incident_id=einsatz.id, action="incident.alarm_teams_resend",
    ).count() == 1


@pytest.mark.parametrize("status", ["uncertain", "failed", "sent", "pending", "sending"])
def test_modal_button_nur_bei_unklar_oder_fehlgeschlagen(client, status):
    name = f"teams_modal_{status}"
    _, einsatz_id, _ = _setup_incident(name)
    with SessionLocal() as db:
        db.add(IncidentAlarmJob(incident_id=einsatz_id, org_id=1, channel="teams", status=status))
        db.commit()
    _login(client, name, "Test1234!")
    antwort = client.get(f"/einsatz/{einsatz_id}/alarm/bearbeiten")
    assert antwort.status_code == 200
    assert ("Teams-Karte erneut senden" in antwort.text) == (status in ("uncertain", "failed"))
    if status in ("uncertain", "failed"):
        assert 'name="_csrf"' in antwort.text
        assert 'hx-confirm="Die Zustellung' in antwort.text
        assert 'hx-target="#cardDetailBody"' in antwort.text


@pytest.mark.parametrize("fall,erwartet", [
    ("erlaubt", 200), ("readonly", 403), ("andere_org", 404), ("geschlossen", 409),
])
def test_route_rechte_org_und_abschluss(client, monkeypatch, fall, erwartet):
    name = f"teams_route_{fall}"
    benutzer_id, einsatz_id, _ = _setup_incident(name)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.add(IncidentAlarmJob(incident_id=einsatz_id, org_id=1, channel="teams", status="uncertain"))
        if fall == "readonly":
            db.query(UserRole).filter_by(user_id=benutzer_id).delete()
            rolle = db.query(Role).filter_by(code="readonly").one()
            db.add(UserRole(user_id=benutzer_id, role_id=rolle.id))
        if fall == "andere_org":
            db.get(Incident, einsatz_id).primary_org_id = 999
        if fall == "geschlossen":
            db.get(Incident, einsatz_id).status = "closed"
        db.commit()
    aufrufe = []

    async def verarbeiten(einsatz_id):
        aufrufe.append(einsatz_id)

    monkeypatch.setattr(alarm, "process_incident_alarm", verarbeiten)
    _login(client, name, "Test1234!")
    antwort = client.post(f"/einsatz/{einsatz_id}/alarm/teams-erneut", data={
        "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    assert antwort.status_code == erwartet, antwort.text[:300]
    assert aufrufe == ([einsatz_id] if erwartet == 200 else [])
    if erwartet == 200:
        assert "Teams-Karte wird erneut gesendet." in antwort.text
        assert "Teams-Karte erneut senden" not in antwort.text
        antwort = client.post(f"/einsatz/{einsatz_id}/alarm/teams-erneut", data={
            "_csrf": client.cookies.get("ec_csrf"),
        })
        assert antwort.status_code == 200
        assert aufrufe == [einsatz_id]
