"""PR 5b: zentrale Plansuche, Facetten, Dokumentgruppen und Live-Dropdown."""

from uuid import uuid4

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanDokument, KatplanDokumentSeite, KatplanElement, KatplanPlan
from app.models.master import OrgSettings
from app.models.user import User
from app.services import katplan_suche_service as service
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_service import baue_suchtext
from app.services.suche import abfrage
from app.services.suche.indexer import neuaufbau
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _user
from tests.test_katplan_k1b import login, umgebung  # noqa: F401
from tests.test_katplan_k8 import daten, suchen  # noqa: F401


@pytest.fixture
def index(request, monkeypatch):
    info = request.getfixturevalue("daten")
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "SEARCH_SEMANTIC_ENABLED", False)
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=info["org_id"]).one().suche_aktiv = True
        neuaufbau(db, info["org_id"])
        db.commit()
    return info


def test_engine_felder_ranking_und_facetten(index, monkeypatch):
    engine = suchen(index, q="Wasser", plan_id=index["plan_id"])
    assert engine["gesamt"] == 2
    monkeypatch.setattr(settings, "SEARCH_ENABLED", False)
    fallback = suchen(index, q="Wasser", plan_id=index["plan_id"])
    nach_id = {t["element_id"]: t for t in fallback["treffer"]}
    for t in engine["treffer"]:
        alt = nach_id[t["element_id"]]
        assert t.keys() == alt.keys()
        assert {k: v for k, v in t.items() if k not in ("ausschnitt", "ausschnitt_html")} == {
            k: v for k, v in alt.items() if k not in ("ausschnitt", "ausschnitt_html")}
        assert t["ausschnitt_html"] == service.markiere(t["ausschnitt"], "Wasser")
    assert engine["facetten"] == fallback["facetten"] == {
        "typ": {"kontrollstelle": 2}, "phase": {str(index["phase_id"]): 1},
        "plan": {index["plan_id"]: 2},
    }
    assert len(engine["gruppen"]) == 1
    assert len(engine["gruppen"][0]["elemente"]) == 2
    assert not engine["gruppen"][0]["seiten"]


@pytest.mark.parametrize("filter", [
    {"arbeitsstand": True}, {"ort": "Wolfurt"}, {"nur_geprueft": True},
    {"lat": 47, "lng": 9, "radius_m": 1000}, {"q": ""},
])
def test_fallback_ohne_engine(index, monkeypatch, filter):
    def verboten(*args, **kwargs):
        pytest.fail("Engine darf fuer diesen Filter nicht verwendet werden")
    monkeypatch.setattr(abfrage, "suche", verboten)
    assert suchen(index, "katplan_verwalter", **{"q": "Wasser", "plan_id": index["plan_id"], **filter})["gesamt"]


@pytest.mark.parametrize("aktiv,system", [(False, True), (True, False)])
def test_org_und_systemschalter(index, monkeypatch, aktiv, system):
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=index["org_id"]).one().suche_aktiv = aktiv
        db.commit()
    monkeypatch.setattr(settings, "SEARCH_ENABLED", system)
    monkeypatch.setattr(abfrage, "suche", lambda *a, **kw: pytest.fail("Engine ist deaktiviert"))
    assert suchen(index, q="Wasser")["gesamt"] == 2


def test_engine_anfrage_filter_und_gefahr(index, monkeypatch):
    original = abfrage.suche
    anfragen = []
    def beobachte(db, user, anfrage):
        anfragen.append(anfrage)
        return original(db, user, anfrage)
    monkeypatch.setattr(abfrage, "suche", beobachte)
    result = suchen(index, q="Wasser", plan_id=index["plan_id"], typ="kontrollstelle",
                    prioritaet=1, phase=str(index["phase_id"]), gefahr="hochwasser")
    assert result["gesamt"] == 1
    assert anfragen[0].limit == 100
    assert anfragen[0].plan_id == index["plan_id"]
    assert anfragen[0].typen == ["katplan_element", "katplan_seite"]
    assert anfragen[0].filter == {"typ": "kontrollstelle", "prioritaet": 1, "phase_id": str(index["phase_id"])}
    assert suchen(index, q="Wasser", gefahr="sturm")["gesamt"] == 0
    assert suchen(index, "fremd", q="Wasser", plan_id=index["plan_id"])["gesamt"] == 0
    assert suchen(index, q="Geheimtext")["gesamt"] == 0
    assert suchen(index, "incident_leader", q="Geheimtext")["gesamt"] == 1


@pytest.mark.parametrize("engine", [False, True])
def test_seiten_zusammenfassung_und_links(client, index, monkeypatch, engine):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, index["plan_id"])
        dok = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="Pegelbericht",
                             dateiname_original="pegel.pdf", pfad="pegel.pdf", groesse_bytes=1,
                             seitenzahl=7, status="fertig")
        db.add(dok)
        db.flush()
        for nr in (7, 3, 4):
            db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=dok.id,
                                       seiten_nr=nr, einzel_pdf_pfad="seite.pdf", volltext="Wasserstand steigt"))
        plan.status = "in_ueberarbeitung"
        user = db.query(User).filter_by(username=index["users"]["katplan_freigeber"]).one()
        freigeben(db, plan, user)
        neuaufbau(db, plan.org_id)
        db.commit()
        did, vid = dok.id, plan.aktuelle_version_id
    monkeypatch.setattr(settings, "SEARCH_ENABLED", engine)
    result = suchen(index, q="Wasser", limit=4)
    assert result["gesamt"] == 5 and result["abgeschnitten"]
    assert result["facetten"]["plan"] == {index["plan_id"]: 5}
    result = suchen(index, q="Wasser")
    gruppe = result["gruppen"][0]
    assert len(gruppe["elemente"]) == 2 and len(gruppe["seiten"]) == 1
    dokument = gruppe["seiten"][0]
    assert dokument["titel"] == "Pegelbericht, Seiten 3, 4, 7"
    assert [s["seiten_nr"] for s in dokument["seiten"]] == [3, 4, 7]
    assert dokument["url"] == f'/plaene/{index["plan_id"]}/quellen/{did}?seite=3&version_id={vid}'
    login(client, index, "readonly")
    html = client.get("/plaene/suche?q=Wasser", headers={"HX-Request": "true"})
    assert html.status_code == 200
    assert "Pegelbericht, Seiten 3, 4, 7" in html.text
    assert 'data-kp-facette="typ"' in html.text and 'data-kp-facette="plan_id"' in html.text
    assert 'Seite 7</a>' in html.text and "<mark>Wasser" in html.text
    dropdown = client.get(f'/plaene/{index["plan_id"]}/suche-treffer?q=Wasserstand')
    assert dropdown.status_code == 200
    assert f'/quellen/{did}?seite=' in dropdown.text
    wissen = client.get('/einsatzwissen?q=Wasserstand')
    assert wissen.status_code == 200
    assert f'/quellen/{did}?seite=7&amp;version_id={vid}' in wissen.text
    assert 'Dokumentseite · Seite 7' in wissen.text


def test_dropdown_rechte_arbeitsstand_und_limit(client, index):
    url = f'/plaene/{index["plan_id"]}/suche-treffer'
    login(client, index, "readonly")
    response = client.get(url + "?q=Wasser")
    assert response.status_code == 200
    assert f'#element-{index["uuid"]}' in response.text
    assert "Vorwarnung" in response.text and "<mark>" in response.text
    assert "Geheimtext" not in client.get(url + "?q=Geheimtext").text
    assert client.get(url + "?q=%3Cb%3E").status_code == 200
    assert client.get(url + "?q= ").text.strip() == ""
    assert 'hx-trigger="input changed delay:300ms"' in client.get(f'/plaene/{index["plan_id"]}').text
    login(client, index, "fremd")
    assert client.get(url + "?q=Wasser").status_code == 404
    with TestingSession() as db:
        set_tenant_context(db, None)
        name = "suche-ohne-rolle-" + uuid4().hex
        _user(db, name, index["org_id"], "fahrtenbuch_leser")
        index["users"]["ohne"] = name
        for nr in range(12):
            e = KatplanElement(org_id=index["org_id"], plan_id=index["plan_id"], kennung=f"I-{nr}",
                               titel=f"Entwurfswort {nr}", typ="info")
            e.suchtext = baue_suchtext(e, [])
            db.add(e)
        db.commit()
    login(client, index, "ohne")
    assert client.get(url + "?q=Wasser", follow_redirects=False).status_code == 403
    login(client, index)
    arbeit = client.get(url + "?q=Entwurfswort&ansicht=arbeitsstand")
    assert arbeit.status_code == 200 and arbeit.text.count('<li>') == 8
    assert '?ansicht=arbeitsstand#element-' in arbeit.text
    assert 'Entwurfswort' not in client.get(url + "?q=Entwurfswort").text
    login(client, index, "readonly")
    assert 'Entwurfswort' not in client.get(url + "?q=Entwurfswort&ansicht=arbeitsstand").text


def test_gruppierung_mehrere_plaene():
    treffer = []
    for pid in (1, 2, 1):
        treffer.append({"art": "element", "plan_id": pid, "plan_titel": f"Plan {pid}",
                        "typ": "info", "ausschnitt": "Alarm", "phase_ids": []})
    result = service._ergebnis(treffer, 100, "Alarm")
    assert [g["plan_id"] for g in result["gruppen"]] == [1, 2]
    assert [len(g["elemente"]) for g in result["gruppen"]] == [2, 1]
    assert result["facetten"]["plan"] == {1: 2, 2: 1}
