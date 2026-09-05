"""Detail-UX: gemeinsame Aktionen, bedingte Widgets und Accordion-Fragmente."""
import os
from datetime import date, datetime
from pathlib import Path

import pytest

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.probenplanung import Probeart, ProbeChecklistItem, ProbePublicToken
from app.models.teilnahme import Termin
from tests.test_probenplanung_autosave import _setup
from tests.test_probenplanung_checkliste import ORG_ID

TABS = ('uebersicht', 'vorbereitung', 'skizze', 'dokumente', 'uebungseinsatz',
        'teilnehmer', 'nachbereitung', 'historie')


def test_detail_alle_tabs_aktionen_widgets_und_fragmente(client):
    csrf, termin_id, item_id = _setup(client, 'detail_ux')
    with SessionLocal() as db:
        set_tenant_context(db, None)
        termin = db.get(Termin, termin_id)
        db.get(Probeart, termin.probeart_id).uebungseinsatz_erlaubt = True
        item = db.get(ProbeChecklistItem, item_id)
        item.pflicht = True
        item.faellig_am = date(2000, 1, 1)
        db.commit()
    export = os.environ.get('PROBE_DETAIL_EXPORT')
    if export:
        Path(export).mkdir(parents=True, exist_ok=True)
    for tab in TABS:
        response = client.get(f'/probenplanung/{termin_id}?tab={tab}')
        assert response.status_code == 200
        html = response.text
        assert 'Übungseinsatz vorbereiten</button>' in html
        assert f'/probenplanung/{termin_id}/duplizieren' in html
        assert ('class="probe-detail-widgets"' in html) == (tab == 'uebersicht')
        assert '<h2>Öffentliche Ansicht</h2>' not in html
        assert 'Termin &amp; Zeitrahmen' in html
        if tab == 'vorbereitung':
            assert '<details class="probe-checklist-section" open>' in html
            assert 'data-section-progress>0/1 erledigt' in html
            assert 'Überfällig seit 01.01.2000' in html
        if export:
            Path(export, f'{tab}.html').write_text(html)
    for name, route in [('checkliste', 'checkliste'), ('exercise', 'uebungseinsatz'),
                        ('teilnehmer_fragment', 'teilnehmer')]:
        response = client.get(f'/probenplanung/{termin_id}/{route}')
        assert response.status_code == 200
        if export:
            Path(export, f'{name}.html').write_text(response.text)
    changed = client.patch(f'/probenplanung/{termin_id}/checkliste/punkt/{item_id}',
                          data={'_csrf': csrf, 'feld': 'zustand', 'wert': 'erledigt', 'version': '0'})
    assert changed.status_code == 200
    assert 'data-zustand="erledigt"' in changed.text
    assert 'Überfällig seit' not in changed.text
    if export:
        Path(export, 'item.html').write_text(changed.text)


@pytest.fixture
def keine_aktiven_public_tokens():
    # Die Testsuite teilt ihre DB. Frühere Tests dürfen den Ausgangszustand
    # dieses Tests nicht verändern; vorhandene Tokens danach wiederherstellen.
    with SessionLocal() as db:
        set_tenant_context(db, None)
        rows = db.query(ProbePublicToken).filter(
            ProbePublicToken.org_id == ORG_ID, ProbePublicToken.widerrufen_am.is_(None)
        ).all()
        ids = [row.id for row in rows]
        for row in rows:
            row.widerrufen_am = datetime(2000, 1, 1)
        db.commit()
    yield
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for token_id in ids:
            db.get(ProbePublicToken, token_id).widerrufen_am = None
        db.commit()


def test_public_widget_braucht_sichtbarkeit_und_aktiven_org_token(client, keine_aktiven_public_tokens):
    _, termin_id, _ = _setup(client, 'detail_public')
    with SessionLocal() as db:
        set_tenant_context(db, None)
        termin = db.get(Termin, termin_id)
        termin.public_sichtbar = True
        org_id = termin.org_id
        db.commit()
    url = f'/probenplanung/{termin_id}'
    assert '<h2>Öffentliche Ansicht</h2>' not in client.get(url).text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        token = ProbePublicToken(org_id=org_id, art='plan', token_hash='d' * 64)
        db.add(token)
        db.commit()
        token_id = token.id
    assert '<h2>Öffentliche Ansicht</h2>' in client.get(url).text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(Termin, termin_id).public_sichtbar = False
        db.commit()
    assert '<h2>Öffentliche Ansicht</h2>' not in client.get(url).text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        termin = db.get(Termin, termin_id)
        termin.public_sichtbar = True
        token = db.get(ProbePublicToken, token_id)
        token.widerrufen_am = termin.beginn
        db.commit()
    assert '<h2>Öffentliche Ansicht</h2>' not in client.get(url).text


def test_skizzen_widget_nimmt_neueste_skizze_und_cta_respektiert_probeart(client):
    from app.models.probenplanung import ProbeMedia

    _, termin_id, _ = _setup(client, 'detail_media')
    with SessionLocal() as db:
        set_tenant_context(db, None)
        termin = db.get(Termin, termin_id)
        art_id = termin.probeart_id
        for art, name in [('skizze', 'Alt'), ('skizze', 'Neu'), ('dokument', 'Dokumentbild')]:
            media = ProbeMedia(org_id=termin.org_id, termin_id=termin_id, art=art, name=name,
                               kind='image', mime_type='image/png', path='test.png',
                               hochgeladen_am=termin.beginn)
            db.add(media)
            db.flush()
            if name == 'Neu':
                neu_id = media.id
        db.get(Probeart, art_id).uebungseinsatz_erlaubt = False
        db.commit()
    html = client.get(f'/probenplanung/{termin_id}').text
    assert f'/probenplanung/medien/{neu_id}/thumb' in html
    assert 'alt="Neu"' in html
    assert 'alt="Dokumentbild"' not in html
    for tab in TABS:
        html = client.get(f'/probenplanung/{termin_id}?tab={tab}').text
        assert 'Übungseinsatz vorbereiten</button>' not in html
        assert 'Übungseinsatz starten</button>' not in html


def test_start_cta_auf_allen_tabs_aber_nicht_fuer_leser(client):
    from tests.test_probenplanung_checkliste import _login, _user
    from tests.test_probenplanung_uebungseinsatz import _probe

    csrf, termin_id = _probe(client, 'detail_start')
    response = client.post(f'/probenplanung/{termin_id}/uebungseinsatz',
                           data={'_csrf': csrf, 'alarm_type_code': 'T1'}, follow_redirects=False)
    assert response.status_code == 303
    for tab in TABS:
        html = client.get(f'/probenplanung/{termin_id}?tab={tab}').text
        assert 'Übungseinsatz starten</button>' in html
        assert f'action="/probenplanung/{termin_id}/uebungseinsatz/starten"' in html
    _user('detail_readonly', 'readonly')
    _login(client, 'detail_readonly')
    for tab in TABS:
        html = client.get(f'/probenplanung/{termin_id}?tab={tab}').text
        assert 'Übungseinsatz starten</button>' not in html
        assert f'action="/probenplanung/{termin_id}/archivieren"' not in html
