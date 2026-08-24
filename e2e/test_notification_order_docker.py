"""Black-box HTTP notification ordering checks against Docker/MariaDB."""
from __future__ import annotations
import json, os, time, urllib.request
from urllib.parse import quote

BASE = os.getenv("E2E_BASE_URL", "http://localhost:8092")
SINK = os.getenv("E2E_SINK_URL", "http://localhost:8089")

def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                 headers={"content-type":"application/json", **(headers or {})},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())

def wait_events(count):
    end=time.time()+15
    while time.time()<end:
        events=request(SINK+"/events")
        if len(events)>=count: return events
        time.sleep(.1)
    raise AssertionError(f"only received {events}")

def wait_match(marker):
    end=time.time()+10
    while time.time()<end:
        if request(SINK+"/match?marker="+quote(marker))["exists"]: return True
        time.sleep(.05)
    return False

def assert_order(marker, response):
    events=[e for e in wait_events(2) if marker in json.dumps(e["payload"])]
    assert {e["path"] for e in events} == {"/messageapi/send", "/teams"}, events
    assert all(not e["match_exists_at_receipt"] for e in events), events
    assert response["einsatz_id"] if "einsatz_id" in response else response["id"]
    assert wait_match(marker), "object match never became visible in MariaDB"
    print(json.dumps({"marker": marker, "response": response, "events": events}, ensure_ascii=False))

def test_api_webhook_notification_precedes_matching():
    request(SINK+"/reset")
    marker=f"E2E-API-{time.time_ns()}"
    response=request(BASE+"/api/v1/einsatz", {"Key":marker,"Stufe":"F3","Strasse":"Orderingweg",
        "HausNr":"7","Ort":"Teststadt","Meldung":marker}, {"X-API-Key":"e2e-notification-api-key"})
    assert response["created"] is True
    assert_order(marker,response)

def test_gateway_notification_precedes_matching():
    request(SINK+"/reset")
    marker=f"E2E-GW-{time.time_ns()}"
    response=request(BASE+"/api/v1/gateway/alarms", {"raw_text":marker,"parse_status":"parsed",
        "parsed":{"alarm_type_code":"F4","street":"Orderingweg","house_no":"7","city":"Teststadt",
                  "reason":marker}}, {"Authorization":"Bearer e2e-notification-gateway-token"})
    assert response["duplicate"] is False
    assert_order(marker,response)
