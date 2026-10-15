"""GSL-Planspiele: Ausfuehrung von Stellen-/Lage-Folgemeldungen, GSL-Trigger,
sowie verschaerfte Pruefungen fuer Start (keine Alarmierung) und Generator."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.models.major_incident import (
    CommLogEntry,
    CrossSiteMarker,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    SiteLogEntry,
    SitePhase,
)
from app.models.planspiel import PlanspielAdresse, PlanspielRunEreignis, PlanspielSzenario, PlanspielZiel
from app.models.road_closure import RoadClosure
from app.services.planspiel_engine import freie_meldung, tick
from tests.conftest import TestingSession
from tests.test_planspiel_gsl import _basis, _run


def _ereignis(db, run, *, ziel_typ, ziel_id, trigger="elapsed_time", due=0.0, param=None, baustein=None,
              text="Testmeldung"):
    seq = 5000 + db.query(PlanspielRunEreignis).filter_by(run_id=run.id).count()
    event = PlanspielRunEreignis(
        org_id=run.org_id, run_id=run.id, seq=seq, quelle="szenario", baustein_code=baustein,
        ziel_typ=ziel_typ, ziel_id=ziel_id, trigger_type=trigger,
        trigger_param={"ereignis_typ": "eskalation", **(param or {})}, faellig_sim_s=due, status="geplant",
        idempotenz_key=f"test:{run.id}:{seq}", sichtbarkeit="teilnehmer", text=text,
    )
    db.add(event)
    db.commit()
    return event


def _erste_stelle(db, run, org):
    ergebnis = tick(TestingSession, run.id, org.id, run.gestartet_at)
    assert ergebnis.generiert
    return db.get(IncidentSite, ergebnis.generiert[0].site_id)


def test_start_ohne_gsl_alarmierung_und_mit_einheiten(monkeypatch):
    def verboten(*_args, **_kwargs):
        raise AssertionError("GSL-Alarmierung darf im Planspiel nicht laufen")

    monkeypatch.setattr("app.services.gsl_notify.notify_gsl_created", verboten)
    monkeypatch.setattr("app.services.gsl_live_notify.notify_gsl_live", verboten)
    monkeypatch.setattr("app.services.sms_dispatch_service.dispatch_gsl_alarm", verboten)
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        assert db.query(LageEinheit).filter_by(lage_id=run.lage_id).count() == 1
        lage_events = db.query(PlanspielRunEreignis).filter_by(run_id=run.id, ziel_typ="lage").all()
        assert lage_events and all(event.ziel_id == run.lage_id for event in lage_events)
    finally:
        db.close()


def test_stellen_folgemeldung_landet_im_funkjournal_und_der_stellenchronik():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        site = _erste_stelle(db, run, org)
        _ereignis(db, run, ziel_typ="site", ziel_id=site.id, text="Wasser steigt im Keller")
        ergebnis = tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=5))
        assert any(item.site_id == site.id for item in ergebnis.ausgefuehrt)
        db.expire_all()
        funk = db.query(CommLogEntry).filter_by(major_incident_id=run.lage_id, related_site_id=site.id).all()
        assert any("Wasser steigt im Keller" in entry.message for entry in funk)
        chronik = db.query(SiteLogEntry).filter_by(incident_site_id=site.id).all()
        assert any("Wasser steigt im Keller" in entry.text for entry in chronik)
    finally:
        db.close()


def test_lagesperre_ist_uebergreifende_meldung_keine_strassensperre():
    db, org, user = _basis()
    try:
        db.add(PlanspielAdresse(org_id=org.id, quelle="strassenpunkt", quell_id="way/77", strasse="Dammweg",
                                lat=47.471, lng=9.751, qualitaet="C", ort_typen=["strasse"], aktiv=True))
        db.commit()
        sperren_vorher = db.query(RoadClosure).filter_by(org_id=org.id).count()
        run = _run(db, org, user)
        _ereignis(db, run, ziel_typ="lage", ziel_id=run.lage_id, baustein="LAG-SPERRE",
                  text="Dammweg nicht passierbar")
        ergebnis = tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=1))
        marker_ids = [item.marker_id for item in ergebnis.ausgefuehrt if item.marker_id]
        assert len(marker_ids) == 1
        db.expire_all()
        marker = db.get(CrossSiteMarker, marker_ids[0])
        assert marker.marker_type == "strasse_gesperrt"
        assert marker.source == "simulation"
        assert marker.strasse == "Dammweg"
        assert db.query(RoadClosure).filter_by(org_id=org.id).count() == sperren_vorher
    finally:
        db.close()


def test_phasentrigger_der_stelle():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        site = _erste_stelle(db, run, org)
        event = _ereignis(db, run, ziel_typ="site", ziel_id=site.id, trigger="phase_reached", due=None,
                          param={"phase": "erkundung"}, text="Person am Fenster")
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=10))
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "geplant"
        site.phase = SitePhase.erkundung
        db.commit()
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=20))
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "ausgefuehrt"
    finally:
        db.close()


def test_auftragstrigger_der_stelle():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        site = _erste_stelle(db, run, org)
        einheit = db.query(LageEinheit).filter_by(lage_id=run.lage_id).first()
        event = _ereignis(db, run, ziel_typ="site", ziel_id=site.id, trigger="assignment_completed", due=None,
                          param={"auftragsart": "erkundung"}, text="Erkundung abgeschlossen")
        dispatch = EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, auftragsart="erkundung",
                                       einheit_status="anfahrt",
                                       dispatched_at=datetime.now(UTC).replace(tzinfo=None))
        db.add(dispatch)
        db.commit()
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=10))
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "geplant"
        dispatch = db.get(EinheitSiteDispatch, dispatch.id)
        dispatch.einheit_status = "abgeschlossen"
        db.commit()
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=20))
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "ausgefuehrt"
    finally:
        db.close()


def test_rueckfallzeit_zaehlt_ab_erzeugung_der_stelle():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        site = _erste_stelle(db, run, org)
        ziel = db.query(PlanspielZiel).filter_by(run_id=run.id, site_id=site.id).one()
        ziel.erzeugt_sim_s = 600.0
        db.commit()
        event = _ereignis(db, run, ziel_typ="site", ziel_id=site.id, trigger="phase_reached", due=None,
                          param={"phase": "in_arbeit"})
        # sim 700: absolut > 480, aber erst 100 s nach Erzeugung der Stelle -> noch nicht faellig
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=700))
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status == "geplant"
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=1090))
        db.expire_all()
        assert db.get(PlanspielRunEreignis, event.id).status in {"ausgefuehrt", "uebersprungen"}
    finally:
        db.close()


def test_freie_meldung_bei_gsl_geht_an_die_lage():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        jetzt = run.gestartet_at + timedelta(seconds=3)
        event = freie_meldung(db, run, user, jetzt, text="Feuerwehrhaus meldet Stromausfall")
        db.commit()
        assert (event.ziel_typ, event.ziel_id) == ("lage", run.lage_id)
        tick(TestingSession, run.id, org.id, jetzt)
        db.expire_all()
        funk = db.query(CommLogEntry).filter_by(major_incident_id=run.lage_id).all()
        assert any("Stromausfall" in entry.message for entry in funk)
    finally:
        db.close()


def _codefolge(anzahl: int) -> list[str]:
    db, org, user = _basis()
    try:
        run = _run(db, org, user, mittel_s=15, max_stellen=anzahl, max_laufzeit_min=600)
        sekunde = 0
        while db.query(PlanspielZiel).filter_by(run_id=run.id).count() < anzahl and sekunde < 36000:
            tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=sekunde))
            sekunde += 10
        ziele = db.query(PlanspielZiel).filter_by(run_id=run.id).order_by(PlanspielZiel.seq).all()
        return [ziel.szenario_code for ziel in ziele]
    finally:
        db.close()


def test_generator_ist_deterministisch_und_folgt_der_schwere():
    erste = _codefolge(40)
    zweite = _codefolge(40)
    assert len(erste) == 40
    assert erste == zweite
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        stichworte = {
            szenario.code: szenario
            for szenario in db.query(PlanspielSzenario).filter(PlanspielSzenario.code.in_(set(erste))).all()
        }
        assert stichworte
    finally:
        db.close()
    from app.data.planspiel_katalog import GSL_WELLEN, SZENARIEN

    erlaubt = {code for welle in GSL_WELLEN["hochwasser"] for code in welle.stellen_codes}
    assert set(erste) <= erlaubt
    stw = {s.code: s.stichwort for s in SZENARIEN}
    zaehler = Counter(stw[code] for code in erste)
    assert zaehler["T1"] > zaehler["T2"] >= zaehler.get("T3", 0)
