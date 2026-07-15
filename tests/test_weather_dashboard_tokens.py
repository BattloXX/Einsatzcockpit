"""Tests fuer mehrere, beschriftete Wetter-Dashboard-Tokens je Org
(app/routers/ui_settings.py: /settings/wetter/dashboard-token/neu + /loeschen).

Ausgeloest durch einen Support-Fall: eine Org mit bereits vorhandenem Infoscreen-Token
konnte keinen zweiten Token fuer die WordPress-Einbindung erzeugen, weil OrgSettings nur
EINEN Token-Hash speicherte -- ein "Erneuern" haette den Infoscreen sofort invalidiert.
"""
import uuid

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept
from app.models.user import Role, User, UserRole
from app.models.weather import WeatherDashboardToken


def _make_org() -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"wx-tok-{uuid.uuid4().hex[:10]}", name="Test-Org Wetter-Tokens",
                        color="#a4000a", bos="Feuerwehr")
        db.add(org)
        db.commit()
        return org.id
    finally:
        db.close()


def _login(client, org_id: int, username: str, role_code: str = "org_admin"):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name=username, org_id=org_id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == role_code).first()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
    finally:
        db.close()

    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    r = client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf},
                     follow_redirects=False)
    assert r.status_code == 302


def _tokens_for(org_id: int) -> list[WeatherDashboardToken]:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return (
            db.query(WeatherDashboardToken)
            .filter(WeatherDashboardToken.org_id == org_id)
            .order_by(WeatherDashboardToken.created_at.desc())
            .all()
        )
    finally:
        db.close()


def test_erster_token_wird_erzeugt(client, setup_db):
    org_id = _make_org()
    _login(client, org_id, "wx_admin1")

    csrf = client.cookies.get("ec_csrf")
    r = client.post("/admin/settings/wetter/dashboard-token/neu",
                     data={"_csrf": csrf, "label": "Infoscreen Geraetehaus"})
    assert r.status_code == 200, r.text[:300]
    assert "Infoscreen Geraetehaus" in r.text
    assert "wxdb_" in r.text  # frischer Token in der Erfolgs-Box sichtbar

    tokens = _tokens_for(org_id)
    assert len(tokens) == 1
    assert tokens[0].label == "Infoscreen Geraetehaus"


def test_zweiter_token_invalidiert_ersten_nicht(client, setup_db):
    """Regression: genau der urspruengliche Bug -- ein zweiter, anders beschrifteter
    Token (z.B. fuers WordPress-Widget) darf den ersten (z.B. Infoscreen) nicht ersetzen."""
    org_id = _make_org()
    _login(client, org_id, "wx_admin2")
    csrf = client.cookies.get("ec_csrf")

    client.post("/admin/settings/wetter/dashboard-token/neu",
                data={"_csrf": csrf, "label": "Infoscreen Geraetehaus"})
    r2 = client.post("/admin/settings/wetter/dashboard-token/neu",
                      data={"_csrf": csrf, "label": "WordPress-Widget"})
    assert r2.status_code == 200, r2.text[:300]

    tokens = _tokens_for(org_id)
    assert len(tokens) == 2
    labels = {t.label for t in tokens}
    assert labels == {"Infoscreen Geraetehaus", "WordPress-Widget"}
    # beide Token-Hashes muessen unterschiedlich sein
    assert tokens[0].token_hash != tokens[1].token_hash


def test_token_loeschen_entfernt_nur_diesen(client, setup_db):
    org_id = _make_org()
    _login(client, org_id, "wx_admin3")
    csrf = client.cookies.get("ec_csrf")

    client.post("/admin/settings/wetter/dashboard-token/neu",
                data={"_csrf": csrf, "label": "Infoscreen Geraetehaus"})
    client.post("/admin/settings/wetter/dashboard-token/neu",
                data={"_csrf": csrf, "label": "WordPress-Widget"})
    tokens = _tokens_for(org_id)
    assert len(tokens) == 2
    to_delete = next(t for t in tokens if t.label == "Infoscreen Geraetehaus")

    r = client.post(f"/admin/settings/wetter/dashboard-token/{to_delete.id}/loeschen",
                     data={"_csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303

    remaining = _tokens_for(org_id)
    assert len(remaining) == 1
    assert remaining[0].label == "WordPress-Widget"


def test_fremde_org_kann_token_nicht_loeschen(client, setup_db):
    org_a = _make_org()
    org_b = _make_org()
    _login(client, org_a, "wx_admin4a")
    csrf = client.cookies.get("ec_csrf")
    client.post("/admin/settings/wetter/dashboard-token/neu",
                data={"_csrf": csrf, "label": "Org A Token"})
    tok_a = _tokens_for(org_a)[0]

    client.get("/logout")
    _login(client, org_b, "wx_admin4b")
    csrf_b = client.cookies.get("ec_csrf")
    client.post(f"/admin/settings/wetter/dashboard-token/{tok_a.id}/loeschen",
                data={"_csrf": csrf_b}, follow_redirects=False)

    # Token von Org A muss trotz Loesch-Versuch durch Org B unangetastet bleiben
    assert len(_tokens_for(org_a)) == 1
