"""Focused MCP regression tests for rendered object-document pages."""

import base64
import io
import json

from PIL import Image

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.objekt import Objekt, ObjektDokument, ObjektDokumentSeite, ObjektEinsatz
from app.services.objekt_dokument_service import absolute_pfad
from tests.test_mcp_einsatz import _einsatz
from tests.test_mcp_objekt_dokumente import _pdf
from tests.test_mcp_objekte import _mcp, _seed, _token


def _result(response):
    lines = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
    return json.loads(lines[-1])["result"]


def _upload(client, token, objekt_id):
    response = _mcp(client, token, "tools/call", {"name": "objekt_dokument_uebergeben", "arguments": {
        "objekt_id": objekt_id, "dateiname": "plan.pdf", "inhalt_base64": _pdf(2),
        "seiten": [{"volltext": "A" * 1500}, {"volltext": "zweite Seite"}],
    }}, 1)
    return json.loads(_result(response)["content"][0]["text"])["dokument_id"]


def _bild(dokument_id, feld="bild_pfad"):
    db = SessionLocal()
    set_tenant_context(db, None)
    seite = db.query(ObjektDokumentSeite).filter_by(dokument_id=dokument_id, seiten_nr=1).one()
    relativ = f"test/{dokument_id}_{feld}.png"
    pfad = absolute_pfad(relativ)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3000, 1000), "red").save(pfad)
    setattr(seite, feld, relativ)
    db.commit()
    db.close()


def test_seite_bild_text_fallback_und_auflisten(client):
    seed = _seed("seiten", {"u": "objekt_verwalter"})
    token = _token(client, seed, "u")
    dokument_id = _upload(client, token, seed["objekt_id"])
    _bild(dokument_id)
    response = _mcp(client, token, "tools/call", {"name": "objekt_dokument_seite_ansehen", "arguments": {
        "dokument_id": dokument_id, "seite": 1, "max_kante_px": 400,
    }}, 2)
    content = _result(response)["content"]
    payload = json.loads(content[0]["text"])
    assert payload["volltext"] == "A" * 1500 and payload["hat_bild"]
    assert content[1]["type"] == "image" and content[1]["mimeType"] == "image/jpeg"
    image = Image.open(io.BytesIO(base64.b64decode(content[1]["data"])))
    assert max(image.size) <= 400
    db = SessionLocal()
    set_tenant_context(db, None)
    page = db.query(ObjektDokumentSeite).filter_by(dokument_id=dokument_id, seiten_nr=1).one()
    page.thumb_pfad, page.bild_pfad = page.bild_pfad, None
    db.commit()
    db.close()
    response = _mcp(client, token, "tools/call", {"name": "objekt_dokument_seite_ansehen", "arguments": {
        "dokument_id": dokument_id, "seite": 1,
    }}, 22)
    assert _result(response)["content"][1]["mimeType"] == "image/jpeg"
    response = _mcp(client, token, "tools/call", {"name": "objekt_dokument_text", "arguments": {
        "dokument_id": dokument_id, "max_zeichen": 1000,
    }}, 3)
    text = json.loads(_result(response)["content"][0]["text"])
    # Seite 1 allein sprengt das Limit: gekuerzt liefern, weiter mit Seite 2 statt Endlosschleife.
    assert text["abgeschnitten"] and text["naechste_seite"] == 2
    assert text["seiten"][0]["seite_gekuerzt"] and len(text["seiten"][0]["volltext"]) == 1000
    response = _mcp(client, token, "tools/call", {"name": "objekt_dokument_text", "arguments": {
        "dokument_id": dokument_id, "von_seite": 2, "max_zeichen": 1000,
    }}, 5)
    text = json.loads(_result(response)["content"][0]["text"])
    assert not text["abgeschnitten"] and text["naechste_seite"] is None
    assert [s["nr"] for s in text["seiten"]] == [2]
    response = _mcp(client, token, "tools/call", {"name": "objekt_dokumente_auflisten", "arguments": {
        "objekt_id": seed["objekt_id"],
    }}, 4)
    listed = json.loads(_result(response)["content"][0]["text"])
    assert listed["dokumente"][0]["seiten"][0]["hat_bild"]


def test_seite_ohne_bild_und_fremde_organisation(client):
    seed = _seed("seiten-no-image", {"u": "objekt_verwalter", "r": "recorder"})
    token = _token(client, seed, "u")
    dokument_id = _upload(client, token, seed["objekt_id"])
    response = _mcp(client, token, "tools/call", {"name": "objekt_dokument_seite_ansehen", "arguments": {
        "dokument_id": dokument_id, "seite": 1,
    }}, 5)
    payload = json.loads(_result(response)["content"][0]["text"])
    assert not payload["hat_bild"] and "hinweis" in payload
    foreign = _seed("seiten-foreign", {"u": "objekt_verwalter"})
    foreign_token = _token(client, foreign, "u")
    response = _mcp(client, foreign_token, "tools/call", {"name": "objekt_dokument_text", "arguments": {
        "dokument_id": dokument_id,
    }}, 6)
    assert _result(response)["isError"]
    denied = _token(client, seed, "r")
    response = _mcp(client, denied, "tools/call", {"name": "objekt_dokument_text", "arguments": {
        "dokument_id": dokument_id,
    }}, 7)
    assert _result(response)["isError"]


def test_einsatz_seitentext_nur_freigegeben_und_verknuepft(client):
    seed = _seed("seiten-einsatz", {"read": "readonly"})
    token = _token(client, seed, "read")
    incident_id, _ = _einsatz(seed["org_id"])
    db = SessionLocal()
    set_tenant_context(db, None)
    dokument = ObjektDokument(org_id=seed["org_id"], objekt_id=seed["objekt_id"], dateiname_original="plan.pdf",
                              pfad="unused", seitenzahl=1, groesse_bytes=1, ist_aktuelle_version=True,
                              freigabe_status="freigegeben")
    db.add(dokument)
    db.flush()
    db.add(ObjektDokumentSeite(org_id=seed["org_id"], objekt_id=seed["objekt_id"], dokument_id=dokument.id,
                               seiten_nr=1, volltext="Einsatztext", text_quelle="pdf"))
    db.add(ObjektEinsatz(org_id=seed["org_id"], incident_id=incident_id, objekt_id=seed["objekt_id"],
                         quelle="manuell", status="bestaetigt"))
    db.commit()
    dokument_id = dokument.id
    db.close()
    response = _mcp(client, token, "tools/call", {"name": "einsatz_objekt_dokument_text", "arguments": {
        "incident_id": incident_id, "dokument_id": dokument_id,
    }}, 8)
    assert json.loads(_result(response)["content"][0]["text"])["seiten"][0]["volltext"] == "Einsatztext"
    db = SessionLocal()
    set_tenant_context(db, None)
    db.get(ObjektDokument, dokument_id).freigabe_status = "wartet_freigabe"
    db.commit()
    db.close()
    response = _mcp(client, token, "tools/call", {"name": "einsatz_objekt_dokument_text", "arguments": {
        "incident_id": incident_id, "dokument_id": dokument_id,
    }}, 9)
    assert _result(response)["isError"]
    db = SessionLocal()
    set_tenant_context(db, None)
    objekt = Objekt(org_id=seed["org_id"], nummer=99, name="Nicht verknuepft", status="freigegeben")
    db.add(objekt)
    db.flush()
    unlinked = ObjektDokument(org_id=seed["org_id"], objekt_id=objekt.id, dateiname_original="fremd.pdf",
                              pfad="unused", seitenzahl=1, groesse_bytes=1, ist_aktuelle_version=True,
                              freigabe_status="freigegeben")
    db.add(unlinked)
    db.commit()
    unlinked_id = unlinked.id
    db.close()
    response = _mcp(client, token, "tools/call", {"name": "einsatz_objekt_dokument_text", "arguments": {
        "incident_id": incident_id, "dokument_id": unlinked_id,
    }}, 10)
    assert _result(response)["isError"]
