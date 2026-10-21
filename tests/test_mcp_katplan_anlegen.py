"""MCP-Plananlage: Vorlagen, Validierung, Rechte und vollstaendiger Rollback."""

from datetime import date

import pytest

from app.core.permissions import KATPLAN_VERWALTER_ROLES
from app.core.tenant import set_tenant_context
from app.mcp.registry import TOOLS
from app.models.katplan import KatplanAbschnitt, KatplanAenderung, KatplanPhase, KatplanPlan
from app.models.kontakt import Kontakt
from app.models.master import OrgSettings
from app.models.user import AuditLog, User
from app.services.katplan_typen import PLANARTEN
from tests.conftest import TestingSession
from tests.test_katplan_k1b import umgebung  # noqa: F401
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_fundament import _mcp, _oauth_tokens
from tests.test_mcp_katplan import inhalte, rufe  # noqa: F401
from tests.test_mcp_strassensperren import _rpc, _rufe


def anlegen(info, **felder):
    return rufe(info, "katplan_anlegen", "katplan_verwalter",
                felder={"titel": "Neuer MCP-Plan", "planart": "alarm_einsatzplan", **felder})


def bestand(info):
    with TestingSession() as db:
        set_tenant_context(db, None)
        return {model.__name__: [row.id for row in db.query(model).filter_by(org_id=info["org_id"])
                                .order_by(model.id)]
                for model in (KatplanPlan, KatplanAbschnitt, KatplanPhase, KatplanAenderung)}


@pytest.mark.parametrize("planart", list(PLANARTEN))
def test_anlage_vorlage_protokoll_und_lesen(inhalte, planart):
    gefahrenarten = ["hochwasser"] if planart == "katastrophenplan" else []
    result = anlegen(inhalte, planart=planart, gefahrenarten=gefahrenarten, beschreibung="• Prüfen")
    assert result["gespeichert"] and result["status"] == "entwurf"
    assert result["url"] == f'/plaene/{result["plan_id"]}'
    vorlage = PLANARTEN[planart]
    assert tuple(k["titel"] for k in result["kapitel"]) == vorlage.kapitel
    assert result["phasen"] == [name for name, _ in vorlage.phasen]
    gelesen = rufe(inhalte, "katplan_lesen", "katplan_verwalter",
                   plan_id=result["plan_id"], fassung="arbeitsstand")
    assert gelesen["kopf"] == result["plan"]
    assert gelesen["kopf"]["titel"] == "Neuer MCP-Plan"
    assert gelesen["kopf"]["planart"] == planart
    assert result["plan_version"] == gelesen["plan_version"]
    assert result["plan"]["pruefintervall_monate"] == vorlage.pruefintervall_monate
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, result["plan_id"])
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        assert plan.org_id == inhalte["org_id"] and plan.status == "entwurf"
        assert plan.erstellt_von_id == plan.aktualisiert_von_id == plan.pruef_verantwortlich_user_id == user.id
        assert plan.pruefintervall_monate is None
        assert plan.beschreibung_html == "<ul><li>Prüfen</li></ul>"
        kapitel = db.query(KatplanAbschnitt).filter_by(plan_id=plan.id).order_by(KatplanAbschnitt.sort).all()
        phasen = db.query(KatplanPhase).filter_by(plan_id=plan.id).order_by(KatplanPhase.sort).all()
        assert [k.id for k in kapitel] == [k["id"] for k in result["kapitel"]]
        assert tuple((p.name, p.farbe) for p in phasen) == vorlage.phasen
        assert [p.sort for p in phasen] == list(range(10, len(phasen) * 10 + 1, 10))
        logs = db.query(KatplanAenderung).filter_by(plan_id=plan.id).all()
        assert len(logs) == 1 + len(kapitel) + len(phasen)
        assert all(log.quelle == "mcp" and log.aktion == "angelegt" for log in logs)
        assert any(log.entitaet == "plan" for log in logs)
        audit = db.query(AuditLog).filter_by(action="mcp.katplan_anlegen", entity_id=plan.id).one()
        assert audit.org_id == plan.org_id and audit.user_id == user.id


def test_optionale_felder_und_massnahmenplan_gefahrenarten(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        person = db.query(User).filter_by(username=inhalte["users"]["katplan_freigeber"]).one()
        kontakt = db.query(Kontakt).filter_by(org_id=inhalte["org_id"], einsatzrelevant=True).one()
        person_id, kontakt_id = person.id, kontakt.id
    result = anlegen(inhalte, planart="massnahmenplan", gefahrenarten=["hochwasser", "hochwasser"],
                     geltungsbereich=" Gemeinde ", verantwortliche_stelle=" Bauhof ",
                     verantwortlich_kontakt_id=kontakt_id, pruef_verantwortlich_user_id=person_id,
                     wiedervorlage_am="2027-01-02", pruefintervall_monate=6)
    assert result["plan"]["gefahrenarten"] == ["hochwasser"]
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, result["plan_id"])
        assert plan.pruef_verantwortlich_user_id == person_id
        assert plan.verantwortlich_kontakt_id == kontakt_id
        assert plan.geltungsbereich == "Gemeinde" and plan.verantwortliche_stelle == "Bauhof"
        assert plan.wiedervorlage_am == date(2027, 1, 2) and plan.pruefintervall_monate == 6


@pytest.mark.parametrize("felder,fehler", [
    ({"planart": "katastrophenplan"}, "mindestens eine gültige Gefahrenart"),
    ({"planart": "massnahmenplan", "gefahrenarten": ["falsch"]}, "gültige Gefahrenart"),
    ({"fremd": 1, "status": "freigegeben"}, "Nicht erlaubte Felder: fremd, status"),
    ({"titel": 123}, "titel muss Text sein"),
    ({"beschreibung": True}, "beschreibung muss Text sein"),
    ({"gefahrenarten": "hochwasser"}, "gültige Gefahrenart"),
    ({"gefahrenarten": [1]}, "gültige Gefahrenart"),
    ({"planart": "falsch"}, "gültige Planart"),
    ({"verantwortlich_kontakt_id": True}, "Kontakt-ID"),
    ({"pruef_verantwortlich_user_id": True}, "Ganzzahl"),
    ({"pruefintervall_monate": "12"}, "Ganzzahl"),
    ({"pruefintervall_monate": 0}, "zwischen 1 und 60"),
    ({"pruefintervall_monate": 61}, "zwischen 1 und 60"),
    ({"wiedervorlage_am": "falsch"}, "Wiedervorlagedatum"),
])
def test_validierung_ohne_anlage(inhalte, felder, fehler):
    vorher = bestand(inhalte)
    with pytest.raises(ValueError, match=fehler):
        anlegen(inhalte, **felder)
    assert bestand(inhalte) == vorher


@pytest.mark.parametrize("felder", [[], {}, {"titel": "Ohne Planart"}, {"planart": "alarm_einsatzplan"}])
def test_objekt_und_pflichtfelder(inhalte, felder):
    vorher = bestand(inhalte)
    with pytest.raises(ValueError):
        rufe(inhalte, "katplan_anlegen", "katplan_verwalter", felder=felder)
    assert bestand(inhalte) == vorher


def test_trockenlauf_ohne_plan_kapitel_phasen_oder_protokoll(inhalte):
    vorher = bestand(inhalte)
    result = rufe(inhalte, "katplan_anlegen", "katplan_verwalter",
                  felder={"titel": "Probe", "planart": "alarm_einsatzplan"}, pruefen=True)
    assert result["gespeichert"] is False
    assert result["plan_id"] and result["kapitel"] and result["phasen"]
    assert bestand(inhalte) == vorher


@pytest.mark.parametrize("rolle", ["readonly", "incident_leader"])
def test_leser_abgewiesen(inhalte, rolle):
    vorher = bestand(inhalte)
    with pytest.raises(PermissionError):
        rufe(inhalte, "katplan_anlegen", rolle, felder={"titel": "Plan", "planart": "alarm_einsatzplan"})
    assert bestand(inhalte) == vorher


@pytest.mark.parametrize("feld", ["pruef_verantwortlich_user_id", "verantwortlich_kontakt_id"])
def test_fremde_organisation_abgewiesen(inhalte, feld):
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["fremd"]).one()
        wert = user.id
        if feld == "verantwortlich_kontakt_id":
            kontakt = Kontakt(org_id=user.org_id, anzeigename="Fremder Kontakt")
            db.add(kontakt)
            db.commit()
            wert = kontakt.id
    vorher = bestand(inhalte)
    fehler = "Benutzer nicht verfügbar" if feld.startswith("pruef") else "Kontakt nicht verfügbar"
    with pytest.raises(ValueError, match=fehler):
        anlegen(inhalte, **{feld: wert})
    assert bestand(inhalte) == vorher


def test_http_registry_und_modulpruefung(client, inhalte):
    assert set(TOOLS["katplan_anlegen"].required_roles) == KATPLAN_VERWALTER_ROLES
    assert TOOLS["katplan_anlegen"].module_check is not None
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        ident = {"id": user.id, "org_id": user.org_id, "username": user.username}
    token = _oauth_tokens(client, ident)["access_token"]
    tools = _rpc(_mcp(client, token, "tools/list", {}, 12))["result"]["tools"]
    tool = next(t for t in tools if t["name"] == "katplan_anlegen")
    assert "felder" in tool["inputSchema"]["required"]
    result = _rufe(client, token, "katplan_anlegen", felder={"titel": "HTTP-Plan", "planart": "alarm_einsatzplan"})
    assert result["gespeichert"] and result["plan"]["titel"] == "HTTP-Plan"
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=inhalte["org_id"]).one().katplan_modul_aktiv = False
        db.commit()
    with pytest.raises(PermissionError):
        anlegen(inhalte)
