"""QR-Routen des Zugang-Tabs: keine Geheimnisse im Grundrendering."""

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheitZugang
from app.models.master import OrgSettings
from tests.test_ressourcenkarte_routes import _login
from tests.test_ressourcenkarte_zugang_routes import _basis, _daten


def _activate_qr(org_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter_by(org_id=org_id).one().gk_qr_aktiv = True
        db.commit()
    finally:
        db.close()


def test_qr_ausstellen_idempotent_vorschau_und_widerruf(client, setup_db):
    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    path = _basis(lage_id, einheit_id)
    first = client.post(path + "/zugang/qr/ausstellen", data={"_csrf": csrf})
    assert first.status_code == 200 and "QR-Zugang" in first.text and "gkq_" not in first.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="qr").one()
        generation = row.generation
    finally:
        db.close()
    assert client.post(path + "/zugang/qr/ausstellen", data={"_csrf": csrf}).status_code == 200
    preview = client.get(path + "/zugang/qr/vorschau")
    assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
    assert "data:image/png;base64," in preview.text and "gkq_" not in preview.text
    assert client.post(path + "/zugang/qr/widerrufen", data={"_csrf": csrf}).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="qr").one().status == "widerrufen"
        assert generation == 1
    finally:
        db.close()


@pytest.mark.parametrize("role", ["readonly"])
def test_qr_routes_require_zugangsverwaltung(client, setup_db, role):
    username, lage_id, einheit_id, org_id = _daten(role)
    _activate_qr(org_id)
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    assert client.post(_basis(lage_id, einheit_id) + "/zugang/qr/ausstellen", data={"_csrf": csrf}).status_code == 403


def _drucker(org_id, *, fremd_org_id=None):
    from app.core.security import hash_api_key
    from app.models.gateway import Gateway, Printer

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gw = Gateway(org_id=fremd_org_id or org_id, name="GW", device_token_hash=hash_api_key("tok-" + str(org_id)))
        db.add(gw)
        db.flush()
        printer = Printer(
            org_id=fremd_org_id or org_id, gateway_id=gw.id, name="Drucker", uri="ipp://x/print", aktiv=True,
            defaults={"media": "A4"},
        )
        db.add(printer)
        db.commit()
        return printer.id
    finally:
        db.close()


def test_qr_drucken_legt_je_klick_einen_job_ohne_rotation_an(client, setup_db, monkeypatch):
    from app.models.gateway import DOC_GSL_EINHEIT_QR, PrintJob
    from app.services import print_dispatcher

    async def kein_versand(db, job):
        return {}

    monkeypatch.setattr(print_dispatcher, "dispatch_job", kein_versand)
    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    printer_id = _drucker(org_id)
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    path = _basis(lage_id, einheit_id)
    for _ in range(2):
        response = client.post(path + "/zugang/qr/drucken", data={"_csrf": csrf, "printer_id": printer_id})
        assert response.status_code == 200, response.text
        assert "gkq_" not in response.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="qr").one()
        jobs = db.query(PrintJob).filter_by(gsl_id=lage_id, document_type=DOC_GSL_EINHEIT_QR).all()
        assert zugang.generation == 1 and zugang.qr_druck_job_id == max(j.id for j in jobs)
        assert len(jobs) == 2 and {j.artifact_ref for j in jobs} == {f"{einheit_id}:1"}
        assert all(j.org_id == org_id and j.options in (None, {}) and not j.error for j in jobs)
    finally:
        db.close()


def test_qr_drucken_fremder_drucker_und_nicht_aktivierte_org_werden_abgewiesen(client, setup_db):
    username, lage_id, einheit_id, org_id = _daten()
    fremd = _daten()
    fremder_drucker = _drucker(org_id, fremd_org_id=fremd[3])
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    path = _basis(lage_id, einheit_id)
    ohne_schalter = client.post(path + "/zugang/qr/drucken", data={"_csrf": csrf, "printer_id": fremder_drucker})
    assert ohne_schalter.status_code == 422
    _activate_qr(org_id)
    fremd_druck = client.post(path + "/zugang/qr/drucken", data={"_csrf": csrf, "printer_id": fremder_drucker})
    assert fremd_druck.status_code == 422
