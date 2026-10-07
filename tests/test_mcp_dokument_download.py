"""Regressionen fuer kurzlebige MCP-Downloads von Objektdokumenten."""

import base64
from hashlib import sha256
from io import BytesIO
from urllib.parse import urlsplit

from pypdf import PdfWriter

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite
from tests.test_mcp_objekte import _mcp, _rufe, _seed, _token


def _pdf(seiten: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(seiten):
        writer.add_blank_page(width=100, height=100)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _dokument(client, seed: dict, *, seiten: int = 1) -> tuple[dict, bytes]:
    data = _pdf(seiten)
    result = _rufe(
        client,
        _token(client, seed, "admin"),
        "objekt_dokument_uebergeben",
        objekt_id=seed["objekt_id"],
        dateiname="plan.pdf",
        inhalt_base64=base64.b64encode(data).decode("ascii"),
        seiten=[{"nr": nr, "dokumentart": ""} for nr in range(1, seiten + 1)],
    )
    assert "__fehler__" not in result
    return result, data


def _download(client, seed: dict, dokument_id: int, **arguments) -> dict:
    return _rufe(
        client,
        _token(client, seed, "admin"),
        "objekt_dokument_herunterladen",
        dokument_id=dokument_id,
        **arguments,
    )


def test_mcp_dokument_download_happy_path_ist_cookie_unabhaengig(client):
    seed = _seed("mcp-download-happy", {"admin": "objekt_verwalter"})
    dokument, original = _dokument(client, seed)

    result = _download(client, seed, dokument["dokument_id"])
    client.cookies.clear()
    response = client.get(urlsplit(result["download_url"]).path)

    assert response.status_code == 200, response.text
    assert response.content == original
    assert result["sha256"] == sha256(original).hexdigest()
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/pdf")


def test_mcp_dokument_download_inline_und_groessenlimit(client, monkeypatch):
    seed = _seed("mcp-download-inline", {"admin": "objekt_verwalter"})
    dokument, original = _dokument(client, seed)

    inline = _download(client, seed, dokument["dokument_id"], inline=True)
    assert base64.b64decode(inline["inhalt_base64"]) == original
    monkeypatch.setattr(settings, "MCP_DOWNLOAD_INLINE_MAX_BYTES", 10)
    zu_gross = _download(client, seed, dokument["dokument_id"], inline=True)
    assert "inhalt_base64" not in zu_gross
    assert "hinweis" in zu_gross
    normal = _download(client, seed, dokument["dokument_id"], inline=False)
    assert "inhalt_base64" not in normal


def test_mcp_dokument_download_einzelseite_und_fehler(client):
    seed = _seed("mcp-download-seite", {"admin": "objekt_verwalter"})
    dokument, _ = _dokument(client, seed, seiten=2)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=dokument["dokument_id"], seiten_nr=1).one()
        assert seite.einzel_pdf_pfad
    finally:
        db.close()

    result = _download(client, seed, dokument["dokument_id"], seite=1)
    response = client.get(urlsplit(result["download_url"]).path)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    fehlend = _download(client, seed, dokument["dokument_id"], seite=99)
    assert "Keine Einzelseite vorhanden" in fehlend["__fehler__"]


def test_mcp_dokument_download_ist_organisationsgetrennt(client):
    erste = _seed("mcp-download-org-a", {"admin": "objekt_verwalter"})
    zweite = _seed("mcp-download-org-b", {"admin": "objekt_verwalter"})
    dokument, _ = _dokument(client, erste)

    fremd = _download(client, zweite, dokument["dokument_id"])
    assert "Dokument nicht gefunden" in fremd["__fehler__"]


def test_mcp_dokument_download_lehnt_manipulierte_und_abgelaufene_tokens_ab(client, monkeypatch):
    seed = _seed("mcp-download-token", {"admin": "objekt_verwalter"})
    dokument, _ = _dokument(client, seed)
    result = _download(client, seed, dokument["dokument_id"])
    token = urlsplit(result["download_url"]).path.rsplit("/", 1)[1]
    manipuliert = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    assert client.get(f"/api/mcp/downloads/{manipuliert}").status_code == 404

    monkeypatch.setattr(settings, "MCP_DOWNLOAD_TOKEN_MINUTEN", -1)
    assert client.get(urlsplit(result["download_url"]).path).status_code == 410


def test_mcp_dokument_download_prueft_rechte_beim_abruf_erneut(client):
    seed = _seed("mcp-download-live-rechte", {"admin": "objekt_verwalter"})
    dokument, _ = _dokument(client, seed)
    result = _download(client, seed, dokument["dokument_id"])
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one().mcp_modul_aktiv = False
        db.commit()
    finally:
        db.close()

    assert client.get(urlsplit(result["download_url"]).path).status_code == 403


def test_mcp_dokument_download_funktioniert_fuer_alte_version_und_archiv(client):
    seed = _seed("mcp-download-archiv", {"admin": "objekt_verwalter"})
    dokument, original = _dokument(client, seed)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(ObjektDokument, dokument["dokument_id"]).ist_aktuelle_version = False
        db.get(Objekt, seed["objekt_id"]).status = "archiviert"
        db.commit()
    finally:
        db.close()

    result = _download(client, seed, dokument["dokument_id"])
    assert result["ist_aktuelle_version"] is False
    response = client.get(urlsplit(result["download_url"]).path)
    assert response.status_code == 200
    assert response.content == original


def test_mcp_dokument_download_ist_ohne_objekt_verwalter_nicht_verfuegbar(client):
    seed = _seed("mcp-download-rolle", {"admin": "objekt_verwalter", "read": "readonly"})
    dokument, _ = _dokument(client, seed)
    token = _token(client, seed, "read")
    assert "objekt_dokument_herunterladen" not in _mcp(client, token, "tools/list", {}, 1).text
    assert "__fehler__" in _rufe(
        client, token, "objekt_dokument_herunterladen", dokument_id=dokument["dokument_id"]
    )


def test_mcp_dokumente_auflisten_liefert_groesse_und_mime(client):
    seed = _seed("mcp-download-list", {"admin": "objekt_verwalter"})
    dokument, original = _dokument(client, seed)
    listed = _rufe(
        client, _token(client, seed, "admin"), "objekt_dokumente_auflisten", objekt_id=seed["objekt_id"]
    )
    eintrag = next(eintrag for eintrag in listed["dokumente"] if eintrag["id"] == dokument["dokument_id"])
    assert eintrag["groesse_bytes"] == len(original)
    assert eintrag["mime"] == "application/pdf"
