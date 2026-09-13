"""HTTP-Integrationstests fuer Phase 3c der Objekt-Kontaktzuordnungen."""
from __future__ import annotations

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.kontakt import Kontakt, ObjektKontaktFreigabe
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektKontakt
from app.models.user import Role, User, UserRole
from app.services import kontakt_service


@pytest.fixture(autouse=True)
def _kein_login_ratelimit():
    from app.core.rate_limit import limiter

    if limiter is None:
        yield
        return
    vorher = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = vorher


def _rolle(db, code: str) -> Role:
    rolle = db.query(Role).filter(Role.code == code).first()
    assert rolle is not None
    return rolle


def _setup(username: str, nummer: int, rollen: tuple[str, ...] = ("objekt_verwalter",)):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        assert org is not None
        user = User(
            username=username, password_hash=hash_password("Test1234!"),
            display_name=username, org_id=org.id, active=True,
        )
        db.add(user)
        db.flush()
        db.add_all(UserRole(user_id=user.id, role_id=_rolle(db, code).id) for code in rollen)
        system = db.get(SystemSettings, "objekt_module_enabled")
        if system is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            system.value = "true"
        settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
        if settings is None:
            settings = OrgSettings(org_id=org.id)
            db.add(settings)
        settings.objekt_module_enabled = True
        objekt = Objekt(org_id=org.id, nummer=nummer, name=f"Objekt {nummer}", status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(objekt)
        db.commit()
        return org.id, objekt.id
    finally:
        db.close()


def _login(client, username: str) -> str:
    client.cookies.clear()
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    client.post("/login", data={"username": username, "password": "Test1234!", "_csrf": csrf})
    return str(client.cookies.get("ec_csrf"))


def _zentral(org_id: int, name: str = "Zentral Kontakt", nummer: str = "+43664111222") -> Kontakt:
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        return kontakt_service.create_kontakt(
            db, {"typ": "person", "anzeigename": name, "email": "zentral@example.at"},
            [{"nummer": nummer, "label": "Mobil", "sms_eignung": True}], [],
            org_id=org_id, user_id=None,
        )
    finally:
        db.close()


def _zuordnung(db, objekt_id: int) -> ObjektKontakt:
    zuordnung = db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt_id).one()
    return zuordnung


def test_suche_zuordnung_und_doppel_guard(client):
    org_id, objekt_id = _setup("p3c_suche", 93001)
    zentral = _zentral(org_id, "Suchbarer Zentraler")
    csrf = _login(client, "p3c_suche")

    assert "Suchbarer Zentraler" in client.get(f"/objekte/{objekt_id}/kontakte/suche?q=Suchbarer").text
    payload = {"_csrf": csrf, "kontakt_id": zentral.id, "art": "sonstig"}
    assert client.post(f"/objekte/{objekt_id}/kontakte/zuordnen", data=payload).status_code == 200
    assert client.post(f"/objekte/{objekt_id}/kontakte/zuordnen", data=payload).status_code == 400

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zuordnung = _zuordnung(db, objekt_id)
        assert (zuordnung.kontakt_id, zuordnung.art, zuordnung.sort) == (zentral.id, "sonstig", 1)
        assert zuordnung.zentraler_kontakt.anzeigename == "Suchbarer Zentraler"
        assert zuordnung.freigaben == []
    finally:
        db.close()


def test_anlegen_und_zuordnen_legt_zentralen_kontakt_mit_telefonen_an(client):
    org_id, objekt_id = _setup("p3c_anlegen", 93002)
    csrf = _login(client, "p3c_anlegen")
    response = client.post(f"/objekte/{objekt_id}/kontakte/anlegen", data={
        "_csrf": csrf, "art": "sonstig", "typ": "person", "anzeigename": "Neu Zentral",
        "organisation": "FF Test", "nummer": ["+436641", "+436642"],
        "telefon_label": ["Mobil", "Dienst"], "sms_eignung": ["0"],
    })
    assert response.status_code == 200
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        zuordnung = _zuordnung(db, objekt_id)
        zentral = kontakt_service.get_kontakt(db, zuordnung.kontakt_id)
        assert zentral is not None and zentral.anzeigename == "Neu Zentral"
        assert [telefon.nummer for telefon in zentral.telefone] == ["+436641", "+436642"]
    finally:
        db.close()


def test_zuordnung_bearbeiten_freigaben_und_whitelist(client):
    org_id, objekt_id = _setup("p3c_freigaben", 93003)
    zentral = _zentral(org_id, nummer="+43664999999")
    csrf = _login(client, "p3c_freigaben")
    client.post(
        f"/objekte/{objekt_id}/kontakte/zuordnen",
        data={"_csrf": csrf, "kontakt_id": zentral.id, "art": "sonstig"},
    )
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zuordnung_id = _zuordnung(db, objekt_id).id
        nummer = zentral.telefone[0].nummer_normalisiert
    finally:
        db.close()
    url = f"/objekte/{objekt_id}/kontakte/{zuordnung_id}/zuordnung"
    basis = {"_csrf": csrf, "art": "betreiber", "sort": "7", "erreichbarkeit": "Nur nachts"}
    assert client.post(url, data={**basis, "freigabe_sms": [nummer, "bogus"]}).status_code == 200
    assert client.post(url, data=basis).status_code == 200
    assert client.post(url, data={**basis, "freigabe_sms": nummer}).status_code == 200

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        zentral_nachher = kontakt_service.get_kontakt(db, zentral.id)
        assert zentral_nachher is not None
        assert zentral_nachher.anzeigename == zentral.anzeigename
        assert zentral_nachher.email == zentral.email
        assert [t.nummer for t in zentral_nachher.telefone] == [t.nummer for t in zentral.telefone]
        zuordnung = _zuordnung(db, objekt_id)
        assert (zuordnung.art, zuordnung.sort, zuordnung.erreichbarkeit) == ("betreiber", 7, "Nur nachts")
        freigaben = db.query(ObjektKontaktFreigabe).filter(
            ObjektKontaktFreigabe.objekt_kontakt_id == zuordnung.id,
            ObjektKontaktFreigabe.kanal == "sms", ObjektKontaktFreigabe.ziel_wert == nummer,
        ).all()
        assert len(freigaben) == 1 and freigaben[0].aktiv
        assert db.query(ObjektKontaktFreigabe).filter(ObjektKontaktFreigabe.ziel_wert == "bogus").count() == 0
    finally:
        db.close()


def test_nur_zentrale_zuordnungen_sind_bearbeitbar(client):
    org_id, objekt_id = _setup("p3c_legacy", 93004)
    zentral = _zentral(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zuordnung = ObjektKontakt(
            org_id=org_id, objekt_id=objekt_id, kontakt_id=zentral.id, art="sonstig"
        )
        db.add(zuordnung)
        db.commit()
        zuordnung_id = zuordnung.id
    finally:
        db.close()
    csrf = _login(client, "p3c_legacy")
    url = f"/objekte/{objekt_id}/kontakte/{zuordnung_id}/zuordnung"
    assert client.post(url, data={"_csrf": csrf, "art": "betreiber", "sort": "2"}).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        gespeichert = db.get(ObjektKontakt, zuordnung_id)
        assert (gespeichert.kontakt_id, gespeichert.art, gespeichert.sort) == (zentral.id, "betreiber", 2)
    finally:
        db.close()


def test_entfernen_belaesst_zentralen_kontakt_und_zweite_zuordnung(client):
    org_id, objekt_id = _setup("p3c_entfernen", 93005)
    zentral = _zentral(org_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zweites = Objekt(org_id=org_id, nummer=93006, name="Zweites", status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(zweites)
        db.commit()
        zweites_id = zweites.id
    finally:
        db.close()
    csrf = _login(client, "p3c_entfernen")
    for ziel in (objekt_id, zweites_id):
        assert client.post(
            f"/objekte/{ziel}/kontakte/zuordnen",
            data={"_csrf": csrf, "kontakt_id": zentral.id, "art": "sonstig"},
        ).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        erste = _zuordnung(db, objekt_id)
        db.add(ObjektKontaktFreigabe(
            org_id=org_id, objekt_kontakt_id=erste.id, kanal="sms",
            ziel_wert="+43664111222", aktiv=True,
        ))
        db.commit()
        erste_id = erste.id
    finally:
        db.close()
    assert client.post(f"/objekte/{objekt_id}/kontakte/{erste_id}/loeschen", data={"_csrf": csrf}).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        assert kontakt_service.get_kontakt(db, zentral.id) is not None
        assert db.get(ObjektKontakt, erste_id) is None
        assert db.query(ObjektKontaktFreigabe).filter(ObjektKontaktFreigabe.objekt_kontakt_id == erste_id).count() == 0
        assert _zuordnung(db, zweites_id).kontakt_id == zentral.id
    finally:
        db.close()


def test_berechtigungen_und_tenant_isolation(client):
    org_id, objekt_id = _setup("p3c_verwalter", 93007)
    zentral = _zentral(org_id, "Org A Kontakt")
    _setup("p3c_leser", 93008, ("readonly",))
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_b = FireDept(slug="p3c-org-b", name="P3C Org B", color="#123456", bos="FW")
        db.add(org_b)
        db.commit()
        org_b_id = org_b.id
    finally:
        db.close()
    fremd = _zentral(org_b_id, "Fremder Org B Kontakt")

    verwalter_csrf = _login(client, "p3c_verwalter")
    assert client.post(
        f"/objekte/{objekt_id}/kontakte/zuordnen",
        data={"_csrf": verwalter_csrf, "kontakt_id": zentral.id, "art": "sonstig"},
    ).status_code == 200
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        zuordnung_id = _zuordnung(db, objekt_id).id
    finally:
        db.close()

    csrf = _login(client, "p3c_leser")
    assert client.get(f"/objekte/{objekt_id}/kontakte").status_code == 200
    assert client.get(f"/objekte/{objekt_id}/kontakte/suche?q=Org").status_code == 200
    for url in (f"/objekte/{objekt_id}/kontakte/zuordnen", f"/objekte/{objekt_id}/kontakte/anlegen"):
        assert client.post(url, data={"_csrf": csrf, "kontakt_id": zentral.id}).status_code == 403
    assert client.post(
        f"/objekte/{objekt_id}/kontakte/{zuordnung_id}/zuordnung", data={"_csrf": csrf}
    ).status_code == 403
    assert client.post(
        f"/objekte/{objekt_id}/kontakte/{zuordnung_id}/loeschen", data={"_csrf": csrf}
    ).status_code == 403

    csrf = _login(client, "p3c_verwalter")
    assert "Fremder Org B Kontakt" not in client.get(f"/objekte/{objekt_id}/kontakte/suche?q=Fremder").text
    assert client.post(
        f"/objekte/{objekt_id}/kontakte/zuordnen",
        data={"_csrf": csrf, "kontakt_id": fremd.id, "art": "sonstig"},
    ).status_code == 404
