"""Outbox-Versand bei automatischer GK-Zuweisung."""

import asyncio

import pytest

from app.models.major_incident import LageEinheitZugang, LageEinheitZugangVersand
from app.models.master import OrgSettings
from app.services import gk_zugang_service as service
from app.services import resource_service
from tests.test_gk_zugang_service import _daten, _session


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


@pytest.fixture
def sms(monkeypatch):
    sent = []

    class Result:
        success = True
        provider = "test"

    async def send(_org, _phone, text, **_kwargs):
        sent.append(text)
        return Result()

    monkeypatch.setattr(service, "send_sms", send)
    monkeypatch.setattr(service, "sms_available", lambda *_args: True)
    monkeypatch.setattr(service, "darf_extern", lambda *_args, **_kwargs: True)
    return sent


def test_auto_sms_plant_und_versendet_nur_einmal(monkeypatch, sms):
    # The background worker deliberately owns a new session; point it at the
    # isolated test database instead of the application database.
    from tests.conftest import TestingSession

    monkeypatch.setattr(service, "SessionLocal", TestingSession)
    with _session() as db:
        org, lage, einheit, leader = _daten(db)
        settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
        settings.gk_zugang_auto_sms = True
        auftrag = service.plane_auto_sms(db, lage, einheit, leader, "neu")
        assert auftrag is not None and "gkz_" not in repr(auftrag)
        assert service.plane_auto_sms(db, lage, einheit, leader, "neu") is None
        db.commit()
        asyncio.run(service.sende_auto_sms(auftrag))
        versand = db.query(LageEinheitZugangVersand).filter_by(id=auftrag.versand_id).one()
        assert versand.ausloeser == "auto" and versand.status == "gesendet"
        assert len(sms) == 1 and "gkz_" in sms[0]


def test_veralteter_auto_auftrag_wird_verworfen(monkeypatch, sms):
    from tests.conftest import TestingSession

    monkeypatch.setattr(service, "SessionLocal", TestingSession)
    with _session() as db:
        org, lage, einheit, leader = _daten(db)
        db.query(OrgSettings).filter_by(org_id=org.id).one().gk_zugang_auto_sms = True
        auftrag = service.plane_auto_sms(db, lage, einheit, leader, "neu")
        assert auftrag
        leader.phone_version += 1
        db.commit()
        asyncio.run(service.sende_auto_sms(auftrag))
        row = db.query(LageEinheitZugangVersand).filter_by(id=auftrag.versand_id).one()
        assert row.status == "verworfen" and not sms
        assert db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id).one().token_hash


def test_setzen_mit_auto_aus_erzeugt_keinen_token():
    with _session() as db:
        org, lage, einheit, _leader = _daten(db)
        result = resource_service.setze_gruppenkommandant(
            db, lage, einheit, person_name="Andere Führung", telefon="+436651112233"
        )
        assert result.aenderung == "wechsel" and result.auto_sms is None
        assert not db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id).count()
