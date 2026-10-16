"""Gezielte Tests fuer den GSL-Planspielgenerator."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import IncidentSite, MajorIncident
from app.models.master import AlarmType, FireDept, OrgSettings, VehicleMaster
from app.models.planspiel import PlanspielAdresse, PlanspielRun, PlanspielSzenario, PlanspielZiel
from app.models.user import User
from app.services.planspiel_engine import generator_stoppen, tick, welle_jetzt
from app.services.planspiel_katalog_service import katalog_importieren, neue_version, system_inhalt
from app.services.planspiel_run_service import EntwurfDaten, entwurf_anlegen, starten
from tests.conftest import TestingSession


def _basis():
    db = TestingSession()
    set_tenant_context(db, None)
    org = FireDept(slug=f"gsl-{uuid4().hex}", name="GSL Test", fallback_lat=47.47, fallback_lng=9.75)
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, planspiel_gebiet_radius_m=5000, planspiel_max_stellen_je_minute=100))
    db.add_all(AlarmType(org_id=org.id, code=code, category="T", label=code) for code in ("T1", "T2", "T3", "T9"))
    db.add(VehicleMaster(dept_id=org.id, code="LF", name="LF", active=True))
    katalog_importieren(db, org.id)
    for index, typ in enumerate(("wohngebaeude", "strasse", "gewaessernaehe", "hang", "tiefgarage")):
        db.add(PlanspielAdresse(org_id=org.id, quelle="manuell", quell_id=str(index), strasse="Hauptstraße",
                                hausnr=str(index + 1), ort="Ort", lat=47.47 + index / 10000, lng=9.75,
                                qualitaet="A", ort_typen=[typ, "gebaeude", "beliebig_geeignet"], aktiv=True))
    user = User(username=f"leiter-{uuid4().hex}", display_name="Leiter", org_id=org.id)
    db.add(user)
    db.flush()
    set_tenant_context(db, org.id)
    return db, org, user


def _run(db, org, user, **values):
    data = {"titel": "Unwetter", "art": "gsl", "erzeugung": "manuell", "seed": 42,
            "szenario_id": db.query(PlanspielSzenario).filter_by(code="G01").one().id}
    data.update(values)
    run = entwurf_anlegen(db, org.id, user, EntwurfDaten(**data))
    starten(db, run, user)
    db.commit()
    return run


def _stellen(db, org, user, modus, seed=12345, groups=6):
    run = _run(db, org, user, gsl_modus=modus, seed=seed, mittel_s=15, max_stellen=60,
               max_laufzeit_min=600)
    run.konfig = {**run.konfig, "duplikat_p": 0.0}
    db.commit()
    for group in range(groups):
        db.refresh(run)
        jetzt = run.gestartet_at + timedelta(seconds=group * 1000)
        welle_jetzt(db, run, user, jetzt, 10)
        db.commit()
        tick(TestingSession, run.id, org.id, jetzt)
        tick(TestingSession, run.id, org.id, jetzt)
    db.expire_all()
    return run, db.query(PlanspielZiel).filter_by(run_id=run.id).order_by(PlanspielZiel.seq).all()


def test_gsl_entwurf_nur_mit_gsl_szenario():
    db, org, user = _basis()
    try:
        with pytest.raises(ValueError, match="GSL-Szenario"):
            entwurf_anlegen(db, org.id, user, EntwurfDaten(
                "x", "gsl", "manuell", szenario_id=db.query(PlanspielSzenario).filter_by(code="B09").one().id,
            ))
        run = _run(db, org, user)
        assert run.szenario_snapshot["gsl_modus"] == "hochwasser"
    finally:
        db.close()


def test_gsl_start_legt_uebungslage_an():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        lage = db.get(MajorIncident, run.lage_id)
        assert lage and lage.is_exercise and lage.planspiel_run_id == run.id and lage.name == "ÜBUNG – Unwetter"
    finally:
        db.close()


def test_generator_legt_stellen_mit_quelle_simulation_an():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        jetzt = run.gestartet_at
        result = tick(TestingSession, run.id, org.id, jetzt)
        assert len(result.generiert) == 1
        site = db.get(IncidentSite, result.generiert[0].site_id)
        assert site.source == "simulation" and site.alarm_stufe
        assert db.query(PlanspielZiel).filter_by(run_id=run.id, seq=1).one()
    finally:
        db.close()


def test_generator_verteilung_und_determinismus():
    db, org, user = _basis()
    try:
        run = _run(db, org, user, mittel_s=20, max_stellen=300, max_laufzeit_min=600)
        for second in range(0, 7200, 10):
            tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(seconds=second))
        assert 1 <= db.query(PlanspielZiel).filter_by(run_id=run.id).count() <= 300
    finally:
        db.close()


def test_generator_stoppt_bei_obergrenze():
    db, org, user = _basis()
    try:
        run = _run(db, org, user, max_stellen=1)
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(minutes=2))
        db.expire_all()
        assert db.get(PlanspielRun, run.id).status == "generierung_gestoppt"
    finally:
        db.close()


def test_generator_stoppen_vorhandene_stellen_bleiben():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        generator_stoppen(db, run, user)
        db.commit()
        assert db.query(IncidentSite).filter_by(major_incident_id=run.lage_id).count() == 1
    finally:
        db.close()


def test_welle_jetzt_erzeugt_mehrere_stellen():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        welle_jetzt(db, run, user, run.gestartet_at, 3)
        db.commit()
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        assert db.query(PlanspielZiel).filter_by(run_id=run.id).count() >= 3
    finally:
        db.close()


def test_ratenlimit_je_minute():
    db, org, user = _basis()
    try:
        db.query(OrgSettings).filter_by(org_id=org.id).one().planspiel_max_stellen_je_minute = 2
        run = _run(db, org, user)
        welle_jetzt(db, run, user, run.gestartet_at, 5)
        db.commit()
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        assert db.query(PlanspielZiel).filter_by(run_id=run.id).count() == 2
    finally:
        db.close()


def test_nachholregel_nach_ausfall():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        tick(TestingSession, run.id, org.id, run.gestartet_at + timedelta(minutes=5))
        assert db.query(PlanspielZiel).filter_by(run_id=run.id).count() == 0
    finally:
        db.close()


def test_parallele_generatoren_keine_doppelte_stelle():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        assert db.query(PlanspielZiel).filter_by(run_id=run.id, seq=1).count() == 1
    finally:
        db.close()


def test_pause_stoppt_generator():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        run.status = "pausiert"
        db.commit()
        assert not tick(TestingSession, run.id, org.id, datetime.now(UTC).replace(tzinfo=None)).generiert
    finally:
        db.close()


def test_doppelmeldung_verweist_auf_frueheres_ziel():
    db, org, user = _basis()
    try:
        run = _run(db, org, user, mittel_s=15)
        run.konfig = {**run.konfig, "duplikat_p": 1.0, "burst_rest": 1}
        db.commit()
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        duplicates = db.query(PlanspielZiel).filter(
            PlanspielZiel.run_id == run.id, PlanspielZiel.ist_duplikat_von.is_not(None),
        ).all()
        assert duplicates
        for duplicate in duplicates:
            original = db.get(PlanspielZiel, duplicate.ist_duplikat_von)
            assert original and duplicate.szenario_code == original.szenario_code
    finally:
        db.close()


def test_keine_externen_kanaele_bei_gsl():
    db, org, user = _basis()
    try:
        run = _run(db, org, user)
        assert run.lage_id is not None
    finally:
        db.close()


def test_folgemeldungen_je_stelle_mit_eigenem_zufall():
    """Jede generierte Stelle wuerfelt ihre Folgemeldungen mit eigenem Seed."""
    from app.services.planspiel_run_service import zeitplan_fuer

    inhalt = {"ereignisse": [{
        "baustein_code": "X", "ereignis_typ": "eskalation", "trigger_type": "elapsed_time",
        "offset_min_s": 0, "offset_max_s": 3600, "trigger_param": None, "wahrscheinlichkeit": 0.5,
        "sichtbarkeit": "teilnehmer", "ziel": "stelle", "payload_template": "Text", "max_fires": 1,
    }]}
    plaene = {
        (plan[0].status, plan[0].faellig_sim_s)
        for plan in (zeitplan_fuer(inhalt, {}, f"7:stelle:{seq}") for seq in range(1, 30))
    }
    assert len(plaene) > 10


@pytest.mark.parametrize("modus", ("hochwasser", "sturm", "kombination"))
def test_gsl_verteilung_ist_breit_und_ohne_dreifachfolge(modus):
    db, org, user = _basis()
    try:
        _, targets = _stellen(db, org, user, modus)
        codes = [target.szenario_code for target in targets]
        assert len(codes) == 60
        assert len(set(codes)) >= 6
        assert len({target.stichwort_code for target in targets}) >= 2
        assert all(codes[index:index + 3] != [code] * 3 for index, code in enumerate(codes[:-2]))
        assert max(Counter(codes).values()) <= 21
    finally:
        db.close()


def test_unveraenderte_systemvorlage_darf_alarmtext_variante_nutzen():
    db, org, user = _basis()
    try:
        scenario = db.query(PlanspielSzenario).filter_by(org_id=org.id, code="T01").one()
        content = system_inhalt("T01")
        content["alarmtext_template"] = "Gepflegter Alarmtext. {adresse}."
        neue_version(db, scenario, content, user.id)
        db.commit()
        run = _run(db, org, user, max_stellen=1)
        tick(TestingSession, run.id, org.id, run.gestartet_at)
        target = db.query(PlanspielZiel).filter_by(run_id=run.id).one()
        site = db.get(IncidentSite, target.site_id)
        assert site.einsatzgrund.startswith("Gepflegter Alarmtext.")
    finally:
        db.close()


def test_gleicher_seed_liefert_gleiche_code_abfolge_in_zwei_organisationen():
    first_db, first_org, first_user = _basis()
    second_db = None
    try:
        _, first = _stellen(first_db, first_org, first_user, "kombination", seed=991, groups=1)
        first_codes = [target.szenario_code for target in first]
        first_db.close()
        second_db, second_org, second_user = _basis()
        _, second = _stellen(second_db, second_org, second_user, "kombination", seed=991, groups=1)
        assert first_codes == [target.szenario_code for target in second]
    finally:
        if first_db.is_active:
            first_db.close()
        if second_db is not None:
            second_db.close()
