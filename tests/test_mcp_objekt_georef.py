"""MCP-Georeferenzierung: Rechte, Tenant-Grenzen und echte Rollbacks."""

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektDokumentGeoref
from tests.test_mcp_objekte import _rufe, _seed, _token
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte


def test_dry_run_lesen_verwerfen_und_fremd(client, plan):
    token = _token(client, plan, "u")
    args = dict(objekt_id=plan["objekt_id"], dokument_id=plan["dokument_id"], seite=1)
    trocken = _rufe(
        client, token, "objekt_karte_referenzpunkte", **args, aktion="setzen", punkte=punkte(), pruefen=True
    )
    assert not trocken["gespeichert"] and trocken["georeferenz"]["status"] == "gueltig"
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        assert db.query(ObjektDokumentGeoref).count() == 0
    echt = _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="setzen", punkte=punkte())
    assert echt["gespeichert"]
    assert (
        _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="lesen")["georeferenz"]["status"]
        == "gueltig"
    )
    _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="verwerfen", pruefen=True)
    assert (
        _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="lesen")["georeferenz"]["status"]
        == "gueltig"
    )
    fremd = _seed("georef-fremd", {"u": "objekt_verwalter"})
    ft = _token(client, fremd, "u")
    for tool, argumente in (
        ("objekt_karte_referenzpunkte", args | {"aktion": "lesen"}),
        ("objekt_karte_gebaeude_umrisse", {"objekt_id": plan["objekt_id"]}),
        (
            "objekt_karte_vorschlaege",
            {
                "objekt_id": fremd["objekt_id"],
                "aktion": "anlegen",
                "vorschlaege": [
                    {"symbol_code": "geometrie", "georef_id": echt["georeferenz"]["id"], "bild_x": 0.5, "bild_y": 0.5}
                ],
            },
        ),
    ):
        assert "__fehler__" in _rufe(client, ft, tool, **argumente)
    verworfen = _rufe(client, token, "objekt_karte_referenzpunkte", **args, aktion="verwerfen")
    assert verworfen["georeferenz"]["status"] == "abgelehnt"


@pytest.mark.parametrize("rolle", ["readonly", "recorder", "incident_leader", "objekt_verwalter", "org_admin"])
def test_rechte(client, plan, rolle):
    seed = _seed("georef-recht-" + rolle, {"u": rolle})
    token = _token(client, seed, "u")
    # Leserecht am eigenen Objekt, aber nicht existierende Seite darf keine Daten liefern.
    assert "__fehler__" in _rufe(
        client,
        token,
        "objekt_karte_referenzpunkte",
        objekt_id=seed["objekt_id"],
        dokument_id=plan["dokument_id"],
        seite=1,
        aktion="lesen",
    )
    from app.models.user import Role, User, UserRole

    with SessionLocal() as db:
        set_tenant_context(db, None)
        u = db.get(User, plan["users"]["u"])
        for ur in list(u.user_roles):
            db.delete(ur)
        db.flush()
        role = db.query(Role).filter_by(code=rolle).one()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        db.commit()
    own = _token(client, plan, "u")
    args = dict(objekt_id=plan["objekt_id"], dokument_id=plan["dokument_id"], seite=1)
    assert "__fehler__" not in _rufe(client, own, "objekt_karte_referenzpunkte", **args, aktion="lesen")
    antwort = _rufe(client, own, "objekt_karte_referenzpunkte", **args, aktion="setzen", punkte=punkte())
    assert ("__fehler__" not in antwort) == (rolle in {"objekt_verwalter", "org_admin"})


def test_umrisse_readonly_org_schalter_und_fehler(client, plan, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models.master import OrgSettings
    from app.services import objekt_gebaeude_service
    from app.services.objekt_karte_service import ObjektKartenFehler

    mock = AsyncMock(return_value={"gebaeude": [{"osm_id": 123, "ecken": []}]})
    monkeypatch.setattr(objekt_gebaeude_service, "gebaeude_umrisse", mock)
    token = _token(client, plan, "u")
    args = dict(objekt_id=plan["objekt_id"])
    antwort = _rufe(client, token, "objekt_karte_gebaeude_umrisse", **args)
    assert antwort["gebaeude"][0]["osm_id"] == 123
    mock.side_effect = ObjektKartenFehler("Overpass-Netzfehler")
    assert "Overpass-Netzfehler" in _rufe(client, token, "objekt_karte_gebaeude_umrisse", **args)["__fehler__"]
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        db.query(OrgSettings).filter_by(org_id=plan["org_id"]).one().hydrant_layer_enabled = False
        db.commit()
    assert "deaktiviert" in _rufe(client, token, "objekt_karte_gebaeude_umrisse", **args)["__fehler__"]
    assert mock.await_count == 2


@pytest.fixture
def zuordnungsdaten(plan, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models.objekt import Objekt
    from app.services import objekt_gebaeude_service
    from tests.test_objekt_georef_zuordnung import kontur

    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        bild, gebaeude = kontur(lat0=objekt.lat, lng0=objekt.lng)
    mock = AsyncMock(return_value={"gebaeude": [gebaeude]})
    monkeypatch.setattr(objekt_gebaeude_service, "gebaeude_umrisse", mock)
    args = dict(objekt_id=plan["objekt_id"], dokument_id=plan["dokument_id"], seite=1,
                bild_ecken=bild, expected_rotation=0)
    return args, mock


def _rolle_setzen(plan, rolle):
    from app.models.user import Role, User, UserRole

    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        user = db.get(User, plan["users"]["u"])
        for zuordnung in list(user.user_roles):
            db.delete(zuordnung)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=rolle).one().id))
        db.commit()


def _georeferenzen(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        return [(g.id, g.status) for g in db.query(ObjektDokumentGeoref).all()]


def test_zuordnen_analyse_als_leser(client, plan, zuordnungsdaten):
    args, mock = zuordnungsdaten
    _rolle_setzen(plan, "readonly")
    token = _token(client, plan, "u")
    antwort = _rufe(client, token, "objekt_karte_georef_zuordnen", **args)
    assert antwort["klasse"] == "A", antwort
    assert not antwort["gespeichert"] and antwort["georeferenz"] is None
    assert antwort["bildgroesse"] == [2000, 1000] and antwort["rotation"] == 0
    assert antwort["quelle"] == "OpenStreetMap / Overpass"
    assert f"/objekte/{plan['objekt_id']}/dokumente/seite/" in antwort["editor_link"]
    assert antwort["editor_link"].endswith("/georef")
    assert _georeferenzen(plan) == []
    mock.assert_awaited_once_with(47.1, 9.2, 150)


def test_zuordnen_speichern(client, plan, zuordnungsdaten):
    args, _ = zuordnungsdaten
    token = _token(client, plan, "u")
    antwort = _rufe(client, token, "objekt_karte_georef_zuordnen", **args, speichern=True, osm_id=123)
    assert antwort["klasse"] == "A" and antwort["gespeichert"], antwort
    assert _georeferenzen(plan) == [(antwort["georeferenz"]["id"], "gueltig")]


def test_zuordnen_speichern_als_leser_verboten(client, plan, zuordnungsdaten):
    args, mock = zuordnungsdaten
    _rolle_setzen(plan, "readonly")
    token = _token(client, plan, "u")
    antwort = _rufe(client, token, "objekt_karte_georef_zuordnen", **args, speichern=True)
    assert "Objektverwaltung erforderlich" in antwort["__fehler__"]
    assert _georeferenzen(plan) == []
    mock.assert_not_awaited()


def test_zuordnen_rechteck_speichert_nie(client, plan, zuordnungsdaten):
    from tests.test_objekt_georef_zuordnung import RECHTECK, kontur

    args, mock = zuordnungsdaten
    bild, gebaeude = kontur(RECHTECK, lat0=47.1, lng0=9.2)
    mock.return_value = {"gebaeude": [gebaeude]}
    token = _token(client, plan, "u")
    antwort = _rufe(client, token, "objekt_karte_georef_zuordnen",
                    **(args | {"bild_ecken": bild}), speichern=True)
    assert antwort["klasse"] == "C" and not antwort["gespeichert"], antwort
    assert antwort["punkte"] is None and _georeferenzen(plan) == []


@pytest.mark.parametrize("fall", ["rotation", "fremde_org", "deaktiviert", "global_deaktiviert", "osm_id", "overpass"])
def test_zuordnen_fehler_speichern_nichts(client, plan, zuordnungsdaten, monkeypatch, fall):
    from app.config import settings
    from app.models.master import OrgSettings
    from app.services.objekt_karte_service import ObjektKartenFehler

    args, mock = zuordnungsdaten
    seed = plan
    erwartet = ""
    if fall == "rotation":
        args["expected_rotation"] = 90
        erwartet = "Dokumentseite wurde inzwischen gedreht; Seite neu ansehen"
    elif fall == "fremde_org":
        seed = _seed("zuordnung-fremd", {"u": "objekt_verwalter"})
        erwartet = "nicht gefunden"
    elif fall == "deaktiviert":
        with SessionLocal() as db:
            set_tenant_context(db, plan["org_id"])
            db.query(OrgSettings).filter_by(org_id=plan["org_id"]).one().hydrant_layer_enabled = False
            db.commit()
        erwartet = "deaktiviert"
    elif fall == "global_deaktiviert":
        monkeypatch.setattr(settings, "HYDRANT_ENABLED", False)
        erwartet = "deaktiviert"
    elif fall == "osm_id":
        args["osm_id"] = 999
        erwartet = "OSM-Gebaeude nicht im Umkreis gefunden"
    else:
        mock.side_effect = ObjektKartenFehler("Overpass-Netzfehler")
        erwartet = "Overpass-Netzfehler"
    token = _token(client, seed, "u")
    antwort = _rufe(client, token, "objekt_karte_georef_zuordnen", **args, speichern=True)
    assert erwartet in antwort["__fehler__"], antwort
    assert _georeferenzen(plan) == []
    if fall not in {"osm_id", "overpass"}:
        mock.assert_not_awaited()


def test_zuordnen_speicherfehler_rollt_zurueck(client, plan, zuordnungsdaten, monkeypatch):
    from app.services import objekt_georef_service

    original = objekt_georef_service.referenzpunkte

    def fehler_nach_speichern(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("Speichern fehlgeschlagen")

    monkeypatch.setattr(objekt_georef_service, "referenzpunkte", fehler_nach_speichern)
    args, _ = zuordnungsdaten
    token = _token(client, plan, "u")
    antwort = _rufe(client, token, "objekt_karte_georef_zuordnen", **args, speichern=True)
    assert "Speichern fehlgeschlagen" in antwort["__fehler__"]
    assert _georeferenzen(plan) == []


def test_zuordnen_tool_liste_und_beschreibung(client, plan):
    from app.mcp.registry import TOOLS
    from tests.test_mcp_objekte import _mcp

    token = _token(client, plan, "u")
    name = "objekt_karte_georef_zuordnen"
    assert name in _mcp(client, token, "tools/list", {}, 1).text
    beschreibung = TOOLS[name].description
    for text in ("objekt_dokument_seite_ansehen", "Keine Koordinaten", "Klasse C", "editor_link", "osm_id"):
        assert text in beschreibung


def test_richtung_batch_wiederholung_und_filter(client, plan):
    token = _token(client, plan, "u")
    g = _rufe(client, token, "objekt_karte_referenzpunkte", objekt_id=plan["objekt_id"],
              dokument_id=plan["dokument_id"], seite=1, aktion="setzen", punkte=punkte())["georeferenz"]
    args = dict(objekt_id=plan["objekt_id"], aktion="anlegen", batch_id="bsp:2026-10.29_1", vorschlaege=[
        dict(symbol_code=code, georef_id=g["id"], bild_x=x, bild_y=0.5, bild_richtung={"dx": 1, "dy": 0})
        for code, x in (("hauptzugang", 0.3), ("tor", 0.7))
    ])
    erster = _rufe(client, token, "objekt_karte_vorschlaege", **args)
    assert erster["zusammenfassung"]["neu"] == 2, erster
    zweiter = _rufe(client, token, "objekt_karte_vorschlaege", **args)
    assert zweiter["zusammenfassung"] == {
        "neu": 0, "aktualisiert": 2, "unveraendert": 0, "unveraendert_offen": 0,
    }
    assert [v["id"] for v in erster["vorschlaege"]] == [v["id"] for v in zweiter["vorschlaege"]]
    assert [v["rotation"] for v in zweiter["vorschlaege"]] == [330, 60]
    basis = dict(objekt_id=plan["objekt_id"], aktion="auflisten", filter_status="offen")
    assert len(_rufe(client, token, "objekt_karte_vorschlaege", **basis)["vorschlaege"]) == 2
    assert len(_rufe(client, token, "objekt_karte_vorschlaege", **basis,
                     batch_id=args["batch_id"])["vorschlaege"]) == 2
    assert _rufe(client, token, "objekt_karte_vorschlaege", **basis, batch_id="anderer")["vorschlaege"] == []
    aendern = _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=plan["objekt_id"], aktion="aendern",
                    vorschlag_id=zweiter["vorschlaege"][0]["id"], batch_id="lauf2", vorschlaege=[{"label": "Zugang"}])
    assert aendern["vorschlaege"][0]["batch_id"] == "lauf2"
    assert aendern["vorschlaege"][0]["ergebnis"] == "aktualisiert"
    assert len(_rufe(client, token, "objekt_karte_vorschlaege", **basis,
                     batch_id=args["batch_id"])["vorschlaege"]) == 1


@pytest.mark.parametrize("batch_id", ["", "a" * 65, "lauf 1", "lauf/1", "ä", 123])
@pytest.mark.parametrize("aktion", ["anlegen", "aendern", "auflisten"])
def test_batch_id_ungueltig(client, plan, batch_id, aktion):
    token = _token(client, plan, "u")
    antwort = _rufe(client, token, "objekt_karte_vorschlaege", objekt_id=plan["objekt_id"],
                    aktion=aktion, batch_id=batch_id, vorschlaege=[{"symbol_code": "tor"}])
    assert "__fehler__" in antwort
