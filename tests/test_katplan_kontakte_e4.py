"""E4: zentrale Kontakte, Telefonnummern-QS und Umwandlungshilfe."""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAenderung, KatplanElement, KatplanLink, KatplanPlan
from app.models.kontakt import Kontakt, KontaktTelefon
from app.models.user import User
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_pdf_service import render_katplan_html
from app.services.katplan_qs_service import element_hinweise, telefonnummern_im_element
from app.services.katplan_service import lege_element_an
from app.services.kontakt_service import create_kontakt
from tests.conftest import TestingSession
from tests.test_katplan_k1b import login, post, umgebung  # noqa: F401
from tests.test_katplan_k2a import basis
from tests.test_katplan_k4b import verknuepfungen  # noqa: F401
from tests.test_mcp_katplan import rufe

NUMMER = "0664 1234567"


@pytest.fixture
def telefon(client, verknuepfungen):  # noqa: F811
    info, element, ids = verknuepfungen
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        e = db.get(KatplanElement, element.id)
        e.beschreibung_html = f"<p>Bauhof Tel.: {NUMMER} erreichen</p>"
        e.daten_json = json.dumps({"kontrollhinweise": f"Tel: {NUMMER}",
                                   "schritte": [{"text": f"Telefon: {NUMMER} anrufen"}]})
        kontakt = db.get(Kontakt, ids[0])
        kontakt.funktion = "Bereitschaft"
        kontakt.organisation = "Gemeinde"
        kontakt.telefone.append(KontaktTelefon(org_id=info["org_id"], nummer="+43 664 1234567",
                                               verwendung="dienst"))
        db.commit()
    return info, element, ids, basis(info) + f"/elemente/{element.id}/telefon"


def test_erkennung_rekursiv_datum_reihenfolge_limit():
    e = SimpleNamespace(beschreibung_html=f"<p>{NUMMER}</p><p>2026-10-21 / 21.10.2026</p>",
                        daten_json=json.dumps({"schritte": [{"text": NUMMER}, {"text": "+43 555 123456"}],
                                               "hinweis": ["kurz 1234567", "0043 555 123456"]}))
    assert telefonnummern_im_element(e) == [NUMMER, "+43 555 123456", "0043 555 123456"]
    e.daten_json = json.dumps([str(10000000 + i) for i in range(20)])
    assert len(telefonnummern_im_element(e)) == 10


def test_warnung_ohne_link_und_freigabe(client, telefon):
    info, element, _, _ = telefon
    page = client.get(basis(info))
    assert "1 Elemente enthalten Telefonnummern im Text." in page.text
    assert "Zur QS-Liste" in page.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        plan, e = db.get(KatplanPlan, info["plan_id"]), db.get(KatplanElement, element.id)
        h = next(h for h in element_hinweise(db, plan, e, heute=date(2026, 10, 21)) if h["code"] == "telefon_im_text")
        assert h["nummern"] == [NUMMER]
        user = db.query(User).filter_by(username=info["users"]["katplan_freigeber"]).one()
        assert freigeben(db, plan, user).id


def test_assistent_normalisiert_org_archiv_und_keine_weiteren_nummern(client, telefon):
    info, _, _, path = telefon
    with TestingSession() as db:
        set_tenant_context(db, None)
        for org, archiviert, name in [(info["org_id"], True, "Archiviert"),
                                     (None, False, "Fremder Treffer")]:
            k = Kontakt(org_id=org, anzeigename=name, archiviert=archiviert)
            db.add(k)
            db.flush()
            k.telefone = [KontaktTelefon(org_id=org, nummer="+436641234567", verwendung="dienst")]
        db.commit()
    response = client.get(path)
    assert response.status_code == 200
    assert "06641234567" in response.text and "Anna Bauhof" in response.text
    assert "Bereitschaft" in response.text and "Gemeinde" in response.text
    assert "+43664001" not in response.text and "+43664002" not in response.text
    assert "Archiviert" not in response.text and "Fremder Treffer" not in response.text
    assert "Kein Recht zum Anlegen" in response.text
    assert "Kontakt verknüpfen" in client.get(basis(info) + "/bearbeiten?element=" + path.split("/")[-2]).text


def test_verknuepfen_entfernen_htmx_protokoll_suchtext(client, telefon):
    info, element, ids, path = telefon
    response = post(client, path, nummer=NUMMER, aktion="verknuepfen", kontakt_id=ids[0],
                    version=element.version, entfernen="true")
    assert response.status_code == 200
    assert 'id="kp-detail" hx-swap-oob="innerHTML"' in response.text
    assert 'id="kp-qualitaet"' in response.text and 'hx-swap-oob="outerHTML"' in response.text
    assert "Keine Telefonnummern mehr im Text" in response.text
    assert "location.reload" not in response.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        e, plan = db.get(KatplanElement, element.id), db.get(KatplanPlan, info["plan_id"])
        assert db.query(KatplanLink).filter_by(element_id=e.id, ziel_id=ids[0]).count() == 1
        assert e.version > element.version
        assert NUMMER not in (e.beschreibung_html or "") + e.daten_json + e.suchtext
        assert "Tel.:" not in (e.beschreibung_html or "")
        assert "Tel:" not in e.daten_json and "Telefon:" not in e.daten_json
        assert "  " not in (e.beschreibung_html or "")
        assert not telefonnummern_im_element(e)
        assert "telefon_im_text" not in {h["code"] for h in element_hinweise(db, plan, e, heute=date(2026, 10, 21))}
        assert db.query(KatplanAenderung).filter_by(element_id=e.id, feld="daten_json").count() == 1


def test_neuer_kontakt_rechte_dienst_und_atomarer_fehler(client, telefon):
    info, element, _, path = telefon
    werte = dict(nummer=NUMMER, aktion="anlegen", version=element.version, anzeigename="Neue Bereitschaft",
                 funktion="Leitung", organisation="Bauhof", entfernen="true")
    assert post(client, path, **werte).status_code == 403
    login(client, info, "org_admin")
    assert 'name="anzeigename"' in client.get(path).text
    assert post(client, path, **{**werte, "rolle": "ungueltig"}).status_code == 422
    assert post(client, path, **werte).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        kontakte = db.query(Kontakt).filter_by(anzeigename="Neue Bereitschaft").all()
        assert len(kontakte) == 1
        k = kontakte[0]
        assert k.einsatzrelevant and k.funktion == "Leitung" and k.organisation == "Bauhof"
        assert [(t.nummer, t.verwendung) for t in k.telefone] == [(NUMMER, "dienst")]
        assert db.query(KatplanLink).filter_by(element_id=element.id, ziel_id=k.id).count() == 1


def test_doppelter_link_und_konflikt(client, telefon):
    info, element, ids, path = telefon
    werte = dict(nummer=NUMMER, aktion="verknuepfen", kontakt_id=ids[0], version=element.version)
    assert post(client, path, **werte).status_code == 200
    assert post(client, path, **werte).status_code == 409
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        version = db.get(KatplanElement, element.id).version
    assert post(client, path, **{**werte, "version": version, "entfernen": "true"}).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        assert db.query(KatplanLink).filter_by(element_id=element.id).count() == 1


@pytest.mark.parametrize("rolle", ["readonly", "fremd"])
def test_assistent_zugriff(client, telefon, rolle):
    info, element, _, path = telefon
    login(client, info, rolle)
    erwartet = 403 if rolle == "readonly" else 404
    assert client.get(path).status_code == erwartet
    assert post(client, path, nummer=NUMMER, aktion="verknuepfen", version=element.version).status_code == erwartet


def test_archivierter_plan(client, telefon):
    info, element, _, path = telefon
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        db.get(KatplanPlan, info["plan_id"]).status = "archiviert"
        db.commit()
    assert client.get(path).status_code == 403
    assert post(client, path, nummer=NUMMER, aktion="verknuepfen", version=element.version).status_code == 403


@pytest.mark.parametrize("planart", ["alarm_einsatzplan", "massnahmenplan", "katastrophenplan"])
def test_verstaendigungsliste_fassung_gruppierung_deduplizierung_rechte(client, telefon, planart):
    info, element, ids, _ = telefon
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        plan = db.get(KatplanPlan, info["plan_id"])
        plan.planart = planart
        user = db.query(User).filter_by(username=info["users"]["katplan_freigeber"]).one()
        e = db.get(KatplanElement, element.id)
        e.akteure_json = '["feuerwehr"]'
        allgemein = create_kontakt(db, {"typ": "person", "anzeigename": "Allgemeine Bereitschaft",
                                       "einsatzrelevant": True},
                                  [{"nummer": "+435551234567", "verwendung": "dienst"}], [],
                                  org_id=info["org_id"], user_id=user.id, commit=False)
        for eid, kid, rolle in [(e.id, ids[0], "zu_verstaendigen"), (e.id, ids[0], "verantwortlich"),
                                 (None, ids[0], "zu_verstaendigen"), (None, allgemein.id, "verantwortlich"),
                                 (None, ids[1], "info")]:
            db.add(KatplanLink(org_id=plan.org_id, plan_id=plan.id, element_id=eid,
                               ziel_typ="kontakt", ziel_id=kid, rolle=rolle))
        # Vertrauliche Elemente duerfen die Liste fuer Leser nicht beeinflussen.
        geheim = lege_element_an(db, plan, user, typ="info", titel="Geheim")
        geheim.vertraulich = True
        k = create_kontakt(db, {"typ": "person", "anzeigename": "Geheime Bereitschaft"}, [], [],
                           org_id=info["org_id"], user_id=user.id, commit=False)
        db.add(KatplanLink(org_id=plan.org_id, plan_id=plan.id, element_id=geheim.id,
                           ziel_typ="kontakt", ziel_id=k.id, rolle="zu_verstaendigen"))
        db.flush()
        freigeben(db, plan, user)
        db.commit()
        # Spaetere Aenderung der Akteurszuordnung darf die Freigabe nicht veraendern.
        e.akteure_json = '["polizei"]'
        db.commit()
    login(client, info, "readonly")
    page = client.get(basis(info))
    assert page.status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        user = db.query(User).filter_by(username=info["users"]["readonly"]).one()
        html = render_katplan_html(db, user, db.get(KatplanPlan, info["plan_id"]))
    for text in [page.text, html]:
        if planart == "katastrophenplan":
            assert "Verständigungsliste" not in text
        else:
            liste = text.split("<h2>Verständigungsliste</h2>")[1].split("</section>")[0]
            assert "Feuerwehr" in liste and "Allgemein" in liste
            assert liste.count("<strong>Anna Bauhof</strong>") == 1
            assert "Allgemeine Bereitschaft" in liste and "Geheime Bereitschaft" not in liste
            assert "+43664002" not in liste


def test_mcp_telefonnummern_und_hinweis(client, telefon):
    info, element, _, _ = telefon
    result = rufe(info, "katplan_element_lesen", benutzerrolle="katplan_freigeber",
                  plan_id=info["plan_id"], element_id=element.uuid, fassung="arbeitsstand")
    assert result["telefonnummern_im_text"] == [NUMMER]
    assert "Telefonnummern bitte als zentrale Kontakte verknüpfen (katplan_verknuepfung_setzen)" in result["warnings"]


def test_entfernen_inline_html_und_verbleibende_nummer(client, telefon):
    info, element, ids, path = telefon
    andere_nummer = "+43 555 123456"
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        e = db.get(KatplanElement, element.id)
        e.beschreibung_html = ("<p><strong>Wichtig:</strong> Tel.: <em>0664</em> 1234567 anrufen</p>"
                               f"<p>Danach {andere_nummer}</p>")
        e.daten_json = json.dumps({"hinweis": [NUMMER, {"text": "Tel.: " + NUMMER, "anzahl": 3}]})
        db.commit()
    response = post(client, path, nummer=NUMMER, aktion="verknuepfen", kontakt_id=ids[0],
                    version=element.version, entfernen="true")
    assert response.status_code == 200
    assert "+43555123456" in response.text
    with TestingSession() as db:
        set_tenant_context(db, info["org_id"])
        e = db.get(KatplanElement, element.id)
        assert telefonnummern_im_element(e) == [andere_nummer]
        assert "<strong>Wichtig:</strong>" in e.beschreibung_html
        assert "Tel.:" not in e.beschreibung_html + e.daten_json
        assert json.loads(e.daten_json)["hinweis"] == ["", {"text": "", "anzahl": 3}]
