"""MCP-Mitglieder-Lesetools: Rechte, Mandantentrennung und reine Lesezugriffe."""
import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext, MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.models.major_incident import LageEinheit, LageEinheitLeader, MajorIncident, MajorIncidentStatus
from app.models.master import (
    FireDept,
    Member,
    MemberQualification,
    MemberTag,
    MemberTagAssignment,
    OrgSettings,
    Qualification,
    SystemSettings,
)
from app.models.user import Role, User, UserRole
from tests.test_mcp_fundament import _mcp, _oauth_tokens

MITGLIEDER_TOOLS = ("mitglieder_suchen", "mitglied_lesen", "mitglieder_kataloge")


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
    for username, rolle in rollen.items():
        user = User(username=username, display_name=username, org_id=org.id, password_hash=hash_password("Test1234!"))
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == rolle).first()
        assert role, rolle
        db.add(UserRole(user_id=user.id, role_id=role.id))
        users[username] = user.id
    q_gk = Qualification(code=f"GK-{slug}", label="Gruppenkommandant", is_gruppenkommandant=True)
    q_agt = Qualification(code=f"AGT-{slug}", label="Atemschutz")
    tag = MemberTag(org_id=org.id, name="Ausbilder", color="#112233")
    db.add_all([q_gk, q_agt, tag])
    db.flush()
    anna = Member(org_id=org.id, firstname="Anna", lastname="Admin", phone="0664123456", email="anna@example.org",
                  sybos_id=f"SY-{slug}-1", ist_gruppenkommandant=True)
    bert = Member(org_id=org.id, firstname="Bert", lastname="Bereit", active=False, phone="0664987654")
    clara = Member(org_id=org.id, firstname="Clara", lastname="Comet")
    db.add_all([anna, bert, clara])
    db.flush()
    db.add_all([
        MemberQualification(member_id=anna.id, qualification_id=q_gk.id),
        MemberQualification(member_id=anna.id, qualification_id=q_agt.id),
        MemberQualification(member_id=bert.id, qualification_id=q_agt.id),
        MemberTagAssignment(member_id=anna.id, tag_id=tag.id),
    ])
    linked = User(username=f"linked-{slug}", display_name="Anna A.", org_id=org.id, member_id=anna.id,
                  password_hash=hash_password("Test1234!"))
    db.add(linked)
    db.flush()
    lage = MajorIncident(org_id=org.id, name="Hochwasser", status=MajorIncidentStatus.active)
    db.add(lage)
    db.flush()
    einheit = LageEinheit(lage_id=lage.id, label="RLF 1")
    db.add(einheit)
    db.flush()
    leader = LageEinheitLeader(einheit_id=einheit.id, member_id=anna.id, person_name="Anna Admin",
                               start_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(leader)
    db.flush()
    einheit.leader_assignment_id = leader.id
    db.commit()
    result = {
        "org_id": org.id,
        "users": users,
        "members": {"anna": anna.id, "bert": bert.id, "clara": clara.id},
        "codes": {"gk": q_gk.code, "agt": q_agt.code},
        "tag": tag.name,
    }
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


def test_mitglieder_tools_sind_registriert_und_nur_lesend():
    assert {name for name in TOOLS if name.startswith("mitglied")} == set(MITGLIEDER_TOOLS)
    for name in MITGLIEDER_TOOLS:
        assert TOOLS[name].required_roles == ("admin", "fahrtenbuch_admin")
        assert TOOLS[name].module_check is None


@pytest.mark.parametrize(
    ("rolle", "erlaubt"),
    [("admin", True), ("fahrtenbuch_admin", True), ("recorder", False), ("readonly", False)],
)
def test_rollenmatrix_live_kontext(rolle, erlaubt):
    seed = _seed(f"mit-rolle-{rolle}", {f"u-{rolle}": rolle})
    db = SessionLocal()
    try:
        if erlaubt:
            assert load_live_context(db, seed["users"][f"u-{rolle}"], seed["org_id"],
                                     ("admin", "fahrtenbuch_admin")).org_id == seed["org_id"]
        else:
            with pytest.raises(MCPPermissionError):
                load_live_context(db, seed["users"][f"u-{rolle}"], seed["org_id"],
                                  ("admin", "fahrtenbuch_admin"))
    finally:
        db.close()


def test_admin_und_fahrtenbuch_admin_koennen_lesen_recorder_nicht(client):
    seed = _seed("mit-http", {"admin": "admin", "fb": "fahrtenbuch_admin", "rec": "recorder"})
    for username in ("admin", "fb"):
        token = _token(client, username)
        assert [m["id"] for m in _rufe(client, token, "mitglieder_suchen")["mitglieder"]] == [seed["members"]["anna"], seed["members"]["clara"]]
        assert _rufe(client, token, "mitglied_lesen", mitglied_id=seed["members"]["anna"])["telefon"] == "0664123456"
    token = _token(client, "rec")
    assert "mitglieder_" not in _mcp(client, token, "tools/list", {}, 2).text
    assert "__fehler__" in _rufe(client, token, "mitglieder_suchen")


def test_filter_paginierung_kataloge_und_gk_funktion(client):
    seed = _seed("mit-filter", {"admin-filter": "admin"})
    token = _token(client, "admin-filter")
    anna = seed["members"]["anna"]
    assert [m["id"] for m in _rufe(client, token, "mitglieder_suchen", q="SY-mit-filter-1")["mitglieder"]] == [anna]
    assert [m["id"] for m in _rufe(client, token, "mitglieder_suchen", aktiv=False)["mitglieder"]] == [seed["members"]["bert"]]
    assert [m["id"] for m in _rufe(client, token, "mitglieder_suchen", qualifikation=seed["codes"]["gk"])["mitglieder"]] == [anna]
    assert [m["id"] for m in _rufe(client, token, "mitglieder_suchen", tag=seed["tag"])["mitglieder"]] == [anna]
    assert [m["id"] for m in _rufe(client, token, "mitglieder_suchen", mit_benutzer=True)["mitglieder"]] == [anna]
    page = _rufe(client, token, "mitglieder_suchen", aktiv=None, limit=2)
    assert page["gesamt"] == 3 and len(page["mitglieder"]) == 2 and page["truncated"] is True
    assert "__fehler__" in _rufe(client, token, "mitglieder_suchen", limit=201)
    detail = _rufe(client, token, "mitglied_lesen", mitglied_id=anna)
    assert detail["benutzer"] == {"id": detail["benutzer"]["id"], "username": "linked-mit-filter", "display_name": "Anna A.", "aktiv": True}
    assert detail["gruppenkommandant_funktionen"] == [{"lage_id": detail["gruppenkommandant_funktionen"][0]["lage_id"], "lage_name": "Hochwasser", "einheit_id": detail["gruppenkommandant_funktionen"][0]["einheit_id"], "einheit_label": "RLF 1"}]
    kataloge = _rufe(client, token, "mitglieder_kataloge")
    assert kataloge["anzahl_aktive_mitglieder"] == 2 and kataloge["anzahl_inaktive_mitglieder"] == 1
    assert kataloge["tags"] == [{"id": kataloge["tags"][0]["id"], "name": "Ausbilder", "color": "#112233"}]


def test_cross_org_isolation(client):
    seed_a = _seed("mit-org-a", {"a-admin": "admin"})
    seed_b = _seed("mit-org-b", {"b-admin": "admin"})
    token = _token(client, "a-admin")
    ids = {m["id"] for m in _rufe(client, token, "mitglieder_suchen", aktiv=None)["mitglieder"]}
    assert ids == set(seed_a["members"].values())
    assert "__fehler__" in _rufe(client, token, "mitglied_lesen", mitglied_id=seed_b["members"]["anna"])


def test_lesetools_schreiben_nichts():
    seed = _seed("mit-readonly", {"mit-ro": "admin"})
    db = SessionLocal()
    set_tenant_context(db, seed["org_id"])
    try:
        user = db.get(User, seed["users"]["mit-ro"])
        context = MCPContext(db=db, user=user, org_id=seed["org_id"])
        commits = []
        db.commit = lambda: commits.append(1)  # type: ignore[method-assign]
        for name, args in (
            ("mitglieder_suchen", {"aktiv": None}),
            ("mitglied_lesen", {"mitglied_id": seed["members"]["anna"]}),
            ("mitglieder_kataloge", {}),
        ):
            asyncio.run(TOOLS[name].handler(context, **args))
            assert not (db.new or db.dirty or db.deleted), name
        assert commits == []
    finally:
        db.close()
