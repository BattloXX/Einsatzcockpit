"""K1b: UI, Rollen, Schalter, Stammdaten und Tenant-Isolation."""

from uuid import uuid4

import pytest

from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAenderung, KatplanPlan, KatplanPruefung
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.user import AuditLog
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _login, _user


@pytest.fixture
def umgebung(client):
    code = uuid4().hex
    with TestingSession() as db:
        set_tenant_context(db, None)
        org = FireDept(slug=f"kp-{code}", name="Katplan UI")
        fremd = FireDept(slug=f"kp-fremd-{code}", name="Andere Organisation")
        db.add_all([org, fremd])
        db.flush()
        db.add_all([OrgSettings(org_id=org.id, katplan_modul_aktiv=True),
                    OrgSettings(org_id=fremd.id, katplan_modul_aktiv=True)])
        flag = db.query(SystemSettings).filter_by(key="katplan_module_enabled").first()
        vorher = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="katplan_module_enabled")
            db.add(flag)
        flag.value = "true"
        users = {}
        for rolle in ("katplan_verwalter", "katplan_freigeber", "readonly", "org_admin", "system_admin"):
            name = f"{rolle}-{code}"
            _user(db, name, org.id, rolle)
            users[rolle] = name
        users["fremd"] = f"fremd-{code}"
        _user(db, users["fremd"], fremd.id, "katplan_verwalter")
        plan = KatplanPlan(org_id=org.id, titel=f"Entwurf-{code}", gefahrenarten_json='["hochwasser"]')
        db.add(plan)
        db.commit()
        info = {"org_id": org.id, "plan_id": plan.id, "titel": plan.titel, "users": users}
    yield info
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="katplan_module_enabled").one()
        if vorher is None:
            db.delete(flag)
        else:
            flag.value = vorher
        db.commit()


def login(client, umgebung, rolle="katplan_verwalter"):
    assert _login(client, umgebung["users"][rolle]).status_code == 302


def post(client, path, **data):
    return client.post(path, data={"_csrf": client.cookies.get("ec_csrf"), **data},
                       headers={"HX-Request": "true"}, follow_redirects=False)


def form(**kwargs):
    return {"titel": "Hochwasserplan", "gefahrenarten": ["hochwasser"], **kwargs}


def test_modul_aus_und_menue(client, umgebung):
    login(client, umgebung)
    page = client.get("/plaene")
    assert page.status_code == 200
    assert 'href="/plaene"' in page.text
    assert 'href="/einsatzwissen"' in page.text
    assert 'href="/einsatzwissen"' in client.get("/").text
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(SystemSettings).filter_by(key="katplan_module_enabled").one().value = "false"
        db.commit()
    assert client.get("/plaene").status_code == 404
    assert 'href="/einsatzwissen"' not in client.get("/").text


def test_anlegen_sanitizing_und_protokoll(client, umgebung):
    login(client, umgebung)
    assert client.get("/plaene/neu").status_code == 200
    response = post(client, "/plaene/neu", **form(
        beschreibung_html='<p>Inhalt</p><script>alert(1)</script><img src=x onerror="alert(1)">',
        geltungsbereich="Gemeinde", verantwortliche_stelle="Bauhof", wiedervorlage_am="2026-11-01"))
    assert response.status_code == 303
    plan_id = int(response.headers["location"].rsplit("/", 1)[1])
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, plan_id)
        assert plan.org_id == umgebung["org_id"]
        assert plan.beschreibung_html == "<p>Inhalt</p>"
        assert plan.gefahrenarten == ["hochwasser"]
        assert db.query(KatplanAenderung).filter_by(plan_id=plan_id).one().aktion == "angelegt"
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200
    assert f'href="/plaene/{plan_id}/bearbeiten"' in detail.text and "Bauhof" in detail.text
    assert "angelegt" in detail.text and umgebung["users"]["katplan_verwalter"] in detail.text


def test_leser_sieht_keine_entwuerfe(client, umgebung):
    login(client, umgebung, "readonly")
    response = client.get("/plaene")
    assert response.status_code == 200
    assert umgebung["titel"] not in response.text
    assert "Noch keine freigegebenen Pläne" in response.text
    assert client.get("/plaene/neu").status_code == 403
    assert post(client, "/plaene/neu", **form()).status_code == 403
    assert client.get(f'/plaene/{umgebung["plan_id"]}').status_code == 404
    for suffix in ("stammdaten", "archivieren", "reaktivieren"):
        assert post(client, f'/plaene/{umgebung["plan_id"]}/{suffix}', **form(version=0)).status_code == 403


def test_stammdaten_konflikt_und_feldprotokoll(client, umgebung):
    login(client, umgebung)
    path = f'/plaene/{umgebung["plan_id"]}/stammdaten'
    assert 'name="version" value="0"' in client.get(path).text
    data = form(beschreibung_html="<p>Neu</p>", geltungsbereich="Bezirk",
                verantwortliche_stelle="Leitstelle", wiedervorlage_am="2026-11-02")
    response = post(client, path, version=-1, **data)
    assert response.status_code == 409
    assert "zwischenzeitlich" in response.text and 'value="Hochwasserplan"' in response.text
    response = post(client, path, version=0, **data)
    assert response.status_code == 200
    assert 'id="kp-kopf"' in response.text and 'hx-swap-oob="outerHTML"' in response.text
    assert "<body" not in response.text
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        assert plan.version == 1 and plan.aktualisiert_von_id is not None
        aenderungen = db.query(KatplanAenderung).filter_by(plan_id=plan.id).all()
        assert {a.feld for a in aenderungen} == {
            "titel", "beschreibung_html", "geltungsbereich", "verantwortliche_stelle", "wiedervorlage_am"}
        assert all(a.aktion == "geaendert" and a.neu for a in aenderungen)
        plan.status = "freigegeben"
        db.commit()
    assert post(client, path, version=1, **data).status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        plan = db.get(KatplanPlan, umgebung["plan_id"])
        assert plan.status == "freigegeben" and plan.version == 2


def test_archivieren_reaktivieren_und_filter(client, umgebung):
    login(client, umgebung)
    path = f'/plaene/{umgebung["plan_id"]}'
    assert post(client, path + "/archivieren").status_code == 403
    login(client, umgebung, "katplan_freigeber")
    assert post(client, path + "/archivieren").status_code == 200
    assert umgebung["titel"] not in client.get("/plaene").text
    response = client.get("/plaene?archiv=1&status=archiviert&gefahr=hochwasser&q=Entwurf",
                          headers={"HX-Request": "true"})
    assert umgebung["titel"] in response.text and "<body" not in response.text
    assert umgebung["titel"] not in client.get("/plaene?archiv=1&gefahr=sturm").text
    assert umgebung["titel"] not in client.get("/plaene?archiv=1&q=unfindbar").text
    assert post(client, path + "/reaktivieren").status_code == 200
    with TestingSession() as db:
        set_tenant_context(db, umgebung["org_id"])
        assert db.get(KatplanPlan, umgebung["plan_id"]).status == "entwurf"
        assert {p.aktion for p in db.query(KatplanPruefung).filter_by(plan_id=umgebung["plan_id"])} == {
            "archiviert", "reaktiviert"}


def test_org_isolation(client, umgebung):
    login(client, umgebung, "fremd")
    path = f'/plaene/{umgebung["plan_id"]}'
    assert umgebung["titel"] not in client.get("/plaene").text
    assert client.get(path).status_code == 404
    assert client.get(path + "/stammdaten").status_code == 404
    assert post(client, path + "/stammdaten", **form(version=0)).status_code == 404
    # Auch ein Freigeber aus der anderen Org darf den Plan nicht mutieren.
    with TestingSession() as db:
        set_tenant_context(db, None)
        from app.models.user import User, UserRole, Role
        user = db.query(User).filter_by(username=umgebung["users"]["fremd"]).one()
        role = db.query(Role).filter_by(code="katplan_freigeber").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    for suffix in ("archivieren", "reaktivieren"):
        assert post(client, path + "/" + suffix).status_code == 404


def test_systemschalter_nur_system_admin(client, umgebung):
    path = "/admin/settings/system/katplan-toggle"
    login(client, umgebung, "org_admin")
    assert post(client, path, enabled_raw="1").status_code == 403
    login(client, umgebung, "system_admin")
    page = client.get(f'/admin/settings?org_id={umgebung["org_id"]}')
    assert page.status_code == 200
    assert 'action="/admin/settings/system/katplan-toggle"' in page.text
    assert 'name="katplan_modul_aktiv_raw"' in page.text
    response = post(client, path, enabled_raw="", org_id=umgebung["org_id"])
    assert response.status_code == 303
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(SystemSettings).filter_by(key="katplan_module_enabled").one().value == "false"
        assert db.query(AuditLog).filter_by(action="katplan.system_toggle").count() >= 1


@pytest.mark.parametrize("data", [form(titel=""), {"titel": "Ohne Gefahr"}, form(titel="x" * 201),
                                      form(gefahrenarten=["unbekannt"]), form(wiedervorlage_am="kaputt")])
def test_validierungsfehler(client, umgebung, data):
    login(client, umgebung)
    response = post(client, "/plaene/neu", **data)
    assert response.status_code == 422
    assert 'role="alert"' in response.text and '<form' in response.text
    response = post(client, f'/plaene/{umgebung["plan_id"]}/stammdaten', version=0, **data)
    assert response.status_code == 422
    assert 'role="alert"' in response.text


def test_org_schalter_nur_bei_aktivem_system_speichern(client, umgebung):
    login(client, umgebung, "org_admin")
    path = "/admin/settings/org"
    response = post(client, path, org_name="Katplan UI")
    assert response.status_code in (200, 303)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert not db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().katplan_modul_aktiv
        assert db.query(AuditLog).filter_by(action="katplan.org_toggle", org_id=umgebung["org_id"]).count() == 1
    assert client.get("/plaene").status_code == 404
    response = post(client, path, org_name="Katplan UI", katplan_modul_aktiv_raw="1")
    assert response.status_code in (200, 303)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().katplan_modul_aktiv
        db.query(SystemSettings).filter_by(key="katplan_module_enabled").one().value = "false"
        db.commit()
    response = post(client, path, org_name="Katplan UI")
    assert response.status_code in (200, 303)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(OrgSettings).filter_by(org_id=umgebung["org_id"]).one().katplan_modul_aktiv
