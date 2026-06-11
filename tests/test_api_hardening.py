"""PR 10 – Rate-Limits & API-Härtung.

Tests für:
- AlarmPayload: Pflichtfelder, max_length-Constraints, field_validator (strip/normalize)
- LageAlarmPayload: Koordinaten-Bounds, max_length
- get_api_key_identifier: Key-basierter Rate-Limit-Schlüssel
"""
import pytest
from types import SimpleNamespace

from pydantic import ValidationError

from app.routers.api_v1 import AlarmPayload, LageAlarmPayload


# ── AlarmPayload ──────────────────────────────────────────────────────────────

def test_alarm_payload_minimal_valid():
    p = AlarmPayload(Key="A-001")
    assert p.Key == "A-001"
    assert p.Uebung is False


def test_alarm_payload_key_stripped():
    p = AlarmPayload(Key="  A-001  ")
    assert p.Key == "A-001"


def test_alarm_payload_stufe_normalized():
    p = AlarmPayload(Key="K1", Stufe="f3")
    assert p.Stufe == "F3"


def test_alarm_payload_stufe_none():
    p = AlarmPayload(Key="K1", Stufe=None)
    assert p.Stufe is None


def test_alarm_payload_key_too_long():
    with pytest.raises(ValidationError):
        AlarmPayload(Key="x" * 201)


def test_alarm_payload_key_empty():
    with pytest.raises(ValidationError):
        AlarmPayload(Key="")


def test_alarm_payload_key_whitespace_only():
    with pytest.raises(ValidationError):
        AlarmPayload(Key="   ")


def test_alarm_payload_meldung_too_long():
    with pytest.raises(ValidationError):
        AlarmPayload(Key="K1", Meldung="x" * 5001)


def test_alarm_payload_meldung_at_limit():
    p = AlarmPayload(Key="K1", Meldung="x" * 5000)
    assert len(p.Meldung) == 5000


def test_alarm_payload_stufe_too_long():
    with pytest.raises(ValidationError):
        AlarmPayload(Key="K1", Stufe="F" * 11)


def test_alarm_payload_nummer_negative():
    with pytest.raises(ValidationError):
        AlarmPayload(Key="K1", Nummer=-1)


def test_alarm_payload_nummer_valid():
    p = AlarmPayload(Key="K1", Nummer=4711)
    assert p.Nummer == 4711


def test_alarm_payload_all_fields():
    p = AlarmPayload(
        Key="A-2026-001",
        Nummer=1234,
        AlarmDatumZeit="2026-06-11T10:00:00+02:00",
        Stufe="f3",
        Art="Brand",
        Meldung="Gebäudebrand mit Menschenleben",
        Einsatzgrund="Automatische Brandmeldeanlage",
        Ort="Wolfurt",
        Strasse="Bahnhofstraße",
        HausNr="5a",
        Uebung=False,
        Name="Müller",
        Telefon="+43 5574 12345",
        Zeitzone="Europe/Vienna",
    )
    assert p.Stufe == "F3"
    assert p.Key == "A-2026-001"


# ── LageAlarmPayload ──────────────────────────────────────────────────────────

def test_lage_payload_valid():
    p = LageAlarmPayload(Key="L-001", Ort="Wolfurt")
    assert p.Key == "L-001"


def test_lage_payload_coords_valid():
    p = LageAlarmPayload(Key="L-001", Lat=47.4664, Lng=9.7416)
    assert p.Lat == pytest.approx(47.4664)


def test_lage_payload_lat_out_of_range():
    with pytest.raises(ValidationError):
        LageAlarmPayload(Key="L-001", Lat=91.0)


def test_lage_payload_lng_out_of_range():
    with pytest.raises(ValidationError):
        LageAlarmPayload(Key="L-001", Lng=181.0)


def test_lage_payload_stufe_normalized():
    p = LageAlarmPayload(Key="L-001", Stufe="t2")
    assert p.Stufe == "T2"


def test_lage_payload_key_stripped():
    p = LageAlarmPayload(Key="  L-001  ")
    assert p.Key == "L-001"


# ── get_api_key_identifier ────────────────────────────────────────────────────

def test_api_key_identifier_with_key():
    from app.core.rate_limit import get_api_key_identifier
    request = SimpleNamespace(
        headers={"X-API-Key": "my-secret-key"},
        client=SimpleNamespace(host="1.2.3.4"),
    )
    key = get_api_key_identifier(request)
    assert key.startswith("apikey:")
    assert len(key) > 7


def test_api_key_identifier_consistent():
    from app.core.rate_limit import get_api_key_identifier
    request = SimpleNamespace(
        headers={"X-API-Key": "same-key"},
        client=SimpleNamespace(host="1.2.3.4"),
    )
    assert get_api_key_identifier(request) == get_api_key_identifier(request)


def test_api_key_identifier_different_keys():
    from app.core.rate_limit import get_api_key_identifier
    r1 = SimpleNamespace(headers={"X-API-Key": "key-a"}, client=SimpleNamespace(host="1.2.3.4"))
    r2 = SimpleNamespace(headers={"X-API-Key": "key-b"}, client=SimpleNamespace(host="1.2.3.4"))
    assert get_api_key_identifier(r1) != get_api_key_identifier(r2)


def test_api_key_identifier_falls_back_to_ip():
    from app.core.rate_limit import get_api_key_identifier
    request = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="10.0.0.1"),
    )
    key = get_api_key_identifier(request)
    assert key == "10.0.0.1"


def test_api_key_identifier_uses_forwarded_ip():
    from app.core.rate_limit import get_api_key_identifier
    request = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.1, 10.0.0.1"},
        client=SimpleNamespace(host="10.0.0.1"),
    )
    key = get_api_key_identifier(request)
    assert key == "203.0.113.1"
