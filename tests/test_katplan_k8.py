"""K8: Freigabesuche, Rechte, Filter, Geometrien und sichere HTMX-Ausgabe."""

import json
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanDokument, KatplanDokumentSeite, KatplanElement, KatplanElementPhase,
    KatplanPhase, KatplanPlan,
)
from app.models.user import User
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_service import baue_suchtext
from app.services.katplan_suche_service import markiere, suche
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _user
from tests.test_katplan_k1b import login, umgebung  # noqa: F401


@pytest.fixture
def daten(umgebung):
    with TestingSession() as db:
        set_tenant_context(db, None)
        leiter = "leitung-" + uuid4().hex
        _user(db, leiter, umgebung["org_id"], "incident_leader")
        umgebung["users"]["incident_leader"] = leiter
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        phase = KatplanPhase(org_id=plan.org_id, plan_id=plan.id, name="Vorwarnung")
        db.add(phase)
        db.flush()
        elemente = []
        for kennung, titel, beschreibung, typ, prio, geheim, lat in (
            ("KS-01", "Wasser Brücke", "Alter Inhalt mit 100% und A_B", "kontrollstelle", 1, False, 47.0027),
            ("KS-02", "Wasser Ferne", "Nur Wasser", "kontrollstelle", 2, False, 47.027),
            ("IN-01", "Vertrauliche Maßnahme", "Geheimtext", "info", 2, True, None),
        ):
            e = KatplanElement(org_id=plan.org_id, plan_id=plan.id, kennung=kennung, titel=titel,
                               beschreibung_html=f"<p>{beschreibung}</p>", typ=typ, prioritaet=prio,
                               vertraulich=geheim, lat=lat, lng=9 if lat else None,
                               ort="Wolfurt" if prio == 1 else "Bregenz", plz="6922", strasse="Dorfstraße",
                               geo_status="geprueft" if prio == 1 else "ungeprueft")
            db.add(e)
            e.suchtext = baue_suchtext(e, [])
            elemente.append(e)
        db.flush()
        db.add(KatplanElementPhase(org_id=plan.org_id, element_id=elemente[0].id, phase_id=phase.id))
        bearbeiter = db.query(User).filter_by(username=umgebung["users"]["katplan_freigeber"]).one()
        version = freigeben(db, plan, bearbeiter)
        db.commit()
        return {**umgebung, "element_id": elemente[0].id, "uuid": elemente[0].uuid,
                "phase_id": phase.id, "version_id": version.id}


def suchen(info, rolle="readonly", **kwargs):
    with TestingSession() as db:
        # Absichtlich ohne Tenant-Listener: der Service muss selbst scopen.
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=info["users"][rolle]).one()
        return suche(db, user, **kwargs)


def test_freigabe_arbeitsstand_und_snapshot_kopf(daten):
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.get(KatplanElement, daten["element_id"])
        e.beschreibung_html = "<p>Neuer Inhalt</p>"
        e.suchtext = baue_suchtext(e, [])
        p = db.get(KatplanPlan, daten["plan_id"])
        p.titel = "Unveröffentlichter Titel"
        p.gefahrenarten_json = '["sturm"]'
        db.commit()
    for rolle in ("readonly", "katplan_verwalter"):
        alt = suchen(daten, rolle, q="alter")
        assert alt["gesamt"] == 1 and alt["treffer"][0]["plan_titel"] == daten["titel"]
        assert alt["treffer"][0]["url"] == f'/plaene/{daten["plan_id"]}#element-{daten["uuid"]}'
        assert suchen(daten, rolle, q="neuer")["gesamt"] == 0
        assert suchen(daten, rolle, q="alter", gefahr="hochwasser")["gesamt"] == 1
        assert suchen(daten, rolle, q="alter", gefahr="sturm")["gesamt"] == 0
    assert suchen(daten, q="neuer", arbeitsstand=True)["gesamt"] == 0
    arbeit = suchen(daten, "katplan_verwalter", q="neuer", arbeitsstand=True)
    assert arbeit["gesamt"] == 1
    assert arbeit["treffer"][0]["url"].endswith(f'/bearbeiten?element={daten["element_id"]}')
    assert suchen(daten, "katplan_verwalter", q="neuer", arbeitsstand=True, gefahr="sturm")["gesamt"] == 1


@pytest.mark.parametrize("rolle,anzahl", [("readonly", 0), ("incident_leader", 1), ("katplan_verwalter", 1)])
def test_vertrauliche_elemente(daten, rolle, anzahl):
    assert suchen(daten, rolle, q="geheimtext")["gesamt"] == anzahl


def test_fremde_org_archiv_und_entwurf(daten):
    assert suchen(daten, "fremd", q="wasser")["gesamt"] == 0
    assert suchen(daten, "fremd", plan_id=daten["plan_id"], arbeitsstand=True)["gesamt"] == 0
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, daten["plan_id"])
        plan.status = "archiviert"
        db.commit()
    for rolle in ("readonly", "katplan_verwalter"):
        assert suchen(daten, rolle, q="wasser", arbeitsstand=True)["gesamt"] == 0
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, daten["plan_id"])
        plan.status, plan.aktuelle_version_id = "entwurf", None
        db.commit()
    assert suchen(daten, q="wasser")["gesamt"] == 0
    assert suchen(daten, "katplan_verwalter", q="wasser")["gesamt"] == 0
    assert suchen(daten, "katplan_verwalter", q="wasser", arbeitsstand=True)["gesamt"] == 2


def test_und_sonderzeichen_leere_suche_und_limit(daten):
    assert suchen(daten, q="WASSER brücke")["gesamt"] == 1
    assert suchen(daten, q="wasser unbekannt")["gesamt"] == 0
    for q in ("%", "_", "100%", "a_b"):
        assert suchen(daten, q=q)["gesamt"] == 1
    assert suchen(daten, q="a%b")["gesamt"] == 0
    assert suchen(daten, q="   ") == {"treffer": [], "gesamt": 0, "abgeschnitten": False}
    result = suchen(daten, q="wasser", limit=1)
    assert result["gesamt"] == 2 and result["abgeschnitten"] and len(result["treffer"]) == 1
    assert result["treffer"][0]["prioritaet"] == 1


@pytest.mark.parametrize("filter,anzahl", [
    ({"typ": "kontrollstelle"}, 2), ({"typ": "massnahme"}, 0),
    ({"prioritaet": 1}, 1), ({"prioritaet": 3}, 0),
    ({"gefahr": "hochwasser"}, 2), ({"gefahr": "sturm"}, 0),
    ({"ort": "WOLF"}, 1), ({"ort": "6922"}, 2), ({"ort": "Dorf"}, 2),
    ({"ort": "%"}, 0), ({"nur_geprueft": True}, 1),
])
@pytest.mark.parametrize("arbeit", [False, True])
def test_filter(daten, filter, anzahl, arbeit):
    assert suchen(daten, "katplan_verwalter", arbeitsstand=arbeit, q="wasser", **filter)["gesamt"] == anzahl


@pytest.mark.parametrize("arbeit", [False, True])
def test_phase(daten, arbeit):
    assert suchen(daten, "katplan_verwalter", arbeitsstand=arbeit, plan_id=daten["plan_id"],
                  phase=str(daten["phase_id"]))["gesamt"] == 1
    assert suchen(daten, "katplan_verwalter", arbeitsstand=arbeit, plan_id=daten["plan_id"],
                  phase="99999999")["gesamt"] == 0


def test_umkreis_punkt_linie_und_sortierung(daten):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, daten["plan_id"])
        # Repraesentativer Punkt weit entfernt; die Linie verlaeuft nahe am Suchpunkt.
        linie = KatplanElement(org_id=plan.org_id, plan_id=plan.id, kennung="GB-1", titel="Nahe Linie",
                               typ="gefahrenbereich", lat=48, lng=10, geo_status="geprueft",
                               geometry_json=json.dumps({"type": "LineString", "coordinates": [
                                   [8.98, 47.001], [9.02, 47.001]]}))
        db.add(linie)
        db.flush()
        linie.suchtext = baue_suchtext(linie, [])
        plan.status = "in_ueberarbeitung"
        user = db.query(User).filter_by(username=daten["users"]["katplan_freigeber"]).one()
        freigeben(db, plan, user)
        db.commit()
    for arbeit in (False, True):
        treffer = suchen(daten, "katplan_verwalter", lat=47, lng=9, radius_m=1000, arbeitsstand=arbeit)["treffer"]
        assert [t["titel"] for t in treffer] == ["Nahe Linie", "Wasser Brücke"]
        assert 80 < treffer[0]["distanz_m"] < 150
        assert 250 < treffer[1]["distanz_m"] < 350
    for werte in ({"lat": 47}, {"lat": 47, "lng": 9, "radius_m": 50001},
                  {"lat": float("nan"), "lng": 9, "radius_m": 1000}):
        with pytest.raises(ValueError):
            suchen(daten, **werte)


def test_dokumentseiten_nur_sichtbare_quellen(client, daten):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, daten["plan_id"])
        doks = []
        for titel in ("Freigegebene Quelle", "Geheime Quelle"):
            dok = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel=titel,
                                 dateiname_original="quelle.pdf", pfad="quelle.pdf", groesse_bytes=1,
                                 seitenzahl=1, status="fertig")
            db.add(dok)
            db.flush()
            db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=dok.id,
                                       seiten_nr=1, einzel_pdf_pfad="seite.pdf", volltext="Pegelprognose <b> Alarm"))
            doks.append(dok)
        e = db.get(KatplanElement, daten["element_id"])
        e.quelle_dokument_id = doks[0].id
        geheim = db.query(KatplanElement).filter_by(plan_id=plan.id, vertraulich=True).one()
        geheim.quelle_dokument_id = doks[1].id
        # Nur Elementreferenzen, kein planweiter Anhang, erteilen hier Leserechte.
        doks[0].ist_aktuelle_version = doks[1].ist_aktuelle_version = False
        plan.status = "in_ueberarbeitung"
        user = db.query(User).filter_by(username=daten["users"]["katplan_freigeber"]).one()
        version = freigeben(db, plan, user)
        db.commit()
        dok_id, vid = doks[0].id, version.id
    with TestingSession() as db:
        set_tenant_context(db, None)
        dok = KatplanDokument(org_id=daten["org_id"], plan_id=daten["plan_id"], titel="Neue Arbeitsquelle",
                             dateiname_original="neu.pdf", pfad="neu.pdf", groesse_bytes=1, seitenzahl=1)
        db.add(dok)
        db.flush()
        db.add(KatplanDokumentSeite(org_id=daten["org_id"], plan_id=daten["plan_id"], dokument_id=dok.id,
                                   seiten_nr=1, einzel_pdf_pfad="neu-seite.pdf", volltext="Pegelprognose"))
        db.commit()
    login(client, daten, "readonly")
    html = client.get("/plaene/suche", params={"q": "<b>"}, headers={"HX-Request": "true"}).text
    assert "<mark>&lt;b&gt;</mark>" in html and "<b>" not in html
    result = suchen(daten, q="pegelprognose")
    assert result["gesamt"] == 1
    seite = result["treffer"][0]
    assert seite["art"] == "seite" and seite["seiten_nr"] == 1
    assert seite["url"] == f'/plaene/{daten["plan_id"]}/quellen/{dok_id}?seite=1&version_id={vid}'
    assert suchen(daten, "incident_leader", q="pegelprognose")["gesamt"] == 2
    assert suchen(daten, "fremd", q="pegelprognose")["gesamt"] == 0
    assert suchen(daten, "katplan_verwalter", q="pegelprognose")["gesamt"] == 1
    assert suchen(daten, q="pegelprognose", mit_quellen=False)["gesamt"] == 0
    for filter in ({"typ": "info"}, {"prioritaet": 1}, {"ort": "Wolfurt"}, {"nur_geprueft": True},
                   {"phase": str(daten["phase_id"]), "plan_id": daten["plan_id"]}, {"lat": 47, "lng": 9, "radius_m": 1000}):
        assert suchen(daten, q="pegelprognose", **filter)["gesamt"] == 0
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(KatplanPlan, daten["plan_id"]).status = "archiviert"
        db.commit()
    assert suchen(daten, q="pegelprognose")["gesamt"] == 0


def test_route_vollseite_fragment_markierung_und_hinweise(client, daten):
    login(client, daten, "readonly")
    path = "/plaene/suche"
    leer = client.get(path)
    assert leer.status_code == 200 and "Bitte Suchbegriffe" in leer.text
    assert 'name="arbeitsstand"' not in leer.text
    page = client.get(path, params={"q": "wasser", "plan_id": daten["plan_id"]})
    assert page.status_code == 200 and "<mark>wasser</mark>" in page.text
    assert "Vorwarnung" in page.text and 'hx-push-url="true"' in page.text
    fragment = client.get(path + "?q=wasser", headers={"HX-Request": "true"})
    assert fragment.status_code == 200 and '<div id="kp-suche-treffer"' in fragment.text
    assert "<html" not in fragment.text and 'hx-swap-oob="outerHTML"' in fragment.text
    for params in ({"lat": "abc"}, {"plan_id": "abc"}, {"plan_id": "9" * 30}, {"prioritaet": "nan"}, {"phase": "x"},
                   {"lat": "nan", "lng": 9, "radius_m": 1000}, {"lat": 47, "lng": 9, "radius_m": -1}):
        invalid = client.get(path, params=params)
        assert invalid.status_code == 200 and 'role="alert"' in invalid.text
    assert str(markiere('<b> & "Alarm"', '<b>')) == '<mark>&lt;b&gt;</mark> &amp; &quot;Alarm&quot;'
    assert "<b>" not in client.get(path, params={"q": "<b>"}).text
    login(client, daten)
    assert 'name="arbeitsstand"' in client.get(path).text
    for path in ("/plaene", f'/plaene/{daten["plan_id"]}'):
        assert 'action="/plaene/suche"' in client.get(path).text


def test_relevanz_titel_vor_kennung_vor_text(daten):
    with TestingSession() as db:
        set_tenant_context(db, None)
        for titel, kennung, beschreibung, prio in (
            ("Sortierwort im Titel", "IN-02", "Text", 3),
            ("Kennungtreffer", "Sortierwort", "Text", 2),
            ("Texttreffer", "IN-03", "Sortierwort im Text", 1),
        ):
            e = KatplanElement(org_id=daten["org_id"], plan_id=daten["plan_id"], titel=titel,
                               kennung=kennung, beschreibung_html=beschreibung, typ="info", prioritaet=prio)
            db.add(e)
            e.suchtext = baue_suchtext(e, [])
        db.commit()
    treffer = suchen(daten, "katplan_verwalter", q="Sortierwort", arbeitsstand=True)["treffer"]
    assert [t["titel"] for t in treffer] == ["Sortierwort im Titel", "Kennungtreffer", "Texttreffer"]


def test_ausschnitt_in_originalschreibweise():
    from types import SimpleNamespace

    from app.services.katplan_suche_service import _klartext

    element = SimpleNamespace(beschreibung_html="<p>Zu verständigen ist der <b>Bauhof</b></p>",
                              suchtext="m-01 zu verständigen ist der bauhof sandsäcke")
    assert _klartext(element, ["bauhof"]) == "Zu verständigen ist der Bauhof"
    assert _klartext(element, ["sandsäcke"]) == element.suchtext
