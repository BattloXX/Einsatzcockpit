"""Versionsuebernahme mit bekannten Planabbildungen und MCP-Rechtepruefung."""

import json
import math

import pytest
from PIL import Image

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentGeoref, ObjektDokumentSeite
from app.services import objekt_georef_service as geo
from app.services.objekt_georef_uebernahme_service import paare_pruefen
from app.services.objekt_karte_vorschlag_service import _abstand
from tests.test_mcp_objekt_georef import _rolle_setzen
from tests.test_mcp_objekt_karte import _rufe
from tests.test_mcp_objekte import _seed, _token
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import punkte


def _abbildung(x, y, faktor=0.7, winkel=0.2, verschiebung=0.04):
    u, v = (x - 0.5) * 2, 0.5 - y
    a, b = faktor * math.cos(winkel), faktor * math.sin(winkel)
    # Neue Seite hat Seitenverhaeltnis 1.5 statt 2.
    return 0.5 + (a * u - b * v + verschiebung) / 1.5, 0.5 - (b * u + a * v)


def _paare(abbildung=_abbildung):
    return [dict(von_x=x, von_y=y, nach_x=abbildung(x, y)[0], nach_y=abbildung(x, y)[1])
            for x, y in ((0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7), (0.5, 0.4))]


@pytest.fixture
def version(plan):
    Image.new("RGB", (1500, 1000)).save(plan["pfad"] / "neu.png")
    (plan["pfad"] / "neu.pdf").write_bytes(b"neuer Plan")
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        objekt = db.get(Objekt, plan["objekt_id"])
        antwort = geo.referenzpunkte(db, objekt, plan["dokument_id"], 1, "setzen", punkte(),
                                     "aehnlichkeit", plan["users"]["u"])
        alt = db.get(ObjektDokument, plan["dokument_id"])
        alt.ist_aktuelle_version = False
        neu = ObjektDokument(org_id=plan["org_id"], objekt_id=objekt.id, pfad="neu.pdf",
                            dateiname_original="neu.pdf", dokument_gruppe_id=alt.id,
                            versionsnummer=2, ist_aktuelle_version=True)
        db.add(neu)
        db.flush()
        db.add(ObjektDokumentSeite(org_id=objekt.org_id, objekt_id=objekt.id,
                                  dokument_id=neu.id, seiten_nr=1, bild_pfad="neu.png"))
        db.commit()
        return plan | {"neu_id": neu.id, "georef_id": antwort["georeferenz"]["id"]}


def _argumente(version, **extra):
    return dict(objekt_id=version["objekt_id"], von_dokument_id=version["dokument_id"], von_seite=1,
                nach_dokument_id=version["neu_id"], nach_seite=1, punktpaare=_paare(), expected_rotation=0) | extra


def _stand(version):
    with SessionLocal() as db:
        set_tenant_context(db, version["org_id"])
        return [(g.id, g.status, g.referenzpunkte_json) for g in db.query(ObjektDokumentGeoref).order_by(
            ObjektDokumentGeoref.id).all()]


def test_uebernahme_analyse_und_speichern(client, version):
    token = _token(client, version, "u")
    vorher = _stand(version)
    analyse = _rufe(client, token, "objekt_karte_georef_uebernehmen", **_argumente(version))
    assert analyse["klasse"] == "A" and not analyse["gespeichert"], analyse
    assert analyse["georeferenz"]["status"] == "gueltig"
    assert _stand(version) == vorher
    antwort = _rufe(client, token, "objekt_karte_georef_uebernehmen", **_argumente(version, speichern=True))
    assert antwort["klasse"] == "A" and antwort["gespeichert"], antwort
    assert antwort["uebernommene_punkte"] == 6 and antwort["verworfene_punkte"] == 0
    assert all(p["fehler"] < 1e-10 and p["loo"] < 1e-10 for p in antwort["paar_residuen"])
    assert antwort["editor_link"].endswith("/georef")
    with SessionLocal() as db:
        set_tenant_context(db, version["org_id"])
        alt = db.get(ObjektDokumentGeoref, version["georef_id"])
        neu = db.get(ObjektDokumentGeoref, antwort["georeferenz"]["id"])
        assert geo.georef_pruefen(db, db.get(Objekt, version["objekt_id"]), alt) == "veraltet"
        erwartet = geo.bild_zu_geo(alt, 0.37, 0.61)
        erhalten = geo.bild_zu_geo(neu, *_abbildung(0.37, 0.61))
        assert _abstand(*erwartet[:2], *erhalten[:2]) < 0.5
        neue_punkte = json.loads(neu.referenzpunkte_json)
        for alt_p, neu_p in zip(punkte(), neue_punkte, strict=True):
            for k in ("lat", "lng", "quelle"):
                assert alt_p[k] == neu_p[k]
            assert "uebernommen aus Dokument" in neu_p["beschreibung"]
        assert [p["id"] for p in neue_punkte] == list(range(1, 7))


@pytest.mark.parametrize("verschiebung, verworfen, klasse", [(0.5, 2, "A"), (1.0, 4, "C")])
def test_zuschnitt(client, version, verschiebung, verworfen, klasse):
    def abbildung(x, y):
        return _abbildung(x, y, faktor=1, winkel=0, verschiebung=verschiebung)

    paare = [dict(von_x=x, von_y=y, nach_x=abbildung(x, y)[0], nach_y=abbildung(x, y)[1])
             for x, y in ((0.1, 0.3), (0.3, 0.3), (0.3, 0.7), (0.1, 0.7))]
    token = _token(client, version, "u")
    vorher = _stand(version)
    antwort = _rufe(client, token, "objekt_karte_georef_uebernehmen",
                    **_argumente(version, punktpaare=paare, speichern=True))
    assert antwort["klasse"] == klasse, antwort
    assert antwort["verworfene_punkte"] == verworfen
    assert antwort["gespeichert"] == (klasse == "A")
    if klasse == "C":
        assert _stand(version) == vorher


@pytest.mark.parametrize("fall", ["falsch", "spiegel", "kollinear"])
def test_paarpruefung_klasse_c(client, version, fall):
    paare = _paare()
    if fall == "falsch":
        paare[0]["nach_x"] = 0.95
    elif fall == "spiegel":
        paare = _paare(lambda x, y: (1 - x, y))
    else:
        paare = [dict(von_x=x, von_y=x, nach_x=x, nach_y=x) for x in (0.2, 0.4, 0.6, 0.8)]
    vorher = _stand(version)
    antwort = _rufe(client, _token(client, version, "u"), "objekt_karte_georef_uebernehmen",
                    **_argumente(version, punktpaare=paare, speichern=True))
    assert antwort["klasse"] == "C" and not antwort["gespeichert"], antwort
    assert _stand(version) == vorher


@pytest.mark.parametrize("fall, meldung", [("gruppe", "Dokumentgruppe"), ("aktuell", "aktuelle Version"),
    ("abgelehnt", "Quellgeoreferenz"), ("fehlend", "Quellgeoreferenz"), ("rotation", "gedreht"),
    ("quelle_rotation", "Quellseite"), ("quelle_groesse", "Quellseite"), ("gleich", "verschiedene")])
def test_fehler(client, version, fall, meldung):
    extra = {}
    with SessionLocal() as db:
        set_tenant_context(db, version["org_id"])
        neu = db.get(ObjektDokument, version["neu_id"])
        g = db.get(ObjektDokumentGeoref, version["georef_id"])
        if fall == "gruppe":
            neu.dokument_gruppe_id = None
        elif fall == "aktuell":
            neu.ist_aktuelle_version = False
        elif fall == "abgelehnt":
            g.status = "abgelehnt"
        elif fall == "fehlend":
            db.delete(g)
        elif fall == "quelle_rotation":
            db.query(ObjektDokumentSeite).filter_by(dokument_id=version["dokument_id"]).one().rotation = 90
        elif fall == "quelle_groesse":
            Image.new("RGB", (100, 100)).save(version["pfad"] / "plan.png")
        elif fall == "rotation":
            extra["expected_rotation"] = 90
        else:
            extra["von_dokument_id"] = neu.id
        db.commit()
    vorher = _stand(version)
    antwort = _rufe(client, _token(client, version, "u"), "objekt_karte_georef_uebernehmen",
                    **_argumente(version, speichern=True, **extra))
    assert meldung in antwort["__fehler__"], antwort
    assert _stand(version) == vorher


def test_leser_und_fremde_org(client, version):
    _rolle_setzen(version, "readonly")
    token = _token(client, version, "u")
    vorher = _stand(version)
    antwort = _rufe(client, token, "objekt_karte_georef_uebernehmen", **_argumente(version))
    assert antwort["klasse"] == "A" and not antwort["gespeichert"], antwort
    assert _stand(version) == vorher
    antwort = _rufe(client, token, "objekt_karte_georef_uebernehmen", **_argumente(version, speichern=True))
    assert "Objektverwaltung" in antwort["__fehler__"]
    fremd = _seed("uebernahme-fremd", {"u": "objekt_verwalter"})
    antwort = _rufe(client, _token(client, fremd, "u"), "objekt_karte_georef_uebernehmen", **_argumente(version))
    assert "nicht gefunden" in antwort["__fehler__"]
    assert _stand(version) == vorher


@pytest.mark.parametrize("paare", [[], [{}] * 31, [{}] * 4, [None] * 4,
    [dict(von_x=float("nan"), von_y=0, nach_x=0, nach_y=0)] * 4])
def test_ungueltige_paare(paare):
    with pytest.raises(ValueError):
        paare_pruefen(paare, (2000, 1000), (1500, 1000))


def test_gedrehte_zielseite(client, version):
    Image.new("RGB", (1000, 1500)).save(version["pfad"] / "neu.png")
    with SessionLocal() as db:
        set_tenant_context(db, version["org_id"])
        db.query(ObjektDokumentSeite).filter_by(dokument_id=version["neu_id"]).one().rotation = 90
        db.commit()
    antwort = _rufe(client, _token(client, version, "u"), "objekt_karte_georef_uebernehmen",
                    **_argumente(version, expected_rotation=90, speichern=True))
    assert antwort["klasse"] == "A" and antwort["gespeichert"], antwort
    assert antwort["georeferenz"]["parameter"]["rotation"] == 90
    assert antwort["georeferenz"]["parameter"]["bildgroesse"] == [1500, 1000]


def test_speicherfehler_rollback(client, version, monkeypatch):
    original = geo.referenzpunkte

    def fehler_nach_speichern(*args, **kwargs):
        antwort = original(*args, **kwargs)
        if not kwargs.get("pruefen"):
            raise ValueError("Speicherfehler nach Anlegen")
        return antwort

    monkeypatch.setattr(geo, "referenzpunkte", fehler_nach_speichern)
    vorher = _stand(version)
    antwort = _rufe(client, _token(client, version, "u"), "objekt_karte_georef_uebernehmen",
                    **_argumente(version, speichern=True))
    assert "Speicherfehler" in antwort["__fehler__"]
    assert _stand(version) == vorher


def test_tool_beschreibung_und_schema(client, version):
    from app.mcp.registry import TOOLS
    from tests.test_mcp_objekte import _mcp

    token = _token(client, version, "u")
    name = "objekt_karte_georef_uebernehmen"
    assert name in _mcp(client, token, "tools/list", {}, 1).text
    for text in ("Dokumentgruppe", "objekt_dokument_seite_ansehen", "Planmerkmale", "Klasse C", "editor_link"):
        assert text in TOOLS[name].description


def test_persistiert_veraltete_quelle_bleibt_nutzbar(client, version):
    # markiere_veraltete setzt die Vorversion nach dem Versionswechsel dauerhaft auf veraltet.
    from app.services.objekt_karte_freigabe_service import markiere_veraltete

    with SessionLocal() as db:
        set_tenant_context(db, version["org_id"])
        markiere_veraltete(db, db.get(Objekt, version["objekt_id"]))
        db.commit()
        assert db.get(ObjektDokumentGeoref, version["georef_id"]).status == "veraltet"
    antwort = _rufe(client, _token(client, version, "u"), "objekt_karte_georef_uebernehmen",
                    **_argumente(version, speichern=True))
    assert antwort["klasse"] == "A" and antwort["gespeichert"], antwort


def test_veraltete_quelle_ausserhalb_grenzwerte_abgewiesen(client, version):
    with SessionLocal() as db:
        set_tenant_context(db, version["org_id"])
        g = db.get(ObjektDokumentGeoref, version["georef_id"])
        schlecht = punkte()
        schlecht[0]["lat"] += 0.001  # rund 110 m Fehler an einer Ecke
        g.status, g.referenzpunkte_json = "veraltet", json.dumps(schlecht)
        db.commit()
    vorher = _stand(version)
    antwort = _rufe(client, _token(client, version, "u"), "objekt_karte_georef_uebernehmen",
                    **_argumente(version, speichern=True))
    assert "Grenzwerte" in antwort["__fehler__"], antwort
    assert _stand(version) == vorher
