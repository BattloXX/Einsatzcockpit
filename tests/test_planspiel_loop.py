"""Worker-Tests fuer Planspiele: Run-Lease, Doppelworker, Neustart, spaete Jobs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from app.core.tenant import set_tenant_context
from app.models.incident import Message
from app.models.master import OrgSettings, SystemSettings
from app.models.planspiel import PlanspielRun, PlanspielRunEreignis
from app.services.planspiel_engine import beenden, tick
from app.services.planspiel_loop import aktive_runs, planspiel_runde, run_beanspruchen
from tests.conftest import TestingSession
from tests.test_planspiel_engine import JETZT, _basis, _close, _event


def _modul_an(db, org_id: int) -> None:
    set_tenant_context(db, None)
    flag = db.get(SystemSettings, "planspiel_module_enabled")
    if flag is None:
        db.add(SystemSettings(key="planspiel_module_enabled", value="true"))
    else:
        flag.value = "true"
    settings = db.query(OrgSettings).filter_by(org_id=org_id).one()
    settings.planspiel_modul_aktiv = True
    db.commit()


def _meldungen(incident_id: int, text: str) -> int:
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        return db.query(Message).filter_by(incident_id=incident_id, detail=text).count()
    finally:
        db.close()


def test_aktive_runs_nur_mit_aktivem_modul():
    db, org, _user, run = _basis()
    try:
        set_tenant_context(db, None)
        assert (run.id, org.id) not in aktive_runs(db)
        _modul_an(db, org.id)
        assert (run.id, org.id) in aktive_runs(db)
    finally:
        _close(db)


def test_zwei_worker_beanspruchen_run_nur_einer():
    db, org, _user, run = _basis()
    run_id, org_id = run.id, org.id
    _close(db)
    barrier = Barrier(2)

    def versuch(worker: str) -> bool:
        session = TestingSession()
        set_tenant_context(session, org_id)
        try:
            barrier.wait()
            return run_beanspruchen(session, run_id, org_id, worker, JETZT)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ergebnisse = list(pool.map(versuch, ["worker-a", "worker-b"]))
    assert sorted(ergebnisse) == [False, True]


def test_abgelaufenes_lease_wird_nach_neustart_uebernommen():
    db, org, _user, run = _basis()
    try:
        assert run_beanspruchen(db, run.id, org.id, "alt", JETZT) is True
        # Derselbe Worker darf verlaengern, ein anderer erst nach Ablauf.
        assert run_beanspruchen(db, run.id, org.id, "alt", JETZT + timedelta(seconds=5)) is True
        assert run_beanspruchen(db, run.id, org.id, "neu", JETZT + timedelta(seconds=10)) is False
        assert run_beanspruchen(db, run.id, org.id, "neu", JETZT + timedelta(seconds=40)) is True
    finally:
        _close(db)


def test_parallele_ticks_fuehren_ereignis_genau_einmal_aus():
    """Auch ohne Run-Lease (z. B. Leader-Lock fail-open) gewinnt nur ein Tick."""
    db, org, _user, run = _basis()
    event = _event(db, run, due=0.0, text="Nur einmal bitte")
    db.commit()
    run_id, org_id, incident_id = run.id, org.id, run.incident_id
    event_id = event.id
    _close(db)
    barrier = Barrier(2)

    def lauf(_n: int) -> int:
        barrier.wait()
        return len(tick(TestingSession, run_id, org_id, JETZT + timedelta(seconds=30)).ausgefuehrt)

    with ThreadPoolExecutor(max_workers=2) as pool:
        ausgefuehrt = sum(pool.map(lauf, range(2)))

    assert _meldungen(incident_id, "Nur einmal bitte") == 1
    assert ausgefuehrt >= 1
    check = TestingSession()
    set_tenant_context(check, None)
    try:
        assert check.get(PlanspielRunEreignis, event_id).status == "ausgefuehrt"
    finally:
        check.close()


def test_runde_fuehrt_aus_und_zweiter_worker_bleibt_aussen_vor():
    db, org, _user, run = _basis()
    _modul_an(db, org.id)
    set_tenant_context(db, org.id)
    _event(db, run, due=0.0, text="Runde A")
    db.commit()
    incident_id = run.incident_id
    _close(db)

    erste = planspiel_runde(TestingSession, "worker-a", JETZT + timedelta(seconds=30))
    zweite = planspiel_runde(TestingSession, "worker-b", JETZT + timedelta(seconds=31))
    assert any(ergebnis.incident_id == incident_id for _org, _run, ergebnis in erste)
    assert not any(ergebnis.incident_id == incident_id for _org, _run, ergebnis in zweite)
    assert _meldungen(incident_id, "Runde A") == 1


def test_beendeter_run_hat_keine_spaeten_jobs():
    db, org, user, run = _basis()
    _modul_an(db, org.id)
    set_tenant_context(db, org.id)
    _event(db, run, due=600.0, text="Viel zu spaet")
    beenden(db, run, user, JETZT + timedelta(seconds=60))
    db.commit()
    run_id, incident_id = run.id, run.incident_id
    _close(db)

    ergebnisse = planspiel_runde(TestingSession, "worker-a", JETZT + timedelta(hours=2))
    assert not any(r == run_id for _org, r, _e in ergebnisse)
    assert _meldungen(incident_id, "Viel zu spaet") == 0
    check = TestingSession()
    set_tenant_context(check, None)
    try:
        assert check.get(PlanspielRun, run_id).worker_lease_until is None
    finally:
        check.close()
