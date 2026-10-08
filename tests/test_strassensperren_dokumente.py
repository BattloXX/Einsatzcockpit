"""PDF-Verordnungen an Straßensperren: Speicherung, Entwurf, MCP und UI."""

import base64
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from reportlab.pdfgen import canvas

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.invitation import OrgPartner
from app.models.mcp import MCPUpload
from app.models.road_closure import RoadClosure, RoadClosureChange, RoadClosureDocument, RoadClosureShare
from app.models.user import AuditLog
from app.services import road_closure_document_service as documents
from app.services import road_closure_section_service as section_service
from app.services.road_closure_section_resolver import SectionResult
from tests.test_mcp_objekte import _token
from tests.test_mcp_strassensperren import ROUTE, _bereit, _db, _rufe, _sperre
from tests.test_strassensperren_ui import _closure, _login, _setup_user

VERORDNUNG = """Bezirkshauptmannschaft Bregenz
Zl. BHBR-I-1234/2026
Verordnung
Gemäß § 43 StVO wird auf der Rebbergstraße sowie der L 190 im Bereich Kellaweg
von 06.10.2026, 07:00 Uhr bis 17.10.2026, 18:00 Uhr ein Fahrverbot für Fahrzeuge aller Art verordnet.
Ausgenommen sind Anrainer und Einsatzfahrzeuge."""


def _pdf(text: str = VERORDNUNG) -> bytes:
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    y = 800
    for line in text.splitlines():
        pdf.drawString(40, y, line)
        y -= 16
    pdf.save()
    return stream.getvalue()


@pytest.fixture()
def media(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    return tmp_path


def test_entwurf_aus_verordnungstext():
    draft = documents.entwurf_aus_text(VERORDNUNG)
    assert draft["valid_from"] == "2026-10-06T07:00"
    assert draft["valid_until"] == "2026-10-17T18:00"
    assert draft["reference_number"] == "BHBR-I-1234/2026"
    assert draft["authority"] == "Bezirkshauptmannschaft Bregenz"
    assert draft["restriction_type"] == "closed"
    assert draft["exceptions"] == "Ausgenommen sind Anrainer und Einsatzfahrzeuge."
    assert draft["strassen"][:3] == ["Rebbergstraße", "L 190", "Kellaweg"]
    assert draft["title_vorschlag"] == "Vollsperre Rebbergstraße"
    assert draft["begruendung"]["valid_from"] == "06.10.2026, 07:00 Uhr"


def test_entwurf_monatsnamen_masse_und_leerer_text():
    draft = documents.entwurf_aus_text(
        "Auf der Bregenzer Straße gilt ab 1. Oktober 2026 eine Gewichtsbeschränkung von 7,5 t "
        "bis 3. November 2026 9 Uhr."
    )
    assert draft["valid_from"] == "2026-10-01T00:00"
    assert draft["valid_until"] == "2026-11-03T09:00"
    assert draft["restriction_type"] == "weight_limit" and draft["max_weight_t"] == 7.5
    assert draft["strassen"] == ["Bregenzer Straße"]
    empty = documents.entwurf_aus_text("")
    assert empty["valid_from"] is None and empty["restriction_type"] is None and empty["strassen"] == []


def test_store_document_prueft_pdf_und_protokolliert(media):
    closure = _closure(org_id=1, title=f"Dok {uuid4().hex}")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.get(RoadClosure, closure.id)
        with pytest.raises(ValueError, match="Nur PDF"):
            documents.store_document(db, row, b"kein pdf", "x.pdf", None, "ui")
        document = documents.store_document(db, row, _pdf(), "../../verordnung.pdf", None, "ui")
        db.commit()
        assert document.filename == "verordnung.pdf"
        assert document.page_count == 1 and "Rebbergstraße" in document.extracted_text
        path = documents.absolute_path(document)
        assert path.is_file() and media in path.parents
        assert db.query(RoadClosureChange).filter_by(road_closure_id=row.id, action="document_added").count() == 1
        assert db.query(AuditLog).filter_by(action="road_closure.document_added", entity_id=document.id).count() == 1
        document.storage_path = "strassensperren/../../geheim.pdf"
        with pytest.raises(ValueError):
            documents.absolute_path(document)
        db.rollback()
    finally:
        db.close()


def test_mcp_entwurf_speichert_nichts_und_anhaengen(client, media, monkeypatch):
    seed = _bereit("mcp-sperre-dok", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")

    async def section(db, org, street, from_text, to_text, city=None):
        return SectionResult(geometry=ROUTE, quality="hoch", geometry_status="ok", osm_name=street)

    monkeypatch.setattr(section_service, "resolve_section", section)
    inhalt = base64.b64encode(_pdf()).decode("ascii")
    db = _db()
    try:
        before = db.query(RoadClosureDocument).count()
    finally:
        db.close()
    draft = _rufe(client, token, "strassensperre_entwurf_aus_pdf", inhalt_base64=inhalt)
    assert draft["entwurf"]["reference_number"] == "BHBR-I-1234/2026"
    assert draft["geometrie_vorschlag"]["osm_name"] == "Rebbergstraße"
    assert "Rebbergstraße" in draft["textauszug"]
    db = _db()
    try:
        assert db.query(RoadClosureDocument).count() == before
    finally:
        db.close()

    closure_id = _sperre(seed["org_id"], "PDF", ROUTE)
    attached = _rufe(
        client, token, "strassensperre_dokument_uebergeben",
        road_closure_id=closure_id, dateiname="bh.pdf", inhalt_base64=inhalt,
    )
    assert attached["dokument_id"] and attached["entwurf"]["restriction_type"] == "closed"
    read = _rufe(client, token, "strassensperre_lesen", road_closure_id=closure_id)
    assert [item["dateiname"] for item in read["dokumente"]] == ["bh.pdf"]
    client.cookies.clear()
    response = client.get(urlsplit(read["dokumente"][0]["download_url"]).path)
    assert response.status_code == 200 and response.content.startswith(b"%PDF")


def test_mcp_upload_zweck_wird_geprueft(client, media):
    seed = _bereit("mcp-sperre-upload", {"obj": "objekt_verwalter"})
    token = _token(client, seed, "obj")
    closure_id = _sperre(seed["org_id"], "Upload", ROUTE)
    prepared = _rufe(client, token, "strassensperre_dokument_upload_vorbereiten", road_closure_id=closure_id)
    entwurf = _rufe(client, token, "strassensperre_dokument_upload_vorbereiten")
    db = _db()
    try:
        assert db.query(MCPUpload).filter_by(upload_id=prepared["upload_id"]).one().zweck == "strassensperre"
        assert db.query(MCPUpload).filter_by(upload_id=entwurf["upload_id"]).one().zweck == "entwurf"
    finally:
        db.close()
    upload = client.post(
        urlsplit(entwurf["upload_url"]).path,
        headers={"Authorization": f"Bearer {entwurf['upload_token']}"},
        files={"datei": ("v.pdf", _pdf(), "application/pdf")},
    )
    assert upload.status_code in (200, 201), upload.text
    wrong = _rufe(
        client, token, "strassensperre_dokument_uebergeben",
        road_closure_id=closure_id, dateiname="v.pdf", upload_id=entwurf["upload_id"],
    )
    assert "__fehler__" in wrong
    draft = _rufe(client, token, "strassensperre_entwurf_aus_pdf", upload_id=entwurf["upload_id"])
    assert draft["entwurf"]["authority"] == "Bezirkshauptmannschaft Bregenz"
    db = _db()
    try:
        # Nicht in der gemeinsamen Test-DB liegen lassen: die Retention-Tests zählen global.
        for upload_id in (prepared["upload_id"], entwurf["upload_id"]):
            db.query(MCPUpload).filter_by(upload_id=upload_id).delete()
        db.commit()
    finally:
        db.close()


def test_mcp_download_fuer_partner_aber_nicht_fuer_fremde(client, media):
    owner = _bereit("mcp-sperre-dok-a", {"obj": "objekt_verwalter"})
    partner = _bereit("mcp-sperre-dok-b")
    foreign = _bereit("mcp-sperre-dok-c")
    closure_id = _sperre(owner["org_id"], "Geteilt", ROUTE)
    _rufe(
        client, _token(client, owner, "obj"), "strassensperre_dokument_uebergeben",
        road_closure_id=closure_id, dateiname="geteilt.pdf", inhalt_base64=base64.b64encode(_pdf()).decode(),
    )
    db = _db()
    try:
        db.add(OrgPartner(org_id=owner["org_id"], partner_org_id=partner["org_id"]))
        db.add(RoadClosureShare(road_closure_id=closure_id, org_id=partner["org_id"]))
        db.commit()
    finally:
        db.close()
    read = _rufe(client, _token(client, partner, "admin"), "strassensperre_lesen", road_closure_id=closure_id)
    client.cookies.clear()
    assert client.get(urlsplit(read["dokumente"][0]["download_url"]).path).status_code == 200
    assert "__fehler__" in _rufe(
        client, _token(client, foreign, "admin"), "strassensperre_lesen", road_closure_id=closure_id
    )
    from app.services.mcp_download_service import erstelle_download_token

    db = _db()
    try:
        document_id = db.query(RoadClosureDocument).filter_by(road_closure_id=closure_id).one().id
    finally:
        db.close()
    foreign_user = foreign["users"]["admin"]
    forged, _ = erstelle_download_token(foreign["org_id"], foreign_user, document_id, None, art="strassensperre")
    client.cookies.clear()
    assert client.get(f"/api/mcp/downloads/{forged}").status_code == 404


def test_ui_hochladen_anzeigen_rechte_und_loeschen(client, media):
    own = _closure(org_id=1, title=f"UI Dok {uuid4().hex}")
    manager = _setup_user("objekt_verwalter")
    _login(client, manager)
    csrf = {"_csrf": client.cookies.get("ec_csrf")}
    bad = client.post(
        f"/strassensperren/{own.id}/dokumente", data=csrf,
        files={"datei": ("x.pdf", b"kein pdf", "application/pdf")}, follow_redirects=True,
    )
    assert "Nur PDF-Dateien erlaubt." in bad.text
    response = client.post(
        f"/strassensperren/{own.id}/dokumente", data=csrf,
        files={"datei": ("verordnung.pdf", _pdf(), "application/pdf")}, follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        document = db.query(RoadClosureDocument).filter_by(road_closure_id=own.id).one()
        document_id, stored = document.id, Path(media) / document.storage_path
    finally:
        db.close()
    page = client.get(f"/strassensperren/{own.id}").text
    assert "verordnung.pdf" in page and "Geometrie" in page and "Deaktivieren" in page
    pdf = client.get(f"/strassensperren/{own.id}/dokumente/{document_id}")
    assert pdf.status_code == 200 and pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.headers["content-disposition"].startswith("inline")

    reader = _setup_user("readonly")
    _login(client, reader)
    assert client.get(f"/strassensperren/{own.id}/dokumente/{document_id}").status_code == 200
    denied = client.post(
        f"/strassensperren/{own.id}/dokumente/{document_id}/loeschen", data={"_csrf": client.cookies.get("ec_csrf")}
    )
    assert denied.status_code == 403

    stranger = _setup_user("objekt_verwalter", org_id=2)
    _login(client, stranger)
    assert client.get(f"/strassensperren/{own.id}/dokumente/{document_id}").status_code == 404

    _login(client, manager)
    removed = client.post(
        f"/strassensperren/{own.id}/dokumente/{document_id}/loeschen",
        data={"_csrf": client.cookies.get("ec_csrf")}, follow_redirects=False,
    )
    assert removed.status_code == 303 and not stored.exists()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(RoadClosureDocument, document_id) is None
    finally:
        db.close()
