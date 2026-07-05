"""Tests für den LIS-SOAP-Client: MTOM/XOP-Parsing (kein Netzwerkzugriff nötig)."""
import asyncio
import gzip

from app.services.lis.lis_client import (
    LisClient,
    LisClientError,
    _find_fault,
    _parse_mtom_binary,
    _result_dict,
    _result_list,
)
import xml.etree.ElementTree as ET


def _build_mtom_response(payload: bytes, gzip_compressed: bool = True) -> tuple[str, bytes]:
    boundary = "uuid:test-boundary-123"
    content_type = f'multipart/related; type="application/xop+xml"; boundary="{boundary}"'
    body_bytes = gzip.compress(payload) if gzip_compressed else payload
    body = (
        f"--{boundary}\r\n"
        'Content-Type: application/xop+xml;charset=utf-8;type="text/xml"\r\n\r\n'
        "<s:Envelope><s:Body><DownloadAttachmentResult>"
        '<xop:Include href="cid:http://tempuri.org/1/x" xmlns:xop="http://x"/>'
        "</DownloadAttachmentResult></s:Body></s:Envelope>\r\n"
        f"--{boundary}\r\n"
        "Content-Type: application/octet-stream\r\n"
        "Content-Transfer-Encoding: binary\r\n\r\n"
    ).encode("utf-8") + body_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return content_type, body


def test_parse_mtom_binary_extracts_and_decompresses_gzip():
    payload = b"raw document bytes \x00\x01\x02 more data"
    content_type, body = _build_mtom_response(payload, gzip_compressed=True)
    result = _parse_mtom_binary(content_type, body)
    assert result == payload


def test_parse_mtom_binary_without_gzip_layer():
    payload = b"\xff\xd8\xff\xe0 not actually gzip-compressed"
    content_type, body = _build_mtom_response(payload, gzip_compressed=False)
    result = _parse_mtom_binary(content_type, body)
    assert result == payload


def test_parse_mtom_binary_raises_without_binary_part():
    content_type = 'multipart/related; boundary="b1"'
    body = b'--b1\r\nContent-Type: application/xop+xml\r\n\r\n<x/>\r\n--b1--\r\n'
    try:
        _parse_mtom_binary(content_type, body)
        assert False, "expected LisClientError"
    except LisClientError:
        pass


# ── SOAP-Response-Parsing (Fault-Erkennung, generische Ergebnislisten) ───────

def test_find_fault_detects_soap_fault():
    xml = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body><s:Fault><faultcode>s:Client</faultcode>
      <faultstring>Unauthorized</faultstring></s:Fault></s:Body></s:Envelope>"""
    root = ET.fromstring(xml)
    assert _find_fault(root) == "Unauthorized"


def test_find_fault_none_on_normal_response():
    xml = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body><LoginResponse xmlns="http://x"/></s:Body></s:Envelope>"""
    root = ET.fromstring(xml)
    assert _find_fault(root) is None


def test_result_list_unwraps_real_tuple_shape():
    """Echter Mitschnitt (Capture 2026-07-04, Testeinsatz LIS): GetOperationsResult ist
    Tuple<List<Operation>, int>, serialisiert als genau zwei Kinder m_Item1 (Liste) +
    m_Item2 (Gesamtanzahl) — kein flaches Array direkt unter GetOperationsResult."""
    xml = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body><GetOperationsResponse xmlns="http://x">
        <GetOperationsResult xmlns:a="http://schemas.datacontract.org/2004/07/System">
          <a:m_Item1 xmlns:b="http://x/Types">
            <b:Operation><b:Id>op-1</b:Id><b:Number>f26005863</b:Number></b:Operation>
            <b:Operation><b:Id>op-2</b:Id><b:Number>f26005864</b:Number></b:Operation>
          </a:m_Item1>
          <a:m_Item2>2</a:m_Item2>
        </GetOperationsResult>
      </GetOperationsResponse></s:Body></s:Envelope>"""
    root = ET.fromstring(xml)
    items = _result_list(root, "GetOperationsResult")
    assert len(items) == 2
    assert items[0]["Id"] == "op-1"
    assert items[1]["Number"] == "f26005864"


def test_result_list_unwraps_empty_tuple_shape():
    """Echte leere Antwort: m_Item1 ohne Kinder, m_Item2 = '0'."""
    xml = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body><GetOperationsResponse xmlns="http://x">
        <GetOperationsResult xmlns:a="http://schemas.datacontract.org/2004/07/System">
          <a:m_Item1 xmlns:b="http://x/Types"/>
          <a:m_Item2>0</a:m_Item2>
        </GetOperationsResult>
      </GetOperationsResponse></s:Body></s:Envelope>"""
    root = ET.fromstring(xml)
    assert _result_list(root, "GetOperationsResult") == []


def test_result_list_falls_back_to_flat_array_without_tuple_wrapper():
    """GetTasks/GetOperationUnits/GetDocumentsByOperationId haben keine range/count/
    startIndex-Parameter und liefern vermutlich (unbestätigt) ein flaches Array ohne
    m_Item1/m_Item2-Wrapper — dafür muss der bisherige direkte-Kinder-Fallback bleiben."""
    xml = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body><GetTasksResponse xmlns="http://x">
        <GetTasksResult>
          <Task><Id>task-1</Id><Description>Testmeldung</Description></Task>
        </GetTasksResult>
      </GetTasksResponse></s:Body></s:Envelope>"""
    root = ET.fromstring(xml)
    items = _result_list(root, "GetTasksResult")
    assert len(items) == 1
    assert items[0]["Id"] == "task-1"


# ── Login: LoginResult.User.Id + automatischer AddSessionEntries-Aufruf ──────
# (Capture 2026-07-04: SelectOperation allein reichte NICHT gegen die GetTasks-
# NullReferenceException — Kandidat ist eine User-Identität in der Session.)

_LOGIN_RESPONSE_XML = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body><LoginResponse xmlns="http://services.intergraph.com/Emea/Pr/2011/03/Core">
    <LoginResult xmlns:a="http://services.intergraph.com/Emea/Pr/2011/03/Types"
                 xmlns:i="http://www.w3.org/2001/XMLSchema-instance">
      <a:Organization i:nil="true"/>
      <a:User>
        <a:Id>da8bfb94-304a-46aa-92c7-805b0c30da70</a:Id>
        <a:Language>de-DE</a:Language>
        <a:Name>johannes.battlogg</a:Name>
      </a:User>
    </LoginResult>
  </LoginResponse></s:Body></s:Envelope>"""

_EMPTY_ENVELOPE_XML = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
  <s:Body></s:Body></s:Envelope>"""


def test_result_dict_parses_login_result_user_id():
    root = ET.fromstring(_LOGIN_RESPONSE_XML)
    result = _result_dict(root, "LoginResult")
    assert result["User"]["Id"] == "da8bfb94-304a-46aa-92c7-805b0c30da70"


def test_login_captures_user_id_and_sends_add_session_entries(monkeypatch):
    calls: list[tuple[str, str]] = []
    client = LisClient("https://x.example/ipr", "LIS", "johannes.battlogg", "pw")

    async def fake_post(url, action, body, retry_on_auth=True):
        calls.append((action, body))
        if action.endswith("/Login"):
            return ET.fromstring(_LOGIN_RESPONSE_XML)
        return ET.fromstring(_EMPTY_ENVELOPE_XML)

    monkeypatch.setattr(client, "_post", fake_post)
    asyncio.run(client.login())

    assert client.user_id == "da8bfb94-304a-46aa-92c7-805b0c30da70"
    assert len(calls) == 3
    assert calls[0][0].endswith("/Login")
    assert calls[1][0].endswith("/AddSessionEntries")
    assert "da8bfb94-304a-46aa-92c7-805b0c30da70" in calls[1][1]
    assert "johannes.battlogg" in calls[1][1]
    assert calls[2][0].endswith("/Authorize")
    assert client.session_id in calls[2][1]


def test_authorize_posts_to_authorization_service_with_session_id(monkeypatch):
    """Der eigentliche Fix für die GetTasks-NullReferenceException (Live-Capture
    2026-07-04): AddSessionEntries mit ProjectId allein reichte nachweislich NICHT —
    ein Authorize-Aufruf gegen den separaten AuthorizationService (GMSC/Authorization.svc,
    eigener Namespace-Stamm ohne "/Pr/") lief im funktionierenden Referenz-Mitschnitt
    zwischen SelectOperation und GetTasks."""
    captured = {}
    client = LisClient("https://lis.example.at/ipr", "LIS", "u", "pw")
    client.session_id = "11111111-2222-3333-4444-555555555555"

    async def fake_post(url, action, body, retry_on_auth=True):
        captured["url"] = url
        captured["action"] = action
        captured["body"] = body
        return ET.fromstring(_EMPTY_ENVELOPE_XML)

    monkeypatch.setattr(client, "_post", fake_post)
    asyncio.run(client.authorize())

    assert captured["url"] == "http://lis.example.at/GMSC/Authorization.svc"
    assert captured["action"] == "http://services.intergraph.com/Emea/2011/03/AuthorizationService/Authorize"
    assert "11111111-2222-3333-4444-555555555555" in captured["body"]
    assert "<a:Site>LIS</a:Site>" in captured["body"]


def test_get_root_organizations_posts_to_operation_service(monkeypatch):
    """Experiment 3 (2026-07-05): byte-genau aus dem Smartclient-Referenz-Mitschnitt
    übernommen — GetRootOrganizations läuft über OperationService.svc (nicht CoreService.svc
    wie SelectOperation/AddSessionEntries), nur mit siteSession, ohne weitere Parameter."""
    captured = {}
    client = LisClient("https://lis.example.at/ipr", "LIS", "u", "pw")
    client.session_id = "11111111-2222-3333-4444-555555555555"
    client._logged_in = True

    async def fake_post(url, action, body, retry_on_auth=True):
        captured["url"] = url
        captured["action"] = action
        captured["body"] = body
        return ET.fromstring(_EMPTY_ENVELOPE_XML)

    monkeypatch.setattr(client, "_post", fake_post)
    asyncio.run(client.get_root_organizations())

    assert captured["url"] == "https://lis.example.at/ipr/OperationService.svc"
    assert captured["action"] == (
        "http://services.intergraph.com/Emea/Pr/2011/03/OperationService/GetRootOrganizations"
    )
    assert "<GetRootOrganizations xmlns=" in captured["body"]
    assert "11111111-2222-3333-4444-555555555555" in captured["body"]


def test_login_hashes_plaintext_password_by_default(monkeypatch):
    import hashlib

    captured = {}
    client = LisClient("https://x.example/ipr", "LIS", "u", "geheim123")

    async def fake_post(url, action, body, retry_on_auth=True):
        if action.endswith("/Login"):
            captured["body"] = body
            return ET.fromstring(_LOGIN_RESPONSE_XML)
        return ET.fromstring(_EMPTY_ENVELOPE_XML)

    monkeypatch.setattr(client, "_post", fake_post)
    asyncio.run(client.login())

    expected_hash = hashlib.sha1(b"geheim123").hexdigest()
    assert f"<password>{expected_hash}</password>" in captured["body"]


def test_login_sends_preconfigured_hash_unmodified_when_password_is_hash(monkeypatch):
    """OrgLisConfig.password_is_hash: Betreiber gibt nur den fertigen SHA1-Hash heraus,
    kein Klartext-Passwort — login() darf diesen Wert dann nicht nochmal hashen."""
    captured = {}
    precomputed_hash = "a" * 40
    client = LisClient(
        "https://x.example/ipr", "LIS", "u", precomputed_hash, password_is_hash=True,
    )

    async def fake_post(url, action, body, retry_on_auth=True):
        if action.endswith("/Login"):
            captured["body"] = body
            return ET.fromstring(_LOGIN_RESPONSE_XML)
        return ET.fromstring(_EMPTY_ENVELOPE_XML)

    monkeypatch.setattr(client, "_post", fake_post)
    asyncio.run(client.login())

    assert f"<password>{precomputed_hash}</password>" in captured["body"]


def test_login_sends_organization_id_session_entry_when_configured(monkeypatch):
    """Zweiter Live-Capture-Vergleich (2026-07-04): weder Konto noch Request-Form sind die
    Ursache der GetTasks-NullReferenceException — der funktionierende Referenz-Mitschnitt
    ist der Intergraph-IPR-Smartclient, der vor GetTasks eine Priming-Sequenz durchläuft,
    die unserer schlanken Poll-Client fehlt. Fix-Versuch: OrganizationId zusätzlich per
    AddSessionEntries injizieren (Session-Entries schreiben nachweislich SessionData —
    UserId/ProjectId kommen 1:1 in AuthorizeResult zurück)."""
    captured = {}
    client = LisClient(
        "https://x.example/ipr", "LIS", "u", "pw",
        organization_id="31ef7d2c-0b24-4057-8a5c-05a5662fd722",
    )

    async def fake_post(url, action, body, retry_on_auth=True):
        if action.endswith("/Login"):
            return ET.fromstring(_LOGIN_RESPONSE_XML)
        if action.endswith("/AddSessionEntries"):
            captured["body"] = body
        return ET.fromstring(_EMPTY_ENVELOPE_XML)

    monkeypatch.setattr(client, "_post", fake_post)
    asyncio.run(client.login())

    assert "<a:Key>OrganizationId</a:Key>" in captured["body"]
    assert "31ef7d2c-0b24-4057-8a5c-05a5662fd722" in captured["body"]


def test_login_skips_add_session_entries_when_user_id_missing(monkeypatch):
    """Falls LoginResult keinen User liefert, darf AddSessionEntries nicht mit
    leerem/None-UserId aufgerufen werden."""
    calls: list[str] = []
    client = LisClient("https://x.example/ipr", "LIS", "u", "pw")

    async def fake_post(url, action, body, retry_on_auth=True):
        calls.append(action)
        return ET.fromstring(_EMPTY_ENVELOPE_XML)

    monkeypatch.setattr(client, "_post", fake_post)
    asyncio.run(client.login())

    assert client.user_id is None
    assert len(calls) == 1
