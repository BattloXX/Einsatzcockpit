"""M2/M4: Verknuepfungen, Telefonregel und gemeinsame Plan-Stammdaten."""

from datetime import date

import pytest

from app.core.permissions import KATPLAN_VERWALTER_ROLES
from app.core.tenant import set_tenant_context
from app.mcp.registry import TOOLS
from app.models.katplan import KatplanAenderung, KatplanElement, KatplanLink, KatplanPlan, KatplanRelation
from app.models.kontakt import Kontakt
from app.models.master import OrgSettings, SystemSettings
from app.models.user import User
from app.services.katplan_qs_service import element_hinweise
from tests.conftest import TestingSession
from tests.test_katplan_k1b import umgebung  # noqa: F401
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_fundament import _mcp, _oauth_tokens
from tests.test_mcp_katplan import inhalte, rufe  # noqa: F401, F811
from tests.test_mcp_katplan_schreiben import schreiben
from tests.test_mcp_strassensperren import _rpc, _rufe

NAMEN = ("katplan_verknuepfung_setzen", "katplan_verknuepfung_entfernen", "katplan_beziehung_setzen",
         "katplan_beziehung_entfernen", "katplan_stammdaten_aendern")


@pytest.fixture(autouse=True)
def kontaktmodul(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(OrgSettings).filter_by(org_id=inhalte["org_id"]).one().kontakte_module_enabled = True
        flag = db.query(SystemSettings).filter_by(key="kontakte_module_enabled").one_or_none()
        vorher = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="kontakte_module_enabled")
            db.add(flag)
        flag.value = "true"
        db.commit()
    yield
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="kontakte_module_enabled").one()
        if vorher is None:
            db.delete(flag)
        else:
            flag.value = vorher
        db.commit()


def kontakt_id(info, privat=False):
    with TestingSession() as db:
        set_tenant_context(db, None)
        return db.query(Kontakt).filter_by(org_id=info["org_id"], einsatzrelevant=not privat).one().id


def kopf(info):
    return rufe(info, "katplan_lesen", "katplan_verwalter", plan_id=info["plan_id"], fassung="arbeitsstand")


def zustand(info):
    with TestingSession() as db:
        set_tenant_context(db, None)
        return [[tuple(getattr(row, c.name) for c in model.__table__.columns)
                 for row in db.query(model).filter_by(org_id=info["org_id"]).order_by(model.id)]
                for model in (KatplanPlan, KatplanElement, KatplanLink, KatplanRelation, KatplanAenderung)]


@pytest.mark.parametrize("element_id", [None, "KS-01"])
def test_link_idempotent_hinweis_telefon_und_entfernen(inhalte, element_id):
    args = dict(ziel_typ="kontakt", ziel_id=kontakt_id(inhalte), element_id=element_id)
    neu = schreiben(inhalte, NAMEN[0], **args)
    assert neu["gespeichert"] and not neu["bereits_vorhanden"]
    assert neu["verknuepfung"]["telefone"] == [{"nummer": "+43557112345", "label": None}]
    if element_id:
        assert neu["element"]["version"] == neu["version"]
    vorher = zustand(inhalte)
    wieder = schreiben(inhalte, NAMEN[0], **args)
    assert wieder["bereits_vorhanden"] and wieder["geaendert"] == []
    assert zustand(inhalte) == vorher
    angepasst = schreiben(inhalte, NAMEN[0], **args, hinweis="Zuerst verständigen")
    assert angepasst["geaendert"] == ["hinweis"]
    assert angepasst["verknuepfung"]["hinweis"] == "Zuerst verständigen"
    assert schreiben(inhalte, NAMEN[0], **args)["verknuepfung"]["hinweis"] == "Zuerst verständigen"
    assert schreiben(inhalte, NAMEN[0], **args, hinweis="")["geaendert"] == ["hinweis"]
    assert schreiben(inhalte, NAMEN[1], **args, rolle="info")["entfernt"] == 1
    with pytest.raises(ValueError, match="Verknuepfung nicht gefunden"):
        schreiben(inhalte, NAMEN[1], **args, rolle="info")
    with TestingSession() as db:
        set_tenant_context(db, None)
        logs = db.query(KatplanAenderung).filter_by(plan_id=inhalte["plan_id"], entitaet="link").all()
        assert logs and all(log.quelle == "mcp" for log in logs)


def test_link_privater_kontakt_und_alle_rollen(inhalte):
    args = dict(ziel_typ="kontakt", ziel_id=kontakt_id(inhalte, privat=True), element_id="KS-01")
    assert schreiben(inhalte, NAMEN[0], **args)["verknuepfung"]["telefone"] == []
    assert schreiben(inhalte, NAMEN[1], **args)["entfernt"] == 2


def test_beziehung_kennungen_idempotent_und_entfernen(inhalte):
    neu = schreiben(inhalte, "katplan_element_entwurf_anlegen", typ="info", titel="Zielelement")
    args = dict(element_id="KS-01", zu_element_id=neu["kennung"], art="folgt_auf")
    result = schreiben(inhalte, NAMEN[2], **args)
    assert result["geaendert"] == ["beziehung"] and result["gespeichert"]
    vorher = zustand(inhalte)
    assert schreiben(inhalte, NAMEN[2], **args)["geaendert"] == []
    assert zustand(inhalte) == vorher
    assert schreiben(inhalte, NAMEN[2], **args, hinweis="Nach Abschluss")["geaendert"] == ["hinweis"]
    assert schreiben(inhalte, NAMEN[3], **args)["entfernt"] == 1
    with pytest.raises(ValueError, match="Beziehung nicht gefunden"):
        schreiben(inhalte, NAMEN[3], **args)
    with TestingSession() as db:
        set_tenant_context(db, None)
        logs = db.query(KatplanAenderung).filter_by(plan_id=inhalte["plan_id"], entitaet="relation").all()
        assert logs and all(log.quelle == "mcp" for log in logs)


def test_fremde_kontakte_abgewiesen(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["fremd"]).one()
        kontakt = Kontakt(org_id=user.org_id, anzeigename="Fremd")
        db.add(kontakt)
        db.commit()
        kid = kontakt.id
    with pytest.raises(ValueError, match="Ziel nicht verfügbar"):
        schreiben(inhalte, NAMEN[0], ziel_typ="kontakt", ziel_id=kid)
    with pytest.raises(ValueError, match="Kontakt nicht verfügbar"):
        schreiben(inhalte, NAMEN[4], version=kopf(inhalte)["plan_version"], felder={"verantwortlich_kontakt_id": kid})


def test_stammdaten_teilaenderung_leeren_konflikt_und_protokoll(inhalte):
    vorher = kopf(inhalte)
    result = schreiben(inhalte, NAMEN[4], version=vorher["plan_version"], felder={
        "titel": "Neuer Plan", "beschreibung": "• Prüfen", "wiedervorlage_am": "2027-01-01",
        "verantwortlich_kontakt_id": kontakt_id(inhalte), "geltungsbereich": "Gemeinde"})
    assert result["plan_version"] == vorher["plan_version"] + 1
    assert result["plan"]["gefahrenarten"] == vorher["kopf"]["gefahrenarten"]
    assert result["plan"]["body"] == "- Prüfen"
    assert result["plan"] == kopf(inhalte)["kopf"]
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        assert plan.status == "in_ueberarbeitung"
        assert plan.beschreibung_html == "<ul><li>Prüfen</li></ul>"
        assert all(log.quelle == "mcp" for log in db.query(KatplanAenderung).filter_by(plan_id=plan.id, aktion="geaendert"))
        plan.wiedervorlage_erinnert_am = date(2026, 1, 1)
        db.commit()
    with pytest.raises(ValueError, match=f'hat version {result["plan_version"]}'):
        schreiben(inhalte, NAMEN[4], version=vorher["plan_version"], felder={"titel": "Alt"})
    result = schreiben(inhalte, NAMEN[4], version=result["plan_version"], felder={
        "beschreibung": None, "wiedervorlage_am": "", "verantwortlich_kontakt_id": None,
        "geltungsbereich": "", "verantwortliche_stelle": None, "titel": "Neuer Plan"}, streng=True)
    assert result["unveraendert"] == ["verantwortliche_stelle", "titel"]
    assert result["verworfen"] == []
    assert all(result["plan"][k] is None for k in ("body", "wiedervorlage_am", "verantwortlich_kontakt_id", "geltungsbereich"))
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(KatplanPlan, inhalte["plan_id"]).wiedervorlage_erinnert_am is None


@pytest.mark.parametrize("felder", [{"status": "freigegeben"}, {"titel": None}, {"gefahrenarten": []},
                                    {"gefahrenarten": ["falsch"]}, {"wiedervorlage_am": "falsch"},
                                    {"titel": 123}, {"verantwortlich_kontakt_id": True}])
def test_stammdaten_validierung(inhalte, felder):
    vorher = zustand(inhalte)
    with pytest.raises(ValueError):
        schreiben(inhalte, NAMEN[4], version=kopf(inhalte)["plan_version"], felder=felder)
    assert zustand(inhalte) == vorher


def argumente(info):
    return [dict(ziel_typ="kontakt", ziel_id=kontakt_id(info), element_id="KS-01"),
            dict(ziel_typ="kontakt", ziel_id=kontakt_id(info)),
            dict(element_id="KS-01", zu_element_id=info["geheim_uuid"], art="folgt_auf"),
            dict(element_id="KS-01", zu_element_id=info["geheim_uuid"]),
            dict(version=kopf(info)["plan_version"], felder={"titel": "Probe"})]


@pytest.mark.parametrize("index", [0, 2, 4])
def test_trockenlauf_ohne_rueckstand(inhalte, index):
    vorher = zustand(inhalte)
    result = schreiben(inhalte, NAMEN[index], **argumente(inhalte)[index], pruefen=True)
    assert result["gespeichert"] is False and result["geaendert"]
    assert zustand(inhalte) == vorher


@pytest.mark.parametrize("index", range(5))
def test_leser_abgewiesen(inhalte, index):
    with pytest.raises(PermissionError):
        rufe(inhalte, NAMEN[index], plan_id=inhalte["plan_id"], **argumente(inhalte)[index])


def test_http_registry_wrapper(client, inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        ident = {"id": user.id, "org_id": user.org_id, "username": user.username}
    token = _oauth_tokens(client, ident)["access_token"]
    tools = _rpc(_mcp(client, token, "tools/list", {}, 12))["result"]["tools"]
    assert set(NAMEN) <= {t["name"] for t in tools}
    for name in NAMEN:
        assert set(TOOLS[name].required_roles) == KATPLAN_VERWALTER_ROLES
    for index in (0, 1, 2, 3, 4):
        assert "__fehler__" not in _rufe(client, token, NAMEN[index], plan_id=inhalte["plan_id"],
                                        **argumente(inhalte)[index])


def test_qs_telefon_mit_und_ohne_kontaktlink(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        plan = db.get(KatplanPlan, inhalte["plan_id"])
        e = db.get(KatplanElement, inhalte["element_id"])
        e.beschreibung_html = "<p>Telefon +43 555 123456</p>"
        assert any(h["code"] == "telefon_im_text" for h in element_hinweise(db, plan, e, heute=date(2026, 10, 20)))
        for link in db.query(KatplanLink).filter_by(element_id=e.id, org_id=plan.org_id).all():
            db.delete(link)
        db.flush()
        assert any(h["code"] == "telefon_im_text" for h in element_hinweise(db, plan, e, heute=date(2026, 10, 20)))
        e.beschreibung_html = "<p>Bitte zentralen Kontakt verständigen.</p>"
        assert not any(h["code"] == "telefon_im_text" for h in element_hinweise(db, plan, e, heute=date(2026, 10, 20)))


def test_beziehung_uuid_planuebergreifend_und_orggrenze(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        fremd = db.query(User).filter_by(username=inhalte["users"]["fremd"]).one()
        uuids = []
        for org_id in (inhalte["org_id"], fremd.org_id):
            plan = KatplanPlan(org_id=org_id, titel="Zielplan")
            db.add(plan)
            db.flush()
            element = KatplanElement(org_id=org_id, plan_id=plan.id, typ="info", titel="Ziel", kennung="I-99")
            db.add(element)
            db.flush()
            uuids.append(element.uuid)
        db.commit()
    args = dict(element_id="KS-01", art="folgt_auf")
    with pytest.raises(ValueError, match="Element nicht gefunden"):
        schreiben(inhalte, NAMEN[2], **args, zu_element_id="I-99")
    with pytest.raises(ValueError, match="Element nicht gefunden"):
        schreiben(inhalte, NAMEN[2], **args, zu_element_id=uuids[1])
    result = schreiben(inhalte, NAMEN[2], **args, zu_element_id=uuids[0])
    assert result["beziehung"]["zu_element_id"] == uuids[0]
    assert schreiben(inhalte, NAMEN[3], element_id="KS-01", zu_element_id=uuids[0])["entfernt"] == 1


def test_stammdaten_gefahrenarten_und_unveraendert(inhalte):
    vorher = kopf(inhalte)
    args = {"titel": vorher["kopf"]["titel"], "gefahrenarten": ["hochwasser", "hochwasser"]}
    result = schreiben(inhalte, NAMEN[4], version=vorher["plan_version"], felder=args)
    assert result["geaendert"] == [] and result["unveraendert"] == list(args)
    assert result["plan"]["gefahrenarten"] == ["hochwasser"]


def test_telefonerkennung_ignoriert_datum_und_zahlen():
    from app.services.katplan_qs_service import _hat_telefon

    assert _hat_telefon("Tel. 0664 1234567") and _hat_telefon("+43 5574 12345")
    assert not _hat_telefon("am 2026-10-20 prüfen") and not _hat_telefon("Stand 20.10.2026")
    assert not _hat_telefon("Pegel 450 cm, PLZ 6922")
