"""Regressionen fuer Adressbuchrechte und einsatzgebundene Kontaktfreigaben."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Incident, IncidentColumn, IncidentVehicle
from app.models.kontakt import EinsatzKontakt, Kontakt, KontaktTelefon, ObjektKontaktFreigabe
from app.models.master import FireDept, OrgSettings, SystemSettings, VehicleMaster
from app.models.objekt import Objekt, ObjektEinsatz, ObjektKontakt
from app.models.user import ApiKey, DeviceToken, Role, User, UserRole
from app.services.kontakt_access_service import incident_for_contacts


def _setup(role="readonly", *, device=False):
    with SessionLocal() as db:
        set_tenant_context(db, None)
        name = uuid4().hex
        org = FireDept(slug=name, name=name)
        db.add(org)
        db.flush()
        user = User(
            username=name,
            display_name=name,
            password_hash=hash_password("Test1234!"),
            org_id=org.id,
            active=True,
            is_device=device,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter_by(code=role).one().id))
        for flag in ("kontakte_module_enabled", "objekt_module_enabled"):
            setting = db.get(SystemSettings, flag)
            if setting is None:
                db.add(SystemSettings(key=flag, value="true"))
            else:
                setting.value = "true"
        db.add(OrgSettings(org_id=org.id, kontakte_module_enabled=True, objekt_module_enabled=True))
        incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active")
        other_incident = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active")
        contact = Kontakt(
            org_id=org.id,
            anzeigename="Freigegebener Ansprechpartner",
            notizen="VERTRAULICHE NOTIZ",
            telefone=[
                KontaktTelefon(org_id=org.id, nummer="+436641234567", verwendung="dienst"),
                KontaktTelefon(org_id=org.id, nummer="+436649999999", verwendung="privat"),
            ],
        )
        secret = Kontakt(org_id=org.id, anzeigename="Nicht zugeordneter Kontakt")
        objekt = Objekt(org_id=org.id, nummer=1, name="Testobjekt", status="freigegeben")
        db.add_all([incident, other_incident, contact, secret, objekt])
        db.flush()
        mapping = ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=contact.id)
        db.add_all(
            [
                mapping,
                EinsatzKontakt(org_id=org.id, incident_id=incident.id, kontakt_id=contact.id),
                ObjektEinsatz(
                    org_id=org.id, incident_id=incident.id, objekt_id=objekt.id, quelle="manuell", status="bestaetigt"
                ),
            ]
        )
        db.flush()
        db.add(
            ObjektKontaktFreigabe(
                org_id=org.id, objekt_kontakt_id=mapping.id, kanal="sms", ziel_wert="+436649999999", aktiv=True
            )
        )
        db.commit()
        return SimpleNamespace(
            user=user.id,
            org=org.id,
            incident=incident.id,
            other=other_incident.id,
            contact=contact.id,
            secret=secret.id,
            objekt=objekt.id,
            mapping=mapping.id,
        )


@pytest.mark.parametrize(
    "path", ["", "/liste", "/offline-sync", "/duplikatcheck", "/export.csv", "/1", "/1/profilbild"]
)
def test_leser_hat_keinen_adressbuchzugriff(client, path):
    data = _setup()
    client.cookies.set("session", sign_session(data.user))
    assert client.get("/kontakte" + path).status_code == 403


def test_leser_sieht_nur_einsatzkontakte_ohne_private_daten(client):
    data = _setup()
    client.cookies.set("session", sign_session(data.user))
    response = client.get(f"/einsatz/{data.incident}/kontakte?q=Nicht")
    assert response.status_code == 200
    assert "Freigegebener Ansprechpartner" in response.text
    for private in ("VERTRAULICHE NOTIZ", "+436649999999", "Nicht zugeordneter Kontakt", "Kontakt öffnen"):
        assert private not in response.text
    assert "+436641234567" in response.text
    assert "Freigegebener Ansprechpartner" not in client.get(f"/einsatz/{data.other}/kontakte").text
    assert 'href="/kontakte"' not in client.get("/").text
    csrf = client.cookies.get("ec_csrf")
    assert (
        client.post(
            f"/einsatz/{data.other}/kontakte/zuordnen", data={"_csrf": csrf, "kontakt_id": data.contact}
        ).status_code
        == 403
    )


def test_objektkontakte_brauchen_bestaetigten_berechtigten_einsatz(client):
    data = _setup()
    client.cookies.set("session", sign_session(data.user))
    path = f"/objekte/{data.objekt}/einsatz-fragment"
    assert "Freigegebener Ansprechpartner" not in client.get(path).text
    assert "Freigegebener Ansprechpartner" not in client.get(path + f"?incident_id={data.other}").text
    response = client.get(path + f"?incident_id={data.incident}")
    assert response.status_code == 200
    assert "Freigegebener Ansprechpartner" in response.text
    assert "+436649999999" not in response.text
    assert "Freigegebener Ansprechpartner" not in client.get(f"/objekte/{data.objekt}/kontakte").text
    assert "+436649999999" not in client.get(f"/objekte/{data.objekt}/benachrichtigung").text
    for suffix in ("kontakte/suche?q=Freigegeben", "datenpflege", "protokoll"):
        assert client.get(f"/objekte/{data.objekt}/{suffix}").status_code == 403
    with SessionLocal() as db:
        set_tenant_context(db, data.org)
        db.get(ObjektKontakt, data.mapping).einsatz_sichtbar = False
        db.commit()
    assert "Freigegebener Ansprechpartner" not in client.get(path + f"?incident_id={data.incident}").text


def test_einsatzleiter_sieht_alle_details_aber_aendert_keine_stammdaten(client):
    data = _setup("incident_leader")
    client.cookies.set("session", sign_session(data.user))
    detail = client.get(f"/kontakte/{data.contact}")
    assert detail.status_code == 200
    assert "VERTRAULICHE NOTIZ" in detail.text
    assert "+436649999999" in detail.text
    assert "Nicht zugeordneter Kontakt" in client.get("/kontakte/liste").text
    assert client.get("/kontakte/offline-sync").status_code == 200
    csrf = client.cookies.get("ec_csrf")
    assert client.post(f"/kontakte/{data.contact}", data={"_csrf": csrf, "anzeigename": "Geaendert"}).status_code == 403
    response = client.post(
        f"/einsatz/{data.other}/kontakte/zuordnen",
        data={"_csrf": csrf, "kontakt_id": data.secret, "hinweis": "Elektriker vor Ort"},
    )
    assert response.status_code == 200
    assert "Elektriker vor Ort" in response.text
    assert "Nicht zugeordneter Kontakt" in response.text
    assert (
        client.post(f"/einsatz/{data.other}/kontakte/{data.secret}/entfernen", data={"_csrf": csrf}).status_code == 200
    )


def test_fremde_organisation_kann_weder_lesen_noch_zuordnen(client):
    data = _setup("incident_leader")
    other = _setup("incident_leader")
    client.cookies.set("session", sign_session(other.user))
    assert client.get(f"/kontakte/{data.contact}").status_code == 404
    assert client.get(f"/einsatz/{data.incident}/kontakte").status_code == 404
    assert client.get(f"/objekte/{data.objekt}/einsatz-fragment?incident_id={data.incident}").status_code == 404
    csrf = client.cookies.get("ec_csrf")
    assert (
        client.post(
            f"/einsatz/{other.incident}/kontakte/zuordnen", data={"_csrf": csrf, "kontakt_id": data.contact}
        ).status_code
        == 404
    )


def test_geraet_braucht_aktive_fahrzeugzuordnung_zum_einsatz(client):
    data = _setup(device=True)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        vehicle = VehicleMaster(dept_id=data.org, name="Testfahrzeug", code="TEST")
        db.add(vehicle)
        db.flush()
        token = DeviceToken(
            user_id=data.user, label="Test", token_hash=hash_api_key(uuid4().hex), vehicle_master_id=vehicle.id
        )
        column = IncidentColumn(incident_id=data.incident, title="Einheiten", code="active", display_order=0)
        db.add_all([token, column])
        db.flush()
        assignment = IncidentVehicle(incident_id=data.incident, column_id=column.id, vehicle_master_id=vehicle.id)
        db.add(assignment)
        db.commit()
        token_id, assignment_id = token.id, assignment.id
    client.cookies.set("session", sign_session(data.user, device=True, device_token_id=token_id))
    assert client.get("/kontakte/offline-sync").status_code == 403
    assert client.get("/api/v1/device/kontakte/sync").status_code == 403
    assert client.get(f"/einsatz/{data.other}/kontakte").status_code == 404
    response = client.get(f"/einsatz/{data.incident}/kontakte")
    assert response.status_code == 200
    assert "Freigegebener Ansprechpartner" in response.text
    from datetime import UTC, datetime

    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(IncidentVehicle, assignment_id).removed_at = datetime.now(UTC)
        db.commit()
    assert client.get(f"/einsatz/{data.incident}/kontakte").status_code == 404


def test_qr_kontext_kann_keinen_fremden_einsatz_lesen():
    data = _setup()
    request = Request({"type": "http", "headers": []})
    with SessionLocal() as db:
        set_tenant_context(db, data.org)
        request.state.user = db.get(User, data.user)
        request.state.qr_incident_id = data.incident
        assert incident_for_contacts(db, request, data.incident).id == data.incident
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as error:
            incident_for_contacts(db, request, data.other)
        assert error.value.status_code == 404


def test_api_key_braucht_kontakte_scope(client):
    data = _setup()
    raw = uuid4().hex
    with SessionLocal() as db:
        set_tenant_context(db, None)
        key = ApiKey(org_id=data.org, label="Test", key_hash=hash_api_key(raw), scopes="einsatz:read")
        db.add(key)
        db.commit()
        key_id = key.id
    assert client.get("/api/v1/kontakte/sync", headers={"X-API-Key": raw}).status_code == 403
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(ApiKey, key_id).scopes = "kontakte:read"
        db.commit()
    assert client.get("/api/v1/kontakte/sync", headers={"X-API-Key": raw}).status_code == 200


def test_leser_pdf_enthaelt_keine_objektkontakte(client):
    from io import BytesIO

    from pypdf import PdfReader

    data = _setup()
    client.cookies.set("session", sign_session(data.user))
    response = client.get(f"/objekte/{data.objekt}/objektblatt.pdf")
    assert response.status_code == 200
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    assert "Testobjekt" in text
    assert "Freigegebener Ansprechpartner" not in text
    assert "436641234567" not in text
