"""App-Geraete: persoenliche Systemadmins, lesende Details und Audit."""

import pytest
from sqlalchemy import select

from app.admin_mcp.context import admin_session as SessionLocal
from app.models.app_geraet import AppGeraet
from app.models.user import AuditLog
from app.services import app_geraet_auswertung
from tests.test_diagnose_checks import insert
from tests.test_systemdiagnose_ui import BASE, admin_env, cockpit, login  # noqa: F401


@pytest.fixture(autouse=True)
def release(monkeypatch):
    monkeypatch.setattr(app_geraet_auswertung, "soll_version", lambda: "v2026.11.01")


def test_liste_detail_audit_und_keine_schreibroute(client, cockpit):  # noqa: F811
    with SessionLocal() as db:
        ident = insert(
            db,
            "app_geraet",
            user_id=cockpit["id"],
            install_id="ui",
            hersteller="Testhersteller",
            modell="Tablet",
            sdk_int=23,
            app_version="2026.01.01",
            version_code=17,
            build_variant="standard",
            einstellungen={"auto_update": False},
            dienste={"letzter_poll_ok_at": "2026-11-01T12:00:00Z"},
        )
    response = client.get(BASE + "/teil/geraete")
    assert response.status_code == 200
    assert "Testhersteller" in response.text and "veraltet" in response.text
    response = client.get(BASE + f"/geraet/{ident}")
    assert response.status_code == 200, response.text
    assert "Automatische Updates" in response.text and "nein" in response.text
    assert "Einstellungen können nur in der App geändert werden" in response.text
    assert "hx-post" not in response.text and "<form" not in response.text
    assert response.headers["cache-control"] == "no-store"
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "diag.ui.geraet", AuditLog.entity_id == ident))
        db.delete(db.get(AppGeraet, ident))
        db.commit()
    assert client.get(BASE + "/geraet/99999999").status_code == 404
    assert client.get(BASE + "/teil/geraete?sdk_max=kaputt").status_code == 400
    # CSRF kann vor dem Routing ablehnen; es existiert jedenfalls kein Schreib-Handler.
    from app.main import app
    from tests.conftest import flatten_routes

    routes = [r for r in flatten_routes(app.routes) if getattr(r, "path", "").startswith(BASE + "/geraet/")]
    assert routes and all(r.methods == {"GET"} for r in routes)


@pytest.mark.parametrize("role", ["admin", "org_admin", "readonly"])
def test_andere_rollen_403(client, cockpit, role):  # noqa: F811
    from app.models.user import Role, UserRole

    with SessionLocal() as db:
        db.query(UserRole).filter(UserRole.user_id == cockpit["id"]).delete()
        r = db.scalar(select(Role).where(Role.code == role))
        db.add(UserRole(user_id=cockpit["id"], role_id=r.id))
        db.commit()
    assert client.get(BASE + "/teil/geraete").status_code == 403
    assert client.get(BASE + "/geraet/99999999").status_code == 403


def test_link_vom_geraetezugang(client, cockpit):  # noqa: F811
    from uuid import uuid4

    with SessionLocal() as db:
        token = insert(db, "device_token", user_id=cockpit["id"], label="Testzugang", token_hash=uuid4().hex)
        ident = insert(db, "app_geraet", user_id=cockpit["id"], install_id="link", device_token_id=token)
    response = client.get(f"/admin/geraete-login/{token}")
    assert response.status_code == 200
    assert f"/admin/systemdiagnose?geraet={ident}" in response.text
    response = client.get(BASE + f"?geraet={ident}")
    assert f'hx-get="/admin/systemdiagnose/geraet/{ident}"' in response.text
    with SessionLocal() as db:
        db.delete(db.get(AppGeraet, ident))
        db.commit()
