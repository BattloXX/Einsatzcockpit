"""Tests für die Teams-Alarmierung: Karteninhalt (teams_card.py) und Dispatch-Entscheidung
Bot vs. Webhook-Fallback (teams_alarm_service.py) — Webhook-Versand mit gemocktem httpx,
kein echter Netzwerkzugriff."""
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.master import AlarmType, FireDept
from app.models.objekt import Objekt, ObjektEinsatz, ObjektKartenObjekt, ObjektSymbol
from app.models.teams_bot import AlarmToken, TeamsAlarmConfig, TeamsChannelBinding
from app.services import teams_alarm_service
from app.services.teams_card import build_incident_message_card
from tests.conftest import TestingSession

ORG_ID = 1


def _session():
    db = TestingSession()
    set_tenant_context(db, ORG_ID)
    return db


def _incident(**overrides) -> Incident:
    defaults = dict(
        primary_org_id=ORG_ID, alarm_type_code="T4", status="active",
        reason="Verkehrsunfall", address_street="Bundesstraße", address_no="1",
        address_city="Wolfurt", started_at=datetime(2026, 7, 4, 10, 0, tzinfo=UTC),
        lat=47.47, lng=9.73, alarm_token="tok_abc123",
    )
    defaults.update(overrides)
    return Incident(**defaults)


def _cfg(**overrides) -> TeamsAlarmConfig:
    defaults = dict(
        org_id=ORG_ID, enabled=True, send_exercise=False,
        include_map=True, include_gmaps_link=True, include_qr_link=True,
        include_board_link=True,
    )
    defaults.update(overrides)
    return TeamsAlarmConfig(**defaults)


def _public_alarm(db, *, token: str, with_objekt: bool = False):
    incident = _incident(alarm_token=None)
    db.add(incident)
    db.flush()
    db.add(AlarmToken(incident_id=incident.id, token_hash=__import__("hashlib").sha256(
        token.encode()
    ).hexdigest()))
    if with_objekt:
        # Nummer nicht aus der Einsatz-ID ableiten: SQLite vergibt IDs geloeschter
        # Einsaetze neu, liegengebliebene Testobjekte kollidierten dann (CI 2026-10-20).
        objekt = Objekt(org_id=ORG_ID, nummer=__import__("random").randint(10**6, 2 * 10**9),
                        name="Testobjekt")
        db.add(objekt)
        db.flush()
        db.add_all([
            ObjektEinsatz(
                org_id=ORG_ID, objekt_id=objekt.id, incident_id=incident.id,
                quelle="manuell", status="bestaetigt",
            ),
            ObjektKartenObjekt(
                org_id=ORG_ID, objekt_id=objekt.id, typ="bmz",
                lat=47.471, lng=9.731, label="Foyer", rotation=75,
            ),
            ObjektSymbol(
                org_id=ORG_ID, code=f"bild_{incident.id}", name="Bildzeichen",
                stil="bild", text=None, bild_pfad="symbole/test.svg", aktiv=True,
            ),
        ])
    db.commit()
    return incident


def test_public_alarm_org_logo_und_button_reihenfolge(client):
    token = "teams-public-logo-order"
    db = _session()
    try:
        org = db.get(FireDept, ORG_ID)
        altes_logo = org.logo_path
        org.logo_path = "/static/uploads/org-test-logo.png"
        _public_alarm(db, token=token)

        response = client.get(f"/alarm/{token}")
        assert response.status_code == 200
        assert org.logo_path in response.text
        assert response.text.index("In Google Maps öffnen") < response.text.index(
            'id="alarm-hydranten"'
        )

        org.logo_path = None
        db.commit()
        fallback = client.get(f"/alarm/{token}")
        assert "/static/img/logo_einsatzcockpit.png" in fallback.text
    finally:
        org.logo_path = altes_logo
        db.commit()
        db.close()


def test_public_alarm_objekt_liefert_kartenobjekte_und_bild_fallback(client):
    token = "teams-public-objekt"
    db = _session()
    try:
        _public_alarm(db, token=token, with_objekt=True)
        response = client.get(f"/alarm/{token}/objekt.json")
        assert response.status_code == 200
        assert response.json()["karten_objekte"] == [{
            "typ": "bmz", "lat": 47.471, "lng": 9.731,
            "geometry": None, "label": "Foyer", "rotation": 75,
        }]
        bild = next(s for s in response.json()["symbole"] if s["name"] == "Bildzeichen")
        assert bild["stil"] == "box"
        assert bild["text"] == "BILD"
        assert bild["bild"] is None
    finally:
        db.close()


def test_public_alarm_objekt_ohne_verknuepfung_und_ungueltiger_token(client):
    token = "teams-public-ohne-objekt"
    db = _session()
    try:
        incident = _public_alarm(db, token=token)
        incident_id = incident.id
    finally:
        db.close()

    response = client.get(f"/alarm/{token}/objekt.json")
    assert response.status_code == 200
    assert response.json() == {"karten_objekte": [], "symbole": []}
    assert client.get("/alarm/ungueltig/objekt.json").status_code == 404

    db = _session()
    try:
        alarm_token = db.query(AlarmToken).filter(
            AlarmToken.incident_id == incident_id
        ).first()
        alarm_token.expires_at = datetime(2020, 1, 1)
        db.commit()
    finally:
        db.close()
    assert client.get(f"/alarm/{token}/objekt.json").status_code == 404


# ── build_incident_message_card ──────────────────────────────────────────────
# Adaptive Card (in "attachments" gewrappt) — siehe teams_card.py-Docstring: der
# Workflows-Webhook (Nachfolger der alten Office-365-Connectors) verwirft Bild/Buttons
# im alten MessageCard-Schema, daher Umstieg auf Adaptive Card.

def _adaptive_content(card: dict) -> dict:
    return card["attachments"][0]["content"]


def _body_texts(card: dict) -> str:
    def texts(node):
        if isinstance(node, dict):
            if node.get("type") == "TextBlock":
                yield node["text"]
            if node.get("type") == "FactSet":
                for fact in node["facts"]:
                    yield f"**{fact['title']}:** {fact['value']}"
            for value in node.values():
                yield from texts(value)
        elif isinstance(node, list):
            for item in node:
                yield from texts(item)
    return "\n".join(texts(_adaptive_content(card)["body"]))


def _images(card: dict) -> list[dict]:
    return [b for b in _adaptive_content(card)["body"] if b.get("type") == "Image"]


def test_build_incident_message_card_includes_all_bausteine_by_default():
    incident = _incident()
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    assert card["type"] == "message"
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    text = _body_texts(card)
    assert "Bundesstraße 1" in text
    assert "Verkehrsunfall" in text
    images = _images(card)
    assert images[0]["url"] == "https://example.com/api/v1/teams/map/tok_abc123.png"

    action_titles = [a["title"] for a in _adaptive_content(card)["actions"]]
    assert any("Google Maps" in t for t in action_titles)
    # Primäraktion: öffentliche Einsatzinformation (vormals "Alarmübersicht")
    assert any("Einsatzinformation" in t for t in action_titles)
    assert action_titles[0].endswith("Einsatzinformation")


def test_identische_meldung_und_einsatzgrund_erscheinen_nur_einmal():
    text = _body_texts(build_incident_message_card(
        _incident(report_text="Verkehrsunfall"), _cfg(), base_url="https://example.com"))
    assert text.count("Verkehrsunfall") == 1
    assert "**Einsatzgrund:**" not in text


def test_abweichender_einsatzgrund_bleibt_erhalten():
    text = _body_texts(build_incident_message_card(
        _incident(report_text="Pkw gegen Baum", reason="Verkehrsunfall"),
        _cfg(), base_url="https://example.com"))
    assert "Pkw gegen Baum" in text
    assert "**Einsatzgrund:** Verkehrsunfall" in text


def test_nur_einsatzgrund_ohne_report_text_erzeugt_eine_meldungszeile():
    text = _body_texts(build_incident_message_card(
        _incident(report_text=None), _cfg(), base_url="https://example.com"))
    assert text.count("Verkehrsunfall") == 1
    assert "**Einsatzgrund:**" not in text


def test_leitstellennummer_steht_in_der_karte():
    text = _body_texts(build_incident_message_card(
        _incident(lis_operation_number="f26001234"), _cfg(), base_url="https://example.com"))
    assert "**Leitstellennummer:** f26001234" in text


def test_leitstellennummer_fehlt_ohne_nummer():
    text = _body_texts(build_incident_message_card(
        _incident(lis_operation_number=None), _cfg(), base_url="https://example.com"))
    assert "Leitstellennummer" not in text


def test_build_incident_message_card_respects_include_toggles():
    incident = _incident()
    cfg = _cfg(include_map=False, include_gmaps_link=False, include_qr_link=False,
               include_board_link=False)
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    assert not _images(card)
    assert "actions" not in _adaptive_content(card)


def test_build_incident_message_card_includes_board_link():
    incident = _incident(id=185)
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    actions = _adaptive_content(card)["actions"]
    board_actions = [a for a in actions if "Einsatz-Board" in a["title"]]
    assert board_actions == [{
        "type": "Action.OpenUrl",
        "title": "🖥 Einsatz-Board öffnen",
        "url": "https://example.com/einsatz/185",
    }]


def test_build_incident_message_card_omits_board_link_when_disabled():
    incident = _incident(id=185)
    cfg = _cfg(include_board_link=False)
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")

    action_titles = [a["title"] for a in _adaptive_content(card).get("actions", [])]
    assert not any("Einsatz-Board" in t for t in action_titles)


def test_build_incident_message_card_marks_exercise():
    incident = _incident(is_exercise=True)
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")
    assert "[ÜBUNG]" in _body_texts(card)


def test_build_incident_message_card_no_map_without_coords():
    incident = _incident(lat=None, lng=None)
    cfg = _cfg()
    card = build_incident_message_card(incident, cfg, base_url="https://example.com")
    assert not _images(card)
    # Wie in der Mail bleibt Navigation über die Adresse möglich.
    actions = _adaptive_content(card).get("actions", [])
    maps = next(a for a in actions if "Google Maps" in a["title"])
    assert "destination=Bundesstra%C3%9Fe+1%2C+Wolfurt" in maps["url"]


# ── Karten-Base-URL: öffentlich erreichbares Kartenbild für Teams ────────────

def test_card_base_url_prefers_public_base_url(monkeypatch):
    """Teams lädt das Kartenbild aus der Cloud → interne request.base_url wird durch die
    konfigurierte PUBLIC_BASE_URL ersetzt, damit das Bild erreichbar ist."""
    from app.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://einsatz.example.org")
    assert teams_alarm_service._card_base_url("http://192.168.1.5:8092") == "https://einsatz.example.org"


def test_card_base_url_falls_back_to_request_base(monkeypatch):
    """Ohne PUBLIC_BASE_URL bleibt es beim übergebenen base_url (Verhalten wie bisher)."""
    from app.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    assert teams_alarm_service._card_base_url("https://example.com") == "https://example.com"


# ── post_incident_card: Dispatch-Entscheidung ────────────────────────────────

def test_post_incident_card_noop_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook",
                         lambda *a, **kw: calls.append(1))

    db = _session()
    try:
        cfg = _cfg(enabled=False, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_skips_exercise_when_not_configured(monkeypatch):
    calls = []

    async def fake_webhook(*a, **kw):
        calls.append(1)
        return True, False, None, False
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, send_exercise=False, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        incident = _incident(is_exercise=True)
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_uses_webhook_when_no_bot_binding(monkeypatch):
    calls = []

    async def fake_webhook(webhook_url, incident, cfg, *, base_url, org):
        calls.append(webhook_url)
        return True, False, None, False
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/alarm")
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == ["https://outlook.office.com/webhook/alarm"]
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_bot_binding_falls_back_to_webhook(monkeypatch):
    bot_calls = []
    webhook_calls = []

    async def fake_bot(*a, **kw):
        bot_calls.append(1)

    async def fake_webhook(*a, **kw):
        webhook_calls.append(1)
        return True, False, None, False

    monkeypatch.setattr("app.services.teams_bot_service.post_incident_card_via_bot", fake_bot)
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, bot_enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/alarm")
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()
        db.add(TeamsChannelBinding(
            org_id=ORG_ID, target="alarm", service_url="https://smba.example/",
            conversation_id="19:abc@thread.tacv2", captured_at=datetime.now(UTC),
        ))
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert bot_calls == [1]
        # Bot-Versand ist noch ein Platzhalter -> Webhook als Fallback, sonst ginge nichts raus
        assert webhook_calls == [1]
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_skips_alarm_type_with_teams_alarm_disabled(monkeypatch):
    calls = []

    async def fake_webhook(*a, **kw):
        calls.append(1)
        return True, False, None, False
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        # T4 ist bereits durch seed_data.py fuer ORG_ID angelegt — hier nur den Flag umschalten
        # statt eine zweite Zeile (org_id, code) anzulegen (UNIQUE-Constraint).
        alarm_type = db.query(AlarmType).filter(
            AlarmType.org_id == ORG_ID, AlarmType.code == "T4",
        ).first()
        alarm_type.teams_alarm_enabled = False
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


def test_post_incident_card_refreshes_stale_coords_before_building_card(monkeypatch):
    """Regression (2026-07-05): ein paralleler Background-Task (eigene DB-Session, z.B.
    Geocoding) kann lat/lng NACH dem Laden dieses `incident`-Objekts persistieren. Ohne
    db.refresh() sieht build_incident_message_card() hier noch die alten (None-)Werte aus
    der Identity-Map — Kartenbild/Google-Maps-Button würden fälschlich fehlen."""
    captured = {}

    async def fake_webhook(webhook_url, incident, cfg, *, base_url, org):
        captured["lat"] = incident.lat
        captured["lng"] = incident.lng
        return True, False, None, False
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook", fake_webhook)

    db = _session()
    try:
        cfg = _cfg(enabled=True, webhook_url_alarm="https://outlook.office.com/webhook/x")
        db.add(cfg)
        incident = _incident(lat=None, lng=None)
        db.add(incident)
        # Ein zweiter, parallel laufender Prozess (eigene Session, eigene DB-Verbindung)
        # muss die Zeile erst SEHEN können — dafuer braucht es einen echten Commit statt
        # nur flush(); darum wird unten explizit statt per rollback() aufgeraeumt.
        db.commit()

        # Simuliert einen zweiten, parallel laufenden Prozess (eigene Session), der
        # zwischenzeitlich die Koordinaten setzt — wie _geocode_incident() in api_v1.py.
        other = TestingSession()
        set_tenant_context(other, ORG_ID)
        other_incident = other.get(Incident, incident.id)
        other_incident.lat = 47.488847
        other_incident.lng = 9.741011
        other.commit()
        other.close()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert captured["lat"] == 47.488847
        assert captured["lng"] == 9.741011
    finally:
        db.rollback()
        db.query(Incident).filter(Incident.id == incident.id).delete()
        db.query(TeamsAlarmConfig).filter(TeamsAlarmConfig.org_id == ORG_ID).delete()
        db.commit()
        db.close()


def test_post_incident_card_noop_when_no_target_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(teams_alarm_service, "_post_via_webhook",
                         lambda *a, **kw: calls.append(1))

    db = _session()
    try:
        cfg = _cfg(enabled=True)  # keine Webhook-URLs gesetzt
        db.add(cfg)
        incident = _incident()
        db.add(incident)
        db.flush()

        import asyncio
        asyncio.run(teams_alarm_service.post_incident_card(db, incident, base_url="https://example.com"))
        assert calls == []
    finally:
        db.rollback()
        db.close()


# ── _post_via_webhook: echte HTTP-Ebene mit gemocktem httpx ──────────────────

def test_post_via_webhook_posts_message_card(monkeypatch):
    captured = {}

    class _MockAsyncClient:
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(teams_alarm_service, "_teams_http_client", lambda: _MockAsyncClient())

    incident = _incident()
    cfg = _cfg()

    import asyncio
    result = asyncio.run(teams_alarm_service._post_via_webhook(
        "https://outlook.office.com/webhook/x", incident, cfg,
        base_url="https://example.com", org=None,
    ))
    assert result == (True, False, None, False)
    assert captured["url"] == "https://outlook.office.com/webhook/x"
    assert captured["json"]["type"] == "message"
    assert captured["json"]["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_post_via_webhook_rejects_non_https_url(monkeypatch):
    incident = _incident()
    cfg = _cfg()

    import asyncio
    monkeypatch.setattr(teams_alarm_service, "_teams_http_client", lambda: pytest.fail("HTTP call"))
    result = asyncio.run(teams_alarm_service._post_via_webhook(
        "http://insecure.example/webhook", incident, cfg,
        base_url="https://example.com", org=None,
    ))
    assert result == (False, False, "ungueltige Webhook-URL", False)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(200), (True, False, None, False)),
        (httpx.Response(429), (False, True, "HTTP 429", False)),
        (httpx.Response(503), (False, False, "HTTP 503", True)),
        (httpx.Response(400), (False, False, "HTTP 400", False)),
        (httpx.ConnectTimeout("timeout"), (False, True, "ConnectTimeout", False)),
        (httpx.ConnectError("Connect-Fehler"), (False, True, "ConnectError", False)),
        (httpx.PoolTimeout("Pool belegt"), (False, True, "PoolTimeout", False)),
        (httpx.ReadTimeout("Lesefehler"), (False, False, "ReadTimeout", True)),
        (httpx.WriteTimeout("Schreibfehler"), (False, False, "WriteTimeout", True)),
        (httpx.RemoteProtocolError("Protokollfehler"), (False, False, "RemoteProtocolError", True)),
        (httpx.ReadError("Lesefehler"), (False, False, "ReadError", True)),
        (httpx.WriteError("Schreibfehler"), (False, False, "WriteError", True)),
        (httpx.TransportError("Transportfehler"), (False, False, "TransportError", True)),
        (httpx.TimeoutException("Zeit abgelaufen"), (False, False, "TimeoutException", True)),
        (httpx.InvalidURL("Ungueltig"), (False, False, "InvalidURL", False)),
    ],
)
def test_post_payload_classifies_retryability(monkeypatch, response, expected):
    class Client:
        async def post(self, *args, **kwargs):
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(teams_alarm_service, "_teams_http_client", lambda: Client())
    import asyncio

    assert asyncio.run(teams_alarm_service._post_payload("https://example.test", {}, log_label="test")) == expected


@pytest.mark.parametrize("pin,token", [(None, "abc"), ("hash", "abc"), ("hash", None)])
def test_teams_pin_anmeldung_nur_mit_pin_und_token(pin, token):
    incident = Incident(id=1, alarm_type_code="T1", access_pin_hash=pin, alarm_token=token)
    cfg = TeamsAlarmConfig(include_qr_link=False, include_board_link=False)
    card = build_incident_message_card(incident, cfg, base_url="https://example.com/")
    actions = _adaptive_content(card).get("actions", [])
    login_actions = [action for action in actions if action["title"] == "Mit PIN anmelden"]
    assert len(login_actions) == int(bool(pin and token))
    if login_actions:
        assert login_actions[0] == {"type": "Action.OpenUrl", "title": "Mit PIN anmelden",
                                    "url": "https://example.com/alarm/abc/anmelden"}


@pytest.mark.parametrize("code,style", [("F3", "attention"), ("T4", "accent"), ("S1", "emphasis")])
def test_teams_layout_schema_badge_und_groesse(code, style):
    card = build_incident_message_card(
        _incident(id=185, alarm_type_code=code, access_pin_hash="hash",
                  report_text="Pkw gegen Baum", dibos_bma_no="42", dibos_diagnose="Melder 1"),
        _cfg(), base_url="https://example.com",
    )
    content = _adaptive_content(card)
    assert content["version"] == "1.4"
    assert content["msteams"] == {"width": "Full"}
    header = content["body"][0]
    assert header["type"] == "ColumnSet"
    assert header["columns"][0]["items"][0]["style"] == style
    facts = next(b["facts"] for b in content["body"] if b["type"] == "FactSet")
    assert {f["title"]: f["value"] for f in facts} == {
        "Einsatzgrund": "Verkehrsunfall", "Brandmeldeanlage": "BMZ 42 – Melder 1", "Einsatznummer": "#185",
    }
    assert all(b["wrap"] for b in content["body"] if b["type"] == "TextBlock")
    assert content["actions"][0]["style"] == "positive"
    assert len(content["actions"]) <= 6
    assert len(json.dumps(card).encode("utf-8")) < 28_000


def test_teams_leere_felder_und_banner():
    incident = Incident(alarm_type_code="T1", is_exercise=True)
    content = _adaptive_content(build_incident_message_card(incident, _cfg(), base_url="https://example.com"))
    assert content["body"][0] == {
        "type": "Container", "style": "warning",
        "items": [{"type": "TextBlock", "text": "[ÜBUNG] – kein Realeinsatz", "wrap": True, "weight": "Bolder"}],
    }
    assert not any(b["type"] in ("FactSet", "Image") for b in content["body"])
    assert not any(b["type"] == "TextBlock" for b in content["body"])
    incident.is_exercise = False
    content = _adaptive_content(build_incident_message_card(incident, _cfg(), base_url="https://example.com"))
    assert content["body"][0]["type"] == "ColumnSet"


@pytest.mark.parametrize("toggle,missing", [
    ("include_map", ".png"), ("include_gmaps_link", "google.com"),
    ("include_qr_link", "Einsatzinformation"), ("include_board_link", "Einsatz-Board"),
])
def test_teams_einzelne_inhaltsschalter(toggle, missing):
    card = build_incident_message_card(_incident(), _cfg(**{toggle: False}), base_url="https://example.com")
    assert missing not in json.dumps(card, ensure_ascii=False)
    assert len(_adaptive_content(card)["actions"]) == (3 if toggle == "include_map" else 2)


def test_teams_und_mail_zeigen_dieselben_daten_inkl_objekt_gefahren(monkeypatch):
    from app.config import settings
    from app.models.objekt import GefahrenKatalog, ObjektGefahr
    from app.services.einsatzinfo_mail_service import build_mail_view

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://example.com")
    with _session() as db:
        org = db.get(FireDept, ORG_ID)
        alarm = AlarmType(org_id=ORG_ID, code="FTEST", category="F", label="Zimmerbrand")
        incident = _incident(alarm_type_code="FTEST", report_text="Rauch im Keller", access_pin_hash="hash",
                             dibos_bma_no="42", dibos_diagnose="Melder 1", lis_operation_number="F123")
        objekt = Objekt(org_id=ORG_ID, nummer=987654321, name="Wohnanlage")
        # Die Testdatenbank ist workerweit; andere Tests legen denselben Katalogeintrag an.
        gefahr = db.query(GefahrenKatalog).filter_by(org_id=ORG_ID, name="Gasanschluss").first()
        if gefahr is None:
            gefahr = GefahrenKatalog(org_id=ORG_ID, name="Gasanschluss", piktogramm_typ="gas")
            db.add(gefahr)
        db.add_all([alarm, incident, objekt])
        db.flush()
        db.add_all([
            ObjektEinsatz(org_id=ORG_ID, incident_id=incident.id, objekt_id=objekt.id,
                          quelle="manuell", status="bestaetigt"),
            ObjektGefahr(org_id=ORG_ID, objekt_id=objekt.id, gefahr_id=gefahr.id),
        ])
        db.flush()
        view = build_mail_view(db, incident, org, alarm, None, "", None)
        card = build_incident_message_card(incident, _cfg(), base_url="https://example.com", org=org, db=db)
        content = _adaptive_content(card)
        text = _body_texts(card)
        for key in ("stichwort", "stichwort_label", "adresse", "meldung", "alarmzeit"):
            assert view[key] in text
        facts = next(b["facts"] for b in content["body"] if b["type"] == "FactSet")
        assert [(f["title"], f["value"]) for f in facts] == view["details"]
        assert "Wohnanlage" in text and "Gasanschluss" in text
        assert _images(card)[0]["url"] == view["map_image_url"]
        assert [a["url"] for a in content["actions"]] == [
            view[k] for k in ("info_link", "anmeldelink", "navi_link", "board_link")
        ]
        assert len(json.dumps(card).encode("utf-8")) < 28_000


def test_webhook_nachversand_praefix_bleibt_mit_neuem_kopf_erhalten(monkeypatch):
    import asyncio

    sent = []

    async def post(_url, payload, **kwargs):
        sent.append(payload)
        return True, False, None, False

    monkeypatch.setattr(teams_alarm_service, "_post_payload", post)
    asyncio.run(teams_alarm_service._post_via_webhook(
        "https://example.com/hook", _incident(is_exercise=True), _cfg(),
        base_url="https://example.com", org=None, text_prefix="Nachversand: ",
    ))
    body = _adaptive_content(sent[0])["body"]
    assert body[0]["style"] == "warning"
    assert body[1]["text"] == "Nachversand: "
    assert body[2]["type"] == "ColumnSet"
