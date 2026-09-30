"""HTTP-Regressionen fuer die MCP-Freigabe im Objekt-Dokumente-Tab."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, OrgStorageUsage, SystemSettings
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektChange,
    ObjektDokument,
    ObjektDokumentSeite,
)
from app.models.user import AuditLog, Role, User, UserRole


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
    client.get("/login")
    return client.post("/login", data={
        "username": username, "password": "Test1234!", "_csrf": client.cookies.get("ec_csrf"),
    }, follow_redirects=False)


def _setup(username, rolle="objekt_verwalter", *, nummer=9711):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.query(FireDept).first()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name=f"{rolle} Tester", org_id=org.id, active=True)
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
        objekt = Objekt(org_id=org.id, nummer=nummer, name="Wartende Dokumente",
                        status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(objekt)
        db.commit()
        return org.id, user.id, objekt.id
    finally:
        db.close()


def _dokument(db, org_id, objekt_id, name, *, gruppe=None, version=1, pflegeauftrag_id=None):
    pfad = f"wartend-ui/{objekt_id}/{name}/original.pdf"
    dokument = ObjektDokument(
        org_id=org_id, objekt_id=objekt_id, dateiname_original=name, pfad=pfad,
        groesse_bytes=100, belegt_bytes=100, seitenzahl=1, status="fertig",
        dokument_gruppe_id=gruppe, versionsnummer=version,
        freigabe_status="wartet_freigabe", ist_aktuelle_version=False,
        pflegeauftrag_id=pflegeauftrag_id,
    )
    db.add(dokument)
    db.flush()
    db.add(ObjektDokumentSeite(
        org_id=org_id, objekt_id=objekt_id, dokument_id=dokument.id, seiten_nr=1,
        dokumentart="lageplan", titel="Erdgeschoss",
    ))
    Path(settings.OBJEKT_MEDIA_DIR, pfad).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.OBJEKT_MEDIA_DIR, pfad).write_bytes(b"pdf")
    return dokument


def _wartendes_dokument(org_id, objekt_id, **kwargs):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        usage = db.get(OrgStorageUsage, org_id)
        if usage is None:
            db.add(OrgStorageUsage(org_id=org_id, used_bytes=100))
        else:
            usage.used_bytes += 100
        dokument = _dokument(db, org_id, objekt_id, "wartend.pdf", **kwargs)
        db.commit()
        return dokument.id, dokument.pfad
    finally:
        db.close()


def test_block_ist_nur_fuer_objekt_verwalter_und_org_admin_sichtbar(client):
    org_id, _user_id, objekt_id = _setup("wartend_verwalter", nummer=9711)
    _wartendes_dokument(org_id, objekt_id)
    _login(client, "wartend_verwalter")
    response = client.get(f"/objekte/{objekt_id}/dokumente")
    assert response.status_code == 200
    assert "Wartende Dokumente (aus KI-Verbindung)" in response.text
    assert "wartend.pdf" in response.text and "Erdgeschoss" in response.text
    assert 'name="_csrf"' in response.text

    for i, rolle in enumerate(("org_admin", "readonly", "recorder", "fahrtenbuch_admin"), start=1):
        _setup(f"wartend_{rolle}", rolle, nummer=9711 + i)
        _login(client, f"wartend_{rolle}")
        response = client.get(f"/objekte/{objekt_id}/dokumente")
        if rolle == "org_admin":
            assert "Wartende Dokumente (aus KI-Verbindung)" in response.text
        else:
            assert "Wartende Dokumente (aus KI-Verbindung)" not in response.text
            csrf = client.cookies.get("ec_csrf")
            assert client.post(
                f"/objekte/{objekt_id}/dokumente/wartend/1/freigeben", data={"_csrf": csrf},
            ).status_code == 403


def test_block_fehlt_ohne_wartende_dokumente(client):
    _org_id, _user_id, objekt_id = _setup("wartend_leer", nummer=9720)
    _login(client, "wartend_leer")
    assert "Wartende Dokumente (aus KI-Verbindung)" not in client.get(
        f"/objekte/{objekt_id}/dokumente"
    ).text


def test_freigeben_zeigt_neue_version_in_galerie_und_audit(client):
    org_id, _user_id, objekt_id = _setup("wartend_freigeben", nummer=9721)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        alt = ObjektDokument(org_id=org_id, objekt_id=objekt_id, dateiname_original="alt.pdf",
                             pfad="wartend-ui/alt.pdf", status="fertig", seitenzahl=1,
                             freigabe_status="freigegeben", ist_aktuelle_version=True)
        db.add(alt)
        db.flush()
        neu = _dokument(db, org_id, objekt_id, "neu.pdf", gruppe=alt.id, version=2)
        db.commit()
        neu_id, alt_id = neu.id, alt.id
    finally:
        db.close()
    _login(client, "wartend_freigeben")
    response = client.post(f"/objekte/{objekt_id}/dokumente/wartend/{neu_id}/freigeben",
                           data={"_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 200
    assert "Wartende Dokumente (aus KI-Verbindung)" not in response.text
    assert "neu.pdf" in response.text
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ObjektDokument, neu_id).ist_aktuelle_version is True
        assert db.get(ObjektDokument, alt_id).freigabe_status == "archiviert"
        assert db.query(AuditLog).filter_by(action="objekt.dokument_freigegeben", entity_id=objekt_id).count() == 1
        assert db.query(ObjektChange).filter_by(objekt_id=objekt_id, feld="dokument_freigegeben").count() == 1
    finally:
        db.close()


def test_verwerfen_raeumt_dateien_und_quota_auf(client):
    org_id, _user_id, objekt_id = _setup("wartend_verwerfen", nummer=9722)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        vorher = (db.get(OrgStorageUsage, org_id).used_bytes
                  if db.get(OrgStorageUsage, org_id) else 0)
    finally:
        db.close()
    dokument_id, pfad = _wartendes_dokument(org_id, objekt_id)
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ObjektDokument, dokument_id).belegt_bytes == 100
        assert db.get(OrgStorageUsage, org_id).used_bytes == vorher + 100
    finally:
        db.close()
    _login(client, "wartend_verwerfen")
    response = client.post(f"/objekte/{objekt_id}/dokumente/wartend/{dokument_id}/verwerfen",
                           data={"_csrf": client.cookies.get("ec_csrf")})
    assert response.status_code == 200
    assert not Path(settings.OBJEKT_MEDIA_DIR, pfad).parent.exists()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.get(ObjektDokument, dokument_id).freigabe_status == "verworfen"
        assert db.get(OrgStorageUsage, org_id).used_bytes == vorher
        assert db.query(AuditLog).filter_by(action="objekt.dokument_verworfen", entity_id=objekt_id).count() == 1
    finally:
        db.close()


def test_fremde_und_pflegeauftrag_dokumente_sind_nicht_freigebbar_und_csrf_ist_pflicht(client):
    org_id, _user_id, objekt_id = _setup("wartend_schutz", nummer=9723)
    dokument_id, _ = _wartendes_dokument(org_id, objekt_id)
    _login(client, "wartend_schutz")
    url = f"/objekte/{objekt_id}/dokumente/wartend/{dokument_id}/freigeben"
    assert client.post(url).status_code == 403
    csrf = client.cookies.get("ec_csrf")

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        fremde_org = FireDept(slug="wartend-fremd", name="Fremde Org", color="#123456", bos="Feuerwehr")
        fremdes_objekt = Objekt(
            org_id=fremde_org.id, nummer=9724, name="Anderes Objekt",
            status=OBJEKT_STATUS_FREIGEGEBEN,
        )
        db.add_all([fremde_org, fremdes_objekt])
        db.flush()
        fremd = _dokument(db, fremde_org.id, fremdes_objekt.id, "fremd.pdf")
        pflege = _dokument(db, org_id, objekt_id, "pflege.pdf", pflegeauftrag_id=999999)
        db.commit()
        fremd_id, pflege_id = fremd.id, pflege.id
    finally:
        db.close()
    assert client.post(
        f"/objekte/{objekt_id}/dokumente/wartend/{fremd_id}/freigeben", data={"_csrf": csrf},
    ).status_code == 404
    assert client.post(
        f"/objekte/{objekt_id}/dokumente/wartend/{pflege_id}/freigeben", data={"_csrf": csrf},
    ).status_code == 404
    assert client.post(url, data={"_csrf": csrf}).status_code == 200
    assert client.post(url, data={"_csrf": csrf}).status_code in (400, 404)
