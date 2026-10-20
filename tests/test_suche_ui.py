"""S5: Suchoberflaeche, Session-Rechte, CSRF, Escaping und JSON-Vertrag."""

from uuid import uuid4

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.major_incident import MajorIncident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt
from app.services.suche import abfrage
from app.services.suche.indexer import indexiere
from app.services.suche.vertrag import SuchErgebnis, Treffer
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _login, _user


@pytest.fixture
def umgebung(client, monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    with TestingSession() as db:
        set_tenant_context(db, None)
        org, fremd = [FireDept(slug=uuid4().hex, name=name) for name in ("Suche UI", "Fremd")]
        db.add_all([org, fremd])
        db.flush()
        db.add(OrgSettings(org_id=org.id, suche_aktiv=True, objekt_module_enabled=True))
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").first()
        vorher = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="objekt_module_enabled")
            db.add(flag)
        flag.value = "true"
        users = {}
        for rolle in ("readonly", "recorder", "incident_leader", "org_admin", "ohne"):
            name = uuid4().hex
            _user(db, name, org.id, *(() if rolle == "ohne" else (rolle,)))
            users[rolle] = name
        objekt = Objekt(org_id=org.id, nummer=987, name="Brand Fabrik", status="freigegeben",
                        informationen='<script>alert(1)</script> Brand')
        einsatz, anderer_einsatz = [Incident(primary_org_id=o.id, alarm_type_code="B1") for o in (org, fremd)]
        lage, andere_lage = [MajorIncident(org_id=o.id, name="Brand Lage") for o in (org, fremd)]
        db.add_all([objekt, einsatz, anderer_einsatz, lage, andere_lage])
        db.flush()
        indexiere(db, org.id, "objekt", str(objekt.id))
        db.commit()
        info = dict(org=org.id, objekt=objekt.id, einsatz=einsatz.id, lage=lage.id,
                    fremder_einsatz=anderer_einsatz.id, fremde_lage=andere_lage.id, users=users)
    assert _login(client, users["readonly"]).status_code == 302
    yield info
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").one()
        if vorher is None:
            db.delete(flag)
        else:
            flag.value = vorher
        db.commit()


def schalter(info, aktiv):
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=info["org"]).one().suche_aktiv = aktiv
        db.commit()


def post(client, **daten):
    return client.post("/suche/treffer", data={"_csrf": client.cookies.get("ec_csrf"), "q": "Brand", **daten},
                       headers={"HX-Request": "true"}, follow_redirects=False)


def api(client, **daten):
    return client.post("/api/suche", json={"q": "Brand", **daten},
                       headers={"X-CSRF-Token": client.cookies.get("ec_csrf")})


def test_sichtbarkeit_und_systemflag(client, umgebung, monkeypatch):
    page = client.get("/suche?q=NICHT_UEBERNEHMEN")
    assert page.status_code == 200
    assert "NICHT_UEBERNEHMEN" not in page.text
    assert page.text.count('data-suche-link') == 2
    schalter(umgebung, False)
    assert client.get("/suche").status_code == 404
    assert 'data-suche-link' not in client.get("/").text
    assert post(client).status_code == 404
    assert api(client).status_code == 404
    schalter(umgebung, True)
    monkeypatch.setattr(settings, "SEARCH_ENABLED", False)
    assert client.get("/suche").status_code == 404
    assert 'data-suche-link' not in client.get("/").text


def test_csrf_und_treffer(client, umgebung):
    assert client.post("/suche/treffer", data={"q": "Brand"}).status_code == 403
    result = post(client)
    assert result.status_code == 200
    assert "Brand Fabrik" in result.text
    assert '<mark>Brand</mark>' in result.text
    assert f'href="/objekte/{umgebung["objekt"]}"' in result.text


def test_xss_index_und_query(client, umgebung, monkeypatch):
    # Der bestehende Index entfernt HTML aus Informationen schon vor der Ausgabe.
    # Ein Titel bleibt Klartext und muss beim Rendern escaped werden.
    with TestingSession() as db:
        set_tenant_context(db, None)
        objekt = db.get(Objekt, umgebung["objekt"])
        objekt.name = '<script>alert(1)</script> Brand'
        db.flush()
        indexiere(db, umgebung["org"], "objekt", str(objekt.id))
        db.commit()
    result = post(client)
    assert '&lt;script&gt;' in result.text
    assert '<script>alert' not in result.text
    result = post(client, q='<b>Brand</b>')
    assert '&lt;b&gt;Brand&lt;/b&gt;' in result.text
    assert '<b>Brand</b>' not in result.text
    # Defense in depth: auch rohe Auszuege duerfen nur mark-Tags erzeugen.
    treffer = Treffer(quelle="objekt", quell_id="1", titel="Test", url="")
    treffer.auszug = '<script>alert(1)</script> Brand'
    monkeypatch.setattr("app.routers.ui_suche.suche", lambda *_: SuchErgebnis(treffer=[treffer]))
    result = post(client)
    assert '&lt;script&gt;' in result.text and '<mark>Brand</mark>' in result.text
    assert '<script>alert' not in result.text
    assert '>Öffnen</a>' not in result.text


def test_teilweise_mit_treffern(client, umgebung, monkeypatch):
    def fehler(*args, **kwargs):
        raise RuntimeError("Volltext ausgefallen")
    monkeypatch.setattr(abfrage, "volltext_kandidaten", fehler)
    result = post(client, q="987")
    assert result.status_code == 200
    assert 'teilweise nicht verfügbar' in result.text
    assert 'Volltextsuche nicht verfügbar' in result.text
    assert 'Brand Fabrik' in result.text


def test_leer_keine_treffer_und_validation(client, umgebung):
    assert 'Suchbegriffe eingeben' in post(client, q="").text
    assert 'Suchbegriffe eingeben' in post(client, q=" ").text
    assert 'Keine Treffer' in post(client, q=uuid4().hex).text
    for daten in ({"q": "x" * 501}, {"lat": "nord"}, {"radius_m": "5001"}, {"typen": "ungueltig"}):
        result = post(client, **daten)
        assert result.status_code == 200
        assert 'role="alert"' in result.text
        assert 'Keine Treffer' not in result.text


@pytest.mark.parametrize("kontext,kennung", [("gsl", "fremde_lage"), ("einsatz", "fremder_einsatz")])
def test_fremder_kontext(client, umgebung, kontext, kennung):
    daten = {"kontext": kontext, "kontext_id": umgebung[kennung]}
    assert client.get("/suche", params=daten).status_code == 404
    assert post(client, **daten).status_code == 404
    assert post(client, q="", **daten).status_code == 404
    assert api(client, **daten).status_code == 404


def test_api_vertrag_csrf_und_validierung(client, umgebung):
    assert client.post("/api/suche", json={"q": "Brand"}).status_code == 403
    result = api(client)
    assert result.status_code == 200
    assert set(result.json()) == {"treffer", "modi", "teilweise", "index_stand", "hinweise"}
    assert result.json()["treffer"][0]["titel"] == "987 Brand Fabrik"
    assert api(client, limit=51).status_code == 422
    assert api(client, q="").status_code == 422


@pytest.mark.parametrize("rolle", ["readonly", "recorder", "incident_leader", "org_admin", "ohne"])
def test_rollen(client, umgebung, rolle):
    assert _login(client, umgebung["users"][rolle]).status_code == 302
    erwartet = 403 if rolle == "ohne" else 200
    assert client.get("/suche", follow_redirects=False).status_code == erwartet
    assert post(client).status_code == erwartet
    assert api(client).status_code == erwartet


def test_einstiege_und_kontext(client, umgebung):
    for kontext, kennung, pfad in (("einsatz", "einsatz", "/einsatz/"), ("gsl", "lage", "/lage/")):
        ident = umgebung[kennung]
        link = f'/suche?kontext={kontext}&kontext_id={ident}'
        result = client.get(f"{pfad}{ident}")
        assert result.status_code == 200
        assert link in result.text
        page = client.get(link)
        assert page.status_code == 200
        assert 'Suche in:' in page.text and 'Ganze Organisation' in page.text
        assert ('>\n        GSL' in page.text) == (kontext == "gsl")
        schalter(umgebung, False)
        assert link not in client.get(f"{pfad}{ident}").text
        schalter(umgebung, True)


def test_typgruppen_und_vorbelegung(client, umgebung):
    page = client.get("/suche?typ=objekt")
    assert 'value="objekt,objekt_dokument_seite"' in page.text
    assert 'checked' in page.text
    assert 'Ganze Organisation' not in page.text
    assert 'gsl_stelle,gsl_meldung,gsl_einheit' not in page.text
    result = post(client, typen=["objekt,objekt_dokument_seite", "katplan_element,katplan_seite"])
    assert result.status_code == 200 and 'Brand Fabrik' in result.text


def test_rate_limit(client, umgebung):
    from app.core.rate_limit import limiter
    if limiter is None:
        pytest.skip("slowapi nicht installiert")
    for _ in range(60):
        assert post(client, q="").status_code == 200
    assert post(client, q="").status_code == 429
