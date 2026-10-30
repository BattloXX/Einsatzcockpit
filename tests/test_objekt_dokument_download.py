"""Objekt-PDFs: Viewer-Einstiege, Downloads und nativer Cookie-Handoff."""
import io
from itertools import count
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import settings
from app.core.security import unsign_native_link_token
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.objekt import ObjektDokumentSeite
from tests.test_einheit_uebersicht import browser as browser
from tests.test_objekt_dokumente_wartend_ui import _dokument, _login, _setup

_nummern = count(1978100)


@pytest.fixture
def objekt_pdf(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    nummer = next(_nummern)
    username = f"pdf_{nummer}"
    org_id, user_id, objekt_id = _setup(username, nummer=nummer)
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=500)
    out = io.BytesIO()
    writer.write(out)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        dokument = _dokument(db, org_id, objekt_id, "Plan & Original.pdf")
        dokument.freigabe_status = "freigegeben"
        dokument.ist_aktuelle_version = True
        db.flush()
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=dokument.id).one()
        seite.einzel_pdf_pfad = dokument.pfad
        zweite = ObjektDokumentSeite(
            org_id=org_id, objekt_id=objekt_id, dokument_id=dokument.id, seiten_nr=2,
            dokumentart="lageplan", titel="Obergeschoss", einzel_pdf_pfad=dokument.pfad,
        )
        db.add(zweite)
        dokument.seitenzahl = 2
        db.flush()
        (tmp_path / dokument.pfad).write_bytes(out.getvalue())
        urls = {
            "original": f"/objekt-medien/dokument/{dokument.id}/original",
            "seite": f"/objekt-medien/seite/{seite.id}/pdf",
            "sammel": f"/objekte/{objekt_id}/dokumente/sammel-pdf",
        }
        zweite_url = f"/objekt-medien/seite/{zweite.id}/pdf"
        db.commit()
    assert _login(client, username).status_code == 302
    return {"urls": urls, "objekt_id": objekt_id, "user_id": user_id, "zweite_url": zweite_url}


@pytest.mark.parametrize("route", ["original", "seite", "sammel"])
@pytest.mark.parametrize("download", [False, True])
def test_pdf_disposition(client, objekt_pdf, route, download):
    response = client.get(objekt_pdf["urls"][route] + ("?download=1" if download else ""))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;" if download else "inline;")
    assert response.content.startswith(b"%PDF-")


def test_dokumentliste_und_seitenviewer(client, objekt_pdf):
    response = client.get(f'/objekte/{objekt_pdf["objekt_id"]}/dokumente')
    assert response.status_code == 200
    assert '<tbody data-dv-group>' in response.text
    assert 'data-dv-document data-dv-kind="pdf" data-dv-title="Plan &amp; Original.pdf"' in response.text
    assert f'href="{objekt_pdf["urls"]["original"]}?download=1" download' in response.text
    assert f'href="{objekt_pdf["urls"]["sammel"]}?download=1" download' in response.text
    viewer = client.get(f'/objekte/{objekt_pdf["objekt_id"]}/dokumente/viewer')
    assert viewer.status_code == 200
    assert '@click.prevent="openDocumentViewer(' in viewer.text
    assert '/static/js/dokumentviewer.js?' in viewer.text
    assert '/static/css/dokumentviewer.css?' in viewer.text


@pytest.mark.parametrize("route", ["original", "seite", "sammel"])
def test_download_handoff_erhaelt_query_und_pfadsperre(client, objekt_pdf, route):
    path = objekt_pdf["urls"][route]
    response = client.post('/api/v1/device/native-link', json={"path": path + '?download=1'})
    assert response.status_code == 200
    url = urlsplit(response.json()["url"])
    query = parse_qs(url.query)
    assert query["download"] == ["1"]
    assert unsign_native_link_token(query["nt"][0]) == (path, objekt_pdf["user_id"])
    with TestClient(app) as anon:
        response = anon.get(url.path + '?' + url.query, follow_redirects=False)
        assert response.status_code == 200
        assert response.headers['content-disposition'].startswith('attachment;')
        assert response.content.startswith(b'%PDF-')
        wrong_path = objekt_pdf["urls"]["seite" if route != "seite" else "original"]
        denied = anon.get(wrong_path + '?download=1&nt=' + query["nt"][0], follow_redirects=False)
        assert denied.status_code == 302
        assert denied.headers['location'] == '/login'


def test_pdf_button_per_touch_oeffnet_aktuelle_seite(client, objekt_pdf, browser):
    from playwright.sync_api import expect

    context = browser.new_context(viewport={"width": 390, "height": 844},
                                  has_touch=True, service_workers="block")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("""
        window.WebSocket = class extends EventTarget {
          constructor() { super(); this.readyState = 1; } close() {} send() {}
        };
        window.nativeBrowserUrls = [];
        window.Capacitor = { isNativePlatform: () => true, Plugins: {
          Browser: { open: async ({url}) => { window.nativeBrowserUrls.push(url); } }
        } };
    """)

    def handle(route):
        url = urlsplit(route.request.url)
        if url.netloc != "ecp.test":
            route.abort()
        elif url.path.startswith('/static/'):
            datei = Path('app') / url.path.lstrip('/')
            if datei.is_file():
                route.fulfill(path=str(datei))
            else:
                route.fulfill(status=404)
        else:
            response = client.get(url.path + ('?' + url.query if url.query else ''))
            headers = {k: v for k, v in response.headers.items()
                       if k not in ('content-length', 'content-encoding')}
            route.fulfill(status=response.status_code, body=response.content, headers=headers)

    page.route('**/*', handle)
    try:
        page.goto(f'https://ecp.test/objekte/{objekt_pdf["objekt_id"]}/dokumente/viewer')
        page.get_by_role('link', name='PDF', exact=True).tap()
        expect(page.locator('#documentViewer')).to_be_visible()
        expect(page.locator('#dv-name')).to_have_text('Erdgeschoss')
        expect(page.locator('[data-dv-download]')).to_have_attribute(
            'href', 'https://ecp.test' + objekt_pdf['urls']['seite'] + '?download=1')
        page.wait_for_function("document.querySelector('#documentViewer').dataset.renderedPage === '1'")
        page.get_by_role('button', name='Dokumentenviewer schließen').tap()
        page.get_by_role('button', name='Nächste Seite', exact=True).tap()
        page.get_by_role('link', name='PDF', exact=True).tap()
        expect(page.locator('#dv-name')).to_have_text('Obergeschoss')
        expect(page.locator('[data-dv-download]')).to_have_attribute(
            'href', 'https://ecp.test' + objekt_pdf['zweite_url'] + '?download=1')
        page.wait_for_function("document.querySelector('#documentViewer').dataset.renderedPage === '1'")
        assert page.evaluate('nativeBrowserUrls') == []
        assert not errors
    finally:
        context.close()
