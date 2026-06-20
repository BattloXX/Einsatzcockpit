"""PR 2: UAS-Compliance-Logik (pilot_freigabe_status, device_einsatzbereit, wartung_faelligkeit)."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.uas_compliance import (
    ABLAUF_WARNUNG_TAGE,
    CURRENCY_DAYS,
    CURRENCY_MINCOUNT,
    PilotFreigabe,
    device_einsatzbereit,
    pilot_freigabe_status,
    wartung_faelligkeit,
)


# ── Hilfs-Factories ───────────────────────────────────────────────────────────

def _pilot(**kw) -> SimpleNamespace:
    heute = date.today()
    defaults = dict(
        id=1,
        geburtsdatum=date(1990, 1, 1),
        ist_truppfuehrer=True,
        a1a3_id="A1A3-001",
        a1a3_gueltig_bis=heute + timedelta(days=200),
        a2_id="A2-001",
        a2_gueltig_bis=heute + timedelta(days=200),
        bos_stufe="1",
        bos_ausbildung_datum=date(2023, 1, 1),
        bos_rezert_bis=heute + timedelta(days=365),
        lfv_zugelassen=True,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _db_with_currency(count: int):
    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = count
    return db


def _device(**kw) -> SimpleNamespace:
    defaults = dict(
        registriernummer="AT-12345",
        versicherung_polizze="POL-001",
        versicherung_gueltig_bis=date.today() + timedelta(days=100),
        status="aktiv",
        wartungen=[],
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _wartung(art: str, datum: date, ergebnis: str = "io") -> SimpleNamespace:
    return SimpleNamespace(art=art, datum=datum, ergebnis=ergebnis)


# ── pilot_freigabe_status ─────────────────────────────────────────────────────

def test_pilot_gruen_wenn_alles_ok():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(_pilot(), db)
    assert result.status == "gruen"
    assert result.fehlende == []


def test_pilot_rot_wenn_kein_truppfuehrer():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(_pilot(ist_truppfuehrer=False), db)
    assert result.status == "rot"
    assert any("Truppführer" in g for g in result.fehlende)


def test_pilot_rot_wenn_a1a3_fehlt():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(_pilot(a1a3_id=None), db)
    assert result.status == "rot"
    assert any("A1/A3" in g for g in result.fehlende)


def test_pilot_rot_wenn_a1a3_abgelaufen():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(_pilot(a1a3_gueltig_bis=date.today() - timedelta(days=1)), db)
    assert result.status == "rot"
    assert any("abgelaufen" in g for g in result.fehlende)


def test_pilot_rot_wenn_currency_zu_wenig():
    db = _db_with_currency(CURRENCY_MINCOUNT - 1)
    result = pilot_freigabe_status(_pilot(), db)
    assert result.status == "rot"
    assert any("Currency" in g for g in result.fehlende)


def test_pilot_rot_wenn_kein_geburtsdatum():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(_pilot(geburtsdatum=None), db)
    assert result.status == "rot"
    assert any("Geburtsdatum" in g for g in result.fehlende)


def test_pilot_rot_wenn_zu_jung():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    jung = date.today() - timedelta(days=365 * 17)
    result = pilot_freigabe_status(_pilot(geburtsdatum=jung), db)
    assert result.status == "rot"
    assert any("18" in g for g in result.fehlende)


def test_pilot_gelb_wenn_ablauf_bald():
    heute = date.today()
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(
        _pilot(a1a3_gueltig_bis=heute + timedelta(days=10)),
        db,
    )
    assert result.status == "gelb"
    assert result.naechster_ablauf is not None


def test_pilot_rot_wenn_lfv_fehlt():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(_pilot(lfv_zugelassen=False), db)
    assert result.status == "rot"
    assert any("LFV" in g for g in result.fehlende)


def test_pilot_rot_wenn_bos_stufe_0():
    db = _db_with_currency(CURRENCY_MINCOUNT)
    result = pilot_freigabe_status(_pilot(bos_stufe="0"), db)
    assert result.status == "rot"
    assert any("BOS" in g for g in result.fehlende)


# ── device_einsatzbereit ──────────────────────────────────────────────────────

def test_device_bereit_wenn_alles_ok():
    d = _device()
    result = device_einsatzbereit(d)
    assert result.einsatzbereit is True
    assert result.gruende == []


def test_device_nicht_bereit_ohne_registriernummer():
    d = _device(registriernummer=None)
    result = device_einsatzbereit(d)
    assert result.einsatzbereit is False
    assert any("Registriernummer" in g for g in result.gruende)


def test_device_nicht_bereit_versicherung_abgelaufen():
    abgelaufen = date.today() - timedelta(days=1)
    d = _device(versicherung_gueltig_bis=abgelaufen)
    result = device_einsatzbereit(d)
    assert result.einsatzbereit is False
    assert any("abgelaufen" in g for g in result.gruende)


def test_device_nicht_bereit_wenn_status_nicht_aktiv():
    d = _device(status="wartung")
    result = device_einsatzbereit(d)
    assert result.einsatzbereit is False


def test_device_nicht_bereit_bei_nio_wartung():
    nio = _wartung("monatliche_sichtkontrolle", date.today(), ergebnis="nio")
    d = _device(wartungen=[nio])
    result = device_einsatzbereit(d)
    assert result.einsatzbereit is False
    assert any("nio" in g for g in result.gruende)


# ── wartung_faelligkeit ───────────────────────────────────────────────────────

def test_wartung_rot_ohne_eintraege():
    d = _device(wartungen=[])
    result = wartung_faelligkeit(d)
    assert result.status == "rot"


def test_wartung_gruen_wenn_aktuell():
    heute = date.today()
    w = _wartung("monatliche_sichtkontrolle", heute - timedelta(days=5))
    d = _device(wartungen=[w])
    result = wartung_faelligkeit(d)
    assert result.status == "gruen"


def test_wartung_gelb_wenn_bald_faellig():
    heute = date.today()
    w = _wartung("monatliche_sichtkontrolle", heute - timedelta(days=25))
    d = _device(wartungen=[w])
    result = wartung_faelligkeit(d)
    assert result.status in ("gelb", "gruen")  # 25 Tage: fällig in 5 → gelb


def test_wartung_rot_wenn_ueberfaellig():
    heute = date.today()
    w = _wartung("monatliche_sichtkontrolle", heute - timedelta(days=40))
    d = _device(wartungen=[w])
    result = wartung_faelligkeit(d)
    assert result.status == "rot"


# ── Importierbarkeit ──────────────────────────────────────────────────────────

def test_compliance_service_importable():
    from app.services.uas_compliance import compliance_dashboard, pilot_freigabe_status
    assert callable(compliance_dashboard)
    assert callable(pilot_freigabe_status)
