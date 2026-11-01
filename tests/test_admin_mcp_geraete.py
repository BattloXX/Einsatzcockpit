"""App-MCP: gemeinsame Regeln, Filter, Datenschutz und lesende Begrenzung."""

import json
from datetime import timedelta

import pytest

from app.admin_mcp.geraete_tools import DETAIL_LIMIT, collect_geraete
from app.admin_mcp.registry import ADMIN_TOOLS, OVERVIEW_EXCLUDED, READ_TOOLS, collect
from app.models.admin_security import utcnow
from app.services import app_geraet_auswertung as auswertung
from app.services.diagnose.registry import MAX_IDS
from tests.test_diagnose_checks import diag_engine, factory, insert, snapshot  # noqa: F401


def seed(db, **values):
    user = insert(db, "user", username="PRIVATE-USERNAME-" + values["install_id"],
                  email=values["install_id"] + "@example.org", org_id=values.get("org_id"))
    return insert(db, "app_geraet", user_id=user, webview_version="130", **values)


@pytest.fixture
def flotte(factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(auswertung, "soll_version", lambda: "v2026.11.01")
    with factory() as db:
        db.info["current_org_id"] = None
        a = insert(db, "fire_dept", name="PRIVATE-ORG", slug="a")
        b = insert(db, "fire_dept", name="OTHER-ORG", slug="b")
        ids = []
        for index in range(MAX_IDS + 2):
            ident = seed(db, install_id=str(index), org_id=a, app_version="2026.01.01", sdk_int=23,
                         android_version="6", hersteller="Samsung", geraeteklasse="tablet",
                         last_report_at=utcnow() - timedelta(hours=49),
                         berechtigungen={"benachrichtigungen": False, "standort": "nein", "sms": True},
                         einstellungen={"auto_update": True, "el_alarm_volume_percent": 70,
                                        "el_alarm_tone": "alice@example.org"},
                         geraet_details={"geraet": {"geraetename": "PRIVATE-USERNAME", "ram_mb_total": 4096}})
            ids.append(ident)
            insert(db, "app_geraet_fehler", geraet_id=ident, komponente="fcm", code="auth", anzahl=5,
                   text="alice@example.org token=SECRET")
        other = seed(db, install_id="other", org_id=b, app_version="2026.11.01", android_version="14")
    return a, b, ids, other


def test_aggregate_filters_limits_and_readonly(factory, diag_engine, flotte):  # noqa: F811
    a, b, ids, other = flotte
    before = snapshot(diag_engine)
    with factory() as db:
        db.info["current_org_id"] = None
        result = collect(db, "admin_geraete_pruefen", org_id=a, sdk_max=23, nur_probleme=True, limit=1)
        assert result["status"] == "fail"
        e = result["evidence"]
        assert e["anzahl_geraete"] == len(ids)
        assert e["soll_version"] == "v2026.11.01"
        assert e["verteilungen"]["app_version"]["werte"] == [{"wert": "2026.01.01", "anzahl": len(ids)}]
        assert e["veraltete_gesamt"] == e["ohne_meldung_48h_gesamt"] == len(ids)
        assert len(e["veraltete_geraete"]) == len(e["ohne_meldung_48h"]) == 1
        assert e["veraltete_begrenzt"] and e["ohne_meldung_48h_begrenzt"]
        cluster = e["fehler_cluster"][0]
        assert cluster["anzahl_geraete"] == len(ids) and cluster["anzahl"] == 5 * len(ids)
        assert len(cluster["geraete_ids"]) == 1 and cluster["begrenzt"]
        assert {g["berechtigung"] for g in e["berechtigungsluecken"]} == {"benachrichtigungen", "standort"}
        bounded = collect_geraete(db, "admin_geraete_pruefen", limit=1)["evidence"]
        versions = bounded["verteilungen"]["app_version"]
        assert versions["gruppen_gesamt"] == 2 and versions["begrenzt"] and len(versions["werte"]) == 1
        e = collect_geraete(db, "admin_geraete_pruefen", limit=100)["evidence"]
        assert len(e["fehler_cluster"][0]["geraete_ids"]) == MAX_IDS
        assert len(e["veraltete_geraete"]) == MAX_IDS
        assert collect_geraete(db, "admin_geraete_pruefen", org_id=b)["status"] == "ok"
        empty = collect_geraete(db, "admin_geraete_pruefen", org_id=b, nur_probleme=True)
        assert empty["evidence"]["anzahl_geraete"] == 0
        assert collect_geraete(db, "admin_geraete_pruefen", sdk_max=22)["evidence"]["anzahl_geraete"] == 0
        db.info["current_org_id"] = b
        assert collect_geraete(db, "admin_geraete_pruefen", org_id=a)["evidence"]["anzahl_geraete"] == 0
        assert collect_geraete(db, "admin_geraet_details", geraet_id=ids[0])["evidence"] == {"geraet": None}
    assert snapshot(diag_engine) == before


def test_details_privacy_error_limit_and_outage(factory, flotte, monkeypatch):  # noqa: F811
    a, b, ids, other = flotte
    with factory() as db:
        db.info["current_org_id"] = None
        for i in range(DETAIL_LIMIT + 1):
            insert(db, "app_geraet_fehler", geraet_id=ids[0], komponente="poller", code=str(i),
                   text="alice@example.org +436641234567 password=SECRET", anzahl=1)
        result = collect(db, "admin_geraet_details", geraet_id=ids[0])
        e = result["evidence"]
        assert result["status"] == "fail"
        assert e["version"]["ampel"] == "veraltet"
        assert e["einstellungen"]["auto_update"] is True and e["einstellungen"]["el_alarm_volume_percent"] == 70
        assert e["schreibgeschuetzt"] is True
        assert len(e["fehler"]) == DETAIL_LIMIT and e["fehler_begrenzt"]
        assert {f["code"] for f in e["befunde"]} >= {"sdk", "version", "benachrichtigungen", "fehler"}
        assert e["geraet_details"]["geraet"]["ram_mb_total"] == 4096
        raw = json.dumps(result)
        for secret in ("PRIVATE-USERNAME", "PRIVATE-ORG", "alice@example.org", "SECRET",
                       "+436641234567", "install_id", "user_id"):
            assert secret not in raw
        clusters = collect_geraete(db, "admin_geraete_pruefen", limit=1)["evidence"]
        assert len(clusters["fehler_cluster"]) == 1 and clusters["fehler_cluster_begrenzt"]
        assert clusters["fehler_cluster_gesamt"] == DETAIL_LIMIT + 2
        monkeypatch.setattr(auswertung, "soll_version", lambda: None)
        e = collect_geraete(db, "admin_geraet_details", geraet_id=other)["evidence"]
        assert e["version"]["soll_version"] is None and e["version"]["ampel"] == "unbekannt"
        assert "hinweis" in e and not e["befunde"]
        e = collect_geraete(db, "admin_geraete_pruefen")["evidence"]
        assert e["soll_version"] is None and e["veraltete_gesamt"] == 0 and "hinweis" in e
        assert collect_geraete(db, "admin_geraet_details", geraet_id=2147483647) == {
            "status": "ok", "evidence": {"geraet": None}}


@pytest.mark.parametrize("args", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"org_id": True},
                                  {"org_id": 0}, {"sdk_max": -1}, {"sdk_max": "23"}, {"nur_probleme": 1}])
def test_invalid_filters(args):
    with pytest.raises(ValueError):
        collect_geraete(None, "admin_geraete_pruefen", **args)


@pytest.mark.parametrize("ident", [None, True, 0, -1, "1", 1.0])
def test_invalid_id(ident):
    with pytest.raises(ValueError):
        collect_geraete(None, "admin_geraet_details", geraet_id=ident)


def test_registry_overview_exclusion():
    for name in ("admin_geraete_pruefen", "admin_geraet_details"):
        assert name in ADMIN_TOOLS and name in READ_TOOLS and name in OVERVIEW_EXCLUDED
