"""Bild-Uploads ohne Objekt-Auswahl und ueber alle MCP-Eingabepfade."""
import base64
from unittest.mock import Mock
from urllib.parse import urlsplit

import httpx
import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.mcp import MCPUpload
from app.models.objekt import Objekt, ObjektDokument
from app.services.mcp_datei_url_service import lade_datei_von_url
from app.services.mcp_upload_service import erstelle_upload
from app.services.objekt_dokument_service import ObjektDokumentFehler
from tests.test_mcp_datei_url import _adressen
from tests.test_mcp_objekte import _rufe, _seed, _token
from tests.test_objekt_bild_normalisierung import _bild
from tests.test_objekt_plan_upload import _login_http
from tests.test_objekt_plan_upload import plan_upload_setup as plan_upload_setup


@pytest.fixture(autouse=True)
def bild_ablage(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    with SessionLocal() as db:
        set_tenant_context(db, None)
        vorhandene_uploads = [row.upload_id for row in db.query(MCPUpload).all()]
    yield
    # Die gemeinsame Test-DB darf keine verbrauchten Uploads fuer Retention-Tests behalten.
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.query(MCPUpload).filter(MCPUpload.upload_id.notin_(vorhandene_uploads)).delete(synchronize_session=False)
        db.commit()


@pytest.fixture
def upload_ui(client, plan_upload_setup, monkeypatch):
    org_id, username = plan_upload_setup
    _login_http(client, username, "Test1234!")
    monkeypatch.setattr("app.services.objekt_ki_service.ki_klassifikation_enabled", lambda *args: True)
    for pfad in (
        "app.services.objekt_dokument_service.verarbeite_dokument",
        "app.services.objekt_ki_service.analysiere_unklassifizierte_seiten",
        "app.routers.ui_objekt._geocode_objekt",
    ):
        monkeypatch.setattr(pfad, Mock())
    return org_id


def _ui_post(client, name, data, mime):
    return client.post("/objekte/dokument-upload", data={"_csrf": client.cookies.get("ec_csrf")},
                       files=[("dateien", (name, data, mime))])


def test_ui_jpeg_identifiziert_und_speichert_pdf(client, upload_ui, monkeypatch):
    gesehen = []

    async def identifiziere(data, name, org_id):
        gesehen.append((data, name, org_id))
        return {"name": "Fotoplan", "quelle": "ki_vision"}

    monkeypatch.setattr("app.services.objekt_plan_upload_service.identifiziere_objekt", identifiziere)
    response = _ui_post(client, "foto.jpg", _bild(), "image/jpeg")
    assert response.status_code == 200, response.text
    assert response.context["ergebnisse"][0]["ok"] is True
    assert len(gesehen) == 1
    assert gesehen[0][0].startswith(b"%PDF")
    assert gesehen[0][1:] == ("foto.pdf", upload_ui)
    with SessionLocal() as db:
        set_tenant_context(db, upload_ui)
        dokument = db.query(ObjektDokument).filter_by(org_id=upload_ui).one()
        assert dokument.mime == "application/pdf"
        assert dokument.dateiname_original == "foto.pdf"
        assert db.get(Objekt, dokument.objekt_id).name == "Fotoplan"


@pytest.mark.parametrize("fall,meldung", [
    ("kaputt", "Bild konnte nicht gelesen werden"),
    ("html", "Nur PDF- oder Bilddateien"),
    ("gross", "Bild ist zu groß"),
    ("speichern", "Speicherfehler"),
])
def test_ui_bildfehler_als_ergebnis_ohne_dokument(client, upload_ui, monkeypatch, fall, meldung):
    data = b"\xff\xd8\xff\xe0" + b"muell" * 10
    name, mime = "kaputt.jpg", "image/jpeg"
    identifiziere = Mock()
    if fall == "html":
        data, name, mime = b"<html>kein PDF</html>", "seite.html", "text/html"
    elif fall == "gross":
        data = _bild()
        monkeypatch.setattr(settings, "OBJEKT_BILD_MAX_BYTES", len(data) - 1)
    elif fall == "speichern":
        data = _bild()

        async def identifiziere(data, name, org_id):
            return {"name": "Wird zurueckgerollt", "quelle": "ki_vision"}

        monkeypatch.setattr("app.services.objekt_dokument_service.store_dokument_bytes",
                            Mock(side_effect=ObjektDokumentFehler("Speicherfehler", 413)))
    monkeypatch.setattr("app.services.objekt_plan_upload_service.identifiziere_objekt", identifiziere)
    response = _ui_post(client, name, data, mime)
    assert response.status_code == 200, response.text
    ergebnis = response.context["ergebnisse"][0]
    assert ergebnis["ok"] is False
    assert meldung in ergebnis["meldung"]
    if fall != "speichern":
        identifiziere.assert_not_called()
    with SessionLocal() as db:
        set_tenant_context(db, upload_ui)
        assert db.query(ObjektDokument).filter_by(org_id=upload_ui).count() == 0
        assert db.query(Objekt).filter_by(org_id=upload_ui).count() == 0


@pytest.mark.parametrize("weg", ["upload", "base64", "datei"])
def test_mcp_bild_uebergeben(client, monkeypatch, weg):
    seed = _seed(f"bild-mcp-{weg}", {"u": "objekt_verwalter"})
    token = _token(client, seed, "u")
    data = _bild(format="PNG" if weg == "upload" else "JPEG")
    name = "foto.png" if weg == "upload" else "foto.jpg"
    args = {"objekt_id": seed["objekt_id"], "dateiname": name,
            "seiten": [{"nr": 1, "dokumentart": "", "volltext": "Bildanalyse auf Seite 1"}]}
    if weg == "upload":
        vorbereitet = _rufe(client, token, "objekt_dokument_upload_vorbereiten",
                            objekt_id=seed["objekt_id"], dateiname=name)
        response = client.post(urlsplit(vorbereitet["upload_url"]).path,
                               headers={"Authorization": f"Bearer {vorbereitet['upload_token']}"},
                               files={"datei": (name, data, "image/png")})
        assert response.status_code == 200, response.text
        assert response.json()["seitenzahl"] == 1
        with SessionLocal() as db:
            set_tenant_context(db, seed["org_id"])
            assert db.query(MCPUpload).filter_by(upload_id=vorbereitet["upload_id"]).one().seitenzahl == 1
        args["upload_id"] = vorbereitet["upload_id"]
    elif weg == "base64":
        args["inhalt_base64"] = base64.b64encode(data).decode()
    else:
        monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: _adressen("8.8.8.8"))
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=data))

        def laden(url, limit, **kwargs):
            return lade_datei_von_url(url, limit, transport=transport, **kwargs)

        monkeypatch.setattr("app.mcp.tools.objekt_dokumente.lade_datei_von_url", laden)
        args.pop("dateiname")
        args["datei"] = {"download_url": "https://files.example/foto.jpg", "file_id": "file_bild",
                         "file_name": name, "mime_type": "image/jpeg"}
    result = _rufe(client, token, "objekt_dokument_uebergeben", **args)
    assert result["seitenzahl"] == 1, result
    assert result["dateiname"] == "foto.pdf"
    with SessionLocal() as db:
        set_tenant_context(db, seed["org_id"])
        dokument = db.get(ObjektDokument, result["dokument_id"])
        assert dokument.objekt_id == seed["objekt_id"]
        assert dokument.mime == "application/pdf"
        assert dokument.seitenzahl == 1
        assert dokument.dateiname_original == "foto.pdf"
        assert dokument.seiten[0].volltext == "Bildanalyse auf Seite 1"


@pytest.mark.parametrize("zweck", ["strassensperre", "entwurf"])
def test_mcp_nicht_objekt_bild_bleibt_415(client, zweck):
    seed = _seed(f"bild-nicht-objekt-{zweck}", {"u": "objekt_verwalter"})
    with SessionLocal() as db:
        set_tenant_context(db, seed["org_id"])
        from app.models.user import User

        user = db.query(User).filter_by(org_id=seed["org_id"]).one()
        row, token = erstelle_upload(db, user, seed["org_id"], None, "foto.png", None, zweck=zweck)
        upload_id = row.upload_id
    response = client.post(f"/api/mcp/uploads/{upload_id}", headers={"Authorization": f"Bearer {token}"},
                           files={"datei": ("foto.png", _bild(format="PNG"), "image/png")})
    assert response.status_code == 415, response.text
    assert "Nur PDF-Dateien" in response.text


def test_url_download_bleibt_standardmaessig_pdf(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: _adressen("8.8.8.8"))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=_bild(format="PNG")))
    with pytest.raises(ValueError, match="kein PDF"):
        lade_datei_von_url("https://files.example/foto.png", 10000, transport=transport)
