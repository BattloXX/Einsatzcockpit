"""Regressionen fuer ChatGPT-Dateianhaenge an MCP-Objektdokumenten."""

import io
import socket

import httpx
import pytest
from pypdf import PdfWriter

from app.services.mcp_datei_url_service import lade_datei_von_url
from tests.test_mcp_objekte import _mcp, _seed, _token
from tests.test_mcp_objekt_dokumente import _antwort, _rufe


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _adressen(*werte: str):
    return [(socket.AF_INET6 if ":" in wert else socket.AF_INET, socket.SOCK_STREAM, 6, "", (wert, 443)) for wert in werte]


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("url", ["http://example.test/datei.pdf"])
def test_datei_url_service_lehnt_unsicheres_schema_ab(url):
    with pytest.raises(ValueError, match="HTTPS"):
        lade_datei_von_url(url, 100, transport=_transport(lambda request: httpx.Response(200)))


@pytest.mark.parametrize("adresse", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_datei_url_service_lehnt_interne_adressen_ab(monkeypatch, adresse):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _adressen(adresse))
    with pytest.raises(ValueError, match="unzulaessige"):
        lade_datei_von_url("https://datei.test/a.pdf", 100, transport=_transport(lambda request: httpx.Response(200)))


def test_datei_url_service_prueft_redirect_und_groesse(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, *args, **kwargs: _adressen("10.0.0.1" if host == "intern.test" else "8.8.8.8"),
    )

    def weiterleitung(request):
        if request.url.host == "datei.test":
            return httpx.Response(302, headers={"location": "https://intern.test/a.pdf"})
        return httpx.Response(200, content=_pdf())

    with pytest.raises(ValueError, match="unzulaessige"):
        lade_datei_von_url("https://datei.test/a.pdf", 1000, transport=_transport(weiterleitung))
    with pytest.raises(ValueError, match="zu gross"):
        lade_datei_von_url(
            "https://datei.test/a.pdf", 5,
            transport=_transport(lambda request: httpx.Response(200, headers={"content-length": "6"})),
        )
    with pytest.raises(ValueError, match="zu gross"):
        lade_datei_von_url(
            "https://datei.test/a.pdf", 5,
            transport=_transport(lambda request: httpx.Response(200, content=b"%PDF-zu gross")),
        )


def test_datei_url_service_prueft_pdf_status_und_liefert_bytes(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _adressen("8.8.8.8"))
    with pytest.raises(ValueError, match="HTTP 404"):
        lade_datei_von_url("https://datei.test/a.pdf", 1000, transport=_transport(lambda request: httpx.Response(404)))
    with pytest.raises(ValueError, match="kein PDF"):
        lade_datei_von_url("https://datei.test/a.pdf", 1000, transport=_transport(lambda request: httpx.Response(200, content=b"text")))
    assert lade_datei_von_url(
        "https://datei.test/a.pdf", 10000, transport=_transport(lambda request: httpx.Response(200, content=_pdf()))
    ) == _pdf()


def test_mcp_dateianhang_faellt_auf_dateiname_zurueck(client, monkeypatch):
    seed = _seed("mcp-datei-url", {"u": "objekt_verwalter"})
    monkeypatch.setattr("app.mcp.tools.objekt_dokumente.lade_datei_von_url", lambda *args: _pdf())
    result = _rufe(
        client, _token(client, seed, "u"), "objekt_dokument_uebergeben",
        objekt_id=seed["objekt_id"],
        datei={"download_url": "https://files.example/plan.pdf", "file_id": "file_1", "file_name": "plan.pdf"},
        seiten=[{"nr": 1, "dokumentart": ""}],
    )
    assert result["seitenzahl"] == 1
    fehler = _rufe(
        client, _token(client, seed, "u"), "objekt_dokument_uebergeben",
        objekt_id=seed["objekt_id"], dateiname="plan.pdf", inhalt_base64="eA==",
        datei={"download_url": "https://files.example/plan.pdf", "file_id": "file_1"},
    )
    assert "Genau eines" in fehler["__fehler__"]


def test_mcp_dateianhang_schema_enthaelt_file_params(client):
    seed = _seed("mcp-datei-schema", {"u": "objekt_verwalter"})
    response = _mcp(client, _token(client, seed, "u"), "tools/list", {}, 1)
    assert response.status_code == 200, response.text
    tool = next(tool for tool in _antwort(response)["result"]["tools"] if tool["name"] == "objekt_dokument_uebergeben")
    assert tool["_meta"] == {"openai/fileParams": ["datei"]}
    datei_schema = tool["inputSchema"]["properties"]["datei"]
    if "anyOf" in datei_schema:
        datei_schema = next(schema for schema in datei_schema["anyOf"] if schema.get("type") != "null")
    if "$ref" in datei_schema:
        datei_schema = tool["inputSchema"]["$defs"][datei_schema["$ref"].rsplit("/", 1)[-1]]
    assert set(datei_schema["required"]) == {"download_url", "file_id"}
    assert datei_schema["properties"]["mime_type"].get("default") is None
    assert datei_schema["properties"]["file_name"].get("default") is None
