"""MCP-PDF-Zwischenablage: Bearer-Upload bleibt kurzlebig und tenant-sicher."""
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from pypdf import PdfWriter

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.mcp import MCPUpload
from app.models.objekt import Objekt
from app.services.mcp_upload_service import purge_alte_uploads
from tests.test_mcp_objekte import _rufe, _seed, _token


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    data = BytesIO()
    writer.write(data)
    # Ein valides PDF darf kommentierende Daten nach %%EOF enthalten; dadurch wird
    # der Stream-Pfad mit einer realistischen, etwa 250-KB-grossen Datei geprueft.
    return data.getvalue() + b"%" + (b"x" * (250 * 1024))


def _vorbereitet(client, seed, user="admin", groesse=None):
    args = {"objekt_id": seed["objekt_id"], "dateiname": "plan.pdf"}
    if groesse is not None:
        args["groesse_bytes"] = groesse
    return _rufe(client, _token(client, seed, user), "objekt_dokument_upload_vorbereiten", **args)


def test_mcp_upload_happy_path_token_und_einmaligkeit(client):
    seed = _seed("mcp-upload-happy", {"admin": "objekt_verwalter"})
    vorbereitet = _vorbereitet(client, seed, groesse=len(_pdf()))
    response = client.post(
        urlsplit(vorbereitet["upload_url"]).path,
        headers={"Authorization": f"Bearer {vorbereitet['upload_token']}"},
        files={"datei": ("plan.pdf", _pdf(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sha256"] == sha256(_pdf()).hexdigest()
    assert response.json()["groesse_bytes"] == len(_pdf())
    assert response.json()["seitenzahl"] == 1
    again = client.post(
        urlsplit(vorbereitet["upload_url"]).path,
        headers={"Authorization": f"Bearer {vorbereitet['upload_token']}"},
        files={"datei": ("plan.pdf", _pdf(), "application/pdf")},
    )
    assert again.status_code == 409


def test_mcp_upload_rejects_wrong_expired_non_pdf_and_too_large(client):
    seed = _seed("mcp-upload-errors", {"admin": "objekt_verwalter"})
    vorbereitet = _vorbereitet(client, seed)
    url = urlsplit(vorbereitet["upload_url"]).path
    wrong = client.post(url, headers={"Authorization": "Bearer falsch"}, files={"datei": ("x.pdf", _pdf())})
    assert wrong.status_code == 401
    non_pdf = client.post(
        url, headers={"Authorization": f"Bearer {vorbereitet['upload_token']}"}, files={"datei": ("x.txt", b"kein pdf")}
    )
    assert non_pdf.status_code == 415
    expired = _vorbereitet(client, seed)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(MCPUpload).filter_by(upload_id=expired["upload_id"]).one()
        row.expires_at -= timedelta(minutes=30)
        db.commit()
    finally:
        db.close()
    assert client.post(
        urlsplit(expired["upload_url"]).path,
        headers={"Authorization": f"Bearer {expired['upload_token']}"}, files={"datei": ("x.pdf", _pdf())}
    ).status_code == 410
    assert "__fehler__" in _vorbereitet(client, seed, groesse=51 * 1024 * 1024)


def test_mcp_upload_tool_visibility_arbeitskopie_and_purge(client):
    seed = _seed("mcp-upload-purge", {"admin": "objekt_verwalter", "read": "readonly"})
    assert "__fehler__" in _vorbereitet(client, seed, "read")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        arbeitskopie = Objekt(
            org_id=seed["org_id"], nummer=9999, name="MCP Arbeitskopie", status="entwurf",
            entwurf_von_id=seed["objekt_id"],
        )
        db.add(arbeitskopie)
        db.commit()
        arbeitskopie_id = arbeitskopie.id
    finally:
        db.close()
    vorbereitet = _rufe(
        client, _token(client, seed, "admin"), "objekt_dokument_upload_vorbereiten",
        objekt_id=arbeitskopie_id, dateiname="plan.pdf",
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(MCPUpload).filter_by(upload_id=vorbereitet["upload_id"]).one()
        assert row.objekt_id == seed["objekt_id"]
        row.created_at -= timedelta(days=2)
        db.commit()
    finally:
        db.close()
    assert purge_alte_uploads() == 1


def test_mcp_upload_wird_uebergeben_und_nicht_wiederverwendet(client):
    seed = _seed("mcp-upload-uebergabe", {"admin": "objekt_verwalter", "anderer": "objekt_verwalter"})
    vorbereitet = _vorbereitet(client, seed)
    response = client.post(
        urlsplit(vorbereitet["upload_url"]).path,
        headers={"Authorization": f"Bearer {vorbereitet['upload_token']}"},
        files={"datei": ("plan.pdf", _pdf(), "application/pdf")},
    )
    assert response.status_code == 200

    ergebnis = _rufe(
        client,
        _token(client, seed, "admin"),
        "objekt_dokument_uebergeben",
        objekt_id=seed["objekt_id"],
        dateiname="plan.pdf",
        upload_id=vorbereitet["upload_id"],
        seiten=[{"nr": 1, "dokumentart": ""}],
    )
    assert ergebnis["klassifizierung_quelle"] == "client"
    assert ergebnis["unklassifizierte_seiten"] == []
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(MCPUpload).filter_by(upload_id=vorbereitet["upload_id"]).one()
        assert row.uebergeben_am is not None
        assert not (Path(settings.OBJEKT_MEDIA_DIR) / row.pfad).exists()
    finally:
        db.close()
    assert "__fehler__" in _rufe(
        client,
        _token(client, seed, "admin"),
        "objekt_dokument_uebergeben",
        objekt_id=seed["objekt_id"],
        dateiname="plan.pdf",
        upload_id=vorbereitet["upload_id"],
    )


def test_mcp_upload_uebergabe_prueft_eingabe_und_benutzer(client):
    seed = _seed("mcp-upload-uebergabe-fehler", {"admin": "objekt_verwalter", "anderer": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    assert "__fehler__" in _rufe(
        client, token, "objekt_dokument_uebergeben", objekt_id=seed["objekt_id"], dateiname="plan.pdf", seiten=[]
    )
    assert "__fehler__" in _rufe(
        client, token, "objekt_dokument_uebergeben", objekt_id=seed["objekt_id"], dateiname="plan.pdf",
        inhalt_base64="", upload_id="beides", seiten=[]
    )
    vorbereitet = _vorbereitet(client, seed)
    response = client.post(
        urlsplit(vorbereitet["upload_url"]).path,
        headers={"Authorization": f"Bearer {vorbereitet['upload_token']}"},
        files={"datei": ("plan.pdf", _pdf(), "application/pdf")},
    )
    assert response.status_code == 200
    assert "__fehler__" in _rufe(
        client,
        _token(client, seed, "anderer"),
        "objekt_dokument_uebergeben",
        objekt_id=seed["objekt_id"],
        dateiname="plan.pdf",
        upload_id=vorbereitet["upload_id"],
    )
