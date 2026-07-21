"""Tests für den DIBOS-EventHub-Client (kein Netzwerkzugriff nötig).

Feldnamen in den Sample-Objekten sind 1:1 aus einem echten HTTP-Mitschnitt des
Elvis-Desktop-Clients (v2.3.0.1, DIBOS EventHub, Landeswarnzentrale Vorarlberg)
entnommen, nicht aus einer Dokumentation abgeleitet.
"""
import asyncio
import json

import httpx
import pytest

from app.services.dibos.dibos_client import (
    DibosAuthError,
    DibosClient,
    DibosClientError,
    parse_events,
    parse_radios,
    parse_units,
)

# ── Sample-Objekte aus dem echten Mitschnitt (GetPublicEvents/GetCurrentUnits/
# GetCurrentRadios-Antworten, gekürzt auf die für uns relevanten Felder) ────────

_SAMPLE_EVENT = {
    "id": 3286034, "eventNumber": "f26006249", "ag": "FW",
    "created": "2026-07-15T18:00:01", "dispatched": "2026-07-15T18:01:13", "closed": None,
    "tycod": "t1", "tycodDescription": "geringer technischer Einsatz",
    "eventComment": "Wassertransport für Heinrich Huetter Hütte > nur Florian Ziel ",
    "callerList": [{"callerName": "Stemer Andre KDT Vandans", "callerNumber": "06642667824"}],
    "targetList": [{"target": "19101", "targetType": "ALARMTEXT"}],
}

# Vollständiges Event (echter Mitschnitt 2026-07-21, Einsatz f26006436, Wolfurt
# Unterlinden 23) — für die um Einsatzort/Diagnose/BMA/Status/Kommentare
# erweiterten Felder, die parse_events() bisher komplett verwarf.
_SAMPLE_EVENT_FULL = {
    "id": 3294510, "eventNumber": "f26006436", "ag": "FW",
    "created": "2026-07-21T17:27:09", "dispatched": "2026-07-21T17:29:59", "closed": None,
    "tycod": "t2", "subTycod": "", "tycodDescription": "kleiner technischer Einsatz",
    "diagnose": "",
    "eventComment": "[Türöffnung] med. Notfall hinter verschlossener Türe",
    "bmaNo": None,
    "status": 1, "statusText": "AL", "statusTime": "2026-07-21T17:29:59",
    "locationCity": "WOLFURT", "locationDistrict": None, "locationCityPart": "WOLFURT-OT",
    "locationStreet": "UNTERLINDEN", "locationStreetNo": "23", "locationZipCode": None,
    "locationObject": "", "locationLongitude": 9.749971, "locationLatitude": 47.47214,
    "callerList": [{"callerName": "PI Wolfurt", "callerNumber": ""}],
    "targetList": [
        {"target": "FW SAM ABS 29 HOFSTEIG", "targetType": "POCSAG", "targetCount": "5", "description": "OHNE"},
    ],
    "comments": [
        {
            "id": 11487513, "messageType": 6, "isInternal": True,
            "comment": "### Sao-Einsatztyp: KRANK5",
            "creationDate": "2026-07-21T17:27:09", "creationPerson": "Fabian Partel",
        },
    ],
}

_SAMPLE_UNIT = {
    "id": 4565136, "unid": "mtf2_wolfu", "unidRfl": "FW Wolfurt MTF 2", "unitType": "mtf",
    "currentStatus": 9, "currentStatusText": "S2", "currentStatusTime": "2026-05-04T22:26:48",
    "longitude": 9.745592, "latitude": 47.460991, "eventNumber": "",
}

_SAMPLE_UNIT_FULL = {
    "id": 4892620, "unid": "rlf_wolfu", "unidRfl": "FW Wolfurt RLF-A", "unitType": "rlf",
    "station": "fw_wolfu", "ag": "FW",
    "currentStatus": 2, "currentStatusText": "S2", "currentStatusTime": "2026-07-21T17:40:08",
    "longitude": 9.745728, "latitude": 47.460881, "eventNumber": "f26006436",
    "al": "2026-07-21T17:37:08", "s4": "2026-07-21T17:37:08", "eta": "00:00:40.2000000",
}

_SAMPLE_RADIO = {
    "issi": "02848849", "alias": "FW-V-WOL-049", "talkGroup": None, "state": "off",
    "department": "FW - Wolfurt",
}


# ── Live-Parsing ─────────────────────────────────────────────────────────────

def test_parse_events_extracts_readable_fields():
    parsed = parse_events([_SAMPLE_EVENT])
    assert len(parsed) == 1
    e = parsed[0]
    assert e["eventNumber"] == "f26006249"
    assert e["tycodDescription"] == "geringer technischer Einsatz"
    assert e["callers"] == [{"name": "Stemer Andre KDT Vandans", "number": "06642667824"}]
    assert e["targets"] == ["19101"]


def test_parse_events_extracts_location_diagnose_bma_and_status():
    """Regression 2026-07-21 (Einsatz f26006436): der vollständige Einsatzort,
    Einsatzcode/Diagnose, BMA-Nr. und der Gesamtstatus wurden bisher komplett
    verworfen, obwohl im Mitschnitt vorhanden."""
    parsed = parse_events([_SAMPLE_EVENT_FULL])[0]
    assert parsed["tycod"] == "t2"
    assert parsed["diagnose"] == ""
    assert parsed["bmaNo"] is None
    assert parsed["status"] == 1
    assert parsed["statusText"] == "AL"
    assert parsed["location"] == {
        "city": "WOLFURT", "district": None, "cityPart": "WOLFURT-OT",
        "street": "UNTERLINDEN", "streetNo": "23", "zipCode": None, "object": "",
        "longitude": 9.749971, "latitude": 47.47214,
    }


def test_parse_events_extracts_target_details_and_keeps_old_targets_shape():
    """"targets" (reine String-Liste) bleibt unverändert (bestehende Konsumenten),
    "targetsDetailed" ergänzt Typ/Anzahl/Beschreibung zusätzlich."""
    parsed = parse_events([_SAMPLE_EVENT_FULL])[0]
    assert parsed["targets"] == ["FW SAM ABS 29 HOFSTEIG"]
    assert parsed["targetsDetailed"] == [
        {"target": "FW SAM ABS 29 HOFSTEIG", "targetType": "POCSAG", "targetCount": "5", "description": "OHNE"},
    ]


def test_parse_events_extracts_comments():
    parsed = parse_events([_SAMPLE_EVENT_FULL])[0]
    assert parsed["comments"] == [
        {
            "id": 11487513, "text": "### Sao-Einsatztyp: KRANK5", "isInternal": True,
            "messageType": 6, "creationDate": "2026-07-21T17:27:09", "creationPerson": "Fabian Partel",
        },
    ]


def test_parse_events_handles_missing_comments_and_location_gracefully():
    """Ältere/andere Events ohne "comments"/location*-Felder dürfen nicht crashen."""
    parsed = parse_events([_SAMPLE_EVENT])[0]
    assert parsed["comments"] == []
    assert parsed["location"] == {
        "city": None, "district": None, "cityPart": None, "street": None,
        "streetNo": None, "zipCode": None, "object": None, "longitude": None, "latitude": None,
    }


def test_parse_units_extracts_readable_fields():
    parsed = parse_units([_SAMPLE_UNIT])
    assert parsed[0]["unid"] == "mtf2_wolfu"
    assert parsed[0]["currentStatusText"] == "S2"
    assert parsed[0]["latitude"] == 47.460991


def test_parse_units_extracts_status_times():
    """Regression 2026-07-21: Fahrzeug-Statuszeiten (al/s4/eta) wurden bisher
    komplett verworfen, obwohl im Mitschnitt vorhanden (Anfahrt-Timeline)."""
    parsed = parse_units([_SAMPLE_UNIT_FULL])[0]
    assert parsed["station"] == "fw_wolfu"
    assert parsed["statusTimes"]["al"] == "2026-07-21T17:37:08"
    assert parsed["statusTimes"]["s4"] == "2026-07-21T17:37:08"
    assert parsed["statusTimes"]["eta"] == "00:00:40.2000000"
    assert parsed["statusTimes"]["s1"] is None


def test_parse_radios_extracts_readable_fields():
    parsed = parse_radios([_SAMPLE_RADIO])
    assert parsed[0]["issi"] == "02848849"
    assert parsed[0]["alias"] == "FW-V-WOL-049"
    assert parsed[0]["department"] == "FW - Wolfurt"


# ── WS-Security-Envelope ─────────────────────────────────────────────────────

def test_ws_envelope_contains_username_token():
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "gw_pw",
        "service.wolfurt.all", "s3cret!23",
    )
    envelope = client._ws_envelope().decode("utf-8")
    assert "<o:Username>service.wolfurt.all</o:Username>" in envelope
    assert "<o:Password>s3cret!23</o:Password>" in envelope
    assert "soapenv:Envelope" in envelope


# ── HTTP-Handshake (401 -> Cookie-Retry -> 200) + on_exchange-Hook ───────────

def _json_response(status_code: int, payload=None) -> httpx.Response:
    content = json.dumps(payload).encode("utf-8") if payload is not None else b""
    return httpx.Response(status_code, content=content)


def test_post_retries_once_on_401_then_succeeds(monkeypatch):
    calls: list[str] = []
    exchanges: list[tuple] = []
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "gw_pw",
        "service.wolfurt.all", "pw",
        on_exchange=lambda url, op, req, resp: exchanges.append((url, op, req, resp)),
    )

    responses = [_json_response(401), _json_response(200, [])]

    async def fake_post(url, content=None, headers=None):
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(client._client, "post", fake_post)

    result = asyncio.run(client.get_current_events())

    assert result == []
    assert len(calls) == 2  # erster 401, dann Retry -> 200
    assert len(exchanges) == 2
    assert exchanges[0][1] == "GetCurrentEvents"


def test_post_raises_auth_error_after_second_401(monkeypatch):
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "wrong_pw",
        "service.wolfurt.all", "pw",
    )

    async def fake_post(url, content=None, headers=None):
        return _json_response(401)

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(DibosAuthError):
        asyncio.run(client.get_current_events())


def test_get_public_events_sends_qty_and_ag_query_params(monkeypatch):
    captured_urls: list[str] = []
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "gw_pw",
        "service.wolfurt.all", "pw", ag="FW",
    )

    async def fake_post(url, content=None, headers=None):
        captured_urls.append(url)
        return _json_response(200, [_SAMPLE_EVENT])

    monkeypatch.setattr(client._client, "post", fake_post)

    result = asyncio.run(client.get_public_events(qty=15))

    assert captured_urls[0].endswith("Main/GetPublicEvents?qty=15&ag=FW")
    assert result == [_SAMPLE_EVENT]


def test_get_elvis_notification_sends_service_user_and_host(monkeypatch):
    captured_urls: list[str] = []
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "gw_pw",
        "service.wolfurt.all", "pw", host="testhost",
    )

    async def fake_post(url, content=None, headers=None):
        captured_urls.append(url)
        return _json_response(200, [])

    monkeypatch.setattr(client._client, "post", fake_post)

    asyncio.run(client.get_elvis_notification())

    assert "serviceUser=service.wolfurt.all" in captured_urls[0]
    assert "host=testhost" in captured_urls[0]


def test_test_connection_reports_failure_without_throwing(monkeypatch):
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "wrong_pw",
        "service.wolfurt.all", "pw",
    )

    async def fake_post(url, content=None, headers=None):
        return _json_response(401)

    monkeypatch.setattr(client._client, "post", fake_post)

    ok, message = asyncio.run(client.test_connection())
    assert ok is False
    assert "Anmeldung fehlgeschlagen" in message


def test_test_connection_reports_success(monkeypatch):
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "gw_pw",
        "service.wolfurt.all", "pw",
    )

    async def fake_post(url, content=None, headers=None):
        return _json_response(200, [_SAMPLE_EVENT])

    monkeypatch.setattr(client._client, "post", fake_post)

    ok, message = asyncio.run(client.test_connection())
    assert ok is True
    assert "1 eigene Einsätze" in message


def test_post_raises_client_error_on_invalid_json(monkeypatch):
    client = DibosClient(
        "https://dibos.example.at/Z_EventHub", "gw_user", "gw_pw",
        "service.wolfurt.all", "pw",
    )

    async def fake_post(url, content=None, headers=None):
        return httpx.Response(200, content=b"not json {{{")

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(DibosClientError):
        asyncio.run(client.get_current_events())
