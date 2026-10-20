"""M3: Quellreferenzen, eingebettete Bilder, Rechte und dateifreie Trockenlaeufe."""

import base64
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.katplan import KatplanAenderung, KatplanAnhang, KatplanDokument, KatplanDokumentSeite
from app.services import katplan_quellbild_service as bilder
from app.services.katplan_quelle_service import parse_seiten
from tests.conftest import TestingSession
from tests.test_katplan_k1b import umgebung  # noqa: F401
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_katplan import inhalte, rufe  # noqa: F401
from tests.test_mcp_katplan_schreiben import schreiben


@pytest.fixture
def quelle(inhalte):  # noqa: F811 - importierte Pytest-Fixture
    root = Path(settings.MEDIA_STORAGE_DIR)
    writer = PdfWriter()
    for groesse in ((600, 400), (100, 100)):
        out = BytesIO()
        Image.new("RGB", groesse, "red").save(out, "PDF")
        reader = PdfReader(BytesIO(out.getvalue()))
        if not writer.pages:
            writer.add_page(reader.pages[0])
        else:
            writer.pages[0].merge_page(reader.pages[0])
    writer.write(root / "seite.pdf")
    Image.new("RGB", (1000, 800), "blue").save(root / "seite.png")
    with TestingSession() as db:
        set_tenant_context(db, None)
        dokument = db.get(KatplanDokument, inhalte["dokument_id"])
        dokument.ist_aktuelle_version = True
        for seite in db.query(KatplanDokumentSeite).filter_by(dokument_id=dokument.id):
            seite.bild_pfad = "seite.png"
        db.commit()
    return inhalte


def aufruf(info, name, **kwargs):
    return schreiben(info, name, dokument_id=info["dokument_id"], seiten_nr=1, **kwargs)


def lesen(info):
    return rufe(info, "katplan_element_lesen", "katplan_verwalter", plan_id=info["plan_id"],
                element_id="KS-01", fassung="arbeitsstand")


def zuordnung(info, seiten="3-4,7", **kwargs):
    return {"element_id": "KS-01", "dokument_id": info["dokument_id"], "seiten": seiten, **kwargs}


def test_sammelzuordnung_teilfehler_und_protokoll(quelle):
    ergebnis = schreiben(quelle, "katplan_quellseite_verknuepfen", zuordnungen=[
        zuordnung(quelle), zuordnung(quelle, "26"), {**zuordnung(quelle), "element_id": "unbekannt"}])
    assert ergebnis["gespeichert"] is True
    assert [e["status"] for e in ergebnis["ergebnisse"]] == ["geaendert", "fehler", "fehler"]
    assert lesen(quelle)["source_references"][0]["seiten"] == "3-4,7"
    ergebnis = schreiben(quelle, "katplan_quellseite_verknuepfen", zuordnungen=[zuordnung(quelle)])
    assert ergebnis["ergebnisse"][0]["status"] == "unveraendert"
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(KatplanAenderung).filter_by(plan_id=quelle["plan_id"],
                                                  quelle="mcp", feld="quelle_seiten").count() == 1


@pytest.mark.parametrize("option", ["streng", "pruefen"])
def test_sammelzuordnung_rollback(quelle, option):
    vorher = lesen(quelle)
    kwargs = {option: True, "zuordnungen": [zuordnung(quelle), zuordnung(quelle, "99")]}
    if option == "streng":
        with pytest.raises(ValueError, match="außerhalb"):
            schreiben(quelle, "katplan_quellseite_verknuepfen", **kwargs)
    else:
        assert schreiben(quelle, "katplan_quellseite_verknuepfen", **kwargs)["gespeichert"] is False
    assert lesen(quelle) == vorher
    fehler = schreiben(quelle, "katplan_quellseite_verknuepfen", zuordnungen=[zuordnung(quelle, version=999)])
    assert "Versionskonflikt" in fehler["ergebnisse"][0]["fehler"]
    with pytest.raises(ValueError, match="50"):
        schreiben(quelle, "katplan_quellseite_verknuepfen", zuordnungen=[zuordnung(quelle)] * 51)


def test_quellreferenz_seiten_urls(quelle):
    schreiben(quelle, "katplan_quellseite_verknuepfen", zuordnungen=[zuordnung(quelle)])
    ref = lesen(quelle)["source_references"][0]
    assert set(ref) == {"dokument_id", "titel", "versionsnummer", "seiten", "seiten_liste"}
    assert [s["seiten_nr"] for s in ref["seiten_liste"]] == [3, 4, 7]
    assert ref["seiten_liste"][0]["text_url"].endswith("/seite/3/text")
    freigabe = rufe(quelle, "katplan_element_lesen", plan_id=quelle["plan_id"], element_id="KS-01")
    for seite in freigabe["source_references"][0]["seiten_liste"]:
        assert set(seite) == {"seiten_nr", "thumb_url", "seiten_url", "text_url"}
        for key in ("thumb_url", "seiten_url", "text_url"):
            assert seite[key].endswith(f'?version_id={quelle["version_id"]}')
    assert parse_seiten("1-100000000", limit=30) == list(range(1, 31))
    assert parse_seiten("1-30,31-40", limit=30) == list(range(1, 31))
    assert parse_seiten("1-30,31-40", limit=0) == []
    assert parse_seiten("1-20,15-40", limit=30) == list(range(1, 31))
    from app.routers.ui_katplan import _seitenbereich_enthaelt

    assert _seitenbereich_enthaelt("3-4,7", 7)
    assert not _seitenbereich_enthaelt("3-4,7", 5)
    for wert in ("0", "3-2", "1,,2", "1,0", "1-2,99"):
        with pytest.raises(ValueError):
            parse_seiten(wert, 25)


def test_bilder_und_vorschauen(quelle):
    result = aufruf(quelle, "katplan_quellseite_bilder")
    assert len(result["bilder"]) == 1
    info = result["bilder"][0]
    assert (info["breite"], info["hoehe"]) == (600, 400)
    assert info["bytes"] > 0 and info["format"] == "JPEG"
    assert result["seitenbild"] == {"breite": 1000, "hoehe": 800}
    assert len(result["vorschauen"]) == 1
    with Image.open(BytesIO(base64.b64decode(result["vorschauen"][0]["bild_base64"]))) as img:
        assert max(img.size) == 512 and img.format == "JPEG"
    assert aufruf(quelle, "katplan_quellseite_bilder", vorschau=False)["vorschauen"] == []
    with pytest.raises(ValueError, match="Nicht gefunden"):
        rufe(quelle, "katplan_quellseite_bilder", plan_id=quelle["plan_id"], dokument_id=quelle["neu_id"], seiten_nr=1)
    # Freigegebene Quelle ist fuer Leser sichtbar.
    assert rufe(quelle, "katplan_quellseite_bilder", plan_id=quelle["plan_id"],
                dokument_id=quelle["dokument_id"], seiten_nr=1)["bilder"]


@pytest.mark.parametrize("modus,extra,groesse", [
    ("seite", {}, (1000, 800)), ("bild", {"bild_index": 0}, (600, 400)),
    ("ausschnitt", {"rahmen": [0.1, 0.2, 0.6, 0.7]}, (500, 400)),
])
def test_anhang_modus_und_trockenlauf(quelle, modus, extra, groesse):
    root = Path(settings.MEDIA_STORAGE_DIR)
    vorher = set(root.rglob("*"))
    element = lesen(quelle)
    result = aufruf(quelle, "katplan_anhang_aus_quellseite", modus=modus, element_id="KS-01", pruefen=True, **extra)
    assert result["gespeichert"] is False and result["geaendert"] == ["anhang"]
    assert result["element"]["attachments"][-1]["uuid"] == result["uuid"]
    assert set(p for p in root.rglob("*") if p.is_file()) == set(p for p in vorher if p.is_file())
    assert lesen(quelle) == element
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert not db.query(KatplanAnhang).filter_by(uuid=result["uuid"]).count()
    result = aufruf(quelle, "katplan_anhang_aus_quellseite", modus=modus, element_id="KS-01", **extra)
    assert result["gespeichert"] is True
    assert result["beschriftung"] == "Quelle: Freigegebene Quelle v1, S. 1"
    assert result["mime"] == "image/jpeg" and result["element"] == lesen(quelle)
    with TestingSession() as db:
        set_tenant_context(db, None)
        anhang = db.query(KatplanAnhang).filter_by(uuid=result["uuid"]).one()
        assert (anhang.breite, anhang.hoehe) == groesse
        assert anhang.urheber == "" and anhang.art == "foto"
        assert (root / anhang.pfad).exists() and (root / anhang.thumb_pfad).exists()


@pytest.mark.parametrize("rahmen", [[0, 0, 0.01, 1], [0.5, 0, 0.2, 1], [-1, 0, 1, 1],
                                    [0, 0, 1], [True, 0, 1, 1], [0, 0, float("nan"), 1]])
def test_ungueltiger_rahmen(quelle, rahmen):
    with pytest.raises(ValueError, match="Rahmen"):
        aufruf(quelle, "katplan_anhang_aus_quellseite", modus="ausschnitt", rahmen=rahmen)


def test_leser_keine_schreibrechte(quelle):
    for name, kwargs in [("katplan_anhang_aus_quellseite", {"dokument_id": quelle["dokument_id"], "seiten_nr": 1}),
                         ("katplan_quellseite_verknuepfen", {"zuordnungen": [zuordnung(quelle)]})]:
        with pytest.raises(PermissionError):
            rufe(quelle, name, plan_id=quelle["plan_id"], **kwargs)


def test_defektes_pdf_und_fehlendes_seitenbild(quelle):
    (Path(settings.MEDIA_STORAGE_DIR) / "seite.pdf").write_bytes(b"kaputt")
    result = aufruf(quelle, "katplan_quellseite_bilder")
    assert result["bilder"] == [] and result["warnings"]
    with TestingSession() as db:
        set_tenant_context(db, None)
        seite = db.query(KatplanDokumentSeite).filter_by(dokument_id=quelle["dokument_id"], seiten_nr=1).one()
        seite.bild_pfad = None
        db.commit()
    with pytest.raises(ValueError, match="Kein Seitenbild"):
        aufruf(quelle, "katplan_anhang_aus_quellseite")


def test_mcp_content_wrapper(quelle, monkeypatch):
    import asyncio

    from app.mcp import server

    ergebnis = aufruf(quelle, "katplan_quellseite_bilder")

    async def call(name, **kwargs):
        return ergebnis

    monkeypatch.setattr(server, "_call_registered_tool", call)
    content = asyncio.run(server.katplan_quellseite_bilder(quelle["plan_id"], quelle["dokument_id"], 1))
    meta = json.loads(content[0].text)
    assert "bild_base64" not in content[0].text
    assert len(content) == 2 and content[1].type == "image"
    assert meta["vorschau_indizes"] == [0]


def test_bild_service_konvertiert_cmyk_rgba():
    for modus in ("CMYK", "RGBA"):
        with Image.open(BytesIO(bilder.jpeg_bytes(Image.new(modus, (200, 200))))) as img:
            assert img.mode == "RGB" and img.format == "JPEG"


def test_bild_und_vorschau_limits(quelle):
    writer = PdfWriter()
    for index in range(22):
        out = BytesIO()
        Image.new("RGB", (200, 200), (index * 10, 40, 60)).save(out, "PDF")
        reader = PdfReader(BytesIO(out.getvalue()))
        if not writer.pages:
            writer.add_page(reader.pages[0])
        else:
            writer.pages[0].merge_page(reader.pages[0])
    writer.write(Path(settings.MEDIA_STORAGE_DIR) / "seite.pdf")
    result = aufruf(quelle, "katplan_quellseite_bilder")
    assert len(result["bilder"]) == 20 and len(result["vorschauen"]) == 8
    with pytest.raises(ValueError, match="nicht gefunden"):
        aufruf(quelle, "katplan_anhang_aus_quellseite", modus="bild", bild_index=21)


def test_anhang_planebene_und_aufraeumen_bei_fehler(quelle, monkeypatch):
    from app.mcp.tools import katplan_schreiben

    root = Path(settings.MEDIA_STORAGE_DIR)
    vorher = {p for p in root.rglob("*") if p.is_file()}
    element = lesen(quelle)

    def fehler(*args, **kwargs):
        raise ValueError("Antwort fehlgeschlagen")

    with monkeypatch.context() as patch:
        patch.setattr(katplan_schreiben, "_antwort", fehler)
        with pytest.raises(ValueError, match="Antwort fehlgeschlagen"):
            aufruf(quelle, "katplan_anhang_aus_quellseite", element_id="KS-01")
    assert {p for p in root.rglob("*") if p.is_file()} == vorher
    assert lesen(quelle) == element
    result = aufruf(quelle, "katplan_anhang_aus_quellseite", beschriftung="Planfoto", vertraulich=True)
    assert "element" not in result
    with TestingSession() as db:
        set_tenant_context(db, None)
        anhang = db.query(KatplanAnhang).filter_by(uuid=result["uuid"]).one()
        assert anhang.element_id is None and anhang.vertraulich and anhang.beschriftung == "Planfoto"


def test_mehrere_gueltige_elemente_und_uuid(quelle):
    result = schreiben(quelle, "katplan_quellseite_verknuepfen", zuordnungen=[
        zuordnung(quelle), {**zuordnung(quelle, "5,7-8"), "element_id": quelle["geheim_uuid"]}])
    assert [e["status"] for e in result["ergebnisse"]] == ["geaendert", "geaendert"]
    geheim = rufe(quelle, "katplan_element_lesen", "katplan_verwalter", plan_id=quelle["plan_id"],
                  element_id=quelle["geheim_uuid"], fassung="arbeitsstand")
    assert geheim["source_references"][0]["seiten"] == "5,7-8"
