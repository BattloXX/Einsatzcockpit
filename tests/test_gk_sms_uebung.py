"""GK-SMS verwenden bei Uebungen ihren eigenen Freigabe-Schalter."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheitZugangVersand
from app.models.master import FireDept, OrgSettings
from app.services import gk_zugang_service as service
from app.services.exercise_guard import darf_extern
from tests.conftest import TestingSession
from tests.test_gk_einstellungen import _anmelden, _org_mit_admin
from tests.test_gk_einstellungen import _daten as settings_daten
from tests.test_gk_zugang_service import _daten, _session


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


@pytest.fixture
def sms(monkeypatch):
    sent = []

    async def send(_org_id, _phone, text, **_kwargs):
        sent.append(text)
        return SimpleNamespace(success=True, provider="test")

    monkeypatch.setattr(service, "send_sms", send)
    monkeypatch.setattr(service, "sms_available", lambda *_args: True)
    monkeypatch.setattr(service, "SessionLocal", TestingSession)
    return sent


def _auftrag(db, lage, einheit):
    site = IncidentSite(
        major_incident_id=lage.id,
        org_id=lage.org_id,
        bezeichnung="Hauptstrasse 1",
    )
    db.add(site)
    db.flush()
    dispatch = EinheitSiteDispatch(
        einheit_id=einheit.id,
        site_id=site.id,
        dispatched_at=datetime.now(),
        auftrag="Keller auspumpen",
    )
    db.add(dispatch)
    db.flush()
    return dispatch


@pytest.mark.parametrize(
    ("gk_sms_uebung", "einsatzinfo_sms_send_exercise", "expected_status"),
    [(False, True, "uebersprungen"), (True, False, "gesendet")],
)
def test_zugangs_sms_uebung_verwendet_nur_gk_schalter(
    sms, gk_sms_uebung, einsatzinfo_sms_send_exercise, expected_status
):
    with _session() as db:
        org, lage, einheit, _leader = _daten(db)
        lage.is_exercise = True
        settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
        settings.gk_sms_uebung = gk_sms_uebung
        settings.einsatzinfo_sms_send_exercise = einsatzinfo_sms_send_exercise
        db.commit()

        result = asyncio.run(service.sende_zugangs_sms(db, lage, einheit, user_id=None, ausloeser="test"))
        versand = db.query(LageEinheitZugangVersand).filter_by(einheit_id=einheit.id).one()

        assert result.status == expected_status
        assert versand.status == expected_status
        if expected_status == "uebersprungen":
            assert result.fehler == "Übung: GK-SMS unterdrückt"
            assert versand.fehler == "Übung: GK-SMS unterdrückt"
            assert sms == []
        else:
            assert len(sms) == 1


@pytest.mark.parametrize(
    ("gk_sms_uebung", "einsatzinfo_sms_send_exercise", "expected_status"),
    [(False, True, "uebersprungen"), (True, False, "gesendet")],
)
def test_auftrags_sms_outbox_uebung_verwendet_nur_gk_schalter(
    sms, gk_sms_uebung, einsatzinfo_sms_send_exercise, expected_status
):
    with _session() as db:
        org, lage, einheit, _leader = _daten(db)
        lage.is_exercise = True
        settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
        settings.gk_auto_sms_auftrag = True
        settings.gk_sms_uebung = gk_sms_uebung
        settings.einsatzinfo_sms_send_exercise = einsatzinfo_sms_send_exercise
        dispatch = _auftrag(db, lage, einheit)
        auftrag = service.plane_auftrag_sms(db, lage, einheit, dispatch, "neu")
        assert auftrag is not None
        db.commit()

        asyncio.run(service.sende_auto_sms(auftrag))
        row = db.query(LageEinheitZugangVersand).filter_by(id=auftrag.versand_id).one()

        assert row.status == expected_status
        if expected_status == "uebersprungen":
            assert row.fehler == "Übung: GK-SMS unterdrückt"
            assert sms == []
        else:
            assert len(sms) == 1


def test_realeinsatz_ignoriert_beide_uebungs_schalter(sms):
    with _session() as db:
        org, lage, einheit, _leader = _daten(db)
        settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
        settings.gk_auto_sms_auftrag = True
        settings.gk_sms_uebung = False
        settings.einsatzinfo_sms_send_exercise = False
        dispatch = _auftrag(db, lage, einheit)
        auftrag = service.plane_auftrag_sms(db, lage, einheit, dispatch, "neu")
        assert auftrag is not None
        db.commit()

        asyncio.run(service.sende_auto_sms(auftrag))
        result = asyncio.run(service.sende_zugangs_sms(db, lage, einheit, user_id=None, ausloeser="test"))
        db.expire_all()
        row = db.query(LageEinheitZugangVersand).filter_by(id=auftrag.versand_id).one()

        assert result.status == "gesendet"
        assert row.status == "gesendet"
        assert len(sms) == 2


def test_exercise_guard_gk_sms_ist_fail_closed_ohne_einstellungen():
    with _session() as db:
        org = FireDept(slug=f"gk-guard-{uuid4().hex[:10]}", name="GK Guard")
        db.add(org)
        db.flush()
        assert darf_extern("gk_sms", is_exercise=True, org_id=org.id, db=db) is False
        settings = OrgSettings(org_id=org.id, gk_sms_uebung=True)
        db.add(settings)
        db.flush()
        assert darf_extern("gk_sms", is_exercise=True, org_id=org.id, db=db) is True
        settings.gk_sms_uebung = False
        assert darf_extern("gk_sms", is_exercise=True, org_id=org.id, db=db) is False
        assert darf_extern("gk_sms", is_exercise=False, org_id=org.id, db=db) is True


def test_gsl_einstellungen_speichern_gk_sms_uebung(client, setup_db):
    org_id, username = _org_mit_admin(uuid4().hex[:10])
    _anmelden(client, username)
    csrf = client.cookies.get("ec_csrf")
    response = client.post("/admin/gsl-einstellungen", data=settings_daten(_csrf=csrf, gk_sms_uebung="1"))
    assert response.status_code == 200
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgSettings).filter_by(org_id=org_id).one().gk_sms_uebung is True
    finally:
        db.close()

    response = client.post("/admin/gsl-einstellungen", data=settings_daten(_csrf=csrf))
    assert response.status_code == 200
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        assert db.query(OrgSettings).filter_by(org_id=org_id).one().gk_sms_uebung is False
    finally:
        db.close()
