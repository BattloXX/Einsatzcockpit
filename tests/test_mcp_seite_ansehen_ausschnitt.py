"""Drehung und Ausschnitt verwenden dieselbe Koordinatenbasis wie der Editor."""

import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import ObjektDokument, ObjektDokumentSeite, ObjektEinsatz
from app.services.mcp_dokument_seiten_service import bild_anhaengen, seiten_bild
from tests.test_mcp_dokument_seiten_lesen import _result
from tests.test_mcp_einsatz import _einsatz
from tests.test_mcp_objekte import _mcp, _seed, _token


@pytest.fixture
def seite(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path))
    bild = Image.new("RGB", (200, 100), "white")
    bild.paste("red", (0, 0, 50, 50))
    bild.save(tmp_path / "seite.png")
    return SimpleNamespace(bild_pfad="seite.png", thumb_pfad=None, rotation=90)


def _bild(daten):
    return Image.open(io.BytesIO(base64.b64decode(daten)))


@pytest.mark.parametrize("fallback", [False, True])
def test_drehung_metadaten_und_ecke(seite, fallback):
    if fallback:
        seite.bild_pfad, seite.thumb_pfad = "fehlt.png", seite.bild_pfad
    antwort = {}
    bild_anhaengen(antwort, seite, 1600)
    bild = _bild(antwort["bild_base64"])
    assert bild.size == (100, 200)
    assert bild.getpixel((75, 25))[0] > 200
    assert bild.getpixel((75, 25))[1] < 50
    assert bild.getpixel((25, 175))[1] > 200
    assert antwort["bildgroesse"] == [100, 200]
    assert antwort["rotation"] == 90
    assert "gedrehte" in antwort["koordinaten_hinweis"]
    # Auch der Einsatzpfad nutzt seiten_bild ohne Ausschnittparameter.
    daten, vorhanden = seiten_bild(seite, 1600)
    assert vorhanden and _bild(daten).size == (100, 200)


def test_ausschnitt_nach_drehung(seite):
    antwort = {}
    bild_anhaengen(antwort, seite, 1600, [0.5, 0, 1, 0.25])
    bild = _bild(antwort["bild_base64"])
    assert bild.size == (50, 50)
    assert bild.getpixel((25, 25))[1] < 50
    assert antwort["ausschnitt"] == [0.5, 0, 1, 0.25]
    assert antwort["bildgroesse"] == [100, 200]
    assert antwort["koordinaten_hinweis"] == (
        "bild_x = x0 + u*(x1-x0), bild_y = y0 + v*(y1-y0); u,v normiert im Ausschnitt"
    )


@pytest.mark.parametrize("ausschnitt", [[], [0, 0, 1], [0, 0, 1, 1, 1], [-0.1, 0, 1, 1],
    [0, 0, float("nan"), 1], [0, 0, float("inf"), 1], [False, 0, 1, 1], ["0", 0, 1, 1],
    [0.5, 0, 0.4, 1], [0, 0.5, 1, 0.5], [0, 0, 0.019, 1], [0, 0, 1, 0.019]])
def test_ausschnitt_ungueltig(seite, ausschnitt):
    with pytest.raises(ValueError, match="Ausschnitt"):
        seiten_bild(seite, 1600, ausschnitt)


def test_mindestgroesse(seite):
    daten, _ = seiten_bild(seite, 1600, [0.5, 0.5, 0.52, 0.52])
    assert _bild(daten).size == (2, 4)


@pytest.mark.parametrize("einsatz", [False, True])
def test_mcp_gedrehtes_bild_und_ausschnitt(client, seite, einsatz):
    import json

    seed = _seed("ausschnitt-" + str(einsatz), {"u": "objekt_verwalter"})
    token = _token(client, seed, "u")
    argumente = dict(seite=1)
    name = "objekt_dokument_seite_ansehen"
    if einsatz:
        incident_id, _ = _einsatz(seed["org_id"])
        argumente["incident_id"] = incident_id
        name = "einsatz_objekt_dokument_seite_ansehen"
    else:
        argumente["ausschnitt"] = [0.5, 0, 1, 0.25]
    with SessionLocal() as db:
        set_tenant_context(db, seed["org_id"])
        dokument = ObjektDokument(org_id=seed["org_id"], objekt_id=seed["objekt_id"],
                                  pfad="plan.pdf", dateiname_original="plan.pdf",
                                  ist_aktuelle_version=True, freigabe_status="freigegeben")
        db.add(dokument)
        db.flush()
        argumente["dokument_id"] = dokument.id
        db.add(ObjektDokumentSeite(org_id=seed["org_id"], objekt_id=seed["objekt_id"],
                                  dokument_id=dokument.id, seiten_nr=1,
                                  bild_pfad=seite.bild_pfad, rotation=90))
        if einsatz:
            db.add(ObjektEinsatz(org_id=seed["org_id"], incident_id=incident_id,
                                objekt_id=seed["objekt_id"], quelle="manuell", status="bestaetigt"))
        db.commit()
    inhalt = _result(_mcp(client, token, "tools/call", {"name": name, "arguments": argumente}, 2))["content"]
    metadaten = json.loads(inhalt[0]["text"])
    bild = _bild(inhalt[1]["data"])
    assert metadaten["rotation"] == 90 and metadaten["bildgroesse"] == [100, 200]
    assert bild.size == ((100, 200) if einsatz else (50, 50))
    assert bild.getpixel((75, 25) if einsatz else (25, 25))[1] < 50
