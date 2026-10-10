"""Tests für die reproduzierbare GSL-Zugangstoken-Ableitung."""

from __future__ import annotations

from app.core.security import hash_api_key
from app.models.major_incident import LageEinheitZugang
from app.services import gk_zugang_service as service
from tests.test_gk_zugang_service import _ausgestellt, _session


def test_ableitung_ist_deterministisch_und_typ_und_generation_getrennt(monkeypatch):
    monkeypatch.setattr(service.settings, "GSL_ZUGANG_KEY", "test-gsl-key")
    first = service.token_ableiten("personal", 12, 3)
    assert first == service.token_ableiten("personal", 12, 3)
    assert first.startswith("gkz_") and len(first) == 47
    assert first != service.token_ableiten("personal", 12, 4)
    assert first != service.token_ableiten("qr", 12, 3)
    assert service.token_ableiten("qr", 12, 3).startswith("gkq_")


def test_ausstellung_speichert_hash_und_token_laesst_sich_neu_berechnen(monkeypatch):
    monkeypatch.setattr(service.settings, "GSL_ZUGANG_KEY", "test-gsl-key")
    with _session() as db:
        _, _, _, _, neu = _ausgestellt(db)
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        token = neu.link.rsplit("#", 1)[1]
        assert zugang.token_hash == hash_api_key(token)
        assert service.token_neu_berechnen(zugang) == token


def test_legacy_zufallstoken_bleibt_gueltig_und_qr_prefix_ist_fuer_personal_ungueltig(monkeypatch):
    monkeypatch.setattr(service.settings, "GSL_ZUGANG_KEY", "test-gsl-key")
    with _session() as db:
        _, _, _, _, neu = _ausgestellt(db)
        zugang = db.get(LageEinheitZugang, neu.zugang_id)
        legacy = "gkz_alter_zufallstoken"
        zugang.token_hash = hash_api_key(legacy)
        db.flush()
        assert service.token_pruefen(db, legacy).zustand == "ok"
        assert service.token_pruefen(db, "gkq_" + "A" * 43).zustand == "unbekannt"


def test_schluesselwechsel_macht_ableitbaren_token_ungueltig(monkeypatch):
    monkeypatch.setattr(service.settings, "GSL_ZUGANG_KEY", "erster-test-key")
    with _session() as db:
        _, _, _, _, neu = _ausgestellt(db)
        token = neu.link.rsplit("#", 1)[1]
        assert service.token_pruefen(db, token).zustand == "ok"
        monkeypatch.setattr(service.settings, "GSL_ZUGANG_KEY", "anderer-test-key")
        assert service.token_pruefen(db, token).zustand == "unbekannt"
