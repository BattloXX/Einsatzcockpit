"""MCP-Benutzer-Lesetools: Rechte, Mandantentrennung und sensible Ausgabefelder."""
import asyncio
import json
from datetime import datetime

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext, MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.models.master import FireDept, Member, OrgSettings, SystemSettings
from app.models.mcp import MCPOAuthToken
from app.models.user import AuditLog, Role, User, UserRole
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


def test_schreibtools_admin_ok_recorder_verweigert_und_audit(client, monkeypatch):
    seed = _seed("ben-write", {"admin": "admin", "recorder": "recorder"})
    admin = _token(client, seed["usernames"]["admin"])
    recorder = _token(client, seed["usernames"]["recorder"])
    for name in ("benutzer_anlegen", "benutzer_aktualisieren", "benutzer_rollen_setzen", "benutzer_aktivieren",
                 "benutzer_deaktivieren", "benutzer_mitglied_verknuepfen", "benutzer_passwort_reset_mail"):
        assert name not in _mcp(client, recorder, "tools/list", {}, 20).text
    assert "__fehler__" in _rufe(client, recorder, "benutzer_anlegen", username="x", display_name="X")
    created = _rufe(client, admin, "benutzer_anlegen", username="mcp-write-ben", display_name="MCP Write",
                    rollen=["readonly"], willkommensmail=False)["benutzer"]
    assert created["rollen"] == ["readonly"]
    assert _rufe(client, admin, "benutzer_aktualisieren", benutzer_id=created["id"],
                 felder={"phone": "0664000000"})["geaendert"]["phone"]["neu"] == "0664000000"
    assert _rufe(client, admin, "benutzer_rollen_setzen", benutzer_id=created["id"], rollen=["recorder"])["rollen"] == ["recorder"]
    assert _rufe(client, admin, "benutzer_deaktivieren", benutzer_id=created["id"])["aktiv"]["neu"] is False
    assert _rufe(client, admin, "benutzer_aktivieren", benutzer_id=created["id"])["aktiv"]["neu"] is True
    db = SessionLocal()
    set_tenant_context(db, seed["org_id"])
    try:
        actions = {row.action for row in db.query(AuditLog).filter(AuditLog.entity_id == created["id"]).all()}
        assert {"mcp.user.created", "mcp.user.updated", "mcp.user.roles_set", "mcp.user.deactivated", "mcp.user.activated"} <= actions
    finally:
        db.close()


def test_schreibtools_schutz_verknuepfung_tokenwiderruf_und_reset_mail(monkeypatch):
    seed = _seed("ben-protect", {"admin": "admin"})
    db = SessionLocal()
    set_tenant_context(db, seed["org_id"])
    try:
        actor = db.get(User, seed["users"]["admin"])
        target = db.get(User, seed["visible"])
        assert actor and target
        context = MCPContext(db=db, user=actor, org_id=seed["org_id"])
        other = FireDept(slug="ben-protect-other", name="Andere Org")
        sysadmin = User(username="mcp-system-target", display_name="System", org_id=seed["org_id"], password_hash="x")
        db.add_all([other, sysadmin])
        db.flush()
        system_role = db.query(Role).filter(Role.code == "system_admin").first()
        assert system_role
        db.add(UserRole(user_id=sysadmin.id, role_id=system_role.id))
        foreign_member = Member(org_id=other.id, firstname="Fremd", lastname="Mitglied")
        db.add(foreign_member)
        db.add(MCPOAuthToken(token_hash="a" * 64, token_type="access", client_id="test", user_id=target.id,
                             org_id=seed["org_id"], scopes="mcp", expires_at=datetime.now(), family_id="f" * 64))
        db.commit()
        with pytest.raises(ValueError, match="Keine Berechtigung"):
            asyncio.run(TOOLS["benutzer_aktualisieren"].handler(context, sysadmin.id, {"phone": "1"}))
        with pytest.raises(ValueError, match="Nicht vergebbare Rollen"):
            asyncio.run(TOOLS["benutzer_rollen_setzen"].handler(context, target.id, ["system_admin"]))
        with pytest.raises(ValueError, match="selbst"):
            asyncio.run(TOOLS["benutzer_deaktivieren"].handler(context, actor.id))
        with pytest.raises(ValueError, match="nicht gefunden"):
            asyncio.run(TOOLS["benutzer_mitglied_verknuepfen"].handler(context, target.id, foreign_member.id))
        asyncio.run(TOOLS["benutzer_deaktivieren"].handler(context, target.id))
        assert db.query(MCPOAuthToken).filter(MCPOAuthToken.user_id == target.id).one().revoked_at is not None
        sent = []

        async def fake_reset(user, session):
            sent.append(user.id)

        monkeypatch.setattr("app.mcp.tools.benutzer.benutzer_service.send_password_reset_mail", fake_reset)
        target.active = True
        target.auth_provider = "local"
        asyncio.run(TOOLS["benutzer_passwort_reset_mail"].handler(context, target.id))
        assert sent == [target.id]
    finally:
        db.close()


def _gk_lage(org_id: int, member_id: int, *, fremd_org_id: int | None = None) -> dict:
    from datetime import UTC
    from datetime import datetime as dt

    from app.models.major_incident import (
        EinheitSiteDispatch,
        IncidentSite,
        LageEinheit,
        LageEinheitLeader,
        MajorIncident,
        MajorIncidentStatus,
        SitePhase,
    )
    from app.services.resource_service import STATUS_IM_EINSATZ

    db = SessionLocal()
    set_tenant_context(db, None)
    lage = MajorIncident(org_id=fremd_org_id or org_id, name="Hochwasser", status=MajorIncidentStatus.active,
                         is_exercise=True)
    db.add(lage)
    db.flush()
    einheit = LageEinheit(lage_id=lage.id, label="RLF Gruppe", status=STATUS_IM_EINSATZ)
    db.add(einheit)
    db.flush()
    leader = LageEinheitLeader(einheit_id=einheit.id, member_id=member_id, person_name="Anna Admin",
                               start_at=dt.now(UTC))
    db.add(leader)
    db.flush()
    einheit.leader_assignment_id = leader.id
    site = IncidentSite(major_incident_id=lage.id, org_id=lage.org_id, bezeichnung="Keller auspumpen",
                        phase=SitePhase.disponiert)
    db.add(site)
    db.flush()
    db.add(EinheitSiteDispatch(einheit_id=einheit.id, site_id=site.id, dispatched_at=dt.now(UTC)))
    db.commit()
    result = {"lage": lage.id, "einheit": einheit.id}
    db.close()
    return result


def test_whoami_und_meine_einheiten_fuer_verknuepften_gk(client):
    seed = _seed("ben-meine-einheit", {"leser": "readonly"})
    db = SessionLocal()
    set_tenant_context(db, None)
    member = Member(org_id=seed["org_id"], firstname="Gerda", lastname="Kommandant")
    db.add(member)
    db.flush()
    db.get(User, seed["users"]["leser"]).member_id = member.id
    db.commit()
    member_id = member.id
    db.close()
    gk = _gk_lage(seed["org_id"], member_id)
    token = _token(client, seed["usernames"]["leser"])

    ich = _rufe(client, token, "mcp_whoami")
    assert ich["mitglied"] == {"id": member_id, "name": "Gerda Kommandant"}
    assert ich["fuehrt_einheiten"] == [{
        "lage_id": gk["lage"], "lage_name": "Hochwasser", "einheit_id": gk["einheit"],
        "einheit_label": "RLF Gruppe", "is_exercise": True,
    }]

    meine = _rufe(client, token, "meine_einheiten")
    assert meine["mitglied_verknuepft"] is True
    [eintrag] = meine["einheiten"]
    assert eintrag["einheit_id"] == gk["einheit"]
    assert eintrag["einheit_url"].endswith(f"/einheit?einheit={gk['einheit']}")
    assert [a["bezeichnung"] for a in eintrag["offene_auftraege"]] == ["Keller auspumpen"]


def test_meine_einheiten_leer_ohne_verknuepfung_und_ohne_fremde_org(client):
    seed = _seed("ben-meine-leer", {"leser": "readonly"})
    fremd = _seed("ben-meine-fremd", {"anderer": "readonly"})
    token = _token(client, seed["usernames"]["leser"])
    leer = _rufe(client, token, "meine_einheiten")
    assert leer == {"einheiten": [], "mitglied_verknuepft": False}
    assert _rufe(client, token, "mcp_whoami")["mitglied"] is None

    # Ein Mitglied der eigenen Org, das in einer fremden Lage als GK eingetragen ist, gibt keinen Zugriff.
    db = SessionLocal()
    set_tenant_context(db, None)
    member = Member(org_id=seed["org_id"], firstname="Otto", lastname="Fremd")
    db.add(member)
    db.flush()
    db.get(User, seed["users"]["leser"]).member_id = member.id
    db.commit()
    member_id = member.id
    db.close()
    _gk_lage(seed["org_id"], member_id, fremd_org_id=fremd["org_id"])
    assert _rufe(client, token, "meine_einheiten")["einheiten"] == []
    assert _rufe(client, token, "mcp_whoami")["fuehrt_einheiten"] == []
