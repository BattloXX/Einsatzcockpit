"""P10: Datenmigration, sichere Darstellung und org-begrenzte Symbolpflege."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import settings
from app.core.icons import MERKMAL_ICON_NAMEN, icon_namen, merkmal_icon, render_icon
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.objekt import MerkmalKatalog
from scripts.icon_inventar import ZUORDNUNG
from tests.test_objekt_verwaltung import _login, _setup_objekt


@pytest.fixture
def migration():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/0301_merkmal_icon_name.py'
    spec = importlib.util.spec_from_file_location('migration_0301', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('zeichen,name', [
    ('🔑', 'key'), ('📕', 'book'), ('🚒', 'firetruck'), ('📻', 'radio'),
    ('🅿', 'parking'), ('☀', 'solar-panel'), ('🛗', 'elevator'), ('🚻', 'users-group'),
    ('🔥', 'flame'), ('💧', 'droplet'), ('🔒', 'lock'), ('🚪', 'door'), ('⚡', 'bolt'),
    ('🧯', 'fire-extinguisher'), ('🚰', 'fire-hydrant'), ('📍', 'map-pin'),
    ('⚠', 'alert-triangle'), ('ℹ', 'info-circle'), ('🌀', None), ('RWA', None),
    ('🌀 RWA', None), ('unbekannt', None), ('', None), (None, None),
])
def test_migration_mapping(migration, zeichen, name):
    assert migration.icon_name_fuer_emoji(zeichen) == name
    if zeichen:
        assert migration.icon_name_fuer_emoji(f' \t{zeichen}\ufe0f\n') == name


def test_mapping_vollstaendig_und_vendort(migration):
    for zeichen, name in ZUORDNUNG.items():
        if not name.startswith('='):
            assert migration.icon_name_fuer_emoji(zeichen) == ('solar-panel' if zeichen == '☀' else name)
    assert set(migration.EMOJI_ICON_NAMEN.values()) <= icon_namen()
    assert set(MERKMAL_ICON_NAMEN) <= icon_namen()


def test_migration_upgrade_downgrade_alle_orgs(migration, tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    original = [(1, 1, ' 🔑️ '), (2, 2, '☀'), (3, 2, '🌀 RWA'), (4, 1, None)]
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE merkmal_katalog (id INTEGER PRIMARY KEY, org_id INTEGER, icon VARCHAR(40))')
        for ident, org, icon in original:
            conn.execute(sa.text('INSERT INTO merkmal_katalog VALUES (:id, :org, :icon)'),
                         {'id': ident, 'org': org, 'icon': icon})
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        rows = conn.execute(sa.text('SELECT id, org_id, icon, icon_name FROM merkmal_katalog ORDER BY id')).all()
        assert [tuple(row[:3]) for row in rows] == original
        assert [row.icon_name for row in rows] == ['key', 'solar-panel', None, None]
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert 'icon_name' not in {c['name'] for c in sa.inspect(conn).get_columns('merkmal_katalog')}
        assert [tuple(row) for row in conn.execute(sa.text('SELECT * FROM merkmal_katalog ORDER BY id'))] == original
    engine.dispose()


def test_rendering_und_escaping(monkeypatch):
    assert merkmal_icon(NS(icon_name='key', icon='alt')) == render_icon('key', size='sm')
    assert merkmal_icon(NS(icon_name='key'), size='lg') == render_icon('key', size='lg')
    assert merkmal_icon(NS(icon='<script>"&', icon_name=None)) == (
        '<span class="ecp-legacy-zeichen" aria-hidden="true">&lt;script&gt;&#34;&amp;</span>'
    )
    assert merkmal_icon(NS(icon=None)) == render_icon('tag', size='sm')
    assert merkmal_icon(None) == render_icon('tag', size='sm')
    assert merkmal_icon({'icon_name': 'key'}) == render_icon('key', size='sm')
    assert 'ecp-legacy-zeichen' in merkmal_icon({'icon': '🌀'})
    monkeypatch.setattr(settings, 'ICONS_STRICT', False)
    assert merkmal_icon(NS(icon_name='unbekannt')) == render_icon('help-square', size='sm')
    monkeypatch.setattr(settings, 'ICONS_STRICT', True)
    with pytest.raises(KeyError):
        merkmal_icon(NS(icon_name='unbekannt'))


def test_katalog_formular_crud_validierung_legacy_und_csrf(client):
    org_id, _ = _setup_objekt('p10-symbol-admin', nummer=91001)
    assert _login(client, 'p10-symbol-admin', 'Test1234!').status_code == 302
    csrf = client.cookies.get('ec_csrf')
    url = '/objekte/kataloge/merkmale/neu'
    assert client.post(url, data={'name': 'Ohne CSRF', 'icon_name': 'key'}, follow_redirects=False).status_code == 403
    for name in ['key', 'unbekannt']:
        response = client.post(url, data={'_csrf': csrf, 'name': 'P10 Neu', 'icon_name': name}, follow_redirects=False)
        assert response.status_code == (303 if name == 'key' else 400)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.query(MerkmalKatalog).filter_by(org_id=org_id, name='P10 Neu').one()
        assert row.icon_name == 'key' and row.icon is None
        ident = row.id
        row.icon_name, row.icon = None, '🌀 RWA'
        db.commit()
    page = client.get('/objekte/kataloge?tab=merkmale')
    assert page.status_code == 200
    assert 'name="icon_name"' in page.text and '>solar-panel</option>' in page.text
    assert 'ecp-legacy-zeichen' in page.text
    url = f'/objekte/kataloge/merkmale/{ident}/edit'
    for name, status in [('unbekannt', 400), ('', 303), ('firetruck', 303)]:
        response = client.post(url, data={'_csrf': csrf, 'name': 'P10 Neu', 'icon_name': name, 'aktiv': '1'},
                               follow_redirects=False)
        assert response.status_code == status
        with SessionLocal() as db:
            set_tenant_context(db, None)
            row = db.get(MerkmalKatalog, ident)
            assert row.icon_name == ('firetruck' if name == 'firetruck' else None)
            assert row.icon == (None if name == 'firetruck' else '🌀 RWA')


def test_fremdes_merkmal_nicht_bearbeitbar(client):
    _setup_objekt('p10-fremd-admin', nummer=91002)
    _login(client, 'p10-fremd-admin', 'Test1234!')
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug='p10-fremd', name='Fremde Organisation')
        db.add(org)
        db.flush()
        row = MerkmalKatalog(org_id=org.id, name='Fremdes Merkmal', icon_name='key', icon='🔑')
        db.add(row)
        db.commit()
        ident = row.id
    response = client.post(f'/objekte/kataloge/merkmale/{ident}/edit',
                           data={'_csrf': client.cookies.get('ec_csrf'), 'name': 'Manipuliert', 'icon_name': 'firetruck'},
                           follow_redirects=False)
    assert response.status_code == 404
    with SessionLocal() as db:
        set_tenant_context(db, None)
        row = db.get(MerkmalKatalog, ident)
        assert (row.name, row.icon_name, row.icon) == ('Fremdes Merkmal', 'key', '🔑')


def test_seed_befuellt_neues_feld_und_behaelt_legacy():
    from app.services.objekt_service import seed_objekt_kataloge

    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = FireDept(slug='p10-seed', name='Seed Organisation')
        db.add(org)
        db.flush()
        seed_objekt_kataloge(db, org.id)
        rows = {m.code: m for m in db.query(MerkmalKatalog).filter_by(org_id=org.id)}
        assert rows['pv'].icon_name == 'solar-panel' and rows['pv'].icon == '☀️'
        assert rows['rwa'].icon_name is None and rows['rwa'].icon == '🌀'
        assert rows['schluesselbox'].icon_name == 'key' and rows['schluesselbox'].icon == '🔑'
        rows['pv'].icon_name = 'sun'
        seed_objekt_kataloge(db, org.id)
        assert rows['pv'].icon_name == 'sun'
        db.rollback()
