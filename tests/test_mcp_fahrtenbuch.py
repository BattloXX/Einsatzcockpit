"""Fahrtenbuch-MCP-Tools: Rechte, Mandantentrennung, Zeitzonen, Ausgabefelder, nur lesend."""
import json
from datetime import datetime
from decimal import Decimal

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS
from app.models.fahrtenbuch import Fahrt, FahrtKategorie, FahrtStatus, Fahrtzweck, Zielort
from app.models.master import FireDept, OrgSettings, SystemSettings, VehicleMaster
from app.models.user import Role, User, UserRole
from tests.test_mcp_fundament import _mcp, _oauth_tokens

FAHRTENBUCH_TOOLS = (
    "fahrtenbuch_stammdaten", "fahrtenbuch_fahrten", "fahrtenbuch_fahrt", "fahrtenbuch_auswertung",
)
VERBOTENE_FELDER = {
    "qr_token", "schaden_mail_override", "schaden_teams_webhook_override", "lis_reference_id",
    "empfaenger", "token_label", "erfasst_von_user_id", "erfasst_via", "storage_path", "phone", "email",
    "schaden_beschreibung",
}


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=None)


def _seed(slug: str, rollen: dict[str, str], *, fahrtenbuch: bool = True, mcp: bool = True) -> dict:
    """Legt Org, Benutzer je Rolle, Stammdaten und Fahrten an."""
    db = SessionLocal()
    set_tenant_context(db, None)
    org = FireDept(slug=slug, name=f"Org {slug}")
    db.add(org)
    db.flush()
    db.add(OrgSettings(org_id=org.id, mcp_modul_aktiv=mcp, fahrtenbuch_modul_aktiv=fahrtenbuch))
    row = db.query(SystemSettings).filter(SystemSettings.key == "mcp_module_enabled").first()
    if row is None:
        db.add(SystemSettings(key="mcp_module_enabled", value="true"))
    else:
        row.value = "true"
    users = {}
    for username, rolle in rollen.items():
        user = User(username=username, display_name=username, org_id=org.id, password_hash=hash_password("Test1234!"))
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == rolle).first()
        assert role, rolle
        db.add(UserRole(user_id=user.id, role_id=role.id))
        users[username] = user.id
    fz = VehicleMaster(dept_id=org.id, code=f"{slug}-RLF", name="Rüstlöschfahrzeug", type="RLF",
                       kennzeichen="B-1", display_order=1, qr_token=f"GEHEIM-QR-TOKEN-{slug}",
                       schaden_mail_override="geheim@example.org", lis_reference_id="LIS-1")
    fz_adhoc = VehicleMaster(dept_id=org.id, code=f"{slug}-ADHOC", name="Adhoc", type="X", display_order=2,
                             is_adhoc=True)
    fz_ext = VehicleMaster(dept_id=org.id, code=f"{slug}-EXT", name="Extern", type="X", display_order=3,
                           is_external=True)
    fz_del = VehicleMaster(dept_id=org.id, code=f"{slug}-DEL", name="Geloescht", type="X", display_order=4,
                           deleted=True)
    db.add_all([fz, fz_adhoc, fz_ext, fz_del])
    z_ue = Fahrtzweck(org_id=org.id, name="Übungsfahrt", kategorie=FahrtKategorie.uebung)
    z_ei = Fahrtzweck(org_id=org.id, name="Einsatzfahrt", kategorie=FahrtKategorie.einsatz)
    ziel = Zielort(org_id=org.id, name="Feuerwehrhaus")
    db.add_all([z_ue, z_ei, ziel])
    db.flush()

    def fahrt(zeit: str, zweck, typ, km, bh, name="Muster Max", **extra):
        f = Fahrt(org_id=org.id, zeitpunkt=_utc(zeit), fahrzeug_id=fz.id, zweck_id=zweck.id, fahrttyp=typ,
                  maschinist_name=name, km_delta=km, km_stand_neu=1000 + km,
                  betriebsstunden_delta=Decimal(bh), zielort_id=ziel.id, bemerkung="ok",
                  erfasst_via="token", token_label="Tablet 1", **extra)
        db.add(f)
        db.flush()
        return f

    fahrten = {
        # Sommerzeit (UTC+2): 30.06. 21:30 UTC = 23:30 lokal am 30.06.; 22:30 UTC = 00:30 lokal am 01.07.
        "juni_spaet": fahrt("2026-06-30T21:30:00", z_ue, FahrtKategorie.uebung, 10, "1.0"),
        "juli_frueh": fahrt("2026-06-30T22:30:00", z_ei, FahrtKategorie.einsatz, 20, "2.5", name="Beispiel Berta"),
        # Winterzeit (UTC+1): 31.01. 23:30 UTC = 01.02. 00:30 lokal
        "feb_frueh": fahrt("2026-01-31T23:30:00", z_ue, FahrtKategorie.uebung, 5, "0.5"),
        "storniert": fahrt("2026-07-05T10:00:00", z_ue, FahrtKategorie.uebung, 99, "9.0",
                           status=FahrtStatus.storniert),
        "unstat": fahrt("2026-07-06T10:00:00", z_ue, FahrtKategorie.uebung, 77, "7.0",
                        nicht_statistikrelevant=True),
    }
    ids = {k: v.id for k, v in fahrten.items()}
    result = {"org_id": org.id, "users": users, "fahrten": ids, "fahrzeug_id": fz.id,
              "zweck_ue": z_ue.id, "adhoc": fz_adhoc.id}
    db.commit()
    db.close()
    return result


def _rpc(response) -> dict:
    """Antwort ist JSON oder ein SSE-Stream mit einer data-Zeile."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        zeilen = [z[5:].strip() for z in response.text.splitlines() if z.startswith("data:")]
        return json.loads(zeilen[-1])
    return response.json()


def _ergebnis(response) -> dict:
    assert response.status_code == 200, response.text
    body = _rpc(response)["result"]
    if body.get("isError"):
        return {"__fehler__": json.dumps(body["content"], ensure_ascii=False)}
    if body.get("structuredContent"):
        data = body["structuredContent"]
        return data.get("result", data)
    return json.loads(body["content"][0]["text"])


def _rufe(client, token: str, tool: str, **arguments) -> dict:
    return _ergebnis(_mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 10))


def _token(client, seed: dict, username: str) -> str:
    return _oauth_tokens(client, {"username": username})["access_token"]


def _alle_schluessel(daten) -> set[str]:
    if isinstance(daten, dict):
        return set(daten) | {k for v in daten.values() for k in _alle_schluessel(v)}
    if isinstance(daten, list):
        return {k for v in daten for k in _alle_schluessel(v)}
    return set()


def test_alle_fahrtenbuch_tools_sind_registriert_lesend_und_adminpflichtig():
    assert {n for n in TOOLS if n.startswith("fahrtenbuch_")} == set(FAHRTENBUCH_TOOLS)
    for name in FAHRTENBUCH_TOOLS:
        assert TOOLS[name].required_roles == ("fahrtenbuch_admin",)
        assert TOOLS[name].module_check is not None
    schreibend = ("anlegen", "erstellen", "storno", "korrig", "loesch", "aendern", "update", "create", "delete")
    assert not [n for n in TOOLS if n.startswith("fahrtenbuch_") and any(w in n for w in schreibend)]


@pytest.mark.parametrize(
    ("rolle", "erlaubt"),
    [
        ("fahrtenbuch_admin", True), ("org_admin", True), ("admin", True),
        ("recorder", False), ("readonly", False), ("objekt_verwalter", False),
        ("incident_leader", False), ("kontakt_verwalter", False),
    ],
)
def test_rollenmatrix_live_kontext(rolle, erlaubt):
    seed = _seed(f"fb-rolle-{rolle}", {f"u_{rolle}": rolle})
    db = SessionLocal()
    try:
        user_id = seed["users"][f"u_{rolle}"]
        if erlaubt:
            assert load_live_context(db, user_id, seed["org_id"], ("fahrtenbuch_admin",)).org_id == seed["org_id"]
        else:
            with pytest.raises(MCPPermissionError):
                load_live_context(db, user_id, seed["org_id"], ("fahrtenbuch_admin",))
    finally:
        db.close()


def test_http_tools_list_und_aufruf_admin_ok_recorder_verweigert(client):
    seed = _seed("fb-http", {"fb_admin": "fahrtenbuch_admin", "fb_recorder": "recorder"})
    admin = _token(client, seed, "fb_admin")
    listed = _mcp(client, admin, "tools/list", {}, 2).text
    for name in FAHRTENBUCH_TOOLS:
        assert name in listed
    stamm = _rufe(client, admin, "fahrtenbuch_stammdaten")
    assert [f["code"] for f in stamm["fahrzeuge"]] == ["fb-http-RLF"]

    recorder = _token(client, seed, "fb_recorder")
    assert "fahrtenbuch_" not in _mcp(client, recorder, "tools/list", {}, 3).text
    assert "__fehler__" in _rufe(client, recorder, "fahrtenbuch_fahrten")


def test_module_aus_verweigert(client):
    seed = _seed("fb-modul-aus", {"fb_admin_aus": "fahrtenbuch_admin"}, fahrtenbuch=False)
    token = _token(client, seed, "fb_admin_aus")
    assert "fahrtenbuch_" not in _mcp(client, token, "tools/list", {}, 2).text
    assert "__fehler__" in _rufe(client, token, "fahrtenbuch_fahrten")

    # MCP-Modul der Org aus: schon der Login ist gesperrt, und der Live-Kontext verweigert.
    seed2 = _seed("fb-mcp-aus", {"fb_admin_mcp_aus": "fahrtenbuch_admin"}, mcp=False)
    db = SessionLocal()
    try:
        with pytest.raises(MCPPermissionError):
            load_live_context(db, seed2["users"]["fb_admin_mcp_aus"], seed2["org_id"], ("fahrtenbuch_admin",))
    finally:
        db.close()
    with pytest.raises(AssertionError):
        _token(client, seed2, "fb_admin_mcp_aus")


def test_cross_org_isolation(client):
    seed_a = _seed("fb-org-a", {"fb_a": "fahrtenbuch_admin"})
    seed_b = _seed("fb-org-b", {"fb_b": "fahrtenbuch_admin"})
    token_a = _token(client, seed_a, "fb_a")
    liste = _rufe(client, token_a, "fahrtenbuch_fahrten", status="alle", limit=200)
    ids = {f["id"] for f in liste["fahrten"]}
    assert ids == set(seed_a["fahrten"].values())
    assert not ids & set(seed_b["fahrten"].values())
    fremd = _rufe(client, token_a, "fahrtenbuch_fahrt", fahrt_id=seed_b["fahrten"]["juli_frueh"])
    assert "__fehler__" in fremd
    stamm = _rufe(client, token_a, "fahrtenbuch_stammdaten")
    assert all(f["code"].startswith("fb-org-a") for f in stamm["fahrzeuge"])
    auswertung = _rufe(client, token_a, "fahrtenbuch_auswertung", gruppierung="fahrzeug")
    assert {z["label"] for z in auswertung["zeilen"]} == {"fb-org-a-RLF"}


def test_zeitzonen_und_tagesgrenzen(client):
    seed = _seed("fb-zeit", {"fb_zeit": "fahrtenbuch_admin"})
    token = _token(client, seed, "fb_zeit")
    f = seed["fahrten"]
    juli = _rufe(client, token, "fahrtenbuch_fahrten", von="2026-07-01", bis="2026-07-31", status="alle")
    juli_ids = {x["id"] for x in juli["fahrten"]}
    assert f["juli_frueh"] in juli_ids and f["juli_frueh"] not in {f["juni_spaet"]}
    assert f["juni_spaet"] not in juli_ids
    juni = _rufe(client, token, "fahrtenbuch_fahrten", von="2026-06-01", bis="2026-06-30", status="alle")
    juni_ids = {x["id"] for x in juni["fahrten"]}
    assert f["juni_spaet"] in juni_ids and f["juli_frueh"] not in juni_ids
    feb = _rufe(client, token, "fahrtenbuch_fahrten", von="2026-02-01", bis="2026-02-28")
    assert {x["id"] for x in feb["fahrten"]} == {f["feb_frueh"]}
    assert feb["fahrten"][0]["zeitpunkt"].startswith("2026-02-01T00:30:00+01:00")
    assert juli["fahrten"][0]["zeitpunkt"].endswith("+02:00")


def test_status_statistik_und_fahrzeugfilter(client):
    seed = _seed("fb-status", {"fb_status": "fahrtenbuch_admin"})
    token = _token(client, seed, "fb_status")
    f = seed["fahrten"]
    aktiv = {x["id"] for x in _rufe(client, token, "fahrtenbuch_fahrten", limit=200)["fahrten"]}
    assert f["storniert"] not in aktiv and f["unstat"] in aktiv
    alle = {x["id"] for x in _rufe(client, token, "fahrtenbuch_fahrten", status="alle", limit=200)["fahrten"]}
    assert f["storniert"] in alle
    stat = {x["id"] for x in _rufe(client, token, "fahrtenbuch_fahrten", nur_statistikrelevant=True,
                                   limit=200)["fahrten"]}
    assert f["unstat"] not in stat
    ungueltig = _rufe(client, token, "fahrtenbuch_fahrten", status="geloescht")
    assert "__fehler__" in ungueltig
    stamm = _rufe(client, token, "fahrtenbuch_stammdaten")
    assert [x["id"] for x in stamm["fahrzeuge"]] == [seed["fahrzeug_id"]]
    assert {z["name"] for z in stamm["zwecke"]} == {"Übungsfahrt", "Einsatzfahrt"}


def test_ausgabe_enthaelt_keine_sensiblen_felder(client):
    seed = _seed("fb-felder", {"fb_felder": "fahrtenbuch_admin"})
    token = _token(client, seed, "fb_felder")
    ausgaben = [
        _rufe(client, token, "fahrtenbuch_stammdaten"),
        _rufe(client, token, "fahrtenbuch_fahrten", status="alle", limit=200),
        _rufe(client, token, "fahrtenbuch_fahrt", fahrt_id=seed["fahrten"]["juli_frueh"]),
        _rufe(client, token, "fahrtenbuch_auswertung", gruppierung="zweck"),
    ]
    for daten in ausgaben:
        assert "__fehler__" not in daten
        assert not _alle_schluessel(daten) & VERBOTENE_FELDER
        text = json.dumps(daten)
        assert "GEHEIM-QR-TOKEN" not in text and "geheim@example.org" not in text and "Tablet 1" not in text


def test_auswertung_gruppierungen_und_summen(client):
    seed = _seed("fb-auswertung", {"fb_ausw": "fahrtenbuch_admin"})
    token = _token(client, seed, "fb_ausw")
    fzg = _rufe(client, token, "fahrtenbuch_auswertung", gruppierung="fahrzeug")
    (zeile,) = fzg["zeilen"]  # storniert und nicht statistikrelevant ausgeschlossen
    assert zeile["anzahl"] == 3 and zeile["km_summe"] == 35 and zeile["betriebsstunden_summe"] == 4.0
    assert (zeile["einsatz"], zeile["uebung"]) == (1, 2)
    monat = {z["label"]: z for z in _rufe(client, token, "fahrtenbuch_auswertung", gruppierung="monat")["zeilen"]}
    assert set(monat) == {"2026-02", "2026-06", "2026-07"}
    assert monat["2026-07"]["km_summe"] == 20
    kat = {z["label"]: z["anzahl"] for z in _rufe(client, token, "fahrtenbuch_auswertung",
                                                   gruppierung="kategorie")["zeilen"]}
    assert sum(kat.values()) == 3
    zweck = {z["label"]: z["anzahl"] for z in _rufe(client, token, "fahrtenbuch_auswertung",
                                                    gruppierung="zweck")["zeilen"]}
    assert zweck == {"Übungsfahrt": 2, "Einsatzfahrt": 1}
    masch = {z["label"]: z["anzahl"] for z in _rufe(client, token, "fahrtenbuch_auswertung",
                                                    gruppierung="maschinist")["zeilen"]}
    assert masch == {"Muster Max": 2, "Beispiel Berta": 1}
    assert "__fehler__" in _rufe(client, token, "fahrtenbuch_auswertung", gruppierung="fahrer")
    eingeschraenkt = _rufe(client, token, "fahrtenbuch_auswertung", gruppierung="fahrzeug",
                           von="2026-07-01", bis="2026-07-31")
    assert eingeschraenkt["zeilen"][0]["anzahl"] == 1


def test_auswertung_stimmt_mit_statistik_ueberein():
    """Gleiche Zahlen wie die bestehende UI-Statistik (Fahrzeug- und Maschinistengruppierung)."""
    from app.routers.ui_stats import _gruppiere_fahrten
    from app.services.fahrtenbuch_query_service import gefilterte_fahrten_query

    seed = _seed("fb-stats", {"fb_stats": "fahrtenbuch_admin"})
    db = SessionLocal()
    set_tenant_context(db, seed["org_id"])
    try:
        org = db.get(FireDept, seed["org_id"])
        fahrten = gefilterte_fahrten_query(db, seed["org_id"], org, status="aktiv",
                                           nur_statistikrelevant=True).all()
        fahrzeuge = db.query(VehicleMaster).filter(VehicleMaster.dept_id == seed["org_id"]).all()
        erwartet = _gruppiere_fahrten(fahrten, "maschinist", fahrzeuge)
        assert sorted((g["label"], g["km_sum"]) for g in erwartet) == [("Beispiel Berta", 20), ("Muster Max", 15)]
    finally:
        db.close()


def test_limit_seiten_und_truncated(client):
    seed = _seed("fb-limit", {"fb_limit": "fahrtenbuch_admin"})
    token = _token(client, seed, "fb_limit")
    seite1 = _rufe(client, token, "fahrtenbuch_fahrten", status="alle", limit=2, seite=1)
    assert len(seite1["fahrten"]) == 2 and seite1["gesamt"] == 5 and seite1["truncated"] is True
    seite3 = _rufe(client, token, "fahrtenbuch_fahrten", status="alle", limit=2, seite=3)
    assert len(seite3["fahrten"]) == 1 and seite3["truncated"] is False
    assert "__fehler__" in _rufe(client, token, "fahrtenbuch_fahrten", limit=201)
    assert "__fehler__" in _rufe(client, token, "fahrtenbuch_fahrten", limit=0)


def test_tools_schreiben_nichts():
    """Die Handler committen nicht und hinterlassen keine geaenderten Objekte."""
    import asyncio

    from app.mcp.context import MCPContext

    seed = _seed("fb-readonly", {"fb_ro": "fahrtenbuch_admin"})
    db = SessionLocal()
    set_tenant_context(db, seed["org_id"])
    try:
        user = db.get(User, seed["users"]["fb_ro"])
        context = MCPContext(db=db, user=user, org_id=seed["org_id"])
        commits = []
        db.commit = lambda: commits.append(1)  # type: ignore[method-assign]
        for name, args in (
            ("fahrtenbuch_stammdaten", {}),
            ("fahrtenbuch_fahrten", {"status": "alle"}),
            ("fahrtenbuch_fahrt", {"fahrt_id": seed["fahrten"]["juli_frueh"]}),
            ("fahrtenbuch_auswertung", {"gruppierung": "monat"}),
        ):
            asyncio.run(TOOLS[name].handler(context, **args))
            assert not (db.new or db.dirty or db.deleted), name
        assert commits == []
    finally:
        db.close()
