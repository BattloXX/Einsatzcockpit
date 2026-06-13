"""Tests für weather_focus.py — Schwerpunkt-Berechnung."""
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.models.major_incident import SitePhase, SitePriority
from app.services.weather_focus import WeatherFocus, resolve_weather_focus


# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 6, 13, 10, 0, 0, tzinfo=UTC)


def _site(
    ort="Wolfurt",
    lat=47.466,
    lng=9.745,
    phase=SitePhase.eingegangen,
    priority=SitePriority.normal,
    created_offset_s=0,
):
    s = MagicMock()
    s.ort = ort
    s.lat = lat
    s.lng = lng
    s.phase = phase
    s.priority = priority
    s.created_at = datetime.fromtimestamp(_NOW.timestamp() + created_offset_s, tz=UTC)
    return s


def _lage(*sites):
    lage = MagicMock()
    lage.sites = list(sites)
    return lage


# ── Basic cases ───────────────────────────────────────────────────────────────

def test_no_sites_returns_none():
    result = resolve_weather_focus(_lage())
    assert result is None


def test_sites_without_coords_returns_none():
    s = _site(lat=None, lng=None)
    result = resolve_weather_focus(_lage(s))
    assert result is None


def test_single_site_returns_its_coords():
    s = _site(ort="Wolfurt", lat=47.466, lng=9.745)
    result = resolve_weather_focus(_lage(s))
    assert result is not None
    assert isinstance(result, WeatherFocus)
    assert result.lat == pytest.approx(47.466)
    assert result.lng == pytest.approx(9.745)
    assert result.site_count == 1
    assert "Wolfurt" in result.label


def test_centroid_of_same_ort_group():
    s1 = _site(ort="Wolfurt", lat=47.460, lng=9.740)
    s2 = _site(ort="Wolfurt", lat=47.470, lng=9.750)
    result = resolve_weather_focus(_lage(s1, s2))
    assert result is not None
    assert result.lat == pytest.approx(47.465)
    assert result.lng == pytest.approx(9.745)
    assert result.site_count == 2


# ── Group selection ───────────────────────────────────────────────────────────

def test_picks_largest_group():
    # Wolfurt: 3 sites, Bregenz: 1 site → Wolfurt wins
    sites = [
        _site(ort="Wolfurt", lat=47.466, lng=9.745),
        _site(ort="Wolfurt", lat=47.467, lng=9.746),
        _site(ort="Wolfurt", lat=47.468, lng=9.747),
        _site(ort="Bregenz", lat=47.503, lng=9.748),
    ]
    result = resolve_weather_focus(_lage(*sites))
    assert result is not None
    assert "Wolfurt" in result.label
    assert result.site_count == 3


def test_tiebreak_by_priority():
    # Equal count (2 each), Dornbirn has more urgent sites (sofort=1 vs normal=3)
    sites = [
        _site(ort="Wolfurt", lat=47.466, lng=9.745, priority=SitePriority.normal),
        _site(ort="Wolfurt", lat=47.467, lng=9.746, priority=SitePriority.normal),
        _site(ort="Dornbirn", lat=47.413, lng=9.744, priority=SitePriority.sofort),
        _site(ort="Dornbirn", lat=47.414, lng=9.745, priority=SitePriority.sofort),
    ]
    result = resolve_weather_focus(_lage(*sites))
    assert result is not None
    assert "Dornbirn" in result.label


def test_tiebreak_by_newest_when_equal_priority():
    # Equal count, equal priority → pick group with more recent site
    old_site = _site(ort="Wolfurt", lat=47.466, lng=9.745, created_offset_s=0)
    new_site = _site(ort="Bregenz", lat=47.503, lng=9.748, created_offset_s=600)
    # both groups have 1 site, same priority → Bregenz (newer) should win
    result = resolve_weather_focus(_lage(old_site, new_site))
    assert result is not None
    assert "Bregenz" in result.label


# ── Inactive phases excluded ──────────────────────────────────────────────────

def test_excludes_erledigt_sites():
    active = _site(ort="Wolfurt", lat=47.466, lng=9.745, phase=SitePhase.eingegangen)
    done = _site(ort="Bregenz", lat=47.503, lng=9.748, phase=SitePhase.erledigt)
    result = resolve_weather_focus(_lage(active, done))
    assert result is not None
    assert "Wolfurt" in result.label


def test_excludes_abgebrochen_sites():
    active = _site(ort="Wolfurt", lat=47.466, lng=9.745, phase=SitePhase.in_arbeit)
    aborted = _site(ort="Bregenz", lat=47.503, lng=9.748, phase=SitePhase.abgebrochen)
    result = resolve_weather_focus(_lage(active, aborted))
    assert result is not None
    assert "Wolfurt" in result.label


def test_all_done_returns_none():
    s1 = _site(phase=SitePhase.erledigt, lat=47.466, lng=9.745)
    s2 = _site(phase=SitePhase.abgebrochen, lat=47.467, lng=9.746)
    result = resolve_weather_focus(_lage(s1, s2))
    assert result is None


# ── Label ─────────────────────────────────────────────────────────────────────

def test_label_singular():
    s = _site(ort="Wolfurt", lat=47.466, lng=9.745)
    result = resolve_weather_focus(_lage(s))
    assert result is not None
    assert "1 Einsatzstelle" in result.label


def test_label_plural():
    s1 = _site(ort="Wolfurt", lat=47.466, lng=9.745)
    s2 = _site(ort="Wolfurt", lat=47.467, lng=9.746)
    result = resolve_weather_focus(_lage(s1, s2))
    assert result is not None
    assert "2 Einsatzstellen" in result.label


def test_label_unknown_ort():
    s = _site(ort=None, lat=47.466, lng=9.745)
    result = resolve_weather_focus(_lage(s))
    assert result is not None
    assert "unbekannt" in result.label


# ── None priority handled ─────────────────────────────────────────────────────

def test_none_priority_treated_as_normal():
    s = _site(ort="Wolfurt", lat=47.466, lng=9.745, priority=None)
    result = resolve_weather_focus(_lage(s))
    assert result is not None
