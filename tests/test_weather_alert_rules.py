"""Tests für die Wetterwarnung-Bewertungsfunktionen (rein synchron, kein Netz)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.services.weather_alert_service import (
    RULE_DEFAULTS,
    WeatherPicture,
    apply_state_machine,
    evaluate_rule,
)


# ── Hilfsobjekte ─────────────────────────────────────────────────────────────

@dataclass
class FakeStation:
    last_gust_ms: float | None = None
    last_wind_ms: float | None = None
    last_temp_c: float | None = None
    last_hum_pct: float | None = None
    last_rain_rate_mmh: float | None = None
    last_dewpoint_c: float | None = None
    last_wind_dir_deg: float | None = None
    active: bool = True


@dataclass
class FakeForecastHorizon:
    hours: int
    precipitation_acc_mm: float | None = None
    temperature_c: float | None = None
    wind_speed_ms: float | None = None
    gust_speed_ms: float | None = None


@dataclass
class FakeForecastResult:
    horizons: list = field(default_factory=list)
    source: str = "test"


@dataclass
class FakeNowcast:
    peak_mm: float = 0.0
    steps: list = field(default_factory=list)
    total_mm: float = 0.0
    trend: str = "stable"
    source: str = "test"


@dataclass
class FakeWarning:
    level: int
    event_type: str
    text: str = ""
    valid_from: datetime = field(default_factory=lambda: datetime.now(UTC))
    valid_to: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(hours=6))
    region: str = ""


@dataclass
class FakeRule:
    key: str
    org_id: int = 1
    enabled: bool = True
    vorwarnung: bool = True
    eskalation: bool = True
    cooldown_min: int = 60
    params: dict | None = None

    def __post_init__(self):
        if self.params is None:
            self.params = RULE_DEFAULTS.get(self.key, {})


@dataclass
class FakeState:
    state: str = "none"
    last_notified_at: datetime | None = None
    last_payload_hash: str | None = None
    below_threshold_cycles: int = 0


def empty_pic(**kwargs) -> WeatherPicture:
    defaults = dict(
        station=None, current=None, nowcast=None, forecast=None,
        warnings=[], bodensee_temp_c=None,
    )
    defaults.update(kwargs)
    return WeatherPicture(**defaults)


# ── Sturm ─────────────────────────────────────────────────────────────────────

def test_sturm_akut():
    st = FakeStation(last_gust_ms=26.0)
    pic = empty_pic(station=st)
    r = evaluate_rule(FakeRule("sturm"), pic)
    assert r.state == "akut"


def test_sturm_vorwarnung():
    f6 = FakeForecastHorizon(hours=6, gust_speed_ms=18.0)
    fc = FakeForecastResult(horizons=[f6])
    pic = empty_pic(forecast=fc)
    r = evaluate_rule(FakeRule("sturm"), pic)
    assert r.state == "vorwarnung"


def test_sturm_none():
    pic = empty_pic(station=FakeStation(last_gust_ms=5.0))
    r = evaluate_rule(FakeRule("sturm"), pic)
    assert r.state == "none"


# ── Starkregen ────────────────────────────────────────────────────────────────

def test_starkregen_akut():
    pic = empty_pic(station=FakeStation(last_rain_rate_mmh=30.0))
    r = evaluate_rule(FakeRule("starkregen"), pic)
    assert r.state == "akut"


def test_starkregen_vorwarnung_nowcast():
    nc = FakeNowcast(peak_mm=20.0)
    pic = empty_pic(nowcast=nc)
    r = evaluate_rule(FakeRule("starkregen"), pic)
    assert r.state == "vorwarnung"


def test_starkregen_none():
    pic = empty_pic(station=FakeStation(last_rain_rate_mmh=5.0))
    r = evaluate_rule(FakeRule("starkregen"), pic)
    assert r.state == "none"


# ── Schneefall ────────────────────────────────────────────────────────────────

def test_schneefall_akut():
    pic = empty_pic(station=FakeStation(last_temp_c=0.5, last_rain_rate_mmh=6.0))
    r = evaluate_rule(FakeRule("schneefall"), pic)
    assert r.state == "akut"


def test_schneefall_no_trigger_warm():
    pic = empty_pic(station=FakeStation(last_temp_c=5.0, last_rain_rate_mmh=10.0))
    r = evaluate_rule(FakeRule("schneefall"), pic)
    assert r.state == "none"


def test_schneefall_vorwarnung():
    f6 = FakeForecastHorizon(hours=6, temperature_c=0.0, precipitation_acc_mm=5.0)
    fc = FakeForecastResult(horizons=[f6])
    pic = empty_pic(forecast=fc)
    r = evaluate_rule(FakeRule("schneefall"), pic)
    assert r.state == "vorwarnung"


# ── Glatteis ──────────────────────────────────────────────────────────────────

def test_glatteis_akut_gefrierregen():
    pic = empty_pic(station=FakeStation(last_temp_c=0.5, last_rain_rate_mmh=0.5))
    r = evaluate_rule(FakeRule("glatteis"), pic)
    assert r.state == "akut"


def test_glatteis_akut_reifglaette():
    pic = empty_pic(station=FakeStation(last_temp_c=-1.0, last_dewpoint_c=-1.3))
    r = evaluate_rule(FakeRule("glatteis"), pic)
    assert r.state == "akut"


def test_glatteis_none():
    pic = empty_pic(station=FakeStation(last_temp_c=5.0, last_rain_rate_mmh=0.0))
    r = evaluate_rule(FakeRule("glatteis"), pic)
    assert r.state == "none"


# ── Gewitter ──────────────────────────────────────────────────────────────────

def test_gewitter_akut():
    w = FakeWarning(level=2, event_type="THUNDERSTORM")
    pic = empty_pic(warnings=[w])
    r = evaluate_rule(FakeRule("gewitter"), pic)
    assert r.state == "akut"


def test_gewitter_none():
    pic = empty_pic(warnings=[])
    r = evaluate_rule(FakeRule("gewitter"), pic)
    assert r.state == "none"


# ── Lake-Effekt ───────────────────────────────────────────────────────────────

def test_lake_effekt_vorwarnung():
    st = FakeStation(
        last_temp_c=-2.0, last_wind_dir_deg=290.0,
        last_wind_ms=8.0, last_hum_pct=85.0,
    )
    pic = empty_pic(station=st, bodensee_temp_c=11.0)
    r = evaluate_rule(FakeRule("lake_effekt"), pic)
    assert r.state == "vorwarnung"


def test_lake_effekt_akut():
    st = FakeStation(
        last_temp_c=-2.0, last_wind_dir_deg=290.0,
        last_wind_ms=8.0, last_hum_pct=85.0, last_rain_rate_mmh=1.0,
    )
    pic = empty_pic(station=st, bodensee_temp_c=11.0)
    r = evaluate_rule(FakeRule("lake_effekt"), pic)
    assert r.state == "akut"


def test_lake_effekt_wrong_dir():
    st = FakeStation(
        last_temp_c=-2.0, last_wind_dir_deg=90.0,  # Ost – kein Lake-Effekt
        last_wind_ms=8.0, last_hum_pct=85.0,
    )
    pic = empty_pic(station=st, bodensee_temp_c=11.0)
    r = evaluate_rule(FakeRule("lake_effekt"), pic)
    assert r.state == "none"


def test_lake_effekt_no_delta_t():
    # Bodensee und Luft fast gleich warm – kein Lake-Effekt
    st = FakeStation(
        last_temp_c=8.0, last_wind_dir_deg=290.0,
        last_wind_ms=8.0, last_hum_pct=85.0,
    )
    pic = empty_pic(station=st, bodensee_temp_c=11.0)
    r = evaluate_rule(FakeRule("lake_effekt"), pic)
    assert r.state == "none"


# ── Amtlich ───────────────────────────────────────────────────────────────────

def test_amtlich_relay():
    w = FakeWarning(level=3, event_type="WIND")
    pic = empty_pic(warnings=[w])
    r = evaluate_rule(FakeRule("amtlich"), pic)
    assert r.state == "akut"
    assert r.payload_hash is not None


def test_amtlich_below_level():
    w = FakeWarning(level=1, event_type="WIND")
    pic = empty_pic(warnings=[w])
    r = evaluate_rule(FakeRule("amtlich"), pic)
    assert r.state == "none"


# ── Foehn ─────────────────────────────────────────────────────────────────────

def test_foehn_akut():
    st = FakeStation(last_gust_ms=16.0, last_wind_dir_deg=180.0, last_hum_pct=35.0)
    pic = empty_pic(station=st)
    r = evaluate_rule(FakeRule("foehn"), pic)
    assert r.state == "akut"


def test_foehn_wrong_direction():
    st = FakeStation(last_gust_ms=20.0, last_wind_dir_deg=0.0, last_hum_pct=35.0)
    pic = empty_pic(station=st)
    r = evaluate_rule(FakeRule("foehn"), pic)
    assert r.state == "none"


# ── Waldbrand ─────────────────────────────────────────────────────────────────

def test_waldbrand_akut():
    st = FakeStation(last_temp_c=28.0, last_hum_pct=30.0, last_wind_ms=4.0)
    pic = empty_pic(station=st, precip_sum_5d_mm=0.5)
    r = evaluate_rule(FakeRule("waldbrand"), pic)
    assert r.state == "akut"


def test_waldbrand_no_data():
    pic = empty_pic()  # precip_sum_5d_mm = None
    r = evaluate_rule(FakeRule("waldbrand"), pic)
    assert r.state == "none"


def test_waldbrand_too_wet():
    st = FakeStation(last_temp_c=28.0, last_hum_pct=30.0, last_wind_ms=4.0)
    pic = empty_pic(station=st, precip_sum_5d_mm=5.0)  # > 1 mm → kein Waldbrand
    r = evaluate_rule(FakeRule("waldbrand"), pic)
    assert r.state == "none"


# ── Tauwetter ─────────────────────────────────────────────────────────────────

def test_tauwetter_akut():
    st = FakeStation(last_temp_c=3.0)
    pic = empty_pic(station=st, pegel_trend="steigend")
    r = evaluate_rule(FakeRule("tauwetter"), pic)
    assert r.state == "akut"


def test_tauwetter_vorwarnung():
    f6  = FakeForecastHorizon(hours=6,  temperature_c=0.0)
    f24 = FakeForecastHorizon(hours=24, temperature_c=9.0)
    fc = FakeForecastResult(horizons=[f6, f24])
    pic = empty_pic(forecast=fc)
    r = evaluate_rule(FakeRule("tauwetter"), pic)
    assert r.state == "vorwarnung"


# ── Downburst ─────────────────────────────────────────────────────────────────

def test_downburst_akut():
    w = FakeWarning(level=3, event_type="THUNDERSTORM")
    pic = empty_pic(warnings=[w])
    r = evaluate_rule(FakeRule("downburst"), pic)
    assert r.state == "akut"


# ── Zustandsmaschine ──────────────────────────────────────────────────────────

def _rule(key="sturm"):
    return FakeRule(key)


def test_state_machine_none_to_vorwarnung():
    from app.services.weather_alert_service import RuleResult
    state = FakeState(state="none")
    result = RuleResult("vorwarnung", "Test", {})
    rule = _rule()
    d = apply_state_machine(rule, result, state)
    assert d.notify is True
    assert d.new_state == "vorwarnung"


def test_state_machine_cooldown_suppresses():
    from app.services.weather_alert_service import RuleResult
    state = FakeState(
        state="akut",
        last_notified_at=datetime.now(UTC),  # gerade eben
    )
    result = RuleResult("akut", "Test", {})
    rule = _rule()
    rule.cooldown_min = 60
    d = apply_state_machine(rule, result, state)
    assert d.notify is False


def test_state_machine_eskalation_bypasses_cooldown():
    from app.services.weather_alert_service import RuleResult
    state = FakeState(
        state="vorwarnung",
        last_notified_at=datetime.now(UTC),
    )
    result = RuleResult("akut", "Eskalation!", {})
    rule = _rule()
    d = apply_state_machine(rule, result, state)
    assert d.notify is True


def test_state_machine_hysterese():
    from app.services.weather_alert_service import RuleResult
    state = FakeState(state="akut", below_threshold_cycles=0)
    result = RuleResult("none", "", {})
    rule = _rule()
    d = apply_state_machine(rule, result, state)
    # Erster Zyklus: noch kein Wechsel (Hysterese)
    assert d.new_state == "akut"
    assert d.notify is False


def test_state_machine_dedup_hash():
    from app.services.weather_alert_service import RuleResult
    state = FakeState(state="akut", last_payload_hash="abc123")
    result = RuleResult("akut", "", {}, payload_hash="abc123")
    rule = _rule()
    d = apply_state_machine(rule, result, state)
    assert d.notify is False
