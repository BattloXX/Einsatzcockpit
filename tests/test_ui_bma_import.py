"""End-to-End-Smoke-Tests fuer /admin/bma-import (Konfiguration) und
/objekte/bma-import (Review-Queue), ueber den echten FastAPI-Router mit
TestClient. Netzwerk wird an keiner Stelle wirklich angesprochen (Muster:
tests/test_dibos_admin_routes.py) - BmaClient wird gefaked.
"""
import pytest

from app.core.security import hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN, Objekt, ObjektBMA
from app.models.user import Role, User, UserRole

ORG_ID = 1  # FF Wolfurt (seeded)


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


def _aktiviere_objekt_modul(db, org_id):
    sys_row = db.get(SystemSettings, "objekt_module_enabled")
    if sys_row is None:
        db.add(SystemSettings(key="objekt_module_enabled", value="true"))
    else:
        sys_row.value = "true"
    os_row = db.query(OrgSettings).filter_by(org_id=org_id).first()
    if os_row is None:
        os_row = OrgSettings(org_id=org_id)
        db.add(os_row)
    os_row.objekt_module_enabled = True


def _setup_admin(username: str, rollen=("org_admin",)) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="BMA Import Test", org_id=ORG_ID, active=True)
        db.add(user)
        db.flush()
        for code in rollen:
            db.add(UserRole(user_id=user.id, role_id=_rolle(db, code).id))
        _aktiviere_objekt_modul(db, ORG_ID)
        db.commit()
        return user.id
    finally:
        db.close()


# ── /admin/bma-import ─────────────────────────────────────────────────────────

def test_settings_page_loads():
    _setup_admin("bma_admin_page_user")

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_admin_page_user", "Test1234!")

    r = client.get("/admin/bma-import", params={"org_id": ORG_ID})
    assert r.status_code == 200, r.text[:300]
    assert "BMA-Webplattform-Import" in r.text
    assert "Session-Cookie" in r.text


def test_save_then_test_connection_roundtrip(monkeypatch):
    _setup_admin("bma_admin_save_user")

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_admin_save_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post("/admin/bma-import/save", data={
        "_csrf": csrf,
        "target_org_id": ORG_ID,
        "enabled": "1",
        "base_url": "https://dibos.example.at/LWZ_BMA_Webplattform",
        "sync_stunde": "4",
        "sync_minute": "15",
        "auto_anlegen": "1",
        "session_cookie": "sid=super-secret-cookie",
        "session_secret_changed": "1",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "flash=saved" in r.headers["location"]

    r = client.get("/admin/bma-import", params={"org_id": ORG_ID})
    assert r.status_code == 200
    assert "super-secret-cookie" not in r.text  # nie im Klartext angezeigt
    assert "Cookie hinterlegt" in r.text

    import app.services.bma_import.bma_client as bma_client_mod

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def test_connection(self):
            return True, "Verbindung erfolgreich (30 Anlagen sichtbar)"

        async def aclose(self):
            pass

    monkeypatch.setattr(bma_client_mod, "BmaClient", _FakeClient)

    r = client.post("/admin/bma-import/test", data={"_csrf": csrf, "target_org_id": ORG_ID})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "30 Anlagen" in body["message"]


def test_sync_jetzt_erstellt_objekt(monkeypatch):
    _setup_admin("bma_admin_sync_user")

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_admin_sync_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    client.post("/admin/bma-import/save", data={
        "_csrf": csrf,
        "target_org_id": ORG_ID,
        "enabled": "1",
        "base_url": "https://dibos.example.at/LWZ_BMA_Webplattform",
        "sync_stunde": "3",
        "sync_minute": "30",
        "auto_anlegen": "1",
        "session_cookie": "sid=abc",
        "session_secret_changed": "1",
    }, follow_redirects=False)

    async def fake_hole_anlagen(self, seiten_groesse=200):
        return [{
            "Id": 9001, "Guid": "test-guid", "BMANR": "9001",
            "Bezeichnung": "Sync-Jetzt Testanlage",
            "Address": {"Strasse": "Teststraße", "Hausnummer": "1", "PLZ": "6900", "Ort": "Bregenz",
                        "Latitude": "47,5", "Longitude": "9,7"},
            "PaymentAddress": None, "IsRFL": False, "IsActive": True,
            "Anlagedatum": "2020-01-01T00:00:00", "Aufschaltdatum": None,
            "ChangeDate": "2026-07-01T00:00:00",
        }]

    async def fake_hole_detail_html(self, extern_id):
        return "<html><body>BMANR</body></html>"

    import app.services.bma_import.bma_client as bma_client_mod
    monkeypatch.setattr(bma_client_mod.BmaClient, "hole_anlagen", fake_hole_anlagen)
    monkeypatch.setattr(bma_client_mod.BmaClient, "hole_detail_html", fake_hole_detail_html)

    r = client.post("/admin/bma-import/sync-jetzt", data={"_csrf": csrf, "target_org_id": ORG_ID},
                    follow_redirects=False)
    assert r.status_code == 302

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.query(Objekt).filter(
            Objekt.org_id == ORG_ID, Objekt.name == "Sync-Jetzt Testanlage",
        ).first()
        assert objekt is not None
        assert objekt.status == "entwurf"
    finally:
        db.close()


# ── /objekte/bma-import (Review-Queue) ───────────────────────────────────────

def _setup_freigegebenes_objekt(org_id: int, extern_id: str, bma_nummer: str, name: str) -> int:
    from datetime import UTC, datetime

    from app.models.bma_import import BMA_SATZ_AKTIV, BMA_ZUORDNUNG_AUTO, BmaImportSatz

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = Objekt(org_id=org_id, nummer=None, name=name, status=OBJEKT_STATUS_FREIGEGEBEN)
        db.add(objekt)
        db.flush()
        db.add(ObjektBMA(org_id=org_id, objekt_id=objekt.id, bma_nummer=bma_nummer))

        anlage = {"extern_id": extern_id, "bezeichnung": f"{name} (neu aus DIBOS)",
                  "bma_nummer": bma_nummer, "strasse": None, "hausnummer": None,
                  "plz": None, "ort": None, "lat": None, "lng": None, "is_rfl": False}
        import hashlib
        import json as _json
        rohdaten = {"anlage": anlage, "kontakte": []}
        rohdaten_json = _json.dumps(rohdaten, sort_keys=True, default=str, ensure_ascii=False)
        satz = BmaImportSatz(
            org_id=org_id, extern_id=extern_id, objekt_id=objekt.id, bma_nummer=bma_nummer,
            bezeichnung=anlage["bezeichnung"], rohdaten_json=rohdaten_json,
            quell_hash=hashlib.sha256(rohdaten_json.encode()).hexdigest(),
            zuordnung=BMA_ZUORDNUNG_AUTO, status=BMA_SATZ_AKTIV,
            erst_gesehen_am=datetime.now(UTC), zuletzt_gesehen_am=datetime.now(UTC),
        )
        db.add(satz)
        db.commit()
        return objekt.id
    finally:
        db.close()


def test_queue_page_zeigt_vorschlag():
    _setup_admin("bma_queue_page_user", rollen=("org_admin", "objekt_verwalter"))
    _setup_freigegebenes_objekt(ORG_ID, "9101", "9101", "Queue-Test-Objekt")

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_queue_page_user", "Test1234!")

    r = client.get("/objekte/bma-import")
    assert r.status_code == 200
    assert "Queue-Test-Objekt" in r.text
    assert "Queue-Test-Objekt (neu aus DIBOS)" in r.text  # neuer Wert aus dem Diff


def test_uebernehmen_erstellt_arbeitskopie_und_gibt_frei():
    _setup_admin("bma_queue_uebernehmen_user", rollen=("org_admin", "objekt_verwalter"))
    objekt_id = _setup_freigegebenes_objekt(ORG_ID, "9102", "9102", "Uebernahme-Test-Objekt")

    from app.models.bma_import import BmaImportSatz

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        satz = db.query(BmaImportSatz).filter(BmaImportSatz.objekt_id == objekt_id).one()
        satz_id = satz.id
    finally:
        db.close()

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_queue_uebernehmen_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/objekte/bma-import/{satz_id}/uebernehmen", data={"_csrf": csrf})
    assert r.status_code == 200, r.text[:300]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, objekt_id)
        assert objekt.name == "Uebernahme-Test-Objekt (neu aus DIBOS)"
        assert objekt.status == OBJEKT_STATUS_FREIGEGEBEN  # sofort wieder freigegeben
        satz = db.get(BmaImportSatz, satz_id)
        assert satz.bestaetigt_hash == satz.quell_hash
    finally:
        db.close()


def test_ignorieren_laesst_objekt_unveraendert():
    _setup_admin("bma_queue_ignorieren_user", rollen=("org_admin", "objekt_verwalter"))
    objekt_id = _setup_freigegebenes_objekt(ORG_ID, "9103", "9103", "Ignorieren-Test-Objekt")

    from app.models.bma_import import BmaImportSatz

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        satz = db.query(BmaImportSatz).filter(BmaImportSatz.objekt_id == objekt_id).one()
        satz_id = satz.id
    finally:
        db.close()

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_queue_ignorieren_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(f"/objekte/bma-import/{satz_id}/ignorieren", data={"_csrf": csrf})
    assert r.status_code == 200

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, objekt_id)
        assert objekt.name == "Ignorieren-Test-Objekt"  # unveraendert
        satz = db.get(BmaImportSatz, satz_id)
        assert satz.bestaetigt_hash == satz.quell_hash
    finally:
        db.close()


def test_objekt_anlegen_fuer_nicht_zugeordneten_satz():
    _setup_admin("bma_queue_anlegen_user", rollen=("org_admin", "objekt_verwalter"))

    from datetime import UTC, datetime

    from app.models.bma_import import BMA_SATZ_AKTIV, BMA_ZUORDNUNG_OFFEN, BmaImportSatz
    import hashlib
    import json as _json

    anlage = {"extern_id": "9104", "bezeichnung": "Frisch importierte Anlage",
              "bma_nummer": "9104", "strasse": "Nirgendweg", "hausnummer": "5",
              "plz": "6900", "ort": "Bregenz", "lat": None, "lng": None, "is_rfl": False}
    rohdaten = {"anlage": anlage, "kontakte": []}
    rohdaten_json = _json.dumps(rohdaten, sort_keys=True, default=str, ensure_ascii=False)

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        satz = BmaImportSatz(
            org_id=ORG_ID, extern_id="9104", objekt_id=None, bma_nummer="9104",
            bezeichnung=anlage["bezeichnung"], rohdaten_json=rohdaten_json,
            quell_hash=hashlib.sha256(rohdaten_json.encode()).hexdigest(),
            zuordnung=BMA_ZUORDNUNG_OFFEN, status=BMA_SATZ_AKTIV,
            erst_gesehen_am=datetime.now(UTC), zuletzt_gesehen_am=datetime.now(UTC),
        )
        db.add(satz)
        db.commit()
        satz_id = satz.id
    finally:
        db.close()

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_queue_anlegen_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.get("/objekte/bma-import")
    assert "Frisch importierte Anlage" in r.text

    r = client.post(f"/objekte/bma-import/{satz_id}/objekt-anlegen", data={"_csrf": csrf})
    assert r.status_code == 200, r.text[:300]

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        satz = db.get(BmaImportSatz, satz_id)
        assert satz.objekt_id is not None
        objekt = db.get(Objekt, satz.objekt_id)
        assert objekt.name == "Frisch importierte Anlage"
        assert objekt.status == "entwurf"
        assert objekt.strasse == "Nirgendweg"
    finally:
        db.close()


# ── /objekte/bma-import/datenblatt-upload ────────────────────────────────────

def _test_pdf_datenblatt(zeilen: list[str]) -> bytes:
    """Echtes Mini-PDF mit Textlayer via reportlab (Muster:
    tests/test_objekt_plan_upload.py::_test_pdf_mit_text) - eine Zeile je
    drawString-Aufruf, damit der zeilenbasierte bma_pdf_parser.py-Parser
    dieselbe Struktur wie ein echtes Datenblatt vorfindet."""
    import io

    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    y = 780
    for zeile in zeilen:
        c.drawString(50, y, zeile)
        y -= 16
    c.showPage()
    c.save()
    return buf.getvalue()


_DATENBLATT_ZEILEN = [
    "BMA 9201",
    "Testfirma Datenblatt-Upload",
    "1. Angaben zur Brandmeldeanlage",
    "Standort: Testfirma Datenblatt-Upload Anlagedatum: 01.01.2020 00:00:00",
    "Straße: Testweg 5 PLZ/Ort: 6900 Bregenz",
    "Telefon Beruf: +43 5574 12345 Fax Beruf: +43 5574 12345-9",
    "EMail Beruf: info@testfirma.at",
    "Aufschaltung RFL:Ja - 01.01.2021",
    "Brandschutzbeauftragte(r)",
    "Name: Test Kontakt",
    "Telefon Beruf:+43 664 1234567",
    "EMail Beruf:test.kontakt@testfirma.at",
]


def test_datenblatt_upload_form_laedt():
    _setup_admin("bma_pdf_form_user", rollen=("org_admin", "objekt_verwalter"))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_pdf_form_user", "Test1234!")

    r = client.get("/objekte/bma-import/datenblatt-upload")
    assert r.status_code == 200
    assert "BMA-Datenblatt hochladen" in r.text


def test_datenblatt_upload_legt_neues_objekt_an():
    _setup_admin("bma_pdf_upload_user", rollen=("org_admin", "objekt_verwalter"))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_pdf_upload_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    pdf_bytes = _test_pdf_datenblatt(_DATENBLATT_ZEILEN)
    r = client.post(
        "/objekte/bma-import/datenblatt-upload",
        data={"_csrf": csrf},
        files=[("dateien", ("BMA_Datenblatt_9201.pdf", pdf_bytes, "application/pdf"))],
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:300]  # Ergebnis-Uebersicht statt Redirect (Mehrfach-Upload-faehig)
    assert "BMA_Datenblatt_9201.pdf" in r.text
    assert "Neues Objekt angelegt" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.query(Objekt).filter(
            Objekt.org_id == ORG_ID, Objekt.name == "Testfirma Datenblatt-Upload",
        ).first()
        assert objekt is not None
        assert objekt.status == "entwurf"
        assert objekt.strasse == "Testweg"
        assert objekt.hausnummer == "5"
        assert objekt.bma is not None
        assert objekt.bma.bma_nummer == "9201"
        assert objekt.bma.uebertragungseinrichtung == "RFL aufgeschaltet"
    finally:
        db.close()


def test_datenblatt_upload_mehrere_dateien_werden_einzeln_verarbeitet():
    """Zwei Datenblaetter unterschiedlicher Anlagen in EINEM Request - beide muessen
    unabhaengig voneinander ein eigenes Objekt anlegen, keins darf das andere
    beeinflussen oder verhindern.

    Eigene, von _DATENBLATT_ZEILEN (BMA 9201) verschiedene BMA-Nummern: die
    geteilte Home-Org (ORG_ID=1) wird zwischen Tests dieser Datei NICHT
    zurueckgesetzt (Muster: test_incident_duplicate_guard.py-Kommentar zu
    session-scoped DBs) - eine wiederverwendete BMA-Nummer wuerde hier faelschlich
    auf den bereits von test_datenblatt_upload_legt_neues_objekt_an angelegten
    BmaImportSatz treffen ("unveraendert" statt "neu_angelegt")."""
    _setup_admin("bma_pdf_mehrfach_user", rollen=("org_admin", "objekt_verwalter"))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_pdf_mehrfach_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    erste_anlage = [
        "BMA 9210",
        "Firma Mehrfach A",
        "1. Angaben zur Brandmeldeanlage",
        "Standort: Firma Mehrfach A Anlagedatum: 01.01.2020 00:00:00",
        "Straße: Erster Weg 1 PLZ/Ort: 6900 Bregenz",
        "Telefon Beruf: +43 5574 11111",
        "Aufschaltung RFL:Nein",
    ]
    zweite_anlage = [
        "BMA 9211",
        "Firma Mehrfach B",
        "1. Angaben zur Brandmeldeanlage",
        "Standort: Firma Mehrfach B Anlagedatum: 01.01.2020 00:00:00",
        "Straße: Zweiter Weg 2 PLZ/Ort: 6900 Bregenz",
        "Telefon Beruf: +43 5574 22222",
        "Aufschaltung RFL:Nein",
    ]

    r = client.post(
        "/objekte/bma-import/datenblatt-upload",
        data={"_csrf": csrf},
        files=[
            ("dateien", ("BMA_9210.pdf", _test_pdf_datenblatt(erste_anlage), "application/pdf")),
            ("dateien", ("BMA_9211.pdf", _test_pdf_datenblatt(zweite_anlage), "application/pdf")),
        ],
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:300]
    assert "BMA_9210.pdf" in r.text
    assert "BMA_9211.pdf" in r.text
    assert r.text.count("Neues Objekt angelegt") == 2

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt1 = db.query(Objekt).filter(
            Objekt.org_id == ORG_ID, Objekt.name == "Firma Mehrfach A",
        ).first()
        objekt2 = db.query(Objekt).filter(
            Objekt.org_id == ORG_ID, Objekt.name == "Firma Mehrfach B",
        ).first()
        assert objekt1 is not None and objekt2 is not None
        assert objekt1.id != objekt2.id
        assert objekt1.bma.bma_nummer == "9210"
        assert objekt2.bma.bma_nummer == "9211"
    finally:
        db.close()


def test_datenblatt_upload_ein_fehler_verhindert_nicht_die_anderen_dateien():
    """Eine kaputte Datei mitten in einem Mehrfach-Upload darf die restlichen
    gueltigen Dateien nicht verhindern. Eigene BMA-Nummer (9212, siehe Docstring
    des vorigen Tests zur Begruendung)."""
    _setup_admin("bma_pdf_teilfehler_user", rollen=("org_admin", "objekt_verwalter"))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_pdf_teilfehler_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    gueltige_anlage = [
        "BMA 9212",
        "Firma Teilfehler",
        "1. Angaben zur Brandmeldeanlage",
        "Standort: Firma Teilfehler Anlagedatum: 01.01.2020 00:00:00",
        "Straße: Dritter Weg 3 PLZ/Ort: 6900 Bregenz",
        "Telefon Beruf: +43 5574 33333",
        "Aufschaltung RFL:Nein",
    ]

    r = client.post(
        "/objekte/bma-import/datenblatt-upload",
        data={"_csrf": csrf},
        files=[
            ("dateien", ("kaputt.pdf", _test_pdf_datenblatt(["Kein gueltiges Datenblatt"]), "application/pdf")),
            ("dateien", ("gueltig.pdf", _test_pdf_datenblatt(gueltige_anlage), "application/pdf")),
        ],
        follow_redirects=False,
    )
    assert r.status_code == 200, r.text[:300]
    assert "kaputt.pdf" in r.text and "BMA-Nummer" in r.text
    assert "gueltig.pdf" in r.text and "Neues Objekt angelegt" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.query(Objekt).filter(
            Objekt.org_id == ORG_ID, Objekt.name == "Firma Teilfehler",
        ).first()
        assert objekt is not None  # die gueltige Datei wurde trotz der kaputten verarbeitet
    finally:
        db.close()


def test_datenblatt_upload_ohne_bma_nummer_zeigt_fehler():
    _setup_admin("bma_pdf_kein_bma_user", rollen=("org_admin", "objekt_verwalter"))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_pdf_kein_bma_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    pdf_bytes = _test_pdf_datenblatt(["Kein gueltiges Datenblatt", "Zeile 2"])
    r = client.post(
        "/objekte/bma-import/datenblatt-upload",
        data={"_csrf": csrf},
        files=[("dateien", ("kaputt.pdf", pdf_bytes, "application/pdf"))],
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "BMA-Nummer" in r.text


def test_datenblatt_upload_lehnt_nicht_pdf_datei_ab():
    _setup_admin("bma_pdf_falscher_typ_user", rollen=("org_admin", "objekt_verwalter"))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_pdf_falscher_typ_user", "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    r = client.post(
        "/objekte/bma-import/datenblatt-upload",
        data={"_csrf": csrf},
        files=[("dateien", ("notiz.txt", b"Das ist kein PDF", "text/plain"))],
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Nur PDF-Dateien" in r.text


def test_datenblatt_upload_nicht_erreichbar_fuer_reine_lesekraft():
    _setup_admin("bma_pdf_no_role_user", rollen=("readonly",))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_pdf_no_role_user", "Test1234!")

    r = client.get("/objekte/bma-import/datenblatt-upload")
    assert r.status_code == 403


def test_review_queue_nicht_erreichbar_fuer_reine_lesekraft():
    """require_role("objekt_verwalter") laesst org_admin/admin immer durch (siehe
    app/core/permissions.py::require_role) - eine reine "readonly"-Rolle aber nicht."""
    _setup_admin("bma_queue_no_role_user", rollen=("readonly",))

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    _login(client, "bma_queue_no_role_user", "Test1234!")

    r = client.get("/objekte/bma-import")
    assert r.status_code == 403
