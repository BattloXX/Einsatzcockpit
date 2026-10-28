"""Ereignisschluessel, transaktionssichere Duplikate und Versandbeginn."""
import json
import uuid
from types import SimpleNamespace

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident, IncidentAlarmJob, IncidentLog
from app.models.master import FireDept
from app.models.user import AuditLog
from app.services import alarm_outbox
from app.services.dibos import dibos_enrich
from app.services.lis import lis_sync
from tests.conftest import TestingSession
from tests.test_dibos_create_incidents import _event


@pytest.fixture
def datenbank(setup_db):
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=f"event-key-{uuid.uuid4().hex}", name="Ereignisschluessel-Test")
        db.add(org)
        db.flush()
        einsatz = Incident(primary_org_id=org.id, alarm_type_code="B2", status="active", lis_operation_number=" F-123 ")
        db.add(einsatz)
        db.commit()
        alarm_outbox._duplikat_audit_zeiten.clear()
        try:
            yield db, einsatz
        finally:
            db.rollback()
            db.query(IncidentAlarmJob).filter_by(org_id=org.id).delete()
            db.commit()
            alarm_outbox._duplikat_audit_zeiten.clear()


def test_enqueue_schluessel_und_wiederholung(datenbank):
    db, einsatz = datenbank
    for _ in range(2):
        alarm_outbox.enqueue_incident_alarm(db, einsatz, org_id=einsatz.primary_org_id, source="test")
    db.commit()
    jobs = db.query(IncidentAlarmJob).filter_by(incident_id=einsatz.id).all()
    assert len(jobs) == 4
    assert {job.event_key for job in jobs} == {
        f"{einsatz.primary_org_id}:f-123:{kanal}:" for kanal in alarm_outbox.CHANNELS
    }


def test_enqueue_erkennt_ungeflushte_jobs(datenbank):
    db, einsatz = datenbank
    vorgemerkt = IncidentAlarmJob(
        incident_id=einsatz.id, org_id=einsatz.primary_org_id, channel="sms", variant="",
    )
    db.add(vorgemerkt)
    assert db.autoflush is False and vorgemerkt in db.new
    alarm_outbox.enqueue_incident_alarm(db, einsatz, org_id=einsatz.primary_org_id, source="test")
    db.commit()
    jobs = db.query(IncidentAlarmJob).filter_by(incident_id=einsatz.id).all()
    assert len(jobs) == 4
    assert next(job for job in jobs if job.channel == "sms").id == vorgemerkt.id


def test_event_key_konflikt_erhaelt_aufrufer_transaktion(datenbank):
    db, einsatz = datenbank
    anderer = Incident(primary_org_id=einsatz.primary_org_id, alarm_type_code="B2", status="active")
    db.add(anderer)
    db.flush()
    for kanal in alarm_outbox.CHANNELS:
        db.add(IncidentAlarmJob(
            incident_id=anderer.id, org_id=einsatz.primary_org_id, channel=kanal, variant="",
            event_key=alarm_outbox._event_key(einsatz, einsatz.primary_org_id, kanal, ""),
        ))
    db.flush()
    einsatz.reason = "Aenderung des Aufrufers"
    alarm_outbox.enqueue_incident_alarm(db, einsatz, org_id=einsatz.primary_org_id, source="test")
    db.add(IncidentLog(incident_id=einsatz.id, text="Nach Konflikt weiterhin schreibbar"))
    db.commit()
    db.expire_all()
    assert db.get(Incident, einsatz.id).reason == "Aenderung des Aufrufers"
    assert db.query(IncidentLog).filter_by(incident_id=einsatz.id).one().text == "Nach Konflikt weiterhin schreibbar"
    assert db.query(IncidentAlarmJob).filter_by(incident_id=einsatz.id).count() == 0
    assert db.query(IncidentAlarmJob).filter_by(incident_id=anderer.id).count() == 4


@pytest.mark.parametrize("kanal", alarm_outbox.CHANNELS)
def test_claim_setzt_versandbeginn_nur_teams_und_mail(datenbank, kanal):
    db, einsatz = datenbank
    alarm_outbox.enqueue_incident_alarm(db, einsatz, org_id=einsatz.primary_org_id, source="test")
    db.commit()
    job = db.query(IncidentAlarmJob).filter_by(incident_id=einsatz.id, channel=kanal).one()
    jetzt = alarm_outbox._now()
    assert alarm_outbox._claim_once(db, job.id, job.org_id, kanal, jetzt)
    db.refresh(job)
    assert job.may_have_sent is (kanal in {"teams", "mail"})
    assert job.send_started_at == (jetzt if kanal in {"teams", "mail"} else None)
    assert not alarm_outbox._claim_once(db, job.id, job.org_id, kanal, jetzt)
    db.refresh(job)
    assert job.send_started_at == (jetzt if kanal in {"teams", "mail"} else None)


def test_zwei_organisationen_gleiche_nummer(datenbank):
    db, einsatz = datenbank
    org = FireDept(slug=f"event-zweit-{uuid.uuid4().hex}", name="Zweite Organisation")
    db.add(org)
    db.flush()
    zweiter = Incident(primary_org_id=org.id, lis_operation_number=einsatz.lis_operation_number, alarm_type_code="B2")
    db.add(zweiter)
    db.flush()
    for vorfall in (einsatz, zweiter):
        alarm_outbox.enqueue_incident_alarm(db, vorfall, org_id=vorfall.primary_org_id, source="test")
    jobs = db.query(IncidentAlarmJob).filter(IncidentAlarmJob.incident_id.in_([einsatz.id, zweiter.id])).all()
    assert len(jobs) == len({job.event_key for job in jobs}) == 8
    db.rollback()


def test_nachalarm_schluessel(datenbank):
    db, einsatz = datenbank
    assert alarm_outbox.enqueue_nachalarmierung(
        db, einsatz, org_id=einsatz.primary_org_id, key="code:r2", codes=["r2"],
        source="test", base_url=None, push_url=None,
    )
    jobs = db.query(IncidentAlarmJob).filter_by(incident_id=einsatz.id).all()
    assert len(jobs) == 4
    assert {job.event_key for job in jobs} == {
        f"{einsatz.primary_org_id}:f-123:{kanal}:nach:code:r2" for kanal in alarm_outbox.CHANNELS
    }


def test_dibos_duplikat_ohne_zusaetzliche_alarmierung(datenbank):
    db, einsatz = datenbank
    org_id = einsatz.primary_org_id
    ereignis = _event("f-duplicate-1234")
    erster = dibos_enrich.enrich_events_for_org(org_id, [ereignis], create_incidents=True)
    assert len(erster["created_ids"]) == 1
    kennung = erster["created_ids"][0]
    for _ in range(3):
        assert dibos_enrich.enrich_events_for_org(org_id, [ereignis], create_incidents=True)["created_ids"] == []
    db.expire_all()
    assert db.query(Incident).filter_by(primary_org_id=org_id, lis_operation_number="f-duplicate-1234").count() == 1
    assert db.query(IncidentAlarmJob).filter_by(incident_id=kennung).count() == 4
    protokoll = db.query(AuditLog).filter_by(incident_id=kennung, action="incident.alarm_duplicate_ingest").one()
    assert json.loads(protokoll.payload_json) == {"source": "dibos"}


def test_audit_throttle_quelle_fenster_und_limit(datenbank, monkeypatch):
    db, einsatz = datenbank
    zeit = [1000.0]
    monkeypatch.setattr(alarm_outbox.time, "monotonic", lambda: zeit[0])
    for quelle in ("dibos", "dibos", "lis", "lis"):
        alarm_outbox._protokolliere_doppelten_eingang(db, einsatz, quelle)
    zeit[0] += 3599
    alarm_outbox._protokolliere_doppelten_eingang(db, einsatz, "dibos")
    zeit[0] += 1
    alarm_outbox._protokolliere_doppelten_eingang(db, einsatz, "dibos")
    assert db.query(AuditLog).filter_by(incident_id=einsatz.id, action="incident.alarm_duplicate_ingest").count() == 3
    monkeypatch.setattr(alarm_outbox, "_DUPLIKAT_AUDIT_LIMIT", 2)
    alarm_outbox._protokolliere_doppelten_eingang(db, einsatz, "test")
    assert len(alarm_outbox._duplikat_audit_zeiten) == 2


@pytest.mark.parametrize("datenbankfehler", [False, True])
def test_audit_fehler_bricht_poll_nicht(datenbank, monkeypatch, caplog, datenbankfehler):
    db, einsatz = datenbank

    def fehler(*args, **kwargs):
        if datenbankfehler:
            db.add(AuditLog(action=None))
        else:
            raise RuntimeError("Audit nicht verfuegbar")

    monkeypatch.setattr("app.core.audit.write_audit", fehler)
    alarm_outbox._protokolliere_doppelten_eingang(db, einsatz, "dibos")
    einsatz.reason = "Poll laeuft weiter"
    db.commit()
    assert "Duplikat-Audit fehlgeschlagen" in caplog.text
    assert len(alarm_outbox._duplikat_audit_zeiten) == 1


def test_lis_vorhandener_treffer_protokolliert_einmal(datenbank, monkeypatch):
    db, einsatz = datenbank
    monkeypatch.setattr(lis_sync, "_find_by_lis_operation_id", lambda *args: einsatz)
    monkeypatch.setattr(lis_sync, "_reopen_if_auto_closed", lambda *args: None)
    for _ in range(2):
        ergebnis = lis_sync._get_or_link_incident(
            db, SimpleNamespace(id=einsatz.primary_org_id), {"lis_operation_id": "test"},
        )
        assert ergebnis == (einsatz, False)
    assert json.loads(db.query(AuditLog).filter_by(
        incident_id=einsatz.id, action="incident.alarm_duplicate_ingest",
    ).one().payload_json) == {"source": "lis"}
