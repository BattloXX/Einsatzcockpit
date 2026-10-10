"""Versand, Kopieren und Status des Gruppenkommandanten-Zugangs (ohne Klartext-Token in der DB)."""

import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import String, inspect, text

from app.models.major_incident import (
    LageEinheitZugang,
    LageEinheitZugangSession,
    LageEinheitZugangVersand,
)
from app.models.sms import SmsLog
from app.services import gk_zugang_service as service
from tests.test_gk_zugang_service import _daten, _session  # noqa: F401  (fresh_db-Fixture unten)


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


class _Ergebnis:
    def __init__(self, success=True):
        self.success = success
        self.provider = "gateway"


@pytest.fixture
def sms(monkeypatch):
    """SMS-Anbieter verfuegbar, Versand protokolliert den gesendeten Text."""
    gesendet = []
    modus = {"ergebnis": "ok"}

    async def fake_send(org_id, to, text_, timeout=15.0, **kwargs):
        gesendet.append((org_id, to, text_))
        if modus["ergebnis"] == "fehler":
            return _Ergebnis(False)
        if modus["ergebnis"] == "exception":
            raise RuntimeError("Gateway kaputt gkz_geheim")
        if modus["ergebnis"] == "timeout":
            raise TimeoutError
        return _Ergebnis(True)

    monkeypatch.setattr(service, "send_sms", fake_send)
    monkeypatch.setattr(service, "sms_available", lambda org_id, db=None: True)
    monkeypatch.setattr(service, "darf_extern", lambda *a, **k: True)
    fake_send.modus = modus  # type: ignore[attr-defined]
    fake_send.gesendet = gesendet  # type: ignore[attr-defined]
    return fake_send


def _token(link):
    return link.rsplit("#", 1)[1]


def _senden(db, lage, einheit, **kwargs):
    kwargs.setdefault("user_id", None)
    kwargs.setdefault("ausloeser", "manuell")
    return asyncio.run(service.sende_zugangs_sms(db, lage, einheit, **kwargs))


def _klartext_scan(db, token):
    """Kein String-Feld einer Zugangs-/SMS-/Audit-/Journal-Tabelle darf den Token enthalten."""
    gefunden = []
    for tabelle in (
        "lage_einheit_zugang", "lage_einheit_zugang_session", "lage_einheit_zugang_versand",
        "sms_log", "sms_log_recipient", "audit_log", "lage_journal_entry",
    ):
        spalten = [
            c["name"] for c in inspect(db.get_bind()).get_columns(tabelle)
            if isinstance(c["type"], String) or "TEXT" in str(c["type"]).upper() or "CHAR" in str(c["type"]).upper()
        ]
        for spalte in spalten:
            rows = db.execute(text(f"select {spalte} from {tabelle} where {spalte} like :p"), {"p": f"%{token}%"}).all()
            if rows:
                gefunden.append((tabelle, spalte))
    return gefunden


def test_sms_erfolgreich_rotiert_und_protokolliert_ohne_token(sms, caplog):
    caplog.set_level(logging.DEBUG)
    with _session() as db:
        org, lage, einheit, leader = _daten(db)
        db.commit()
        ergebnis = _senden(db, lage, einheit)
        assert ergebnis.status == "gesendet"
        assert len(sms.gesendet) == 1
        _, ziel, nachricht = sms.gesendet[0]
        assert ziel == "+436641234567"
        token = _token(nachricht.split()[-1] if "#gkz_" in nachricht.split()[-1] else
                       next(w for w in nachricht.split() if "#gkz_" in w))
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id, typ="personal").one()
        assert service.token_pruefen(db, token).zustand == "ok"
        assert zugang.token_hash != token
        versand = db.query(LageEinheitZugangVersand).filter_by(zugang_id=zugang.id, kanal="sms").one()
        assert versand.status == "gesendet" and versand.segmente and versand.zeichen
        assert versand.ziel_maske.endswith("4567") and "123" not in versand.ziel_maske
        log = db.query(SmsLog).filter_by(org_id=org.id, source="gk_zugang").one()
        assert "gkz_" not in log.text and "/gk#" in log.text
        assert _klartext_scan(db, token) == []
        assert token not in caplog.text


@pytest.mark.parametrize("modus,status", [("fehler", "fehlgeschlagen"), ("exception", "fehlgeschlagen"), ("timeout", "unklar")])
def test_providerfehler_laesst_zuweisung_und_zugang_unberuehrt(sms, modus, status):
    sms.modus["ergebnis"] = modus
    with _session() as db:
        _, lage, einheit, leader = _daten(db)
        db.commit()
        ergebnis = _senden(db, lage, einheit)
        assert ergebnis.status == status
        assert "gkz_" not in (ergebnis.fehler or "")
        assert einheit.leader_assignment_id == leader.id
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id, typ="personal").one()
        erster_hash = zugang.token_hash
        assert zugang.status == "aktiv" and erster_hash
        sms.modus["ergebnis"] = "ok"
        assert _senden(db, lage, einheit).status == "gesendet"
        db.refresh(zugang)
        assert zugang.token_hash != erster_hash
        assert service.token_pruefen(db, _token(
            next(w for w in sms.gesendet[0][2].split() if "#gkz_" in w))).zustand == "beendet"


def test_ohne_anbieter_oder_in_uebung_keine_rotation(monkeypatch, sms):
    with _session() as db:
        _, lage, einheit, _ = _daten(db)
        db.commit()
        alt = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
        db.commit()
        monkeypatch.setattr(service, "sms_available", lambda *a, **k: False)
        assert _senden(db, lage, einheit).status == "uebersprungen"
        monkeypatch.setattr(service, "sms_available", lambda *a, **k: True)
        monkeypatch.setattr(service, "darf_extern", lambda *a, **k: False)
        assert _senden(db, lage, einheit).status == "uebersprungen"
        assert sms.gesendet == []
        assert service.token_pruefen(db, _token(alt.link)).zustand == "ok"


def test_uebersprungen_ohne_bestehenden_zugang_legt_zeile_ohne_token_an(monkeypatch, sms):
    monkeypatch.setattr(service, "sms_available", lambda *a, **k: False)
    with _session() as db:
        _, lage, einheit, _ = _daten(db)
        db.commit()
        _senden(db, lage, einheit)
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id, typ="personal").one()
        assert zugang.token_hash is None and zugang.status == "kein_token"


def test_voraussetzungen_werden_geprueft(sms):
    with _session() as db:
        org, lage, einheit, leader = _daten(db)
        leader.phone_e164 = None
        db.commit()
        with pytest.raises(ValueError):
            _senden(db, lage, einheit)
        leader.phone_e164 = "+436641234567"
        from app.models.master import OrgSettings
        db.query(OrgSettings).filter_by(org_id=org.id).one().gk_zugang_aktiv = False
        db.commit()
        with pytest.raises(ValueError):
            _senden(db, lage, einheit)
        assert sms.gesendet == []


def test_aktive_sitzung_braucht_bestaetigung(sms):
    with _session() as db:
        _, lage, einheit, _ = _daten(db)
        neu = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        cookie, _ = service.sitzung_anlegen(db, zugang, user_agent="UA", ip="1.2.3.4", verifiziert=True)
        db.commit()
        with pytest.raises(service.SitzungAktiv):
            _senden(db, lage, einheit)
        with pytest.raises(service.SitzungAktiv):
            service.kopie_ausstellen(db, lage, einheit, user_id=None, modus="link")
        assert service.sitzung_pruefen(db, cookie) is not None
        assert _senden(db, lage, einheit, bestaetigt=True).status == "gesendet"
        assert service.sitzung_pruefen(db, cookie) is None


def test_bestehender_link_verhindert_zweite_rotation(sms):
    with _session() as db:
        _, lage, einheit, _ = _daten(db)
        db.commit()
        kopie = service.kopie_ausstellen(db, lage, einheit, user_id=None, modus="nachricht")
        link = next(w for w in kopie.text.split() if "#gkz_" in w)
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id, typ="personal").one()
        generation = zugang.generation
        zweite = service.kopie_ausstellen(db, lage, einheit, user_id=None, modus="link", bestehender_link=link)
        assert zweite.link == link == zweite.text and zweite.generation == generation
        assert _senden(db, lage, einheit, bestehender_link=link).status == "gesendet"
        db.refresh(zugang)
        assert zugang.generation == generation
        assert link in sms.gesendet[0][2]
        # falscher Link -> Rotation
        _senden(db, lage, einheit, bestehender_link=link[:-3] + "xyz")
        db.refresh(zugang)
        assert zugang.generation == generation + 1


def test_kopie_modi_und_versandzeilen(sms):
    with _session() as db:
        _, lage, einheit, _ = _daten(db)
        db.commit()
        nachricht = service.kopie_ausstellen(db, lage, einheit, user_id=None, modus="nachricht", bestaetigt=True)
        assert nachricht.link in nachricht.text and len(nachricht.text) > len(nachricht.link)
        nur_link = service.kopie_ausstellen(db, lage, einheit, user_id=None, modus="link", bestaetigt=True)
        assert nur_link.text == nur_link.link
        kanaele = {
            v.kanal for v in db.query(LageEinheitZugangVersand).filter_by(einheit_id=einheit.id).all()
        }
        assert {"kopie_nachricht", "kopie_link"} <= kanaele
        with pytest.raises(ValueError):
            service.kopie_ausstellen(db, lage, einheit, user_id=None, modus="x")
        assert _klartext_scan(db, _token(nachricht.link)) == []
        assert _klartext_scan(db, _token(nur_link.link)) == []


def test_status_zustaende(sms):
    with _session() as db:
        org, lage, einheit, leader = _daten(db)
        db.commit()
        assert service.zugang_status(db, einheit)["zustand"] == "kein_zugang"
        neu = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
        db.commit()
        assert service.zugang_status(db, einheit)["zustand"] == "aktiv"
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        zugang.laeuft_ab_at = service._now() + timedelta(hours=1)
        db.commit()
        assert service.zugang_status(db, einheit)["zustand"] == "laeuft_bald_ab"
        zugang.laeuft_ab_at = service._now() - timedelta(minutes=1)
        db.commit()
        assert service.zugang_status(db, einheit)["zustand"] == "abgelaufen"
        service.widerrufe(db, einheit.id, grund="manuell")
        db.commit()
        status = service.zugang_status(db, einheit)
        assert status["zustand"] == "widerrufen" and status["widerruf_grund"] == "manuell"
        leader.phone_e164 = None
        db.commit()
        assert service.zugang_status(db, einheit)["zustand"] == "keine_nummer"
        from app.models.master import OrgSettings
        db.query(OrgSettings).filter_by(org_id=org.id).one().gk_zugang_aktiv = False
        db.commit()
        assert service.zugang_status(db, einheit)["zustand"] == "deaktiviert"


def test_status_listet_sitzungen_und_versandprotokoll(sms):
    with _session() as db:
        _, lage, einheit, _ = _daten(db)
        db.commit()
        _senden(db, lage, einheit)
        zugang = db.query(LageEinheitZugang).filter_by(einheit_id=einheit.id, typ="personal").one()
        service.sitzung_anlegen(db, zugang, user_agent="Android Chrome", ip="10.0.0.9", verifiziert=True)
        db.commit()
        status = service.zugang_status(db, einheit)
        assert len(status["aktive_sitzungen"]) == 1
        assert status["letzter_versand"]["kanal"] == "sms"
        assert len(status["versandprotokoll"]) >= 1
        sitzung = db.query(LageEinheitZugangSession).filter_by(zugang_id=zugang.id).one()
        assert sitzung.ip_gruppe == "10.0.0.0/24"


def test_haengende_versaende_werden_aufgeraeumt(sms):
    with _session() as db:
        _, lage, einheit, _ = _daten(db)
        db.commit()
        neu = service.stelle_zugang_aus(db, lage, einheit, user_id=None, grund="test")
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        alt = LageEinheitZugangVersand(
            org_id=zugang.org_id, zugang_id=zugang.id, einheit_id=einheit.id, generation=1, kanal="sms",
            ausloeser="auto", status="geplant", created_at=service._now() - timedelta(minutes=5),
        )
        neu_v = LageEinheitZugangVersand(
            org_id=zugang.org_id, zugang_id=zugang.id, einheit_id=einheit.id, generation=1, kanal="sms",
            ausloeser="auto", status="geplant", created_at=service._now(),
        )
        db.add_all([alt, neu_v])
        db.commit()
        assert service.aufraeumen_haengende_versaende(db) >= 1
        db.refresh(alt)
        db.refresh(neu_v)
        assert alt.status == "fehlgeschlagen" and neu_v.status == "geplant"
