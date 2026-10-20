"""K10: MCP-Snapshots, Live-Rechte und Schutz vor Datenabfluss."""

import asyncio
import json
from urllib.parse import urlsplit

import pytest

from app.config import settings
from app.core.permissions import KATPLAN_LESE_ROLLEN
from app.core.tenant import set_tenant_context
from app.mcp.context import MCPContext
from app.mcp.registry import TOOLS
from app.models.katplan import (
    KatplanAnhang, KatplanDokument, KatplanDokumentSeite, KatplanElement,
    KatplanLink, KatplanPlan, KatplanRelation,
)
from app.models.kontakt import Kontakt, KontaktTelefon
from app.models.master import OrgSettings, SystemSettings
from app.models.user import AuditLog, User
from app.services.katplan_freigabe_service import freigeben
from app.services.katplan_service import baue_suchtext
from app.services.mcp_download_service import MCPDownloadFehler, lade_download
from tests.conftest import TestingSession
from tests.test_katplan_k1b import umgebung  # noqa: F401
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_fundament import _mcp, _oauth_tokens
from tests.test_mcp_strassensperren import _rpc, _rufe

NAMEN = {
    "katplan_liste", "katplan_lesen", "katplan_suchen", "katplan_elemente", "katplan_element_lesen",
    "katplan_orte_suchen", "katplan_dokument_auszug", "katplan_verknuepfungen",
}


def rufe(info, name, rolle="readonly", **kwargs):
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=info["users"][rolle]).one()
        return asyncio.run(TOOLS[name].handler(MCPContext(db, user, user.org_id), **kwargs))


@pytest.fixture
def inhalte(daten, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path))
    (tmp_path / "original.pdf").write_bytes(b"%PDF-1.4 source")
    (tmp_path / "seite.pdf").write_bytes(b"%PDF-1.4 page")
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, daten["plan_id"])
        user = db.query(User).filter_by(username=daten["users"]["katplan_freigeber"]).one()
        geheim = db.query(KatplanElement).filter_by(plan_id=plan.id, vertraulich=True).one()
        offen = db.get(KatplanElement, daten["element_id"])
        offen.beschreibung_html = "<p>Offener Inhalt</p><ul><li>Schritt eins</li><li>Schritt zwei</li></ul>"
        kontakt = Kontakt(org_id=plan.org_id, anzeigename="Bauhof", einsatzrelevant=True)
        privat = Kontakt(org_id=plan.org_id, anzeigename="Privater Kontakt", einsatzrelevant=False)
        db.add_all([kontakt, privat])
        db.flush()
        for k in (kontakt, privat):
            for verwendung, nummer in (("dienst", "+43557112345"), ("privat", "+43664123456")):
                db.add(KontaktTelefon(org_id=plan.org_id, kontakt_id=k.id, nummer=nummer, verwendung=verwendung))
        for eid, k in ((None, kontakt), (offen.id, privat), (geheim.id, kontakt)):
            db.add(KatplanLink(org_id=plan.org_id, plan_id=plan.id, element_id=eid,
                               ziel_typ="kontakt", ziel_id=k.id, rolle="zustaendig"))
        for eid, vertraulich, titel in ((None, False, "Offener Anhang"), (None, True, "Geheimer Anhang"),
                                       (geheim.id, False, "Geheimes Elementfoto")):
            db.add(KatplanAnhang(org_id=plan.org_id, plan_id=plan.id, element_id=eid,
                                 art="link", url="https://example.org/", beschriftung=titel, vertraulich=vertraulich))
        db.add(KatplanRelation(org_id=plan.org_id, plan_id=plan.id, von_element_id=offen.id,
                               zu_element_uuid=geheim.uuid, art="siehe_auch"))
        db.add(KatplanRelation(org_id=plan.org_id, plan_id=plan.id, von_element_id=geheim.id,
                               zu_element_uuid=offen.uuid, art="siehe_auch"))
        dok = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="Freigegebene Quelle",
                              dateiname_original="quelle.pdf", pfad="original.pdf", groesse_bytes=20,
                              seitenzahl=25, status="fertig")
        db.add(dok)
        db.flush()
        for nr in range(1, 26):
            db.add(KatplanDokumentSeite(org_id=plan.org_id, plan_id=plan.id, dokument_id=dok.id,
                                       seiten_nr=nr, titel=f"Titel {nr}", volltext=f"Quelltext Seite {nr}",
                                       einzel_pdf_pfad="seite.pdf"))
        offen.quelle_dokument_id, offen.quelle_seiten = dok.id, "1-2"
        dok.ist_aktuelle_version = False
        # Quelle nur am sichtbaren Element referenziert, nicht planweit.
        plan.status = "in_ueberarbeitung"
        v = freigeben(db, plan, user)
        neu = KatplanDokument(org_id=plan.org_id, plan_id=plan.id, titel="Arbeitsquelle",
                              dateiname_original="neu.pdf", pfad="original.pdf", groesse_bytes=1, seitenzahl=1)
        db.add(neu)
        for org in db.query(OrgSettings).filter(OrgSettings.org_id.in_([plan.org_id])):
            org.mcp_modul_aktiv = True
        flag = db.query(SystemSettings).filter_by(key="mcp_module_enabled").first()
        if flag is None:
            db.add(SystemSettings(key="mcp_module_enabled", value="true"))
        else:
            flag.value = "true"
        db.commit()
        return {**daten, "dokument_id": dok.id, "neu_id": neu.id, "geheim_uuid": geheim.uuid,
                "version_id": v.id}


def test_registrierung_http_und_live_modul(client, inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["readonly"]).one()
        ident = {"id": user.id, "org_id": user.org_id, "username": user.username}
    token = _oauth_tokens(client, ident)["access_token"]
    for name in NAMEN:
        assert TOOLS[name].required_roles == KATPLAN_LESE_ROLLEN
        assert TOOLS[name].module_check is not None
    liste = _rpc(_mcp(client, token, "tools/list", {}, 12))["result"]["tools"]
    assert NAMEN <= {t["name"] for t in liste}
    assert _rufe(client, token, "katplan_lesen", plan_id=inhalte["plan_id"])["plan_id"] == inhalte["plan_id"]
    for name, args in (
        ("katplan_liste", {}),
        ("katplan_suchen", {"q": "wasser"}),
        ("katplan_elemente", {"plan_id": inhalte["plan_id"]}),
        ("katplan_element_lesen", {"plan_id": inhalte["plan_id"], "element_id": "KS-01"}),
        ("katplan_orte_suchen", {"lat": 47, "lng": 9}),
        ("katplan_dokument_auszug", {"plan_id": inhalte["plan_id"], "dokument_id": inhalte["dokument_id"], "seite": 1}),
        ("katplan_verknuepfungen", {"plan_id": inhalte["plan_id"]}),
    ):
        assert "__fehler__" not in _rufe(client, token, name, **args)
    assert "Nicht gefunden" in _rufe(client, token, "katplan_element_lesen", plan_id=inhalte["plan_id"],
                                     element_id=inhalte["geheim_uuid"])["__fehler__"]
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=inhalte["org_id"]).one().katplan_modul_aktiv = False
        db.commit()
    liste = _rpc(_mcp(client, token, "tools/list", {}, 13))["result"]["tools"]
    assert not NAMEN & {t["name"] for t in liste}
    assert "__fehler__" in _rufe(client, token, "katplan_lesen", plan_id=inhalte["plan_id"])


def test_freigabe_arbeitsstand_und_format(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.get(KatplanElement, inhalte["element_id"])
        e.titel = "Nur im Arbeitsstand"
        e.suchtext = baue_suchtext(e, [])
        db.get(KatplanPlan, inhalte["plan_id"]).titel = "Geänderter Kopf"
        db.commit()
    args = {"plan_id": inhalte["plan_id"]}
    for rolle in ("readonly", "katplan_verwalter"):
        result = rufe(inhalte, "katplan_lesen", rolle, mit_elementen=True, **args)
        assert result["kopf"]["titel"] == inhalte["titel"]
        e = rufe(inhalte, "katplan_element_lesen", rolle, element_id="KS-01", **args)
        assert e == rufe(inhalte, "katplan_element_lesen", rolle, element_id=inhalte["uuid"], **args)
        assert e["title"] == "Wasser Brücke"
        assert e["body"] == "Offener Inhalt\n- Schritt eins\n- Schritt zwei"
        assert set(e) == {"plan_id", "plan_version_id", "element_id", "element_version_id", "type", "type_label",
                          "kennung", "title", "body", "daten", "priority", "tags", "phases", "chapter", "address",
                          "geometry", "geo_status", "linked_entities", "relations", "attachments", "source_references",
                          "review_status", "vertraulich", "gueltig_bis", "pruefen_am", "warnings"}
        assert e["source_references"][0]["titel"] == "Freigegebene Quelle"
        with pytest.raises(PermissionError):
            rufe(inhalte, "katplan_lesen", fassung="arbeitsstand", **args)
    arbeit = rufe(inhalte, "katplan_element_lesen", "katplan_verwalter", fassung="arbeitsstand", element_id="KS-01", **args)
    assert arbeit["title"] == "Nur im Arbeitsstand" and arbeit["element_version_id"] is None
    assert rufe(inhalte, "katplan_suchen", q="Arbeitsstand")["gesamt"] == 0
    assert rufe(inhalte, "katplan_suchen", "katplan_verwalter", q="Arbeitsstand", fassung="arbeitsstand")["gesamt"] == 1
    with pytest.raises(PermissionError):
        rufe(inhalte, "katplan_suchen", fassung="arbeitsstand", q="wasser")


@pytest.mark.parametrize("rolle,anzahl", [("readonly", 2), ("incident_leader", 3)])
def test_vertrauliches_ueber_alle_tools(inhalte, rolle, anzahl):
    args = {"plan_id": inhalte["plan_id"]}
    assert rufe(inhalte, "katplan_liste", rolle)["plaene"][0]["anzahl_elemente"] == anzahl
    result = rufe(inhalte, "katplan_lesen", rolle, mit_elementen=True, **args)
    assert len(result["elemente"]) == anzahl
    assert len(result["anhaenge"]) == (1 if rolle == "readonly" else 2)
    assert len(rufe(inhalte, "katplan_elemente", rolle, **args)["elemente"]) == anzahl
    assert rufe(inhalte, "katplan_suchen", rolle, q="geheimtext")["gesamt"] == (anzahl - 2)
    verknuepfungen = rufe(inhalte, "katplan_verknuepfungen", rolle, element_id="KS-01", **args)
    if rolle == "readonly":
        assert not verknuepfungen["elemente"][0]["relations"]
        assert not verknuepfungen["eingehende_beziehungen"]
        assert "Geheim" not in json.dumps(result)
        for name in ("katplan_element_lesen", "katplan_verknuepfungen"):
            with pytest.raises(ValueError, match="Nicht gefunden"):
                rufe(inhalte, name, element_id=inhalte["geheim_uuid"], **args)
    else:
        geheim = rufe(inhalte, "katplan_element_lesen", rolle, element_id=inhalte["geheim_uuid"], **args)
        assert geheim["attachments"] and geheim["linked_entities"]
        assert geheim["geometry"] is None and any("geometry" in w for w in geheim["warnings"])
        assert verknuepfungen["eingehende_beziehungen"]


@pytest.mark.parametrize("name,args", [
    ("katplan_liste", {}), ("katplan_suchen", {"q": "wasser"}),
    ("katplan_orte_suchen", {"lat": 47, "lng": 9}), ("katplan_lesen", {}), ("katplan_elemente", {}),
    ("katplan_element_lesen", {"element_id": "KS-01"}), ("katplan_verknuepfungen", {}),
    ("katplan_dokument_auszug", {}),
])
def test_fremde_org(inhalte, name, args):
    if name not in ("katplan_liste", "katplan_suchen", "katplan_orte_suchen"):
        args = {"plan_id": inhalte["plan_id"], **args}
    if name == "katplan_dokument_auszug":
        args["dokument_id"] = inhalte["dokument_id"]
    if "plan_id" in args:
        with pytest.raises(ValueError, match="Nicht gefunden"):
            rufe(inhalte, name, "fremd", **args)
    else:
        result = rufe(inhalte, name, "fremd", **args)
        assert not result.get("plaene", result.get("treffer"))


def test_telefonregel(inhalte):
    args = {"plan_id": inhalte["plan_id"]}
    leser = rufe(inhalte, "katplan_verknuepfungen", element_id="KS-01", **args)
    assert len(leser["links"][0]["telefone"]) == 1
    assert "+43664123456" not in json.dumps(leser)
    assert not leser["elemente"][0]["linked_entities"][0]["telefone"]
    voll = rufe(inhalte, "katplan_verknuepfungen", "org_admin", element_id="KS-01", **args)
    assert len(voll["links"][0]["telefone"]) == 2
    assert len(voll["elemente"][0]["linked_entities"][0]["telefone"]) == 2


def test_dokument_text_bereich_download_live_rechte(inhalte, client):
    args = {"plan_id": inhalte["plan_id"], "dokument_id": inhalte["dokument_id"]}
    assert len(rufe(inhalte, "katplan_dokument_auszug", **args)["seiten"]) == 25
    result = rufe(inhalte, "katplan_dokument_auszug", seite=1, download=True, **args)
    assert result["seiten"][0]["text"] == "Quelltext Seite 1"
    assert len(rufe(inhalte, "katplan_dokument_auszug", von=1, bis=20, **args)["seiten"]) == 20
    for bereich in ({"von": 1, "bis": 21}, {"von": 0, "bis": 1}, {"von": 1}, {"seite": 1, "bis": 2}):
        with pytest.raises(ValueError):
            rufe(inhalte, "katplan_dokument_auszug", **bereich, **args)
    with pytest.raises(ValueError, match="Nicht gefunden"):
        rufe(inhalte, "katplan_dokument_auszug", plan_id=inhalte["plan_id"], dokument_id=inhalte["neu_id"])
    token = urlsplit(result["download_url"]).path.rsplit("/", 1)[1]
    with TestingSession() as db:
        set_tenant_context(db, None)
        path, _, mime = lade_download(db, token)
        assert path.name == "seite.pdf" and mime == "application/pdf"
    assert client.get(urlsplit(result["download_url"]).path).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["readonly"]).one()
        for zuordnung in user.user_roles:
            db.delete(zuordnung)
        db.commit()
    assert client.get(urlsplit(result["download_url"]).path).status_code == 403
    with TestingSession() as db:
        set_tenant_context(db, None)
        with pytest.raises(MCPDownloadFehler) as exc:
            lade_download(db, token)
        assert exc.value.status_code == 403
    with pytest.raises(PermissionError):
        rufe(inhalte, "katplan_liste")


def test_umkreis_sortierung_und_audit(inhalte):
    result = rufe(inhalte, "katplan_orte_suchen", lat=47, lng=9, radius_m=4000)
    assert len(result["treffer"]) == 2
    assert result["treffer"][0]["distanz_m"] < result["treffer"][1]["distanz_m"]
    assert result["treffer"][0]["element_id"] == inhalte["uuid"]
    assert all(t["geometry"]["type"] == "Point" for t in result["treffer"])
    assert len(rufe(inhalte, "katplan_orte_suchen", lat=47, lng=9)["treffer"]) == 1
    with pytest.raises(ValueError):
        rufe(inhalte, "katplan_orte_suchen", lat=float("nan"), lng=9)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(AuditLog).filter_by(org_id=inhalte["org_id"], action="mcp.katplan_orte_suchen").count() == 2


def test_historische_versionen_und_fremde_version(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        e = db.get(KatplanElement, inhalte["element_id"])
        e.titel = "Neue Freigabe"
        plan.status = "in_ueberarbeitung"
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_freigeber"]).one()
        neu = freigeben(db, plan, user)
        anderer = KatplanPlan(org_id=plan.org_id, titel="Anderer Plan", gefahrenarten_json='["sturm"]')
        db.add(anderer)
        db.flush()
        db.add(KatplanElement(org_id=plan.org_id, plan_id=anderer.id, typ="info", titel="Fremdes Element"))
        fremde_version = freigeben(db, anderer, user)
        db.commit()
        neu_id, fremd_id = neu.id, fremde_version.id
    args = {"plan_id": inhalte["plan_id"], "element_id": "KS-01"}
    assert rufe(inhalte, "katplan_element_lesen", **args)["plan_version_id"] == neu_id
    alt = rufe(inhalte, "katplan_element_lesen", version_id=inhalte["version_id"], **args)
    assert alt["title"] == "Wasser Brücke"
    assert alt["source_references"][0]["titel"] == "Freigegebene Quelle"
    for name in ("katplan_lesen", "katplan_element_lesen"):
        extra = {"element_id": "KS-01"} if name.endswith("element_lesen") else {}
        with pytest.raises(ValueError, match="Nicht gefunden"):
            rufe(inhalte, name, plan_id=inhalte["plan_id"], version_id=fremd_id, **extra)


def test_kapitel_phasen_filter_und_begrenzung(inhalte):
    from app.models.katplan import KatplanAbschnitt

    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        kap = KatplanAbschnitt(org_id=plan.org_id, plan_id=plan.id, titel="Hauptkapitel", sort=1)
        db.add(kap)
        db.flush()
        unter = KatplanAbschnitt(org_id=plan.org_id, plan_id=plan.id, parent_id=kap.id, titel="Unterkapitel", sort=2)
        db.add(unter)
        db.flush()
        db.get(KatplanElement, inhalte["element_id"]).abschnitt_id = unter.id
        for nr in range(201):
            db.add(KatplanElement(org_id=plan.org_id, plan_id=plan.id, typ="info", titel=f"Info {nr}",
                                  kennung=f"I-{nr}", sort=nr+10, suchtext=f"info {nr}"))
        plan.status = "in_ueberarbeitung"
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_freigeber"]).one()
        freigeben(db, plan, user)
        db.commit()
    args = {"plan_id": inhalte["plan_id"]}
    result = rufe(inhalte, "katplan_lesen", mit_elementen=True, **args)
    assert len(result["elemente"]) == 200 and any("200" in w for w in result["warnings"])
    assert result["kapitelbaum"][0]["kinder"][0]["elementkennungen"] == ["KS-01"]
    e = rufe(inhalte, "katplan_element_lesen", element_id="KS-01", **args)
    assert e["chapter"] == "Hauptkapitel > Unterkapitel" and e["phases"] == ["Vorwarnung"]
    assert len(rufe(inhalte, "katplan_elemente", kapitel="Unterkapitel", **args)["elemente"]) == 1
    assert len(rufe(inhalte, "katplan_elemente", phase="Vorwarnung", **args)["elemente"]) == 1
    assert len(rufe(inhalte, "katplan_elemente", typ="kontrollstelle", **args)["elemente"]) == 2
    assert len(rufe(inhalte, "katplan_elemente", limit=1, **args)["elemente"]) == 1
    with pytest.raises(ValueError):
        rufe(inhalte, "katplan_suchen", q="Info", limit=51)
    suche = rufe(inhalte, "katplan_suchen", q="Info", limit=50)
    assert len(suche["treffer"]) == 50 and suche["warnings"]


def test_suche_quellseiten_und_download_referenz_entzogen(inhalte, client):
    result = rufe(inhalte, "katplan_suchen", q="Quelltext")
    assert result["gesamt"] == 25
    assert all(t["dokument_id"] == inhalte["dokument_id"] and t["seiten_nr"] for t in result["treffer"])
    result = rufe(inhalte, "katplan_dokument_auszug", plan_id=inhalte["plan_id"],
                  dokument_id=inhalte["dokument_id"], download=True)
    path = urlsplit(result["download_url"]).path
    assert client.get(path).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_freigeber"]).one()
        e = db.get(KatplanElement, inhalte["element_id"])
        e.quelle_dokument_id, e.quelle_seiten = None, None
        plan.status = "in_ueberarbeitung"
        freigeben(db, plan, user)
        db.commit()
    assert client.get(path).status_code == 404
    assert rufe(inhalte, "katplan_suchen", q="Quelltext")["gesamt"] == 0


def test_standard_und_expliziter_arbeitsstand_in_liste(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        plan.titel = "Arbeitsstandtitel"
        db.commit()
    assert rufe(inhalte, "katplan_liste", "katplan_verwalter")["plaene"][0]["titel"] == inhalte["titel"]
    assert rufe(inhalte, "katplan_liste", "katplan_verwalter", fassung="arbeitsstand")["plaene"][0]["titel"] == "Arbeitsstandtitel"
    with pytest.raises(PermissionError):
        rufe(inhalte, "katplan_liste", fassung="arbeitsstand")
