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


# ── abfluss_poll_loop ──────────────────────────────────────────────────────────

def test_abfluss_poll_loop_returns_sofort_wenn_deaktiviert(monkeypatch):
    import asyncio

    from app.config import settings
    from app.services.abfluss_poll_loop import abfluss_poll_loop
    monkeypatch.setattr(settings, "ABFLUSS_POLL_ENABLED", False)
    asyncio.run(abfluss_poll_loop())  # darf nicht haengen/schlafen, muss sofort zurueckkehren


def test_poll_all_orgs_ruft_nur_orgs_mit_konfigurierten_stationen_ab(monkeypatch):
    import asyncio

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.db as appdb
    from app.core.tenant import set_tenant_context
    from app.models.master import FireDept, OrgSettings

    # StaticPool: _poll_all_orgs laedt die Org-Liste seit Audit-PR 4 via
    # asyncio.to_thread — der Default-Pool fuer sqlite:///:memory: ist
    # thread-lokal und saehe im Worker-Thread eine LEERE Datenbank.
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    appdb.Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(appdb, "SessionLocal", TestSession)

    s = TestSession()
    set_tenant_context(s, None)
    org_mit = FireDept(slug="poll-mit-station", name="Poll Org Mit", color="#111111", bos="Feuerwehr")
    org_ohne = FireDept(slug="poll-ohne-station", name="Poll Org Ohne", color="#222222", bos="Feuerwehr")
    s.add_all([org_mit, org_ohne])
    s.flush()
    s.add(OrgSettings(org_id=org_mit.id, abfluss_stationen='[{"hzbnr": "200048", "name": "Test"}]'))
    s.add(OrgSettings(org_id=org_ohne.id, abfluss_stationen=None))
    s.commit()
    org_mit_id = org_mit.id
    s.close()

    calls = []

    async def fake_refresh_all_for_org(org_id, stationen_cfg):
        calls.append((org_id, stationen_cfg))
        return []

    monkeypatch.setattr("app.services.abfluss_service.refresh_all_for_org", fake_refresh_all_for_org)

    from app.services.abfluss_poll_loop import _poll_all_orgs
    asyncio.run(_poll_all_orgs())

    assert len(calls) == 1
    assert calls[0][0] == org_mit_id
    assert calls[0][1] == [{"hzbnr": "200048", "name": "Test"}]
