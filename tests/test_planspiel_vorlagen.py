"""Integrationstests fuer wiederverwendbare Planspiel-Vorlagen."""

from app.core.tenant import _TENANT_TABLE_NAMES, set_tenant_context
from app.db import SessionLocal
from app.models.planspiel import PlanspielRun, PlanspielVorlage
from tests.test_planspiel_assistent_ui import _data, _login, _setup_user


def _run_id(db, org_id: int) -> int:
    return db.query(PlanspielRun).filter(PlanspielRun.org_id == org_id).order_by(PlanspielRun.id.desc()).one().id


def test_vorlage_speichern_und_als_entwurf_anlegen(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        from app.models.planspiel import PlanspielAdresse

        adresse = db.query(PlanspielAdresse).filter(PlanspielAdresse.org_id == user.org_id).one()
    finally:
        db.close()
    assert client.post(
        "/planspiele/neu", data=_data(client, adresse_id=str(adresse.id)), follow_redirects=False
    ).status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        run = db.get(PlanspielRun, _run_id(db, user.org_id))
        seed = run.seed
    finally:
        db.close()
    answer = client.post(
        f"/planspiele/{run.id}/als-vorlage",
        data={"_csrf": client.cookies.get("ec_csrf"), "titel": "Meine Vorlage", "gleicher_ablauf": "on"},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vorlage = db.query(PlanspielVorlage).filter(PlanspielVorlage.org_id == user.org_id).one()
        assert "seed" not in vorlage.konfig
        assert vorlage.seed_fixiert == seed
    finally:
        db.close()
    answer = client.post(
        f"/planspiele/vorlagen/{vorlage.id}/anlegen", data={"_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        neu = db.query(PlanspielRun).filter(PlanspielRun.org_id == user.org_id).order_by(PlanspielRun.id.desc()).first()
        assert neu.status == "entwurf" and neu.titel == "Meine Vorlage" and neu.seed == seed
    finally:
        db.close()


def test_vorlagen_tenant_und_rolle(client):
    leitung = _setup_user("uebungsleiter")
    beobachter = _setup_user("uebungsbeobachter")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(PlanspielVorlage(org_id=leitung.org_id, titel="Nur A", art="einzel", konfig={"erzeugung": "manuell"}))
        db.commit()
        vorlage = db.query(PlanspielVorlage).filter(PlanspielVorlage.org_id == leitung.org_id).one()
    finally:
        db.close()
    _login(client, beobachter)
    assert client.get("/planspiele/vorlagen").status_code == 403
    _login(client, leitung)
    assert client.get("/planspiele/vorlagen").status_code == 200
    assert client.post(
        f"/planspiele/vorlagen/{vorlage.id}/loeschen", data={"_csrf": client.cookies.get("ec_csrf")},
        follow_redirects=False,
    ).status_code == 303


def test_planspiel_vorlage_ist_tenant_tabelle():
    assert "planspiel_vorlage" in _TENANT_TABLE_NAMES
