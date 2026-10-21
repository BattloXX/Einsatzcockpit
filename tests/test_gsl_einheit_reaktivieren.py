"""Regressionen fuer die Reaktivierung abgerueckter GSL-Einheiten."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.models.major_incident import IncidentSite, LageEinheit, LageJournalEntry, MajorIncident, Sector
from app.models.master import FireDept, VehicleMaster
from app.models.user import Role, User, UserRole
from app.services import resource_service as service
from tests.conftest import TestingSession


@pytest.fixture
def daten(setup_db):
    db = TestingSession()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex
        org = FireDept(slug=f"reaktivieren-{suffix}", name="Reaktivierung", color="#123456", bos="FW")
        db.add(org)
        db.flush()
        user = User(
            username=f"reaktivieren-{suffix}", password_hash=hash_password("Test1234!"),
            display_name="Test", org_id=org.id, active=True,
        )
        lage = MajorIncident(org_id=org.id, name="Reaktivierung")
        vehicle = VehicleMaster(dept_id=org.id, code="RLF", name="RLF")
        db.add_all([user, lage, vehicle])
        db.flush()
        role = db.query(Role).filter_by(code="recorder").one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        sector = Sector(major_incident_id=lage.id, name="Nord")
        site = IncidentSite(major_incident_id=lage.id, org_id=org.id, bezeichnung="Stelle")
        db.add_all([sector, site])
        db.flush()
        gestern = datetime.now(UTC) - timedelta(days=1)
        einheit = LageEinheit(
            lage_id=lage.id, vehicle_id=vehicle.id, label="RLF alt", status=service.STATUS_ABGERUECKT,
            released_at=gestern, arrived_at=gestern, status_at=gestern,
            sector_id=sector.id, incident_site_id=site.id,
        )
        db.add(einheit)
        db.commit()
        yield db, lage, einheit, user
    finally:
        db.close()


def _duplikat(db, einheit):
    db.add(LageEinheit(
        lage_id=einheit.lage_id, vehicle_id=einheit.vehicle_id, label="RLF neu", status=service.STATUS_IM_EINSATZ,
    ))
    db.flush()


def _login(client, user):
    client.get("/login")
    response = client.post("/login", data={
        "username": user.username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    })
    assert response.status_code == 200


def test_service_reaktiviert_in_pool_mit_journal(daten):
    db, lage, einheit, user = daten
    altes_arrived_at = einheit.arrived_at
    result = service.reaktiviere_einheit(db, einheit.id, lage.id, author_name="Test", user_id=user.id)
    assert result is einheit
    assert einheit.status == service.STATUS_BEREITGESTELLT
    assert einheit.released_at is None
    assert einheit.sector_id is None
    assert einheit.incident_site_id is None
    assert einheit.status_at == einheit.arrived_at
    assert einheit.arrived_at.replace(tzinfo=UTC) > altes_arrived_at.replace(tzinfo=UTC)
    db.flush()
    entry = db.query(LageJournalEntry).filter_by(einheit_id=einheit.id).one()
    assert entry.text == "RLF alt: reaktiviert (Abgerückt → Bereitgestellt, Pool/Reserve)"
    assert (entry.category, entry.ereignis_typ, entry.quelle) == ("ressource", "status", "manuell")
    assert (entry.author_name, entry.user_id) == ("Test", user.id)
    db.rollback()
    db.refresh(einheit)
    assert einheit.status == service.STATUS_ABGERUECKT
    assert db.query(LageJournalEntry).filter_by(einheit_id=einheit.id).count() == 0


def test_service_lehnt_aktive_einheit_ab(daten):
    db, lage, einheit, _ = daten
    einheit.status = service.STATUS_BEREITGESTELLT
    with pytest.raises(ValueError, match="Einheit ist nicht abgerückt"):
        service.reaktiviere_einheit(db, einheit.id, lage.id)


@pytest.mark.parametrize("funktion", ["reaktivieren", "set_status"])
def test_service_lehnt_aktives_fahrzeugduplikat_ab(daten, funktion):
    db, lage, einheit, _ = daten
    _duplikat(db, einheit)
    with pytest.raises(ValueError, match="Fahrzeug ist bereits wieder in der Lage"):
        if funktion == "reaktivieren":
            service.reaktiviere_einheit(db, einheit.id, lage.id)
        else:
            service.set_status(db, einheit.id, lage.id, service.STATUS_IM_EINSATZ)
    assert einheit.status == service.STATUS_ABGERUECKT
    assert einheit.released_at is not None


@pytest.mark.parametrize("funktion", ["reaktivieren", "set_status"])
def test_service_lehnt_aufgeloesten_verband_ab(daten, funktion):
    db, lage, einheit, _ = daten
    einheit.resource_type = "verband"
    einheit.vehicle_id = None
    with pytest.raises(ValueError, match="Aufgelöste Verbände können nicht reaktiviert werden"):
        if funktion == "reaktivieren":
            service.reaktiviere_einheit(db, einheit.id, lage.id)
        else:
            service.set_status(db, einheit.id, lage.id, service.STATUS_IM_EINSATZ)
    assert einheit.status == service.STATUS_ABGERUECKT


def test_set_status_entfernt_released_at(daten):
    db, lage, einheit, _ = daten
    service.set_status(db, einheit.id, lage.id, service.STATUS_IM_EINSATZ)
    assert einheit.status == service.STATUS_IM_EINSATZ
    assert einheit.released_at is None


def test_route_und_fragment(client, daten, monkeypatch):
    import app.routers.ui_major_incident as router

    db, lage, einheit, user = daten
    events = []

    async def broadcast(lage_id, event):
        events.append((lage_id, event))

    monkeypatch.setattr(router, "broadcast_lage", broadcast)
    _login(client, user)
    url = f"/lage/{lage.id}/einheiten/{einheit.id}/reaktivieren"
    fragment = client.get(f"/lage/{lage.id}/ressourcen/kraefteuebersicht")
    assert fragment.status_code == 200
    assert f'hx-post="{url}"' in fragment.text
    assert ">Reaktivieren</button>" in fragment.text
    response = client.post(url, data={"_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 204
    db.refresh(einheit)
    assert einheit.status == service.STATUS_BEREITGESTELLT
    assert einheit.released_at is None
    assert einheit.sector_id is None and einheit.incident_site_id is None
    assert events == [(lage.id, {"type": "ressource:changed", "einheit_id": einheit.id})]


def test_route_duplikat_liefert_fehlermeldung(client, daten):
    db, lage, einheit, user = daten
    _duplikat(db, einheit)
    db.commit()
    _login(client, user)
    response = client.post(
        f"/lage/{lage.id}/einheiten/{einheit.id}/reaktivieren",
        data={"_csrf": client.cookies.get("ec_csrf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Fahrzeug ist bereits wieder in der Lage"
    db.refresh(einheit)
    assert einheit.status == service.STATUS_ABGERUECKT


@pytest.mark.parametrize("fremde_org", [False, True])
def test_route_fremde_org_oder_andere_lage(client, daten, fremde_org):
    db, lage, einheit, user = daten
    org_id = lage.org_id
    if fremde_org:
        org = FireDept(slug=f"fremd-{uuid4().hex}", name="Fremd", color="#123456", bos="FW")
        db.add(org)
        db.flush()
        org_id = org.id
    andere_lage = MajorIncident(org_id=org_id, name="Andere Lage")
    db.add(andere_lage)
    db.commit()
    _login(client, user)
    for action in ("status", "reaktivieren"):
        response = client.post(
            f"/lage/{andere_lage.id}/einheiten/{einheit.id}/{action}",
            data={"_csrf": client.cookies.get("ec_csrf"), "status": service.STATUS_BEREITGESTELLT},
        )
        assert response.status_code in ({403, 404} if fremde_org else {404})
    db.refresh(einheit)
    assert einheit.status == service.STATUS_ABGERUECKT


@pytest.mark.parametrize("fall", ["readonly", "verband", "verbandskind"])
def test_fragment_ohne_reaktivierung(client, daten, fall):
    db, lage, einheit, user = daten
    if fall == "readonly":
        db.query(UserRole).filter_by(user_id=user.id).one().role_id = db.query(Role).filter_by(code="readonly").one().id
    elif fall == "verband":
        einheit.resource_type = "verband"
    else:
        verband = LageEinheit(lage_id=lage.id, label="Verband", resource_type="verband", status="abgerueckt")
        db.add(verband)
        db.flush()
        einheit.verband_id = verband.id
    db.commit()
    _login(client, user)
    response = client.get(f"/lage/{lage.id}/ressourcen/kraefteuebersicht")
    assert response.status_code == 200
    assert ">Reaktivieren</button>" not in response.text
    if fall == "readonly":
        response = client.post(
            f"/lage/{lage.id}/einheiten/{einheit.id}/reaktivieren",
            data={"_csrf": client.cookies.get("ec_csrf")},
        )
        assert response.status_code == 403
