"""Gefahren: weiterfuehrende Links + Gefahrgut-DB-Anreicherung (Phase 2)."""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    GefahrenKatalog,
    Objekt,
    ObjektGefahr,
)
from app.models.user import Role, User, UserRole
from app.services.gefahrgut_service import _norm_un, generierte_links, lookup_un
from app.services.objekt_service import gefahr_links, links_aus_form


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


def test_gefahrgut_lookup_und_normierung():
    assert _norm_un("UN 1203") == "1203"
    assert _norm_un("0001203") == "1203"
    treffer = lookup_un("1203")
    assert treffer and treffer["stoffname"].startswith("Benzin")
    assert treffer["gefahrnummer"] == "33"
    assert lookup_un("999999") is None


def test_generierte_links():
    links = generierte_links("1203", "Benzin")
    urls = [x["url"] for x in links]
    assert any("dgg.bam.de" in u and "UN1203" in u for u in urls)
    assert any("gestis" in u for u in urls)


def test_links_aus_form_und_validierung():
    js = links_aus_form(["Datenblatt", "", "Ohne Schema"], ["https://a.example", "", "b.example"])
    import json
    daten = json.loads(js)
    # Leere URL entfaellt; fehlendes Schema wird zu https:// ergaenzt
    assert len(daten) == 2
    assert daten[0]["url"] == "https://a.example"
    assert daten[1]["url"] == "https://b.example"


def test_gefahr_links_merge_dedup():
    kat = GefahrenKatalog(name="Benzin", piktogramm_typ="gas",
                          links_json='[{"label":"Katalog","url":"https://kat.example"}]')
    og = ObjektGefahr(un_nummer="1203", stoffname="Benzin",
                      links_json='[{"label":"Objekt","url":"https://obj.example"},'
                                 '{"label":"Dup","url":"https://kat.example"}]')
    og.gefahr = kat
    links = gefahr_links(og)
    urls = [x["url"] for x in links]
    assert "https://kat.example" in urls
    assert "https://obj.example" in urls
    assert urls.count("https://kat.example") == 1  # dedup
    assert any("dgg.bam.de" in u for u in urls)  # generierter DB-Link


def _login(client, username, password):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


def _setup(username, nummer):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Gefahr Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        role = db.query(Role).filter(Role.code == "objekt_verwalter").first()
        if role is None:
            role = Role(code="objekt_verwalter", name="objekt_verwalter")
            db.add(role)
            db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
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
        kat = GefahrenKatalog(org_id=org.id, name="Gasanschluss", piktogramm_typ="gas", aktiv=True)
        db.add(kat)
        obj = Objekt(org_id=org.id, nummer=nummer, name="Gefahr-Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(obj)
        db.commit()
        return org.id, obj.id, kat.id
    finally:
        db.close()


def test_lookup_endpoint_und_gefahr_neu(client):
    org_id, obj_id, kat_id = _setup("gef_user", nummer=7001)
    _login(client, "gef_user", "Test1234!")

    # Anreicherungs-Endpunkt
    r = client.get("/objekte/gefahrgut/lookup?un=1203")
    assert r.status_code == 200
    d = r.json()
    assert d["gefunden"] and d["stoffname"].startswith("Benzin")

    # Gefahr mit Anreicherung + Link anlegen
    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/objekte/{obj_id}/gefahren/neu", data={
        "_csrf": csrf, "gefahr_id": kat_id, "un_nummer": "1203",
        "stoffname": "Benzin", "gefahrklasse": "3", "gefahrnummer": "33",
        "detail": "Tank im UG", "link_label": "Datenblatt", "link_url": "https://firma.example/sdb",
    }, follow_redirects=False)
    assert r.status_code == 200, r.text[:300]

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        og = db.query(ObjektGefahr).filter(ObjektGefahr.objekt_id == obj_id).first()
        assert og is not None
        assert og.stoffname == "Benzin" and og.gefahrnummer == "33"
        links = gefahr_links(og)
        urls = [x["url"] for x in links]
        assert "https://firma.example/sdb" in urls          # manueller Link
        assert any("dgg.bam.de" in u for u in urls)          # generierter DB-Link
    finally:
        db.close()
