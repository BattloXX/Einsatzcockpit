"""Manuelles Loeschen eines BMA-importierten Kontakts (POST .../kontakte/{id}/loeschen)
muss die Bestaetigung des zugehoerigen BmaImportSatz zuruecksetzen - sonst haelt der
naechste Abgleich (Live-Sync oder Datenblatt-PDF-Upload) das Objekt faelschlich fuer
"bereits auf dem bestaetigten Stand" (bestaetigt_hash vergleicht nur den QUELL-Inhalt,
nicht den Live-Zustand des Objekts) und importiert die geloeschten Kontakte NICHT erneut,
selbst bei identischem erneuten Upload/Sync. Vorfall: Objekt 916 (Hotel Sternen Wolfurt) -
Kontakte manuell geloescht, PDF danach erneut hochgeladen, Ergebnis "unveraendert" statt
eines neuen Vorschlags. Muster: test_objekt_changelog.py (frische Org je Test, _login())."""
import uuid

import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.bma_import import BmaImportSatz
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektKontakt
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
    client.cookies.clear()
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


def _setup_org_mit_objekt_und_kontakt(username: str, extern_quelle: str | None):
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(
            slug=f"kontakt-loeschen-{uuid.uuid4().hex[:8]}", name="Kontakt-Loeschen-Test-Org",
            color="#123456", bos="Feuerwehr",
        )
        db.add(org)
        db.flush()
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Kontakt Loeschen Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=_rolle(db, "objekt_verwalter").id))

        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        db.add(OrgSettings(org_id=org.id, objekt_module_enabled=True))

        objekt = Objekt(org_id=org.id, nummer=916, name="Hotel Sternen Wolfurt",
                        status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(objekt)
        db.flush()
        kontakt = ObjektKontakt(
            org_id=org.id, objekt_id=objekt.id, art="brandschutzbeauftragter",
            name="Justin Winsauer", extern_quelle=extern_quelle,
            extern_id="pdf:1" if extern_quelle else None,
        )
        db.add(kontakt)
        satz = BmaImportSatz(
            org_id=org.id, extern_id="pdf:1332", objekt_id=objekt.id,
            bma_nummer="1332", bezeichnung="Hotel Sternen Wolfurt",
            quell_hash="hash-vom-pdf", bestaetigt_hash="hash-vom-pdf",
        )
        db.add(satz)
        db.commit()
        return org.id, objekt.id, kontakt.id, satz.id
    finally:
        db.close()


def test_loeschen_eines_importierten_kontakts_setzt_bestaetigt_hash_zurueck():
    org_id, objekt_id, kontakt_id, satz_id = _setup_org_mit_objekt_und_kontakt(
        "kontakt_loeschen_bma_user", extern_quelle="dibos_bma",
    )

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "kontakt_loeschen_bma_user", "Test1234!")

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/objekte/{objekt_id}/kontakte/{kontakt_id}/loeschen", data={"_csrf": csrf})
    assert r.status_code == 200
    assert "Keine Kontakte erfasst" in r.text

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        assert db.query(ObjektKontakt).filter(ObjektKontakt.id == kontakt_id).first() is None
        satz = db.query(BmaImportSatz).filter(BmaImportSatz.id == satz_id).one()
        assert satz.bestaetigt_hash is None
        assert satz.quell_hash == "hash-vom-pdf"
    finally:
        db.close()


def test_loeschen_eines_manuellen_kontakts_laesst_bestaetigt_hash_unangetastet():
    """Ein haendisch (nicht importiert) angelegter Kontakt hat mit dem BMA-Import nichts
    zu tun - dessen Loeschung darf keinen bestehenden Vorschlag/Bestaetigungsstand
    beeinflussen."""
    org_id, objekt_id, kontakt_id, satz_id = _setup_org_mit_objekt_und_kontakt(
        "kontakt_loeschen_manuell_user", extern_quelle=None,
    )

    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    _login(client, "kontakt_loeschen_manuell_user", "Test1234!")

    csrf = client.cookies.get("ec_csrf")
    r = client.post(f"/objekte/{objekt_id}/kontakte/{kontakt_id}/loeschen", data={"_csrf": csrf})
    assert r.status_code == 200

    db = SessionLocal()
    set_tenant_context(db, org_id)
    try:
        satz = db.query(BmaImportSatz).filter(BmaImportSatz.id == satz_id).one()
        assert satz.bestaetigt_hash == "hash-vom-pdf"
    finally:
        db.close()
