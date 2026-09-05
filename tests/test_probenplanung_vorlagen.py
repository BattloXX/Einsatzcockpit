"""HTTP-Tests fuer Phase 3 der Probenplanung: versionierte Vorlagen."""

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings, SystemSettings
from app.models.probenplanung import (
    ChecklistItemTyp,
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateSection,
    ChecklistTemplateVersion,
    Probeart,
)
from app.models.user import Role, User, UserRole

ORG_ID = 1
BASE = "/admin/probenplanung/vorlagen"


def _user(username: str, role_code: str, org_id: int = ORG_ID) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(
            username=username,
            password_hash=hash_password("Test1234!"),
            display_name=username,
            org_id=org_id,
            active=True,
        )
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def _flags(enabled: bool) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        row = db.query(SystemSettings).filter(SystemSettings.key == "probenplanung_module_enabled").first()
        if row is None:
            db.add(SystemSettings(key="probenplanung_module_enabled", value=str(enabled).lower()))
        else:
            row.value = str(enabled).lower()
        org = db.query(OrgSettings).filter(OrgSettings.org_id == ORG_ID).first()
        if org is None:
            org = OrgSettings(org_id=ORG_ID)
            db.add(org)
        org.probenplanung_modul_aktiv = enabled
        db.commit()
    finally:
        db.close()


def _login(client, username: str) -> str:
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf})
    return client.cookies.get("ec_csrf")


def _create_template(client, csrf: str, name: str) -> tuple[int, int]:
    response = client.post(BASE, data={"_csrf": csrf, "name": name}, follow_redirects=False)
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        template = (
            db.query(ChecklistTemplate).filter(ChecklistTemplate.org_id == ORG_ID, ChecklistTemplate.name == name).one()
        )
        version = db.query(ChecklistTemplateVersion).filter(ChecklistTemplateVersion.template_id == template.id).one()
        return template.id, version.id
    finally:
        db.close()


def _add_section(client, csrf: str, template_id: int, version_id: int, title: str) -> int:
    response = client.post(
        f"{BASE}/{template_id}/version/{version_id}/bereiche",
        data={"_csrf": csrf, "titel": title},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return (
            db.query(ChecklistTemplateSection)
            .filter(
                ChecklistTemplateSection.version_id == version_id,
                ChecklistTemplateSection.titel == title,
            )
            .one()
            .id
        )
    finally:
        db.close()


def _add_item(
    client,
    csrf: str,
    template_id: int,
    version_id: int,
    section_id: int,
    title: str,
    item_type: str = "checkbox",
) -> int:
    data = {"_csrf": csrf, "titel": title, "typ": item_type}
    if item_type in {"auswahl", "mehrfachauswahl"}:
        data["optionen"] = "Erste Option\nZweite Option"
    response = client.post(
        f"{BASE}/{template_id}/version/{version_id}/bereiche/{section_id}/punkte",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return (
            db.query(ChecklistTemplateItem)
            .filter(
                ChecklistTemplateItem.section_id == section_id,
                ChecklistTemplateItem.titel == title,
            )
            .one()
            .id
        )
    finally:
        db.close()


def test_veroeffentlichen_immutability_und_versionsklon(client):
    _user("vorlagen_version_admin", "org_admin")
    _flags(True)
    csrf = _login(client, "vorlagen_version_admin")
    template_id, version_id = _create_template(client, csrf, "Versionstest")
    section_id = _add_section(client, csrf, template_id, version_id, "Vorbereitung")
    item_id = _add_item(client, csrf, template_id, version_id, section_id, "Eigentümer anrufen")

    response = client.post(
        f"{BASE}/{template_id}/version/{version_id}/veroeffentlichen",
        data={"_csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        template = db.get(ChecklistTemplate, template_id)
        published = db.get(ChecklistTemplateVersion, version_id)
        assert published.veroeffentlicht_am is not None
        assert template.aktive_version_id == version_id
    finally:
        db.close()

    mutation = client.post(
        f"{BASE}/{template_id}/version/{version_id}/bereiche/{section_id}/punkte/{item_id}",
        data={"_csrf": csrf, "titel": "Manipuliert", "typ": "text"},
        follow_redirects=False,
    )
    assert mutation.status_code == 409

    clone_response = client.post(f"{BASE}/{template_id}/version/neu", data={"_csrf": csrf}, follow_redirects=False)
    assert clone_response.status_code == 303
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        versions = (
            db.query(ChecklistTemplateVersion)
            .filter(ChecklistTemplateVersion.template_id == template_id)
            .order_by(ChecklistTemplateVersion.version)
            .all()
        )
        assert [row.version for row in versions] == [1, 2]
        assert versions[1].veroeffentlicht_am is None
        clone_sections = (
            db.query(ChecklistTemplateSection).filter(ChecklistTemplateSection.version_id == versions[1].id).all()
        )
        clone_items = (
            db.query(ChecklistTemplateItem).filter(ChecklistTemplateItem.section_id == clone_sections[0].id).all()
        )
        assert [row.titel for row in clone_items] == ["Eigentümer anrufen"]
        assert db.get(ChecklistTemplateItem, item_id).titel == "Eigentümer anrufen"
    finally:
        db.close()


def test_alle_punkttypen_und_sortierung_ueber_http(client):
    _user("vorlagen_typen_admin", "admin")
    _flags(True)
    csrf = _login(client, "vorlagen_typen_admin")
    template_id, version_id = _create_template(client, csrf, "Typen und Sortierung")
    first_section = _add_section(client, csrf, template_id, version_id, "Zuerst")
    second_section = _add_section(client, csrf, template_id, version_id, "Danach")
    item_ids = [
        _add_item(client, csrf, template_id, version_id, first_section, f"Punkt {item_type.value}", item_type.value)
        for item_type in ChecklistItemTyp
    ]

    response = client.post(
        f"{BASE}/{template_id}/version/{version_id}/bereiche/sortieren",
        data={"_csrf": csrf, "sortierung": f"[{second_section},{first_section}]"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    reversed_ids = list(reversed(item_ids))
    response = client.post(
        f"{BASE}/{template_id}/version/{version_id}/bereiche/{first_section}/punkte/sortieren",
        data={"_csrf": csrf, "sortierung": str(reversed_ids)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get(f"{BASE}/{template_id}?version={version_id}").status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        sections = (
            db.query(ChecklistTemplateSection)
            .filter(ChecklistTemplateSection.version_id == version_id)
            .order_by(ChecklistTemplateSection.sortierung)
            .all()
        )
        assert [row.id for row in sections] == [second_section, first_section]
        items = (
            db.query(ChecklistTemplateItem)
            .filter(ChecklistTemplateItem.section_id == first_section)
            .order_by(ChecklistTemplateItem.sortierung)
            .all()
        )
        assert [row.id for row in items] == reversed_ids
        assert {row.typ for row in items} == {item_type.value for item_type in ChecklistItemTyp}
    finally:
        db.close()


def test_rollenschutz_modulflag_und_fremde_org(client):
    _user("vorlagen_editor", "probenverwalter")
    _flags(True)
    _login(client, "vorlagen_editor")
    assert client.get(BASE).status_code == 403

    _user("vorlagen_guard_admin", "org_admin")
    csrf = _login(client, "vorlagen_guard_admin")
    _flags(False)
    assert client.get(BASE).status_code == 404
    _flags(True)

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        foreign = ChecklistTemplate(org_id=999, name="Fremde Vorlage")
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    finally:
        db.close()
    assert client.get(f"{BASE}/{foreign_id}").status_code == 404
    assert (
        client.post(f"{BASE}/{foreign_id}/version/neu", data={"_csrf": csrf}, follow_redirects=False).status_code == 404
    )


def test_standardvorlage_import_ist_idempotent(client):
    _user("vorlagen_import_admin", "org_admin")
    _flags(True)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.add(Probeart(org_id=ORG_ID, name="Vollprobe", kurz="VP", sortierung=1))
        db.commit()
    finally:
        db.close()
    csrf = _login(client, "vorlagen_import_admin")
    for _ in range(2):
        response = client.post(f"{BASE}/standard-import", data={"_csrf": csrf}, follow_redirects=False)
        assert response.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        templates = (
            db.query(ChecklistTemplate)
            .filter(
                ChecklistTemplate.org_id == ORG_ID,
                ChecklistTemplate.name == "Vollprobe Standard",
            )
            .all()
        )
        assert len(templates) == 1
        versions = (
            db.query(ChecklistTemplateVersion).filter(ChecklistTemplateVersion.template_id == templates[0].id).all()
        )
        assert len(versions) == 1
        sections = (
            db.query(ChecklistTemplateSection).filter(ChecklistTemplateSection.version_id == versions[0].id).all()
        )
        assert len(sections) == 8
        assert (
            db.query(ChecklistTemplateItem)
            .filter(ChecklistTemplateItem.section_id.in_([row.id for row in sections]))
            .count()
            == 40
        )
        vollprobe = db.query(Probeart).filter(Probeart.org_id == ORG_ID, Probeart.name == "Vollprobe").one()
        assert vollprobe.checklist_template_id == templates[0].id
    finally:
        db.close()
