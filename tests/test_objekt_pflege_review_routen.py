"""Objektverwaltung: Review/Freigabe-Routen ueber HTTP (Routen-Verdrahtung).

Ergaenzt tests/test_objekt_pflege_review.py (Service-Ebene) um den HTTP-Pfad:
POST /{id}/pflegeauftrag/{auftrag_id}/review/freigeben. Muster: Login/CSRF wie
tests/test_objekt_arbeitskopie_routen.py.
"""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.kontakt import Kontakt
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektKontakt, ObjektPflegeauftrag
from app.models.user import Role, User, UserRole
from app.services.objekt_pflege_service import erstelle_pflegeauftrag


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


def _setup_eingereicht(username, *, nummer):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Review-Routentest", org_id=org.id, active=True)
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

        obj = Objekt(org_id=org.id, nummer=nummer, name="Review-Routen-Test-Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
        kontakt = Kontakt(org_id=org.id, anzeigename="Betreiber Test", email="betreiber-review@example.test")
        db.add_all([obj, kontakt])
        db.flush()
        db.add(ObjektKontakt(org_id=org.id, objekt_id=obj.id, kontakt_id=kontakt.id, art="betreiber"))
        db.flush()
        auftrag, _ = erstelle_pflegeauftrag(db, obj, kontakt, ersteller_id=None, bereiche=["stammdaten"])
        auftrag.status = "eingereicht"
        db.commit()
        return obj.id, auftrag.id
    finally:
        db.close()


def _csrf(client):
    return client.cookies.get("ec_csrf")


def test_freigeben_ohne_offene_posten_setzt_auftrag_frei(client):
    obj_id, auftrag_id = _setup_eingereicht("review_freigeben_user", nummer=8902)
    _login(client, "review_freigeben_user", "Test1234!")

    r = client.get(f"/objekte/{obj_id}/pflegeauftrag/{auftrag_id}/review")
    assert r.status_code == 200
    assert "Datenpflege pr" in r.text

    r = client.post(
        f"/objekte/{obj_id}/pflegeauftrag/{auftrag_id}/review/freigeben",
        data={"_csrf": _csrf(client), "revision_intervall_tage": "365"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        auftrag = db.get(ObjektPflegeauftrag, auftrag_id)
        assert auftrag.status == "freigegeben"
        objekt = db.get(Objekt, obj_id)
        assert objekt.letzte_bestaetigung_am is not None
        assert objekt.revision_datum is not None
    finally:
        db.close()


def test_freigeben_mit_offenem_kontaktvorschlag_ohne_entscheidung_gibt_400(client):
    obj_id, auftrag_id = _setup_eingereicht("review_400_user", nummer=8903)
    _login(client, "review_400_user", "Test1234!")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        import json

        from app.models.objekt import KontaktAenderungsvorschlag

        auftrag = db.get(ObjektPflegeauftrag, auftrag_id)
        db.add(KontaktAenderungsvorschlag(
            org_id=auftrag.org_id, pflegeauftrag_id=auftrag.id, kontakt_id=auftrag.kontakt_id, basis_version=0,
            diff_json=json.dumps({"funktion": {"alt": None, "neu": "Neu"}}),
        ))
        db.commit()
    finally:
        db.close()

    r = client.post(
        f"/objekte/{obj_id}/pflegeauftrag/{auftrag_id}/review/freigeben",
        data={"_csrf": _csrf(client), "revision_intervall_tage": "365"},
    )
    assert r.status_code == 400

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ObjektPflegeauftrag, auftrag_id).status == "eingereicht"
    finally:
        db.close()
