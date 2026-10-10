"""Tablet-Push bei Disposition, Auftragsänderung und Rückzug."""

import pytest

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import EinheitSiteDispatch, LageEinheit
from app.models.master import VehicleMaster
from app.models.user import DeviceToken, User
from app.services import exercise_guard, push_service
from tests.test_gk_auftrag_sms import _aufbau, _post
from tests.test_ressourcenkarte_routes import _login


@pytest.fixture
def pushes(monkeypatch):
    aufrufe = []

    def notify(db, vehicle_id, title, body, url=None):
        aufrufe.append((vehicle_id, title, body, url))
        return 1

    monkeypatch.setattr(push_service, "notify_vehicle", notify)
    return aufrufe


def _tablet(a, *, profil="einheit", widerrufen=False):
    from datetime import UTC, datetime
    from uuid import uuid4

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fahrzeug = VehicleMaster(dept_id=a.org, code="TLF", name="TLF")
        db.add(fahrzeug)
        db.flush()
        db.query(LageEinheit).filter_by(id=a.einheit).one().vehicle_id = fahrzeug.id
        user = db.query(User).filter_by(org_id=a.org).first()
        db.add(DeviceToken(
            label="Tablet", token_hash=hash_api_key(uuid4().hex), user_id=user.id, vehicle_master_id=fahrzeug.id,
            gsl_profil=profil, revoked_at=datetime.now(UTC).replace(tzinfo=None) if widerrufen else None,
        ))
        db.commit()
        return fahrzeug.id
    finally:
        db.close()


def test_push_bei_neu_aenderung_und_rueckzug(client, setup_db, pushes):
    a = _aufbau(schalter=False)
    fahrzeug_id = _tablet(a)
    _login(client, a.username)
    basis = f"/lage/{a.lage}/stellen/{a.site}"
    _post(client, basis + "/einheit-disponieren", einheit_id=a.einheit, auftrag="Keller auspumpen")
    assert [p[1] for p in pushes] == ["Neuer Einsatzauftrag"]
    assert pushes[0][0] == fahrzeug_id and "Riedweg 4" in pushes[0][2] and pushes[0][3].startswith("/einheit/auftrag/")
    db = SessionLocal()
    set_tenant_context(db, None)
    dispatch_id = db.query(EinheitSiteDispatch).filter_by(einheit_id=a.einheit).one().id
    db.close()
    _post(client, f"{basis}/einheit/{dispatch_id}/auftrag", auftrag="Keller auspumpen")
    assert len(pushes) == 1
    _post(client, f"{basis}/einheit/{dispatch_id}/auftrag", auftrag="Garage auspumpen")
    assert [p[1] for p in pushes] == ["Neuer Einsatzauftrag", "Auftrag geändert"]
    _post(client, basis + "/einheit-abziehen", einheit_id=a.einheit)
    assert pushes[-1][1] == "Auftrag zurückgezogen" and len(pushes) == 3


@pytest.mark.parametrize("variante", ["ohne_tablet", "falsches_profil", "widerrufen"])
def test_kein_push_ohne_passendes_tablet(client, setup_db, pushes, variante):
    a = _aufbau(schalter=False)
    if variante == "falsches_profil":
        _tablet(a, profil="fuehrung")
    elif variante == "widerrufen":
        _tablet(a, widerrufen=True)
    _login(client, a.username)
    antwort = _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="X")
    assert antwort.status_code < 400 and pushes == []


def test_uebung_unterdrueckt_push(client, setup_db, pushes, monkeypatch):
    monkeypatch.setattr(exercise_guard, "darf_extern", lambda *args, **kwargs: False)
    a = _aufbau(schalter=False, uebung=True)
    _tablet(a)
    _login(client, a.username)
    _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="X")
    assert pushes == []


def test_pushfehler_blockiert_disposition_nicht(client, setup_db, monkeypatch):
    def kaputt(*args, **kwargs):
        raise RuntimeError("FCM weg")

    monkeypatch.setattr(push_service, "notify_vehicle", kaputt)
    a = _aufbau(schalter=False)
    _tablet(a)
    _login(client, a.username)
    antwort = _post(client, f"/lage/{a.lage}/stellen/{a.site}/einheit-disponieren", einheit_id=a.einheit, auftrag="X")
    assert antwort.status_code < 400
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(EinheitSiteDispatch).filter_by(einheit_id=a.einheit).count() == 1
    finally:
        db.close()
