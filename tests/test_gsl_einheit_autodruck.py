"""Druckregel gsl_einheit_angelegt: genau ein A4-QR-Auftrag je neuer Einheit."""

import asyncio
from uuid import uuid4

import pytest

from app.core.security import hash_api_key
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.gateway import (
    DOC_GSL_EINHEIT_QR,
    TRIGGER_GSL_EINHEIT_ANGELEGT,
    Gateway,
    Printer,
    PrintJob,
    PrintRule,
)
from app.models.major_incident import LageEinheit, LageEinheitZugang, MajorIncident
from app.models.master import OrgSettings, SystemSettings
from app.services import print_dispatcher
from tests.test_ressourcenkarte_routes import _login
from tests.test_ressourcenkarte_zugang_routes import _daten


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _aufbau(org_id, *, regel=True, qr=True, filters=None, uebung=False, lage_id=None):
    db = _db()
    try:
        if not db.query(SystemSettings).filter_by(key="gateway_module_enabled").first():
            db.add(SystemSettings(key="gateway_module_enabled", value="true"))
        else:
            db.query(SystemSettings).filter_by(key="gateway_module_enabled").one().value = "true"
        settings = db.query(OrgSettings).filter_by(org_id=org_id).one()
        settings.gateway_module_enabled, settings.gk_qr_aktiv = True, qr
        gw = Gateway(org_id=org_id, name="GW", device_token_hash=hash_api_key("tok-" + uuid4().hex))
        db.add(gw)
        db.flush()
        printer = Printer(org_id=org_id, gateway_id=gw.id, name="P", uri="ipp://x/p", aktiv=True, defaults={})
        db.add(printer)
        db.flush()
        if regel:
            db.add(PrintRule(
                org_id=org_id, name="QR bei Neuanlage " + uuid4().hex[:6], aktiv=True,
                trigger=TRIGGER_GSL_EINHEIT_ANGELEGT, documents=[DOC_GSL_EINHEIT_QR],
                printer_ids=[printer.id], filters=filters or {},
            ))
        if uebung and lage_id:
            db.get(MajorIncident, lage_id).is_exercise = True
        db.commit()
        return printer.id
    finally:
        db.close()


@pytest.fixture
def kein_versand(monkeypatch):
    gesendet = []

    async def dispatch(db, job):
        gesendet.append(job.id)

    monkeypatch.setattr(print_dispatcher, "dispatch_job", dispatch)
    return gesendet


def _anlegen(client, lage_id, label):
    return client.post(
        f"/lage/{lage_id}/einheiten",
        data={"_csrf": client.cookies.get("ec_csrf"), "resource_type": "extern", "label": label, "org_name": "X"},
    )


def _jobs(lage_id):
    db = _db()
    try:
        return db.query(PrintJob).filter_by(gsl_id=lage_id, document_type=DOC_GSL_EINHEIT_QR).all()
    finally:
        db.close()


def _einheit_id(lage_id, label):
    db = _db()
    try:
        return db.query(LageEinheit).filter_by(lage_id=lage_id, label=label).one().id
    finally:
        db.close()


def test_aktive_regel_erzeugt_genau_einen_job_und_zwei_einheiten_getrennte(client, setup_db, kein_versand):
    username, lage_id, _, org_id = _daten()
    _aufbau(org_id)
    _login(client, username)
    assert _anlegen(client, lage_id, "Alpha").status_code == 204
    assert _anlegen(client, lage_id, "Beta").status_code == 204
    jobs = _jobs(lage_id)
    refs = {j.artifact_ref for j in jobs}
    assert len(jobs) == 2 and refs == {f"{_einheit_id(lage_id, n)}:1" for n in ("Alpha", "Beta")}
    assert all(j.org_id == org_id and j.source == "rule" and not j.error for j in jobs)
    assert len(kein_versand) == 2
    db = _db()
    try:
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=_einheit_id(lage_id, "Alpha"), typ="qr").one()
        assert zugang.qr_druck_job_id is not None and zugang.qr_druck_at is not None
    finally:
        db.close()


@pytest.mark.parametrize("variante", ["ohne_regel", "qr_aus"])
def test_ohne_regel_oder_ohne_qr_schalter_kein_job_aber_einheit_bleibt(client, setup_db, kein_versand, variante):
    username, lage_id, _, org_id = _daten()
    _aufbau(org_id, regel=variante != "ohne_regel", qr=variante != "qr_aus")
    _login(client, username)
    assert _anlegen(client, lage_id, "Gamma").status_code == 204
    assert _jobs(lage_id) == [] and kein_versand == []
    _einheit_id(lage_id, "Gamma")


@pytest.mark.parametrize(
    "filter_, uebung, erwartet",
    [({"uebung": "nur_echt"}, True, 0), ({"uebung": "nur_uebung"}, True, 1), ({"uebung": "nur_uebung"}, False, 0)],
)
def test_echt_uebungsfilter(client, setup_db, kein_versand, filter_, uebung, erwartet):
    username, lage_id, _, org_id = _daten()
    _aufbau(org_id, filters=filter_, uebung=uebung, lage_id=lage_id)
    _login(client, username)
    assert _anlegen(client, lage_id, "Delta").status_code == 204
    assert len(_jobs(lage_id)) == erwartet


def test_druckfehler_rollt_einheit_nicht_zurueck(client, setup_db, monkeypatch):
    async def kaputt(db, job):
        raise RuntimeError("Gateway weg")

    monkeypatch.setattr(print_dispatcher, "dispatch_job", kaputt)
    username, lage_id, _, org_id = _daten()
    _aufbau(org_id)
    _login(client, username)
    assert _anlegen(client, lage_id, "Epsilon").status_code == 204
    _einheit_id(lage_id, "Epsilon")
    assert len(_jobs(lage_id)) == 1


def test_wiederholter_hook_aufruf_erzeugt_keinen_zweiten_job(client, setup_db, kein_versand):
    username, lage_id, _, org_id = _daten()
    _aufbau(org_id)
    _login(client, username)
    assert _anlegen(client, lage_id, "Zeta").status_code == 204
    einheit_id = _einheit_id(lage_id, "Zeta")
    for _ in range(2):
        asyncio.run(print_dispatcher.autoprint_gsl_einheit_background(einheit_id))
    assert len(_jobs(lage_id)) == 1


def test_regel_einer_fremden_org_feuert_nicht(client, setup_db, kein_versand):
    username, lage_id, _, org_id = _daten()
    fremd = _daten()
    _aufbau(org_id, regel=False)
    _aufbau(fremd[3])
    _login(client, username)
    assert _anlegen(client, lage_id, "Eta").status_code == 204
    assert _jobs(lage_id) == []


def test_bestehende_trigger_bleiben_unveraendert():
    from app.models.gateway import TRIGGER_DOCUMENT_TYPES, TRIGGER_LABELS

    assert TRIGGER_DOCUMENT_TYPES[TRIGGER_GSL_EINHEIT_ANGELEGT] == frozenset({DOC_GSL_EINHEIT_QR})
    assert "gsl_created" in TRIGGER_LABELS and "einsatz_created" in TRIGGER_LABELS
    assert DOC_GSL_EINHEIT_QR not in TRIGGER_DOCUMENT_TYPES["gsl_created"]
