"""UI-Tests fuer GSL-Planspiele: Assistent, Generator-Steuerung im Cockpit, Board-Badge."""
from __future__ import annotations

from datetime import timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, MajorIncident
from app.models.master import AlarmType, VehicleMaster
from app.models.planspiel import PlanspielRun, PlanspielSzenario, PlanspielZiel
from app.services.planspiel_engine import tick
from tests.test_planspiel_assistent_ui import _data, _login, _setup_user


def _gsl_vorbereiten(org_id: int) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        for code in ("T2", "T3", "T9"):
            db.add(AlarmType(org_id=org_id, code=code, category="T", label=code))
        db.add(VehicleMaster(dept_id=org_id, code="LF", name="LF", active=True))
        db.commit()
        return db.query(PlanspielSzenario).filter_by(org_id=org_id, code="G01").one().id
    finally:
        db.close()


def _run_by_title(org_id: int, titel: str) -> PlanspielRun:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        run = db.query(PlanspielRun).filter_by(org_id=org_id, titel=titel).one()
        db.expunge(run)
        return run
    finally:
        db.close()


def _gsl_starten(client, user, titel="Unwetter UI"):
    g01 = _gsl_vorbereiten(user.org_id)
    antwort = client.post("/planspiele/neu", data=_data(
        client, titel=titel, art="gsl", szenario_id=str(g01), gsl_modus="sturm", mittel_s="30",
        max_stellen="10", max_laufzeit_min="60",
    ), follow_redirects=False)
    assert antwort.status_code == 303, antwort.text[:500]
    run = _run_by_title(user.org_id, titel)
    start = client.post(f"/planspiele/{run.id}/start", data={"_csrf": client.cookies.get("ec_csrf")},
                        follow_redirects=False)
    assert start.status_code == 303
    return _run_by_title(user.org_id, titel)


def test_assistent_legt_gsl_entwurf_mit_generator_optionen_an(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    g01 = _gsl_vorbereiten(user.org_id)
    antwort = client.post("/planspiele/neu", data=_data(
        client, titel="GSL Entwurf", art="gsl", szenario_id=str(g01), gsl_modus="kombination", mittel_s="60",
        max_stellen="25", max_laufzeit_min="90",
    ), follow_redirects=False)
    assert antwort.status_code == 303
    run = _run_by_title(user.org_id, "GSL Entwurf")
    assert run.art == "gsl"
    assert run.szenario_snapshot["gsl_modus"] == "kombination"
    assert (run.konfig["mittel_s"], run.konfig["max_stellen"], run.konfig["max_laufzeit_min"]) == (60, 25, 90)
    detail = client.get(f"/planspiele/{run.id}")
    assert "Großschadenslage" in detail.text


def test_assistent_lehnt_ungueltige_gsl_grenzen_ab(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    g01 = _gsl_vorbereiten(user.org_id)
    antwort = client.post("/planspiele/neu", data=_data(
        client, titel="GSL kaputt", art="gsl", szenario_id=str(g01), mittel_s="5",
    ))
    assert antwort.status_code == 400


def test_cockpit_zeigt_generator_und_gsl_board(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _gsl_starten(client, user)
    seite = client.get(f"/planspiele/{run.id}")
    assert "Unwetter-Generator" in seite.text
    assert f"/lage/{run.lage_id}" in seite.text
    assert "Stichwort ändern" not in seite.text


def test_generator_stoppen_und_fortsetzen_ueber_cockpit(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _gsl_starten(client, user)
    csrf = client.cookies.get("ec_csrf")
    gestoppt = client.post(f"/planspiele/{run.id}/generator", data={"_csrf": csrf, "aktion": "stoppen"},
                           headers={"HX-Request": "true"})
    assert gestoppt.status_code == 200
    assert "Generator fortsetzen" in gestoppt.text
    assert _run_by_title(user.org_id, "Unwetter UI").status == "generierung_gestoppt"
    weiter = client.post(f"/planspiele/{run.id}/generator", data={"_csrf": csrf, "aktion": "fortsetzen"},
                         headers={"HX-Request": "true"})
    assert weiter.status_code == 200
    assert _run_by_title(user.org_id, "Unwetter UI").status == "laeuft"


def test_welle_jetzt_erzeugt_sofort_mehrere_stellen(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _gsl_starten(client, user)
    antwort = client.post(f"/planspiele/{run.id}/welle", data={"_csrf": client.cookies.get("ec_csrf"),
                                                                "anzahl": "3"}, headers={"HX-Request": "true"})
    assert antwort.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(PlanspielZiel).filter_by(run_id=run.id).count() >= 3
    finally:
        db.close()


def test_board_karte_zeigt_planspiel_badge(client):
    # Das GSL-Board verlangt Einsatzleiter-/Admin-Rechte; die reine Rolle uebungsleiter genuegt dort nicht.
    user = _setup_user("admin")
    _login(client, user)
    run = _gsl_starten(client, user)
    tick(SessionLocal, run.id, user.org_id, run.gestartet_at + timedelta(seconds=1))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        site = db.query(IncidentSite).filter_by(major_incident_id=run.lage_id).first()
        lage = db.get(MajorIncident, run.lage_id)
        assert site is not None and lage.is_exercise
        site_id = site.id
    finally:
        db.close()
    karte = client.get(f"/lage/{run.lage_id}/stellen/{site_id}/card")
    assert karte.status_code == 200
    assert "Planspiel" in karte.text


def test_cockpit_zeigt_ereignisse_schon_beim_ersten_laden(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _gsl_starten(client, user, titel="Erstes Laden")
    seite = client.get(f"/planspiele/{run.id}")
    assert "Keine anstehenden Ereignisse." not in seite.text
    assert "Ganze Lage" in seite.text
