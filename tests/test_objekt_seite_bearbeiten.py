"""HTTP-Regressionen fuer die Einzelbearbeitung von Objekt-Dokumentseiten."""
from __future__ import annotations

import datetime as dt

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektDokument, ObjektDokumentSeite
from app.models.user import Role, User, UserRole


@pytest.fixture(autouse=True)
def _no_login_ratelimit():
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


def _rolle(db, code):
    rolle = db.query(Role).filter(Role.code == code).first()
    if rolle is None:
        rolle = Role(code=code, label=code)
        db.add(rolle)
        db.flush()
    return rolle


def _login(client, username):
    client.cookies.clear()
    client.get("/login")
    client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)
    return client.cookies.get("ec_csrf")


def _objekt_mit_seite(db, org_id, nummer):
    objekt = Objekt(org_id=org_id, nummer=nummer, name=f"Bearbeiten {nummer}",
                    status=OBJEKT_STATUS_FREIGEGEBEN)
    db.add(objekt)
    db.flush()
    dokument = ObjektDokument(
        org_id=org_id, objekt_id=objekt.id, dateiname_original="plan.pdf",
        pfad=f"bearbeiten/{objekt.id}/plan.pdf", groesse_bytes=100, belegt_bytes=100,
        seitenzahl=1, status="fertig", ist_aktuelle_version=True,
    )
    db.add(dokument)
    db.flush()
    seite = ObjektDokumentSeite(
        org_id=org_id, objekt_id=objekt.id, dokument_id=dokument.id, seiten_nr=1,
        dokumentart="lageplan", titel="Alter Titel", melderlinien="12",
        stand=dt.date(2020, 3, 1), bei_einsatz_drucken=True,
    )
    db.add(seite)
    db.flush()
    return objekt.id, seite.id


def _setup(username, rolle="objekt_verwalter", nummer=9811):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name=username, org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, rolle).id))
        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        org_row = db.query(OrgSettings).filter_by(org_id=org.id).first()
        if org_row is None:
            org_row = OrgSettings(org_id=org.id)
            db.add(org_row)
        org_row.objekt_module_enabled = True
        objekt_id, seite_id = _objekt_mit_seite(db, org.id, nummer)
        db.commit()
        return org.id, objekt_id, seite_id
    finally:
        db.close()


def _seite(seite_id):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        return db.get(ObjektDokumentSeite, seite_id)
    finally:
        db.close()


def test_galerie_und_viewer_zeigen_vorausgefuelltes_formular(client):
    _org, objekt_id, seite_id = _setup("seite_edit_prefill", nummer=9811)
    _login(client, "seite_edit_prefill")
    galerie = client.get(f"/objekte/{objekt_id}/dokumente")
    assert galerie.status_code == 200
    assert f"/dokumente/seite/{seite_id}/bearbeiten" in galerie.text
    assert 'value="Alter Titel"' in galerie.text
    assert 'value="2020-03-01"' in galerie.text
    viewer = client.get(f"/objekte/{objekt_id}/dokumente/viewer?seite={seite_id}")
    assert viewer.status_code == 200
    assert f"viewerSeiteBearbeiten{seite_id}" in viewer.text


def test_einzelbearbeitung_leert_felder(client):
    _org, objekt_id, seite_id = _setup("seite_edit_leeren", nummer=9812)
    csrf = _login(client, "seite_edit_leeren")
    r = client.post(f"/objekte/{objekt_id}/dokumente/seite/{seite_id}/bearbeiten", data={
        "_csrf": csrf, "dokumentart": "lageplan", "titel": "", "melderlinien": "", "stand": "",
    })
    assert r.status_code == 200
    seite = _seite(seite_id)
    assert seite.titel is None
    assert seite.melderlinien is None
    assert seite.stand is None
    assert seite.bei_einsatz_drucken is False

    r = client.post(f"/objekte/{objekt_id}/dokumente/seite/{seite_id}/bearbeiten", data={
        "_csrf": csrf, "dokumentart": "lageplan", "titel": "Neu", "stand": "2026-10-09",
        "bei_einsatz_drucken": "1", "viewer": "1",
    })
    assert r.status_code == 200
    assert 'id="objekt-viewer-info"' in r.text
    seite = _seite(seite_id)
    assert seite.titel == "Neu"
    assert seite.stand == dt.date(2026, 10, 9)
    assert seite.bei_einsatz_drucken is True


def test_bulk_ohne_einsatzdruck_erhaelt_markierung(client):
    _org, objekt_id, seite_id = _setup("seite_bulk_flag", nummer=9813)
    csrf = _login(client, "seite_bulk_flag")
    r = client.post(f"/objekte/{objekt_id}/dokumente/seiten/bulk", data={
        "_csrf": csrf, "seiten_ids": str(seite_id), "titel": "Nur Titel", "bei_einsatz_drucken": "",
    })
    assert r.status_code == 200
    seite = _seite(seite_id)
    assert seite.titel == "Nur Titel"
    assert seite.bei_einsatz_drucken is True

    client.post(f"/objekte/{objekt_id}/dokumente/seiten/bulk", data={
        "_csrf": csrf, "seiten_ids": str(seite_id), "bei_einsatz_drucken": "0",
    })
    assert _seite(seite_id).bei_einsatz_drucken is False


def test_ungueltige_eingaben_und_fremde_seite(client):
    org_id, objekt_id, seite_id = _setup("seite_edit_fremd", nummer=9814)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        anderes_objekt_id, andere_seite_id = _objekt_mit_seite(db, org_id, 9815)
        fremd = FireDept(slug="seite-edit-fremd", name="Fremd", color="#00ff00", bos="Feuerwehr")
        db.add(fremd)
        db.flush()
        _fremd_objekt_id, fremde_seite_id = _objekt_mit_seite(db, fremd.id, 9816)
        db.commit()
    finally:
        db.close()
    csrf = _login(client, "seite_edit_fremd")
    url = f"/objekte/{objekt_id}/dokumente/seite/{{}}/bearbeiten"
    # Seite eines anderen Objekts bzw. einer fremden Org ueber dieses Objekt
    assert client.post(url.format(andere_seite_id), data={"_csrf": csrf}).status_code == 404
    assert client.post(url.format(fremde_seite_id), data={"_csrf": csrf}).status_code == 404
    assert _seite(andere_seite_id).titel == "Alter Titel"
    assert _seite(fremde_seite_id).titel == "Alter Titel"
    # Validierung
    assert client.post(url.format(seite_id), data={"_csrf": csrf, "dokumentart": "gibtsnicht"}).status_code == 400
    assert client.post(url.format(seite_id), data={"_csrf": csrf, "stand": "09.10.2026"}).status_code == 400
    assert anderes_objekt_id != objekt_id


def test_ohne_verwalterrolle_verboten(client):
    _org, objekt_id, seite_id = _setup("seite_edit_lesend", rolle="readonly", nummer=9817)
    csrf = _login(client, "seite_edit_lesend")
    r = client.post(f"/objekte/{objekt_id}/dokumente/seite/{seite_id}/bearbeiten",
                    data={"_csrf": csrf, "titel": ""})
    assert r.status_code == 403
    assert _seite(seite_id).titel == "Alter Titel"
