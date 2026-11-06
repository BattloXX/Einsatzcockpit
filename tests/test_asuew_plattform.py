"""Plattformneutralitaet, iOS-Mitueberwachung und bestehender Eskalationspfad."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.breathing import BreathingGeraetSitzung, BreathingWarnung
from app.models.master import FireDept
from app.models.user import FcmToken, User
from app.services import breathing_service as bs
from app.services import breathing_sitzung as svc
from tests.test_breathing_routes import breathing_routes  # noqa: F401
from tests.test_breathing_sitzung import ctx, service_start  # noqa: F401


def android(c, **changes):
    user = User(username=uuid4().hex, display_name="Mitueberwacher", org_id=c.user.org_id)
    c.db.add(user)
    c.db.flush()
    token = FcmToken(user_id=user.id, token=uuid4().hex, capabilities="asuew", platform="android")
    c.db.add(token)
    c.db.flush()
    return svc.start_sitzung(c.db, user, **{**c.data, "fcm_token": token.token, **changes}, now=c.now)


def start_ios(c, **changes):
    return c.client.post('/api/asuew/sitzung', json={**c.data, **changes}, headers={'X-ECP-Platform': 'ios'})


@pytest.mark.parametrize('header,fallback,expected', [
    (None, 'android', 'android'), (None, 'ios', 'ios'), (None, 'other', 'web'),
    ('android', 'ios', 'android'), ('ios', 'android', 'ios'), ('web', 'android', 'web'),
    ('', 'android', 'web'), ('Android', 'android', 'web'), ('unknown', 'ios', 'web'),
])
def test_klassifikation(header, fallback, expected):
    assert svc.client_platform(header, fallback) == expected


@pytest.mark.parametrize('header,expected,status', [(None, 'android', 200), ('android', 'android', 200),
                                                    ('ios', 'ios', 409), ('other', 'web', 403)])
def test_api_platform_persistiert(ctx, header, expected, status):
    c = ctx
    response = c.client.post('/api/asuew/sitzung', json=c.data,
                             headers={'X-ECP-Platform': header} if header is not None else {})
    assert response.status_code == status, response.text
    payload = response.json()
    assert payload['platform'] == expected
    row = c.db.query(BreathingGeraetSitzung).filter_by(session_key=payload['session_key']).one()
    assert row.platform == expected
    assert payload['monitoring_erlaubt'] is (status == 200)
    heartbeat = c.client.post('/api/asuew/heartbeat', json={})
    assert heartbeat.json()['platform'] == expected  # Fehlender Header erhaelt Sitzungsklassifikation.
    assert heartbeat.status_code == status


def test_ios_token_fallback(ctx):
    ctx.token.platform = 'ios'
    ctx.db.commit()
    r = ctx.client.post('/api/asuew/sitzung', json=ctx.data)
    assert r.status_code == 409 and r.json()['platform'] == 'ios'
    assert r.json()['error'] == 'ios_android_erforderlich'


@pytest.mark.parametrize('test', [None, {}, {'online': False}, {'exakte_wecker': False},
                                 {'benachrichtigung': 'denied'}])
def test_ios_braucht_gruenen_selbsttest(ctx, test):
    android(ctx)
    ctx.db.commit()
    r = start_ios(ctx, selbsttest=test)
    assert r.status_code == 409 and r.json()['error'] == 'ios_selbsttest'
    assert 'nur lesend' in r.json()['message']
    assert 'Android' in r.json()['plattform_hinweis']


@pytest.mark.parametrize('cause', ['fehlt', 'alt', 'beendet', 'sitzung_alt', 'rot', 'gelb',
                                  'kein_test', 'anderer_einsatz', 'ios', 'web', 'fremde_org'])
def test_ios_braucht_android_selber_einsatz(ctx, cause):
    c = ctx
    if cause != 'fehlt':
        row = android(c)
        if cause == 'alt': row.letzter_heartbeat_at = c.now - timedelta(seconds=61)
        if cause == 'beendet': row.beendet_at = c.now
        if cause == 'sitzung_alt': row.gestartet_at = c.now - timedelta(hours=25)
        if cause == 'rot': row.selbsttest = json.dumps({'online': False})
        if cause == 'gelb': row.selbsttest = json.dumps({'exakte_wecker': False})
        if cause == 'kein_test': row.selbsttest = None
        if cause == 'anderer_einsatz': row.incident_id = c.other_incident_id
        if cause in ('ios', 'web'): row.platform = cause
        if cause == 'fremde_org':
            org = FireDept(slug=uuid4().hex, name='Fremde Org')
            c.db.add(org)
            c.db.flush()
            c.db.get(User, row.user_id).org_id = org.id
    c.db.commit()
    r = start_ios(c)
    assert r.status_code == 409 and r.json()['error'] == 'ios_android_erforderlich'


@pytest.mark.parametrize('app_pflicht', [True, False])
def test_ios_verlust_wiederkehr_und_schreibschutz(ctx, app_pflicht):
    c = ctx
    a = android(c)
    c.settings.asuew_app_pflicht = app_pflicht
    c.db.commit()
    r = start_ios(c)
    assert r.status_code == 200 and r.json()['monitoring_erlaubt']
    key = r.json()['session_key']
    assert svc.validiere_sitzung(c.db, key, c.incident_id, c.user.id, c.now)
    a.beendet_at = c.now
    c.db.commit()
    assert svc.validiere_sitzung(c.db, key, c.incident_id, c.user.id, c.now) is None
    assert not svc.hat_frischen_heartbeat(c.db, c.incident_id, c.now)
    assert c.post(c.url + '/meldung', {'text': 'Gesperrt'}).status_code == 403
    page = c.client.get(f'/einsatz/{c.incident_id}/atemschutz')
    assert page.context['asuew_readonly']
    r = c.client.post('/api/asuew/heartbeat', json={})
    assert r.status_code == 409 and r.json()['error'] == 'ios_android_erforderlich'
    a.beendet_at = None
    c.db.commit()
    assert c.client.post('/api/asuew/heartbeat', json={}).status_code == 200
    assert c.post(c.url + '/meldung', {'text': 'Wieder erlaubt'}).status_code == 204


def test_roter_heartbeat_bleibt_persistiert(ctx):
    a = android(ctx)
    ctx.db.commit()
    r = start_ios(ctx)
    assert r.status_code == 200
    key = r.json()['session_key']
    r = ctx.client.post('/api/asuew/heartbeat', json={'selbsttest': {'online': False}})
    assert r.status_code == 409 and r.json()['error'] == 'ios_selbsttest'
    ctx.db.expire_all()
    row = ctx.db.query(BreathingGeraetSitzung).filter_by(session_key=key).one()
    assert json.loads(row.selbsttest) == {'online': False}
    assert svc.validiere_sitzung(ctx.db, key, ctx.incident_id, ctx.user.id) is None
    # Android-Selbsttest bleibt fuer die eigene Sitzung wie bisher nur eine Warnung.
    a.selbsttest = json.dumps({'online': False})
    ctx.db.flush()
    assert svc.validiere_sitzung(ctx.db, a.session_key, ctx.incident_id, a.user_id, ctx.now)


def test_ios_allein_nutzt_bestehende_notify_outbox(ctx, monkeypatch):
    c = ctx
    c.settings.asuew_app_pflicht = True
    c.settings.asuew_eskalation_sms = True
    c.settings.asuew_eskalation_user_ids = str(c.user.id)
    c.user.phone = '+43664000000'
    c.troop.status = 'im_einsatz'
    c.troop.entry_at = c.now
    row = service_start(c, platform='ios')
    c.db.commit()
    monkeypatch.setattr(bs, '_now', lambda: c.now)
    set_tenant_context(c.db, None)
    events = bs._collect_new_breathing_warnings(c.db)
    assert (c.incident_id, c.troop.id, 'kein_heartbeat') in events
    warning = c.db.query(BreathingWarnung).filter_by(troop_id=c.troop.id, kind='kein_heartbeat').one()
    assert {(z.kanal, z.stufe) for z in warning.zustellungen} == {('push', 0), ('sms', 1)}
    assert all(z.status == 'pending' for z in warning.zustellungen)
    android(c)
    c.db.commit()
    bs._collect_new_breathing_warnings(c.db)
    c.db.refresh(warning)
    assert warning.erledigt_at == c.now
    assert all(z.status == 'suppressed' for z in warning.zustellungen)
    assert svc.validiere_sitzung(c.db, row.session_key, c.incident_id, c.user.id, c.now)
