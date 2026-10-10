"""Ressourcenübersicht: Formular „Einheit hinzufügen“ und Anlage in einem Vorgang."""

import json
from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, LageEinheitLeader, Sector, MajorIncident
from app.models.master import FireDept, Member
from app.models.user import Role, User, UserRole


def _daten(role_code="recorder"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:8]
        org = FireDept(slug="anlage-" + suffix, name="Anlage", color="#f00", bos="FW")
        db.add(org)
        db.flush()
        user = User(
            username="anlage-" + suffix, password_hash=hash_password("Test1234!"),
            display_name="Test", org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == role_code).one().id))
        lage = MajorIncident(org_id=org.id, name="Lage")
        db.add(lage)
        db.flush()
        sektor = Sector(major_incident_id=lage.id, name="Nord")
        member = Member(org_id=org.id, firstname="Anna", lastname="Muster", phone="+436641234567", active=True)
        db.add_all([sektor, member])
        db.commit()
        return user.username, lage.id, sektor.id, member.id
    finally:
        db.close()


def _login(client, username):
    client.get("/login")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")}
    )
    assert response.status_code == 200


def test_formular_zeigt_neue_felder_und_telefon_nur_eigene_org(client, setup_db):
    username, lage_id, _, member_id = _daten()
    fremd = _daten()
    _login(client, username)
    text = client.get(f"/lage/{lage_id}/ressourcen").text
    assert 'name="gk_telefon"' in text and 'name="funkrufname"' in text and 'name="sektor_id"' in text
    assert "+436641234567" in text
    daten = json.loads(text.split("var resourceMemberData = ")[1].split(";\n")[0])
    assert [m["id"] for m in daten] == [member_id] and fremd[3] != member_id


def test_post_vollfelder_ist_sofort_auf_karte_sichtbar(client, setup_db):
    username, lage_id, sektor_id, member_id = _daten()
    _login(client, username)
    response = client.post(
        f"/lage/{lage_id}/einheiten",
        data={
            "_csrf": client.cookies.get("ec_csrf"), "resource_type": "extern", "label": "Bergrettung Nord",
            "org_name": "BR", "bos": "BR", "funkrufname": "Berg 1", "sektor_id": str(sektor_id),
            "bereitstellungsraum": "Parkplatz", "gk_member_id": str(member_id),
            "gk_telefon": "+436641234567", "personal_gesamt": "6", "personal_fuehrer": "1",
        },
    )
    assert response.status_code == 204, response.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        einheit = db.query(LageEinheit).filter_by(lage_id=lage_id, label="Bergrettung Nord").one()
        assert einheit.funkrufname == "Berg 1" and einheit.sector_id == sektor_id
        leader = db.query(LageEinheitLeader).filter_by(einheit_id=einheit.id).one()
        assert leader.phone_e164 == "+436641234567" and leader.member_id == member_id
        einheit_id = einheit.id
    finally:
        db.close()
    karte = client.get(f"/lage/{lage_id}/einheiten/{einheit_id}/karte")
    assert karte.status_code == 200 and "Bergrettung Nord" in karte.text
    journal = client.get(f"/lage/{lage_id}/einheiten/{einheit_id}/karte/journal")
    assert journal.status_code == 200 and "Ressource hinzugefügt" in journal.text


def test_readonly_darf_nicht_anlegen(client, setup_db):
    username, lage_id, _, _ = _daten("readonly")
    _login(client, username)
    response = client.post(
        f"/lage/{lage_id}/einheiten",
        data={"_csrf": client.cookies.get("ec_csrf"), "resource_type": "extern", "label": "X"},
    )
    assert response.status_code == 403
    assert 'name="gk_telefon"' not in client.get(f"/lage/{lage_id}/ressourcen").text
