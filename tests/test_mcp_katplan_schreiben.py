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
    assert set(neu["geaendert"]) == {"typ", "titel", "prioritaet"}
    result = _rufe(client, token, NAMEN[1], plan_id=inhalte["plan_id"], element_id=neu["element_id"],
                   version=neu["version"], felder={"titel": "HTTP geändert"})
    assert result["version"] == neu["version"] + 1
    konflikt = _rufe(client, token, NAMEN[1], plan_id=inhalte["plan_id"], element_id=neu["element_id"],
                      version=neu["version"], felder={"titel": "Veraltet"})
    assert "Versionskonflikt" in konflikt["__fehler__"]
    probe = _rufe(client, token, NAMEN[0], plan_id=inhalte["plan_id"], typ="info", titel="HTTP Probe",
                  kapitel_titel="HTTP Probe", phasen=["HTTP Probe"], pruefen=True)
    assert probe["gespeichert"] is False and probe["element"]["chapter"] == "HTTP Probe"
    streng = _rufe(client, token, NAMEN[1], plan_id=inhalte["plan_id"], element_id=neu["element_id"],
                   version=result["version"], felder={"daten": {"fremd": 1}}, streng=True)
    assert "Nicht gespeichert" in streng["__fehler__"]
    probe = _rufe(client, token, NAMEN[1], plan_id=inhalte["plan_id"], element_id=neu["element_id"],
                  version=result["version"], felder={"titel": "HTTP Probe geändert"}, pruefen=True)
    assert probe["gespeichert"] is False and probe["element"]["title"] == "HTTP Probe geändert"
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


def test_adresse_verorten_und_validierung(inhalte, monkeypatch):
    from app.services import geocoding

    suchen = []

    async def suche(q, limit=5):
        suchen.append(q)
        return [{"display_name": "Eulentobel 7, 6922 Wolfurt", "strasse": "Eulentobel", "hausnummer": "7",
                 "plz": "6922", "ort": "Wolfurt", "lat": 47.47, "lng": 9.75}]

    monkeypatch.setattr(geocoding, "search_addresses", suche)
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        user.org.city = "Wolfurt"
        db.commit()
    neu = schreiben(inhalte, "katplan_element_entwurf_anlegen", typ="kontrollstelle", titel="Eulentobelbach",
                    adresse={"strasse": "Eulentobel", "hausnummer": "7"}, verorten=True)
    # Ohne Ort wird der Ort der Organisation fuer die Suche verwendet.
    assert suchen == ["Eulentobel 7, Wolfurt"]
    assert neu["verortung"]["verortet"] is True and neu["verortung"]["geo_status"] == "ungeprueft"
    with TestingSession() as db:
        set_tenant_context(db, inhalte["org_id"])
        e = db.query(KatplanElement).filter_by(uuid=neu["element_id"]).one()
        assert (e.strasse, e.hausnummer, e.plz, e.ort) == ("Eulentobel", "7", "6922", "Wolfurt")
        assert e.geo_status == "ungeprueft" and json.loads(e.geometry_json)["type"] == "Point"
    # Teiladresse aendert nur das uebergebene Feld.
    geaendert = schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=neu["element_id"],
                          version=neu["version"], felder={"adresse": {"hausnummer": "9"}})
    with TestingSession() as db:
        set_tenant_context(db, inhalte["org_id"])
        e = db.query(KatplanElement).filter_by(uuid=neu["element_id"]).one()
        assert (e.strasse, e.hausnummer, e.ort) == ("Eulentobel", "9", "Wolfurt")
    with pytest.raises(ValueError, match="Unbekannte Adressfelder: adresse"):
        schreiben(inhalte, "katplan_element_entwurf_aendern", element_id=neu["element_id"],
                  version=geaendert["version"], felder={"adresse": {"adresse": "Eulentobel 7"}})
    # Reine Linien-/Flaechentypen werden nicht als Punkt verortet.
    flaeche = schreiben(inhalte, "katplan_element_entwurf_anlegen", typ="gefahrenbereich", titel="Fläche",
                        adresse={"strasse": "Eulentobel"}, verorten=True)
    assert flaeche["verortung"]["verortet"] is False
    # Lesetools liefern die Adresse auch strukturiert.
    gelesen = rufe(inhalte, "katplan_element_lesen", "katplan_verwalter", plan_id=inhalte["plan_id"],
                   element_id=neu["element_id"], fassung="arbeitsstand")
    assert gelesen["address_parts"] == {"strasse": "Eulentobel", "hausnummer": "9", "plz": "6922", "ort": "Wolfurt"}


def test_adresse_schema_fuer_ki_clients(client, inhalte):
    with TestingSession() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter_by(username=inhalte["users"]["katplan_verwalter"]).one()
        ident = {"id": user.id, "org_id": user.org_id, "username": user.username}
    token = _oauth_tokens(client, ident)["access_token"]
    tools = {t["name"]: t for t in _rpc(_mcp(client, token, "tools/list", {}, 13))["result"]["tools"]}
    schema = tools["katplan_element_entwurf_anlegen"]["inputSchema"]
    adresse = schema["properties"]["adresse"]
    if "anyOf" in adresse:
        adresse = next(s for s in adresse["anyOf"] if s.get("type") != "null")
    if "$ref" in adresse:
        adresse = schema["$defs"][adresse["$ref"].rsplit("/", 1)[-1]]
    assert set(adresse["properties"]) == {"strasse", "hausnummer", "plz", "ort"}
    assert "verorten" in schema["properties"]
    assert "verorten" in tools["katplan_element_entwurf_aendern"]["inputSchema"]["properties"]


def lesen_arbeit(inhalte, element_id):
    return rufe(inhalte, "katplan_element_lesen", "katplan_verwalter", plan_id=inhalte["plan_id"],
                element_id=element_id, fassung="arbeitsstand")


def test_m1_zahlen_verluste_und_streng(inhalte):
    daten = {"bedarf": [{"bezeichnung": "Sandsäcke", "menge": 500, "einheit": "Stk"}]}
    neu = schreiben(inhalte, NAMEN[0], typ="ressource", titel="Material", daten=daten)
    assert neu["gespeichert"] is True and neu["verworfen"] == []
    assert set(neu["geaendert"]) == {"typ", "titel", "prioritaet", "daten"}
    assert neu["element"]["daten"]["bedarf"][0]["menge"] == "500"
    daten_neu = {"bedarf": [{"bezeichnung": "Sandsäcke", "menge": 501, "einheit": "Stk",
                             "lieferant": "X"}], "lieferant": "X"}
    angepasst = schreiben(inhalte, NAMEN[1], element_id=neu["element_id"], version=neu["version"],
                           felder={"daten": daten_neu})
    assert angepasst["geaendert"] == ["daten"] and len(angepasst["verworfen"]) == 2
    assert angepasst["element"]["daten"]["bedarf"][0]["menge"] == "501"
    neu = schreiben(inhalte, NAMEN[1], element_id=neu["element_id"], version=angepasst["version"],
                     felder={"daten": daten})
    assert neu["element"] == lesen_arbeit(inhalte, neu["element_id"])
    daten["lieferant"] = "Unbekannt"
    daten["bedarf"][0]["lieferant"] = "Unbekannt"
    with pytest.raises(ValueError, match="Nicht gespeichert:.*daten.bedarf.*daten.lieferant"):
        schreiben(inhalte, NAMEN[1], element_id=neu["element_id"], version=neu["version"],
                   felder={"titel": "Verlust", "daten": daten}, streng=True)
    assert lesen_arbeit(inhalte, neu["element_id"]) == neu["element"]
    result = schreiben(inhalte, NAMEN[1], element_id=neu["element_id"], version=neu["version"],
                        felder={"titel": "Material", "daten": daten})
    assert result["geaendert"] == []
    assert result["unveraendert"] == ["titel", "daten"]
    assert set(result["verworfen"]) == {"daten.lieferant: unbekanntes Feld",
                                         "daten.bedarf[0].lieferant: unbekanntes Feld"}
    with pytest.raises(ValueError, match="Nicht gespeichert"):
        schreiben(inhalte, NAMEN[0], typ="ressource", titel="Streng", daten=daten,
                   kapitel_titel="Nie angelegt", phasen=["Nie angelegt"], streng=True)
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert not db.query(KatplanElement).filter_by(plan_id=inhalte["plan_id"], titel="Streng").count()


def test_m1_leeren_und_version(inhalte):
    neu = schreiben(inhalte, NAMEN[0], typ="kontrollstelle", titel="Adresse",
                     adresse={"strasse": "Weg", "hausnummer": "7", "ort": "Wolfurt", "plz": "6922"},
                     geometrie={"type": "Point", "coordinates": [9.7, 47.4]},
                     quelle_dokument_id=inhalte["dokument_id"], quelle_seiten="1-2")
    for eingabe, erwartet in [
        ({"hausnummer": None}, {"strasse": "Weg", "hausnummer": "", "ort": "Wolfurt", "plz": "6922"}),
        ({"ort": ""}, {"strasse": "Weg", "hausnummer": "", "ort": "", "plz": "6922"}),
        (None, {"strasse": "", "hausnummer": "", "ort": "", "plz": ""}),
    ]:
        gelesen = lesen_arbeit(inhalte, neu["element_id"])
        assert gelesen["version"] == neu["version"]
        assert not any(w.startswith(("version:", "element_version_id:", "plan_version_id:"))
                       for w in gelesen["warnings"])
        neu = schreiben(inhalte, NAMEN[1], element_id=neu["element_id"], version=gelesen["version"],
                         felder={"adresse": eingabe})
        assert neu["element"]["address_parts"] == erwartet
        assert neu["geaendert"] == ["adresse"]
    with pytest.raises(ValueError, match=f'hat version {neu["version"]}, übergeben 9999'):
        schreiben(inhalte, NAMEN[1], element_id=neu["element_id"], version=9999, felder={"titel": "Alt"})
    neu = schreiben(inhalte, NAMEN[1], element_id=neu["element_id"], version=neu["version"],
                     felder={"geometrie": None, "quelle_dokument_id": None})
    assert neu["element"]["geometry"] is None and neu["element"]["geo_status"] == "keine"
    assert neu["element"]["source_references"] == []
    with TestingSession() as db:
        set_tenant_context(db, None)
        e = db.get(KatplanElement, neu["id"])
        assert e.geometry_json is e.lat is e.lng is e.quelle_dokument_id is e.quelle_seiten is None
    kopf = rufe(inhalte, "katplan_lesen", "katplan_verwalter", plan_id=inhalte["plan_id"], fassung="arbeitsstand")
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert kopf["plan_version"] == db.get(KatplanPlan, inhalte["plan_id"]).version
    freigabe = rufe(inhalte, "katplan_element_lesen", plan_id=inhalte["plan_id"], element_id="KS-01")
    assert freigabe["version"] is None and "version: nicht vorhanden." not in freigabe["warnings"]
    assert rufe(inhalte, "katplan_lesen", plan_id=inhalte["plan_id"])["plan_version"] is None


def test_m1_trockenlauf_ohne_rueckstaende(inhalte):
    modelle = (KatplanPlan, KatplanElement, KatplanAbschnitt, KatplanPhase, KatplanElementPhase, KatplanAenderung)

    def zustand():
        with TestingSession() as db:
            set_tenant_context(db, None)
            return [[{c.name: getattr(row, c.name) for c in m.__table__.columns}
                     for row in db.query(m).filter_by(org_id=inhalte["org_id"]).order_by(m.id)] for m in modelle]

    vorher = zustand()
    neu = schreiben(inhalte, NAMEN[0], typ="ressource", titel="Probe", kapitel_titel="Probe-Kapitel",
                     phasen=["Probe-Phase"], daten={"bedarf": [{"menge": 500}]}, pruefen=True)
    assert neu["gespeichert"] is False
    assert neu["element"]["title"] == "Probe" and neu["element"]["chapter"] == "Probe-Kapitel"
    assert neu["element"]["phases"] == ["Probe-Phase"]
    assert zustand() == vorher
    gelesen = lesen_arbeit(inhalte, "KS-01")
    result = schreiben(inhalte, NAMEN[1], element_id="KS-01", version=gelesen["version"],
                        felder={"titel": "Probe neu", "phasen": ["Neue Probe-Phase"]}, pruefen=True)
    assert result["gespeichert"] is False and result["element"]["title"] == "Probe neu"
    assert result["element"]["phases"] == ["Neue Probe-Phase"]
    assert set(result["geaendert"]) == {"titel", "phasen"}
    assert zustand() == vorher
    assert lesen_arbeit(inhalte, "KS-01") == gelesen


def test_m1_anlegen_nur_wirksame_eingaben(inhalte):
    neu = schreiben(inhalte, NAMEN[0], typ="info", titel="Leerwerte", prioritaet=2,
                     adresse={"ort": " "}, tags=[" "], daten={"unbekannt": 500})
    assert set(neu["geaendert"]) == {"typ", "titel", "prioritaet"}
    assert neu["verworfen"] == ["daten.unbekannt: unbekanntes Feld"]
    assert not any(neu["element"]["address_parts"].values())
