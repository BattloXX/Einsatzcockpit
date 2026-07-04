"""Tests für den LIS-SOAP-Client: MTOM/XOP-Parsing (kein Netzwerkzugriff nötig)."""
import gzip

from app.services.lis.lis_client import (
    LisClientError,
    _find_fault,
    _parse_mtom_binary,
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


def test_result_list_parses_repeated_elements():
    xml = """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
      <s:Body><GetOperationsResponse xmlns="http://x">
        <GetOperationsResult>
          <Operation><Id>op-1</Id><Number>f26005863</Number></Operation>
          <Operation><Id>op-2</Id><Number>f26005864</Number></Operation>
        </GetOperationsResult>
      </GetOperationsResponse></s:Body></s:Envelope>"""
    root = ET.fromstring(xml)
    items = _result_list(root, "GetOperationsResult")
    assert len(items) == 2
    assert items[0]["Id"] == "op-1"
    assert items[1]["Number"] == "f26005864"
