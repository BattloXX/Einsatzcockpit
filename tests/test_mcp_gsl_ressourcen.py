"""MCP-Regressionen für die GSL-Ressourcenwerkzeuge."""

import json
from uuid import uuid4

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, MajorIncident
from tests.test_mcp_objekte import _mcp, _seed, _token


def _rpc(response):
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads([line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")][-1])
    return response.json()


def _call(client, token, tool, **arguments):
    response = _mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 72)
    assert response.status_code == 200, response.text
    result = _rpc(response)["result"]
    if result.get("isError"):
        return {"__fehler__": json.dumps(result["content"], ensure_ascii=False)}
    return result.get("structuredContent", {}).get("result", json.loads(result["content"][0]["text"]))


def _lage(seed):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = MajorIncident(org_id=seed["org_id"], name="MCP GSL")
        db.add(lage)
        db.flush()
        einheit = LageEinheit(lage_id=lage.id, label="RLF MCP", status="bereitgestellt", resource_type="fahrzeug")
        db.add(einheit)
        db.commit()
        return lage.id, einheit.id
    finally:
        db.close()


def _new_seed(prefix, rollen):
    return _seed(f"{prefix}-{uuid4().hex[:8]}", rollen)


def test_alle_gsl_tools_lesend_und_schreibend(client):
    seed = _new_seed("mcp-gsl-tools", {"editor": "recorder"})
    lage_id, einheit_id = _lage(seed)
    token = _token(client, seed, "editor")

    assert _call(client, token, "gsl_ressourcen_liste", lage_id=lage_id)["ressourcen"][0]["id"] == einheit_id
    assert "allgemein" in _call(client, token, "gsl_ressource_details", lage_id=lage_id, einheit_id=einheit_id)
    assert _call(
        client,
        token,
        "gsl_ressource_aktualisieren",
        lage_id=lage_id,
        einheit_id=einheit_id,
        felder={"org_name": "FF MCP"},
    )
    assert (
        _call(client, token, "gsl_ressource_fuehrer_setzen", lage_id=lage_id, einheit_id=einheit_id, name="Max MCP")[
            "aenderung"
        ]
        == "neu"
    )
    assert "journal" in _call(
        client, token, "gsl_ressource_journal", lage_id=lage_id, einheit_id=einheit_id, neuer_eintrag="MCP-Test"
    )
    assert "staerke" in _call(client, token, "gsl_ressource_personal", lage_id=lage_id, einheit_id=einheit_id)
    assert "ausstattung" in _call(client, token, "gsl_ressource_ausstattung", lage_id=lage_id, einheit_id=einheit_id)
    # Der Versandpfad wird bei deaktiviertem Org-Schalter sicher verweigert; auch diese Antwort ist geheimnisfrei.
    sent = _call(client, token, "gsl_ressource_zugang_senden", lage_id=lage_id, einheit_id=einheit_id, bestaetigt=True)
    revoked = _call(client, token, "gsl_ressource_zugang_widerrufen", lage_id=lage_id, einheit_id=einheit_id)
    assert "__fehler__" in sent and revoked["status"] == "widerrufen"
    assert "gkz_" not in json.dumps([sent, revoked]) and "/gk#" not in json.dumps([sent, revoked])


def test_readonly_darf_nicht_schreiben_und_fremde_lage_ist_unsichtbar(client):
    own = _new_seed("mcp-gsl-own", {"readonly": "readonly"})
    foreign = _new_seed("mcp-gsl-foreign", {"editor": "recorder"})
    own_lage, own_einheit = _lage(own)
    foreign_lage, _ = _lage(foreign)
    token = _token(client, own, "readonly")
    assert "ressourcen" in _call(client, token, "gsl_ressourcen_liste", lage_id=own_lage)
    assert "__fehler__" in _call(
        client,
        token,
        "gsl_ressource_aktualisieren",
        lage_id=own_lage,
        einheit_id=own_einheit,
        felder={"org_name": "Nein"},
    )
    foreign_result = _call(client, token, "gsl_ressourcen_liste", lage_id=foreign_lage)
    assert "Lage nicht gefunden" in foreign_result["__fehler__"]


def test_antworten_enthalten_keine_gk_geheimnisse(client):
    seed = _new_seed("mcp-gsl-secret", {"editor": "recorder"})
    lage_id, einheit_id = _lage(seed)
    token = _token(client, seed, "editor")
    antworten = [
        _call(client, token, "gsl_ressource_details", lage_id=lage_id, einheit_id=einheit_id),
        _call(client, token, "gsl_ressource_zugang_widerrufen", lage_id=lage_id, einheit_id=einheit_id),
    ]
    text = json.dumps(antworten)
    assert "gkz_" not in text and "/gk#" not in text and "token_hash" not in text and "pin" not in text


def test_feature_aus_und_geschlossene_lage_werden_abgewiesen(client):
    from app.models.master import OrgSettings

    seed = _new_seed("mcp-gsl-feature", {"editor": "recorder"})
    lage_id, einheit_id = _lage(seed)
    token = _token(client, seed, "editor")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(MajorIncident).filter_by(id=lage_id).one().status = "closed"
        db.commit()
    finally:
        db.close()
    geschlossen = _call(
        client, token, "gsl_ressource_aktualisieren", lage_id=lage_id, einheit_id=einheit_id, felder={"org_name": "X"}
    )
    assert "nicht aktiv" in geschlossen["__fehler__"]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter_by(org_id=seed["org_id"]).one().mi_feature_ressourcen = False
        db.commit()
    finally:
        db.close()
    assert "__fehler__" in _call(client, token, "gsl_ressourcen_liste", lage_id=lage_id)


def test_gk_cookie_und_gk_token_oeffnen_kein_mcp(client):
    from tests.test_einheit_zugang import _zugang_mit_auftrag

    cookie, _, _, _ = _zugang_mit_auftrag()
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    client.cookies.set("ec_gk", cookie)
    assert client.post("/mcp", headers=headers, json=body).status_code == 401
    client.cookies.clear()
    fremd = {**headers, "Authorization": "Bearer gkz_erfunden"}
    assert client.post("/mcp", headers=fremd, json=body).status_code == 401
    client.cookies.set("ec_gk", cookie)
    assert client.post(
        "/api/mcp/uploads/abc", files={"datei": ("test.pdf", b"%PDF-1.4", "application/pdf")}
    ).status_code in (401, 403)
    assert client.get("/api/mcp/downloads/abc").status_code in (401, 403, 404)
