"""Regressionen fuer die atomare Erfassung einer GSL-Einheit."""
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, MajorIncident
from app.models.master import FireDept, Member, VehicleMaster
from app.models.user import AuditLog, Role, User, UserRole
from app.services import resource_service, ressource_karte_service
from tests.conftest import TestingSession


@contextmanager
def _session():
    db = TestingSession()
    set_tenant_context(db, 1)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def fresh_db(setup_db):
    yield


def _lage(db):
    lage = MajorIncident(name="Neue Einheit", org_id=1, started_at=datetime.now(UTC).replace(tzinfo=None))
    db.add(lage)
    db.flush()
    return lage


def test_minimalfelder_und_audit():
    with _session() as db:
        lage = _lage(db)
        result = resource_service.lege_einheit_an(
            db, lage, resource_type="extern", label="  Bergrettung  ", user_id=7, author_name="EL",
        )
        db.commit()
        assert result.einheit.label == "Bergrettung"
        audit = db.query(AuditLog).filter(AuditLog.action == "gsl.einheit.angelegt").one()
        assert audit.entity_id == result.einheit.id


def test_vehicle_cross_org_and_duplicate_protection():
    with _session() as db:
        lage = _lage(db)
        other_org = FireDept(slug="other-" + uuid4().hex, name="Andere", color="#000", bos="FW")
        db.add(other_org)
        db.flush()
        foreign_vehicle = VehicleMaster(dept_id=other_org.id, code="X", name="Fremd")
        own_vehicle = VehicleMaster(dept_id=1, code="RLF", name="RLF")
        db.add_all((foreign_vehicle, own_vehicle))
        db.flush()
        with pytest.raises(ValueError, match="Organisation der Lage"):
            resource_service.lege_einheit_an(
                db, lage, resource_type="fahrzeug", label="X", vehicle_id=foreign_vehicle.id,
                user_id=1, author_name="EL",
            )
        resource_service.lege_einheit_an(
            db, lage, resource_type="fahrzeug", label="RLF", vehicle_id=own_vehicle.id,
            user_id=1, author_name="EL",
        )
        with pytest.raises(ValueError, match="bereits in der Lage"):
            resource_service.lege_einheit_an(
                db, lage, resource_type="fahrzeug", label="Anders", vehicle_id=own_vehicle.id,
                user_id=1, author_name="EL",
            )
        with pytest.raises(ValueError, match="Bezeichnung, Typ und Organisation"):
            resource_service.lege_einheit_an(
                db, lage, resource_type="fahrzeug", label="rlf", user_id=1, author_name="EL",
            )
        resource_service.lege_einheit_an(
            db, lage, resource_type="extern", label="Helfer", org_name="Org A", user_id=1, author_name="EL",
        )
        resource_service.lege_einheit_an(
            db, lage, resource_type="extern", label="helfer", org_name="Org B", user_id=1, author_name="EL",
        )
        with pytest.raises(ValueError, match="Bezeichnung, Typ und Organisation"):
            resource_service.lege_einheit_an(
                db, lage, resource_type="extern", label="HELFER", org_name="Org A", user_id=1, author_name="EL",
            )


def test_rollback_nach_gk_fehler_hinterlaesst_keine_einheit():
    with _session() as db:
        lage = _lage(db)
        with pytest.raises(ValueError, match="Mitglied nicht gefunden"):
            resource_service.lege_einheit_an(
                db, lage, resource_type="extern", label="Fehler", gk={"member_id": 999999},
                user_id=1, author_name="EL",
            )
        db.rollback()
        assert db.query(LageEinheit).filter(LageEinheit.lage_id == lage.id).count() == 0


def test_vollfelder_sind_ueber_ressourcenkarte_sichtbar():
    with _session() as db:
        lage = _lage(db)
        member = Member(org_id=1, firstname="Anna", lastname="Muster", phone="+4366412345", active=True)
        db.add(member)
        db.flush()
        result = resource_service.lege_einheit_an(
            db, lage, resource_type="extern", label="Sanität", org_name="RK", bos="RK",
            funkrufname="RK 1", bereitstellungsraum="Nord", gk={"member_id": member.id, "modus": "auto"},
            stellvertreter={"person_name": "Max Stellvertreter"},
            personal={"gesamt": 4, "fuehrung": 1, "agt": 1},
            ausstattung=[{"kategorie": "tauchpumpe", "menge": 2}], bemerkung="mit Zelt",
            user_id=3, author_name="EL",
        )
        db.commit()
        card = ressource_karte_service.karte(db, lage, result.einheit)
        assert card["allgemein"]["funkrufname"] == "RK 1"
        assert card["allgemein"]["standort"] == "Nord"
        assert card["gruppenkommandant"]["current"]["name"] == "Anna Muster"
        assert card["gruppenkommandant"]["current"]["phone"] == "+4366412345"
        assert card["gruppenkommandant"]["stellvertreter"]["name"] == "Max Stellvertreter"
        assert result.einheit.staerke_gesamt == 4


def _route_data():
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        suffix = uuid4().hex[:10]
        org = FireDept(slug="create-" + suffix, name="Create", color="#f00", bos="FW")
        db.add(org)
        db.flush()
        user = User(
            username="create-" + suffix, password_hash=hash_password("Test1234!"),
            display_name="EL", org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == "recorder").one().id))
        lage = MajorIncident(org_id=org.id, name="Route")
        db.add(lage)
        db.commit()
        return user.username, lage.id
    finally:
        db.close()


def test_route_bleibt_mit_bisherigen_formularfeldern_kompatibel(client):
    username, lage_id = _route_data()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    assert client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf}).status_code == 200
    response = client.post(
        f"/lage/{lage_id}/einheiten",
        data={
            "_csrf": client.cookies.get("ec_csrf"), "resource_type": "extern", "label": "Altformular",
            "org_name": "RK", "bos": "RK", "qty": "2", "unit": "Fzg",
        },
    )
    assert response.status_code == 204
