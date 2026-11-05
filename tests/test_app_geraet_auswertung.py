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
    assert set(codes) == set(auswertung.REGELN) - {"technik_maskiert"}
    assert auswertung.gesundheit(codes) == "fail"
    g.sdk_int, g.sicherheitspatch, g.webview_version = 24, "2025-11-01", "100.0"
    g.geraet_details = {"locale": {"uhr_abweichung_s": 120}, "geraet": {"speicher_mb_free": 200}}
    g.berechtigungen, g.dienste = {}, {}
    g.last_report_at = now - timedelta(hours=48)
    assert auswertung.befunde(g, None, token=token, jetzt=now) == []
    assert auswertung.gesundheit([]) == "ok"


@pytest.mark.parametrize(
    "wert,erwartet",
    [("[Telefon]", ["technik_maskiert"]), ("[Token]", ["technik_maskiert"]),
     ("[E-Mail]", ["technik_maskiert"]), ("[ID]", ["technik_maskiert"]),
     ("[URL]", ["technik_maskiert"]), ("[Sonstiges]", ["technik_maskiert"]),
     ("90.[Telefon]", ["technik_maskiert"]), ("90.0", ["webview"]),
     (None, ["webview"]), ("", ["webview"]), ("  ", ["webview"]),
     ("156.0.0.0", []), ("kaputt", [])],
)
def test_webview_altwerte_und_echte_versionen(wert, erwartet):
    g = geraet()
    g.webview_version = wert
    assert auswertung.befunde(g, None) == erwartet
    assert auswertung.gesundheit(erwartet) == (
        "warn" if "technik_maskiert" in erwartet else "fail" if erwartet else "ok"
    )


def test_maskierte_app_version_bleibt_unbekannt_und_warnt_nur_einmal():
    g = geraet()
    g.app_version = "[Telefon]"
    assert auswertung.ampel(g.app_version, "2026.11.01") == "unbekannt"
    assert auswertung.befunde(g, "2026.11.01") == ["technik_maskiert"]
    g.webview_version = "[Telefon]"
    g.geraet_details = {"app": {"version_name": "[Telefon]"},
                       "webview": {"version": "[Telefon]"}, "android": {"build_id": "BP4A.[Telefon]"}}
    assert auswertung.befunde(g, "2026.11.01") == ["technik_maskiert"]


@pytest.mark.parametrize("gruppe,feld", [("app", "version_name"), ("webview", "version"), ("android", "build_id")])
def test_maskierte_technikdetails_warnen(gruppe, feld):
    g = geraet(geraet_details={gruppe: {feld: "BP4A.[Telefon]"}})
    assert auswertung.befunde(g, None) == ["technik_maskiert"]


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
    monkeypatch.setattr(app_geraete, "android_release_version_cached", lambda: "v2026.11.01")
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


def test_maskierter_altbericht_in_diagnose_und_mcp(factory, monkeypatch):  # noqa: F811
    from app.admin_mcp.geraete_tools import collect_geraete

    monkeypatch.setattr(app_geraete, "android_release_version_cached", lambda: "2026.11.01")
    monkeypatch.setattr(auswertung, "soll_version", lambda: "2026.11.01")
    with factory() as db:
        db.info["current_org_id"] = None
        benutzer = insert(db, "user", username="altbericht")
        ident = insert(db, "app_geraet", user_id=benutzer, install_id="altbericht",
                       app_version="[Telefon]", webview_version="[Telefon]")
        ergebnis = app_geraete.check(db, Budget())
        assert len(ergebnis.findings) == 1
        befund = ergebnis.findings[0]
        titel, schweregrad, _, empfehlung = auswertung.REGELN["technik_maskiert"]
        assert befund.kern == "technik_maskiert"
        assert befund.titel == f"1 Geraete: {titel}"
        assert befund.schweregrad == schweregrad == "warn"
        assert befund.empfehlung == empfehlung
        details = collect_geraete(db, "admin_geraet_details", geraet_id=ident)
        assert details["status"] == "warn"
        assert details["evidence"]["version"]["ampel"] == "unbekannt"
        assert details["evidence"]["befunde"] == [
            {"code": "technik_maskiert", "titel": titel, "schweregrad": schweregrad}
        ]


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


def test_check_ohne_netz_mit_alten_fehlern(factory, diag_engine, monkeypatch):  # noqa: F811
    import httpx
    from sqlalchemy import event

    def netz_verboten(*args, **kwargs):
        pytest.fail("Leichter Diagnosecheck darf GitHub nicht aufrufen")

    monkeypatch.setattr(httpx, "get", netz_verboten)
    monkeypatch.setattr(download, "_cache_bis", 0)
    with factory() as db:
        for i in range(11):
            ident = insert(db, "app_geraet", user_id=i + 1, install_id=str(i), sdk_int=30,
                           webview_version="130")
            insert(db, "app_geraet_fehler", geraet_id=ident, komponente="fcm", code="alt", anzahl=50,
                   last_at=utcnow() - timedelta(days=8))
    abfragen = []

    def merken(conn, cursor, statement, parameters, context, executemany):
        abfragen.append(statement)

    event.listen(diag_engine, "before_cursor_execute", merken)
    try:
        ergebnis = execute_check(diag_engine, CHECKS["app.geraete"])
    finally:
        event.remove(diag_engine, "before_cursor_execute", merken)
    assert ergebnis.vollstaendig and not ergebnis.findings
    assert "Soll-Version unbekannt" in ergebnis.hinweis
    assert len(abfragen) == 2
    assert all("report_zeiten" not in sql and "einstellungen" not in sql for sql in abfragen)


def _app_check_ohne_http(db, budget):
    # Im Kindprozess jeden versehentlichen HTTP-Aufruf sichtbar scheitern lassen.
    def verboten(*args, **kwargs):
        raise AssertionError("Der isolierte Check darf kein HTTP verwenden")

    download.httpx.get = verboten
    return app_geraete.check(db, budget)


@pytest.mark.parametrize("erfolg", [True, False])
def test_isolierter_check_waermt_release_cache_vorher(factory, diag_engine, monkeypatch, erfolg):  # noqa: F811
    from app.services.diagnose.registry import Check, execute_isolated

    monkeypatch.setattr(download, "_cache_url", None)
    monkeypatch.setattr(download, "_cache_bis", 0)
    monkeypatch.setattr(download, "_cache_version", None)
    calls = []

    def release(url, **kwargs):
        calls.append(kwargs["timeout"])
        if not erfolg:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, request=httpx.Request("GET", url), json={"tag_name": "2026.11.01"})

    monkeypatch.setattr(download.httpx, "get", release)
    with factory() as db:
        insert(db, "app_geraet", user_id=1, install_id="isolated", app_version="2026.01.01",
               sdk_int=34, webview_version="130")
    check = Check("app.geraete", "app", "", "leicht", _app_check_ohne_http)
    for _ in range(2):
        result = execute_isolated(diag_engine, check)
        assert result.vollstaendig
        assert any(f.kern == "version" for f in result.findings) == erfolg
        assert (result.hinweis is None) == erfolg
    assert calls == [5.0]
    # Andere Checks loesen auch bei leerem Cache keinen Abruf aus.
    monkeypatch.setattr(download, "_cache_bis", 0)
    execute_isolated(diag_engine, CHECKS["einsatz.integritaet"])
    assert calls == [5.0]
