"""Tests für die Warnungs-Anzeige-Aufbereitung in ui_weather (_build_warning_views)."""
from datetime import UTC, datetime

from app.routers.ui_weather import _build_warning_views, _warn_zeitraum
from app.services.weather_service import WeatherWarning


def _warn(level=1, event="Gewitter", vf=None, vt=None, text="Test"):
    return WeatherWarning(
        level=level,
        event_type=event,
        text=text,
        valid_from=vf or datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        valid_to=vt or datetime(2026, 6, 19, 20, 0, tzinfo=UTC),
        region="Wolfurt",
    )


def test_build_warning_views_liefert_alle_warnungen():
    views = _build_warning_views([_warn(level=1, event="Gewitter"),
                                  _warn(level=3, event="Hitze")])
    assert len(views) == 2
    assert views[0]["event_type"] == "Gewitter"
    assert views[1]["event_type"] == "Hitze"


def test_build_warning_views_stufen_farben():
    views = _build_warning_views([_warn(level=1), _warn(level=2),
                                  _warn(level=3), _warn(level=4)])
    assert [v["color"] for v in views] == ["#fbbf24", "#f97316", "#ef4444", "#a855f7"]


def test_build_warning_views_leere_liste():
    assert _build_warning_views([]) == []


def test_build_warning_views_filtert_zukuenftige_nicht():
    # _build_warning_views filtert selbst nicht – das macht _render_weather_panel.
    # Eine zukünftige Warnung (valid_from in der Zukunft) wird TROTZDEM übergeben
    # wenn der Aufrufer sie schon gefiltert hat; hier sicherstellen dass alle
    # übergebenen Warnungen erscheinen.
    future = _warn(level=2, event="Sturm",
                   vf=datetime(2099, 1, 1, 0, 0, tzinfo=UTC),
                   vt=datetime(2099, 1, 1, 6, 0, tzinfo=UTC))
    views = _build_warning_views([future])
    assert len(views) == 1
    assert views[0]["event_type"] == "Sturm"


def test_warn_zeitraum_gleicher_tag_zeigt_datum_nur_einmal():
    # 12:00–20:00 UTC → 14:00–22:00 Wiener Zeit (Sommerzeit, UTC+2)
    s = _warn_zeitraum(
        datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        datetime(2026, 6, 19, 20, 0, tzinfo=UTC),
    )
    assert s == "Fr 19.06. 14:00–22:00"


def test_warn_zeitraum_tagesuebergreifend_zeigt_beide_daten():
    s = _warn_zeitraum(
        datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        datetime(2026, 6, 20, 6, 0, tzinfo=UTC),
    )
    assert s == "Fr 19.06. 14:00 – Sa 20.06. 08:00"
