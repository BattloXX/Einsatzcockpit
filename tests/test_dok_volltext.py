"""Objektdokumente: Volltext-Indexierung + Suche (Phase 1)."""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import (
    OBJEKT_STATUS_FREIGEGEBEN,
    Objekt,
    ObjektDokument,
    ObjektDokumentSeite,
)
from app.models.user import Role, User, UserRole
from app.services.objekt_dokument_service import extrahiere_seitentext


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


class _FakePage:
    def __init__(self, text):
        self._t = text

    def extract_text(self):
        return self._t


def test_extraktion_pdf_textlayer():
    text, quelle = extrahiere_seitentext(_FakePage("Raum 12, Melderlinie 5, Heizraum UG"), None)
    assert quelle == "pdf"
    assert "Melderlinie 5" in text


def test_extraktion_ocr_fallback():
    # Kein Textlayer, aber Seitenbild + OCR-Func → OCR
    text, quelle = extrahiere_seitentext(
        _FakePage(""), b"PNGDATA", ocr_func=lambda png: "Tanklager Halle 3 Gefahrgut UN1203")
    assert quelle == "ocr"
    assert "Tanklager" in text


def test_extraktion_keine_daten():
    text, quelle = extrahiere_seitentext(_FakePage(""), None, ocr_func=lambda p: "")
    assert quelle == "none" and text is None


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
                    display_name="Dok Test", org_id=org.id, active=True)
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
        obj = Objekt(org_id=org.id, nummer=nummer, name="Dok-Objekt",
                     status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(obj)
        db.flush()
        dok = ObjektDokument(org_id=org.id, objekt_id=obj.id, dateiname_original="plan.pdf",
                             pfad=f"{org.id}/{obj.id}/x/original.pdf", mime="application/pdf",
                             groesse_bytes=1, seitenzahl=1, status="fertig",
                             hochgeladen_von_id=user.id)
        db.add(dok)
        db.flush()
        db.add(ObjektDokumentSeite(
            org_id=org.id, objekt_id=obj.id, dokument_id=dok.id, seiten_nr=1,
            volltext="Zufahrt Nord, Heizraum im Kellergeschoss, Melderlinie 7",
            text_quelle="pdf"))
        db.commit()
        return org.id, obj.id
    finally:
        db.close()


def test_galerie_und_einsatz_suche_findet_volltext(client):
    _, obj_id = _setup("dok_such_user", nummer=6001)
    _login(client, "dok_such_user", "Test1234!")

    # Objektverwaltungs-Galerie: Volltext-Filter
    r = client.get(f"/objekte/{obj_id}/dokumente?suche=Heizraum")
    assert r.status_code == 200, r.text[:300]
    assert "Seite 1" in r.text or "seite=" in r.text

    # Einsatzinfo-Suche (JSON)
    r = client.get(f"/objekte/{obj_id}/dokumente/suche.json?q=Melderlinie 7")
    assert r.status_code == 200
    treffer = r.json()["treffer"]
    assert len(treffer) == 1
    assert treffer[0]["seiten_nr"] == 1
    assert "Melderlinie 7" in treffer[0]["snippet"]

    # Zu kurzer Term → keine Treffer
    assert client.get(f"/objekte/{obj_id}/dokumente/suche.json?q=a").json()["treffer"] == []
