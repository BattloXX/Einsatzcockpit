"""K6b: Anhangsverwaltung, Lesesichten und historische Auslieferung."""

import re
from html.parser import HTMLParser

import pytest

from app.core.icons import render_icon
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAbschnitt, KatplanAnhang, KatplanElement, KatplanPlan, KatplanVersion
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt, ObjektDokument
from app.models.user import Role, User, UserRole
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import anlegen, basis, element_form, objekte
from tests.test_katplan_k3a import lade_plan
from tests.test_katplan_k6a import pdf, storage, upload  # noqa: F401


class Bereiche(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form = False
        self.ids = []
        self.felder = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            assert not self.form, "Verschachtelte Formulare"
            self.form = True
        if "data-kp-anhaenge" in attrs:
            assert not self.form, "Anhangsverwaltung im Hauptformular"
        if tag == "input":
            self.felder.append(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])

    def handle_endtag(self, tag):
        if tag == "form":
            assert self.form
            self.form = False


def freigeben(client, info):
    login(client, info, "katplan_freigeber")
    response = post(client, basis(info) + "/freigeben")
    assert response.status_code == 200, response.text
    return lade_plan(info).aktuelle_version_id


def test_elementformular_upload_version_und_baum(client, umgebung):
    anhang_badge = f'{render_icon("paperclip", size="sm")} 1'
    login(client, umgebung)
    element = anlegen(client, umgebung, typ="info")
    path = basis(umgebung) + f"/elemente/{element.id}"
    page = client.get(path)
    parser = Bereiche()
    parser.feed(page.text)
    assert not parser.form
    assert f"kp-anhaenge-element-{element.id}" in parser.ids
    assert any("multiple" in f and f.get("accept") ==
               "image/*,application/pdf,.docx,.xlsx,.odt,.ods,.txt" for f in parser.felder)
    assert any(f.get("capture") == "environment" for f in parser.felder)
    response = upload(client, umgebung, element_id=element.id, beschriftung="Brückenfoto")
    assert response.status_code == 200
    a = objekte(umgebung, KatplanAnhang)[0]
    assert f'/anhang/{a.uuid}/thumb' in response.text
    assert re.search(r'id="kp-element-version" value="1" hx-swap-oob="outerHTML"', response.text)
    assert anhang_badge in response.text
    element = objekte(umgebung, KatplanElement)[0]
    assert post(client, path, **element_form(element, titel="Gespeichert")).status_code == 200
    assert anhang_badge in client.get(basis(umgebung) + "/bearbeiten").text
    assert post(client, basis(umgebung) + f"/anhaenge/{a.uuid}", beschriftung="Bild neu",
                urheber="Anna", aufnahme_datum="2026-10-19", vertraulich="true").status_code == 200
    assert "Bild neu" in client.get(path).text
    response = post(client, basis(umgebung) + f"/anhaenge/{a.uuid}/loeschen")
    assert response.status_code == 200 and anhang_badge not in response.text


def test_uploadfehler_im_fragment(client, umgebung, monkeypatch):
    from app.config import settings

    login(client, umgebung)
    element = anlegen(client, umgebung, typ="info")
    response = upload(client, umgebung, b"invalid", "bild.png", "image/png", element_id=element.id)
    assert response.status_code == 422
    assert 'role="alert"' in response.text and f'kp-anhaenge-element-{element.id}' in response.text
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES_IMAGE", 8)
    response = upload(client, umgebung, element_id=element.id)
    assert response.status_code == 413 and 'role="alert"' in response.text
    from fastapi import HTTPException
    from app.services import katplan_anhang_service

    def nicht_unterstuetzt(*args, **kwargs):
        raise HTTPException(415, "Format nicht unterstützt.")

    monkeypatch.setattr(katplan_anhang_service, "anhang_hochladen", nicht_unterstuetzt)
    response = upload(client, umgebung, element_id=element.id)
    assert response.status_code == 415 and 'role="alert"' in response.text
    assert "Format nicht unterstützt." in response.text


def test_lesesicht_ebenen_vertraulich_und_vergleich(client, umgebung):
    login(client, umgebung)
    path = basis(umgebung)
    assert post(client, path + "/abschnitte", titel="Kapitel Nord").status_code == 200
    kapitel = objekte(umgebung, KatplanAbschnitt)[0]
    element = anlegen(client, umgebung, typ="info", abschnitt_id=kapitel.id)
    assert upload(client, umgebung, element_id=element.id, beschriftung="Galeriefoto").status_code == 200
    assert upload(client, umgebung, pdf(), "ablauf.pdf", "application/pdf", element_id=element.id,
                  beschriftung="Dateidownload").status_code == 200
    assert upload(client, umgebung, beschriftung="Geheime Aufnahme", vertraulich="true").status_code == 200
    assert post(client, path + "/anhaenge", url="https://example.org/kapitel", abschnitt_id=kapitel.id,
                beschriftung="Kapitelunterlage").status_code == 200
    assert post(client, path + "/anhaenge", url="https://example.org/plan", beschriftung="Planunterlage").status_code == 200
    arbeitsstand = client.get(path + "?ansicht=arbeitsstand").text
    assert "Galeriefoto" in arbeitsstand and "Dateidownload" in arbeitsstand
    kapitel_form = client.get(path + f"/abschnitte/{kapitel.id}").text
    parser = Bereiche()
    parser.feed(kapitel_form)
    assert f"kp-anhaenge-kapitel-{kapitel.id}" in parser.ids
    assert "Kapitelunterlage" in kapitel_form and "Planunterlage" not in kapitel_form
    v1 = freigeben(client, umgebung)
    login(client, umgebung, "readonly")
    page = client.get(path)
    assert page.status_code == 200
    html = page.text
    assert "Galeriefoto" in html and 'data-kp-lightbox' in html and " download>" in html
    assert "Dateidownload" in html and " KB" in html
    assert "Geheime Aufnahme" not in html
    assert html.index('id="kp-unterlagen"') < html.index("Planunterlage") < html.index('class="kp-planinhalt"')
    assert html.index("Kapitel Nord</h2>") < html.index("Kapitelunterlage") < html.index('data-element-id=')
    assert 'rel="noopener noreferrer" target="_blank"' in html
    login(client, umgebung)
    assert post(client, path + "/anhaenge", url="https://example.org/neu").status_code == 200
    vergleich = client.get(path + f"/vergleich?von={v1}&nach=arbeitsstand")
    assert vergleich.status_code == 200 and "Anhänge" in vergleich.text
    secret = next(a for a in objekte(umgebung, KatplanAnhang) if a.vertraulich)
    assert post(client, path + f"/anhaenge/{secret.uuid}", beschriftung="Vertrauliche Änderung",
                vertraulich="true").status_code == 200
    v2 = freigeben(client, umgebung)
    login(client, umgebung, "readonly")
    vergleich = client.get(path + f"/vergleich?von={v1}&nach={v2}")
    assert vergleich.status_code == 200
    assert "Geheime Aufnahme" not in vergleich.text and "Vertrauliche Änderung" not in vergleich.text


@pytest.fixture
def dokumente(umgebung):
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").one_or_none()
        vorher = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="objekt_module_enabled")
            db.add(flag)
        flag.value = "true"
        db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().objekt_module_enabled = True
        fremd_id = db.query(FireDept.id).filter(FireDept.id != umgebung["org_id"]).first()[0]
        a = Objekt(org_id=umgebung["org_id"], name="Schule Nord")
        b = Objekt(org_id=fremd_id, name="Schule Fremd")
        db.add_all([a, b])
        db.flush()
        docs = [ObjektDokument(org_id=a.org_id, objekt_id=a.id, dateiname_original="Schule.pdf", pfad="a", seitenzahl=5),
                ObjektDokument(org_id=b.org_id, objekt_id=b.id, dateiname_original="Schule-Fremd.pdf", pfad="b", seitenzahl=8),
                ObjektDokument(org_id=a.org_id, objekt_id=a.id, dateiname_original="Schule-Alt.pdf", pfad="c", seitenzahl=3,
                               ist_aktuelle_version=False)]
        db.add_all(docs)
        db.commit()
        did = docs[0].id
    yield did
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").one()
        if vorher is None:
            db.delete(flag)
        else:
            flag.value = vorher
        db.commit()


def test_objektdokument_suche_und_leserecht(client, umgebung, dokumente):
    login(client, umgebung)
    path = basis(umgebung)
    anlegen(client, umgebung, typ="info")
    suche = path + "/objektdokumente/suche?q=Schule"
    page = client.get(suche)
    assert page.status_code == 200
    assert "Schule Nord" in page.text and "Schule.pdf" in page.text and "5 Seiten" in page.text
    assert "Schule Fremd" not in page.text and "Schule-Fremd.pdf" not in page.text and "Schule-Alt.pdf" not in page.text
    assert post(client, path + "/anhaenge", objekt_dokument_id=dokumente, seiten="2-4").status_code == 200
    a = objekte(umgebung, KatplanAnhang)[0]
    freigeben(client, umgebung)
    # Katplan-Verwalter hat kein Objekt-Leserecht; readonly hat es im Bestand.
    login(client, umgebung)
    page = client.get(path + "?ansicht=freigabe")
    assert "Objektdokument (keine Berechtigung)" in page.text
    assert f'href="/plaene/anhang/{a.uuid}' not in page.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=umgebung["users"]["katplan_verwalter"]).one()
        role = db.query(Role).filter_by(code="objekt_verwalter").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    login(client, umgebung)
    assert "Objektdokument Schule.pdf, S. 2–4" in client.get(path + "?ansicht=freigabe").text
    login(client, umgebung, "readonly")
    assert "Objektdokument Schule.pdf, S. 2–4" in client.get(path).text
    login(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().objekt_module_enabled = False
        db.commit()
    assert "Schule.pdf" not in client.get(suche).text


def test_historische_urls_und_planbindung(client, umgebung):
    login(client, umgebung)
    element = anlegen(client, umgebung, typ="info")
    assert upload(client, umgebung, element_id=element.id).status_code == 200
    a = objekte(umgebung, KatplanAnhang)[0]
    v1 = freigeben(client, umgebung)
    login(client, umgebung)
    assert post(client, basis(umgebung) + f"/anhaenge/{a.uuid}/loeschen").status_code == 200
    freigeben(client, umgebung)
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        other = KatplanPlan(org_id=umgebung["org_id"], titel="Anderer Plan")
        db.add(other)
        db.flush()
        version = KatplanVersion(org_id=other.org_id, plan_id=other.id, versionsnummer=1,
                                plan_json='{}', inhalt_hash="0" * 64, anzahl_elemente=0)
        db.add(version)
        db.commit()
        fremde_vid = version.id
    login(client, umgebung, "readonly")
    url = f"/plaene/anhang/{a.uuid}"
    assert client.get(url + f"?version_id={v1}").status_code == 200
    assert client.get(url + f"/thumb?version_id={v1}").status_code == 200
    assert client.get(url).status_code == 404
    assert client.get(url + f"?version_id={fremde_vid}").status_code == 404
    page = client.get(basis(umgebung) + f"/versionen/{v1}")
    assert page.status_code == 200
    assert f'href="{url}?version_id={v1}"' in page.text
    assert f'src="{url}/thumb?version_id={v1}"' in page.text
