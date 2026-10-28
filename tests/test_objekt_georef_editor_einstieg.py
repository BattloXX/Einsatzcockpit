"""Eigenstaendiger Editor-Einstieg, Hinweise und Vorschau ohne Netzabfragen."""

import pytest
from sqlalchemy import event
from starlette.requests import Request

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentGeoref, ObjektDokumentSeite
from app.models.user import User
from app.routers.ui_objekt_dokumente import _galerie_context
from app.services import objekt_georef_service as geo
from tests.test_mcp_objekte import _rufe, _seed, _token
from tests.test_objekt_georef import karte as karte
from tests.test_objekt_georef import plan as plan
from tests.test_objekt_georef import position, punkte
from tests.test_objekt_karte_freigabe_routes import _login
from tests.test_objekt_karte_rotation import _post


def _seite(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        return db.query(ObjektDokumentSeite).filter_by(dokument_id=plan["dokument_id"]).one().id


def test_einstieg_rahmen_und_aktuelle_version(client, plan):
    _login(client, plan)
    sid = _seite(plan)
    url = f'/objekte/{plan["objekt_id"]}/dokumente/seite/{sid}/georef'
    antwort = client.get(url)
    assert antwort.status_code == 200
    assert 'id="georef-editor"' in antwort.text
    assert 'data-seite="1"' in antwort.text
    assert f'data-basis-url="/objekte/{plan["objekt_id"]}/karte"' in antwort.text
    assert f'data-zurueck-url="/objekte/{plan["objekt_id"]}/dokumente/viewer?seite={sid}"' in antwort.text
    assert '<script src="/static/js/leaflet.min.js"></script>' in antwort.text
    assert f'data-dokument-id="{plan["dokument_id"]}"' in antwort.text
    assert 'Georeferenzierung · plan.pdf · Seite 1' in antwort.text
    assert f'/dokumente/viewer?seite={sid}' in antwort.text
    assert '/static/js/objekt_georef_editor.js" defer' in antwort.text
    assert 'objekt_karte.js' not in antwort.text
    assert client.get(url.replace(f'/{sid}/georef', '/999999/georef')).status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        db.get(ObjektDokument, plan["dokument_id"]).ist_aktuelle_version = False
        db.commit()
    assert client.get(url).status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        db.get(ObjektDokument, plan["dokument_id"]).ist_aktuelle_version = True
        db.delete(db.get(ObjektDokumentSeite, sid))
        db.commit()
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("rolle", ["readonly", "objekt_verwalter"])
def test_einstieg_rechte_und_scoping(client, plan, rolle):
    fremd = _seed('georef-einstieg-' + rolle, {"u": rolle})
    sid = _seite(plan)
    _login(client, fremd)
    for oid in (plan["objekt_id"], fremd["objekt_id"]):
        response = client.get(f'/objekte/{oid}/dokumente/seite/{sid}/georef')
        assert response.status_code == (403 if rolle == 'readonly' else 404)
    client.cookies.clear()
    _login(client, plan)
    with SessionLocal() as db:
        set_tenant_context(db, plan["org_id"])
        o = Objekt(org_id=plan["org_id"], name="Anderes Objekt", nummer="GEO-2")
        db.add(o)
        db.commit()
        oid = o.id
    assert client.get(f'/objekte/{oid}/dokumente/seite/{sid}/georef').status_code == 404


def test_hinweise_nicht_blockierend_und_grenzen():
    p = punkte()[:3]
    assert {h['code'] for h in geo.transformation_berechnen(p, 2000, 1000)['hinweise']} == {'wenige_punkte'}
    for punkt in p:
        punkt['x'] = 0.4 + punkt['x'] * 0.3
        punkt['y'] = 0.4 + punkt['y'] * 0.3
        punkt['lat'], punkt['lng'] = position(punkt['x'], punkt['y'])
        punkt['quelle'] = 'manuell'
    ergebnis = geo.transformation_berechnen(p, 2000, 1000)
    assert ergebnis['status'] == 'gueltig'
    assert {h['code'] for h in ergebnis['hinweise']} == {'wenige_punkte', 'punkte_eng', 'nur_freie_klicks'}
    for punkt in p:
        punkt['x'] *= 0.01
    assert 'punkte_eng' not in {h['code'] for h in geo.transformation_berechnen(p, 2000, 1000)['hinweise']}
    p = punkte()
    p[0]['lat'] += 2 / (geo.ERDRADIUS_M * 3.141592653589793 / 180)
    ergebnis = geo.transformation_berechnen(p, 2000, 1000)
    assert ergebnis['status'] == 'gueltig'
    assert 'restfehler_hoch' in {h['code'] for h in ergebnis['hinweise']}
    p[0]['lat'] += 0.001
    ergebnis = geo.transformation_berechnen(p, 2000, 1000)
    assert ergebnis['status'] == 'abgelehnt'
    assert 'restfehler_hoch' not in {h['code'] for h in ergebnis['hinweise']}


def test_frische_vorschau_lesen_und_badges(client, plan):
    _login(client, plan)
    url = f'/objekte/{plan["objekt_id"]}/karte/georef'
    galerie = f'/objekte/{plan["objekt_id"]}/dokumente'
    assert 'data-georef-status="keine"' in client.get(galerie).text
    daten = dict(dokument_id=plan['dokument_id'], seite=1, punkte=punkte())
    gespeichert = _post(client, url, daten).json()['georeferenz']
    assert 'data-georef-status="gueltig"' in client.get(galerie).text
    assert 'gültig (RMSE 0.00 m)' in client.get(galerie).text
    for p in daten['punkte']:
        p['lat'] += 0.001
    frisch = _post(client, url, daten | {'pruefen': True}).json()['georeferenz']
    assert [(e['x'], e['y']) for e in frisch['vorschau_ecken']] == [(0, 0), (1, 0), (1, 1), (0, 1)]
    for e in frisch['vorschau_ecken']:
        lat, lng = position(e['x'], e['y'])
        assert e['lat'] == pytest.approx(lat + 0.001, abs=1e-8)
        assert e['lng'] == pytest.approx(lng, abs=1e-8)
    gelesen = client.get(url + f'.json?dokument_id={plan["dokument_id"]}&seite=1').json()['georeferenz']
    assert gelesen['vorschau_ecken'] == gespeichert['vorschau_ecken']
    assert gelesen['hinweise'] == []
    assert 'hinweise' not in ObjektDokumentGeoref.__table__.columns
    ungueltig = _post(client, url, daten | {'punkte': punkte()[:2], 'pruefen': True}).json()['georeferenz']
    assert 'vorschau_ecken' not in ungueltig
    (plan['pfad'] / 'plan.pdf').write_bytes(b'geaendert')
    assert 'data-georef-status="veraltet"' in client.get(galerie).text
    gelesen = client.get(url + f'.json?dokument_id={plan["dokument_id"]}&seite=1').json()['georeferenz']
    assert 'vorschau_ecken' not in gelesen
    (plan['pfad'] / 'plan.pdf').write_bytes(b'original')
    _post(client, url + '/verwerfen', daten)
    assert 'data-georef-status="abgelehnt"' in client.get(galerie).text


def test_galerie_abfragezahl_unabhaengig_von_seitenzahl(plan):
    with SessionLocal() as db:
        set_tenant_context(db, plan['org_id'])
        objekt = db.get(Objekt, plan['objekt_id'])
        user = db.get(User, plan['users']['u'])
        geo.referenzpunkte(db, objekt, plan['dokument_id'], 1, 'setzen', punkte(), None, user.id)
        request = Request({'type': 'http'})

        def anzahl():
            abfragen = []
            def merken(conn, cursor, statement, parameters, context, executemany):
                abfragen.append(statement)
            conn = db.connection()
            event.listen(conn, 'before_cursor_execute', merken)
            try:
                _galerie_context(request, db, user, objekt)
            finally:
                event.remove(conn, 'before_cursor_execute', merken)
            return len(abfragen)

        # Beziehungen/Rechte einmal laden, dann dieselbe Abfragezahl vergleichen.
        anzahl()
        vorher = anzahl()
        for nr in range(2, 7):
            db.add(ObjektDokumentSeite(org_id=objekt.org_id, objekt_id=objekt.id,
                   dokument_id=plan['dokument_id'], seiten_nr=nr, bild_pfad='plan.png'))
            db.flush()
            geo.referenzpunkte(db, objekt, plan['dokument_id'], nr, 'setzen', punkte(), None, user.id)
        assert anzahl() == vorher


def test_mcp_editor_link_hinweise_und_pruefen(client, plan):
    token = _token(client, plan, 'u')
    args = dict(objekt_id=plan['objekt_id'], dokument_id=plan['dokument_id'], seite=1)
    link = (f'{settings.effective_public_base_url.rstrip("/")}/objekte/{plan["objekt_id"]}'
            f'/dokumente/seite/{_seite(plan)}/georef')
    for aktion, extra in [('lesen', {}), ('setzen', {'punkte': punkte()[:3], 'pruefen': True})]:
        antwort = _rufe(client, token, 'objekt_karte_referenzpunkte', **args, aktion=aktion, **extra)
        assert antwort['editor_link'] == link
        assert isinstance(antwort['hinweise'], list)
        if aktion == 'setzen':
            assert antwort['hinweise'][0]['code'] == 'wenige_punkte'
            assert len(antwort['georeferenz']['vorschau_ecken']) == 4

    with SessionLocal() as db:
        set_tenant_context(db, plan['org_id'])
        assert db.query(ObjektDokumentGeoref).count() == 0
    _rufe(client, token, 'objekt_karte_referenzpunkte', **args, aktion='setzen', punkte=punkte()[:3])
    gelesen = _rufe(client, token, 'objekt_karte_referenzpunkte', **args, aktion='lesen')
    assert gelesen['hinweise'] == gelesen['georeferenz']['hinweise']
    assert gelesen['hinweise'][0]['code'] == 'wenige_punkte'
