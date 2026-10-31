"""Kurzlink /a/<code> fuer die Einsatzinfo-SMS."""
import hashlib
from types import SimpleNamespace

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.teams_bot import AlarmToken
from app.services.alarm_link import (
    _ALPHABET,
    KURZCODE_LAENGE,
    alarm_link,
    kurzcode_hash,
    neuer_kurzcode,
)
from tests.conftest import TestingSession


def test_kurzcode_format():
    codes = {neuer_kurzcode() for _ in range(200)}
    assert len(codes) == 200
    assert all(len(c) == KURZCODE_LAENGE and set(c) <= set(_ALPHABET) for c in codes)


def test_alarm_link_bevorzugt_kurzcode_und_faellt_zurueck():
    mit = SimpleNamespace(alarm_kurzcode="Abc123Xyz9", alarm_token="alm_lang")
    ohne = SimpleNamespace(alarm_kurzcode=None, alarm_token="alm_lang")
    leer = SimpleNamespace(alarm_kurzcode=None, alarm_token=None)
    assert alarm_link(mit).endswith("/a/Abc123Xyz9")
    assert alarm_link(mit, "/anmelden").endswith("/a/Abc123Xyz9/anmelden")
    assert alarm_link(ohne).endswith("/alarm/alm_lang")
    assert alarm_link(leer) == ""


def test_kurzlink_oeffnet_einsatzinfo(client):
    code = neuer_kurzcode()
    with TestingSession() as db:
        set_tenant_context(db, None)
        inc = Incident(primary_org_id=1, alarm_type_code="T1", status="active", alarm_kurzcode=code)
        db.add(inc)
        db.flush()
        db.add(AlarmToken(incident_id=inc.id, token_hash=hashlib.sha256(b"alm_x").hexdigest(),
                          kurzcode_hash=kurzcode_hash(code)))
        db.commit()
    assert client.get(f"/a/{code}").status_code == 200
    assert client.get(f"/a/{code}/anmelden").status_code in (200, 403)
    assert client.get("/a/NichtVorhanden").status_code == 404


def test_alarm_link_nutzt_kurz_domain_nur_fuer_kurzlink(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "SHORT_BASE_URL", "https://ec.at/")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://einsatz.example.at")
    mit = SimpleNamespace(alarm_kurzcode="Abc123Xyz9", alarm_token="alm_lang")
    ohne = SimpleNamespace(alarm_kurzcode=None, alarm_token="alm_lang")
    assert alarm_link(mit) == "https://ec.at/a/Abc123Xyz9"
    assert alarm_link(mit, "/anmelden") == "https://ec.at/a/Abc123Xyz9/anmelden"
    assert alarm_link(ohne) == "https://einsatz.example.at/alarm/alm_lang"
    monkeypatch.setattr(settings, "SHORT_BASE_URL", "")
    assert alarm_link(mit) == "https://einsatz.example.at/a/Abc123Xyz9"
