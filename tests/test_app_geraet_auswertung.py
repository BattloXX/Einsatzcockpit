"""Gemeinsame Regeln, Flottenfilter, Release-Ausfall und budgetierte Aggregation."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.models.admin_security import utcnow
from app.services import app_download_service as download
from app.services import app_geraet_auswertung as auswertung
from app.services.diagnose import app_geraete
from app.services.diagnose.registry import CHECKS, MAX_IDS, Budget, execute_check
from tests.test_diagnose_checks import diag_engine, factory, insert, snapshot  # noqa: F401


def geraet(**kwargs):
    return SimpleNamespace(
        sdk_int=34,
        sicherheitspatch="2026-10-01",
        webview_version="130.1",
        geraet_details=kwargs.pop("geraet_details", {}),
        app_version="v2026.10.01",
        device_token_id=kwargs.pop("device_token_id", None),
        berechtigungen=kwargs.pop("berechtigungen", {}),
        dienste=kwargs.pop("dienste", {}),
        last_report_at=kwargs.pop("last_report_at", utcnow()),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("ist", "soll", "status"),
    [
        ("v2026.1.2", "2026.01.02.0", "aktuell"),
        ("2026.01.02.1", "v2026.01.02.2", "veraltet"),
        ("2026.01.03", "2026.01.02.9", "aktuell"),
        ("kaputt", "2026.01.02", "unbekannt"),
        ("2026.02.30", "2026.03.01", "unbekannt"),
        ("2026.01.02", None, "unbekannt"),
    ],
)
def test_calver(ist, soll, status):
    assert auswertung.ampel(ist, soll) == status


def test_alle_regeln_und_grenzen():
    now = datetime(2026, 11, 1, tzinfo=UTC).replace(tzinfo=None)  # naive UTC wie die DB
    g = geraet(
        geraet_details={"locale": {"uhr_abweichung_s": -121}, "geraet": {"speicher_mb_free": 199}},
        berechtigungen={key: False for key in ("benachrichtigungen", "akku_optimierung_aus", "vollbild_intent")},
        dienste={"fcm_registriert": False},
        device_token_id=1,
        last_report_at=now - timedelta(hours=49),
    )
    g.sdk_int, g.sicherheitspatch, g.webview_version = 23, "2025-10-31", None
    errors = [SimpleNamespace(anzahl=5, last_at=now - timedelta(days=7))]
    token = SimpleNamespace(duty_active=True, revoked_at=None)
    codes = auswertung.befunde(g, "v2026.11.01", token=token, fehler=errors, jetzt=now)
    assert set(codes) == set(auswertung.REGELN)
    assert auswertung.gesundheit(codes) == "fail"
    g.sdk_int, g.sicherheitspatch, g.webview_version = 24, "2025-11-01", "100.0"
    g.geraet_details = {"locale": {"uhr_abweichung_s": 120}, "geraet": {"speicher_mb_free": 200}}
    g.berechtigungen, g.dienste = {}, {}
    g.last_report_at = now - timedelta(hours=48)
    assert auswertung.befunde(g, None, token=token, jetzt=now) == []
    assert auswertung.gesundheit([]) == "ok"


def test_token_und_fehlerschwellen():
    now = utcnow()
    g = geraet(last_report_at=now - timedelta(days=3))
    assert auswertung.befunde(g, None, jetzt=now) == []
    g.device_token_id = 1
    token = SimpleNamespace(duty_active=True, revoked_at=now)
    errors = [SimpleNamespace(anzahl=5, last_at=now - timedelta(days=8))]
    assert auswertung.befunde(g, None, token=token, fehler=errors, jetzt=now) == []
    errors[0].last_at, errors[0].anzahl = now, 4
    assert auswertung.befunde(g, None, token=token, fehler=errors, jetzt=now) == []


def test_release_ausfall_und_cache(monkeypatch):
    monkeypatch.setattr(download, "_cache_bis", 0)
    monkeypatch.setattr(download, "_cache_url", None)
    monkeypatch.setattr(download, "_cache_version", None)
    calls = []

    def offline(*args, **kwargs):
        calls.append(1)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(download.httpx, "get", offline)
    assert auswertung.soll_version() is None
    assert auswertung.soll_version() is None
    assert len(calls) == 1
    assert auswertung.ampel("v2026.01.01", auswertung.soll_version()) == "unbekannt"


def seed(db, **kwargs):
    user = insert(db, "user", username="u" + str(kwargs.get("install_id", "1")), display_name="Test")
    return insert(
        db,
        "app_geraet",
        user_id=user,
        install_id=str(user),
        sdk_int=kwargs.pop("sdk_int", 34),
        webview_version="130",
        **kwargs,
    )


def test_filter_verteilung_paginierung(factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(auswertung, "soll_version", lambda: "v2026.11.01")
    with factory() as db:
        db.info["current_org_id"] = None
        org = insert(db, "fire_dept", slug="org", name="Org")
        old = seed(
            db,
            app_version="2026.01.01",
            org_id=org,
            android_version="14",
            geraeteklasse="tablet",
            hersteller="Samsung",
            last_report_at=utcnow() - timedelta(days=3),
        )
        insert(db, "app_geraet_fehler", geraet_id=old, komponente="fcm", code="x", anzahl=5)
        insert(
            db,
            "app_geraet",
            user_id=999,
            install_id="other",
            sdk_int=35,
            app_version="2026.11.01",
            webview_version="130",
        )
        # Benutzer/Fahrzeug werden per SQL verknuepft.
        result = auswertung.liste(
            db,
            org_id=org,
            version="veraltet",
            sdk_max=34,
            android_version="14",
            geraeteklasse="tablet",
            hersteller="Samsung",
            nur_probleme=True,
            lange_nicht_gesehen=True,
        )
        assert result["gesamt"] == 1
        assert result["geraete"][0]["geraet"].id == old
        assert result["geraete"][0]["fehlerzahl"] == 5
        assert auswertung.liste(db, org_id=org, page=1, page_size=1)["geraete"] == []
        assert auswertung.liste(db, sdk_max=23)["gesamt"] == 0
        assert auswertung.verteilung(db)["android_version"]["14"] == 1


def test_check_aggregation_readonly_budget(factory, diag_engine, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app_geraete, "soll_version", lambda: "v2026.11.01")
    with factory() as db:
        ids = [
            insert(
                db,
                "app_geraet",
                user_id=i + 1,
                install_id=str(i),
                sdk_int=23,
                app_version="2026.01.01",
                webview_version="130",
            )
            for i in range(MAX_IDS + 2)
        ]
        for ident in ids:
            insert(db, "app_geraet_fehler", geraet_id=ident, komponente="fcm", code="auth", anzahl=5)
    before = snapshot(diag_engine)
    result = execute_check(diag_engine, CHECKS["app.geraete"])
    clusters = [f for f in result.findings if isinstance(f.kern, list)]
    assert len(clusters) == 1
    assert clusters[0].evidenz["modul"] == "push"
    assert clusters[0].betroffene_ids["app_geraet"] == ids[:MAX_IDS]
    assert any(f.kern == "version" for f in result.findings)
    assert any(f.kern == "sdk" for f in result.findings)
    assert snapshot(diag_engine) == before
    with factory() as db:
        limited = app_geraete.check(db, Budget(rows=1))
        assert not limited.vollstaendig
        assert all(len(f.betroffene_ids["app_geraet"]) <= 1 for f in limited.findings)
        with pytest.raises(TimeoutError):
            app_geraete.check(db, Budget(seconds=-1))


def test_verteilung_und_liste_behalten_tenant(factory, monkeypatch):  # noqa: F811
    from app.core.tenant import TenantContextMissing, set_tenant_context

    monkeypatch.setattr(auswertung, "soll_version", lambda: None)
    with factory() as db:
        with pytest.raises(TenantContextMissing):
            auswertung.verteilung(db)
        a = insert(db, "fire_dept", name="A", slug="a")
        b = insert(db, "fire_dept", name="B", slug="b")
        ua = insert(db, "user", username="a", org_id=a)
        ub = insert(db, "user", username="b", org_id=b)
        insert(db, "app_geraet", user_id=ua, org_id=a, install_id="a", android_version="14")
        insert(db, "app_geraet", user_id=ub, org_id=b, install_id="b", android_version="15")
        set_tenant_context(db, a)
        assert auswertung.verteilung(db)["android_version"] == {"14": 1}
        assert auswertung.liste(db)["gesamt"] == 1
        assert auswertung.liste(db, org_id=b)["gesamt"] == 0


@pytest.mark.parametrize(
    "assets",
    [[], [{"name": "einsatzcockpit-autoupdate-test.apk", "browser_download_url": "https://example.org/test.apk"}]],
)
def test_release_tag_aus_gemeinsamem_cache(monkeypatch, assets):
    monkeypatch.setattr(download, "_cache_url", None)
    monkeypatch.setattr(download, "_cache_bis", 0)
    monkeypatch.setattr(download, "_cache_version", None)
    calls = []

    def release(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            200, json={"tag_name": "v2026.11.01.2", "assets": assets}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(download.httpx, "get", release)
    assert auswertung.soll_version() == "v2026.11.01.2"
    download.android_apk_url()
    assert len(calls) == 1
