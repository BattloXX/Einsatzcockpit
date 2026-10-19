"""K2b: Sortierung, Vorlagen, Duplizieren, Rich Text und GSL-Vorbereitung."""

import json
from copy import deepcopy

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanAbschnitt, KatplanAenderung, KatplanElement, KatplanElementPhase, KatplanPhase, KatplanPlan,
)
from app.models.user import User
from app.services.katplan_typen import ELEMENT_VORLAGEN
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form, objekte


def sortieren(client, info, daten):
    return client.post(basis(info) + "/sortierung", json=daten,
                       headers={"X-CSRF-Token": client.cookies.get("ec_csrf"), "HX-Request": "true"})


def baum(info):
    return {"abschnitte": [{"id": a.id, "parent_id": a.parent_id, "sort": a.sort}
                           for a in objekte(info, KatplanAbschnitt)],
            "elemente": [{"id": e.id, "abschnitt_id": e.abschnitt_id, "sort": e.sort}
                         for e in objekte(info, KatplanElement)]}


def kapitel(client, info, **werte):
    assert post(client, basis(info) + "/abschnitte", titel="Kapitel", **werte).status_code == 200
    return objekte(info, KatplanAbschnitt)[-1]


def weiterer_plan(info, fremd=False, status="entwurf"):
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = db.query(User).filter_by(username=info["users"]["fremd"]).one().org_id if fremd else info["org_id"]
        plan = KatplanPlan(org_id=org, titel="Zielplan", status=status)
        db.add(plan)
        db.commit()
        return plan.id, org


def test_sortierung_verschieben_normalisieren_status_und_protokoll(client, umgebung):
    login(client, umgebung)
    a = kapitel(client, umgebung)
    b = kapitel(client, umgebung)
    e1 = anlegen(client, umgebung, abschnitt_id=a.id)
    e2 = anlegen(client, umgebung, abschnitt_id=b.id)
    daten = baum(umgebung)
    daten["abschnitte"][0]["sort"] = 999
    daten["elemente"][0].update(abschnitt_id=b.id, sort=999)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        db.get(KatplanPlan, umgebung["plan_id"]).status = "freigegeben"
        db.commit()
    response = sortieren(client, umgebung, daten)
    assert response.status_code == 200 and 'id="kp-baum"' in response.text
    assert "In Überarbeitung" in response.text
    gespeichert = baum(umgebung)
    assert [a["sort"] for a in gespeichert["abschnitte"]] == [20, 10]
    assert gespeichert["elemente"] == [
        {"id": e1.id, "abschnitt_id": b.id, "sort": 20},
        {"id": e2.id, "abschnitt_id": b.id, "sort": 10},
    ]
    versionen = [e.version for e in objekte(umgebung, KatplanElement)]
    assert versionen == [1, 0]
    # Identischer Stand erzeugt weder Versionsänderungen noch neue Protokolle.
    assert sortieren(client, umgebung, gespeichert).status_code == 200
    assert [e.version for e in objekte(umgebung, KatplanElement)] == versionen
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.get(KatplanPlan, umgebung["plan_id"]).status == "in_ueberarbeitung"
        assert db.query(KatplanAenderung).filter_by(plan_id=umgebung["plan_id"], aktion="sortiert").count() == 1


@pytest.mark.parametrize("fremd", [False, True])
@pytest.mark.parametrize("feld", ["element", "abschnitt", "parent", "ziel"])
def test_sortierung_fremde_ids_atomar(client, umgebung, fremd, feld):
    login(client, umgebung)
    a = kapitel(client, umgebung)
    anlegen(client, umgebung, abschnitt_id=a.id)
    pid, org = weiterer_plan(umgebung, fremd=fremd)
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = KatplanElement(org_id=org, plan_id=pid, typ="info", titel="Fremd")
        k = KatplanAbschnitt(org_id=org, plan_id=pid, titel="Fremd")
        db.add_all([e, k])
        db.commit()
        eid, aid = e.id, k.id
    vorher = baum(umgebung)
    daten = deepcopy(vorher)
    daten["elemente"][0]["sort"] = 999
    if feld == "element":
        daten["elemente"][0]["id"] = eid
    elif feld == "abschnitt":
        daten["abschnitte"][0]["id"] = aid
    elif feld == "parent":
        daten["abschnitte"][0]["parent_id"] = aid
    else:
        daten["elemente"][0]["abschnitt_id"] = aid
    assert sortieren(client, umgebung, daten).status_code == 422
    assert baum(umgebung) == vorher
    assert objekte(umgebung, KatplanElement)[0].version == 0


@pytest.mark.parametrize("fehler", ["zyklus", "selbst", "doppelt", "fehlend", "falscher_typ"])
def test_sortierung_ungueltiger_baum(client, umgebung, fehler):
    login(client, umgebung)
    a = kapitel(client, umgebung)
    b = kapitel(client, umgebung, parent_id=a.id)
    anlegen(client, umgebung)
    vorher = baum(umgebung)
    daten = deepcopy(vorher)
    if fehler == "zyklus":
        daten["abschnitte"][0]["parent_id"] = b.id
    elif fehler == "selbst":
        daten["abschnitte"][0]["parent_id"] = a.id
    elif fehler == "doppelt":
        daten["abschnitte"][1]["id"] = a.id
    elif fehler == "fehlend":
        daten["elemente"] = []
    else:
        daten["elemente"][0]["sort"] = "falsch"
    assert sortieren(client, umgebung, daten).status_code == 422
    assert baum(umgebung) == vorher


@pytest.mark.parametrize("art,modell", [("elemente", KatplanElement), ("abschnitte", KatplanAbschnitt)])
def test_verschieben_nachbar_und_rand(client, umgebung, art, modell):
    login(client, umgebung)
    erzeugen = anlegen if art == "elemente" else kapitel
    a, b = erzeugen(client, umgebung), erzeugen(client, umgebung)
    path = basis(umgebung) + f"/{art}/{a.id}/verschieben"
    assert post(client, path, richtung="hoch").status_code == 200
    assert [o.sort for o in objekte(umgebung, modell)] == [10, 20]
    assert post(client, path, richtung="runter").status_code == 200
    assert [o.sort for o in objekte(umgebung, modell)] == [20, 10]
    assert post(client, path, richtung="runter").status_code == 200
    assert [o.sort for o in objekte(umgebung, modell)] == [20, 10]
    assert post(client, path, richtung="hoch").status_code == 200
    assert [o.sort for o in objekte(umgebung, modell)] == [10, 20]
    assert post(client, path, richtung="links").status_code == 422
    # Einträge auf einer anderen Ebene sind keine Nachbarn.
    k = kapitel(client, umgebung)
    if art == "elemente":
        erzeugen(client, umgebung, abschnitt_id=k.id)
    else:
        erzeugen(client, umgebung, parent_id=k.id)
    assert post(client, basis(umgebung) + f"/{art}/{b.id}/verschieben", richtung="hoch").status_code == 200
    assert objekte(umgebung, modell)[0].sort == 20


@pytest.mark.parametrize("code", ELEMENT_VORLAGEN)
def test_elementvorlagen(client, umgebung, code):
    login(client, umgebung)
    response = post(client, basis(umgebung) + "/elemente", vorlage=code, titel="")
    assert response.status_code == 200
    e = objekte(umgebung, KatplanElement)[0]
    vorlage = ELEMENT_VORLAGEN[code]
    assert (e.typ, e.titel) == (vorlage.typ, vorlage.titel)
    assert json.loads(e.daten_json) == vorlage.daten
    assert e.beschreibung_html == vorlage.beschreibung_html
    assert post(client, basis(umgebung) + "/elemente", vorlage="unbekannt", titel="X").status_code == 422
    assert len(objekte(umgebung, KatplanElement)) == 1
    response = post(client, basis(umgebung) + "/elemente", vorlage=code, titel="Eigener Titel", typ="kaputt")
    assert response.status_code == 200
    assert objekte(umgebung, KatplanElement)[-1].titel == "Eigener Titel"


def test_duplizieren_inhalte_phasen_und_zielplan(client, umgebung):
    login(client, umgebung)
    a = kapitel(client, umgebung)
    e = anlegen(client, umgebung, abschnitt_id=a.id)
    post(client, basis(umgebung) + "/phasen", name="Alarm", farbe="rot")
    phase = objekte(umgebung, KatplanPhase)[0]
    inhalt = {"beschreibung_html": "<h3>Prüfen</h3>", "strasse": "Weg", "hausnummer": "1", "ort": "Wien",
              "plz": "1010", "lat": 48.2, "lng": 16.3, "geometry_json": '{"type":"Point"}',
              "geo_status": "geprueft", "gsl_vorlage": True, "gsl_auftragsart": "erkundung",
              "gsl_prioritaet": 1, "gsl_dauer_min": 0, "gsl_ressourcentypen_json": '["Boot"]',
              "gsl_voraussetzungen": "Freigabe", "tags_json": '["Wasser"]', "herkunft": "imported",
              "quelle_dokument_id": 42, "quelle_seiten": "1-2", "vertraulich": True,
              "daten_json": '{"gefahren":["Überflutung"]}', "status": "aktiv", "prioritaet": 1}
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        quelle = db.get(KatplanElement, e.id)
        for feld, wert in inhalt.items():
            setattr(quelle, feld, wert)
        db.add(KatplanElementPhase(org_id=umgebung["org_id"], element_id=e.id, phase_id=phase.id))
        db.commit()
    path = basis(umgebung) + f"/elemente/{e.id}/duplizieren"
    response = post(client, path)
    assert response.status_code == 200 and 'hx-swap-oob="outerHTML"' in response.text
    kopie = objekte(umgebung, KatplanElement)[-1]
    assert kopie.uuid != e.uuid and kopie.kennung != e.kennung
    assert kopie.titel == e.titel + " (Kopie)" and kopie.version == 0
    assert kopie.abschnitt_id == a.id
    for feld, wert in inhalt.items():
        assert getattr(kopie, feld) == wert
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.query(KatplanElementPhase).filter_by(element_id=kopie.id).one().phase_id == phase.id
    ziel_id, org = weiterer_plan(umgebung, status="freigegeben")
    response = post(client, path, ziel_plan_id=ziel_id)
    assert response.status_code == 200 and f'/katastrophenplaene/{ziel_id}/bearbeiten' in response.text
    with TestingSession() as db:
        set_tenant_context(db, org)
        kopie = db.query(KatplanElement).filter_by(plan_id=ziel_id).one()
        assert kopie.abschnitt_id is None and kopie.titel == e.titel and kopie.uuid != e.uuid and kopie.version == 0
        assert db.query(KatplanElementPhase).filter_by(element_id=kopie.id).count() == 0
        for feld, wert in inhalt.items():
            assert getattr(kopie, feld) == wert
        assert db.get(KatplanPlan, ziel_id).status == "in_ueberarbeitung"
        for pid in (umgebung["plan_id"], ziel_id):
            logs = db.query(KatplanAenderung).filter_by(plan_id=pid, aktion="dupliziert").all()
            assert any(e.uuid in log.neu for log in logs)
    for fremd, status, erwartet in [(True, "entwurf", 404), (False, "archiviert", 422)]:
        pid, _ = weiterer_plan(umgebung, fremd=fremd, status=status)
        assert post(client, path, ziel_plan_id=pid).status_code == erwartet
    form = client.get(basis(umgebung) + f"/elemente/{e.id}").text
    assert 'name="ziel_plan_id"' in form


def test_gsl_speichern_feldprotokoll_und_anzeige(client, umgebung):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    werte = {"gsl_vorlage": "1", "gsl_auftragsart": "abarbeitung", "gsl_prioritaet": 3,
             "gsl_ressourcentypen": " Boot, Pumpe ", "gsl_dauer_min": 0, "gsl_voraussetzungen": "Zufahrt frei"}
    response = post(client, basis(umgebung) + f"/elemente/{e.id}", **element_form(e, **werte))
    assert response.status_code == 200 and 'name="gsl_dauer_min" min="0" max="10080" value="0"' in response.text
    stored = objekte(umgebung, KatplanElement)[0]
    assert stored.gsl_vorlage and stored.gsl_auftragsart == "abarbeitung" and stored.gsl_prioritaet == 3
    assert json.loads(stored.gsl_ressourcentypen_json) == ["Boot", "Pumpe"] and stored.gsl_dauer_min == 0
    assert stored.gsl_voraussetzungen == "Zufahrt frei"
    assert "GSL-Vorlage" in client.get(basis(umgebung)).text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        felder = {a.feld for a in db.query(KatplanAenderung).filter_by(element_id=e.id, aktion="geaendert")}
        assert felder == {"gsl_vorlage", "gsl_auftragsart", "gsl_prioritaet", "gsl_ressourcentypen_json",
                          "gsl_dauer_min", "gsl_voraussetzungen"}
    assert post(client, basis(umgebung) + f"/elemente/{e.id}", **element_form(stored)).status_code == 200
    stored = objekte(umgebung, KatplanElement)[0]
    assert not stored.gsl_vorlage and stored.gsl_dauer_min is None and stored.gsl_ressourcentypen_json is None


@pytest.mark.parametrize("werte", [
    {"gsl_auftragsart": "falsch"}, {"gsl_prioritaet": 4}, {"gsl_prioritaet": "falsch"},
    {"gsl_dauer_min": 10081}, {"gsl_dauer_min": -1}, {"gsl_dauer_min": "falsch"},
    {"gsl_ressourcentypen": "x" * 51}, {"gsl_ressourcentypen": ",".join(str(i) for i in range(21))},
    {"gsl_voraussetzungen": "x" * 2001},
])
def test_gsl_validierung_ohne_mutation(client, umgebung, werte):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    assert post(client, basis(umgebung) + f"/elemente/{e.id}", **element_form(e, titel="Geändert", **werte)).status_code == 422
    stored = objekte(umgebung, KatplanElement)[0]
    assert stored.version == 0 and stored.titel == e.titel


@pytest.mark.parametrize("html,erwartet", [
    ("<p><br></p>", None), ('<img src=x onerror="alert(1)"><p>Text</p>', '<p>Text</p>'),
    ("<h3>Überschrift</h3><h4>Untertitel</h4>", "<h3>Überschrift</h3><h4>Untertitel</h4>"),
])
def test_quill_beschreibungen_aller_formulare(client, umgebung, html, erwartet):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    a = kapitel(client, umgebung)
    assert post(client, basis(umgebung) + f"/elemente/{e.id}",
                **element_form(e, beschreibung_html=html)).status_code == 200
    assert post(client, basis(umgebung) + f"/abschnitte/{a.id}", titel=a.titel, beschreibung_html=html).status_code == 200
    assert post(client, basis(umgebung) + "/stammdaten", titel="Plan", gefahrenarten=["hochwasser"],
                version=0, beschreibung_html=html).status_code == 200
    assert objekte(umgebung, KatplanElement)[0].beschreibung_html == erwartet
    assert objekte(umgebung, KatplanAbschnitt)[0].beschreibung_html == erwartet
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.get(KatplanPlan, umgebung["plan_id"]).beschreibung_html == erwartet


def test_editor_assets_und_formulare(client, umgebung):
    login(client, umgebung)
    for pfad in (basis(umgebung), basis(umgebung) + "/bearbeiten", "/katastrophenplaene/neu"):
        text = client.get(pfad).text
        for datei in ("katplan_editor.js", "quill.min.js", "sortable.min.js", "quill.snow.css"):
            assert datei in text
    e = anlegen(client, umgebung)
    a = kapitel(client, umgebung)
    for suffix in (f"/elemente/{e.id}", f"/abschnitte/{a.id}", "/stammdaten"):
        text = client.get(basis(umgebung) + suffix).text
        assert 'data-kp-quill' in text and '<template>' in text and 'name="beschreibung_html"' in text
    editor = client.get(basis(umgebung) + "/bearbeiten").text
    assert 'class="kp-griff"' in editor and 'name="vorlage"' in editor


def test_neue_routen_nur_bearbeiter(client, umgebung):
    login(client, umgebung)
    e = anlegen(client, umgebung)
    a = kapitel(client, umgebung)
    daten = baum(umgebung)
    login(client, umgebung, "readonly")
    assert sortieren(client, umgebung, daten).status_code == 403
    for suffix in (f"/elemente/{e.id}/duplizieren", f"/elemente/{e.id}/verschieben",
                   f"/abschnitte/{a.id}/verschieben"):
        assert post(client, basis(umgebung) + suffix, richtung="hoch").status_code == 403
