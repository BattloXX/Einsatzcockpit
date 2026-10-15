"""Gezielte Tests fuer Uhr, Regie und Ereignis-Engine der Einzel-Planspiele."""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.models.incident import IncidentColumn, IncidentLog, IncidentVehicle, Message
from app.models.master import AlarmType, FireDept, OrgSettings, VehicleMaster
from app.models.planspiel import PlanspielAdresse, PlanspielRunEreignis, PlanspielSzenario
from app.models.user import User
from app.services.planspiel_engine import (
    beenden,
    ereignis_ausfuehren,
    ereignis_beanspruchen,
    ereignis_jetzt,
    ereignis_ueberspringen,
    ereignis_wiederholen,
    folgemeldungen_setzen,
    fortsetzen,
    freie_meldung,
    pausieren,
    reaktive_trigger_auswerten,
    sim_sekunden,
    stichwort_aendern,
    tick,
    verpasste_markieren,
)
from app.services.planspiel_katalog_service import katalog_importieren
from app.services.planspiel_run_service import EntwurfDaten, entwurf_anlegen, starten
from tests.conftest import TestingSession

JETZT = datetime(2026, 10, 14, 12, 0, 0)


def _basis():
    db = TestingSession()
    set_tenant_context(db, None)
    org = FireDept(slug=f"engine-{uuid4().hex}", name="Engine", fallback_lat=47.47, fallback_lng=9.75)
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, planspiel_gebiet_radius_m=5000))
    db.add_all([AlarmType(org_id=org.id, code=code, category="F", label=code) for code in ("F1", "F3")])
    katalog_importieren(db, org.id)
    user = User(username=f"engine-{uuid4().hex}", display_name="Leitung", org_id=org.id)
    address = PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id=uuid4().hex, strasse="Hauptstraße",
        hausnr="1", plz="6800", ort="Feldkirch", lat=47.471, lng=9.751, qualitaet="A",
        ort_typen=["wohngebaeude"], aktiv=True)
    db.add_all([user, address])
    db.flush()
    set_tenant_context(db, org.id)
    scenario = db.query(PlanspielSzenario).filter_by(code="B09").one()
    run = entwurf_anlegen(db, org.id, user, EntwurfDaten(titel="Engine", art="einzel", erzeugung="manuell",
        seed=42, szenario_id=scenario.id, ort_modus="pool", adresse_id=address.id))
    starten(db, run, user)
    run.gestartet_at = JETZT
    db.commit()
    return db, org, user, run


def _event(db, run, *, due=0.0, trigger="elapsed_time", quelle="szenario", text="Testmeldung"):
    event = PlanspielRunEreignis(org_id=run.org_id, run_id=run.id, seq=900 + db.query(PlanspielRunEreignis).count(),
        quelle=quelle, ziel_typ="incident", ziel_id=run.incident_id, trigger_type=trigger,
        trigger_param={"ereignis_typ": "lageweit"}, faellig_sim_s=due, status="geplant",
        idempotenz_key=f"test:{uuid4().hex}", sichtbarkeit="teilnehmer", text=text)
    db.add(event)
    db.flush()
    return event


def _close(db):
    db.rollback()
    db.close()


def test_uhr_tempo_und_pause():
    db, _, user, run = _basis()
    try:
        run.tempo = 2
        assert sim_sekunden(run, JETZT + timedelta(seconds=30)) == 60
        pausieren(db, run, user, JETZT + timedelta(seconds=30))
        assert sim_sekunden(run, JETZT + timedelta(seconds=130)) == 60
        fortsetzen(db, run, user, JETZT + timedelta(seconds=130))
        assert sim_sekunden(run, JETZT + timedelta(seconds=140)) == 80
    finally:
        _close(db)


def test_zeitereignis_wird_zur_faelligkeit_ausgefuehrt():
    db, org, _, run = _basis()
    try:
        event = _event(db, run, due=10)
        db.commit()
        assert not tick(TestingSession, run.id, org.id, JETZT + timedelta(seconds=9)).ausgefuehrt
        result = tick(TestingSession, run.id, org.id, JETZT + timedelta(seconds=10))
        assert len(result.ausgefuehrt) == 1
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "ausgefuehrt"
        assert db.query(Message).filter(Message.incident_id == run.incident_id).count() == 1
        assert db.query(IncidentLog).filter(IncidentLog.incident_id == run.incident_id).count() >= 2
    finally:
        _close(db)


def test_pause_verhindert_ausfuehrung():
    db, org, user, run = _basis()
    try:
        _event(db, run)
        pausieren(db, run, user, JETZT)
        db.commit()
        assert not tick(TestingSession, run.id, org.id, JETZT).ausgefuehrt
    finally:
        _close(db)


def test_beenden_bricht_ereignisse_ab_und_keine_spaeten_jobs():
    db, org, user, run = _basis()
    try:
        event = _event(db, run)
        beenden(db, run, user, JETZT)
        db.commit()
        assert not tick(TestingSession, run.id, org.id, JETZT).ausgefuehrt
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "abgebrochen"
    finally:
        _close(db)


def test_doppelte_ausfuehrung_ausgeschlossen():
    db, org, _, run = _basis()
    try:
        event = _event(db, run)
        db.commit()
        assert ereignis_beanspruchen(db, event.id, org.id, JETZT)
        assert not ereignis_beanspruchen(db, event.id, org.id, JETZT)
        assert ereignis_ausfuehren(db, event.id, org.id, run.steuer_version, JETZT)
        assert not tick(TestingSession, run.id, org.id, JETZT).ausgefuehrt
    finally:
        _close(db)


def test_steuerversion_race_stoppt_ausfuehrung():
    db, org, user, run = _basis()
    try:
        event = _event(db, run)
        db.commit()
        old = run.steuer_version
        assert ereignis_beanspruchen(db, event.id, org.id, JETZT)
        pausieren(db, run, user, JETZT)
        assert ereignis_ausfuehren(db, event.id, org.id, old, JETZT) is None
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "geplant"
    finally:
        _close(db)


def test_reaktiver_trigger_fahrzeug_am_einsatzort():
    db, _, _, run = _basis()
    try:
        event = _event(db, run, due=None, trigger="phase_reached")
        event.trigger_param = {"phase": "erkundung", "ereignis_typ": "lageweit"}
        assert reaktive_trigger_auswerten(db, run, JETZT) == 0
        vehicle = db.query(IncidentVehicle).filter_by(incident_id=run.incident_id).first()
        if vehicle is None:
            column = IncidentColumn(incident_id=run.incident_id, code="test", title="Test")
            master = VehicleMaster(dept_id=run.org_id, code="TEST", name="Testfahrzeug")
            db.add_all([column, master])
            db.flush()
            vehicle = IncidentVehicle(incident_id=run.incident_id, column_id=column.id, vehicle_master_id=master.id)
            db.add(vehicle)
        vehicle.unit_status = "Am Einsatzort"
        db.flush()
        assert reaktive_trigger_auswerten(db, run, JETZT) >= 1
        assert event.faellig_sim_s == 0
    finally:
        _close(db)


def test_reaktiver_trigger_fallback():
    db, _, _, run = _basis()
    try:
        run.konfig = {**(run.konfig or {}), "reaktiv_fallback_s": 10}
        event = _event(db, run, due=None, trigger="assignment_created")
        assert reaktive_trigger_auswerten(db, run, JETZT + timedelta(seconds=10)) >= 1
        assert event.ergebnis_ref["ausloesung"] == "fallback"
    finally:
        _close(db)


def test_manuelles_ereignis_nur_per_regie():
    db, org, user, run = _basis()
    try:
        event = _event(db, run, due=None, trigger="manual")
        db.commit()
        assert not tick(TestingSession, run.id, org.id, JETZT).ausgefuehrt
        ereignis_jetzt(db, run, event.id, user, JETZT)
        db.commit()
        assert tick(TestingSession, run.id, org.id, JETZT).ausgefuehrt
    finally:
        _close(db)


def test_freie_meldung_idempotent():
    db, _, user, run = _basis()
    try:
        first = freie_meldung(db, run, user, JETZT, text="Regie", client_uuid="same")
        second = freie_meldung(db, run, user, JETZT, text="Regie", client_uuid="same")
        assert first.id == second.id
        assert db.query(PlanspielRunEreignis).filter_by(idempotenz_key=f"run{run.id}:regie:same").count() == 1
    finally:
        _close(db)


def test_ueberspringen_und_wiederholen():
    db, _, user, run = _basis()
    try:
        event = _event(db, run)
        ereignis_ueberspringen(db, run, event.id, user)
        copy = ereignis_wiederholen(db, run, event.id, user, JETZT)
        assert event.status == "uebersprungen" and copy.quelle == "regie"
    finally:
        _close(db)


def test_regie_sichtbarkeit_erzeugt_keine_meldung():
    db, org, user, run = _basis()
    try:
        event = freie_meldung(db, run, user, JETZT, text="intern", sichtbarkeit="regie")
        db.commit()
        assert tick(TestingSession, run.id, org.id, JETZT).ausgefuehrt
        assert db.query(Message).filter_by(incident_id=run.incident_id).count() == 0
        assert event.id
    finally:
        _close(db)


def test_folgemeldungen_aus_stoppt_szenario_aber_nicht_regie():
    db, org, user, run = _basis()
    try:
        scenario = _event(db, run)
        regie = _event(db, run, quelle="regie")
        folgemeldungen_setzen(db, run, user, False)
        db.commit()
        assert len(tick(TestingSession, run.id, org.id, JETZT).ausgefuehrt) == 1
        db.expire_all()
        assert db.get(PlanspielRunEreignis, scenario.id).status == "geplant"
        assert db.get(PlanspielRunEreignis, regie.id).status == "ausgefuehrt"
    finally:
        _close(db)


def test_stichwort_aendern():
    db, _, user, run = _basis()
    try:
        incident = stichwort_aendern(db, run, user, "F1")
        assert incident.alarm_type_code == "F1"
    finally:
        _close(db)


def test_verpasste_ereignisse_nach_ausfall():
    db, _, _, run = _basis()
    try:
        event = _event(db, run, due=0)
        assert verpasste_markieren(db, run, JETZT + timedelta(seconds=121)) == 1
        assert event.status == "uebersprungen"
    finally:
        _close(db)


def test_fehlerhafte_ausfuehrung_wird_wiederholt_und_markiert(monkeypatch):
    db, org, _, run = _basis()
    try:
        event = _event(db, run)
        def kaputt(*args, **kwargs):
            raise RuntimeError("kaputt")

        monkeypatch.setattr("app.services.planspiel_engine.add_message", kaputt)
        db.commit()
        for _ in range(3):
            assert ereignis_beanspruchen(db, event.id, org.id, JETZT)
            ereignis_ausfuehren(db, event.id, org.id, run.steuer_version, JETZT)
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "fehlgeschlagen"
    finally:
        _close(db)


def test_fremde_org_ereignis_nicht_beanspruchbar():
    db, org, _, run = _basis()
    try:
        event = _event(db, run)
        db.commit()
        assert not ereignis_beanspruchen(db, event.id, org.id + 999999, JETZT)
    finally:
        _close(db)


def test_regie_faelligkeit_ist_ganzzahlig_fuer_mariadb_float():
    """MariaDB speichert faellig_sim_s als FLOAT (einfache Genauigkeit): gebrochene
    Werte werden minimal aufgerundet und waeren fuer den Sofort-Tick nicht faellig."""
    db, _org, user, run = _basis()
    try:
        jetzt = JETZT + timedelta(seconds=12.345)
        event = freie_meldung(db, run, user, jetzt, text="Ganzzahlig")
        assert event.faellig_sim_s == float(int(event.faellig_sim_s))
        assert event.faellig_sim_s <= sim_sekunden(run, jetzt)
    finally:
        _close(db)
