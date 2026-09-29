"""HTTP-Regressionen fuer MCP-Objekte und Kontakte."""

import json

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.models.kontakt import Kontakt, KontaktTelefon
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import (
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektChange,
    ObjektGefahr,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektZusatzadresse,
)
from app.models.user import AuditLog, Role, User, UserRole
from tests.test_mcp_fundament import _mcp, _oauth_tokens

OBJEKT_TOOLS = {
    "objekt_kataloge", "objekt_suchen", "objekt_lesen", "objekt_duplikate_pruefen", "objekt_anlegen",
    "objekt_aktualisieren",
}
KONTAKT_TOOLS = {"kontakt_suchen", "kontakt_duplikate_pruefen"}


def _seed(slug: str, rollen: dict[str, str], *, objekt: bool = True, kontakte: bool = True) -> dict:
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=slug, name=f"Org {slug}")
    db.add(org)
    db.flush()
    db.add(
        OrgSettings(org_id=org.id, mcp_modul_aktiv=True, objekt_module_enabled=objekt, kontakte_module_enabled=kontakte)
    )
    for key in ("mcp_module_enabled", "objekt_module_enabled", "kontakte_module_enabled"):
        row = db.query(SystemSettings).filter(SystemSettings.key == key).first()
        if row is None:
            db.add(SystemSettings(key=key, value="true"))
        else:
            row.value = "true"
    users = {}
    for username, rolle in rollen.items():
        login = f"{slug}-{username}"
        user = User(username=login, display_name=login, org_id=org.id, password_hash=hash_password("Test1234!"))
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == rolle).first()
        assert role
        db.add(UserRole(user_id=user.id, role_id=role.id))
        users[username] = user.id
        users[f"{username}_login"] = login
    bestaetigt = Objekt(
        org_id=org.id,
        nummer=1,
        name="Musterbetrieb",
        strasse="Hauptstrasse",
        hausnummer="1",
        plz="1010",
        ort="Wien",
        status="freigegeben",
    )
    db.add(bestaetigt)
    db.flush()
    db.add(ObjektBMA(org_id=org.id, objekt_id=bestaetigt.id, bma_nummer="BMA-1", rfl_nummer="RFL-1"))
    kontakt = Kontakt(
        org_id=org.id, typ="person", anzeigename="Max Muster", organisation="Muster GmbH", email="max@example.test"
    )
    db.add(kontakt)
    db.flush()
    db.add(KontaktTelefon(org_id=org.id, kontakt_id=kontakt.id, nummer="0664 1234567"))
    db.commit()
    result = {"org_id": org.id, "users": users, "objekt_id": bestaetigt.id, "kontakt_id": kontakt.id}
    db.close()
    return result


def _rpc(response) -> dict:
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads([z[5:].strip() for z in response.text.splitlines() if z.startswith("data:")][-1])
    return response.json()


def _rufe(client, token: str, tool: str, **arguments) -> dict:
    response = _mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 10)
    assert response.status_code == 200, response.text
    body = _rpc(response)["result"]
    if body.get("isError"):
        return {"__fehler__": json.dumps(body["content"], ensure_ascii=False)}
    return body.get("structuredContent", {}).get("result", json.loads(body["content"][0]["text"]))


def _token(client, seed: dict, username: str) -> str:
    return _oauth_tokens(client, {"username": seed["users"][f"{username}_login"]})["access_token"]


@pytest.mark.parametrize(
    "rolle,erlaubt",
    [
        ("objekt_verwalter", True),
        ("org_admin", True),
        ("admin", True),
        ("fahrtenbuch_admin", False),
        ("recorder", False),
        ("readonly", False),
        ("kontakt_verwalter", False),
    ],
)
def test_rollenmatrix_live_und_tools_list(client, rolle, erlaubt):
    seed = _seed(f"obj-rolle-{rolle}", {rolle: rolle})
    db = SessionLocal()
    try:
        if erlaubt:
            assert load_live_context(db, seed["users"][rolle], seed["org_id"], ("objekt_verwalter",))
        else:
            with pytest.raises(MCPPermissionError):
                load_live_context(db, seed["users"][rolle], seed["org_id"], ("objekt_verwalter",))
    finally:
        db.close()
    token = _token(client, seed, rolle)
    listed = _mcp(client, token, "tools/list", {}, 1).text
    if erlaubt:
        assert OBJEKT_TOOLS <= set(TOOLS) and all(name in listed for name in OBJEKT_TOOLS)
    else:
        assert not any(name in listed for name in OBJEKT_TOOLS)
        assert "__fehler__" in _rufe(client, token, "objekt_suchen")


def test_module_aus_cross_org_und_privacy(client):
    disabled = _seed("obj-aus", {"admin": "objekt_verwalter"}, objekt=False)
    token = _token(client, disabled, "admin")
    assert "objekt_" not in _mcp(client, token, "tools/list", {}, 1).text
    kontakte_aus = _seed("kontakte-aus", {"admin": "objekt_verwalter"}, kontakte=False)
    kontakte_token = _token(client, kontakte_aus, "admin")
    listed = _mcp(client, kontakte_token, "tools/list", {}, 1).text
    assert not any(name in listed for name in KONTAKT_TOOLS)
    seed_a = _seed("obj-a", {"a": "objekt_verwalter"})
    seed_b = _seed("obj-b", {"b": "objekt_verwalter"})
    a = _token(client, seed_a, "a")
    assert "__fehler__" in _rufe(client, a, "objekt_lesen", objekt_id=seed_b["objekt_id"])
    gelesen = _rufe(client, a, "objekt_lesen", objekt_id=seed_a["objekt_id"])
    text = json.dumps(gelesen)
    assert "0664" not in text and "max@example.test" not in text


def test_anlegen_entwurf_audit_und_duplikate(client):
    seed = _seed("obj-create", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    doppelt = _rufe(
        client,
        token,
        "objekt_anlegen",
        stammdaten={"name": "Musterbetrieb", "strasse": "Hauptstrasse", "hausnummer": "1", "ort": "Wien"},
    )
    assert "__fehler__" in doppelt
    result = _rufe(
        client,
        token,
        "objekt_anlegen",
        stammdaten={"name": "Neu", "strasse": "Nebenstrasse", "ort": "Wien"},
        duplikat_bestaetigt=True,
    )
    assert result["status"] == "entwurf" and result["ui_link"].endswith(f"/objekte/{result['objekt_id']}")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(Objekt, result["objekt_id"]).status == "entwurf"
        assert db.query(ObjektChange).filter_by(objekt_id=result["objekt_id"], quelle="mcp").count()
        assert db.query(AuditLog).filter_by(action="objekt.mcp_angelegt", entity_id=result["objekt_id"]).count() == 1
    finally:
        db.close()


def test_anlegen_atomisch_neuer_kontakt_ohne_freigabe_und_kontakt_dublette(client):
    seed = _seed("obj-atom", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    kaputt = _rufe(
        client,
        token,
        "objekt_anlegen",
        stammdaten={"name": "Atom", "ort": "Wien"},
        kontakte=[
            {"art": "betreiber", "neu": {"anzeigename": "Neu Kontakt", "typ": "person"}},
            {"neu": {"anzeigename": "Fehler"}},
        ],
    )
    assert "__fehler__" in kaputt
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(Objekt).filter_by(name="Atom").first() is None
        assert db.query(Kontakt).filter_by(anzeigename="Neu Kontakt").first() is None
    finally:
        db.close()
    duplicate = _rufe(
        client,
        token,
        "objekt_anlegen",
        stammdaten={"name": "Kontaktobjekt", "ort": "Wien"},
        kontakte=[
            {"art": "betreiber", "neu": {"anzeigename": "Max Muster", "typ": "person", "email": "max@example.test"}}
        ],
    )
    assert "__fehler__" in duplicate
    success = _rufe(
        client,
        token,
        "objekt_anlegen",
        stammdaten={"name": "Mit Kontakt", "ort": "Wien"},
        kontakte=[
            {
                "art": "betreiber",
                "neu": {"anzeigename": "Neue Person", "typ": "person", "telefone": [{"nummer": "0664 9999999"}]},
            }
        ],
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kontakt = db.query(Kontakt).filter_by(anzeigename="Neue Person").one()
        assert kontakt.id and not hasattr(kontakt, "freigaben")
        assert db.get(Objekt, success["objekt_id"]).kontakte[0].freigaben == []
    finally:
        db.close()


def test_validierung_und_duplikat_tools(client):
    seed = _seed("obj-valid", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    assert "__fehler__" in _rufe(client, token, "objekt_suchen", limit=51)
    assert "__fehler__" in _rufe(client, token, "objekt_anlegen", stammdaten={"name": " "})
    objekte = _rufe(
        client,
        token,
        "objekt_duplikate_pruefen",
        name="Musterbetrieb",
        strasse="Hauptstrasse",
        hausnummer="1",
        plz="1010",
        ort="Wien",
    )
    assert objekte["duplikate_gefunden"] and objekte["kandidaten"][0]["id"] == seed["objekt_id"]
    kontakte = _rufe(client, token, "kontakt_duplikate_pruefen", anzeigename="Max Muster", email="max@example.test")
    assert kontakte["duplikate_gefunden"] and kontakte["kandidaten"][0]["id"] == seed["kontakt_id"]


def test_aktualisieren_arbeitskopie_stammdaten_bma_audit_und_rollback(client):
    seed = _seed("obj-update", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        basis = db.get(Objekt, seed["objekt_id"])
        vorher = {feld: getattr(basis, feld) for feld in ("name", "strasse", "ort")}
    finally:
        db.close()
    result = _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"],
        stammdaten={"name": "MCP Musterbetrieb", "ort": "Graz"}, bma={"bma_nummer": "BMA-2"},
    )
    assert result["basis_objekt_id"] == seed["objekt_id"]
    assert result["objekt_id"] != seed["objekt_id"]
    assert "Arbeitskopie" in result["hinweis"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        basis = db.get(Objekt, seed["objekt_id"])
        kopie = db.get(Objekt, result["objekt_id"])
        assert {feld: getattr(basis, feld) for feld in vorher} == vorher
        assert kopie.name == "MCP Musterbetrieb" and kopie.ort == "Graz" and kopie.bma.bma_nummer == "BMA-2"
        assert db.query(ObjektChange).filter_by(objekt_id=kopie.id, quelle="mcp").count()
        assert db.query(AuditLog).filter_by(action="objekt.mcp_aktualisiert", entity_id=kopie.id).count() == 1
    finally:
        db.close()
    erneut = _rufe(client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"], stammdaten={"vulgoname": "Neu"})
    assert erneut["objekt_id"] == result["objekt_id"]
    kaputt_seed = _seed("obj-rollback-update", {"admin": "objekt_verwalter"})
    kaputt = _rufe(
        client, _token(client, kaputt_seed, "admin"), "objekt_aktualisieren", objekt_id=kaputt_seed["objekt_id"],
        gefahren_hinzufuegen=[{"gefahr_id": 999999}],
    )
    assert "__fehler__" in kaputt
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(Objekt).filter_by(entwurf_von_id=kaputt_seed["objekt_id"]).count() == 0
    finally:
        db.close()


def test_aktualisieren_entwurf_archiv_cross_org_kinder_und_kontakte(client):
    seed = _seed("obj-update-kinder", {"admin": "objekt_verwalter"})
    other = _seed("obj-update-fremd", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        entwurf = Objekt(org_id=seed["org_id"], nummer=2, name="Entwurf", status="entwurf")
        archiv = Objekt(org_id=seed["org_id"], nummer=3, name="Archiv", status="archiviert")
        gefahr = GefahrenKatalog(org_id=seed["org_id"], name="Gas")
        merkmal = MerkmalKatalog(org_id=seed["org_id"], name="FSD")
        db.add_all([entwurf, archiv, gefahr, merkmal])
        db.commit()
        entwurf_id, archiv_id, gefahr_id, merkmal_id = entwurf.id, archiv.id, gefahr.id, merkmal.id
    finally:
        db.close()
    direkt = _rufe(client, token, "objekt_aktualisieren", objekt_id=entwurf_id, stammdaten={"name": "Direkt"})
    assert direkt["objekt_id"] == entwurf_id
    assert "__fehler__" in _rufe(
        client, token, "objekt_aktualisieren", objekt_id=archiv_id, stammdaten={"name": "Nein"}
    )
    fremd = _rufe(client, token, "objekt_aktualisieren", objekt_id=other["objekt_id"], stammdaten={"name": "Nein"})
    assert "__fehler__" in fremd
    hinzu = _rufe(
        client, token, "objekt_aktualisieren", objekt_id=entwurf_id,
        gefahren_hinzufuegen=[{"gefahr_id": gefahr_id}], merkmale_hinzufuegen=[{"merkmal_id": merkmal_id}],
        zusatzadressen_hinzufuegen=[{"bezeichnung": "Tor 2", "ort": "Wien"}],
        kontakte_hinzufuegen=[{"art": "betreiber", "kontakt_id": seed["kontakt_id"]}],
    )
    assert hinzu["objekt_id"] == entwurf_id
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        obj = db.get(Objekt, entwurf_id)
        ids = (obj.gefahren[0].id, obj.merkmale[0].id, obj.zusatzadressen[0].id, obj.kontakte[0].id)
        kontakt = db.get(Kontakt, seed["kontakt_id"])
        assert kontakt.anzeigename == "Max Muster" and obj.kontakte[0].freigaben == []
    finally:
        db.close()
    _rufe(
        client, token, "objekt_aktualisieren", objekt_id=entwurf_id,
        gefahren_entfernen=[ids[0]],
        merkmale_entfernen=[ids[1]],
        zusatzadressen_entfernen=[ids[2]],
        kontakte_entfernen=[ids[3]],
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(ObjektGefahr).filter_by(id=ids[0]).first() is None
        assert db.query(ObjektMerkmal).filter_by(id=ids[1]).first() is None
        assert db.query(ObjektZusatzadresse).filter_by(id=ids[2]).first() is None
        assert db.query(ObjektKontakt).filter_by(id=ids[3]).first() is None
        assert db.get(Kontakt, seed["kontakt_id"]) is not None
        assert db.get(Objekt, entwurf_id).status == "entwurf"
    finally:
        db.close()
