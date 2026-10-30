"""MCP-BSP: Entwuerfe, lesende Auslieferbarkeitspruefung und menschliche Freigabe."""

import json
from pathlib import Path

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.registry import TOOLS
from app.models.objekt import Objekt, ObjektBspOverlay, ObjektChange, ObjektDokument, ObjektDokumentGeoref
from app.models.user import User
from app.services import objekt_bsp_overlay_service as bsp
from app.services import objekt_georef_service as geo
from tests.test_mcp_objekt_georef import _rolle_setzen
from tests.test_mcp_objekte import _mcp, _rufe, _seed, _token
from tests.test_objekt_georef import karte as karte, plan as plan, punkte

TOOL = "objekt_bsp_overlay_entwurf"


@pytest.fixture
def georef_plan(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        db.get(ObjektDokument, plan["dokument_id"]).freigabe_status = "freigegeben"
        geo.referenzpunkte(db, db.get(Objekt, plan["objekt_id"]), plan["dokument_id"],
                          1, "setzen", punkte(), "aehnlichkeit", plan["users"]["u"])
        db.commit()
    return plan


def _args(plan, **werte):
    return dict(objekt_id=plan["objekt_id"], dokument_id=plan["dokument_id"], seite=1,
                aktion="anlegen", name="Halle EG", plan_art="hallenbereich", bereich="Halle",
                geschoss="EG", ausschnitt={"art": "rechteck", "x0": 0.1, "y0": 0.2,
                                          "x1": 0.8, "y1": 0.9}, expected_rotation=0) | werte


def _bestand(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        return db.query(ObjektBspOverlay).count(), db.query(ObjektChange).filter(
            ObjektChange.feld.like("bsp_overlay.%")
        ).count()


def test_entwurf_trockenlauf_echt_status_und_audit(client, georef_plan):
    plan = georef_plan
    token = _token(client, plan, "u")
    args = _args(plan)
    vorher = _bestand(plan)
    trocken = _rufe(client, token, TOOL, **args)
    assert trocken["klasse"] == "A" and not trocken["gespeichert"], trocken
    assert trocken["overlay"]["status"] == "entwurf"
    assert not trocken["overlay"]["auslieferbar"] and "raster_bounds" not in trocken["overlay"]
    assert trocken["qualitaet"]["rmse_m"] < 0.001
    assert trocken["qualitaet"]["punktzahl"] == 6
    assert trocken["qualitaet"]["modell"] == "aehnlichkeit"
    assert trocken["qualitaet"]["freigabe_warnungen"] == []
    assert trocken["editor_link"].endswith("/georef#bsp-overlay")
    assert _bestand(plan) == vorher
    assert not (plan["pfad"] / "bsp_overlay").exists()
    echt = _rufe(client, token, TOOL, **args, pruefen=False)
    assert echt["gespeichert"] and echt["overlay"]["status"] == "entwurf", echt
    assert echt["vorschau"] == trocken["vorschau"]
    assert _bestand(plan)[0] == vorher[0] + 1
    assert not (plan["pfad"] / "bsp_overlay").exists()
    status = _rufe(client, token, TOOL, objekt_id=plan["objekt_id"], aktion="status",
                   overlay_id=echt["overlay"]["id"])
    assert not status["gespeichert"] and status["overlay"] == echt["overlay"]
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        audit = db.query(ObjektChange).filter(ObjektChange.feld.like("bsp_overlay.%")).all()
        assert audit and all(a.user_id == plan["users"]["u"] for a in audit)
        assert any(a.feld.endswith(".status") and json.loads(a.after_json) == "entwurf" for a in audit)


@pytest.mark.parametrize("zustand", ["entwurf", "freigegeben", "veraltet", "zurueckgezogen"])
def test_lesen_vorschau_felder_und_keine_schreibwirkung(client, georef_plan, zustand):
    plan = georef_plan
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        overlay = bsp.entwurf_anlegen(db, plan["objekt_id"], plan["dokument_id"], 1,
                                     {"name": "Lageplan", "plan_art": "lageplan", "geschoss": "EG", "bereich": "Hof"})
        if zustand != "entwurf":
            bsp.freigeben(db, overlay, db.get(User, plan["users"]["u"]))
        if zustand == "veraltet":
            geo.referenzpunkte(db, db.get(Objekt, plan["objekt_id"]), plan["dokument_id"], 1,
                              "setzen", punkte(), "aehnlichkeit", plan["users"]["u"])
        if zustand == "zurueckgezogen":
            bsp.zurueckziehen(db, overlay)
        db.commit()
        erwartet = {f: getattr(overlay, f) for f in ("id", "name", "status", "plan_art", "geschoss", "bereich",
                    "dokument_id", "seite", "freigabe_nr", "georef_id", "georef_revision")}
    _rolle_setzen(plan, "readonly")
    token = _token(client, plan, "u")
    vorher = _bestand(plan)
    for tool in ("objekt_karte_lesen", "objekt_karte_vorschau"):
        daten = _rufe(client, token, tool, objekt_id=plan["objekt_id"])["bsp_overlays"]
        assert len(daten) == 1
        eintrag = daten[0]
        assert all(eintrag[k] == v for k, v in erwartet.items())
        assert eintrag["ausschnitt"] == {"art": "seite", "x0": 0, "y0": 0, "x1": 1, "y1": 1}
        assert eintrag["editor_link"].endswith("/georef#bsp-overlay")
        assert eintrag["auslieferbar"] == (zustand == "freigegeben")
        if zustand == "freigegeben":
            assert len(eintrag["raster_bounds"]) == 2 and "auslieferbar_grund" not in eintrag
        else:
            assert "raster_bounds" not in eintrag and eintrag["auslieferbar_grund"]
    assert _bestand(plan) == vorher


@pytest.mark.parametrize("rolle", ["readonly", "recorder", "incident_leader", "objekt_verwalter", "org_admin"])
def test_rollen_und_lesetoken(client, georef_plan, rolle):
    plan = georef_plan
    _rolle_setzen(plan, rolle)
    token = _token(client, plan, "u")
    erlaubt = rolle in {"objekt_verwalter", "org_admin"}
    assert (f'"name":"{TOOL}"' in _mcp(client, token, "tools/list", {}, 1).text) == erlaubt
    antwort = _rufe(client, token, TOOL, **_args(plan), pruefen=False)
    assert ("__fehler__" not in antwort) == erlaubt
    assert _bestand(plan)[0] == int(erlaubt)


def test_fremde_org_objekt_dokument_und_overlay(client, georef_plan):
    plan = georef_plan
    own = _token(client, plan, "u")
    overlay_id = _rufe(client, own, TOOL, **_args(plan), pruefen=False)["overlay"]["id"]
    fremd = _seed("bsp-mcp-fremd", {"u": "objekt_verwalter"})
    token = _token(client, fremd, "u")
    vorher = _bestand(plan)
    for tool in ("objekt_karte_lesen", "objekt_karte_vorschau"):
        assert "__fehler__" in _rufe(client, token, tool, objekt_id=plan["objekt_id"])
        assert _rufe(client, token, tool, objekt_id=fremd["objekt_id"])["bsp_overlays"] == []
    for args in (_args(plan), _args(plan, objekt_id=fremd["objekt_id"]),
                 dict(objekt_id=fremd["objekt_id"], overlay_id=overlay_id, aktion="status"),
                 dict(objekt_id=plan["objekt_id"], overlay_id=overlay_id, aktion="aendern", name="Fremd")):
        assert "__fehler__" in _rufe(client, token, TOOL, **args, pruefen=False)
    assert _bestand(plan) == vorher


@pytest.mark.parametrize("fall", ["fehlt", "abgelehnt", "veraltet", "dokumentfreigabe"])
def test_ohne_gueltige_georef_fehlercode(client, georef_plan, fall):
    plan = georef_plan
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        g = db.query(ObjektDokumentGeoref).one()
        if fall == "fehlt":
            db.delete(g)
        elif fall == "dokumentfreigabe":
            db.get(ObjektDokument, plan["dokument_id"]).freigabe_status = "entwurf"
        else:
            g.status = fall
        db.commit()
    vorher = _bestand(plan)
    antwort = _rufe(client, _token(client, plan, "u"), TOOL, **_args(plan), pruefen=False)
    assert antwort["code"] == "BSP_GEOREF_UNGUELTIG" and antwort["klasse"] == "C", antwort
    assert not antwort["gespeichert"] and "vorschau" not in antwort
    assert "objekt_karte_georef_zuordnen" in antwort["hinweis"]
    assert "objekt_karte_georef_uebernehmen" in antwort["hinweis"]
    assert antwort["editor_link"].endswith("/georef#bsp-overlay")
    assert _bestand(plan) == vorher


@pytest.mark.parametrize("rotation", [None, 90, 180, 270])
def test_expected_rotation_verhindert_speichern(client, georef_plan, rotation):
    plan = georef_plan
    vorher = _bestand(plan)
    antwort = _rufe(client, _token(client, plan, "u"), TOOL,
                    **_args(plan, expected_rotation=rotation), pruefen=False)
    assert "__fehler__" in antwort and "Seite neu ansehen" in antwort["__fehler__"]
    assert _bestand(plan) == vorher


@pytest.mark.parametrize("aktion", ["freigegeben", "freigeben", "zurueckziehen", "loeschen"])
def test_keine_freigabe_aktion(client, georef_plan, aktion):
    plan = georef_plan
    assert "__fehler__" in _rufe(client, _token(client, plan, "u"), TOOL,
                                **_args(plan, aktion=aktion), pruefen=False)
    assert _bestand(plan) == (0, 0)


def test_keine_status_eingabe_und_nur_ein_overlay_tool(client, georef_plan):
    token = _token(client, georef_plan, "u")
    # FastMCP ignoriert unbekannte Argumente; selbst ein injizierter Status gibt nichts frei.
    antwort = _rufe(client, token, TOOL, **_args(georef_plan), status="freigegeben", pruefen=False)
    assert antwort["overlay"]["status"] == "entwurf" and not antwort["overlay"]["auslieferbar"]
    with SessionLocal() as db:
        set_tenant_context(db, georef_plan["org_id"])
        assert db.query(ObjektBspOverlay).one().status == "entwurf"
    assert {n for n in TOOLS if n.startswith("objekt_bsp_overlay_")} == {TOOL}
    katalog = _mcp(client, token, "tools/list", {}, 1).text
    assert f'"name":"{TOOL}"' in katalog
    for name in ("objekt_bsp_overlay_freigeben", "objekt_bsp_overlay_zurueckziehen", "objekt_bsp_overlay_loeschen"):
        assert f'"name":"{name}"' not in katalog
    assert TOOLS[TOOL].required_roles == ("objekt_verwalter",) and TOOLS[TOOL].module_check
    for text in ("nie ohne menschliche Freigabe", "expected_rotation", "pruefen=true", "editor_link", "Klasse A"):
        assert text in TOOLS[TOOL].description
    doku = (Path(__file__).resolve().parents[1] / "docs/konzepte/objektkarte-ki-symbolpflege.md").read_text()
    assert "## BSP-Overlay" in doku and TOOL in doku and "BSP_GEOREF_UNGUELTIG" in doku


def test_aendern_entwurf_invalidiert_raster_rollback_und_audit(client, georef_plan):
    plan = georef_plan
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        overlay = bsp.entwurf_anlegen(db, plan["objekt_id"], plan["dokument_id"], 1, {"name": "Alt"})
        bsp.freigeben(db, overlay, db.get(User, plan["users"]["u"]))
        db.commit()
        oid, pfad, freigabe_nr = overlay.id, overlay.raster_pfad, overlay.freigabe_nr
    token = _token(client, plan, "u")
    args = dict(objekt_id=plan["objekt_id"], overlay_id=oid, aktion="aendern", name="Neu", expected_rotation=0)
    vorher = _bestand(plan)
    trocken = _rufe(client, token, TOOL, **args)
    assert not trocken["gespeichert"] and trocken["overlay"]["status"] == "entwurf"
    assert _bestand(plan) == vorher
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        overlay = db.get(ObjektBspOverlay, oid)
        assert overlay.status == "freigegeben" and overlay.raster_pfad == pfad
    echt = _rufe(client, token, TOOL, **args, pruefen=False)
    assert echt["gespeichert"] and echt["overlay"]["freigabe_nr"] == freigabe_nr
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        overlay = db.get(ObjektBspOverlay, oid)
        assert overlay.status == "entwurf" and overlay.name == "Neu"
        assert all(getattr(overlay, f) is None for f in (
            "raster_pfad", "raster_sha256", "raster_bounds_json", "raster_breite", "raster_hoehe"))
        assert not bsp.overlay_ist_auslieferbar(db, overlay)
        aenderungen = db.query(ObjektChange).filter_by(feld=f"bsp_overlay.{oid}.status").all()
        assert any(a.before_json and json.loads(a.before_json) == "freigegeben" and json.loads(a.after_json) == "entwurf"
                   and a.user_id == plan["users"]["u"] for a in aenderungen)


def test_warnungen_und_bounds_aus_georef(client, georef_plan):
    plan = georef_plan
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        p = punkte()[:3]
        for punkt in p:
            punkt["quelle"] = "manuell"
        geo.referenzpunkte(db, db.get(Objekt, plan["objekt_id"]), plan["dokument_id"], 1,
                          "setzen", p, "aehnlichkeit", plan["users"]["u"])
        db.commit()
    token = _token(client, plan, "u")
    antwort = _rufe(client, token, TOOL, **_args(plan))
    assert set(antwort["qualitaet"]["freigabe_warnungen"]) == {"wenige_punkte", "nur_freie_klicks"}
    assert antwort["qualitaet"]["punktzahl"] == 3
    echt = _rufe(client, token, TOOL, **_args(plan), pruefen=False)
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        overlay = db.get(ObjektBspOverlay, echt["overlay"]["id"])
        bsp.raster_erzeugen(db, overlay)
        assert json.loads(overlay.raster_bounds_json) == antwort["vorschau"]["bounds"]


@pytest.mark.parametrize("ausschnitt", [
    {"art": "rechteck", "x0": -0.1, "y0": 0, "x1": 1, "y1": 1},
    {"art": "rechteck", "x0": 0.9, "y0": 0, "x1": 0.1, "y1": 1},
    {"art": "polygon"}, {"art": "seite", "x0": 0.1},
])
def test_ausschnitt_ungueltig_speichert_nichts(client, georef_plan, ausschnitt):
    plan = georef_plan
    assert "__fehler__" in _rufe(client, _token(client, plan, "u"), TOOL,
                                **_args(plan, ausschnitt=ausschnitt), pruefen=False)
    assert _bestand(plan) == (0, 0)


@pytest.mark.parametrize("ungueltig", [False, True])
def test_polygon_entwurf_bounds_rotation_und_lesefelder(client, georef_plan, ungueltig):
    from tests.test_bsp_overlay_service import POLYGON

    plan = georef_plan
    token = _token(client, plan, "u")
    crop = POLYGON | {"punkte": POLYGON["punkte"][:2]} if ungueltig else POLYGON | {"x1": -99}
    args = _args(plan, ausschnitt=crop)
    falsch = _rufe(client, token, TOOL, **(args | {"expected_rotation": 90}), pruefen=False)
    assert "__fehler__" in falsch and _bestand(plan) == (0, 0)
    antwort = _rufe(client, token, TOOL, **args, pruefen=False)
    if ungueltig:
        assert "__fehler__" in antwort and _bestand(plan) == (0, 0)
        return
    assert antwort["gespeichert"] and antwort["overlay"]["status"] == "entwurf"
    assert antwort["overlay"]["ausschnitt"] == bsp.ausschnitt_validieren(POLYGON)
    for tool in ("objekt_karte_lesen", "objekt_karte_vorschau"):
        assert _rufe(client, token, tool, objekt_id=plan["objekt_id"])["bsp_overlays"][0]["ausschnitt"] == antwort["overlay"]["ausschnitt"]
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        overlay = db.get(ObjektBspOverlay, antwort["overlay"]["id"])
        bsp.raster_erzeugen(db, overlay)
        assert json.loads(overlay.raster_bounds_json) == antwort["vorschau"]["bounds"]
