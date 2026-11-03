"""Bild-Uploads in Galerie und externem Objektpflege-Portal."""
import json
import re
from itertools import count
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgStorageUsage
from app.models.objekt import ObjektChange, ObjektDokument
from app.models.user import AuditLog
from tests.test_objekt_bild_normalisierung import _bild
from tests.test_objekt_dokumente_wartend_ui import _login, _setup
from tests.test_objekt_pr3 import _test_pdf
from tests.test_objektpflege_public import _auftrag, _csrf

_nummern = count(9741)


@pytest.fixture
def galerie(client, monkeypatch, tmp_path):
    from app.core.rate_limit import limiter

    if limiter is not None:
        monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    worker = Mock()
    monkeypatch.setattr("app.routers.ui_objekt_dokumente.verarbeite_dokument", worker)
    nummer = next(_nummern)
    username = f"bilder_ui_{nummer}"
    org_id, _, objekt_id = _setup(username, nummer=nummer)
    assert _login(client, username).status_code == 302
    return org_id, objekt_id, worker


def _upload(client, objekt_id, dateien, zusammenfassen=False):
    data = {"_csrf": client.cookies.get("ec_csrf")}
    if zusammenfassen:
        data["bilder_zusammenfassen"] = "1"
    return client.post(f"/objekte/{objekt_id}/dokumente/upload", data=data,
                       files=[("dateien", datei) for datei in dateien])


@pytest.mark.parametrize("anzahl,mit_pdf,haken,erwartet", [
    (3, False, True, [("erstes.pdf", 3)]),
    (2, True, True, [("erstes.pdf", 2), ("plan.pdf", 1)]),
    (2, False, False, [("erstes.pdf", 1), ("bild1.pdf", 1)]),
    (1, False, True, [("erstes.pdf", 1)]),
])
def test_galerie_uploads(client, galerie, anzahl, mit_pdf, haken, erwartet):
    org_id, objekt_id, worker = galerie
    dateien = [("erstes.jpg" if i == 0 else f"bild{i}.jpg", _bild(), "image/jpeg")
               for i in range(anzahl)]
    if mit_pdf:
        dateien.insert(1, ("plan.pdf", _test_pdf(1), "application/pdf"))
    response = _upload(client, objekt_id, dateien, haken)
    assert response.status_code == 200, response.text
    assert 'class="alert alert-danger"' not in response.text
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        dokumente = db.query(ObjektDokument).filter_by(objekt_id=objekt_id).order_by(ObjektDokument.id).all()
        assert [(d.dateiname_original, d.seitenzahl) for d in dokumente] == erwartet
        assert worker.call_count == len(erwartet)
        audit = db.query(AuditLog).filter_by(action="objekt.dokument_uploaded", entity_id=objekt_id).one()
        payload = {"anzahl": len(erwartet)}
        if haken and anzahl >= 2:
            payload["bilder_zusammengefasst"] = anzahl
        assert json.loads(audit.payload_json) == payload
        change = db.query(ObjektChange).filter_by(objekt_id=objekt_id, feld="dokument_upload").one()
        assert str(len(erwartet)) in change.after_json


@pytest.mark.parametrize("expliziter_dateiname", [False, True])
def test_leeres_kamerafeld(client, galerie, expliziter_dateiname):
    org_id, objekt_id, worker = galerie
    if expliziter_dateiname:
        # filename="" erzwingt ein echtes leeres UploadFile; httpx laesst sonst
        # den filename-Parameter weg und sendet stattdessen ein leeres Textfeld.
        body = (
            b'--bildtest\r\nContent-Disposition: form-data; name="_csrf"\r\n\r\n'
            + client.cookies.get("ec_csrf").encode()
            + b'\r\n--bildtest\r\nContent-Disposition: form-data; name="dateien"; filename="foto.jpg"\r\n'
            + b'Content-Type: image/jpeg\r\n\r\n' + _bild()
            + b'\r\n--bildtest\r\nContent-Disposition: form-data; name="dateien"; filename=""\r\n'
            + b'Content-Type: application/octet-stream\r\n\r\n\r\n--bildtest--\r\n'
        )
        response = client.post(f"/objekte/{objekt_id}/dokumente/upload", content=body,
                               headers={"Content-Type": "multipart/form-data; boundary=bildtest"})
    else:
        response = _upload(client, objekt_id, [
            ("foto.jpg", _bild(), "image/jpeg"), ("", b"", "application/octet-stream"),
        ])
    assert response.status_code == 200, response.text
    assert "Leere Datei" not in response.text
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        assert db.query(ObjektDokument).filter_by(objekt_id=objekt_id).one().seitenzahl == 1
    worker.assert_called_once()


@pytest.mark.parametrize("zu_gross", [False, True])
def test_sammelfehler_ohne_datei_oder_quota(client, galerie, tmp_path, monkeypatch, zu_gross):
    org_id, objekt_id, worker = galerie
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        usage = db.get(OrgStorageUsage, org_id)
        vorher = usage.used_bytes if usage else 0
    bild = _bild()
    if zu_gross:
        monkeypatch.setattr(settings, "OBJEKT_BILD_MAX_BYTES", len(bild) - 1)
    response = _upload(client, objekt_id, [
        ("foto.jpg", bild, "image/jpeg"),
        ("kaputt.jpg", b"\xff\xd8\xff\xe0" + b"kaputt" * 10, "image/jpeg"),
    ], True)
    assert response.status_code == 200
    assert ("Bild zu groß" if zu_gross else "Bild konnte nicht gelesen werden") in response.text
    with SessionLocal() as db:
        set_tenant_context(db, org_id)
        assert db.query(ObjektDokument).filter_by(objekt_id=objekt_id).count() == 0
        usage = db.get(OrgStorageUsage, org_id)
        assert (usage.used_bytes if usage else 0) == vorher
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]
    worker.assert_not_called()


def test_partial_upload_felder(client, galerie):
    _, objekt_id, _ = galerie
    response = client.get(f"/objekte/{objekt_id}/dokumente")
    assert response.status_code == 200
    assert 'name="bilder_zusammenfassen" value="1"' in response.text
    assert response.text.index('name="bilder_zusammenfassen"') < response.text.index('type="file"')
    assert 'accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"' in response.text
    assert 'capture="environment"' in response.text
    assert 'hx-swap="innerHTML"' in response.text


def test_objektpflege_bild_upload(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr("app.routers.objektpflege_public.verarbeite_dokument", Mock())
    auftrag_id, objekt_id, _, token = _auftrag("Bildpflege", ["dokumente"])
    csrf = _csrf(client, token)
    response = client.post(f"/objektpflege/{token}/dokumente/neu", data={"_csrf": csrf},
                           files={"datei": ("foto.jpg", _bild(), "image/jpeg")}, follow_redirects=False)
    assert response.status_code == 303, response.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        dokument = db.query(ObjektDokument).filter_by(objekt_id=objekt_id, pflegeauftrag_id=auftrag_id).one()
        assert dokument.dateiname_original == "foto.pdf"
        assert dokument.seitenzahl == 1
    portal = client.get(f"/objektpflege/{token}/pruefen")
    assert "PDF oder Foto auswählen" in portal.text
    assert 'accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"' in portal.text


@pytest.mark.parametrize("status", [413, 415])
def test_objektpflege_bildfehler_sichtbar(client, monkeypatch, status):
    _, objekt_id, _, token = _auftrag("Bildfehler", ["dokumente"])
    csrf = _csrf(client, token)
    if status == 413:
        monkeypatch.setattr(settings, "OBJEKT_BILD_MAX_BYTES", 1)
    data = _bild() if status == 413 else b"\xff\xd8\xff\xe0" + b"kaputt" * 10
    response = client.post(f"/objektpflege/{token}/dokumente/neu", data={"_csrf": csrf},
                           files={"datei": ("foto.jpg", data, "image/jpeg")}, headers={"Accept": "text/html"})
    assert response.status_code == status
    assert ("Bild zu groß" if status == 413 else "Bild konnte nicht gelesen werden") in response.text
    with SessionLocal() as db:
        set_tenant_context(db, None)
        assert db.query(ObjektDokument).filter_by(objekt_id=objekt_id).count() == 0


@pytest.mark.parametrize("pfad", ["app/templates/objekt/_dokumente.html", "app/templates/objektpflege/pruefen.html"])
def test_keine_smart_quotes_in_attributen(pfad):
    # Alle Vorkommen pruefen, auch fehlerhaft mit typografischen Quotes begrenzte Attribute.
    for tag in re.findall(r"<[^>]+>", Path(pfad).read_text()):
        assert not re.search("[“”„‘’]", tag), tag
