"""HTTP-Regressionen für Verband-Aktionen der Ressourcenkarte."""

from uuid import uuid4

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.major_incident import LageEinheit, MajorIncident
from app.models.master import FireDept
from app.models.user import Role, User, UserRole


def _daten(role_code: str = "recorder") -> tuple[str, int, int, int]:
    suffix = uuid4().hex
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"verband-route-{suffix}", name="Verband", color="#123456", bos="FW")
        db.add(org)
        db.flush()
        user = User(
            username=f"verband-route-{suffix}",
            password_hash=hash_password("Test1234!"),
            display_name="EL",
            org_id=org.id,
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=db.query(Role).filter(Role.code == role_code).one().id))
        lage = MajorIncident(org_id=org.id, name="Lage")
        db.add(lage)
        db.flush()
        a = LageEinheit(lage_id=lage.id, label="RLF", status="bereitgestellt", staerke_gesamt=4)
        b = LageEinheit(lage_id=lage.id, label="TLF", status="bereitgestellt", staerke_gesamt=3)
        db.add_all([a, b])
        db.commit()
        return user.username, lage.id, a.id, b.id
    finally:
        db.close()


def _login(client, username: str) -> None:
    client.get("/login")
    response = client.post(
        "/login", data={"username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf")}
    )
    assert response.status_code == 200


def _basis(lage_id: int, einheit_id: int) -> str:
    return f"/lage/{lage_id}/einheiten/{einheit_id}"


def test_verband_happy_path_bilden_aufteilen_aufloesen(client, setup_db):
    username, lage_id, a_id, b_id = _daten()
    _login(client, username)
    csrf = client.cookies.get("ec_csrf")
    response = client.post(
        _basis(lage_id, a_id) + "/verband/bilden",
        data={"_csrf": csrf, "label": "Verband Nord", "einheit_ids": [a_id, b_id]},
    )
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        verband = db.query(LageEinheit).filter_by(lage_id=lage_id, resource_type="verband").one()
        kinder = db.query(LageEinheit).filter(LageEinheit.id.in_([a_id, b_id]))
        assert {row.verband_id for row in kinder} == {verband.id}
        verband_id = verband.id
    finally:
        db.close()
    response = client.post(_basis(lage_id, verband_id) + "/verband/aufloesen", data={"_csrf": csrf})
    assert response.status_code == 200
    response = client.post(
        _basis(lage_id, a_id) + "/verband/aufteilen",
        data={"_csrf": csrf, "teil_label": "RLF Teil", "teil_typ": "fahrzeug", "teil_personal": "2"},
    )
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        teil = db.query(LageEinheit).filter_by(lage_id=lage_id, label="RLF Teil").one()
        assert teil.aufgeteilt_von_id == a_id and teil.staerke_gesamt == 2
    finally:
        db.close()


def test_verband_route_fremde_org_ist_404(client, setup_db):
    username, _, _, _ = _daten()
    _, fremde_lage, fremde_einheit, _ = _daten()
    _login(client, username)
    response = client.post(
        _basis(fremde_lage, fremde_einheit) + "/verband/aufteilen",
        data={"_csrf": client.cookies.get("ec_csrf"), "teil_label": "X", "teil_personal": "0"},
    )
    assert response.status_code == 404


def test_verband_formular_listet_nur_freie_einheiten_der_lage(client, setup_db):
    username, lage_id, a_id, _ = _daten()
    fremdes_label = f"Fremde Einheit {uuid4().hex}"
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, lage_id)
        assert lage is not None
        andere_lage = MajorIncident(org_id=lage.org_id, name="Andere Lage")
        db.add(andere_lage)
        db.flush()
        db.add(LageEinheit(lage_id=andere_lage.id, label=fremdes_label, status="bereitgestellt"))
        db.commit()
    finally:
        db.close()
    _login(client, username)
    response = client.get(_basis(lage_id, a_id) + "/karte/uebersicht")
    assert response.status_code == 200
    assert fremdes_label not in response.text


def test_verband_route_berechtigung_und_gk_zugang_gesperrt(client, setup_db):
    username, lage_id, a_id, b_id = _daten("readonly")
    _login(client, username)
    response = client.post(
        _basis(lage_id, a_id) + "/verband/bilden",
        data={"_csrf": client.cookies.get("ec_csrf"), "label": "V", "einheit_ids": [a_id, b_id]},
    )
    assert response.status_code == 403

    from types import SimpleNamespace

    from app.routers.ui_ressourcenkarte import _zugang_erlaubt

    gk_request = SimpleNamespace(state=SimpleNamespace(is_device=True, qr_lage_id=None, qr_incident_id=None))
    gk_user = SimpleNamespace(roles=[SimpleNamespace(code="recorder")], gsl_nur_lesen=False)
    assert _zugang_erlaubt(gk_request, gk_user) is False
