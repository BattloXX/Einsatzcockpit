"""Standortmeldung: Messzeit statt Empfangszeit, Status ohne Position, 24-h-Veraltung."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import geraet_standort as gs

JETZT = datetime(2026, 11, 2, 12, 0, 0)


def token(**kw):
    basis = dict(last_lat=None, last_lng=None, last_location_at=None, last_location_received_at=None,
                 last_location_attempt_at=None, last_location_accuracy_m=None, last_location_status=None,
                 last_location_source=None, last_location_error=None)
    return SimpleNamespace(**{**basis, **kw})


def zeit(dt):
    return dt.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_altclient_ohne_messzeit_gilt_als_frisch_live():
    t = token()
    ergebnis = gs.verarbeite(t, {"lat": 47.5, "lng": 9.7, "accuracy": 8}, JETZT)
    assert ergebnis == {"position": True, "frisch": True}
    assert (t.last_location_at, t.last_location_status, t.last_location_source) == (JETZT, "ok", "live")


def test_alte_position_wird_nicht_frisch_und_zaehlt_mit_messzeit():
    t = token()
    gemessen = JETZT - timedelta(hours=3)
    ergebnis = gs.verarbeite(t, {"lat": 47.5, "lng": 9.7, "measured_at": zeit(gemessen), "source": "periodic"}, JETZT)
    assert ergebnis == {"position": True, "frisch": False}
    assert t.last_location_at == gemessen and t.last_location_received_at == JETZT
    assert gs.zustand(t, JETZT)["veraltet"] is False
    assert gs.zustand(t, JETZT + timedelta(hours=22))["veraltet"] is True


def test_position_aelter_als_24h_wird_verworfen_obwohl_uebertragung_erfolgreich():
    t = token(last_location_at=JETZT - timedelta(hours=2), last_lat=1.0, last_lng=2.0)
    ergebnis = gs.verarbeite(t, {"lat": 47.5, "lng": 9.7, "measured_at": zeit(JETZT - timedelta(hours=30))}, JETZT)
    assert ergebnis["position"] is False
    assert t.last_location_status == "stale_fix"
    assert (t.last_lat, t.last_lng) == (1.0, 2.0)


def test_aeltere_messung_ueberschreibt_neuere_nicht():
    t = token(last_location_at=JETZT - timedelta(minutes=5), last_lat=1.0, last_lng=2.0)
    gs.verarbeite(t, {"lat": 47.5, "lng": 9.7, "measured_at": zeit(JETZT - timedelta(hours=1))}, JETZT)
    assert (t.last_lat, t.last_lng) == (1.0, 2.0)


def test_zukunft_wird_auf_empfangszeit_begrenzt():
    t = token()
    gs.verarbeite(t, {"lat": 47.5, "lng": 9.7, "measured_at": zeit(JETZT + timedelta(days=3))}, JETZT)
    assert t.last_location_at == JETZT


def test_status_ohne_position_haelt_grund_fest_und_aendert_nichts():
    t = token(last_lat=1.0, last_lng=2.0, last_location_at=JETZT - timedelta(hours=1))
    ergebnis = gs.verarbeite(t, {"status": "permission_denied", "error": "Bearer abc https://x.test/a"}, JETZT)
    assert ergebnis["position"] is False
    assert t.last_location_status == "permission_denied"
    assert "abc" not in t.last_location_error and "x.test" not in t.last_location_error
    assert t.last_location_at == JETZT - timedelta(hours=1)
    assert gs.zustand(t, JETZT)["status_text"] == "Standortberechtigung fehlt"


@pytest.mark.parametrize("daten", [{"lat": 91, "lng": 0}, {"lat": 0, "lng": 181}, {"lat": "x", "lng": 0},
                                    {"lat": float("nan"), "lng": 0}, {"lat": 1, "lng": 1, "measured_at": "gestern"}])
def test_ungueltige_koordinaten_oder_zeit(daten):
    with pytest.raises(ValueError):
        gs.verarbeite(token(), daten, JETZT)


def test_geraet_ohne_messung_ist_veraltet():
    z = gs.zustand(token(), JETZT)
    assert z["veraltet"] and z["alter_s"] is None and z["status_text"].startswith("Noch keine")
