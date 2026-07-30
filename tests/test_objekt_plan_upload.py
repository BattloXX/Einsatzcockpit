"""Brandschutzplan-Upload ohne Objekt-Vorauswahl (objekt_plan_upload_service.py
+ neue Routen in ui_objekt_dokumente.py).

Die KI liest Name/Adresse/BMA-Nummer aus dem Dokument, BEVOR es gespeichert
wird, und loest damit das Ziel-Objekt auf - kein Objekt muss beim Upload
ausgewaehlt werden (siehe Plan "Brandschutzplan-Upload ohne Objekt-Auswahl").
"""
import asyncio
import io
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.master import FireDept
from app.models.objekt import OBJEKT_STATUS_ENTWURF, GefahrenKatalog, Objekt, ObjektBMA
from app.services.objekt_plan_upload_service import (
    _name_aus_dateiname,
    _parse_identitaet,
    _text_erster_seiten,
    erstelle_objekt_aus_identitaet,
    finde_passendes_objekt,
    identifiziere_objekt,
)


def _test_pdf_blank(seiten: int = 1) -> bytes:
    """Blankes Mini-PDF (kein Textlayer) - fuer store_dokument_upload-Validierung."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    for _ in range(seiten):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _test_pdf_mit_text(text: str) -> bytes:
    """Echtes Mini-PDF MIT Textlayer via reportlab, fuer den Text-Extraktions-Pfad."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    c.drawString(50, 780, text)
    c.showPage()
    c.save()
    return buf.getvalue()


# ── Reine Parser-/Helfer-Funktionen ───────────────────────────────────────────

def test_parse_identitaet_gueltig():
    antwort = ('{"name": "Meusburger Georg GmbH & Co KG", "strasse": "Kesselstraße", '
               '"hausnummer": "42", "plz": "6960", "ort": "Wolfurt", "bma_nummer": null}')
    p = _parse_identitaet(antwort)
    assert p is not None
    assert p["name"] == "Meusburger Georg GmbH & Co KG"
    assert p["hausnummer"] == "42"
    assert p["bma_nummer"] is None


def test_parse_identitaet_markdown_fence():
    antwort = '```json\n{"name": "Test GmbH", "strasse": null}\n```'
    p = _parse_identitaet(antwort)
    assert p is not None
    assert p["name"] == "Test GmbH"


def test_parse_identitaet_ungueltiges_json():
    assert _parse_identitaet("kein json") is None
    assert _parse_identitaet('["liste"]') is None


def test_name_aus_dateiname():
    assert _name_aus_dateiname("Meusburger_BSP.pdf") == "Meusburger BSP"
    assert _name_aus_dateiname("Plan-2026.pdf") == "Plan 2026"
    assert _name_aus_dateiname("") == "Neues Objekt"


def test_text_erster_seiten_liest_echten_textlayer():
    data = _test_pdf_mit_text("Meusburger Georg GmbH & Co KG, Kesselstraße 42, 6960 Wolfurt")
    text = _text_erster_seiten(data)
    assert "Meusburger" in text
    assert "Kesselstraße" in text


def test_text_erster_seiten_leer_bei_blankem_pdf():
    assert _text_erster_seiten(_test_pdf_blank()) == ""


# ── identifiziere_objekt (KI gemockt) ─────────────────────────────────────────

def test_identifiziere_objekt_nutzt_text_pfad_wenn_textlayer_vorhanden():
    data = _test_pdf_mit_text(
        "BRANDSCHUTZPLAN Meusburger Georg GmbH & Co KG Kesselstraße 42, 6960 Wolfurt " * 5
    )

    async def fake_complete(system, user, *, fast=False, max_tokens=None, org_id=None):
        assert "Meusburger" in user  # der extrahierte Text wurde tatsaechlich mitgeschickt
        return ('{"name": "Meusburger Georg GmbH & Co KG", "strasse": "Kesselstraße", '
                '"hausnummer": "42", "plz": "6960", "ort": "Wolfurt", "bma_nummer": null}')

    with patch("app.services.ai_service.complete", side_effect=fake_complete) as m_complete, \
         patch("app.services.ai_service.complete_vision") as m_vision:
        ergebnis = asyncio.run(identifiziere_objekt(data, "bsp.pdf", org_id=1))

    assert ergebnis["quelle"] == "ki_text"
    assert ergebnis["name"] == "Meusburger Georg GmbH & Co KG"
    assert ergebnis["ort"] == "Wolfurt"
    m_complete.assert_called_once()
    m_vision.assert_not_called()


def test_identifiziere_objekt_ki_fehler_faellt_auf_dateiname_zurueck():
    data = _test_pdf_mit_text("Irgendein Titelblock-Text " * 20)

    async def fake_complete(*a, **kw):
        from app.services.ai_service import AIServiceError
        raise AIServiceError("KI-Dienst nicht aktiviert.")

    with patch("app.services.ai_service.complete", side_effect=fake_complete), \
         patch("app.services.ai_service.complete_vision", side_effect=fake_complete):
        ergebnis = asyncio.run(identifiziere_objekt(data, "Meusburger_BSP.pdf", org_id=1))

    assert ergebnis["quelle"] == "dateiname"
    assert ergebnis["name"] == "Meusburger BSP"


def test_identifiziere_objekt_nicht_pdf_faellt_sofort_auf_dateiname_zurueck():
    with patch("app.services.ai_service.complete") as m_complete:
        ergebnis = asyncio.run(identifiziere_objekt(b"nicht wirklich ein pdf", "test.pdf", org_id=1))
    assert ergebnis["quelle"] == "dateiname"
    m_complete.assert_not_called()


def test_identifiziere_objekt_duenner_textlayer_nutzt_vision_fallback():
    """Ohne brauchbaren Textlayer wird - falls Rendering verfuegbar - Vision genutzt."""
    data = _test_pdf_blank()

    async def fake_vision(system, user, images, *, org_id=None):
        return '{"name": "Aus Bild erkannt", "strasse": null, "hausnummer": null, "plz": null, "ort": null, "bma_nummer": null}'

    with patch("app.services.ai_service.complete") as m_complete, \
         patch("app.services.ai_service.complete_vision", side_effect=fake_vision) as m_vision, \
         patch("app.services.objekt_plan_upload_service._render_erste_seite", return_value=b"fake-png-bytes"):
        ergebnis = asyncio.run(identifiziere_objekt(data, "scan.pdf", org_id=1))

    m_complete.assert_not_called()  # Textlayer zu duenn (blanke Seite)
    m_vision.assert_called_once()
    assert ergebnis["quelle"] == "ki_vision"
    assert ergebnis["name"] == "Aus Bild erkannt"


# ── finde_passendes_objekt (In-Memory-DB) ─────────────────────────────────────

@pytest.fixture()
def match_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    set_tenant_context(db, None)
    org_a = FireDept(slug=f"plan-upload-a-{uuid.uuid4().hex[:8]}", name="Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug=f"plan-upload-b-{uuid.uuid4().hex[:8]}", name="Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()
    yield db, org_a, org_b
    db.close()
    Base.metadata.drop_all(bind=engine)


def test_finde_passendes_objekt_ueber_bma_nummer(match_db):
    db, org_a, _ = match_db
    objekt = Objekt(org_id=org_a.id, nummer=1, name="Bestehendes Objekt", status=OBJEKT_STATUS_ENTWURF)
    db.add(objekt)
    db.flush()
    db.add(ObjektBMA(org_id=org_a.id, objekt_id=objekt.id, bma_nummer="01044"))
    db.commit()

    treffer = finde_passendes_objekt(db, org_a.id, {"bma_nummer": "1044", "strasse": None, "hausnummer": None, "ort": None})
    assert treffer is not None
    assert treffer.id == objekt.id


def test_finde_passendes_objekt_ueber_adresse(match_db):
    db, org_a, _ = match_db
    objekt = Objekt(org_id=org_a.id, nummer=1, name="Bestehendes Objekt", status=OBJEKT_STATUS_ENTWURF,
                    strasse="Kesselstraße", hausnummer="42", ort="Wolfurt")
    db.add(objekt)
    db.commit()

    treffer = finde_passendes_objekt(db, org_a.id, {
        "bma_nummer": None, "strasse": "Kesselstrasse", "hausnummer": "42", "ort": "Wolfurt",
    })
    assert treffer is not None
    assert treffer.id == objekt.id


def test_finde_passendes_objekt_mehrdeutige_adresse_liefert_none(match_db):
    db, org_a, _ = match_db
    db.add(Objekt(org_id=org_a.id, nummer=1, name="Objekt 1", status=OBJEKT_STATUS_ENTWURF,
                  strasse="Hauptstraße", hausnummer="1", ort="Musterstadt"))
    db.add(Objekt(org_id=org_a.id, nummer=2, name="Objekt 2", status=OBJEKT_STATUS_ENTWURF,
                  strasse="Hauptstraße", hausnummer="1", ort="Musterstadt"))
    db.commit()

    treffer = finde_passendes_objekt(db, org_a.id, {
        "bma_nummer": None, "strasse": "Hauptstraße", "hausnummer": "1", "ort": "Musterstadt",
    })
    assert treffer is None  # nicht raten -> lieber neu anlegen


def test_finde_passendes_objekt_kein_treffer_liefert_none(match_db):
    db, org_a, _ = match_db
    db.add(Objekt(org_id=org_a.id, nummer=1, name="Anderes Objekt", status=OBJEKT_STATUS_ENTWURF,
                  strasse="Andere Straße", hausnummer="1", ort="Anderswo"))
    db.commit()

    treffer = finde_passendes_objekt(db, org_a.id, {
        "bma_nummer": None, "strasse": "Kesselstraße", "hausnummer": "42", "ort": "Wolfurt",
    })
    assert treffer is None


def test_finde_passendes_objekt_cross_org_isolation(match_db):
    """Kritisch: eine zufaellig identische BMA-Nummer/Adresse in einer FREMDEN
    Org darf niemals als Treffer zurueckkommen."""
    db, org_a, org_b = match_db
    objekt_b = Objekt(org_id=org_b.id, nummer=1, name="Objekt in Org B", status=OBJEKT_STATUS_ENTWURF,
                      strasse="Kesselstraße", hausnummer="42", ort="Wolfurt")
    db.add(objekt_b)
    db.flush()
    db.add(ObjektBMA(org_id=org_b.id, objekt_id=objekt_b.id, bma_nummer="1044"))
    db.commit()

    # Gleiche BMA-Nummer UND gleiche Adresse, aber Suche laeuft fuer Org A
    treffer = finde_passendes_objekt(db, org_a.id, {
        "bma_nummer": "1044", "strasse": "Kesselstraße", "hausnummer": "42", "ort": "Wolfurt",
    })
    assert treffer is None


# ── erstelle_objekt_aus_identitaet ────────────────────────────────────────────

def test_erstelle_objekt_aus_identitaet(match_db):
    from app.models.objekt import ObjektChange
    db, org_a, _ = match_db

    class _FakeUser:
        id = 1
        org_id = org_a.id

    objekt = erstelle_objekt_aus_identitaet(db, _FakeUser(), {
        "name": "Meusburger Georg GmbH & Co KG", "strasse": "Kesselstraße",
        "hausnummer": "42", "plz": "6960", "ort": "Wolfurt", "bma_nummer": None,
        "quelle": "ki_text",
    })
    db.commit()

    assert objekt.id is not None
    assert objekt.status == OBJEKT_STATUS_ENTWURF
    assert objekt.nummer == 1
    assert objekt.name == "Meusburger Georg GmbH & Co KG"
    assert objekt.ort == "Wolfurt"
    assert db.query(ObjektChange).filter(ObjektChange.objekt_id == objekt.id).count() >= 1


def test_pr_registrierung():
    from app.routers.ui_objekt import router
    pfade = {r.path for r in router.routes}
    assert "/objekte/dokument-upload" in pfade


# ── Echter HTTP-Roundtrip (Login + POST + CSRF) ───────────────────────────────
# Nutzt die GETEILTE Test-DB (app.db.SessionLocal, wie test_objekt_verwaltung.py),
# nicht die isolierte match_db-Engine - die Route laeuft ueber die echte App.

def _login_http(client, username, password):
    client.get("/login")
    csrf = client.cookies.get("ec_csrf")
    return client.post("/login", data={"username": username, "password": password, "_csrf": csrf},
                       follow_redirects=False)


@pytest.fixture()
def plan_upload_setup():
    from app.core.security import hash_password
    from app.db import SessionLocal
    from app.models.master import OrgSettings, SystemSettings
    from app.models.user import Role, User, UserRole

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = FireDept(slug=f"plan-upload-http-{uuid.uuid4().hex[:8]}", name="Plan-Upload-Org",
                       color="#123456", bos="Feuerwehr")
        db.add(org)
        db.flush()

        sys_row = db.get(SystemSettings, "objekt_module_enabled")
        if sys_row is None:
            db.add(SystemSettings(key="objekt_module_enabled", value="true"))
        else:
            sys_row.value = "true"
        db.add(OrgSettings(org_id=org.id, objekt_module_enabled=True,
                           objekt_ki_klassifikation_enabled=True))

        role = db.query(Role).filter(Role.code == "objekt_verwalter").first()
        if role is None:
            role = Role(code="objekt_verwalter", name="objekt_verwalter")
            db.add(role)
            db.flush()
        username = f"plan_upload_user_{uuid.uuid4().hex[:8]}"
        user = User(username=username, password_hash=hash_password("Test1234!"),
                    display_name="Plan-Upload Test", org_id=org.id, active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return org.id, username
    finally:
        db.close()


def test_dokument_upload_form_zeigt_hinweis_wenn_ki_deaktiviert(client, plan_upload_setup):
    from app.db import SessionLocal
    from app.models.master import OrgSettings

    org_id, username = plan_upload_setup
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first().objekt_ki_klassifikation_enabled = False
        db.commit()
    finally:
        db.close()

    _login_http(client, username, "Test1234!")
    r = client.get("/objekte/dokument-upload")
    assert r.status_code == 200
    assert "aktivierter KI-Klassifikation" in r.text
    assert "enctype" in r.text  # Datenblätter funktionieren auch ohne KI


def test_dokument_upload_legt_neues_objekt_an(client, plan_upload_setup):
    from app.db import SessionLocal

    org_id, username = plan_upload_setup
    _login_http(client, username, "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    async def fake_identifiziere(data, dateiname, org_id):
        return {"name": "Meusburger Georg GmbH & Co KG", "strasse": "Kesselstraße",
                "hausnummer": "42", "plz": "6960", "ort": "Wolfurt", "bma_nummer": None,
                "quelle": "ki_text"}

    pdf_bytes = _test_pdf_blank()
    with patch("app.services.ai_service.is_enabled", return_value=True), \
         patch("app.services.objekt_plan_upload_service.identifiziere_objekt", side_effect=fake_identifiziere), \
         patch("app.services.objekt_dokument_service.verarbeite_dokument"), \
         patch("app.services.objekt_ki_service.analysiere_unklassifizierte_seiten"), \
         patch("app.routers.ui_objekt._geocode_objekt"):
        r = client.post(
            "/objekte/dokument-upload",
            data={"_csrf": csrf},
            files=[("dateien", ("Meusburger_BSP.pdf", pdf_bytes, "application/pdf"))],
            follow_redirects=False,
        )

    assert r.status_code == 200, r.text[:500]
    assert "Meusburger_BSP.pdf" in r.text
    assert "neu" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.query(Objekt).filter(
            Objekt.org_id == org_id, Objekt.name == "Meusburger Georg GmbH & Co KG",
        ).first()
        assert objekt is not None
        assert objekt.status == OBJEKT_STATUS_ENTWURF
        assert objekt.ort == "Wolfurt"
        from app.models.objekt import ObjektDokument
        assert db.query(ObjektDokument).filter(ObjektDokument.objekt_id == objekt.id).count() == 1
    finally:
        db.close()


def test_dokument_upload_ergaenzt_bestehendes_objekt_ueber_adresse(client, plan_upload_setup):
    from app.db import SessionLocal

    org_id, username = plan_upload_setup
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        bestehendes = Objekt(org_id=org_id, nummer=1, name="Meusburger Wolfurt",
                             status=OBJEKT_STATUS_ENTWURF, strasse="Kesselstraße",
                             hausnummer="42", ort="Wolfurt")
        db.add(bestehendes)
        db.commit()
        bestehendes_id = bestehendes.id
    finally:
        db.close()

    _login_http(client, username, "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    async def fake_identifiziere(data, dateiname, org_id):
        return {"name": "Meusburger Georg GmbH & Co KG", "strasse": "Kesselstrasse",
                "hausnummer": "42", "plz": "6960", "ort": "Wolfurt", "bma_nummer": None,
                "quelle": "ki_text"}

    with patch("app.services.ai_service.is_enabled", return_value=True), \
         patch("app.services.objekt_plan_upload_service.identifiziere_objekt", side_effect=fake_identifiziere), \
         patch("app.services.objekt_dokument_service.verarbeite_dokument"), \
         patch("app.services.objekt_ki_service.analysiere_unklassifizierte_seiten"):
        r = client.post(
            "/objekte/dokument-upload",
            data={"_csrf": csrf},
            files=[("dateien", ("bsp.pdf", _test_pdf_blank(), "application/pdf"))],
            follow_redirects=False,
        )

    assert r.status_code == 200, r.text[:500]
    assert "bsp.pdf" in r.text
    assert "ergaenzt" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        # Kein zweites Objekt fuer dieselbe Adresse angelegt
        assert db.query(Objekt).filter(Objekt.org_id == org_id).count() == 1
        from app.models.objekt import ObjektDokument
        assert db.query(ObjektDokument).filter(ObjektDokument.objekt_id == bestehendes_id).count() == 1
    finally:
        db.close()


def test_dokument_upload_ungueltige_datei_hinterlaesst_kein_karteileichen_objekt(client, plan_upload_setup):
    from app.db import SessionLocal

    org_id, username = plan_upload_setup
    _login_http(client, username, "Test1234!")
    csrf = client.cookies.get("ec_csrf")

    async def fake_identifiziere(data, dateiname, org_id):
        return {"name": "Wird nie gespeichert", "strasse": None, "hausnummer": None,
                "plz": None, "ort": None, "bma_nummer": None, "quelle": "dateiname"}

    with patch("app.services.ai_service.is_enabled", return_value=True), \
         patch("app.services.objekt_plan_upload_service.identifiziere_objekt", side_effect=fake_identifiziere):
        r = client.post(
            "/objekte/dokument-upload",
            data={"_csrf": csrf},
            files=[("dateien", ("kaputt.pdf", b"das ist kein PDF", "application/pdf"))],
        )

    assert r.status_code == 200
    assert "PDF" in r.text or "gelesen" in r.text

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        assert db.query(Objekt).filter(
            Objekt.org_id == org_id, Objekt.name == "Wird nie gespeichert",
        ).first() is None
    finally:
        db.close()


def test_dokument_upload_ohne_csrf_wird_abgelehnt(client, plan_upload_setup):
    org_id, username = plan_upload_setup
    _login_http(client, username, "Test1234!")

    r = client.post(
        "/objekte/dokument-upload",
        files={"datei": ("bsp.pdf", _test_pdf_blank(), "application/pdf")},
    )
    assert r.status_code == 403
