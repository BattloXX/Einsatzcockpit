"""Tests fuer app.services.abfluss_service (Pegelmessstationen)."""
import re
from datetime import UTC, datetime
from pathlib import Path

from app.services.abfluss_service import AbflussMessung, HQWerte, _StationState, alarm_stufe, sparkline_data

_APP_CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css"


def _defined_css_vars() -> set[str]:
    css = _APP_CSS.read_text(encoding="utf-8")
    return set(re.findall(r"(--[\w-]+)\s*:", css))


def _referenced_vars_without_fallback(css_color: str) -> list[str]:
    # var(--name) ohne Komma -> kein Fallback definiert
    return [m.group(1) for m in re.finditer(r"var\((--[\w-]+)\)", css_color)]


def test_alarm_stufe_farben_referenzieren_nur_definierte_oder_fallback_css_variablen():
    defined = _defined_css_vars()
    hq = HQWerte(hq1=10, hq10=20, hq30=30, hq100=40)
    for wert in (5, 15, 25, 35, 45):
        _, _, farbe = alarm_stufe(wert, hq)
        for var_name in _referenced_vars_without_fallback(farbe):
            assert var_name in defined, (
                f"CSS-Variable {var_name!r} in alarm_stufe()-Farbe {farbe!r} ist in app.css "
                "nicht definiert und hat keinen var()-Fallback -> Wert wird unsichtbar "
                "(z.B. Sparkline-Linie 'stroke:none')."
            )


def test_alarm_stufe_normal_ist_gruen():
    stufe, label, farbe = alarm_stufe(1.0, HQWerte())
    assert stufe == 0
    assert label == "Normal"
    assert "ampel-green" in farbe


def test_sparkline_data_baut_polyline_aus_verlauf():
    st = _StationState(hzbnr="123", name="Teststation")
    st.verlauf.append(AbflussMessung(zeitstempel=datetime(2026, 7, 11, 10, 0, tzinfo=UTC), wert_m3s=1.0))
    st.verlauf.append(AbflussMessung(zeitstempel=datetime(2026, 7, 11, 10, 15, tzinfo=UTC), wert_m3s=2.0))
    sl = sparkline_data(st)
    assert sl["points"]
    assert sl["width"] == 120
