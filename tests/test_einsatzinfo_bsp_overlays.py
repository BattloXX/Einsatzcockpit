"""P4: geschuetzte, rein lokale BSP-Ebenen in weiteren Einsatzkarten."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.permissions import EINSATZ_LESE_ROLLEN
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, MajorIncident, SitePhase
from app.models.master import OrgSettings
from app.models.objekt import BspOverlayEinsatz, ObjektBspOverlay, ObjektDokument, ObjektEinsatz
from tests.test_bsp_overlay_routes import _rolle
from tests.test_bsp_overlay_service import umgebung as umgebung
from tests.test_lagefuehrung_bsp_overlays import einsatz_plan as einsatz_plan
from tests.test_mcp_objekte import _seed
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_karte_freigabe_routes import _login


def _url(p):
    return f'/einsatz/{p["incident_id"]}/info/bsp-overlays.json'


def _lesen(client, p):
    response = client.get(_url(p))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    return response.json()


@pytest.mark.parametrize('aenderung', ['vorschlag', 'entwurf', 'zurueckgezogen', 'hash', 'georef', 'dokument'])
def test_json_nur_bestaetigt_freigegeben_auslieferbar(client, einsatz_plan, aenderung):
    p = einsatz_plan
    _login(client, p)
    daten = _lesen(client, p)
    assert len(daten) == 1
    assert daten[0]['kann_einstellen'] is False
    lage = client.get(f'/einsatz/{p["incident_id"]}/lagefuehrung/bsp-overlays.json').json()[0]
    assert daten[0] == lage | {'kann_einstellen': False}
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        overlay = db.get(ObjektBspOverlay, p['overlay_id'])
        if aenderung == 'vorschlag':
            db.query(ObjektEinsatz).filter_by(incident_id=p['incident_id']).one().status = 'vorschlag'
        elif aenderung in ('entwurf', 'zurueckgezogen'):
            overlay.status = aenderung
        elif aenderung == 'hash':
            overlay.raster_sha256 = 'ungueltig'
        elif aenderung == 'georef':
            overlay.georef_revision += 1
        else:
            db.get(ObjektDokument, p['dokument_id']).ist_aktuelle_version = False
        db.commit()
    assert _lesen(client, p) == []


@pytest.mark.parametrize('rolle', EINSATZ_LESE_ROLLEN)
def test_zugriff_wie_einsatzinfo_ohne_lagefuehrungsmodul(client, einsatz_plan, rolle):
    p = einsatz_plan
    _rolle(p, rolle)
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        db.query(OrgSettings).filter_by(org_id=p['org_id']).one().lagefuehrung_modul_aktiv = False
        db.commit()
    _login(client, p)
    assert client.get(f'/einsatz/{p["incident_id"]}/info').status_code == 200
    assert _lesen(client, p)[0]['id'] == p['overlay_id']
    assert client.get(f'/objekt-medien/bsp-overlay/{p["overlay_id"]}/raster').status_code == 200
    assert client.post(_url(p), json={}).status_code in (403, 405)


@pytest.mark.parametrize('sichtbarkeit,standard,deckkraft,sichtbar,opacity', [
    (None, True, None, True, 0.6), ('INHERIT', False, None, False, 0.6),
    ('INHERIT', True, None, True, 0.6), ('ON', False, 0.3, True, 0.3), ('OFF', True, 0.8, False, 0.8),
])
def test_startzustand_einsatzvorgabe_oder_objektdefault(
    client, einsatz_plan, sichtbarkeit, standard, deckkraft, sichtbar, opacity,
):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        db.get(ObjektBspOverlay, p['overlay_id']).standard_sichtbar = standard
        if sichtbarkeit:
            db.add(BspOverlayEinsatz(org_id=p['org_id'], incident_id=p['incident_id'],
                                    overlay_id=p['overlay_id'], sichtbarkeit=sichtbarkeit, deckkraft=deckkraft))
        db.commit()
    _login(client, p)
    o = _lesen(client, p)[0]
    assert o['effektiv_sichtbar'] is sichtbar and o['effektiv_deckkraft'] == opacity
    assert o['kann_einstellen'] is False


def test_fremde_org_404_und_anonym_gesperrt(client, einsatz_plan):
    p = einsatz_plan
    client.cookies.clear()
    assert client.get(_url(p)).status_code == 401
    fremd = _seed('info-bsp-fremd-' + uuid4().hex[:8], {'u': 'incident_leader'})
    _login(client, fremd)
    assert client.get(_url(p)).status_code == 404
    assert client.get(f'/einsatz/{p["incident_id"]}/info').status_code == 404


def test_objektmodul_aus_blendet_liste_und_bedienung_aus(client, einsatz_plan):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        db.query(OrgSettings).filter_by(org_id=p['org_id']).one().objekt_module_enabled = False
        db.commit()
    _login(client, p)
    assert _lesen(client, p) == []
    assert 'id="lft-layer-bsp"' not in client.get(f'/einsatz/{p["incident_id"]}/info').text


@pytest.fixture
def gsl_plan(einsatz_plan):
    p = einsatz_plan
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        lage = MajorIncident(org_id=p['org_id'], name='BSP-Lage')
        db.add(lage)
        db.flush()
        site = IncidentSite(org_id=p['org_id'], major_incident_id=lage.id,
                            incident_id=p['incident_id'], bezeichnung='Betrieb', phase=SitePhase.disponiert)
        einheit = LageEinheit(lage_id=lage.id, label='BSP-Einheit')
        db.add_all([site, einheit])
        db.flush()
        dispatch = EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=datetime.now(UTC))
        db.add(dispatch)
        db.add(BspOverlayEinsatz(org_id=p['org_id'], incident_id=p['incident_id'],
                                overlay_id=p['overlay_id'], sichtbarkeit='ON', deckkraft=0.9))
        db.commit()
        return p | {'lage_id': lage.id, 'site_id': site.id, 'einheit_id': einheit.id, 'dispatch_id': dispatch.id}


@pytest.mark.parametrize('ansicht', ['gsl', 'einheit'])
def test_gsl_objektkette_lokaler_default_und_entfernen(client, gsl_plan, ansicht):
    p = gsl_plan
    # Admin darf den bestehenden Einheiten-Simulationskontext lesend nutzen.
    _rolle(p, 'admin')
    _login(client, p)
    url = (f'/lage/{p["lage_id"]}/bsp-overlays.json' if ansicht == 'gsl'
           else f'/einheit/bsp-overlays.json?sim={p["einheit_id"]}')
    response = client.get(url)
    assert response.status_code == 200, response.text
    o = response.json()[0]
    assert o['id'] == p['overlay_id'] and o['kann_einstellen'] is False
    assert o['sichtbarkeit'] == 'INHERIT' and o['deckkraft'] is None
    assert o['effektiv_sichtbar'] is False and o['effektiv_deckkraft'] == 0.6
    with SessionLocal() as db:
        set_tenant_context(db, p['org_id'])
        if ansicht == 'gsl':
            db.get(IncidentSite, p['site_id']).phase = SitePhase.abgebrochen
        else:
            db.get(EinheitSiteDispatch, p['dispatch_id']).withdrawn_at = datetime.now(UTC)
        db.commit()
    assert client.get(url).json() == []


def test_gsl_fremde_org_404(client, gsl_plan):
    fremd = _seed('gsl-bsp-fremd-' + uuid4().hex[:8], {'u': 'incident_leader'})
    _login(client, fremd)
    assert client.get(f'/lage/{gsl_plan["lage_id"]}/bsp-overlays.json').status_code == 404
