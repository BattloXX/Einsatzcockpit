"""MCP-Benutzer-Lesetools: Rechte, Mandantentrennung und sensible Ausgabefelder."""
import asyncio
import json

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext, MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.models.master import FireDept, Member, OrgSettings, SystemSettings
from app.models.user import Role, User, UserRole
from tests.test_mcp_fundament import _mcp, _oauth_tokens

TOOLS_BENUTZER = ("benutzer_suchen", "benutzer_lesen", "rollen_katalog")
VERBOTENE_FELDER = {"password_hash", "token", "tokens", "failed_login_count", "entra_oid", "entra_tid"}


def _seed(slug: str, rollen: dict[str, str]) -> dict:
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=slug, name=f"Org {slug}")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, mcp_modul_aktiv=True))
    system = db.query(SystemSettings).filter(SystemSettings.key == "mcp_module_enabled").first()
    if system is None:
        db.add(SystemSettings(key="mcp_module_enabled", value="true"))
    else:
        system.value = "true"
    users = {}
    usernames = {}
    for username, rolle in rollen.items():
        actual_username = f"{username}-{slug}"
        user = User(username=actual_username, display_name=username, org_id=org.id, password_hash=hash_password("Test1234!"))
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == rolle).first()
        assert role, rolle
        db.add(UserRole(user_id=user.id, role_id=role.id))
        users[username] = user.id
        usernames[username] = actual_username
    member = Member(org_id=org.id, firstname="Anna", lastname="Admin")
    db.add(member)
    db.flush()
    visible = User(username=f"visible-{slug}", display_name="Sichtbar", full_name="Sichtbarer Benutzer",
                   email=f"sichtbar-{slug}@example.org", phone="0664000000", org_id=org.id, member_id=member.id,
                   password_hash="GEHEIM-PASSWORT", auth_provider="entra", entra_oid="GEHEIM-OID")
    inactive = User(username=f"inactive-{slug}", display_name="Inaktiv", org_id=org.id, active=False,
                    password_hash=hash_password("Test1234!"))
    device = User(username=f"device-{slug}", display_name="Geraet", org_id=org.id, is_device=True,
                  password_hash=hash_password("Test1234!"))
    db.add_all([visible, inactive, device])
    db.flush()
    admin_role = db.query(Role).filter(Role.code == "admin").first()
    assert admin_role
    db.add(UserRole(user_id=visible.id, role_id=admin_role.id))
    db.commit()
    result = {"org_id": org.id, "users": users, "usernames": usernames, "visible": visible.id,
              "inactive": inactive.id, "device": device.id}
    db.close()
    return result


def _ergebnis(response) -> dict:
    assert response.status_code == 200, response.text
    if "text/event-stream" in response.headers.get("content-type", ""):
        lines = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        body = json.loads(lines[-1])["result"]
    else:
        body = response.json()["result"]
    if body.get("isError"):
        return {"__fehler__": json.dumps(body["content"], ensure_ascii=False)}
    structured = body.get("structuredContent")
    return structured.get("result", structured) if structured else json.loads(body["content"][0]["text"])


def _token(client, username: str) -> str:
    return _oauth_tokens(client, {"username": username})["access_token"]


def _rufe(client, token: str, tool: str, **arguments) -> dict:
    return _ergebnis(_mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 10))


def _alle_schluessel(daten) -> set[str]:
    if isinstance(daten, dict):
        return set(daten) | {key for value in daten.values() for key in _alle_schluessel(value)}
    if isinstance(daten, list):
        return {key for value in daten for key in _alle_schluessel(value)}
    return set()


def test_benutzer_tools_sind_registriert_und_adminpflichtig():
    assert {name for name in TOOLS if name in TOOLS_BENUTZER} == set(TOOLS_BENUTZER)
    assert all(TOOLS[name].required_roles == ("admin",) for name in TOOLS_BENUTZER)


@pytest.mark.parametrize(("rolle", "erlaubt"), [("admin", True), ("fahrtenbuch_admin", False), ("recorder", False)])
def test_rollenmatrix_live_kontext(rolle, erlaubt):
    seed = _seed(f"ben-rolle-{rolle}", {rolle: rolle})
    db = SessionLocal()
    try:
        if erlaubt:
            assert load_live_context(db, seed["users"][rolle], seed["org_id"], ("admin",)).org_id == seed["org_id"]
        else:
            with pytest.raises(MCPPermissionError):
                load_live_context(db, seed["users"][rolle], seed["org_id"], ("admin",))
    finally:
        db.close()


def test_suche_filter_und_geraete_benutzer_unsichtbar(client):
    seed = _seed("ben-filter", {"admin": "admin"})
    token = _token(client, seed["usernames"]["admin"])
    listed = _rufe(client, token, "benutzer_suchen", q="Sichtbarer")
    assert [user["id"] for user in listed["benutzer"]] == [seed["visible"]]
    assert _rufe(client, token, "benutzer_suchen", aktiv=False)["benutzer"][0]["id"] == seed["inactive"]
    assert seed["visible"] in {user["id"] for user in _rufe(client, token, "benutzer_suchen", rolle="admin")["benutzer"]}
    assert [user["id"] for user in _rufe(client, token, "benutzer_suchen", mit_mitglied=True)["benutzer"]] == [seed["visible"]]
    all_ids = {user["id"] for user in _rufe(client, token, "benutzer_suchen", limit=200)["benutzer"]}
    assert seed["device"] not in all_ids
    assert "__fehler__" in _rufe(client, token, "benutzer_lesen", benutzer_id=seed["device"])


def test_cross_org_sensible_felder_und_rollenkatalog(client):
    seed_a = _seed("ben-org-a", {"admin-a": "admin"})
    seed_b = _seed("ben-org-b", {"admin-b": "admin"})
    token = _token(client, seed_a["usernames"]["admin-a"])
    search = _rufe(client, token, "benutzer_suchen", limit=200)
    assert seed_b["visible"] not in {user["id"] for user in search["benutzer"]}
    assert "__fehler__" in _rufe(client, token, "benutzer_lesen", benutzer_id=seed_b["visible"])
    detail = _rufe(client, token, "benutzer_lesen", benutzer_id=seed_a["visible"])
    assert detail["auth_provider"] == "entra" and detail["mitglied"]["name"] == "Anna Admin"
    assert detail["mitglied"]["gruppenkommandant_funktionen"] == []
    assert not _alle_schluessel([search, detail]) & VERBOTENE_FELDER
    assert "GEHEIM-PASSWORT" not in json.dumps([search, detail]) and "GEHEIM-OID" not in json.dumps([search, detail])
    katalog = {role["code"]: role for role in _rufe(client, token, "rollen_katalog")["rollen"]}
    assert katalog["admin"]["vergebbar"] is True and katalog["system_admin"]["vergebbar"] is False


def test_lesetools_schreiben_nichts():
    seed = _seed("ben-readonly", {"admin": "admin"})
    db = SessionLocal()
    set_tenant_context(db, seed["org_id"])
    try:
        user = db.get(User, seed["users"]["admin"])
        context = MCPContext(db=db, user=user, org_id=seed["org_id"])
        commits = []
        db.commit = lambda: commits.append(1)  # type: ignore[method-assign]
        for name, args in (("benutzer_suchen", {}), ("benutzer_lesen", {"benutzer_id": seed["visible"]}),
                           ("rollen_katalog", {})):
            asyncio.run(TOOLS[name].handler(context, **args))
            assert not (db.new or db.dirty or db.deleted), name
        assert commits == []
    finally:
        db.close()
