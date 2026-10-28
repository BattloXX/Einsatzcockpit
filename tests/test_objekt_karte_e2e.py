"""Phasenuebergreifende MCP-Fluesse von der Planseite bis zum PDF-Marker.

Transformation/Validierungsdetails bleiben in den jeweiligen Service-Tests.
Alle Tools laufen ueber HTTP tools/call mit echten Tokens und DB-Transaktionen.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from PIL import Image, ImageDraw

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import (
    Objekt,
    ObjektChange,
    ObjektDokument,
    ObjektDokumentGeoref,
    ObjektKartenObjekt,
    ObjektKartenVorschlag,
)
from app.models.user import Role, User, UserRole
from app.services.objekt_georef_service import seite_veralten
from app.services.objekt_karte_vorschlag_service import _abstand
from app.services.objekt_pdf_service import render_objekt_map_png
from app.services.objekt_service import erstelle_arbeitskopie, verwirf_arbeitskopie
from tests.test_mcp_objekt_karte import karte as karte
from tests.test_mcp_objekte import _rufe, _seed, _token
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import position, punkte


@pytest.fixture
def fluss(client, plan, monkeypatch):
    # Echtes Dokument und Rendering; bekannte Plantransformation aus Georef-Tests.
    bild = Image.new("RGB", (2000, 1000), "white")
    zeichnung = ImageDraw.Draw(bild)
    zeichnung.polygon([(p["x"] * 2000, p["y"] * 1000) for p in punkte()[:4]], outline="black", width=5)
    zeichnung.ellipse((730, 600, 750, 620), fill="red")
    zeichnung.text((760, 600), "Werkstor", fill="black")
    bild.save(plan["pfad"] / "plan.png")
    bild.save(plan["pfad"] / "plan.pdf", "PDF")
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        objekt.lat, objekt.lng = 47, 9
        dokument = db.get(ObjektDokument, plan["dokument_id"])
        dokument.seitenzahl = 1
        seite = dokument.seiten[0]
        seite.volltext, seite.text_quelle, seite.titel = "Werkstor im Lageplan", "mcp", "Lageplan"
        db.commit()
    ring = [{"lat": p["lat"], "lon": p["lng"]} for p in punkte()[:4]]
    overpass = AsyncMock(return_value=httpx.Response(
        200, request=httpx.Request("POST", "https://overpass.test"),
        json={"elements": [{"id": 123, "geometry": ring + ring[:1]}]},
    ))
    monkeypatch.setattr("httpx.AsyncClient.post", overpass)
    monkeypatch.setattr("app.services.hydrant_service._lesen", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.hydrant_service._setzen", AsyncMock())
    token = _token(client, plan, "u")

    def rufe(tool, **args):
        return _rufe(client, token, "objekt_karte_" + tool, objekt_id=plan["objekt_id"], **args)

    return plan | {"token": token, "rufe": rufe, "overpass": overpass}


def _georef(f, punkte_liste=None, **args):
    return f["rufe"]("referenzpunkte", dokument_id=f["dokument_id"], seite=1, aktion="setzen",
                     punkte=punkte()[:4] if punkte_liste is None else punkte_liste, **args)["georeferenz"]


def _vorschlag(f, **daten):
    return f["rufe"]("vorschlaege", aktion="anlegen", vorschlaege=[{"symbol_code": "tor"} | daten])["vorschlaege"][0]


def _planvorschlag(f, **daten):
    g = _georef(f)
    return _vorschlag(f, georef_id=g["id"], bild_x=0.37, bild_y=0.61, **daten)


def _frei(f, *vorschlaege, warnungen=None, **args):
    return f["rufe"]("freigeben", vorschlag_ids=[v["id"] for v in vorschlaege],
                     bestaetigte_warnungen=warnungen or {}, bestaetigung="ICH_BESTAETIGE_FREIGABE", **args)


def test_plan_sichten_georef_vorschlag_review_freigabe_pdf(client, fluss):
    f = fluss
    assert f["rufe"]("lesen")["anzahl_marker"] == 2
    assert any(s["code"] == "tor" for s in _rufe(client, f["token"], "objekt_karte_symbolkatalog")["symbole"])
    docs = _rufe(client, f["token"], "objekt_dokumente_auflisten", objekt_id=f["objekt_id"])
    assert docs["dokumente"][0]["seiten"][0]["hat_bild"]
    seite = _rufe(client, f["token"], "objekt_dokument_seite_ansehen", dokument_id=f["dokument_id"], seite=1)
    assert "Werkstor" in seite["volltext"]
    text = _rufe(client, f["token"], "objekt_dokument_text", dokument_id=f["dokument_id"])
    assert "Werkstor" in json.dumps(text)
    umrisse = f["rufe"]("gebaeude_umrisse")
    f["overpass"].assert_awaited_once()
    referenzen = [p | {"lat": e["lat"], "lng": e["lng"]}
                  for p, e in zip(punkte()[:4], umrisse["gebaeude"][0]["ecken"], strict=True)]
    assert _georef(f, referenzen, pruefen=True)["status"] == "gueltig"
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        assert db.query(ObjektDokumentGeoref).count() == 0
    g = _georef(f, referenzen)
    daten = dict(symbol_code="tor", georef_id=g["id"], bild_x=0.37, bild_y=0.61)
    trocken = f["rufe"]("vorschlaege", aktion="anlegen", vorschlaege=[daten], pruefen=True)
    assert not trocken["gespeichert"]
    assert f["rufe"]("vorschlaege", aktion="auflisten")["vorschlaege"] == []
    v = _vorschlag(f, **daten)
    assert _abstand(v["lat"], v["lng"], *position(0.37, 0.61)) < 0.01
    assert _vorschlag(f, **daten)["id"] == v["id"]
    assert len(f["rufe"]("pruefen")["vorschlaege"]) == 1
    review = f["rufe"]("vorschau")
    assert review["ui_link"].endswith(f'/objekte/{f["objekt_id"]}/karte')
    assert review["vorschlaege"][0]["quelle_seite_id"]
    assert review["vorschlaege"][0]["georeferenz"]["status"] == "gueltig"
    result = _frei(f, v)
    assert result["gespeichert"], result
    mid = result["ergebnisse"][0]["marker_id"]
    assert any(m["id"] == mid and m["position_status"] == "bestaetigt" for m in f["rufe"]("lesen")["marker"])
    assert _frei(f, v)["status_code"] == 409
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        audit = db.query(ObjektChange).filter_by(objekt_id=f["objekt_id"], feld="symbol_neu").one()
        assert json.loads(audit.after_json)["id"] == mid and audit.quelle == "mcp"
        with patch("staticmap.StaticMap") as karte_mock:
            karte_mock.return_value.render.return_value = Image.new("RGB", (10, 10))
            assert render_objekt_map_png(db.get(Objekt, f["objekt_id"])).startswith(b"\x89PNG")
            assert any(c.args[0].coord == (v["lng"], v["lat"])
                       for c in karte_mock.return_value.add_marker.call_args_list)


@pytest.mark.parametrize("anzahl", [0, 2])
def test_fehlende_referenzen_geben_keine_position(fluss, anzahl):
    g = _georef(fluss, punkte()[:anzahl])
    assert g["status"] == "abgelehnt"
    v = _vorschlag(fluss, georef_id=g["id"], bild_x=0.3, bild_y=0.6)
    assert v["lat"] is None and v["lng"] is None
    assert _frei(fluss, v)["status_code"] == 422


def test_unleserlich_bleibt_in_review_ohne_position(fluss):
    v = _vorschlag(fluss, quelle_dokument_id=fluss["dokument_id"], quelle_seite=1,
                   begruendung="Symbol erkannt, Standort unleserlich")
    review = fluss["rufe"]("vorschau")["vorschlaege"][0]
    assert review["position_status"] == "pruefung_erforderlich" and review["lat"] is None
    assert _frei(fluss, v)["status_code"] == 422
    assert fluss["rufe"]("lesen")["anzahl_marker"] == 2


@pytest.mark.parametrize("fall,code", [("dublette", "moegliche_dublette"), ("fern", "weit_vom_objekt"),
                                       ("fsd", "einsatzkritisch_freigabe_erforderlich"),
                                       ("bmz", "einsatzkritisch_freigabe_erforderlich")])
def test_warnungen_von_plan_bis_freigabe(client, fluss, fall, code):
    f = fluss
    if fall == "dublette":
        with SessionLocal() as db:
            set_tenant_context(db, f["org_id"])
            marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=f["objekt_id"], typ="tor").one()
            marker.lat, marker.lng = position(0.37, 0.61)
            db.commit()
    v = (_vorschlag(f, lat=48, lng=10, quelle_dokument_id=f["dokument_id"], quelle_seite=1)
         if fall == "fern" else _planvorschlag(f, symbol_code=fall if fall in {"fsd", "bmz"} else "tor"))
    assert code in {w["code"] for w in f["rufe"]("vorschau")["vorschlaege"][0]["warnungen"]}
    assert _frei(f, v)["status_code"] == 422
    if fall in {"fsd", "bmz"}:
        args = dict(objekt_id=f["objekt_id"], vorschlag_ids=[v["id"]], bestaetigte_warnungen={str(v["id"]): [code]})
        for extra in ({}, {"bestaetigung": "ja"}):
            assert "__fehler__" in _rufe(client, f["token"], "objekt_karte_freigeben", **args, **extra)
    warnungen = {str(v["id"]): [code]}
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        audit_anzahl = db.query(ObjektChange).filter_by(objekt_id=f["objekt_id"]).count()
    assert not _frei(f, v, warnungen=warnungen, pruefen=True)["gespeichert"]
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        assert db.query(ObjektChange).filter_by(objekt_id=f["objekt_id"]).count() == audit_anzahl
    assert f["rufe"]("lesen")["anzahl_marker"] == 2
    assert f["rufe"]("vorschau")["vorschlaege"][0]["freigabe_status"] == "offen"
    assert _frei(f, v, warnungen=warnungen)["gespeichert"]


def test_revision_konflikt_ist_atomar_ueber_mcp(fluss):
    f = fluss
    gut = _planvorschlag(f)
    marker = next(m for m in f["rufe"]("lesen")["marker"] if m["typ"] == "tor")
    konflikt = _vorschlag(f, operation="aendern", basis_marker_id=marker["id"], expected_revision=marker["revision"],
                         lat=47.001, lng=9.001, quelle_dokument_id=f["dokument_id"])
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        db.get(ObjektKartenObjekt, marker["id"]).revision += 1
        db.commit()
    antwort = _frei(f, gut, konflikt)
    assert antwort["status_code"] == 409 and len(antwort["ergebnisse"]) == 2
    assert f["rufe"]("lesen")["anzahl_marker"] == 2
    assert all(v["freigabe_status"] == "offen" for v in f["rufe"]("vorschau")["vorschlaege"])
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        assert db.query(ObjektChange).filter_by(objekt_id=f["objekt_id"], feld="symbol_neu").count() == 0


@pytest.mark.parametrize("aenderung", ["hash", "version", "rotation"])
def test_quellenaenderung_entwertet_den_fluss(fluss, aenderung):
    f = fluss
    angenommen = _planvorschlag(f)
    mid = _frei(f, angenommen)["ergebnisse"][0]["marker_id"]
    offen = _vorschlag(f, georef_id=angenommen["georef_id"], bild_x=0.8, bild_y=0.8)
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        objekt = db.get(Objekt, f["objekt_id"])
        dokument = db.get(ObjektDokument, f["dokument_id"])
        if aenderung == "rotation":
            dokument.seiten[0].rotation = 90
            seite_veralten(db, objekt, dokument.seiten[0])
        elif aenderung == "version":
            dokument.ist_aktuelle_version = False
            (f["pfad"] / "neu.pdf").write_bytes(b"Neue Planversion")
            db.add(ObjektDokument(org_id=f["org_id"], objekt_id=objekt.id, pfad="neu.pdf",
                                 dateiname_original="Plan neu.pdf", dokument_gruppe_id=dokument.id, versionsnummer=2))
        else:
            (f["pfad"] / "plan.pdf").write_bytes(b"Ersetzter Plan")
        db.commit()
    review = f["rufe"]("vorschau")["vorschlaege"]
    v = next(v for v in review if v["id"] == offen["id"])
    assert v["georeferenz"]["status"] == "veraltet" and v["lat"] is None
    assert _frei(f, offen)["status_code"] == 422
    f["rufe"]("vorschlaege", aktion="auflisten", veraltete_markieren=True)
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        assert db.get(ObjektKartenVorschlag, offen["id"]) is not None
        m = db.get(ObjektKartenObjekt, mid)
        assert m is not None
        if aenderung != "rotation":
            assert m.position_status == "veraltet"
        assert db.get(ObjektKartenVorschlag, angenommen["id"]).freigabe_status == "angenommen"


def test_arbeitskopie_verwerfen_entsperrt_planvorschlag(fluss):
    f = fluss
    v = _planvorschlag(f)
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        kopie = erstelle_arbeitskopie(db, db.get(Objekt, f["objekt_id"]), f["users"]["u"])
        kid = kopie.id
        db.commit()
    assert f["rufe"]("lesen")["hat_arbeitskopie"]
    assert _frei(f, v)["status_code"] == 409
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        verwirf_arbeitskopie(db, db.get(Objekt, kid), f["users"]["u"])
        db.commit()
    assert _frei(f, v)["gespeichert"]


def test_tenant_und_leser_sind_im_gesamten_fluss_isoliert(client, fluss):
    f = fluss
    v = _planvorschlag(f)
    fremd = _seed("e2e-fremd", {"u": "objekt_verwalter"})
    token = _token(client, fremd, "u")
    aufrufe = [
        ("objekt_karte_lesen", {"objekt_id": f["objekt_id"]}),
        ("objekt_dokumente_auflisten", {"objekt_id": f["objekt_id"]}),
        ("objekt_dokument_seite_ansehen", {"dokument_id": f["dokument_id"], "seite": 1}),
        ("objekt_dokument_text", {"dokument_id": f["dokument_id"]}),
        ("objekt_karte_referenzpunkte", {"objekt_id": f["objekt_id"], "dokument_id": f["dokument_id"],
                                       "seite": 1, "aktion": "lesen"}),
        ("objekt_karte_vorschlaege", {"objekt_id": f["objekt_id"], "aktion": "auflisten"}),
        ("objekt_karte_pruefen", {"objekt_id": f["objekt_id"]}),
        ("objekt_karte_gebaeude_umrisse", {"objekt_id": f["objekt_id"]}),
        ("objekt_karte_vorschau", {"objekt_id": f["objekt_id"]}),
        ("objekt_dokument_seiten_klassifizieren", {"dokument_id": f["dokument_id"],
                                                  "seiten": [{"seite": 1, "titel": "Fremdzugriff"}]}),
        ("objekt_karte_referenzpunkte", {"objekt_id": f["objekt_id"], "dokument_id": f["dokument_id"],
                                       "seite": 1, "aktion": "setzen", "punkte": punkte()[:4]}),
        ("objekt_karte_freigeben", {"objekt_id": f["objekt_id"], "vorschlag_ids": [v["id"]],
                                  "bestaetigte_warnungen": {}, "bestaetigung": "ICH_BESTAETIGE_FREIGABE"}),
        ("objekt_karte_vorschlaege", {"objekt_id": fremd["objekt_id"], "aktion": "anlegen", "vorschlaege": [
            {"symbol_code": "geometrie", "georef_id": v["georef_id"], "bild_x": 0.5, "bild_y": 0.5}]}),
        ("objekt_karte_freigeben", {"objekt_id": fremd["objekt_id"], "vorschlag_ids": [v["id"]],
                                  "bestaetigte_warnungen": {}, "bestaetigung": "ICH_BESTAETIGE_FREIGABE"}),
    ]
    for tool, args in aufrufe:
        antwort = _rufe(client, token, tool, **args)
        assert "__fehler__" in antwort or antwort.get("status_code") == 404, (tool, antwort)
    # Derselbe Nutzer wird Leser: vorhandene Vorschlaege bleiben sichtbar, Mutationen gesperrt.
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        user = db.get(User, f["users"]["u"])
        for ur in list(user.user_roles):
            db.delete(ur)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code="readonly").one().id))
        db.commit()
    leser = _token(client, f, "u")
    for tool, args in aufrufe[:-2]:
        antwort = _rufe(client, leser, tool, **args)
        erlaubt = tool in {"objekt_karte_lesen", "objekt_karte_referenzpunkte", "objekt_karte_vorschlaege",
                           "objekt_karte_vorschau", "objekt_karte_pruefen", "objekt_karte_gebaeude_umrisse"}
        if tool == "objekt_karte_referenzpunkte" and args["aktion"] == "setzen":
            erlaubt = False
        assert ("__fehler__" not in antwort) == erlaubt, (tool, antwort)
    assert "__fehler__" in _rufe(client, leser, "objekt_karte_vorschlaege", objekt_id=f["objekt_id"],
                                 aktion="anlegen", vorschlaege=[{"symbol_code": "tor"}])
    with SessionLocal() as db:
        set_tenant_context(db, f["org_id"])
        assert db.get(ObjektKartenVorschlag, v["id"]).freigabe_status == "offen"
        assert db.query(ObjektKartenObjekt).filter_by(objekt_id=f["objekt_id"]).count() == 2
