"""Auswertung, PDF und Wiederholung von Planspielen."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Message, Task
from app.models.major_incident import IncidentSite, SitePhase
from app.models.planspiel import PlanspielMitglied, PlanspielRun, PlanspielSzenario, PlanspielZiel
from app.models.user import AuditLog, User
from app.services.major_incident_service import setze_site_phase
from app.services.planspiel_auswertung_service import auswertung
from app.services.planspiel_engine import tick
from app.services.stats_service import get_stats
from tests.conftest import TestingSession
from tests.test_planspiel_assistent_ui import _data, _login, _setup_user
from tests.test_planspiel_gsl import _basis, _run


def _einzel_starten(client, user, titel: str = "Auswertung Einzel") -> PlanspielRun:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        scenario = db.query(PlanspielSzenario).filter_by(org_id=user.org_id, code="B09").one()
        from app.models.planspiel import PlanspielAdresse
        address_id = db.query(PlanspielAdresse).filter_by(org_id=user.org_id).one().id
    finally:
        db.close()
    answer = client.post("/planspiele/neu", data=_data(
        client, titel=titel, szenario_id=str(scenario.id), adresse_id=str(address_id),
    ), follow_redirects=False)
    assert answer.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        run = db.query(PlanspielRun).filter_by(org_id=user.org_id, titel=titel).one()
        run_id = run.id
    finally:
        db.close()
    answer = client.post(
        f"/planspiele/{run_id}/start",
        data={"_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        run = db.get(PlanspielRun, run_id)
        db.expunge(run)
        return run
    finally:
        db.close()


def test_auswertung_gsl_zeiten_aus_phasenwechseln():
    db, org, user = _basis()
    try:
        run = _run(db, org, user, tempo=3)
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        site = db.query(IncidentSite).filter_by(major_incident_id=run.lage_id).one()
        run.gestartet_at = datetime(2026, 1, 1, 9, 59)
        run.beendet_at = datetime(2026, 1, 1, 11, 0)
        site.created_at = datetime(2026, 1, 1, 10, 0)
        setze_site_phase(db, site, SitePhase.erkundung, user_id=user.id, author_name="Leiter")
        db.flush()
        audit = db.query(AuditLog).filter_by(org_id=org.id, action="major_incident.site.phase_changed").one()
        audit.created_at = datetime(2026, 1, 1, 10, 2)
        db.commit()
        assert auswertung(db, run)["gsl"]["zeiten"][0]["median_min"] == 6
    finally:
        db.close()


def test_auswertung_zaehlt_doppelmeldungen_und_phasen():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        site = db.query(IncidentSite).filter_by(major_incident_id=run.lage_id).one()
        ziel = db.query(PlanspielZiel).filter_by(run_id=run.id).one()
        db.add(PlanspielZiel(org_id=org.id, run_id=run.id, seq=99, typ="site", site_id=site.id,
                             ist_duplikat_von=ziel.id))
        setze_site_phase(db, site, SitePhase.erledigt, user_id=user.id, author_name="Leiter")
        db.commit()
        data = auswertung(db, run)["gsl"]
        assert data["doppelmeldungen"] == data["doppelmeldungen_erledigt"] == 1
        assert data["je_phase"]["erledigt"] == 1
    finally:
        db.close()


def test_auswertung_einzel_meldungen_und_aufgaben(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _einzel_starten(client, user)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add_all([
            Message(incident_id=run.incident_id, title="[Übung] Meldung", is_done=True),
            Task(incident_id=run.incident_id, title="Aufgabe", is_done=True),
        ])
        db.commit()
        data = auswertung(db, db.get(PlanspielRun, run.id))["einzel"]
        assert (
            data["meldungen"], data["meldungen_erledigt"], data["aufgaben"], data["aufgaben_erledigt"]
        ) == (1, 1, 1, 1)
    finally:
        db.close()


def test_auswertung_seite_und_rechte(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _einzel_starten(client, user)
    assert client.get(f"/planspiele/{run.id}/auswertung").status_code == 200
    observer = _setup_user("uebungsbeobachter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        observer_row = db.get(User, observer.id)
        observer_row.org_id = user.org_id
        db.add(PlanspielMitglied(org_id=user.org_id, run_id=run.id, user_id=observer.id, rolle="beobachter"))
        draft = PlanspielRun(org_id=user.org_id, titel="Entwurf", art="einzel", erzeugung="manuell", seed=1)
        db.add(draft)
        db.commit()
        draft_id = draft.id
    finally:
        db.close()
    _login(client, observer)
    assert client.get(f"/planspiele/{run.id}/auswertung").status_code == 200
    assert client.get(f"/planspiele/{draft_id}/auswertung").status_code == 404
    stranger = _setup_user("uebungsleiter")
    _login(client, stranger)
    assert client.get(f"/planspiele/{run.id}/auswertung").status_code == 404


def test_auswertung_pdf(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _einzel_starten(client, user)
    answer = client.get(f"/planspiele/{run.id}/auswertung.pdf")
    assert answer.status_code == 200 and answer.headers["content-type"] == "application/pdf"
    assert answer.content.startswith(b"%PDF")


def test_wiederholen_kopiert_snapshot_und_seed(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _einzel_starten(client, user)
    answer = client.post(
        f"/planspiele/{run.id}/wiederholen",
        data={"_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        repeat = db.query(PlanspielRun).filter(
            PlanspielRun.org_id == user.org_id, PlanspielRun.titel.like("%Wiederholung)%"),
        ).one()
        scenario = db.query(PlanspielSzenario).filter_by(org_id=user.org_id, code="B09").one()
        assert repeat.seed == run.seed and repeat.szenario_snapshot == db.get(PlanspielRun, run.id).szenario_snapshot
        assert scenario.code == "B09" and repeat.titel.endswith("(Wiederholung)")
    finally:
        db.close()


def test_planspiel_einsaetze_nicht_in_einsatzstatistik(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    _einzel_starten(client, user)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        # +1 Tag: zwischen 22 und 24 Uhr UTC ist es in der Org-Zeitzone (Wien) bereits der Folgetag
        stats = get_stats(db, user.org_id, date.today() - timedelta(days=1), date.today() + timedelta(days=1))
        assert stats.total == 0 and stats.total_exercises == 1
    finally:
        db.close()


def test_regie_aktionen_in_auswertung(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _einzel_starten(client, user)
    csrf = client.cookies.get("ec_csrf")
    assert client.post(
        f"/planspiele/{run.id}/pause", data={"_csrf": csrf}, follow_redirects=False,
    ).status_code == 303
    assert client.post(
        f"/planspiele/{run.id}/fortsetzen", data={"_csrf": csrf}, follow_redirects=False,
    ).status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        actions = auswertung(db, db.get(PlanspielRun, run.id))["regie_aktionen"]
        assert {row["aktion"] for row in actions} >= {"planspiel.run.pausiert", "planspiel.run.fortgesetzt"}
    finally:
        db.close()
