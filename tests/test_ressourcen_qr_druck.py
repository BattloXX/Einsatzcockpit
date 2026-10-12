"""Druckwege des QR-Zugangs in der GSL-Ressourcenkarte."""

import json

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import Printer, PrintJob
from app.models.major_incident import LageEinheitZugang
from app.models.user import AuditLog
from app.services import print_dispatcher
from tests.test_ressourcenkarte_qr_routes import _activate_qr, _drucker
from tests.test_ressourcenkarte_routes import _login
from tests.test_ressourcenkarte_zugang_routes import _basis, _daten


async def _kein_versand(db, job):
    return {"status": "queued"}


def _qr_ausstellen(client, path, csrf):
    response = client.post(path + "/zugang/qr/ausstellen", data={"_csrf": csrf})
    assert response.status_code == 200


def test_karte_verwendet_standard_druckdialog(client, setup_db):
    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    _login(client, username)
    path = _basis(lage_id, einheit_id)
    _qr_ausstellen(client, path, client.cookies.get("ec_csrf"))

    html = client.get(path + "/karte/zugang").text
    assert "ecpgPrint(" in html
    assert "gsl_einheit_qr" in html
    assert "stationUrl" in html
    assert "/zugang/qr/vorschau?druck=1" in html
    assert '<select name="printer_id"' not in html
    board = client.get(f"/lage/{lage_id}").text
    assert "stationUrl" in board and "onStationDone" in board
    # Die Ressourcenkarte lebt auf der Kräfteübersicht – dort muss der Dialog genau einmal stehen.
    ressourcen = client.get(f"/lage/{lage_id}/ressourcen").text
    assert ressourcen.count('id="ecpgPrintDialog"') == 1


def test_stationsdruck_json_optionen_und_html_fallback(client, setup_db, monkeypatch):
    monkeypatch.setattr(print_dispatcher, "dispatch_job", _kein_versand)
    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    printer_id = _drucker(org_id)
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    path = _basis(lage_id, einheit_id)

    response = client.post(
        path + "/zugang/qr/drucken",
        data={"_csrf": csrf, "printer_id": printer_id, "copies": 2, "duplex": "long-edge", "media": "A4"},
        headers={"X-ECPG": "1"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        job = db.query(PrintJob).filter_by(gsl_id=lage_id).one()
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="qr").one()
        audit = db.query(AuditLog).filter_by(action="gsl.zugang.qr_gedruckt").order_by(AuditLog.id.desc()).first()
        assert job.options["copies"] == 2 and job.options["duplex"] == "long-edge"
        assert zugang.qr_druck_job_id == job.id
        assert json.loads(audit.payload_json)["ziel"] == "station"
    finally:
        db.close()

    fallback = client.post(path + "/zugang/qr/drucken", data={"_csrf": csrf, "printer_id": printer_id})
    assert fallback.status_code == 200
    assert fallback.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("copies", [0, 21])
def test_stationsdruck_ungueltige_kopien_abgelehnt(client, setup_db, copies):
    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    _login(client, username)
    response = client.post(
        _basis(lage_id, einheit_id) + "/zugang/qr/drucken",
        data={"_csrf": client.cookies.get("ec_csrf"), "printer_id": _drucker(org_id), "copies": copies},
        headers={"X-ECPG": "1"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("inactive", [False, True])
def test_stationsdruck_fremder_oder_inaktiver_drucker_liefert_json_fehler(client, setup_db, inactive):
    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    _login(client, username)
    if inactive:
        printer_id = _drucker(org_id)
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            db.get(Printer, printer_id).aktiv = False
            db.commit()
        finally:
            db.close()
    else:
        fremde_org = _daten()[3]
        printer_id = _drucker(org_id, fremd_org_id=fremde_org)
    response = client.post(
        _basis(lage_id, einheit_id) + "/zugang/qr/drucken",
        data={"_csrf": client.cookies.get("ec_csrf"), "printer_id": printer_id},
        headers={"X-ECPG": "1"},
    )
    assert response.status_code == 422
    assert response.json()["ok"] is False
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(PrintJob).filter_by(gsl_id=lage_id).count() == 0
    finally:
        db.close()


def test_lokaler_druck_auditiert_nur_druckseite(client, setup_db):
    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    _login(client, username)
    path = _basis(lage_id, einheit_id)
    _qr_ausstellen(client, path, client.cookies.get("ec_csrf"))
    assert "window.print" not in client.get(path + "/zugang/qr/vorschau").text
    printed = client.get(path + "/zugang/qr/vorschau?druck=1")
    assert printed.status_code == 200 and "window.print" in printed.text
    # Gleiches Blatt wie beim Stationsdruck; die PIN wird separat übergeben und steht nie darauf.
    assert "Kurzanleitung" in printed.text and "PIN:" not in printed.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="qr").one()
        audit = db.query(AuditLog).filter_by(action="gsl.zugang.qr_gedruckt").order_by(AuditLog.id.desc()).first()
        assert zugang.qr_druck_at is not None
        assert json.loads(audit.payload_json)["ziel"] == "lokal"
    finally:
        db.close()


def test_lokaler_druck_zeigt_keine_pin(client, setup_db):
    from app.services import gk_zugang_service

    username, lage_id, einheit_id, org_id = _daten()
    _activate_qr(org_id)
    _login(client, username)
    path = _basis(lage_id, einheit_id)
    _qr_ausstellen(client, path, client.cookies.get("ec_csrf"))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit_id, typ="qr").one()
        zugang.pin_pflicht = True
        db.commit()
        pin = gk_zugang_service.qr_pin_fuer_fuehrung(zugang)
    finally:
        db.close()
    assert pin
    printed = client.get(path + "/zugang/qr/vorschau?druck=1").text
    assert pin not in printed and "PIN erhalten Sie von der Einsatzleitung" in printed


def test_druck_und_vorschau_benoetigen_zugangsverwaltung(client, setup_db):
    username, lage_id, einheit_id, org_id = _daten("readonly")
    _activate_qr(org_id)
    _login(client, username)
    path = _basis(lage_id, einheit_id)
    assert client.get(path + "/zugang/qr/vorschau?druck=1").status_code == 403
    assert client.post(path + "/zugang/qr/drucken", headers={"X-ECPG": "1"}).status_code == 403
