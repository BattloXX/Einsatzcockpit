"""K11: Schreibgrenzen, unveraenderliche Freigaben und Upload-/Review-Pfade."""

import base64
import json
from io import BytesIO
from urllib.parse import urlsplit

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.config import settings
from app.core.permissions import KATPLAN_VERWALTER_ROLES
from app.core.tenant import set_tenant_context
from app.mcp.registry import TOOLS
from app.models.katplan import (
    KatplanAbschnitt, KatplanAenderung, KatplanAnhang, KatplanElement, KatplanElementPhase,
    KatplanImportVorschlag, KatplanPhase, KatplanPlan, KatplanVersionElement,
)
from app.models.mcp import MCPUpload
from app.models.user import User
from tests.conftest import TestingSession
from tests.test_katplan_k1b import umgebung  # noqa: F401
from tests.test_katplan_k8 import daten  # noqa: F401
from tests.test_mcp_fundament import _mcp, _oauth_tokens
from tests.test_mcp_katplan import inhalte, rufe  # noqa: F401
from tests.test_mcp_strassensperren import _rpc, _rufe

NAMEN = (
    "katplan_element_entwurf_anlegen", "katplan_element_entwurf_aendern",
    "katplan_anhang_upload_vorbereiten", "katplan_anhang_uebergeben", "katplan_vorschlaege_aus_dokument",
)


@pytest.fixture(autouse=True)
def upload_aufraeumen(inhalte):
    yield
    # Die Testdatenbank bleibt innerhalb der Session bestehen. Keine konsumierten
    # K11-Uploads fuer nachfolgende globale Retention-Tests hinterlassen.
    with TestingSession() as db:
        set_tenant_context(db, None)
        for row in db.query(MCPUpload).filter_by(org_id=inhalte["org_id"]).all():
            db.delete(row)
        db.commit()


def args(info):
    return (
        {"typ": "massnahme", "titel": "MCP Entwurf"},
        {"element_id": "KS-01", "version": 1, "felder": {"titel": "Neu"}},
        {"dateiname": "foto.png"},
        {"inhalt_base64": "eA==", "dateiname": "x.pdf"},
        {"dokument_id": info["dokument_id"], "seiten_nr": 1, "elemente": [{"titel": "Vorschlag"}]},
    )


def schreiben(info, name, **kwargs):
    return rufe(info, name, "katplan_verwalter", plan_id=info["plan_id"], **kwargs)


@pytest.mark.parametrize("rolle", ["readonly", "incident_leader"])
@pytest.mark.parametrize("index", range(5))
def test_leser_abgewiesen(inhalte, rolle, index):
    with pytest.raises(PermissionError):
        rufe(inhalte, NAMEN[index], rolle, plan_id=inhalte["plan_id"], **args(inhalte)[index])


@pytest.mark.parametrize("index", range(5))
def test_fremde_org_und_archiv(inhalte, index):
    with pytest.raises(ValueError, match="Nicht gefunden"):
        rufe(inhalte, NAMEN[index], "fremd", plan_id=inhalte["plan_id"], **args(inhalte)[index])
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(KatplanPlan, inhalte["plan_id"]).status = "archiviert"
        db.commit()
    with pytest.raises(ValueError, match="Archiviert"):
        schreiben(inhalte, NAMEN[index], **args(inhalte)[index])


def test_registry_und_http_wrapper(client, inhalte):
    for name in NAMEN:
        assert set(TOOLS[name].required_roles) == KATPLAN_VERWALTER_ROLES
        assert TOOLS[name].module_check is not None
    assert not any(n.startswith("katplan_") and any(w in n for w in ("einreichen", "freigeben")) for n in TOOLS)
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        ident = {"id": user.id, "org_id": user.org_id, "username": user.username}
    token = _oauth_tokens(client, ident)["access_token"]
    liste = _rpc(_mcp(client, token, "tools/list", {}, 12))["result"]["tools"]
    assert set(NAMEN) <= {t["name"] for t in liste}
    neu = _rufe(client, token, NAMEN[0], plan_id=inhalte["plan_id"], typ="info", titel="HTTP Entwurf")
    assert neu["element_id"]
    result = _rufe(client, token, NAMEN[1], plan_id=inhalte["plan_id"], element_id=neu["element_id"],
                   version=neu["version"], felder={"titel": "HTTP geändert"})
    assert result["version"] == neu["version"] + 1
    konflikt = _rufe(client, token, NAMEN[1], plan_id=inhalte["plan_id"], element_id=neu["element_id"],
                      version=neu["version"], felder={"titel": "Veraltet"})
    assert "Versionskonflikt" in konflikt["__fehler__"]
    vorbereitet = _rufe(client, token, NAMEN[2], plan_id=inhalte["plan_id"], dateiname="foto.png")
    assert vorbereitet["upload_token"]
    result = _rufe(client, token, NAMEN[3], plan_id=inhalte["plan_id"], dateiname="foto.png",
                   inhalt_base64=base64.b64encode(datei_bytes("PNG")).decode())
    assert result["anhang_id"]
    assert _rufe(client, token, NAMEN[4], plan_id=inhalte["plan_id"], dokument_id=inhalte["dokument_id"],
                  seiten_nr=1, elemente=[{"titel": "HTTP Review"}])["anzahl"] == 1


def test_anlegen_herkunft_protokoll_kapitel_phase_und_freigabe(inhalte):
    alt = rufe(inhalte, "katplan_elemente", plan_id=inhalte["plan_id"])
    with TestingSession() as db:
        set_tenant_context(db, None)
        vorher = db.query(KatplanAenderung).filter_by(plan_id=inhalte["plan_id"]).count()
        snapshot = [(e.id, e.titel, e.beschreibung_html) for e in db.query(KatplanVersionElement).filter_by(
            version_id=inhalte["version_id"])]
    neu = schreiben(inhalte, NAMEN[0], typ="massnahme", titel=" Sandsäcke ", kapitel_titel="Sicherung",
                     phasen=["Alarm", "Alarm"], beschreibung="• Eins\n- Zwei\n<script>alert(1)</script>",
                     prioritaet=1, tags=["Test"], adresse={"ort": "Wolfurt"},
                     quelle_dokument_id=inhalte["dokument_id"], quelle_seiten="1-2")
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.get(KatplanElement, neu["id"])
        assert e.herkunft == "ai_suggested" and e.titel == "Sandsäcke" and e.prioritaet == 1
        assert "<li>Eins</li><li>Zwei</li>" in e.beschreibung_html
        assert "<script>" not in e.beschreibung_html
        assert db.get(KatplanAbschnitt, e.abschnitt_id).titel == "Sicherung"
        z = db.query(KatplanElementPhase).filter_by(element_id=e.id).one()
        assert db.get(KatplanPhase, z.phase_id).name == "Alarm"
        assert "wolfurt" in e.suchtext and "eins" in e.suchtext
        assert db.get(KatplanPlan, e.plan_id).status == "in_ueberarbeitung"
        logs = db.query(KatplanAenderung).filter_by(plan_id=e.plan_id).order_by(KatplanAenderung.id).all()[vorher:]
        assert logs and all(a.quelle == "mcp" for a in logs)
        assert snapshot == [(e.id, e.titel, e.beschreibung_html) for e in db.query(KatplanVersionElement).filter_by(
            version_id=inhalte["version_id"])]
    assert rufe(inhalte, "katplan_elemente", plan_id=inhalte["plan_id"]) == alt
    # Bestehende Kapitel und Phasen werden wiederverwendet.
    wieder = schreiben(inhalte, NAMEN[0], typ="info", titel="Zweit", kapitel_titel="Sicherung", phasen=["Alarm"])
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.get(KatplanElement, wieder["id"]).abschnitt_id == db.get(KatplanElement, neu["id"]).abschnitt_id
        assert db.query(KatplanPhase).filter_by(plan_id=inhalte["plan_id"], name="Alarm").count() == 1


@pytest.mark.parametrize("extra", [
    {"titel": ""}, {"titel": "x" * 201}, {"prioritaet": 4}, {"daten": []},
    {"tags": ["x"] * 21}, {"tags": ["x" * 51]}, {"adresse": {"ort": "x" * 101}},
    {"geometrie": {"type": "Point", "coordinates": [500, 47]}}, {"typ": "unbekannt"},
])
def test_feldvalidierung_ohne_teilanlage(inhalte, extra):
    with pytest.raises(ValueError):
        schreiben(inhalte, NAMEN[0], **{"typ": "kontrollstelle", "titel": "Test", **extra})
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(KatplanElement).filter_by(plan_id=inhalte["plan_id"]).count() == 3


def test_aendern_version_kennung_geometrie_und_herkunft(inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        version = db.get(KatplanElement, inhalte["element_id"]).version
        log_start = db.query(KatplanAenderung).filter_by(plan_id=inhalte["plan_id"]).count()
    with pytest.raises(ValueError, match="Versionskonflikt"):
        schreiben(inhalte, NAMEN[1], element_id="KS-01", version=version + 100, felder={"titel": "Falsch"})
    for feld in ("vertraulich", "herkunft", "geo_status", "gsl_vorlage", "gsl_auftragsart"):
        with pytest.raises(ValueError, match=feld):
            schreiben(inhalte, NAMEN[1], element_id="KS-01", version=version, felder={feld: "geprueft"})
    with pytest.raises(ValueError):
        schreiben(inhalte, NAMEN[1], element_id="KS-01", version=version,
                   felder={"geometrie": {"type": "Point", "coordinates": [9, 500]}})
    result = schreiben(inhalte, NAMEN[1], element_id="KS-01", version=version,
                        felder={"titel": "Neu Wasser", "phasen": ["MCP Phase"], "tags": ["Kontrolle"]})
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.get(KatplanElement, inhalte["element_id"])
        assert e.geo_status == "geprueft" and e.herkunft == "manual"
    geom = {"type": "Point", "coordinates": [9.1, 47.1]}
    result = schreiben(inhalte, NAMEN[1], element_id=inhalte["uuid"], version=result["version"],
                        felder={"geometrie": geom, "beschreibung": "• Prüfen", "gueltig_bis": "2027-01-01"})
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.get(KatplanElement, inhalte["element_id"])
        assert e.herkunft == "manual" and e.geo_status == "ungeprueft"
        assert json.loads(e.geometry_json) == geom and (e.lat, e.lng) == (47.1, 9.1)
        assert "neu wasser" in e.suchtext and "prüfen" in e.suchtext
        logs = db.query(KatplanAenderung).filter_by(plan_id=e.plan_id).order_by(KatplanAenderung.id).all()[log_start:]
        assert logs and all(a.quelle == "mcp" for a in logs)
    assert rufe(inhalte, "katplan_element_lesen", plan_id=inhalte["plan_id"], element_id="KS-01")["title"] == "Wasser Brücke"


def datei_bytes(art):
    out = BytesIO()
    if art == "PDF":
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(out)
    else:
        Image.new("RGB", (24, 16), "red").save(out, art)
    return out.getvalue()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "uploads"))
    return tmp_path / "media"


@pytest.mark.parametrize("art,endung,mime", [("PNG", "png", "image/png"), ("JPEG", "jpg", "image/jpeg"),
                                           ("WEBP", "webp", "image/webp"), ("PDF", "pdf", "application/pdf")])
def test_anhang_upload_quota_und_mcp_protokoll(client, inhalte, storage, art, endung, mime):
    from tests.test_katplan_k6a import usage

    raw = datei_bytes(art)
    start = schreiben(inhalte, NAMEN[2], dateiname=f"anhang.{endung}", element_id="KS-01", groesse_bytes=len(raw))
    response = client.post(urlsplit(start["upload_url"]).path,
                           headers={"Authorization": "Bearer " + start["upload_token"]},
                           files={"datei": (f"anhang.{endung}", raw, mime)})
    assert response.status_code == 200, response.text
    result = schreiben(inhalte, NAMEN[3], upload_id=start["upload_id"], element_id="KS-01", beschriftung="Anhang")
    with TestingSession() as db:
        set_tenant_context(db, None)
        a = db.get(KatplanAnhang, result["anhang_id"])
        assert a.element_id == inhalte["element_id"]
        assert (storage / a.pfad).is_file()
        assert usage(db, inhalte["org_id"]) == a.groesse_bytes > 0
        assert db.query(KatplanAenderung).filter_by(plan_id=inhalte["plan_id"], entitaet="anhang").one().quelle == "mcp"
        assert db.query(MCPUpload).filter_by(upload_id=start["upload_id"]).one().uebergeben_am
    with pytest.raises(ValueError, match="bereits"):
        schreiben(inhalte, NAMEN[3], upload_id=start["upload_id"])


def test_upload_benutzer_zweck_zuordnung_und_archiv(client, inhalte, storage):
    raw = datei_bytes("PNG")
    start = schreiben(inhalte, NAMEN[2], dateiname="foto.png")
    assert client.post(urlsplit(start["upload_url"]).path,
                       headers={"Authorization": "Bearer " + start["upload_token"]},
                       files={"datei": ("foto.png", raw)}).status_code == 200
    with pytest.raises(ValueError, match="nicht gefunden"):
        rufe(inhalte, NAMEN[3], "katplan_freigeber", plan_id=inhalte["plan_id"], upload_id=start["upload_id"])
    with TestingSession() as db:
        set_tenant_context(db, None)
        row = db.query(MCPUpload).filter_by(upload_id=start["upload_id"]).one()
        row.zweck = "objekt"
        db.commit()
    with pytest.raises(ValueError, match="Zweck"):
        schreiben(inhalte, NAMEN[3], upload_id=start["upload_id"])
    with TestingSession() as db:
        set_tenant_context(db, None)
        anderer = KatplanPlan(org_id=inhalte["org_id"], titel="Anderer Plan")
        db.add(anderer)
        db.flush()
        e = KatplanElement(org_id=inhalte["org_id"], plan_id=anderer.id, titel="Fremd", typ="info")
        db.add(e)
        db.commit()
        uid = e.uuid
    for name in (NAMEN[2], NAMEN[3]):
        with pytest.raises(ValueError, match="Element nicht gefunden"):
            schreiben(inhalte, name, dateiname="foto.png", element_id=uid)
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(MCPUpload).filter_by(upload_id=start["upload_id"]).one().zweck = "katplan_anhang"
        db.get(KatplanPlan, inhalte["plan_id"]).status = "archiviert"
        db.commit()
    with pytest.raises(ValueError, match="Archiviert"):
        schreiben(inhalte, NAMEN[3], upload_id=start["upload_id"])


def test_inline_download_url_und_dateityp(inhalte, storage, monkeypatch):
    import app.mcp.tools.katplan_schreiben as tools

    raw = datei_bytes("PNG")
    calls = []

    def download(url, limit):
        calls.append((url, limit))
        return raw

    monkeypatch.setattr(tools, "lade_datei_von_url", download)
    assert schreiben(inhalte, NAMEN[3], datei={"download_url": "https://example.org/foto", "file_name": "foto.png"})["uuid"]
    assert calls[0][0] == "https://example.org/foto"
    assert schreiben(inhalte, NAMEN[3], dateiname="foto.png", inhalt_base64=base64.b64encode(raw).decode())["uuid"]
    for params in ({}, {"datei": {}}, {"inhalt_base64": "?"},
                   {"inhalt_base64": "eA==", "dateiname": "script.exe"},
                   {"inhalt_base64": "eA==", "upload_id": "x"}):
        with pytest.raises(ValueError):
            schreiben(inhalte, NAMEN[3], **params)


def test_vorschlaege_offen_kein_element_und_quellgrenzen(inhalte):
    result = schreiben(inhalte, NAMEN[4], dokument_id=inhalte["dokument_id"], seiten_nr=1,
                        elemente=[{"typ": "massnahme", "titel": "Review", "beschreibung": ["• Prüfen"],
                                   "kapitel": "Sicherheit", "phase": "Alarm", "kontakte": [], "adresse": {"ort": "Wolfurt"}}])
    assert result["anzahl"] == 1 and "&seite=1" in result["ui_link"]
    with TestingSession() as db:
        set_tenant_context(db, None)
        v = db.query(KatplanImportVorschlag).filter_by(plan_id=inhalte["plan_id"]).one()
        assert v.status == "offen" and v.element_id is None and v.titel == "Review"
        assert v.dokument_id == inhalte["dokument_id"] and v.seiten_nr == 1
        assert v.ort == "Wolfurt"
        assert db.query(KatplanElement).filter_by(plan_id=inhalte["plan_id"]).count() == 3
    for params in ({"dokument_id": inhalte["dokument_id"], "seiten_nr": 26},
                   {"dokument_id": inhalte["dokument_id"], "seiten_nr": 0},
                   {"dokument_id": 99999999, "seiten_nr": 1}):
        with pytest.raises(ValueError):
            schreiben(inhalte, NAMEN[4], elemente=[{"titel": "Falsch"}], **params)
    with pytest.raises(ValueError, match="30"):
        schreiben(inhalte, NAMEN[4], dokument_id=inhalte["dokument_id"], seiten_nr=1, elemente=[{}] * 31)
