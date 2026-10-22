"""Medien geben ihre DB-Verbindung vor dem Aufbau der Dateiantwort frei."""

import io

import pytest
from PIL import Image

from app.core.tenant import set_tenant_context
from app.db import SessionLocal, get_db
from app.main import app
from app.models.objekt import ObjektDokumentSeite, ObjektSymbol
from app.routers import ui_objekt_dokumente as medien
from tests import test_einsatzinfo_alle_benutzer as einsatzinfo
from tests.conftest import TestingSession

info_daten = einsatzinfo.info_daten


@pytest.fixture()
def medien_daten(info_daten, tmp_path):
    bild = tmp_path / "seite.png"
    thumb = tmp_path / "thumb.jpg"
    Image.new("RGB", (40, 80), "white").save(bild)
    Image.new("RGB", (40, 80), "white").save(thumb)
    with SessionLocal() as db:
        set_tenant_context(db, None)
        seite = db.get(ObjektDokumentSeite, info_daten.seite)
        seite.thumb_pfad = str(thumb)
        symbol = ObjektSymbol(org_id=info_daten.org, code="test-bild", name="Testbild",
                              stil="bild", bild_pfad=str(bild))
        db.add(symbol)
        db.commit()
        info_daten.symbol = symbol.id
    return info_daten


def _url(daten, variante):
    if variante == "original":
        return f"/objekt-medien/dokument/{daten.dokument}/original?download=1"
    if variante == "symbol":
        return f"/objekt-medien/symbol/{daten.symbol}"
    return f"/objekt-medien/seite/{daten.seite}/{variante}"


@pytest.fixture()
def antwort_session(client, monkeypatch):
    """Echte Request-Session verfolgen; spaete ORM-Zugriffe schlagen fehl."""
    sessions = []
    aufbauten = []

    def get_test_db():
        db = TestingSession()
        set_tenant_context(db, None)
        sessions.append(db)
        close = db.close

        def schliessen():
            # Abgelaufene Attribute duerfen nach close nicht nachgeladen werden.
            db.expire_all()
            close()
            db.info["geschlossen"] = True

        monkeypatch.setattr(db, "close", schliessen)
        try:
            yield db
        finally:
            db.close()

    def pruefe_antwortaufbau(original):
        def antwort(*args, **kwargs):
            assert len(sessions) == 1
            db = sessions[0]
            assert db.info.get("geschlossen"), "Session beim Antwortaufbau noch offen"
            assert not db.in_transaction()
            aufbauten.append(original.__name__)
            return original(*args, **kwargs)

        return antwort

    monkeypatch.setitem(app.dependency_overrides, get_db, get_test_db)
    monkeypatch.setattr(medien, "_bild_response", pruefe_antwortaufbau(medien._bild_response))
    monkeypatch.setattr(medien, "FileResponse", pruefe_antwortaufbau(medien.FileResponse))
    return aufbauten


@pytest.mark.parametrize("variante,rotation", [
    ("thumb", 0), ("bild", 0), ("thumb", 90), ("bild", 90),
    ("pdf", 0), ("original", 0), ("symbol", 0),
])
def test_medien_schliessen_session_vor_antwortaufbau(
    client, medien_daten, antwort_session, variante, rotation,
):
    d = medien_daten
    with SessionLocal() as db:
        set_tenant_context(db, None)
        db.get(ObjektDokumentSeite, d.seite).rotation = rotation
        db.commit()
    einsatzinfo._anmelden(client, d.org, "readonly")

    response = client.get(_url(d, variante))

    assert response.status_code == 200
    assert antwort_session
    if variante in ("thumb", "bild", "symbol"):
        media_type = "image/jpeg" if variante == "thumb" else "image/png"
        assert response.headers["content-type"] == media_type
        with Image.open(io.BytesIO(response.content)) as bild:
            assert bild.size == ((80, 40) if rotation else (40, 80))
    else:
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
        if variante == "pdf":
            assert 'inline; filename="seite_0001.pdf"' == response.headers["content-disposition"]
        else:
            assert 'attachment; filename="Einsatzplan.pdf"' == response.headers["content-disposition"]
    if variante == "symbol":
        assert response.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'"


@pytest.mark.parametrize("variante", ["thumb", "bild", "pdf", "original", "symbol"])
def test_medien_fremde_org_bleibt_404(client, medien_daten, variante):
    einsatzinfo._anmelden(client, medien_daten.fremd, "readonly")
    assert client.get(_url(medien_daten, variante)).status_code == 404
