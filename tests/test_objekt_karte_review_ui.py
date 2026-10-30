"""Review-Oberfläche: Rollen, Zähler, sichere JSON-Texte und Quellen."""
from pathlib import Path

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektDokument, ObjektDokumentSeite
from tests.test_mcp_objekt_karte import karte as karte
from tests.test_mcp_objekte import _seed
from tests.test_objekt_karte_freigabe_routes import _login
from tests.test_objekt_karte_vorschlaege import _neu


def test_verwalter_review_zaehler_und_sichere_json_quelle(client, karte):
    text = '<script>alert("KI")</script>'
    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        dokument = ObjektDokument(org_id=karte["org_id"], objekt_id=karte["objekt_id"],
                                  pfad="fehlt.pdf", dateiname_original=text)
        db.add(dokument)
        db.flush()
        seite = ObjektDokumentSeite(org_id=karte["org_id"], objekt_id=karte["objekt_id"],
                                   dokument_id=dokument.id, seiten_nr=2)
        db.add(seite)
        _neu(db, karte, label=text, begruendung=text, quelle_dokument_id=dokument.id, quelle_seite=2)
        db.commit()
        seite_id = seite.id
    _login(client, karte)
    basis = f'/objekte/{karte["objekt_id"]}/karte'
    for route in (basis, basis + "/tab"):
        response = client.get(route)
        assert response.status_code == 200
        assert 'id="oks-review"' in response.text
        assert 'data-review-count>1</span>' in response.text
        assert text not in response.text
    response = client.get(basis + "/vorschlaege.json")
    assert response.headers["content-type"] == "application/json"
    v = response.json()["vorschlaege"][0]
    assert v["label"] == v["begruendung"] == v["quelle_dokument_name"] == text
    assert v["quelle_seite_id"] == seite_id
    assert response.json()["freigabe_gesperrt"] is False


def test_lesende_sehen_keinen_review_modus(client):
    seed = _seed("karte-review-lesend", {"u": "readonly"})
    _login(client, seed)
    basis = f'/objekte/{seed["objekt_id"]}/karte'
    response = client.get(basis + "/tab")
    assert response.status_code == 200
    assert "KI-Vorschläge prüfen" not in response.text
    assert "data-review-" not in response.text
    assert "oks-review" not in response.text
    assert client.get(basis).status_code == 403


def test_review_css_klassen_definiert():
    import re

    from tests.test_css_klassen_definiert import _css_modifier, _template_modifier

    template = Path("app/templates/objekt/_karte_review.html").read_text()
    css = Path("app/static/css/app.css").read_text() + template
    assert not _template_modifier(template) - _css_modifier(css)
    js = Path("app/static/js/objekt_karte.js").read_text()
    verwendet = set(re.findall(r"oks-review(?:__[a-z-]+)?", js + template))
    definiert = set(re.findall(r"\.(oks-review(?:__[a-z-]+)?)", template))
    assert verwendet <= definiert


def test_arbeitskopie_sperrt_freigabe_in_template_und_json(client, karte):
    from app.models.objekt import Objekt
    from app.services.objekt_service import erstelle_arbeitskopie

    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        objekt = db.get(Objekt, karte["objekt_id"])
        _neu(db, karte, lat=47.101, lng=9.201)
        erstelle_arbeitskopie(db, objekt, karte["users"]["u"])
        db.commit()
    _login(client, karte)
    basis = f'/objekte/{karte["objekt_id"]}/karte'
    for route in (basis, basis + "/tab"):
        response = client.get(route)
        assert response.status_code == 200
        assert 'data-gesperrt="1"' in response.text
        assert 'data-review-freigeben disabled' in response.text
        assert "Offene Arbeitskopie: Freigabe" in response.text
        assert f'href="/objekte/{karte["objekt_id"]}">Zur Arbeitskopie: Übernehmen oder Verwerfen</a>' in response.text
    assert client.get(basis + "/vorschlaege.json").json()["freigabe_gesperrt"] is True


def test_marker_json_enthaelt_pruefstatus(client, karte):
    from app.models.objekt import ObjektKartenObjekt

    with SessionLocal() as db:
        set_tenant_context(db, karte["org_id"])
        marker = db.query(ObjektKartenObjekt).filter_by(objekt_id=karte["objekt_id"]).first()
        marker.position_status = "bestaetigt"
        db.commit()
    _login(client, karte)
    eintraege = client.get(f'/objekte/{karte["objekt_id"]}/karte/objekte.json').json()["eintraege"]
    assert {e["position_status"] for e in eintraege} == {"ungeprueft", "bestaetigt"}
