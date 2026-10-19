"""K2a: Kapitelbaum, Phasen, Elementeditor und Arbeitsstand."""

import json
import re

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanAbschnitt, KatplanAenderung, KatplanElement, KatplanElementPhase, KatplanPhase, KatplanPlan,
)
from app.services.katplan_service import abschnitte_baum
from app.services.katplan_typen import ELEMENT_TYPEN
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401


def basis(info):
    return f'/katastrophenplaene/{info["plan_id"]}'


def objekte(info, modell):
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        return db.query(modell).filter_by(plan_id=info["plan_id"]).order_by(modell.id).all()


def anlegen(client, info, typ="kontrollstelle", **werte):
    r = post(client, basis(info) + "/elemente", typ=typ, titel="Brücke", **werte)
    assert r.status_code == 200, r.text
    return objekte(info, KatplanElement)[-1]


def element_form(element, **werte):
    return {"titel": element.titel, "kennung": element.kennung, "version": element.version,
            "daten_json": element.daten_json or "{}", "prioritaet": 2, **werte}


def test_kapitel_anlegen_aendern_und_loeschen(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    assert post(client, path + "/abschnitte", titel="Eltern").status_code == 200
    parent = objekte(umgebung, KatplanAbschnitt)[-1]
    assert post(client, path + "/abschnitte", titel="Kind", parent_id=parent.id).status_code == 200
    kind = objekte(umgebung, KatplanAbschnitt)[-1]
    assert post(client, path + "/abschnitte", titel="Enkel", parent_id=kind.id).status_code == 200
    enkel = objekte(umgebung, KatplanAbschnitt)[-1]
    element = anlegen(client, umgebung, abschnitt_id=kind.id)
    assert client.get(path + f"/abschnitte/{kind.id}").status_code == 200
    response = post(client, path + f"/abschnitte/{kind.id}", titel="Neuer Titel",
                    beschreibung_html="<p>Kapitelinhalt</p><script>bad()</script>")
    assert response.status_code == 200
    assert 'hx-swap-oob="outerHTML"' in response.text and "Neuer Titel" in response.text
    assert post(client, path + f"/abschnitte/{kind.id}/loeschen").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.get(KatplanAbschnitt, kind.id) is None
        assert db.get(KatplanAbschnitt, enkel.id).parent_id == parent.id
        assert db.get(KatplanElement, element.id).abschnitt_id == parent.id
    assert post(client, path + f"/abschnitte/{parent.id}/loeschen").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.get(KatplanAbschnitt, enkel.id).parent_id is None
        assert db.get(KatplanElement, element.id).abschnitt_id is None


def test_schnellerfassung_kennung_sort_und_suchtext(client, umgebung):
    login(client, umgebung)
    first = anlegen(client, umgebung)
    second = anlegen(client, umgebung)
    assert (first.kennung, second.kennung) == ("KS-01", "KS-02")
    assert (first.sort, second.sort) == (10, 20)
    assert "brücke" in first.suchtext and "ks-01" in first.suchtext
    assert first.herkunft == "manual"
    assert post(client, basis(umgebung) + "/elemente", typ="kaputt", titel="Test").status_code == 422


def test_element_speichern_bereinigt_synchronisiert_und_protokolliert(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    post(client, path + "/phasen", name="Vorwarnung", farbe="gelb", sort=10)
    phase = objekte(umgebung, KatplanPhase)[0]
    element = anlegen(client, umgebung)
    ziel = path + f"/elemente/{element.id}"
    daten = {"gefahren": ["Überflutung"], "kontrollhinweise": "Beobachten", "unbekannt": "weg"}
    form = element_form(element, titel="Neue Brücke", kennung="KS-99", beschreibung_html="<p>Sicher</p><script>bad()</script>",
                        daten_json=json.dumps(daten), phasen=[phase.id], prioritaet=1, vertraulich="1",
                        tags="Brücke, Wasser", gueltig_bis="2027-01-02", pruefen_am="2026-12-03",
                        strasse="Hauptstraße", hausnummer="5", plz="1010", ort="Wien", typ="info")
    response = post(client, ziel, **form)
    assert response.status_code == 200 and "Element gespeichert" in response.text
    assert 'id="kp-baum"' in response.text and 'hx-swap-oob="outerHTML"' in response.text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        stored = db.get(KatplanElement, element.id)
        assert stored.typ == "kontrollstelle" and stored.version == 1
        assert json.loads(stored.daten_json) == {k: v for k, v in daten.items() if k != "unbekannt"}
        assert stored.beschreibung_html == "<p>Sicher</p>"
        assert json.loads(stored.tags_json) == ["Brücke", "Wasser"]
        assert stored.vertraulich and stored.prioritaet == 1
        assert stored.gueltig_bis.isoformat() == "2027-01-02" and stored.aktualisiert_von_id
        assert "überflutung" in stored.suchtext and "hauptstraße" in stored.suchtext
        assert [z.phase_id for z in db.query(KatplanElementPhase).filter_by(element_id=element.id)] == [phase.id]
        protokoll = db.query(KatplanAenderung).filter_by(element_id=element.id, aktion="geaendert").all()
        assert {a.feld for a in protokoll} == {
            "titel", "kennung", "beschreibung_html", "daten", "prioritaet", "vertraulich", "tags_json",
            "gueltig_bis", "pruefen_am", "strasse", "hausnummer", "plz", "ort", "phasen",
        }
    response = post(client, ziel, **form)
    assert response.status_code == 409 and "zwischenzeitlich" in response.text
    assert 'value="Neue Brücke"' in response.text
    konflikt_daten = json.loads(re.search(r'<script type="application/json"[^>]*>(.*?)</script>', response.text).group(1))
    assert konflikt_daten["gefahren"] == ["Überflutung"]
    assert post(client, ziel, **element_form(element, version=1)).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.query(KatplanElementPhase).filter_by(element_id=element.id).count() == 0
    zweites = anlegen(client, umgebung)
    assert post(client, ziel, **element_form(element, version=2, kennung=zweites.kennung)).status_code == 422
    assert post(client, ziel, **element_form(element, version=2, daten_json="{kaputt")).status_code == 422
    assert post(client, ziel, **element_form(element, version=2, daten_json="[]")).status_code == 422


@pytest.mark.parametrize("werte", [
    {"titel": ""}, {"titel": "x" * 201}, {"kennung": "x" * 31}, {"prioritaet": 4},
    {"tags": "x" * 51}, {"tags": ",".join(str(i) for i in range(21))},
    {"gueltig_bis": "kaputt"}, {"pruefen_am": "2026-02-30"}, {"ort": "x" * 101},
])
def test_element_validierung_ohne_mutation(client, umgebung, werte):
    login(client, umgebung)
    element = anlegen(client, umgebung)
    response = post(client, basis(umgebung) + f"/elemente/{element.id}", **element_form(element, **werte))
    assert response.status_code == 422 and 'role="alert"' in response.text
    assert objekte(umgebung, KatplanElement)[0].version == 0


def test_inhaltsaenderungen_status_ohne_kopfdaten_version(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    element = anlegen(client, umgebung)
    post(client, path + "/abschnitte", titel="Kapitel")
    abschnitt = objekte(umgebung, KatplanAbschnitt)[0]
    post(client, path + "/phasen", name="Phase", farbe="rot")
    phase = objekte(umgebung, KatplanPhase)[0]
    aktionen = [
        ("/abschnitte", {"titel": "Weiteres Kapitel"}),
        (f"/abschnitte/{abschnitt.id}", {"titel": "Geändert"}),
        (f"/abschnitte/{abschnitt.id}/loeschen", {}),
        ("/phasen", {"name": "Neu", "farbe": "blau"}),
        (f"/phasen/{phase.id}", {"name": "Geändert", "farbe": "gelb"}),
        (f"/phasen/{phase.id}/loeschen", {}),
        ("/elemente", {"titel": "Neues Element", "typ": "info"}),
        (f"/elemente/{element.id}", element_form(element, titel="Geändert")),
        (f"/elemente/{element.id}/loeschen", {}),
    ]
    for suffix, form in aktionen:
        with TestingSession() as db:
            set_tenant_context(db, umgebung["org_id"])
            plan = db.get(KatplanPlan, umgebung["plan_id"])
            plan.status = "freigegeben"
            plan.version = 17
            db.commit()
        assert post(client, path + suffix, **form).status_code == 200
        with TestingSession() as db:
            set_tenant_context(db, umgebung["org_id"])
            plan = db.get(KatplanPlan, umgebung["plan_id"])
            assert plan.status == "in_ueberarbeitung" and plan.version == 17
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.query(KatplanAenderung).filter_by(plan_id=plan.id, feld="status").count() == len(aktionen)


def test_leser_kein_editor_oder_element_post(client, umgebung):
    login(client, umgebung)
    element = anlegen(client, umgebung)
    login(client, umgebung, "readonly")
    path = basis(umgebung)
    assert client.get(path + "/bearbeiten").status_code == 403
    assert client.get(path + f"/elemente/{element.id}").status_code == 403
    assert post(client, path + "/elemente", typ="info", titel="Verboten").status_code == 403
    assert post(client, path + f"/elemente/{element.id}", **element_form(element)).status_code == 403


def test_plan_und_org_isolation_aller_objekte(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd_id = db.query(KatplanPlan.org_id).filter(KatplanPlan.id == umgebung["plan_id"]).scalar()
        from app.models.user import User
        org_b = db.query(User).filter_by(username=umgebung["users"]["fremd"]).one().org_id
        ids = []
        for org_id in (fremd_id, org_b):
            plan = KatplanPlan(org_id=org_id, titel="Anderer Plan")
            db.add(plan)
            db.flush()
            element = KatplanElement(org_id=org_id, plan_id=plan.id, typ="info", titel="Fremdes Element")
            abschnitt = KatplanAbschnitt(org_id=org_id, plan_id=plan.id, titel="Fremdes Kapitel")
            phase = KatplanPhase(org_id=org_id, plan_id=plan.id, name="Fremde Phase")
            db.add_all([element, abschnitt, phase])
            db.flush()
            ids.append((plan.id, element.id, abschnitt.id, phase.id))
        db.commit()
    eigenes = anlegen(client, umgebung)
    for plan_id, eid, aid, pid in ids:
        for suffix in (f"/elemente/{eid}", f"/abschnitte/{aid}"):
            assert client.get(path + suffix).status_code == 404
            assert post(client, path + suffix, titel="Manipuliert").status_code == 404
            assert post(client, path + suffix + "/loeschen").status_code == 404
        assert post(client, path + f"/phasen/{pid}", name="Manipuliert").status_code == 404
        assert post(client, path + f"/phasen/{pid}/loeschen").status_code == 404
        assert post(client, path + "/abschnitte", titel="Falsch", parent_id=aid).status_code == 404
        assert post(client, path + "/elemente", titel="Falsch", typ="info", abschnitt_id=aid).status_code == 404
        assert post(client, path + f"/elemente/{eigenes.id}", **element_form(eigenes, phasen=[pid])).status_code == 404
        assert post(client, path + f"/elemente/{eigenes.id}", **element_form(eigenes, abschnitt_id=aid)).status_code == 404
    fremd_plan = ids[1][0]
    assert client.get(f"/katastrophenplaene/{fremd_plan}/bearbeiten").status_code == 404


@pytest.mark.parametrize("typ", ELEMENT_TYPEN)
def test_alle_typen_formular_und_leseansicht(client, umgebung, typ):
    login(client, umgebung)
    element = anlegen(client, umgebung, typ=typ)
    # Alle Datenfelder und Tabellen auch mit Inhalt rendern.
    daten = {feld: f"Inhalt {feld}" for feld in ELEMENT_TYPEN[typ].felder}
    for feld, wert in {
        "schritte": [{"text": "Prüfen", "erledigt_hinweis": "Protokollieren"}],
        "bedingungen": [{"wenn": "Pegel steigt", "dann": "Alarmieren"}],
        "bedarf": [{"bezeichnung": "Sandsäcke", "menge": "100", "einheit": "Stück"}],
        "gefahren": ["Überflutung"],
    }.items():
        if feld in daten:
            daten[feld] = wert
    path = basis(umgebung)
    assert post(client, path + f"/elemente/{element.id}", **element_form(element, daten_json=json.dumps(daten))).status_code == 200
    response = client.get(path + f"/elemente/{element.id}")
    assert response.status_code == 200 and 'name="daten_json"' in response.text
    assert '<script type="application/json"' in response.text
    editor = client.get(path + "/bearbeiten")
    assert editor.status_code == 200 and 'id="kp-baum"' in editor.text and 'id="kp-detail"' in editor.text
    assert "Element oder Kapitel wählen" in editor.text
    detail = client.get(path)
    assert detail.status_code == 200 and element.kennung in detail.text
    assert ELEMENT_TYPEN[typ].label in detail.text and "Editor in Arbeit" not in detail.text
    for feld, wert in daten.items():
        if isinstance(wert, str):
            assert wert in detail.text
    if typ == "massnahme":
        assert "Prüfen" in detail.text and "Protokollieren" in detail.text
    if typ == "ablauf":
        assert "Pegel steigt" in detail.text and "Alarmieren" in detail.text
    if typ == "ressource":
        assert "Sandsäcke" in detail.text and "100" in detail.text


def test_phase_crud_und_zuordnungen_loeschen(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    assert client.get(path + "/phasen").status_code == 200
    assert post(client, path + "/phasen", name="Vorwarnung", farbe="gelb", sort=20).status_code == 200
    phase = objekte(umgebung, KatplanPhase)[0]
    assert post(client, path + "/phasen", name="Alarm", farbe="rot", sort=10).status_code == 200
    response = client.get(path + "/phasen")
    assert response.text.index("Alarm") < response.text.index("Vorwarnung")
    assert post(client, path + f"/phasen/{phase.id}", name="Entwarnung", farbe="gruen", sort=30,
                beschreibung="Ende", ausloeser="Pegel sinkt").status_code == 200
    phase = objekte(umgebung, KatplanPhase)[0]
    assert (phase.name, phase.farbe, phase.sort) == ("Entwarnung", "gruen", 30)
    for form in ({"name": "", "farbe": "gelb"}, {"name": "x" * 121},
                 {"name": "Ungültig", "farbe": "pink"}, {"name": "Ungültig", "sort": "kaputt"}):
        assert post(client, path + "/phasen", **form).status_code == 422
        assert post(client, path + f"/phasen/{phase.id}", **form).status_code == 422
    element = anlegen(client, umgebung)
    assert post(client, path + f"/elemente/{element.id}", **element_form(element, phasen=[phase.id])).status_code == 200
    assert post(client, path + f"/phasen/{phase.id}/loeschen").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.get(KatplanPhase, phase.id) is None
        assert db.query(KatplanElementPhase).filter_by(element_id=element.id).count() == 0
    assert post(client, path + f"/elemente/{element.id}/loeschen").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.query(KatplanAenderung).filter_by(element_id=element.id, aktion="geloescht").count() == 1


def test_baum_sortierung_beliebige_tiefe_und_zyklen(client, umgebung):
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        a = KatplanAbschnitt(org_id=plan.org_id, plan_id=plan.id, titel="A", sort=20)
        b = KatplanAbschnitt(org_id=plan.org_id, plan_id=plan.id, titel="B", sort=10)
        db.add_all([a, b])
        db.flush()
        parent = a
        for i in range(15):
            kind = KatplanAbschnitt(org_id=plan.org_id, plan_id=plan.id, titel=str(i), parent_id=parent.id)
            db.add(kind)
            db.flush()
            parent = kind
        baum = abschnitte_baum(db, plan)
        assert [k["abschnitt"].titel for k in baum[:-1]] == ["B", "A"]
        assert baum[-1]["abschnitt"] is None
        a.parent_id = parent.id
        db.flush()
        baum = abschnitte_baum(db, plan)
        def zaehle(knoten):
            return sum((1 if k["abschnitt"] else 0) + zaehle(k["kinder"]) for k in knoten)
        assert zaehle(baum) == 17
        db.rollback()
