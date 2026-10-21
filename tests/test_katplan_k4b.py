"""K4b: Verknuepfungsoberflaeche, Telefonrechte und HTMX-Versionen."""

import re
from html.parser import HTMLParser
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanElement, KatplanLink, KatplanPlan, KatplanRelation
from app.models.user import User
from app.models.kontakt import Kontakt, KontaktTelefon
from app.models.master import OrgSettings, SystemSettings
from app.services import katplan_freigabe_service
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form


@pytest.fixture
def verknuepfungen(client, umgebung):
    login(client, umgebung, "katplan_freigeber")
    element = anlegen(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        einstellungen = db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one()
        einstellungen.kontakte_module_enabled = True
        flag = db.query(SystemSettings).filter_by(key="kontakte_module_enabled").one_or_none()
        vorher = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="kontakte_module_enabled")
            db.add(flag)
        flag.value = "true"
        kontakte = [Kontakt(org_id=umgebung["org_id"], anzeigename="Anna Bauhof", einsatzrelevant=True),
                    Kontakt(org_id=umgebung["org_id"], anzeigename="Berta Privat", einsatzrelevant=False)]
        db.add_all(kontakte)
        db.flush()
        for index, kontakt in enumerate(kontakte):
            kontakt.telefone = [KontaktTelefon(org_id=umgebung["org_id"], nummer=f"+4366400{index}1", verwendung="dienst"),
                                KontaktTelefon(org_id=umgebung["org_id"], nummer=f"+4366400{index}2", verwendung="privat")]
        db.commit()
        ids = [k.id for k in kontakte]
    yield umgebung, element, ids
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="kontakte_module_enabled").one()
        if vorher is None:
            db.delete(flag)
        else:
            flag.value = vorher
        db.commit()


def link(client, info, element, kontakt_id, **werte):
    return post(client, basis(info) + (f"/elemente/{element.id}" if element else "") + "/links",
                ziel_typ="kontakt", ziel_id=kontakt_id, rolle="zu_verstaendigen", **werte)


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tiefe = 0
        self.anzahl = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            assert self.tiefe == 0, "Verschachtelte Formulare"
            self.tiefe += 1
            self.anzahl += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.tiefe -= 1
            assert self.tiefe == 0


def test_suche_modul_und_formulare(client, verknuepfungen):
    info, element, ids = verknuepfungen
    path = basis(info)
    suche = path + f"/verknuepfungen/suche?ziel_typ=kontakt&element_id={element.id}&q=Anna"
    antwort = client.get(suche)
    assert antwort.status_code == 200
    assert "Anna Bauhof" in antwort.text and "+4366400" not in antwort.text
    assert "Verknüpfen" in antwort.text and 'name="rolle"' in antwort.text
    assert "mindestens 2 Zeichen" in client.get(suche.replace("q=Anna", "q=A")).text
    assert link(client, info, element, ids[0]).status_code == 200
    page = client.get(path + f"/elemente/{element.id}")
    parser = FormParser()
    parser.feed(page.text)
    assert parser.anzahl >= 3 and parser.tiefe == 0
    assert page.text.index(">Phasen<") < page.text.index(">Verknüpfungen<") < page.text.index(">Typspezifische Angaben<")
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        db.query(OrgSettings).filter_by(org_id=info["org_id"]).one().kontakte_module_enabled = False
        db.commit()
    page = client.get(path + f"/elemente/{element.id}")
    assert '<option value="kontakt">' not in page.text
    assert "Anna Bauhof" not in client.get(suche).text


def test_link_version_oob_und_speichern(client, verknuepfungen):
    info, element, ids = verknuepfungen
    response = link(client, info, element, ids[0], hinweis="Sofort anrufen")
    assert response.status_code == 200 and "Anna Bauhof" in response.text
    assert "Sofort anrufen" in response.text
    version = int(re.search(r'id="kp-element-version" value="(\d+)" hx-swap-oob="outerHTML"', response.text)[1])
    assert version == element.version + 1
    assert post(client, basis(info) + f"/elemente/{element.id}", **element_form(element, version=version)).status_code == 200
    duplicate = link(client, info, element, ids[0])
    assert duplicate.status_code == 422 and "existiert bereits" in duplicate.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        lid = db.query(KatplanLink).filter_by(element_id=element.id).one().id
    deleted = post(client, basis(info) + f"/links/{lid}/loeschen")
    assert deleted.status_code == 200 and 'hx-swap-oob="outerHTML"' in deleted.text
    assert "Anna Bauhof" not in deleted.text


def test_freigabe_telefone_liveaenderung_und_snapshot(client, verknuepfungen):
    info, element, ids = verknuepfungen
    for kontakt_id in ids:
        assert link(client, info, element, kontakt_id, hinweis="Rufbereitschaft").status_code == 200
    assert post(client, basis(info) + "/freigeben").status_code == 200
    login(client, info, "readonly")
    page = client.get(basis(info))
    assert 'href="tel:+436640001"' in page.text
    for nummer in ("+436640002", "+436640011", "+436640012"):
        assert nummer not in page.text
    assert "Rufbereitschaft" in page.text
    login(client, info, "org_admin")
    page = client.get(basis(info) + "?ansicht=freigabe")
    for nummer in ("+436640001", "+436640002", "+436640011", "+436640012"):
        assert f'href="tel:{nummer}"' in page.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        db.get(Kontakt, ids[0]).anzeigename = "Anna Neu"
        db.commit()
    login(client, info, "readonly")
    page = client.get(basis(info))
    assert "Anna Neu" in page.text and "geändert seit Freigabe" in page.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        db.get(Kontakt, ids[0]).archiviert = True
        db.commit()
    page = client.get(basis(info))
    assert "Anna Bauhof" in page.text and "nicht mehr vorhanden" in page.text
    assert "+436640001" not in page.text
    login(client, info)
    assert "Verknüpfungsziel fehlt" in client.get(basis(info) + "/bearbeiten").text


def test_beziehungen_anderer_plan_und_fremde_org(client, verknuepfungen):
    info, element, _ = verknuepfungen
    with TestingSession() as db:
        set_tenant_context(db, None)
        anderer = KatplanPlan(org_id=info["org_id"], titel="Nachbarplan")
        fremd_org = db.query(OrgSettings).filter(OrgSettings.org_id != info["org_id"]).first().org_id
        fremder = KatplanPlan(org_id=fremd_org, titel="Fremdplan")
        db.add_all([anderer, fremder])
        db.flush()
        ziel = KatplanElement(org_id=info["org_id"], plan_id=anderer.id, titel="Zielbrücke", typ="info", kennung="IN-01")
        fremdes = KatplanElement(org_id=fremd_org, plan_id=fremder.id, titel="Geheimziel", typ="info")
        db.add_all([ziel, fremdes])
        db.commit()
        ziel_uuid, ziel_plan, fremd_uuid = ziel.uuid, anderer.id, fremdes.uuid
    path = basis(info) + f"/elemente/{element.id}/beziehungen"
    suche = client.get(path + "/suche?q=brücke")
    assert "Zielbrücke" in suche.text and "Nachbarplan" in suche.text
    assert "Brücke</strong>" not in suche.text
    response = post(client, path, zu_element_uuid=ziel_uuid, art="siehe_auch", hinweis="Nachsehen")
    assert response.status_code == 200 and "Nachbarplan" in response.text
    assert 'id="kp-element-version"' in response.text
    page = client.get(basis(info))
    assert f'href="/plaene/{ziel_plan}#element-{ziel_uuid}"' in page.text
    assert f'id="element-{element.uuid}"' in page.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        user = db.query(User).filter_by(username=info["users"]["katplan_freigeber"]).one()
        katplan_freigabe_service.freigeben(db, db.get(KatplanPlan, ziel_plan), user)
        db.commit()
    assert post(client, basis(info) + "/freigeben").status_code == 200
    login(client, info, "readonly")
    page = client.get(basis(info))
    assert f'href="/plaene/{ziel_plan}#element-{ziel_uuid}"' in page.text
    assert f'id="element-{element.uuid}"' in page.text
    login(client, info, "katplan_freigeber")
    assert post(client, path, zu_element_uuid=fremd_uuid, art="siehe_auch").status_code in (404, 422)
    assert post(client, path, zu_element_uuid=str(uuid4()), art="siehe_auch").status_code == 422
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        rid = db.query(KatplanRelation).filter_by(von_element_id=element.id).one().id
    removed = post(client, basis(info) + f"/beziehungen/{rid}/loeschen")
    assert removed.status_code == 200 and 'id="kp-element-version"' in removed.text
    assert "Zielbrücke" not in removed.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        db.get(KatplanPlan, ziel_plan).status = "archiviert"
        db.commit()
    assert "Zielbrücke" not in client.get(path + "/suche?q=brücke").text
    assert post(client, path, zu_element_uuid=ziel_uuid, art="folgt_auf").status_code == 422


def test_plan_zustaendigkeit_und_fremde_ids(client, verknuepfungen):
    info, element, ids = verknuepfungen
    response = link(client, info, None, ids[0])
    assert response.status_code == 200 and 'id="kp-plan-links"' in response.text
    assert "Anna Bauhof" in client.get(basis(info)).text
    assert post(client, basis(info) + "/freigeben").status_code == 200
    login(client, info, "readonly")
    page = client.get(basis(info))
    assert "Zuständigkeiten" in page.text and "Anna Bauhof" in page.text
    assert 'href="tel:+436640001"' in page.text
    assert post(client, basis(info) + "/links", ziel_typ="kontakt", ziel_id=ids[0], rolle="info").status_code == 403
    assert client.get(basis(info) + "/verknuepfungen/suche?q=Anna&ziel_typ=kontakt").status_code == 403
    login(client, info)
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd_org = db.query(OrgSettings).filter(OrgSettings.org_id != info["org_id"]).first().org_id
        links = []
        for org_id in (info["org_id"], fremd_org):
            plan = KatplanPlan(org_id=org_id, titel="Fremder Plan")
            db.add(plan)
            db.flush()
            fremd_link = KatplanLink(org_id=org_id, plan_id=plan.id, ziel_typ="kontakt", ziel_id=ids[0], rolle="info")
            db.add(fremd_link)
            db.flush()
            links.append(fremd_link.id)
        db.commit()
    for lid in links:
        assert post(client, basis(info) + f"/links/{lid}/loeschen").status_code == 404
    assert client.get(basis(info) + "/verknuepfungen/suche?element_id=999999&ziel_typ=kontakt&q=Anna").status_code == 404
