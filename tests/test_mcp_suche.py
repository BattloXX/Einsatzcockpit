"""S6: MCP-Suche, Live-Rechte, Datensparsamkeit und Paritaet zur Oberflaeche."""

import inspect
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from app.config import settings
from app.core.permissions import ROLES
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.registry import TOOLS
from app.models.incident import Incident
from app.models.kontakt import EinsatzKontakt, Kontakt, KontaktTelefon
from app.models.major_incident import LageJournalEntry, MajorIncident
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt
from app.models.user import Role, User, UserRole
from app.routers.ui_suche import _leser
from app.services.suche.indexer import indexiere
from app.services.suche.vertrag import SuchErgebnis, Treffer
from tests.test_admin_rollenrechte import _login
from tests.test_mcp_fundament import _enable_mcp, _mcp, _oauth_tokens
from tests.test_mcp_objekte import _rpc, _rufe


@pytest.fixture
def umgebung(client, monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "SEARCH_MCP_ENABLED", True)
    user = _enable_mcp(username=uuid4().hex, slug=uuid4().hex)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        org = db.query(OrgSettings).filter_by(org_id=user["org_id"]).one()
        org.suche_aktiv = True
        org.objekt_module_enabled = True
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").first()
        if flag is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            flag.value = "true"
        fremd = FireDept(slug=uuid4().hex, name="Fremde Organisation")
        db.add(fremd)
        db.flush()
        db.add(OrgSettings(org_id=fremd.id, suche_aktiv=True, objekt_module_enabled=True))
        objekt = Objekt(org_id=user["org_id"], nummer=987, name="Brand Fabrik +43 664 1234567",
                        status="freigegeben", informationen="Brand Sprinkler +43 664 1234567")
        anderes_objekt = Objekt(org_id=fremd.id, nummer=988, name="Brand Fremdbetrieb", status="freigegeben")
        lage = MajorIncident(org_id=user["org_id"], name="Brand Lage")
        andere_lage = MajorIncident(org_id=fremd.id, name="Brand Fremdlage")
        db.add_all([objekt, anderes_objekt, lage, andere_lage])
        db.flush()
        db.add(LageJournalEntry(major_incident_id=lage.id, text="Brand Wasserversorgung +43 664 1234567",
                               author_name="Max Muster", ts=datetime(2026, 10, 20, 10, tzinfo=UTC)))
        indexiere(db, user["org_id"], "objekt", str(objekt.id))
        indexiere(db, fremd.id, "objekt", str(anderes_objekt.id))
        db.commit()
        info = {**user, "objekt": objekt.id, "fremdes_objekt": anderes_objekt.id,
                "lage": lage.id, "fremde_lage": andere_lage.id}
    info["token"] = _oauth_tokens(client, user)["access_token"]
    return info


def _rolle(info, rolle):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        for zuordnung in db.query(UserRole).filter_by(user_id=info["id"]).all():
            db.delete(zuordnung)
        if rolle:
            role = db.query(Role).filter_by(code=rolle).one()
            db.add(UserRole(user_id=info["id"], role_id=role.id))
        db.commit()


def _gelistet(client, info):
    response = _mcp(client, info["token"], "tools/list", {}, 2)
    assert response.status_code == 200, response.text
    return "ecp_suchen" in {tool["name"] for tool in _rpc(response)["result"]["tools"]}


def _suche(client, info, **daten):
    return _rufe(client, info["token"], "ecp_suchen", **{"q": "Brand", **daten})


def test_rollenmatrix_entspricht_ui_und_prueft_live(client, umgebung):
    for rolle in (*ROLES, None):
        _rolle(umgebung, rolle)
        erlaubt = rolle is not None and ROLES[rolle] >= ROLES["readonly"]
        assert _gelistet(client, umgebung) == erlaubt, rolle
        assert ("__fehler__" not in _suche(client, umgebung)) == erlaubt, rolle
        # Die Leserollen direkt vergleichen; Systemadmins haben zusaetzlich einen Org-Umschalter im UI.
        with SessionLocal() as db:
            set_tenant_context(db, None)
            user = db.get(User, umgebung["id"])
            request = Request({"type": "http", "state": {"user": user}})
            if erlaubt:
                assert _leser(request) is user
            else:
                with pytest.raises(HTTPException) as exc:
                    _leser(request)
                assert exc.value.status_code == 403


@pytest.mark.parametrize("flag", ["suche_aktiv", "SEARCH_MCP_ENABLED", "SEARCH_ENABLED"])
def test_modulcheck_live(client, umgebung, monkeypatch, flag):
    assert _gelistet(client, umgebung)
    if flag == "suche_aktiv":
        with SessionLocal() as db:
            set_tenant_context(db, None)
            db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().suche_aktiv = False
            db.commit()
    else:
        monkeypatch.setattr(settings, flag, False)
    assert not _gelistet(client, umgebung)
    assert "__fehler__" in _suche(client, umgebung)


def test_tenant_und_fremder_gsl_kontext(client, umgebung):
    result = _suche(client, umgebung, typen=["objekt"])
    assert [(t["quelle"], t["quell_id"]) for t in result["treffer"]] == [("objekt", str(umgebung["objekt"]))]
    assert "Fremdbetrieb" not in json.dumps(result)
    fehler = _suche(client, umgebung, kontext="gsl", kontext_id=umgebung["fremde_lage"])
    assert "Kontext nicht gefunden." in fehler["__fehler__"]


def test_ausgabefelder_datensparsamkeit_und_utc(client, umgebung):
    result = _suche(client, umgebung, kontext="gsl", kontext_id=umgebung["lage"], ganze_org=True)
    assert set(result) == {"treffer", "teilweise", "index_stand", "hinweise", "modi"}
    assert {t["quelle"] for t in result["treffer"]} == {"objekt", "gsl_meldung"}
    assert "Max Muster" not in json.dumps(result, ensure_ascii=False)
    felder = {"quelle", "quell_id", "titel", "untertitel", "auszug", "fundstelle", "status",
              "ist_aktuell", "grund", "distanz_m", "stand", "url"}
    for t in result["treffer"]:
        assert felder <= set(t) <= felder | {"details_werkzeug", "details_parameter"}
        assert len(t["auszug"]) <= 300
        assert not re.search(r"(?:\+43|0664)[\d\s]{6,}", t["titel"] + t["auszug"])
        if t["stand"]:
            assert t["stand"].endswith("Z")
        if "details_werkzeug" in t:
            assert set(t["details_parameter"]) <= set(inspect.signature(TOOLS[t["details_werkzeug"]].handler).parameters)
    assert result["index_stand"].endswith("Z")
    meldung = next(t for t in result["treffer"] if t["quelle"] == "gsl_meldung")
    assert meldung["stand"] == "2026-10-20T10:00:00Z"


@pytest.mark.parametrize("daten,meldung", [({"limit": 51}, "limit: Input should be less than or equal to 50"),
                                          ({"q": " "}, "Suchtext muss 1 bis 500 Zeichen enthalten"),
                                          ({"kontext": "gsl"}, "Kontext-ID fehlt"),
                                          ({"lat": 48.2}, "Koordinaten und Radius nur gemeinsam")])
def test_lesbare_validierung(client, umgebung, daten, meldung):
    result = _suche(client, umgebung, **daten)
    assert meldung in result["__fehler__"]


@pytest.mark.parametrize("kontext", ["global", "gsl"])
def test_paritaet_mit_api(client, umgebung, kontext):
    assert _login(client, umgebung["username"]).status_code == 302
    daten = {"q": "Brand", "kontext": kontext, "ganze_org": True}
    if kontext == "gsl":
        daten["kontext_id"] = umgebung["lage"]
    response = client.post("/api/suche", json=daten, headers={"X-CSRF-Token": client.cookies.get("ec_csrf")})
    assert response.status_code == 200, response.text
    result = _suche(client, umgebung, **daten)
    api_ids = [(t["quelle"], t["quell_id"]) for t in response.json()["treffer"]]
    assert api_ids
    assert [(t["quelle"], t["quell_id"]) for t in result["treffer"]] == api_ids


def test_objekt_details_nur_mit_zielrechten_und_absoluter_url(client, umgebung, monkeypatch):
    treffer = _suche(client, umgebung, typen=["objekt"])["treffer"][0]
    assert treffer["url"] == settings.effective_public_base_url.rstrip("/") + f'/objekte/{umgebung["objekt"]}'
    assert "details_werkzeug" not in treffer
    assert "details_parameter" not in treffer
    _rolle(umgebung, "objekt_verwalter")
    treffer = _suche(client, umgebung, typen=["objekt"])["treffer"][0]
    assert treffer["details_werkzeug"] == "objekt_lesen"
    assert treffer["details_parameter"] == {"objekt_id": umgebung["objekt"]}
    definition = TOOLS["objekt_lesen"]
    monkeypatch.setitem(TOOLS, "objekt_lesen", replace(definition, module_check=lambda org_id, db: False))
    treffer = _suche(client, umgebung, typen=["objekt"])["treffer"][0]
    assert "details_werkzeug" not in treffer
    monkeypatch.delitem(TOOLS, "objekt_lesen")
    treffer = _suche(client, umgebung, typen=["objekt"])["treffer"][0]
    assert "details_werkzeug" not in treffer


def test_serialisierung_teilweise_zeitzonen_und_auszug(client, umgebung, monkeypatch):
    zeit = datetime(2026, 10, 20, 12, tzinfo=timezone(timedelta(hours=2)))
    t = Treffer(quelle="objekt", quell_id=str(umgebung["objekt"]), titel="Brand", url="", stand=zeit)
    t.auszug = "a" * 500
    monkeypatch.setattr("app.mcp.tools.suche.suche", lambda *_, **__: SuchErgebnis(
        treffer=[t], teilweise=True, hinweise=["Volltextsuche nicht verfügbar"], modi=["live"], index_stand=zeit,
    ))
    result = _suche(client, umgebung)
    assert result["teilweise"] is True
    assert result["hinweise"] == ["Volltextsuche nicht verfügbar"]
    assert result["modi"] == ["live"]
    assert result["index_stand"] == result["treffer"][0]["stand"] == "2026-10-20T10:00:00Z"
    assert len(result["treffer"][0]["auszug"]) == 300
    assert result["treffer"][0]["url"] == ""


@pytest.fixture
def kontakte(umgebung):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="kontakte_module_enabled").first()
        if flag is None:
            db.add(SystemSettings(key="kontakte_module_enabled", value="true"))
        else:
            flag.value = "true"
        db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().kontakte_module_enabled = True
        einsatz = Incident(primary_org_id=umgebung["org_id"], alarm_type_code="T1")
        frei = Kontakt(org_id=umgebung["org_id"], anzeigename="Brand Bereitschaft", funktion="Gasnetz",
                       telefone=[KontaktTelefon(org_id=umgebung["org_id"], nummer="06641234567",
                                                verwendung="dienst")])
        verzeichnis = Kontakt(org_id=umgebung["org_id"], anzeigename="Brand Beauftragter")
        db.add_all([einsatz, frei, verzeichnis])
        db.flush()
        db.add(EinsatzKontakt(org_id=umgebung["org_id"], incident_id=einsatz.id, kontakt_id=frei.id))
        db.commit()
        return {"einsatz": einsatz.id, "frei": frei.id, "verzeichnis": verzeichnis.id}


def _kontakttreffer(result):
    assert "__fehler__" not in result, result
    return {t["quell_id"]: t for t in result["treffer"] if t["quelle"] == "kontakt"}


def test_kontakte_mit_verzeichnisrecht_ohne_telefonnummern(client, umgebung, kontakte):
    _rolle(umgebung, "recorder")
    result = _suche(client, umgebung, typen=["kontakt"])
    treffer = _kontakttreffer(result)
    assert set(treffer) == {str(kontakte["frei"]), str(kontakte["verzeichnis"])}
    for t in treffer.values():
        assert "telefone" not in t
        assert t["details_werkzeug"] == "kontakt_lesen"
        assert t["details_parameter"] == {"kontakt_id": int(t["quell_id"])}
    assert "1234567" not in json.dumps(result)


def test_kontakte_ohne_verzeichnisrecht(client, umgebung, kontakte):
    _rolle(umgebung, "breathing_supervisor")
    assert not _kontakttreffer(_suche(client, umgebung, typen=["kontakt"]))
    treffer = _kontakttreffer(_suche(client, umgebung, typen=["kontakt"], kontext="einsatz",
                                     kontext_id=kontakte["einsatz"]))
    assert set(treffer) == {str(kontakte["frei"])}
    assert "details_werkzeug" not in treffer[str(kontakte["frei"])]
    _rolle(umgebung, "readonly")
    assert not _kontakttreffer(_suche(client, umgebung, q="Bereitschaft", typen=["kontakt"]))


def test_kontakte_modul_aus(client, umgebung, kontakte):
    _rolle(umgebung, "recorder")
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().kontakte_module_enabled = False
        db.commit()
    assert not _kontakttreffer(_suche(client, umgebung, typen=["kontakt"]))
