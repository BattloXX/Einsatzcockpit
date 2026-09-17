"""Objektverwaltung: Datenpflege-UI-Routen ueber HTTP (Routen-Verdrahtung).

Ergaenzt tests/test_objekt_pflege_ui.py (Service-Ebene) um den HTTP-Pfad:
POST /{id}/pflegeauftrag/einladen. Mailversand wird gemockt (kein echter SMTP-Call
in Tests), Muster: tests/test_objekt_arbeitskopie_routen.py fuer Login/CSRF.
"""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.kontakt import Kontakt
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektKontakt, ObjektPflegeauftrag
from app.models.user import Role, User, UserRole


@pytest.fixture(autouse=True)
def _no_login_ratelimit():
    from app.core.rate_limit import limiter
    if limiter is None:
        yield
        return
    prev = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = prev


def _login(client, username, password):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _rolle(db, code):
    role = db.query(Role).filter(Role.code == code).first()
    if role is None:
        role = Role(code=code, name=code)
        db.add(role)
        db.flush()
    return role


def _setup_objekt_mit_kontakt(username, *, nummer):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Pflege-Routentest", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "objekt_verwalter").id))

        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        os_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if os_row is None:
            os_row = OrgSettings(org_id=org.id)
            db.add(os_row)
        os_row.objekt_module_enabled = True

        obj = Objekt(org_id=org.id, nummer=nummer, name="Pflege-Routen-Test-Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
        kontakt = Kontakt(org_id=org.id, anzeigename="Betreiber Test", email="betreiber@example.test")
        db.add_all([obj, kontakt])
        db.flush()
        db.add(ObjektKontakt(org_id=org.id, objekt_id=obj.id, kontakt_id=kontakt.id, art="betreiber"))
        db.commit()
        return obj.id, kontakt.id
    finally:
        db.close()


def _csrf(client):
    return client.cookies.get("ec_csrf")


def test_pflegeauftrag_einladen_legt_auftrag_an_und_versendet_mail(client, monkeypatch):
    obj_id, kontakt_id = _setup_objekt_mit_kontakt("pflege_einladen_user", nummer=8901)
    _login(client, "pflege_einladen_user", "Test1234!")

    gesendet = []

    async def fake_send(**kwargs):
        gesendet.append(kwargs)

    monkeypatch.setattr("app.routers.ui_objekt.send_pflegeauftrag_einladung", fake_send)

    r = client.post(
        f"/objekte/{obj_id}/pflegeauftrag/einladen",
        data={
            "kontakt_id": str(kontakt_id),
            "gueltig_tage": "60",
            "bereiche": ["stammdaten", "kontakte"],
            "auftrag_text": "Bitte pruefen.",
            "_csrf": _csrf(client),
        },
    )
    assert r.status_code == 200
    assert len(gesendet) == 1
    assert gesendet[0]["to"] == "betreiber@example.test"

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        auftrag = db.query(ObjektPflegeauftrag).filter(ObjektPflegeauftrag.objekt_id == obj_id).first()
        assert auftrag is not None
        assert auftrag.status == "eingeladen"
        assert auftrag.gesendet_am is not None
        assert auftrag.kontakt_id == kontakt_id
    finally:
        db.close()
