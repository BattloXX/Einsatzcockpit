"""HTTP-Regressionen fuer MCP-Objektdokumente."""

import base64
import io
import json

from pypdf import PdfWriter

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektDokument
from app.models.user import AuditLog
from tests.test_mcp_objekte import _mcp, _seed, _token

TOOLS_DOKUMENTE = {
    "objekt_dokument_uebergeben",
    "objekt_dokumente_auflisten",
    "objekt_dokument_seiten_klassifizieren",
}


def _pdf(seiten: int = 2) -> str:
    writer = PdfWriter()
    for _ in range(seiten):
        writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    return base64.b64encode(stream.getvalue()).decode()


def _antwort(response) -> dict:
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads([line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")][-1])
    return response.json()


def _rufe(client, token: str, tool: str, **arguments) -> dict:
    response = _mcp(client, token, "tools/call", {"name": tool, "arguments": arguments}, 42)
    assert response.status_code == 200, response.text
    result = _antwort(response)["result"]
    if result.get("isError"):
        return {"__fehler__": json.dumps(result["content"], ensure_ascii=False)}
    return result.get("structuredContent", {}).get("result", json.loads(result["content"][0]["text"]))


def test_rollen_modul_und_keine_freigabe_tools(client):
    for rolle, erlaubt in (("objekt_verwalter", True), ("org_admin", True), ("admin", True), ("recorder", False)):
        seed = _seed(f"doc-role-{rolle}", {"u": rolle})
        token = _token(client, seed, "u")
        text = _mcp(client, token, "tools/list", {}, 1).text
        assert all(name in text for name in TOOLS_DOKUMENTE) == erlaubt
    assert not any("freigeb" in name or "loesch" in name or "verwerf" in name for name in TOOLS_DOKUMENTE)


def test_uebergabe_entwurf_freigabeversionen_auflisten_und_audit(client):
    seed = _seed("doc-ok", {"u": "objekt_verwalter"})
    token = _token(client, seed, "u")
    db = SessionLocal()
    set_tenant_context(db, None)
    entwurf = Objekt(org_id=seed["org_id"], nummer=2, name="Entwurf", status="entwurf")
    db.add(entwurf)
    db.commit()
    entwurf_id = entwurf.id
    db.close()
    analyse = [{"volltext": "eins", "dokumentart": ""}, {"volltext": "zwei", "dokumentart": ""}]
    result = _rufe(
        client,
        token,
        "objekt_dokument_uebergeben",
        objekt_id=entwurf_id,
        dateiname="plan.pdf",
        inhalt_base64=_pdf(),
        seiten=analyse,
    )
    assert result["seitenzahl"] == 2 and result["freigabe_status"] == "freigegeben" and not result["wartet_freigabe"]
    wartend = _rufe(
        client,
        token,
        "objekt_dokument_uebergeben",
        objekt_id=seed["objekt_id"],
        dateiname="plan.pdf",
        inhalt_base64=_pdf(),
        seiten=analyse,
    )
    assert wartend["wartet_freigabe"]
    listed = _rufe(client, token, "objekt_dokumente_auflisten", objekt_id=seed["objekt_id"])
    assert listed["dokumente"][0]["seiten"][0].keys() == {"nr", "dokumentart", "titel"}
    assert "pfad" not in json.dumps(listed)
    db = SessionLocal()
    set_tenant_context(db, None)
    assert db.get(ObjektDokument, wartend["dokument_id"]).ist_aktuelle_version is False
    assert (
        db.query(AuditLog).filter_by(action="objekt.mcp_dokument_uebergeben", entity_id=wartend["dokument_id"]).count()
        == 1
    )
    db.close()


def test_validierung_cross_org_klassifizierung_und_groessenlimit(client, monkeypatch):
    a = _seed("doc-a", {"u": "objekt_verwalter"})
    b = _seed("doc-b", {"u": "objekt_verwalter"})
    token = _token(client, a, "u")
    analyse = [{"dokumentart": ""}, {"dokumentart": ""}]
    assert "__fehler__" in _rufe(
        client,
        token,
        "objekt_dokument_uebergeben",
        objekt_id=b["objekt_id"],
        dateiname="x.pdf",
        inhalt_base64=_pdf(),
        seiten=analyse,
    )
    assert "__fehler__" in _rufe(
        client,
        token,
        "objekt_dokument_uebergeben",
        objekt_id=a["objekt_id"],
        dateiname="x.pdf",
        inhalt_base64="%%%",
        seiten=analyse,
    )
    assert "__fehler__" in _rufe(
        client,
        token,
        "objekt_dokument_uebergeben",
        objekt_id=a["objekt_id"],
        dateiname="x.pdf",
        inhalt_base64=_pdf(),
        seiten=[{}],
    )
    monkeypatch.setattr(settings, "MCP_MAX_UPLOAD_BYTES", 5)
    assert "__fehler__" in _rufe(
        client,
        token,
        "objekt_dokument_uebergeben",
        objekt_id=a["objekt_id"],
        dateiname="x.pdf",
        inhalt_base64=_pdf(),
        seiten=analyse,
    )


def _analyse(text: str = "Plantext") -> list[dict]:
    return [
        {"volltext": f"{text} 1", "dokumentart": "", "titel": "Seite eins"},
        {"volltext": f"{text} 2", "dokumentart": ""},
    ]


def test_weder_ocr_noch_ki_werden_aufgerufen_und_text_ist_sofort_da(client, monkeypatch):
    from unittest.mock import AsyncMock, Mock

    ocr, ki, vision = Mock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr("app.services.objekt_dokument_service._ocr_tesseract", ocr)
    monkeypatch.setattr("app.services.objekt_ki_service.analysiere_unklassifizierte_seiten", ki)
    monkeypatch.setattr("app.services.ai_service.complete", ki)
    monkeypatch.setattr("app.services.ai_service.complete_vision", vision)
    seed = _seed("doc-noocr", {"u": "objekt_verwalter"})
    token = _token(client, seed, "u")
    result = _rufe(
        client, token, "objekt_dokument_uebergeben", objekt_id=seed["objekt_id"], dateiname="plan.pdf",
        inhalt_base64=_pdf(), seiten=_analyse("Melderlinie Nord"),
    )
    assert "__fehler__" not in result
    ocr.assert_not_called()
    ki.assert_not_called()
    vision.assert_not_called()
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dokument = db.get(ObjektDokument, result["dokument_id"])
        assert dokument.status == "fertig"
        seiten = sorted(dokument.seiten, key=lambda s: s.seiten_nr)
        assert [s.text_quelle for s in seiten] == ["mcp", "mcp"]
        assert seiten[0].volltext == "Melderlinie Nord 1" and seiten[0].titel == "Seite eins"
    finally:
        db.close()


def test_arbeitskopie_id_wird_auf_basis_objekt_abgebildet_und_neue_version(client):
    seed = _seed("doc-kopie", {"u": "objekt_verwalter"})
    token = _token(client, seed, "u")
    db = SessionLocal()
    set_tenant_context(db, None)
    kopie = Objekt(org_id=seed["org_id"], nummer=None, name="Kopie", status="entwurf",
                   entwurf_von_id=seed["objekt_id"])
    db.add(kopie)
    db.commit()
    kopie_id = kopie.id
    db.close()
    erste = _rufe(client, token, "objekt_dokument_uebergeben", objekt_id=kopie_id, dateiname="a.pdf",
                  inhalt_base64=_pdf(), seiten=_analyse("v1"))
    assert erste["wartet_freigabe"]
    zweite = _rufe(client, token, "objekt_dokument_uebergeben", objekt_id=seed["objekt_id"],
                   dateiname="a2.pdf", inhalt_base64=_pdf(), seiten=_analyse("v2"),
                   ersetzt_dokument_id=erste["dokument_id"])
    assert zweite["wartet_freigabe"]
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        d1 = db.get(ObjektDokument, erste["dokument_id"])
        d2 = db.get(ObjektDokument, zweite["dokument_id"])
        assert d1.objekt_id == seed["objekt_id"] and d2.objekt_id == seed["objekt_id"]
        assert d2.versionsnummer == 2 and d2.dokument_gruppe_id in (d1.id, d1.dokument_gruppe_id)
        assert d2.ist_aktuelle_version is False and d2.freigabe_status == "wartet_freigabe"
    finally:
        db.close()


def test_fehler_hinterlassen_keine_dokumente_und_klassifizieren_aendert_freigabestand_nicht(client):
    seed = _seed("doc-clean", {"u": "objekt_verwalter"})
    token = _token(client, seed, "u")

    def anzahl() -> int:
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            return db.query(ObjektDokument).filter(ObjektDokument.objekt_id == seed["objekt_id"]).count()
        finally:
            db.close()

    vorher = anzahl()
    assert "__fehler__" in _rufe(client, token, "objekt_dokument_uebergeben", objekt_id=seed["objekt_id"],
                                 dateiname="x.pdf", inhalt_base64=_pdf(3), seiten=_analyse())
    assert "__fehler__" in _rufe(client, token, "objekt_dokument_uebergeben", objekt_id=seed["objekt_id"],
                                 dateiname="x.pdf", inhalt_base64=_pdf(),
                                 seiten=[{"volltext": "a", "dokumentart": "gibt_es_nicht"}, {"volltext": "b"}])
    assert anzahl() == vorher
    ok = _rufe(client, token, "objekt_dokument_uebergeben", objekt_id=seed["objekt_id"], dateiname="p.pdf",
               inhalt_base64=_pdf(), seiten=_analyse())
    korrigiert = _rufe(client, token, "objekt_dokument_seiten_klassifizieren", dokument_id=ok["dokument_id"],
                       seiten=[{"seite": 2, "titel": "Neu", "volltext": "korrigiert"}])
    assert korrigiert["freigabe_status"] == "wartet_freigabe" and korrigiert["seiten_klassifiziert"] == 1
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        dokument = db.get(ObjektDokument, ok["dokument_id"])
        assert dokument.ist_aktuelle_version is False and dokument.freigabe_status == "wartet_freigabe"
        zwei = next(s for s in dokument.seiten if s.seiten_nr == 2)
        assert zwei.titel == "Neu" and zwei.volltext == "korrigiert"
    finally:
        db.close()
