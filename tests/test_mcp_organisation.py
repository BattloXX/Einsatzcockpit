"""HTTP-Regressionen fuer das MCP-Tool ``organisation_lesen``."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings
from tests.test_mcp_objekte import _mcp, _seed, _token


def _rpc(response) -> dict:
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads([line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")][-1])
    return response.json()


def _organisation_roh(client, token: str, **arguments) -> dict:
    response = _mcp(client, token, "tools/call", {"name": "organisation_lesen", "arguments": arguments}, 70)
    assert response.status_code == 200, response.text
    result = _rpc(response)["result"]
    assert not result.get("isError"), result
    return result


def _text_json(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _setze_logo(org_id: int, logo_path: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(FireDept, org_id).logo_path = logo_path
        db.commit()
    finally:
        db.close()


def test_organisation_stammdaten_und_sensible_felder(client):
    seed = _seed("org-stammdaten", {"leser": "readonly"})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, seed["org_id"])
        org.short_code = "ORG"
        org.contact_email = "leitung@example.test"
        org.contact_phone = "+43 1 234567"
        org.street = "Hauptplatz 1"
        org.city = "Wien"
        org.timezone = "Europe/Vienna"
        org.fallback_lat = 48.2082
        org.fallback_lng = 16.3738
        org.storage_quota_bytes = 987654
        org_settings = db.query(OrgSettings).filter_by(org_id=org.id).one()
        org_settings.primary_color = "#123456"
        org_settings.footer_text = "Footer der Organisation"
        org_settings.ai_mode = "byok"
        org_settings.ai_api_key_enc = "verschluesseltes-geheimnis"
        db.commit()
    finally:
        db.close()

    result = _organisation_roh(client, _token(client, seed, "leser"), logo_als_bild=False)
    assert len(result["content"]) == 1
    organisation = _text_json(result)
    assert organisation["name"] == "Org org-stammdaten"
    assert organisation["slug"] == "org-stammdaten"
    assert organisation["short_code"] == "ORG"
    assert organisation["contact_email"] == "leitung@example.test"
    assert organisation["contact_phone"] == "+43 1 234567"
    assert organisation["street"] == "Hauptplatz 1"
    assert organisation["city"] == "Wien"
    assert organisation["timezone"] == "Europe/Vienna"
    assert organisation["fallback_lat"] == 48.2082
    assert organisation["fallback_lng"] == 16.3738
    assert organisation["primary_color"] == "#123456"
    assert organisation["footer_text"] == "Footer der Organisation"
    text_json = result["content"][0]["text"]
    assert "ai_api_key_enc" not in text_json
    assert "ai_mode" not in text_json
    assert "storage_quota_bytes" not in text_json


def test_organisation_standardlogo_und_logo_als_bild_false(client):
    seed = _seed("org-standardlogo", {"leser": "readonly"})
    result = _organisation_roh(client, _token(client, seed, "leser"))
    organisation = _text_json(result)
    standard = Path("app/static/img/Logo-rot.png").read_bytes()

    assert organisation["logo"]["ist_standardlogo"] is True
    assert organisation["logo"]["url"].endswith("/static/img/Logo-rot.png")
    assert len(result["content"]) == 2
    assert result["content"][1]["type"] == "image"
    assert result["content"][1].get("mimeType", result["content"][1].get("mime_type")) == "image/png"
    assert result["content"][1]["data"] == base64.b64encode(standard).decode("ascii")

    ohne_bild = _organisation_roh(client, _token(client, seed, "leser"), logo_als_bild=False)
    assert len(ohne_bild["content"]) == 1


def test_organisation_eigenes_png_logo_als_bild(client):
    seed = _seed("org-png-logo", {"leser": "readonly"})
    logo_datei = Path(f"app/static/img/uploads/logo_org{seed['org_id']}.png")
    logo_datei.parent.mkdir(parents=True, exist_ok=True)
    inhalt = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9JvJ8AAAAASUVORK5CYII="
    )
    logo_datei.write_bytes(inhalt)
    try:
        _setze_logo(seed["org_id"], f"/static/img/uploads/{logo_datei.name}")
        result = _organisation_roh(client, _token(client, seed, "leser"))
        organisation = _text_json(result)

        assert organisation["logo"]["ist_standardlogo"] is False
        assert organisation["logo"]["sha256"] == hashlib.sha256(inhalt).hexdigest()
        assert "inhalt_base64" not in organisation["logo"]
        assert len(result["content"]) == 2
        assert result["content"][1].get("mimeType", result["content"][1].get("mime_type")) == "image/png"
        assert result["content"][1]["data"] == base64.b64encode(inhalt).decode("ascii")
    finally:
        logo_datei.unlink(missing_ok=True)


def test_organisation_svg_logo_liefert_quelltext_ohne_image_block(client):
    seed = _seed("org-svg-logo", {"leser": "readonly"})
    logo_datei = Path(f"app/static/img/uploads/logo_org{seed['org_id']}.svg")
    logo_datei.parent.mkdir(parents=True, exist_ok=True)
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    logo_datei.write_text(svg, encoding="utf-8")
    try:
        _setze_logo(seed["org_id"], f"/static/img/uploads/{logo_datei.name}")
        result = _organisation_roh(client, _token(client, seed, "leser"))
        organisation = _text_json(result)

        assert len(result["content"]) == 1
        assert organisation["logo"]["ist_standardlogo"] is False
        assert organisation["logo"]["svg_text"] == svg
        assert "inhalt_base64" not in organisation["logo"]
    finally:
        logo_datei.unlink(missing_ok=True)


@pytest.mark.parametrize("logo_path", ["/static/../../etc/passwd", "/static/img/uploads/nicht-vorhanden.png"])
def test_organisation_unsicheres_oder_fehlerhaftes_logo_faellt_zurueck(client, logo_path):
    seed = _seed(f"org-fallback-{len(logo_path)}", {"leser": "readonly"})
    _setze_logo(seed["org_id"], logo_path)

    organisation = _text_json(_organisation_roh(client, _token(client, seed, "leser"), logo_als_bild=False))
    assert organisation["logo"]["ist_standardlogo"] is True
    assert organisation["logo"]["url"].endswith("/static/img/Logo-rot.png")


def test_organisation_ist_auf_eigene_organisation_begrenzt(client):
    org_a = _seed("org-sicht-a", {"leser": "readonly"})
    org_b = _seed("org-sicht-b", {"leser": "readonly"})
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.get(FireDept, org_a["org_id"]).name = "Nur Organisation A"
        db.get(FireDept, org_b["org_id"]).name = "Nur Organisation B"
        db.commit()
    finally:
        db.close()

    organisation = _text_json(_organisation_roh(client, _token(client, org_b, "leser"), logo_als_bild=False))
    assert organisation["name"] == "Nur Organisation B"
    assert "Nur Organisation A" not in json.dumps(organisation, ensure_ascii=False)


def test_organisation_zu_grosses_logo_wird_nicht_inline_geliefert(client, monkeypatch):
    seed = _seed("org-logo-limit", {"leser": "readonly"})
    monkeypatch.setattr(settings, "MCP_LOGO_INLINE_MAX_BYTES", 10)

    result = _organisation_roh(client, _token(client, seed, "leser"))
    organisation = _text_json(result)
    assert len(result["content"]) == 1
    assert "hinweis" in organisation["logo"]
    assert "inhalt_base64" not in organisation["logo"]
