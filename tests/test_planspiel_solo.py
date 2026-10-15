"""Phase F1: Allein-Betrieb mit simulierten Einheiten (Abnahmekriterium 7)."""
from __future__ import annotations

from datetime import timedelta

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import IncidentSite, LageEinheit, LageEinheitZugang, MajorIncident, SitePhase
from app.models.planspiel import PlanspielMitglied, PlanspielRun
from app.models.user import Role, User, UserRole
from app.services import resource_service as rs
from app.services.einheit_service import kontext_fuer_einheit, setze_einheit_status
from app.services.planspiel_engine import beenden, tick
from tests.test_planspiel_assistent_ui import _login, _setup_user
from tests.test_planspiel_gsl_ui import _gsl_starten


def _rolle_dazu(user_id: int, code: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(UserRole(user_id=user_id, role_id=db.query(Role).filter_by(code=code).one().id))
        db.commit()
    finally:
        db.close()


def _erste_einheit(lage_id: int) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.query(LageEinheit).filter_by(lage_id=lage_id).order_by(LageEinheit.id).first().id
    finally:
        db.close()


def test_einsatzleiter_als_mitglied_darf_einheit_simulieren(client):
    user = _setup_user("uebungsleiter")
    _rolle_dazu(user.id, "incident_leader")
    _login(client, user)
    run = _gsl_starten(client, user, titel="Solo erlaubt")
    seite = client.get(f"/einheit?sim={_erste_einheit(run.lage_id)}")
    assert seite.status_code == 200
    assert "SIMULATION" in seite.text
    assert f"/planspiele/{run.id}" in seite.text


def test_nur_uebungsleiter_ohne_einsatzrechte_darf_nicht_simulieren(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _gsl_starten(client, user, titel="Solo ohne Rechte")
    assert client.get(f"/einheit?sim={_erste_einheit(run.lage_id)}").status_code == 403
    cockpit = client.get(f"/planspiele/{run.id}")
    assert "zusätzlich die Rolle" in cockpit.text


def test_einsatzleiter_ohne_mitgliedschaft_darf_nicht_simulieren(client):
    leiter = _setup_user("uebungsleiter")
    _login(client, leiter)
    run = _gsl_starten(client, leiter, titel="Solo fremd")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fremd = User(username=f"el-{run.id}-x", display_name="EL", password_hash=db.get(User, leiter.id).password_hash,
                     org_id=leiter.org_id, active=True)
        db.add(fremd)
        db.flush()
        db.add(UserRole(user_id=fremd.id, role_id=db.query(Role).filter_by(code="incident_leader").one().id))
        db.commit()
        db.refresh(fremd)
        db.expunge(fremd)
    finally:
        db.close()
    _login(client, fremd)
    assert client.get(f"/einheit?sim={_erste_einheit(run.lage_id)}").status_code == 403


def test_simulierte_einheit_anlegen(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _gsl_starten(client, user, titel="Solo Einheit")
    antwort = client.post(f"/planspiele/{run.id}/einheit", data={"_csrf": client.cookies.get("ec_csrf"),
                                                                  "label": "Nachbarwehr LF"}, follow_redirects=False)
    assert antwort.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(LageEinheit).filter_by(lage_id=run.lage_id, label="SIM Nachbarwehr LF").count() == 1
    finally:
        db.close()


def test_solo_durchlauf_phasenautomatik_reagiert(client):
    """Abnahmekriterium 7: ein Login, Uebungs-GSL, simulierte Einheit erkundet und arbeitet ab."""
    user = _setup_user("uebungsleiter")
    _rolle_dazu(user.id, "incident_leader")
    _login(client, user)
    run = _gsl_starten(client, user, titel="Solo Durchlauf")
    tick(SessionLocal, run.id, user.org_id, run.gestartet_at + timedelta(seconds=1))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        nutzer = db.get(User, user.id)
        lage = db.get(MajorIncident, run.lage_id)
        site = db.query(IncidentSite).filter_by(major_incident_id=lage.id).order_by(IncidentSite.id).first()
        einheit = db.query(LageEinheit).filter_by(lage_id=lage.id).order_by(LageEinheit.id).first()
        assert site.phase == SitePhase.eingegangen

        erkundung = rs.dispatch_to_site(db, einheit.id, lage.id, site.id, auftragsart="erkundung")
        db.flush()
        assert site.phase == SitePhase.erkundung
        ctx = kontext_fuer_einheit(db, nutzer, einheit.id, quelle="simulation")
        for status in ("bestaetigt", "anfahrt", "vor_ort", "abgeschlossen"):
            setze_einheit_status(db, ctx, erkundung, status, user_id=nutzer.id, author_name="Solo")
        db.flush()
        assert site.phase == SitePhase.bewertet

        abarbeitung = rs.dispatch_to_site(db, einheit.id, lage.id, site.id, auftragsart="abarbeitung")
        db.flush()
        assert site.phase == SitePhase.disponiert
        for status in ("bestaetigt", "anfahrt", "vor_ort", "in_arbeit"):
            setze_einheit_status(db, ctx, abarbeitung, status, user_id=nutzer.id, author_name="Solo")
        db.flush()
        assert site.phase == SitePhase.in_arbeit
        setze_einheit_status(db, ctx, abarbeitung, "abgeschlossen", user_id=nutzer.id, author_name="Solo")
        db.flush()
        assert site.phase == SitePhase.fertig_gemeldet
        db.commit()
    finally:
        db.close()


def test_beenden_widerruft_zugaenge_und_oeffentlichen_link(client):
    user = _setup_user("uebungsleiter")
    _login(client, user)
    run = _gsl_starten(client, user, titel="Solo Ende")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, run.lage_id)
        lage.public_token = f"tok-{run.id}"
        einheit = db.query(LageEinheit).filter_by(lage_id=lage.id).first()
        db.add(LageEinheitZugang(org_id=lage.org_id, lage_id=lage.id, einheit_id=einheit.id, typ="qr",
                                 token_hash=f"hash-{run.id}"))
        db.commit()
        laufend = db.get(PlanspielRun, run.id)
        beenden(db, laufend, db.get(User, user.id), run.gestartet_at + timedelta(minutes=5))
        db.commit()
        db.expire_all()
        assert db.get(MajorIncident, run.lage_id).public_token is None
        zugaenge = db.query(LageEinheitZugang).filter_by(lage_id=lage.id).all()
        assert zugaenge and all(z.widerrufen_at is not None for z in zugaenge)
        assert db.query(PlanspielMitglied).filter_by(run_id=run.id).count() >= 1
    finally:
        db.close()
