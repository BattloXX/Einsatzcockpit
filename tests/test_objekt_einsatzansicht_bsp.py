"""BSP-Ebenen der Objekt-Einsatzansicht: Leserechte, Gueltigkeit und Einbettung."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.permissions import EINSATZ_LESE_ROLLEN
from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.objekt import BspOverlayEinsatz, Objekt, ObjektBspOverlay, ObjektDokument
from tests.test_bsp_overlay_routes import _rolle
from tests.test_bsp_overlay_service import umgebung as umgebung
from tests.test_lagefuehrung_bsp_overlays import einsatz_plan as einsatz_plan
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_karte_freigabe_routes import _login


def _url(p):
    return f'/objekte/{p["objekt_id"]}/bsp-overlays.json'


def _lesen(client, p):
    response = client.get(_url(p))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    return response.json()


@pytest.mark.parametrize('aenderung', ['entwurf', 'zurueckgezogen', 'hash', 'georef', 'dokument', 'dokumentfreigabe'])
def test_nur_auslieferbare_freigegebene_overlays(client, einsatz_plan, aenderung):
    p = einsatz_plan
    _login(client, p)
    daten = _lesen(client, p)
    assert len(daten) == 1
    assert daten == client.get(f'/einsatz/{p["incident_id"]}/info/bsp-overlays.json').json()
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        overlay = db.get(ObjektBspOverlay, p['overlay_id'])
        if aenderung in ('entwurf', 'zurueckgezogen'):
            overlay.status = aenderung
        elif aenderung == 'hash':
            overlay.raster_sha256 = 'ungueltig'
        elif aenderung == 'georef':
            overlay.georef_revision += 1
        elif aenderung == 'dokument':
            db.get(ObjektDokument, p['dokument_id']).ist_aktuelle_version = False
        else:
            db.get(ObjektDokument, p['dokument_id']).freigabe_status = 'entwurf'
        db.commit()
    assert _lesen(client, p) == []


def test_nur_overlays_dieses_objekts(client, einsatz_plan):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        anderes = Objekt(org_id=p['org_id'], name='Anderes Objekt', status='freigegeben')
        db.add(anderes)
        db.flush()
        anderer_id = anderes.id
        db.commit()
    _login(client, p)
    assert client.get(f'/objekte/{anderer_id}/bsp-overlays.json').json() == []
    assert [o['id'] for o in _lesen(client, p)] == [p['overlay_id']]


@pytest.mark.parametrize('standard', [True, False])
def test_objektvorgabe_ohne_einsatzeinstellung(client, einsatz_plan, standard):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        db.get(ObjektBspOverlay, p['overlay_id']).standard_sichtbar = standard
        db.add(BspOverlayEinsatz(org_id=p['org_id'], incident_id=p['incident_id'],
                                overlay_id=p['overlay_id'], sichtbarkeit='ON', deckkraft=0.9))
        db.commit()
    _login(client, p)
    o = _lesen(client, p)[0]
    assert o['sichtbarkeit'] == 'INHERIT' and o['deckkraft'] is None
    assert o['effektiv_sichtbar'] is standard and o['effektiv_deckkraft'] == 0.6
    assert o['kann_einstellen'] is False


@pytest.mark.parametrize('rolle', EINSATZ_LESE_ROLLEN)
def test_leserollen_duerfen_lesen(client, einsatz_plan, rolle):
    _rolle(einsatz_plan, rolle)
    _login(client, einsatz_plan)
    assert _lesen(client, einsatz_plan)[0]['id'] == einsatz_plan['overlay_id']
    assert client.post(_url(einsatz_plan), json={}).status_code in (403, 405)


@pytest.mark.parametrize('rolle,erwartet', [('readonly', 404), ('incident_leader', 404),
                                           ('objekt_verwalter', 200), ('org_admin', 200)])
def test_entwurf_objekt_nur_verwalter(client, einsatz_plan, rolle, erwartet):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        db.get(Objekt, p['objekt_id']).status = 'entwurf'
        db.commit()
    _rolle(p, rolle)
    _login(client, p)
    response = client.get(_url(p))
    assert response.status_code == erwartet
    if erwartet == 200:
        assert response.json()[0]['id'] == p['overlay_id']


def test_fremde_org_404_und_ohne_login_gesperrt(client, einsatz_plan):
    client.cookies.clear()
    assert client.get(_url(einsatz_plan)).status_code in (401, 302)
    fremd = _seed('objekt-bsp-fremd-' + uuid4().hex[:8], {'u': 'objekt_verwalter'})
    _login(client, fremd)
    assert client.get(_url(einsatz_plan)).status_code == 404


def test_einbettung_und_editor_mit_lokalem_overlay(client, einsatz_plan):
    p = einsatz_plan
    _rolle(p, 'objekt_verwalter')
    _login(client, p)
    response = client.get(f'/objekte/{p["objekt_id"]}/karte/einbettung')
    assert response.status_code == 200
    assert 'id="lft-layer-bsp"' in response.text
    assert 'id="lft-bsp-deckkraft"' in response.text
    assert f'listUrl: "{_url(p)}", nurLokal: true' in response.text
    assert 'bsp_overlay_layer.js' in response.text
    editor = client.get(f'/objekte/{p["objekt_id"]}/karte')
    assert editor.status_code == 200
    assert 'id="lft-layer-bsp"' in editor.text
    assert 'EcpBspOverlay.init' in editor.text
    ansicht = client.get(f'/objekte/{p["objekt_id"]}/einsatz')
    assert ansicht.status_code == 200
    assert f'/objekte/{p["objekt_id"]}/karte/einbettung' in ansicht.text
    fragment = client.get(f'/objekte/{p["objekt_id"]}/einsatz-fragment')
    assert fragment.status_code == 200
    assert '/karte/einbettung' not in fragment.text  # kompakt: Einsatzinfo hat bereits eine Karte


def test_objektmodul_inaktiv_sperrt_route_und_bedienung(client, einsatz_plan):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        db.query(OrgSettings).filter_by(org_id=p['org_id']).one().objekt_module_enabled = False
        db.commit()
    _login(client, p)
    assert client.get(_url(p)).status_code == 404
    assert client.get(f'/objekte/{p["objekt_id"]}/karte/einbettung').status_code == 404
    # Zusaetzlich die Template-Schranke pruefen, unabhaengig von der Routen-Schranke.
    request = Request({'type': 'http', 'state': {'objekt_enabled': False, 'csrf_token': 'test'}})
    html = templates.env.get_template('objekt/_karte_readonly.html').render(
        request=request, objekt=SimpleNamespace(id=p['objekt_id']), ist_verwalter=False,
    )
    assert 'id="lft-layer-bsp"' not in html
    assert 'EcpBspOverlay.init' not in html
