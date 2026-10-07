"""HTTP-Regressionen fuer MCP-Objekte und Kontakte."""

import json
from datetime import date

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
    ObjektKategorie,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektWohnanlage,
    ObjektZusatzadresse,
)
from app.models.user import AuditLog, Role, User, UserRole
from tests.test_mcp_fundament import _mcp, _oauth_tokens

OBJEKT_TOOLS = {
    "objekt_kataloge", "objekt_suchen", "objekt_lesen", "objekt_duplikate_pruefen", "objekt_anlegen",
    "objekt_aktualisieren", "objekt_dokument_upload_vorbereiten",
}
KONTAKT_TOOLS = {
    "kontakt_suchen", "kontakt_duplikate_pruefen", "kontakt_lesen", "kontakt_kategorien", "kontakt_anlegen",
    "kontakt_aktualisieren", "kontakt_archivieren", "kontakt_zusammenfuehren",
}


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


def test_kontakt_rollenmatrix_und_tools_list(client):
    for rolle, erlaubt in (("kontakt_verwalter", True), ("objekt_verwalter", True), ("readonly", False)):
        seed = _seed(f"kontakt-rolle-{rolle}", {rolle: rolle})
        token = _token(client, seed, rolle)
        listed = _mcp(client, token, "tools/list", {}, 1).text
        assert all(name in listed for name in KONTAKT_TOOLS) is erlaubt
        if rolle == "kontakt_verwalter":
            assert not any(name in listed for name in OBJEKT_TOOLS)


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


def test_kontaktzuordnungen_lesen_aendern_arbeitskopie_und_wohnanlage(client):
    seed = _seed("obj-kontakt-pflege", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        basis = db.get(Objekt, seed["objekt_id"])
        zuordnung = ObjektKontakt(
            org_id=seed["org_id"], objekt_id=basis.id, kontakt_id=seed["kontakt_id"], art="betreiber", sort=4,
            erreichbarkeit="Portier",
        )
        db.add(zuordnung)
        db.flush()
        db.add(ObjektWohnanlage(org_id=seed["org_id"], objekt_id=basis.id, hausverwaltung_kontakt_id=zuordnung.id))
        db.commit()
        zuordnung_id = zuordnung.id
    finally:
        db.close()
    gelesen = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"])
    kontakt = gelesen["kontakte"][0]
    assert kontakt == {
        "zuordnung_id": zuordnung_id,
        "kontakt_id": seed["kontakt_id"],
        "anzeigename": "Max Muster",
        "funktion": None,
        "art": "betreiber",
        "sort": 4,
        "erreichbarkeit": "Portier",
        "freigaben_anzahl": 0,
    }
    assert "Arbeitskopie" not in str(gelesen["arbeitskopie_hinweis"])
    result = _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"], stammdaten={"vulgoname": "Kopie"}
    )
    kopie_id = result["objekt_id"]
    assert kopie_id != seed["objekt_id"]
    basis = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"])
    assert basis["arbeitskopie_id"] == kopie_id and "Arbeitskopie" in basis["arbeitskopie_hinweis"]
    kopie = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"], arbeitskopie=True)
    kopie_zuordnung_id = kopie["kontakte"][0]["zuordnung_id"]
    assert kopie["id"] == kopie_id
    geaendert = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=seed["objekt_id"],
        kontakte_aendern=[
            {"zuordnung_id": kopie_zuordnung_id, "art": "hausverwaltung", "sort": 9, "erreichbarkeit": "24h"}
        ],
    )
    assert geaendert["objekt_id"] == kopie_id
    kopie = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"], arbeitskopie=True)
    assert kopie["kontakte"][0]["art"] == "hausverwaltung"
    assert "__fehler__" in _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"],
        kontakte_aendern=[{"zuordnung_id": kopie_zuordnung_id, "art": "ungueltig"}],
    )
    assert "__fehler__" in _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"],
        kontakte_entfernen=[kopie_zuordnung_id],
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        kopie = db.get(Objekt, kopie_id)
        assert kopie.kontakte[0].freigaben == []
        assert kopie.wohnanlage.hausverwaltung_kontakt_id == kopie_zuordnung_id
    finally:
        db.close()


def test_neue_stammdaten_wohnanlage_und_kontakt_ergebnis(client):
    seed = _seed("obj-neue-felder", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    erstellt = _rufe(
        client,
        token,
        "objekt_anlegen",
        stammdaten={
            "name": "Neue Felder",
            "ort": "Wien",
            "informationen": "Sprinkler im Keller",
            "anfahrtsweg": "Tor Nord",
            "revision_datum": "2026-10-01",
        },
        kontakte=[{"art": "betreiber", "kontakt_id": seed["kontakt_id"], "erreichbarkeit": "Leitwarte"}],
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, erstellt["objekt_id"])
        assert objekt.informationen == "Sprinkler im Keller"
        assert objekt.anfahrtsweg == "Tor Nord"
        assert objekt.revision_datum.isoformat() == "2026-10-01"
        zuordnung_id = objekt.kontakte[0].id
    finally:
        db.close()
    direkt = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=erstellt["objekt_id"],
        kontakte_aendern=[{"zuordnung_id": zuordnung_id, "sort": 3, "erreichbarkeit": "Tagsueber"}],
    )
    assert direkt["objekt_id"] == erstellt["objekt_id"]
    aktualisiert = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=erstellt["objekt_id"],
        stammdaten={"informationen": "Aktualisiert", "revision_datum": "2026-11-01"},
        wohnanlage={"wohneinheiten": 12, "hausverwaltung_kontakt_id": zuordnung_id},
        kontakte_hinzufuegen=[{"art": "hausverwaltung", "kontakt_id": seed["kontakt_id"]}],
    )
    assert aktualisiert["kontakte_hinzugefuegt"][0]["zuordnung_id"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, aktualisiert["objekt_id"])
        assert objekt.informationen == "Aktualisiert" and objekt.revision_datum.isoformat() == "2026-11-01"
        assert objekt.wohnanlage.wohneinheiten == 12
        assert objekt.wohnanlage.hausverwaltung_kontakt_id == zuordnung_id
        assert all(k.freigaben == [] for k in objekt.kontakte)
    finally:
        db.close()


def test_flache_kontakte_und_entfernen_per_kontakt_id_an_arbeitskopie(client):
    seed = _seed("obj-flach", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    aktualisiert = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=seed["objekt_id"],
        kontakte_hinzufuegen=[
            {"art": "betreiber", "vorname": "Flach", "nachname": "Kontakt", "mobil": "+43 660 1234567"}
        ],
    )
    assert "__fehler__" not in aktualisiert
    hinzugefuegt = aktualisiert["kontakte_hinzugefuegt"][0]
    lesen = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"], arbeitskopie=True)
    assert any(k["kontakt_id"] == hinzugefuegt["kontakt_id"] for k in lesen["kontakte"])
    entfernt = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=seed["objekt_id"],
        kontakte_entfernen=[{"kontakt_id": hinzugefuegt["kontakt_id"]}],
    )
    assert "__fehler__" not in entfernt
    unbekannt = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=seed["objekt_id"],
        kontakte_hinzufuegen=[{"art": "betreiber", "vorname": "X", "quatsch": 1}],
    )
    assert "__fehler__" in unbekannt and "Erlaubt" in unbekannt["__fehler__"]


def test_arbeitskopie_id_und_basis_kontaktzuordnung_werden_aufgeloest(client):
    seed = _seed("obj-arbeitskopie-id-kontakt", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zweiter_kontakt = Kontakt(
            org_id=seed["org_id"],
            typ="person",
            anzeigename="Arbeitskopie Zweiter Kontakt",
            organisation="Arbeitskopie Testorganisation",
        )
        db.add(zweiter_kontakt)
        db.flush()
        erste_zuordnung = ObjektKontakt(
            org_id=seed["org_id"],
            objekt_id=seed["objekt_id"],
            kontakt_id=seed["kontakt_id"],
            art="betreiber",
        )
        zweite_zuordnung = ObjektKontakt(
            org_id=seed["org_id"],
            objekt_id=seed["objekt_id"],
            kontakt_id=zweiter_kontakt.id,
            art="hausverwaltung",
        )
        db.add_all([erste_zuordnung, zweite_zuordnung])
        db.commit()
        erste_zuordnung_id = erste_zuordnung.id
        zweiter_kontakt_id = zweiter_kontakt.id
        zweite_zuordnung_id = zweite_zuordnung.id
    finally:
        db.close()

    arbeitskopie = _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"], stammdaten={"vulgoname": "Kopie"}
    )
    arbeitskopie_id = arbeitskopie["objekt_id"]
    entfernt_per_kontakt = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=arbeitskopie_id,
        kontakte_entfernen=[{"kontakt_id": zweiter_kontakt_id}],
    )
    assert entfernt_per_kontakt["objekt_id"] == arbeitskopie_id
    assert entfernt_per_kontakt["basis_objekt_id"] == seed["objekt_id"]
    entfernt_per_basis_zuordnung = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=arbeitskopie_id,
        kontakte_entfernen=[erste_zuordnung_id],
    )
    assert "__fehler__" not in entfernt_per_basis_zuordnung
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(ObjektKontakt).filter_by(objekt_id=arbeitskopie_id).count() == 0
        assert db.query(ObjektKontakt).filter_by(objekt_id=seed["objekt_id"]).count() == 2
        assert db.get(ObjektKontakt, zweite_zuordnung_id) is not None
    finally:
        db.close()


def test_kontakt_dublette_benennt_neuen_und_vorhandenen_kontakt(client):
    seed = _seed("obj-kontakt-dublette-hinweis", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    result = _rufe(
        client,
        token,
        "objekt_aktualisieren",
        objekt_id=seed["objekt_id"],
        kontakte_hinzufuegen=[
            {"art": "betreiber", "vorname": "Max", "nachname": "Muster", "email": "max@example.test"}
        ],
    )
    assert "__fehler__" in result
    fehlertext = json.loads(result["__fehler__"])[0]["text"]
    assert 'Neuer Kontakt "Max Muster" (kontakte_hinzufuegen[1])' in fehlertext
    assert f"{seed['kontakt_id']} Max Muster (Muster GmbH)" in fehlertext
    assert "duplikat_bestaetigt=true" in fehlertext


def test_lesen_liefert_erweiterte_objekt_und_kinddaten_ohne_benachrichtigungen(client):
    seed = _seed("obj-lesen-erweitert", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    assert _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"])["wohnanlage"] is None
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, seed["objekt_id"])
        kategorie = ObjektKategorie(org_id=seed["org_id"], name="Industrie")
        gefahr = GefahrenKatalog(org_id=seed["org_id"], name="Chemie")
        merkmal = MerkmalKatalog(org_id=seed["org_id"], name="Schluesselbox")
        db.add_all([kategorie, gefahr, merkmal])
        db.flush()
        objekt.kategorie_id = kategorie.id
        objekt.informationen = "Zutritt ueber Portier"
        objekt.anfahrtsweg = "Nordtor verwenden"
        objekt.revision_datum = date(2026, 10, 1)
        objekt.lat, objekt.lng = 48.2082, 16.3738
        objekt.bma.bmz_standort = "Foyer"
        objekt.bma.fbf_standort = "Eingang"
        objekt.bma.laufkarten_ablageort = "BMZ"
        objekt.bma.uebertragungseinrichtung = "UE-1"
        objekt.bma.schluesselsafe_vorhanden = True
        objekt.bma.schluesselsafe_standort = "Tor"
        objekt.bma.schluesselsafe_inhalt = "Generalschluessel"
        objekt.bma.benachrichtigung_sms = "+436641234"
        objekt.bma.benachrichtigung_email = "alarm@example.test"
        db.add_all([
            ObjektGefahr(
                org_id=seed["org_id"], objekt_id=objekt.id, gefahr_id=gefahr.id,
                un_nummer="1203", stoffname="Benzin", links_json='[{"label":"SDB","url":"https://example.test/sdb"}]',
            ),
            ObjektMerkmal(org_id=seed["org_id"], objekt_id=objekt.id, merkmal_id=merkmal.id, hinweis="Beim Tor"),
            ObjektWohnanlage(
                org_id=seed["org_id"], objekt_id=objekt.id, wohneinheiten=24, geschosse=6, stiegen=2,
                hinweise="Feuerwehrzufahrt freihalten",
            ),
        ])
        db.commit()
    finally:
        db.close()

    gelesen = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"])
    felder = ("informationen", "anfahrtsweg", "revision_datum", "kategorie", "lat", "lng")
    assert {key: gelesen[key] for key in felder} == {
        "informationen": "Zutritt ueber Portier", "anfahrtsweg": "Nordtor verwenden", "revision_datum": "2026-10-01",
        "kategorie": "Industrie", "lat": 48.2082, "lng": 16.3738,
    }
    assert gelesen["bma"] == {
        "bma_nummer": "BMA-1", "rfl_nummer": "RFL-1", "bmz_standort": "Foyer", "fbf_standort": "Eingang",
        "laufkarten_ablageort": "BMZ", "uebertragungseinrichtung": "UE-1", "schluesselsafe_vorhanden": True,
        "schluesselsafe_standort": "Tor", "schluesselsafe_inhalt": "Generalschluessel",
        "benachrichtigung_sms_gesetzt": True, "benachrichtigung_email_gesetzt": True,
    }
    assert gelesen["gefahren"][0]["name"] == "Chemie"
    assert gelesen["gefahren"][0]["un_nummer"] == "1203"
    assert gelesen["gefahren"][0]["stoffname"] == "Benzin"
    assert gelesen["gefahren"][0]["links"] == [{"label": "SDB", "url": "https://example.test/sdb"}]
    assert gelesen["merkmale"][0]["name"] == "Schluesselbox"
    assert gelesen["merkmale"][0]["hinweis"] == "Beim Tor"
    assert gelesen["wohnanlage"] == {
        "wohneinheiten": 24, "geschosse": 6, "stiegen": 2, "hausverwaltung_kontakt_id": None,
        "hinweise": "Feuerwehrzufahrt freihalten",
    }


def test_informationen_werden_in_arbeitskopie_aktualisiert(client):
    seed = _seed("obj-informationen-kopie", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    aktualisiert = _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"],
        stammdaten={"informationen": "Schluessel beim Portier"},
    )
    basis = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"])
    kopie = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"], arbeitskopie=True)
    assert aktualisiert["objekt_id"] == kopie["id"] != seed["objekt_id"]
    assert kopie["informationen"] == "Schluessel beim Portier"
    assert basis["informationen"] is None
    assert basis["arbeitskopie_hinweis"]


def test_merkmale_aendern_auf_entwurf_und_basis_id_der_arbeitskopie(client):
    seed = _seed("obj-merkmale-aendern", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        katalog = MerkmalKatalog(org_id=seed["org_id"], name="FSD")
        entwurf = Objekt(org_id=seed["org_id"], nummer=2, name="Entwurf", status="entwurf")
        db.add_all([katalog, entwurf])
        db.flush()
        basis_merkmal = ObjektMerkmal(
            org_id=seed["org_id"], objekt_id=seed["objekt_id"], merkmal_id=katalog.id, hinweis="Basis"
        )
        entwurf_merkmal = ObjektMerkmal(
            org_id=seed["org_id"], objekt_id=entwurf.id, merkmal_id=katalog.id, hinweis="Alt"
        )
        db.add_all([basis_merkmal, entwurf_merkmal])
        db.commit()
        entwurf_id, entwurf_merkmal_id, basis_merkmal_id = entwurf.id, entwurf_merkmal.id, basis_merkmal.id
    finally:
        db.close()
    _rufe(
        client, token, "objekt_aktualisieren", objekt_id=entwurf_id,
        merkmale_aendern=[{"id": entwurf_merkmal_id, "hinweis": "Neu"}],
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ObjektMerkmal, entwurf_merkmal_id).hinweis == "Neu"
        assert db.query(ObjektChange).filter_by(objekt_id=entwurf_id, feld="merkmal_hinweis").count() == 1
    finally:
        db.close()
    kopie_result = _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"],
        merkmale_aendern=[{"id": basis_merkmal_id, "hinweis": "Nur Kopie"}],
    )
    assert _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"])["merkmale"][0]["hinweis"] == "Basis"
    kopie = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"], arbeitskopie=True)
    assert kopie["merkmale"][0]["hinweis"] == "Nur Kopie"
    assert kopie_result["objekt_id"] != seed["objekt_id"]
    assert "__fehler__" in _rufe(
        client, token, "objekt_aktualisieren", objekt_id=entwurf_id, merkmale_aendern=[{"id": 999999}]
    )
    assert "__fehler__" in _rufe(
        client, token, "objekt_aktualisieren", objekt_id=entwurf_id,
        merkmale_aendern=[{"id": entwurf_merkmal_id, "unbekannt": "x"}],
    )


def test_merkmale_hinzufuegen_aktualisiert_bestehenden_hinweis_ohne_dublette(client):
    seed = _seed("obj-merkmale-hinzufuegen", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = Objekt(org_id=seed["org_id"], nummer=2, name="Entwurf", status="entwurf")
        merkmal = MerkmalKatalog(org_id=seed["org_id"], name="Tiefgarage")
        db.add_all([objekt, merkmal])
        db.flush()
        db.add(ObjektMerkmal(org_id=seed["org_id"], objekt_id=objekt.id, merkmal_id=merkmal.id, hinweis="Alt"))
        db.commit()
        objekt_id, merkmal_id = objekt.id, merkmal.id
    finally:
        db.close()
    _rufe(
        client, token, "objekt_aktualisieren", objekt_id=objekt_id,
        merkmale_hinzufuegen=[{"merkmal_id": merkmal_id, "hinweis": "Neu"}],
    )
    wiederholt = _rufe(
        client, token, "objekt_aktualisieren", objekt_id=objekt_id, merkmale_hinzufuegen=[{"merkmal_id": merkmal_id}]
    )
    gelesen = _rufe(client, token, "objekt_lesen", objekt_id=objekt_id)
    assert len(gelesen["merkmale"]) == 1 and gelesen["merkmale"][0]["hinweis"] == "Neu"
    assert wiederholt["hinweise"] == [f"Merkmal {merkmal_id} ist bereits zugeordnet."]


def test_gefahren_aendern_teilupdates_links_und_basis_ids(client):
    seed = _seed("obj-gefahren-aendern", {"admin": "objekt_verwalter"})
    other = _seed("obj-gefahren-aendern-fremd", {"admin": "objekt_verwalter"})
    token = _token(client, seed, "admin")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        katalog = GefahrenKatalog(org_id=seed["org_id"], name="Gas")
        db.add(katalog)
        db.flush()
        gefahr = ObjektGefahr(
            org_id=seed["org_id"], objekt_id=seed["objekt_id"], gefahr_id=katalog.id,
            un_nummer="1971", stoffname="Alt", detail="Detail",
        )
        fremde_katalog = GefahrenKatalog(org_id=other["org_id"], name="Fremd")
        db.add_all([gefahr, fremde_katalog])
        db.flush()
        fremde_gefahr = ObjektGefahr(
            org_id=other["org_id"], objekt_id=other["objekt_id"], gefahr_id=fremde_katalog.id,
        )
        db.add(fremde_gefahr)
        db.commit()
        gefahr_id, fremde_gefahr_id = gefahr.id, fremde_gefahr.id
    finally:
        db.close()
    _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"],
        gefahren_aendern=[{"id": gefahr_id, "stoffname": "Erdgas", "links": [{"label": "SDB", "url": "https://example.test/sdb"}]}],
    )
    kopie = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"], arbeitskopie=True)
    assert kopie["gefahren"][0]["un_nummer"] == "1971"
    assert kopie["gefahren"][0]["stoffname"] == "Erdgas"
    assert kopie["gefahren"][0]["links"] == [{"label": "SDB", "url": "https://example.test/sdb"}]
    assert _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"])["gefahren"][0]["stoffname"] == "Alt"
    _rufe(
        client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"],
        gefahren_aendern=[{"id": gefahr_id, "stoffname": "", "un_nummer": ""}],
    )
    kopie = _rufe(client, token, "objekt_lesen", objekt_id=seed["objekt_id"], arbeitskopie=True)
    assert kopie["gefahren"][0]["stoffname"] is None and kopie["gefahren"][0]["un_nummer"] is None
    for aenderung in (
        [{"id": gefahr_id, "unbekannt": "x"}],
        [{"id": 999999, "stoffname": "Nein"}],
        [{"id": fremde_gefahr_id, "stoffname": "Nein"}],
    ):
        assert "__fehler__" in _rufe(
            client, token, "objekt_aktualisieren", objekt_id=seed["objekt_id"], gefahren_aendern=aenderung
        )
