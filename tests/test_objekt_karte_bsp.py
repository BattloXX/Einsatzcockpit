"""Lokale BSP-Ebene in beiden Objektkarten, ohne Entwurfsraster."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.templating import templates
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektBspOverlay
from tests.test_bsp_overlay_routes import _rolle
from tests.test_bsp_overlay_service import umgebung as umgebung
from tests.test_lagefuehrung_bsp_overlays import einsatz_plan as einsatz_plan
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_karte_freigabe_routes import _login


@pytest.mark.parametrize('pfad', ['karte/tab', 'karte'])
def test_objektkarten_rendern_lokale_bsp_bedienung(client, einsatz_plan, pfad):
    p = einsatz_plan
    _rolle(p, 'objekt_verwalter')
    _login(client, p)
    response = client.get(f'/objekte/{p["objekt_id"]}/{pfad}')
    assert response.status_code == 200
    assert 'id="lft-layer-bsp"' in response.text
    assert '<details id="lft-bsp"' in response.text
    assert 'bsp_overlay_layer.js' in response.text
    assert f'listUrl: "/objekte/{p["objekt_id"]}/bsp-overlays.json", nurLokal: true' in response.text
    assert 'dataset.initialisiert' in response.text


@pytest.mark.parametrize('template', ['objekt/_karte_tab.html', 'objekt/karte.html'])
@pytest.mark.parametrize('aktiv', [False, True])
def test_template_bsp_nur_mit_objektmodul(template, aktiv):
    request = Request({'type': 'http', 'state': {'objekt_enabled': aktiv, 'csrf_token': 'test'}})
    html = templates.env.get_template(template).render(
        request=request, objekt=SimpleNamespace(id=42, name='Testhalle', anzeige_nummer='42'),
        ist_verwalter=False, symbol_typen={},
    )
    assert ('id="lft-layer-bsp"' in html) is aktiv
    assert ('bsp_overlay_layer.js' in html) is aktiv
    assert ('EcpBspOverlay.init' in html) is aktiv


@pytest.mark.parametrize('rolle', ['readonly', 'objekt_verwalter', 'org_admin'])
@pytest.mark.parametrize('status', ['entwurf', 'veraltet', 'zurueckgezogen', 'freigegeben'])
def test_zaehler_nur_fuer_verwalter_ohne_entwurfsdaten(client, einsatz_plan, rolle, status):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        overlay = db.get(ObjektBspOverlay, p['overlay_id'])
        if status == 'veraltet':
            overlay.georef_revision += 1
        else:
            overlay.status = status
        db.commit()
    _rolle(p, rolle)
    _login(client, p)
    response = client.get(f'/objekte/{p["objekt_id"]}/bsp-overlays.json')
    assert response.status_code == 200
    if rolle == 'readonly':
        assert 'X-BSP-Pruefen' not in response.headers
        tab = client.get(f'/objekte/{p["objekt_id"]}/karte/tab')
        assert tab.status_code == 200
        assert 'Overlay(s) nicht freigegeben/veraltet' not in tab.text
    else:
        assert response.headers['X-BSP-Pruefen'] == ('1' if status in ('entwurf', 'veraltet') else '0')
    if status == 'freigegeben':
        assert [o['id'] for o in response.json()] == [p['overlay_id']]
    else:
        assert response.json() == []
        assert 'raster_url' not in response.text


@pytest.mark.parametrize('pfad', ['karte/tab', 'karte', 'bsp-overlays.json'])
def test_fremde_org_404(client, einsatz_plan, pfad):
    fremd = _seed('karte-bsp-fremd-' + uuid4().hex[:8], {'u': 'objekt_verwalter'})
    _login(client, fremd)
    response = client.get(f'/objekte/{einsatz_plan["objekt_id"]}/{pfad}')
    assert response.status_code == 404


@pytest.mark.parametrize('pfad', ['karte/tab', 'karte', 'bsp-overlays.json'])
def test_inaktives_objektmodul_sperrt_objektkarten(client, einsatz_plan, pfad):
    from app.models.master import OrgSettings

    p = einsatz_plan
    _rolle(p, 'objekt_verwalter')
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        db.query(OrgSettings).filter_by(org_id=p['org_id']).one().objekt_module_enabled = False
        db.commit()
    _login(client, p)
    assert client.get(f'/objekte/{p["objekt_id"]}/{pfad}').status_code == 404
