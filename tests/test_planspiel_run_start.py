"""Gezielte Tests fuer Entwurf und Start von Einzel-Planspielen."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import IncidentLog
from app.models.master import AlarmType, FireDept, OrgSettings
from app.models.objekt import Objekt, ObjektEinsatz
from app.models.planspiel import PlanspielAdresse, PlanspielRunEreignis, PlanspielSzenario, PlanspielZiel
from app.models.user import User
from app.services.incident_notify import alarm_unterdrueckung_grund
from app.services.planspiel_katalog_service import katalog_importieren
from app.services.planspiel_run_service import (
    EntwurfDaten,
    ZustandsFehler,
    entwurf_anlegen,
    entwurf_verwerfen,
    starten,
    vorschau,
    zeitplan,
)
from tests.conftest import TestingSession


def _basis():
    db = TestingSession()
    set_tenant_context(db, None)
    org = FireDept(slug=f"planspiel-run-{uuid4().hex}", name="Planspiel Run", fallback_lat=47.47, fallback_lng=9.75)
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, planspiel_gebiet_radius_m=5000))
    db.add_all([AlarmType(org_id=org.id, code=code, category=code[0], label=code) for code in ("F1", "F3", "T1")])
    katalog_importieren(db, org.id)
    db.flush()
    user = User(username=f"leiter-{uuid4().hex}", display_name="Leiter", org_id=org.id)
    db.add(user)
    db.flush()
    set_tenant_context(db, org.id)
    return db, org, user


def _pool(db, org, ident="pool"):
    row = PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id=ident, strasse="Hauptstraße", hausnr="1",
                           plz="6800", ort="Feldkirch", lat=47.471, lng=9.751, qualitaet="A",
                           ort_typen=["wohngebaeude"], aktiv=True)
    db.add(row)
    db.flush()
    return row


def _daten(pool, **extra):
    values = dict(titel="Testlauf", art="einzel", erzeugung="manuell", szenario_id=None, seed=42,
                  ort_modus="pool", adresse_id=pool.id)
    values.update(extra)
    return EntwurfDaten(**values)


def _b09(db):
    return db.query(PlanspielSzenario).filter_by(code="B09").one()


def test_entwurf_mit_szenario_und_poolort_erzeugt_snapshot():
    db, org, user = _basis()
    try:
        pool = _pool(db, org)
        szenario = _b09(db)
        run = entwurf_anlegen(db, org.id, user, _daten(pool, szenario_id=szenario.id))
        assert run.szenario_snapshot["code"] == "B09"
        assert "{adresse}" not in run.szenario_snapshot["alarmtext"]
        assert run.szenario_snapshot["ort"]["adresse_id"] == pool.id
        assert run.szenario_snapshot["stichwort_org"] == "F3"
    finally:
        db.rollback()
        db.close()


def test_gleicher_seed_gleicher_entwurf():
    db, org, user = _basis()
    try:
        pool = _pool(db, org)
        first = entwurf_anlegen(db, org.id, user, _daten(pool))
        second = entwurf_anlegen(db, org.id, user, _daten(pool))
        assert first.szenario_snapshot == second.szenario_snapshot
    finally:
        db.rollback()
        db.close()


def test_manueller_ort_ausserhalb_wird_abgelehnt():
    db, org, user = _basis()
    try:
        szenario = _b09(db)
        with pytest.raises(ValueError, match="außerhalb"):
            entwurf_anlegen(db, org.id, user, EntwurfDaten(titel="x", art="einzel", erzeugung="manuell", seed=1,
                szenario_id=szenario.id, ort_modus="manuell", strasse="Fern", lat=48.0, lng=9.75))
    finally:
        db.rollback()
        db.close()


def test_fremdes_objekt_wird_abgelehnt():
    db, org, user = _basis()
    try:
        other = FireDept(slug=f"other-{uuid4().hex}", name="Andere")
        db.add(other)
        db.flush()
        objekt = Objekt(org_id=other.id, name="Fremd", lat=47.47, lng=9.75)
        db.add(objekt)
        db.flush()
        with pytest.raises(ValueError):
            entwurf_anlegen(db, org.id, user, EntwurfDaten(titel="x", art="einzel", erzeugung="manuell", seed=1,
                ort_modus="objekt", objekt_id=objekt.id))
    finally:
        db.rollback()
        db.close()


def test_override_mit_unbekanntem_platzhalter_abgelehnt():
    db, org, user = _basis()
    try:
        pool = _pool(db, org)
        with pytest.raises(ValueError):
            entwurf_anlegen(db, org.id, user, _daten(pool, alarmtext="Test {unbekannt}"))
    finally:
        db.rollback()
        db.close()


def test_zeitplan_deterministisch_und_wahrscheinlichkeit():
    db, org, user = _basis()
    try:
        pool = _pool(db, org)
        events = [{"baustein_code": "X", "ereignis_typ": "eskalation", "trigger_type": "elapsed_time",
                   "offset_min_s": 10, "offset_max_s": 20, "trigger_param": None, "wahrscheinlichkeit": 0,
                   "max_fires": 1, "prioritaet": 0, "sichtbarkeit": "teilnehmer", "ziel": "stelle",
                   "payload_template": "bei {adresse}", "voraussetzungen": None, "abbruch": None, "reihenfolge": 0}]
        run = entwurf_anlegen(db, org.id, user, _daten(pool, ereignisse=events))
        assert zeitplan(run) == zeitplan(run)
        assert zeitplan(run)[0].status == "uebersprungen"
        assert 10 <= zeitplan(run)[0].faellig_sim_s <= 20
        run.folge_aktiv = False
        assert zeitplan(run) == []
    finally:
        db.rollback()
        db.close()


def test_vorschau_listet_gesperrte_kanaele():
    db, org, user = _basis()
    try:
        run = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org)))
        assert "SMS" in vorschau(db, run)["wird_nicht_ausgeloest"]
        assert "keine" in vorschau(db, run)["wird_nicht_ausgeloest"][-1]
    finally:
        db.rollback()
        db.close()


def test_start_legt_uebungseinsatz_ziel_und_ereignisse_an():
    db, org, user = _basis()
    try:
        run = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org)))
        incident = starten(db, run, user)
        assert incident.is_exercise and incident.planspiel_run_id == run.id
        assert incident.alarm_type_code == run.szenario_snapshot["stichwort_org"]
        assert incident.report_text == run.szenario_snapshot["alarmtext"]
        assert "[Planspiel]" in db.query(IncidentLog).filter(IncidentLog.incident_id == incident.id).one().text
        assert db.query(PlanspielZiel).filter_by(run_id=run.id, seq=1).one()
        assert all(event.idempotenz_key.startswith(f"run{run.id}:") for event in db.query(PlanspielRunEreignis).all())
        assert run.status == "laeuft"
    finally:
        db.rollback()
        db.close()


def test_start_loest_keine_externen_kanaele_aus(monkeypatch):
    db, org, user = _basis()
    try:
        settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
        for flag in ("uebung_push_erlaubt", "uebung_ws_alarm_erlaubt", "uebung_nachbar_einladung_erlaubt",
                     "uebung_lis_status_erlaubt", "uebung_wordpress_bericht_erlaubt", "uebung_geocoding_erlaubt",
                     "einsatzinfo_sms_send_exercise", "einsatzinfo_mail_send_exercise", "gk_sms_uebung"):
            setattr(settings, flag, True)
        for target in ("app.services.incident_notify.notify_incident_created",
                       "app.services.print_dispatcher.autoprint_incident_background",
                       "app.services.objekt_matching_service.match_incident_background",
                       "app.services.geocoding.geocode_address", "app.services.push_service.notify_org_web"):
            monkeypatch.setattr(target, lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError(target)))
        run = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org)))
        incident = starten(db, run, user)
        assert alarm_unterdrueckung_grund(db, incident, org.id) == "Planspiel"
    finally:
        db.rollback()
        db.close()


def test_start_mit_objekt_verknuepft_objekteinsatz():
    db, org, user = _basis()
    try:
        objekt = Objekt(org_id=org.id, name="Schule", strasse="Objektweg", hausnummer="2", lat=47.471, lng=9.751)
        db.add(objekt)
        db.flush()
        b09 = _b09(db)
        run = entwurf_anlegen(db, org.id, user, EntwurfDaten(titel="Objekt", art="einzel", erzeugung="manuell",
            seed=1, szenario_id=b09.id, ort_modus="objekt", objekt_id=objekt.id))
        incident = starten(db, run, user)
        assert db.query(ObjektEinsatz).filter_by(incident_id=incident.id, objekt_id=objekt.id).one()
    finally:
        db.rollback()
        db.close()


def test_start_zweimal_nicht_moeglich():
    db, org, user = _basis()
    try:
        run = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org)))
        starten(db, run, user)
        with pytest.raises(ZustandsFehler):
            starten(db, run, user)
    finally:
        db.rollback()
        db.close()


def test_org_limit_laufender_runs():
    db, org, user = _basis()
    try:
        db.query(OrgSettings).filter_by(org_id=org.id).one().planspiel_max_laufende_runs = 1
        first = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org, "a")))
        starten(db, first, user)
        second = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org, "b")))
        with pytest.raises(ZustandsFehler):
            starten(db, second, user)
    finally:
        db.rollback()
        db.close()


def test_gsl_start_noch_nicht_verfuegbar():
    db, org, user = _basis()
    try:
        b09 = _b09(db)
        run = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org), art="gsl", szenario_id=b09.id))
        with pytest.raises(ZustandsFehler, match="Phase E"):
            starten(db, run, user)
    finally:
        db.rollback()
        db.close()


def test_entwurf_verwerfen():
    db, org, user = _basis()
    try:
        run = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org)))
        entwurf_verwerfen(db, run, user)
        assert run.status == "abgebrochen" and run.beendet_at is not None
    finally:
        db.rollback()
        db.close()


def test_start_broadcast_ohne_alarmfreigabe(monkeypatch):
    import asyncio

    from app.services import planspiel_run_service

    gesendet = []

    async def fake_broadcast(org_id, event):
        gesendet.append((org_id, event))

    monkeypatch.setattr(planspiel_run_service, "broadcast_org", fake_broadcast)
    db, org, user = _basis()
    try:
        run = entwurf_anlegen(db, org.id, user, _daten(_pool(db, org)))
        incident = starten(db, run, user)
        asyncio.run(planspiel_run_service.nach_start_benachrichtigen(run, incident))
    finally:
        db.rollback()
        db.close()
    erstellt = next(event for _, event in gesendet if event["type"] == "incident_created")
    assert erstellt["alarm_erlaubt"] is False
    assert erstellt["is_exercise"] is True
    assert erstellt["title"].startswith("[ÜBUNG]")
    assert any(event["type"] == "planspiel_changed" for _, event in gesendet)
