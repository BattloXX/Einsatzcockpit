"""Einheitenansicht für eingeloggte, verknüpfte Gruppenkommandanten."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.core.security import hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import (
    EinheitAktion,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    MajorIncident,
    MajorIncidentStatus,
    SiteLogEntry,
    SitePhase,
)
from app.models.master import FireDept, Member
from app.models.user import Role, User, UserRole
from app.routers.ui_einheit import einheit_darf
from app.services.einheit_service import kontext_fuer_benutzer
from app.services.resource_service import STATUS_BEREITGESTELLT


def _db():
    db = SessionLocal()
    set_tenant_context(db, None)
    return db


def _daten():
    db = _db()
    try:
        suffix = uuid4().hex
        org = FireDept(slug=f"benutzer-einheit-{suffix}", name="Benutzer Einheit")
        fremd = FireDept(slug=f"benutzer-einheit-fremd-{suffix}", name="Fremd")
        db.add_all([org, fremd])
        db.flush()
        member = Member(org_id=org.id, firstname="Max", lastname="Mustermann")
        user = User(username=f"gk-benutzer-{suffix}", password_hash=hash_password("Test1234!"), display_name="Max M.", org_id=org.id, member_id=None)
        ohne = User(username=f"ohne-benutzer-{suffix}", password_hash=hash_password("Test1234!"), display_name="Ohne", org_id=org.id)
        db.add_all([member, user, ohne])
        db.flush()
        user.member_id = member.id
        lage = MajorIncident(org_id=org.id, name="Lage A", status=MajorIncidentStatus.active)
        fremde_lage = MajorIncident(org_id=fremd.id, name="Fremde Lage", status=MajorIncidentStatus.active)
        db.add_all([lage, fremde_lage])
        db.flush()
        einheit = LageEinheit(lage_id=lage.id, label="Gruppe 1", status=STATUS_BEREITGESTELLT)
        fremde_einheit = LageEinheit(lage_id=fremde_lage.id, label="Fremd", status=STATUS_BEREITGESTELLT)
        db.add_all([einheit, fremde_einheit])
        db.flush()
        leader = LageEinheitLeader(einheit_id=einheit.id, member_id=member.id, person_name="Max Mustermann", start_at=datetime.now(UTC))
        fremder_leader = LageEinheitLeader(einheit_id=fremde_einheit.id, member_id=member.id, person_name="Max Mustermann", start_at=datetime.now(UTC))
        db.add_all([leader, fremder_leader])
        db.flush()
        einheit.leader_assignment_id = leader.id
        fremde_einheit.leader_assignment_id = fremder_leader.id
        db.commit()
        return {"org": org.id, "user": user.id, "ohne": ohne.id, "member": member.id, "einheit": einheit.id, "leader": leader.id, "fremd": fremde_einheit.id}
    finally:
        db.close()


def _anmelden(client, user_id):
    client.cookies.clear()
    client.cookies.set("session", sign_session(user_id))


def _headers(client, **extra):
    headers = dict(extra)
    if csrf := client.cookies.get("ec_csrf"):
        headers["X-CSRF-Token"] = csrf
    return headers


def _auftrag(db, einheit_id, lage_id, org_id, label="Einsatzstelle"):
    site = IncidentSite(
        major_incident_id=lage_id, org_id=org_id, bezeichnung=label, phase=SitePhase.disponiert
    )
    db.add(site)
    db.flush()
    dispatch = EinheitSiteDispatch(einheit_id=einheit_id, site_id=site.id, dispatched_at=datetime.now(UTC))
    db.add(dispatch)
    db.flush()
    return dispatch


def _zweite_einheit(db, x):
    lage = MajorIncident(org_id=x["org"], name="Lage B", status=MajorIncidentStatus.active)
    db.add(lage)
    db.flush()
    einheit = LageEinheit(lage_id=lage.id, label="Gruppe 2", status=STATUS_BEREITGESTELLT)
    db.add(einheit)
    db.flush()
    leader = LageEinheitLeader(
        einheit_id=einheit.id, member_id=x["member"], person_name="Max Mustermann", start_at=datetime.now(UTC)
    )
    db.add(leader)
    db.flush()
    einheit.leader_assignment_id = leader.id
    db.commit()
    return einheit.id


def test_verknuepfter_gk_bekommt_kontext_und_startkarte(client, setup_db):
    x = _daten()
    _anmelden(client, x["user"])
    assert client.get("/einheit/api/zustand").status_code == 200
    start = client.get("/")
    assert start.status_code == 200
    assert "Du führst Gruppe 1 – Lage A" in start.text


def test_ohne_verknuepfung_und_fremde_einheit_haben_keinen_kontext(client, setup_db):
    x = _daten()
    _anmelden(client, x["ohne"])
    assert client.get("/einheit/api/zustand").status_code == 403
    _anmelden(client, x["user"])
    assert client.get("/einheit/api/zustand", headers={"X-EC-Einheit": str(x["fremd"])}).status_code == 403


def test_gk_wechsel_und_beendete_einheit_entziehen_kontext(client, setup_db):
    x = _daten()
    _anmelden(client, x["user"])
    with _db() as db:
        leader = db.get(LageEinheitLeader, x["leader"])
        leader.end_at = datetime.now(UTC)
        db.commit()
    assert client.get("/einheit/api/zustand").status_code == 403


def test_benutzer_mitglied_ohne_einheit_sieht_hinweis(client, setup_db):
    x = _daten()
    with _db() as db:
        einheit = db.get(LageEinheit, x["einheit"])
        einheit.status = "beendet"
        db.commit()
    _anmelden(client, x["user"])
    response = client.get("/einheit")
    assert response.status_code == 200
    assert "Du führst derzeit keine Einheit in einer aktiven Großschadenslage." in response.text


def test_benutzer_waehlt_zweite_einheit_per_header_und_sieht_umschalter(client, setup_db):
    x = _daten()
    with _db() as db:
        zweite = _zweite_einheit(db, x)
    _anmelden(client, x["user"])
    headers = {"X-EC-Einheit": str(zweite)}
    zustand = client.get("/einheit/api/zustand", headers=headers)
    assert zustand.status_code == 200
    assert zustand.json()["kopf"]["einheit_id"] == zweite
    assert client.get("/einheit/api/zustand", headers={"X-EC-Einheit": str(x["fremd"])}).status_code == 403
    assert client.get("/einheit/api/zustand?einheit=abc").status_code == 404
    seite = client.get("/einheit")
    assert seite.status_code == 200
    assert "Gruppe 1" in seite.text and "Gruppe 2" in seite.text


def test_benutzer_status_schreibt_autor_quelle_und_ist_idempotent(client, setup_db):
    x = _daten()
    with _db() as db:
        dispatch = _auftrag(db, x["einheit"], db.get(LageEinheit, x["einheit"]).lage_id, x["org"])
        dispatch_id, site_id = dispatch.id, dispatch.site_id
        db.commit()
    _anmelden(client, x["user"])
    client.get("/einheit/api/zustand")
    payload = {"status": "vor_ort", "client_uuid": str(uuid4())}
    first = client.post(f"/einheit/api/auftrag/{dispatch_id}/status", json=payload, headers=_headers(client))
    again = client.post(f"/einheit/api/auftrag/{dispatch_id}/status", json=payload, headers=_headers(client))
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    with _db() as db:
        assert db.query(EinheitAktion).filter_by(client_uuid=payload["client_uuid"]).one().quelle == "benutzer"
        log = db.query(SiteLogEntry).filter_by(incident_site_id=site_id, kind="einheit").one()
        assert log.author_name == "Max Mustermann (GK Gruppe 1)"


def test_benutzer_gesperrte_aktionen_werden_abgewiesen(client, setup_db):
    x = _daten()
    _anmelden(client, x["user"])
    client.get("/einheit/api/zustand")
    with _db() as db:
        user = db.get(User, x["user"])
        ctx = kontext_fuer_benutzer(db, user)
        assert ctx is not None
        with pytest.raises(HTTPException) as error:
            einheit_darf("nicht_erlaubt", ctx, db)
        assert error.value.status_code == 403
        assert error.value.detail["code"] == "zugang_aktion_nicht_erlaubt"
    response = client.post(
        "/einheit/api/ressource/personal",
        json={"operation": "setzen", "gesamt": 8, "client_uuid": str(uuid4())},
        headers=_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "zugang_aktion_nicht_erlaubt"


def test_admin_simulation_hat_vorrang_vor_benutzer_kontext(client, setup_db):
    x = _daten()
    with _db() as db:
        user = db.get(User, x["user"])
        admin = db.query(Role).filter_by(code="admin").one()
        db.add(UserRole(user_id=user.id, role_id=admin.id))
        db.commit()
    _anmelden(client, x["user"])
    response = client.get("/einheit/api/zustand", headers={"X-EC-Einheit-Sim": str(x["einheit"])})
    assert response.status_code == 200
    assert response.json()["simulation"] is True
