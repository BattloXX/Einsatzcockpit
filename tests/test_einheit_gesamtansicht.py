"""E1: Einheit-Tablets duerfen die Fuehrungsansicht nur lesen."""
from uuid import uuid4

import pytest

from app.core.security import hash_api_key, hash_password, sign_session
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.main import app
from app.models.major_incident import IncidentSite, MajorIncident, MajorIncidentStatus, SitePhase
from app.models.master import FireDept
from app.models.user import DeviceToken, Role, User, UserRole
from app.services.einheit_service import EINHEIT_GESAMTANSICHT_ROUTEN
from tests.conftest import flatten_routes


def _anlegen(profil: str | None, *, geraet: bool) -> tuple[int, int, int, int]:
    """Legt eine Lage samt Recorder und optionalem Geraet an."""
    suffix = uuid4().hex[:10]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"einheit-e1-{suffix}", name=f"Einheit E1 {suffix}")
        db.add(org)
        db.flush()
        user = User(
            username=f"einheit-e1-{suffix}", password_hash=hash_password("Test1234!"),
            display_name="Einheit E1", org_id=org.id, active=True, is_device=geraet,
        )
        db.add(user)
        db.flush()
        recorder = db.query(Role).filter(Role.code == "recorder").first()
        assert recorder is not None
        db.add(UserRole(user_id=user.id, role_id=recorder.id))
        lage = MajorIncident(
            org_id=org.id, name="E1 Testlage", status=MajorIncidentStatus.active,
            public_token=f"melden-{suffix}",
        )
        db.add(lage)
        db.flush()
        site = IncidentSite(
            major_incident_id=lage.id, org_id=org.id, bezeichnung="E1 Teststelle",
            phase=SitePhase.eingegangen,
        )
        db.add(site)
        token = DeviceToken(
            user_id=user.id, label="E1 Tablet", token_hash=hash_api_key(f"e1-{suffix}"),
            gsl_profil=profil,
        )
        db.add(token)
        db.commit()
        return user.id, token.id, lage.id, site.id
    finally:
        db.close()


def _als_geraet(client, user_id: int, token_id: int) -> None:
    client.cookies.set("session", sign_session(user_id, device=True, device_token_id=token_id))


def test_einheit_geraet_darf_gesamtansicht_lesen_aber_nicht_bedienen(client, setup_db):
    user_id, token_id, lage_id, site_id = _anlegen("einheit", geraet=True)
    _als_geraet(client, user_id, token_id)

    board = client.get(f"/lage/{lage_id}", follow_redirects=False)
    assert board.status_code == 200
    assert "Nur-Lese-Ansicht (Einheitenmodus)" in board.text
    assert f'hx-post="/lage/{lage_id}/stellen/neu"' not in board.text

    assert client.get(f"/lage/{lage_id}/stellen/{site_id}", follow_redirects=False).status_code == 200
    csrf = client.cookies.get("ec_csrf")
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    assert client.post(
        f"/lage/{lage_id}/stellen/{site_id}/phase", data={"phase": "erkundung"}, headers=headers,
    ).status_code == 403
    assert client.post(
        f"/lage/{lage_id}/stellen/{site_id}/einheit-disponieren", headers=headers,
    ).status_code == 403
    assert client.post(f"/lage/{lage_id}/beenden", headers=headers).status_code == 403
    assert client.get(f"/lage/{lage_id}/qr", follow_redirects=False).status_code == 403
    assert client.post(f"/lage/{lage_id}/funkjournal", headers=headers).status_code == 403


@pytest.mark.parametrize("profil", ["fuehrung", None])
def test_andere_geraeteprofile_behalten_bisherigen_schreibzugriff(client, setup_db, profil):
    user_id, token_id, lage_id, site_id = _anlegen(profil, geraet=True)
    _als_geraet(client, user_id, token_id)
    client.get(f"/lage/{lage_id}")
    response = client.post(
        f"/lage/{lage_id}/stellen/{site_id}/phase", data={"phase": "erkundung"},
        headers={"X-CSRF-Token": client.cookies.get("ec_csrf")},
    )
    assert response.status_code == 204


def test_normaler_benutzer_bleibt_unveraendert(client, setup_db):
    user_id, token_id, lage_id, site_id = _anlegen(None, geraet=False)
    client.cookies.set("session", sign_session(user_id))
    client.get(f"/lage/{lage_id}")
    response = client.post(
        f"/lage/{lage_id}/stellen/{site_id}/phase", data={"phase": "erkundung"},
        headers={"X-CSRF-Token": client.cookies.get("ec_csrf")},
    )
    assert response.status_code == 204


def test_oeffentliches_meldeportal_bleibt_ohne_login_erreichbar(client, setup_db):
    _user_id, _token_id, lage_id, _site_id = _anlegen(None, geraet=False)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        lage = db.get(MajorIncident, lage_id)
        assert lage is not None
        token = lage.public_token
    finally:
        db.close()
    client.cookies.clear()
    assert client.get(f"/melden/{token}", follow_redirects=False).status_code == 200


def test_allowlist_verweist_auf_bestehende_get_routen():
    module_namen = {
        "app.routers.ui_major_incident",
        "app.routers.ui_gsl_staff",
        "app.routers.ui_lagedokument",
    }
    routen = [
        route for route in flatten_routes(app.routes)
        if getattr(getattr(route, "endpoint", None), "__module__", None) in module_namen
    ]
    nach_name = {route.endpoint.__name__: route for route in routen}
    fehlend = EINHEIT_GESAMTANSICHT_ROUTEN - nach_name.keys()
    assert not fehlend, f"Allowlist ohne Route: {sorted(fehlend)}"
    nicht_get = [name for name in EINHEIT_GESAMTANSICHT_ROUTEN if "GET" not in nach_name[name].methods]
    assert not nicht_get, f"Allowlist ohne GET: {sorted(nicht_get)}"
