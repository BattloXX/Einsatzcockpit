"""BMA-Webplattform-Import (Landeswarnzentrale Vorarlberg) -> Objektverwaltung.

PR 1: Modelle, Tenant-Registrierung, Isolation.
"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

# BigInteger → INTEGER für SQLite-Testumgebung
@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.bma_import import (
    BMA_LAUF_FEHLER,
    BMA_LAUF_SESSION_ABGELAUFEN,
    BMA_SATZ_AKTIV,
    BMA_ZUORDNUNG_AUTO,
    BMA_ZUORDNUNG_OFFEN,
    BmaImportLauf,
    BmaImportSatz,
    OrgBmaImportConfig,
)
from app.models.master import FireDept
from app.models.objekt import OBJEKT_STATUS_ENTWURF, Objekt, ObjektBMA, ObjektKontakt


# ── Modelle / Registrierung ───────────────────────────────────────────────────

def test_bma_import_models_in_tenant_tables():
    from app.core.tenant import _TENANT_TABLE_NAMES
    for tbl in ("bma_import_satz", "bma_import_lauf"):
        assert tbl in _TENANT_TABLE_NAMES, f"{tbl} fehlt in _TENANT_TABLE_NAMES"
    # org_bma_import_config ist plain Base (kein TenantScoped), Muster org_dibos_config
    assert "org_bma_import_config" not in _TENANT_TABLE_NAMES


def test_bma_import_models_importable_from_package():
    from app.models import BmaImportLauf as _L
    from app.models import BmaImportSatz as _S
    from app.models import OrgBmaImportConfig as _C
    assert _L is BmaImportLauf
    assert _S is BmaImportSatz
    assert _C is OrgBmaImportConfig


def test_objekt_kontakt_hat_externe_identitaet_spalten():
    kontakt = ObjektKontakt(objekt_id=1, name="Test")
    assert kontakt.extern_quelle is None
    assert kontakt.extern_id is None


# ── DB-Fixture (zwei Orgs, Muster: tests/test_objekt_pr2.py::pr2_db) ─────────

@pytest.fixture(scope="module")
def bma_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    org_a = FireDept(slug="bma-org-a", name="Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug="bma-org-b", name="Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()

    set_tenant_context(db, None)
    objekt_a = Objekt(org_id=org_a.id, nummer=1, name="Anlage A", status=OBJEKT_STATUS_ENTWURF)
    objekt_b = Objekt(org_id=org_b.id, nummer=1, name="Anlage B", status=OBJEKT_STATUS_ENTWURF)
    db.add_all([objekt_a, objekt_b])
    db.flush()

    db.add(BmaImportSatz(
        org_id=org_a.id, extern_id="174", bma_nummer="1020", bezeichnung="Anlage A",
        objekt_id=objekt_a.id, zuordnung=BMA_ZUORDNUNG_OFFEN, status=BMA_SATZ_AKTIV,
        erst_gesehen_am=datetime.now(UTC), zuletzt_gesehen_am=datetime.now(UTC),
    ))
    db.add(BmaImportLauf(org_id=org_a.id, gestartet_am=datetime.now(UTC), gefunden=1))
    db.commit()

    yield db, org_a.id, org_b.id, objekt_a.id, objekt_b.id

    db.close()
    Base.metadata.drop_all(bind=engine)


def test_bma_import_satz_isolation(bma_db):
    db, org_a_id, org_b_id, _, _ = bma_db
    set_tenant_context(db, org_a_id)
    assert db.query(BmaImportSatz).count() == 1
    set_tenant_context(db, org_b_id)
    assert db.query(BmaImportSatz).count() == 0


def test_bma_import_lauf_isolation(bma_db):
    db, org_a_id, org_b_id, _, _ = bma_db
    set_tenant_context(db, org_a_id)
    assert db.query(BmaImportLauf).count() == 1
    set_tenant_context(db, org_b_id)
    assert db.query(BmaImportLauf).count() == 0


def test_bma_import_satz_unique_je_org_und_extern_id(bma_db):
    db, org_a_id, org_b_id, objekt_a_id, objekt_b_id = bma_db
    set_tenant_context(db, None)

    # Gleiche extern_id in einer ANDEREN Org ist erlaubt (Unique ist org-skoped)
    db.add(BmaImportSatz(
        org_id=org_b_id, extern_id="174", bma_nummer="1020", bezeichnung="Anlage B",
        objekt_id=objekt_b_id, zuordnung=BMA_ZUORDNUNG_OFFEN, status=BMA_SATZ_AKTIV,
        erst_gesehen_am=datetime.now(UTC), zuletzt_gesehen_am=datetime.now(UTC),
    ))
    db.commit()

    from sqlalchemy.exc import IntegrityError
    db.add(BmaImportSatz(
        org_id=org_a_id, extern_id="174", bma_nummer="1020-dup",
        zuordnung=BMA_ZUORDNUNG_OFFEN, status=BMA_SATZ_AKTIV,
        erst_gesehen_am=datetime.now(UTC), zuletzt_gesehen_am=datetime.now(UTC),
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_org_bma_import_config_is_fully_configured():
    cfg = OrgBmaImportConfig(org_id=1)
    assert cfg.is_fully_configured is False
    cfg.base_url = "https://dibos.lwz-vorarlberg.at/LWZ_BMA_Webplattform"
    cfg.session_cookie_enc = "verschluesselt"
    assert cfg.is_fully_configured is True


# ── PR 2: bma_parser.py (Struktur/Feldnamen aus einem echten Mitschnitt,
#    Personen/Adressen sind frei erfunden) ───────────────────────────────────

from app.services.bma_import import bma_parser  # noqa: E402

LISTE_ANTWORT = {
    "Data": [
        {
            "Id": 174, "Guid": "9f9b2df6-f04c-4694-96dc-1c67ea59de28",
            "BMANR": "1020", "Bezeichnung": "Testareal 1 Musterort",
            "Address": {
                "Strasse": "Musterstraße", "Hausnummer": "16", "PLZ": "6922",
                "Ort": "Musterort", "Latitude": "47,455640961990206",
                "Longitude": "9,735831280936772",
            },
            "PaymentAddress": {
                "PaymentName": "Muster Immobilien GmbH", "PaymentEmail": "",
                "Strasse": "Beispielgasse 10", "PLZ": "6850", "Ort": "Beispielstadt",
            },
            "IsRFL": True, "IsActive": True,
            "Anlagedatum": "2020-02-19T10:01:01.11",
            "Aufschaltdatum": "1998-01-22T00:00:00",
            "ChangeDate": "2026-07-23T00:02:03.0051136",
            "ESZ": 12950116, "Abschnitt": "FW - Abschnitt - 29", "Bezirk": "FW - Bezirk - Bregenz",
        },
        {
            # Zweiter Datensatz: kein RFL, kein Aufschaltdatum, keine Rechnungsadresse
            "Id": 183, "Guid": "aaaa-bbbb", "BMANR": "1035",
            "Bezeichnung": "Testareal 2 Musterort",
            "Address": {
                "Strasse": "Musterstraße", "Hausnummer": "18b", "PLZ": "6922",
                "Ort": "Musterort", "Latitude": "47,4", "Longitude": "9,7",
            },
            "PaymentAddress": None,
            "IsRFL": False, "IsActive": True,
            "Anlagedatum": "2019-01-01T00:00:00",
            "Aufschaltdatum": None,
            "ChangeDate": "2026-07-20T08:00:00",
            "ESZ": 12950116, "Abschnitt": "FW - Abschnitt - 29", "Bezirk": "FW - Bezirk - Bregenz",
        },
        # Datensatz ohne Id -> muss uebersprungen werden
        {"Id": None, "BMANR": "9999", "Bezeichnung": "Kaputter Datensatz"},
    ],
    "Total": 3,
}


def test_parse_anlagen_grundfelder():
    zeilen = bma_parser.parse_anlagen(LISTE_ANTWORT)
    assert len(zeilen) == 2  # der Datensatz ohne Id wird uebersprungen

    a = zeilen[0]
    assert a["extern_id"] == "174"
    assert a["extern_guid"] == "9f9b2df6-f04c-4694-96dc-1c67ea59de28"
    assert a["bma_nummer"] == "1020"
    assert a["bezeichnung"] == "Testareal 1 Musterort"
    assert a["strasse"] == "Musterstraße"
    assert a["hausnummer"] == "16"
    assert a["plz"] == "6922"
    assert a["ort"] == "Musterort"
    assert a["is_rfl"] is True
    assert a["is_active"] is True


def test_parse_anlagen_komma_dezimaltrennzeichen_wird_konvertiert():
    zeilen = bma_parser.parse_anlagen(LISTE_ANTWORT)
    a = zeilen[0]
    assert a["lat"] == pytest.approx(47.455640961990206)
    assert a["lng"] == pytest.approx(9.735831280936772)


def test_parse_anlagen_datumsfelder():
    zeilen = bma_parser.parse_anlagen(LISTE_ANTWORT)
    a = zeilen[0]
    assert a["anlagedatum"] == datetime(2020, 2, 19, 10, 1, 1, 110000)
    assert a["aufschaltdatum"] == datetime(1998, 1, 22, 0, 0, 0)
    # > 6 Nachkommastellen werden auf Mikrosekunden-Praezision gekappt, nicht verworfen
    assert a["change_date"] == datetime(2026, 7, 23, 0, 2, 3, 5113)


def test_parse_anlagen_fehlendes_aufschaltdatum_wird_none():
    zeilen = bma_parser.parse_anlagen(LISTE_ANTWORT)
    assert zeilen[1]["aufschaltdatum"] is None
    assert zeilen[1]["is_rfl"] is False


def test_parse_anlagen_rechnungsadresse_optional():
    zeilen = bma_parser.parse_anlagen(LISTE_ANTWORT)
    assert zeilen[0]["rechnungsadresse"]["name"] == "Muster Immobilien GmbH"
    assert zeilen[1]["rechnungsadresse"] is None


def test_parse_anlagen_leere_antwort():
    assert bma_parser.parse_anlagen(None) == []
    assert bma_parser.parse_anlagen({}) == []
    assert bma_parser.parse_anlagen({"Data": []}) == []


# Detailseiten-HTML: Struktur (Card-Aufbau, bare Text-Labels vor <b>-Werten,
# editContactData-onclick mit stabiler ID) 1:1 aus dem Mitschnitt, Namen/
# Adressen/Kontaktdaten frei erfunden.
DETAIL_HTML = """
<html><body>
<div class="card">
    <b>1020</b><b>Testareal 1 Musterort</b><b>Ja</b>
</div>
<div class="card">
    <b class="text-success">Brandschutzbeauftragte(r)</b>
    Name:
    <b>Max Mustermann</b>
    Adresse:
    <b>6890 Beispielort Musterweg 19</b>
    Tel.Mobil Privat:
    <b>+43 664 1234567</b>
    EMail Beruf:
    <b>max.mustermann@example.at</b>
    Zuletzt aktualisiert:
    <b></b>
    <a onclick="editContactData(172, 174, 1)">Kontaktdaten bearbeiten</a>
    <a onclick="editPersonData(172, 174, 1)">Neue Person eintragen</a>
    <a onclick="deletePersonData(172, 174, 1)">Person löschen</a>
</div>
<div class="card">
    <b class="text-success">BMA Alarmperson</b>
    Name:
    <b>Erika Musterfrau</b>
    Adresse:
    <b>6850 Beispielstadt Ringstraße 3</b>
    Telefon Beruf:
    <b>+43 5572 000000</b>
    Tel.Mobil Beruf:
    <b>+43 664 7654321</b>
    Tel.Mobil Privat:
    <b>+43 664 7654322</b>
    Pager:
    <b>4711</b>
    EMail Beruf:
    <b>erika.musterfrau@example.at</b>
    EMail Privat:
    <b>erika.privat@example.at</b>
    Zuletzt aktualisiert:
    <b></b>
    <a onclick="editContactData(172, 174, 2)">Kontaktdaten bearbeiten</a>
    <a onclick="editPersonData(172, 174, 2)">Neue Person eintragen</a>
    <a onclick="deletePersonData(172, 174, 2)">Person löschen</a>
</div>
<div class="card">
    <b class="text-success">BMA Alarmperson</b>
    Name:
    <b>Dieter Beispiel</b>
    Adresse:
    <b>6850 Beispielstadt Nebengasse 7</b>
    Tel.Mobil Privat:
    <b>+43 664 9998877</b>
    EMail Beruf:
    <b>dieter.beispiel@example.at</b>
    Zuletzt aktualisiert:
    <b></b>
    <a onclick="editContactData(313, 174, 2)">Kontaktdaten bearbeiten</a>
    <a onclick="editPersonData(313, 174, 2)">Neue Person eintragen</a>
    <a onclick="deletePersonData(313, 174, 2)">Person löschen</a>
</div>
<div class="card">
    <b>Neue Person hinzufügen</b>
</div>
</body></html>
"""


def test_parse_kontakte_anzahl_und_reihenfolge():
    kontakte = bma_parser.parse_kontakte(DETAIL_HTML)
    # Die allgemeine Anlagen-Info-Karte und die "Neue Person hinzufuegen"-Karte
    # haben keine Rolle (kein b.text-success) und werden uebersprungen.
    assert len(kontakte) == 3
    assert [k["name"] for k in kontakte] == ["Max Mustermann", "Erika Musterfrau", "Dieter Beispiel"]


def test_parse_kontakte_rollen_mapping():
    kontakte = bma_parser.parse_kontakte(DETAIL_HTML)
    assert kontakte[0]["art"] == "brandschutzbeauftragter"
    assert kontakte[0]["rolle_quelle"] == "Brandschutzbeauftragte(r)"
    assert kontakte[1]["art"] == "bma_alarmperson"
    assert kontakte[2]["art"] == "bma_alarmperson"


def test_parse_kontakte_stabile_extern_id_aus_onclick():
    kontakte = bma_parser.parse_kontakte(DETAIL_HTML)
    assert kontakte[0]["extern_id"] == "172:1"
    assert kontakte[1]["extern_id"] == "172:2"
    assert kontakte[2]["extern_id"] == "313:2"


def test_parse_kontakte_adresse_wird_nicht_uebernommen():
    kontakte = bma_parser.parse_kontakte(DETAIL_HTML)
    for k in kontakte:
        assert "Musterweg" not in "".join(k.get("telefone") or [])
        assert k.get("adresse") is None or "adresse" not in k


def test_parse_kontakte_leeres_zuletzt_aktualisiert_verschiebt_nichts():
    """Regressionstest fuer die im Mitschnitt beobachtete Falle: 'Zuletzt
    aktualisiert:' wird auch leer gerendert - eine naive 'naechste Zeile ist der
    Wert'-Paarung wuerde dann den folgenden Button-Text als Wert einfangen."""
    kontakte = bma_parser.parse_kontakte(DETAIL_HTML)
    kontakt = kontakte[0]
    assert kontakt["name"] == "Max Mustermann"
    assert kontakt["email"] == "max.mustermann@example.at"
    assert "Kontaktdaten bearbeiten" not in (kontakt.get("email") or "")


def test_parse_kontakte_mehrere_telefone_mit_label():
    kontakt = bma_parser.parse_kontakte(DETAIL_HTML)[1]
    assert kontakt["telefone"] == [
        "Telefon beruflich: +43 5572 000000",
        "Mobil beruflich: +43 664 7654321",
        "Mobil privat: +43 664 7654322",
        "Pager: 4711",
    ]
    assert kontakt["email"] == "erika.musterfrau@example.at"  # Beruf vor Privat


def test_parse_kontakte_dieselbe_person_in_zwei_rollen_ergibt_zwei_kontakte():
    """Person 172 tritt (wie im echten Mitschnitt) als Brandschutzbeauftragter
    UND als BMA Alarmperson auf - beides sind eigenstaendige objekt_kontakt-
    Zeilen mit unterschiedlicher extern_id (person_id:kontakttyp_id)."""
    kontakte = bma_parser.parse_kontakte(DETAIL_HTML)
    person_172 = [k for k in kontakte if k["person_id"] == "172"]
    assert len(person_172) == 2
    assert {k["kontakttyp_id"] for k in person_172} == {"1", "2"}


def test_parse_kontakte_ohne_editcontactdata_wird_uebersprungen():
    html = '<div class="card"><b class="text-success">Betreiber</b>Name:<b>Ohne ID</b></div>'
    assert bma_parser.parse_kontakte(html) == []


def test_parse_kontakte_leere_eingabe():
    assert bma_parser.parse_kontakte(None) == []
    assert bma_parser.parse_kontakte("") == []
    assert bma_parser.parse_kontakte("<html></html>") == []


# ── PR 2: bma_client.py (kein Netzwerkzugriff - client._client.post/.get wird
#    gemockt, Muster: tests/test_dibos_client.py) ────────────────────────────

import asyncio  # noqa: E402
import json  # noqa: E402

import httpx  # noqa: E402

from app.services.bma_import.bma_client import (  # noqa: E402
    BmaClient,
    BmaClientError,
    BmaSessionAbgelaufenError,
)


def _json_response(status_code: int, payload=None, headers=None) -> httpx.Response:
    content = json.dumps(payload).encode("utf-8") if payload is not None else b""
    return httpx.Response(status_code, content=content, headers=headers or {})


def _html_response(status_code: int, text: str) -> httpx.Response:
    return httpx.Response(status_code, content=text.encode("utf-8"))


def test_hole_anlagen_paginiert_bis_total_erreicht(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")
    seiten = [
        _json_response(200, {"Data": [{"Id": 1}, {"Id": 2}], "Total": 3}),
        _json_response(200, {"Data": [{"Id": 3}], "Total": 3}),
    ]
    aufgerufene_skips = []

    async def fake_post(url, content=None, headers=None):
        aufgerufene_skips.append(content)
        return seiten.pop(0)

    monkeypatch.setattr(client._client, "post", fake_post)
    anlagen = asyncio.run(client.hole_anlagen(seiten_groesse=2))
    assert [a["Id"] for a in anlagen] == [1, 2, 3]
    assert len(aufgerufene_skips) == 2
    assert "skip=0" in aufgerufene_skips[0]
    assert "skip=2" in aufgerufene_skips[1]


def test_hole_anlagen_leere_aber_gueltige_antwort_wirft_nicht(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_post(url, content=None, headers=None):
        return _json_response(200, {"Data": [], "Total": 0})

    monkeypatch.setattr(client._client, "post", fake_post)
    assert asyncio.run(client.hole_anlagen()) == []


def test_hole_anlagen_redirect_ist_session_abgelaufen(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_post(url, content=None, headers=None):
        return _json_response(302, headers={"location": "/dibos-web/gui/"})

    monkeypatch.setattr(client._client, "post", fake_post)
    with pytest.raises(BmaSessionAbgelaufenError):
        asyncio.run(client.hole_anlagen())


def test_hole_anlagen_html_statt_json_ist_session_abgelaufen(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_post(url, content=None, headers=None):
        return _html_response(200, "<html>Anmelden</html>")

    monkeypatch.setattr(client._client, "post", fake_post)
    with pytest.raises(BmaSessionAbgelaufenError):
        asyncio.run(client.hole_anlagen())


def test_hole_anlagen_http_fehler_ist_client_error(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_post(url, content=None, headers=None):
        return _json_response(500)

    monkeypatch.setattr(client._client, "post", fake_post)
    with pytest.raises(BmaClientError):
        asyncio.run(client.hole_anlagen())


def test_hole_anlagen_unerwartetes_json_format_zeigt_body_ausschnitt(monkeypatch):
    """Gueltiges JSON, aber ohne Data/Total (z.B. ein Fehlerobjekt der Plattform,
    etwa bei fehlendem Anti-Forgery-Token) - die Fehlermeldung muss einen Body-
    Ausschnitt enthalten, da es (anders als bei DIBOS, siehe dibos_capture.py)
    keine Rohdaten-Aufzeichnung fuer BMA gibt und sich der Fehler sonst nicht
    diagnostizieren laesst (Vorfall 2026-07-26: "unerwartetes Antwortformat"
    ohne jeden weiteren Hinweis)."""
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_post(url, content=None, headers=None):
        return _json_response(200, {"Message": "Ungueltige Anfrage"})

    monkeypatch.setattr(client._client, "post", fake_post)
    with pytest.raises(BmaClientError, match="Ungueltige Anfrage"):
        asyncio.run(client.hole_anlagen())


def test_hole_detail_html_ok(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_get(url, **kw):
        assert url.endswith("/AlarmSystem/Details/174")
        return _html_response(200, "<div>BMANR: 1020</div>")

    monkeypatch.setattr(client._client, "get", fake_get)
    html = asyncio.run(client.hole_detail_html("174"))
    assert "BMANR" in html


def test_hole_detail_html_ohne_bmanr_ist_session_abgelaufen(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_get(url, **kw):
        return _html_response(200, "<html>Anmelden</html>")

    monkeypatch.setattr(client._client, "get", fake_get)
    with pytest.raises(BmaSessionAbgelaufenError):
        asyncio.run(client.hole_detail_html("174"))


def test_test_connection_erfolgreich(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_post(url, content=None, headers=None):
        return _json_response(200, {"Data": [{"Id": 1}], "Total": 30})

    monkeypatch.setattr(client._client, "post", fake_post)
    ok, meldung = asyncio.run(client.test_connection())
    assert ok is True
    assert "30" in meldung


def test_test_connection_meldet_abgelaufene_session_ohne_wurf(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_post(url, content=None, headers=None):
        return _json_response(302, headers={"location": "/dibos-web/gui/"})

    monkeypatch.setattr(client._client, "post", fake_post)
    ok, meldung = asyncio.run(client.test_connection())
    assert ok is False
    assert "Session" in meldung


def test_keepalive_erfolgreich(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_get(url, **kw):
        return _html_response(200, "ok")

    monkeypatch.setattr(client._client, "get", fake_get)
    assert asyncio.run(client.keepalive()) is True


def test_keepalive_bei_abgelaufener_session_liefert_false(monkeypatch):
    client = BmaClient("https://dibos.example.at/LWZ_BMA_Webplattform", "sid=abc")

    async def fake_get(url, **kw):
        return _html_response(302, "")

    monkeypatch.setattr(client._client, "get", fake_get)
    assert asyncio.run(client.keepalive()) is False


# ── PR 3: bma_sync.py (Fachlogik - Zuordnung, Upsert, Review-Queue, Laufprotokoll) ──

import copy  # noqa: E402
import uuid  # noqa: E402

from app.core.crypto import encrypt_secret  # noqa: E402
from app.models.objekt import OBJEKT_STATUS_FREIGEGEBEN  # noqa: E402
from app.services.bma_import import bma_sync  # noqa: E402


def _anlage_roh(**overrides) -> dict:
    basis = {
        "Id": 174, "Guid": "9f9b2df6-f04c-4694-96dc-1c67ea59de28",
        "BMANR": "1020", "Bezeichnung": "Testareal 1 Musterort",
        "Address": {
            "Strasse": "Musterstraße", "Hausnummer": "16", "PLZ": "6922",
            "Ort": "Musterort", "Latitude": "47,455640961990206",
            "Longitude": "9,735831280936772",
        },
        "PaymentAddress": None,
        "IsRFL": True, "IsActive": True,
        "Anlagedatum": "2020-02-19T10:01:01.11",
        "Aufschaltdatum": "1998-01-22T00:00:00",
        "ChangeDate": "2026-07-23T00:02:03.005",
        "ESZ": 12950116, "Abschnitt": "FW - Abschnitt - 29", "Bezirk": "FW - Bezirk - Bregenz",
    }
    ergebnis = copy.deepcopy(basis)
    for schluessel, wert in overrides.items():
        if schluessel == "Address":
            ergebnis["Address"].update(wert)
        else:
            ergebnis[schluessel] = wert
    return ergebnis


@pytest.fixture
def sync_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    # sync_org_bma erwartet set_tenant_context(db, None) (Muster: lis_sync.py::sync_organization) -
    # der Aufrufer (bma_loop.py/Admin-UI) filtert Tenant-Tabellen selbst explizit auf org.id.
    set_tenant_context(db, None)
    org_a = FireDept(slug=f"bma-sync-a-{uuid.uuid4().hex[:8]}", name="Org A", color="#ff0000", bos="Feuerwehr")
    org_b = FireDept(slug=f"bma-sync-b-{uuid.uuid4().hex[:8]}", name="Org B", color="#0000ff", bos="Feuerwehr")
    db.add_all([org_a, org_b])
    db.flush()
    yield db, org_a, org_b
    db.close()
    Base.metadata.drop_all(bind=engine)


def _config(org, **overrides) -> OrgBmaImportConfig:
    cfg = OrgBmaImportConfig(
        org_id=org.id, enabled=True,
        base_url="https://dibos.example.at/LWZ_BMA_Webplattform",
        session_cookie_enc=encrypt_secret("sid=abc"),
        auto_anlegen=True,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _patch_client(monkeypatch, anlagen_roh: list[dict], details: dict[str, str] | None = None):
    """Patcht BmaClient klassenweit (die Instanz wird INNERHALB von sync_org_bma
    erzeugt, kann also nicht direkt injiziert werden)."""
    details = details or {}
    aufrufe = {"hole_anlagen": 0, "hole_detail_html": []}

    async def fake_hole_anlagen(self, seiten_groesse=200):
        aufrufe["hole_anlagen"] += 1
        return anlagen_roh

    async def fake_hole_detail_html(self, extern_id):
        aufrufe["hole_detail_html"].append(extern_id)
        return details.get(extern_id, "<html></html>")

    monkeypatch.setattr(BmaClient, "hole_anlagen", fake_hole_anlagen)
    monkeypatch.setattr(BmaClient, "hole_detail_html", fake_hole_detail_html)
    return aufrufe


def test_sync_neue_anlage_legt_entwurf_objekt_und_kontakte_an(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    _patch_client(monkeypatch, [_anlage_roh()], {"174": DETAIL_HTML})
    cfg = _config(org_a)

    lauf = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg, ausloeser="manuell"))

    assert lauf.status == "ok"
    assert lauf.gefunden == 1
    assert lauf.neu_angelegt == 1
    assert lauf.fehler == 0

    objekt = db.query(Objekt).filter(Objekt.org_id == org_a.id).one()
    assert objekt.status == OBJEKT_STATUS_ENTWURF
    assert objekt.name == "Testareal 1 Musterort"
    assert objekt.strasse == "Musterstraße"
    assert objekt.lat == pytest.approx(47.455640961990206)

    assert objekt.bma is not None
    assert objekt.bma.bma_nummer == "1020"
    assert objekt.bma.uebertragungseinrichtung == "RFL aufgeschaltet"

    kontakte = db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id).all()
    assert len(kontakte) == 3
    assert all(k.extern_quelle == "dibos_bma" for k in kontakte)
    assert {k.name for k in kontakte} == {"Max Mustermann", "Erika Musterfrau", "Dieter Beispiel"}

    satz = db.query(BmaImportSatz).filter(BmaImportSatz.org_id == org_a.id).one()
    assert satz.objekt_id == objekt.id
    assert satz.zuordnung == BMA_ZUORDNUNG_AUTO
    assert satz.status == BMA_SATZ_AKTIV


def test_sync_zweiter_lauf_ohne_aenderung_schreibt_nichts(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    aufrufe = _patch_client(monkeypatch, [_anlage_roh()], {"174": DETAIL_HTML})
    cfg = _config(org_a)

    asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))
    objekt = db.query(Objekt).filter(Objekt.org_id == org_a.id).one()
    aktualisiert_am_vorher = objekt.aktualisiert_am
    kontakt_ids_vorher = {k.id for k in db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id)}

    lauf2 = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    assert lauf2.neu_angelegt == 0
    assert lauf2.aktualisiert == 0
    db.refresh(objekt)
    assert objekt.aktualisiert_am == aktualisiert_am_vorher
    kontakt_ids_nachher = {k.id for k in db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id)}
    assert kontakt_ids_nachher == kontakt_ids_vorher
    # ChangeDate unveraendert -> Detailseite (Kontakte) wird beim zweiten Lauf NICHT erneut geholt
    assert aufrufe["hole_detail_html"] == ["174"]


def test_sync_feldaenderung_an_entwurf_wird_uebernommen(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    _patch_client(monkeypatch, [_anlage_roh()], {"174": DETAIL_HTML})
    cfg = _config(org_a)
    asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    geaenderte_anlage = _anlage_roh(
        Bezeichnung="Testareal 1 NEU Musterort",
        Address={"Strasse": "Neue Musterstraße"},
        ChangeDate="2026-07-24T00:00:00",
    )
    _patch_client(monkeypatch, [geaenderte_anlage], {"174": DETAIL_HTML})
    lauf2 = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    assert lauf2.aktualisiert == 1
    objekt = db.query(Objekt).filter(Objekt.org_id == org_a.id).one()
    assert objekt.name == "Testareal 1 NEU Musterort"
    assert objekt.strasse == "Neue Musterstraße"


def test_sync_feldaenderung_an_freigegebenem_objekt_bleibt_in_queue(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    objekt = Objekt(
        org_id=org_a.id, nummer=1, name="Bestandsobjekt", status=OBJEKT_STATUS_FREIGEGEBEN,
        strasse="Musterstraße", hausnummer="16", plz="6922", ort="Musterort",
    )
    db.add(objekt)
    db.flush()
    db.add(ObjektBMA(org_id=org_a.id, objekt_id=objekt.id, bma_nummer="1020"))
    db.commit()

    _patch_client(monkeypatch, [_anlage_roh(Bezeichnung="Testareal NEU aus DIBOS")], {"174": DETAIL_HTML})
    cfg = _config(org_a)
    lauf = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    assert lauf.vorschlaege == 1
    assert lauf.aktualisiert == 0
    db.refresh(objekt)
    assert objekt.name == "Bestandsobjekt"  # unveraendert - nur Vorschlag, kein Auto-Apply
    assert objekt.status == OBJEKT_STATUS_FREIGEGEBEN
    kontakte = db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id).count()
    assert kontakte == 0  # Kontakte werden bei Nicht-Entwurf-Objekten nicht geschrieben

    satz = db.query(BmaImportSatz).filter(BmaImportSatz.org_id == org_a.id).one()
    assert satz.objekt_id == objekt.id
    assert satz.bestaetigt_hash is None
    erster_hash = satz.quell_hash

    # "Uebernehmen" wird von der Admin-UI (PR 5) simuliert - sie bestaetigt den Stand
    satz.bestaetigt_hash = erster_hash
    db.commit()

    # Zweiter Lauf OHNE weitere Quelledaenderung -> keine neuen Vorschlaege mehr
    _patch_client(monkeypatch, [_anlage_roh(Bezeichnung="Testareal NEU aus DIBOS")], {"174": DETAIL_HTML})
    lauf2 = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))
    assert lauf2.vorschlaege == 0


def test_sync_haendischer_kontakt_bleibt_unangetastet(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    objekt = Objekt(
        org_id=org_a.id, nummer=1, name="Testareal 1 Musterort", status=OBJEKT_STATUS_ENTWURF,
        strasse="Musterstraße", hausnummer="16", plz="6922", ort="Musterort",
    )
    db.add(objekt)
    db.flush()
    db.add(ObjektBMA(org_id=org_a.id, objekt_id=objekt.id, bma_nummer="1020"))
    manueller_kontakt = ObjektKontakt(
        org_id=org_a.id, objekt_id=objekt.id, art="betreiber", name="Manueller Kontakt", sort=1,
    )
    db.add(manueller_kontakt)
    db.commit()
    manueller_id = manueller_kontakt.id

    _patch_client(monkeypatch, [_anlage_roh()], {"174": DETAIL_HTML})
    cfg = _config(org_a)
    asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    kontakte = db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id).all()
    assert len(kontakte) == 4  # 1 manuell + 3 importiert
    manueller = next(k for k in kontakte if k.id == manueller_id)
    assert manueller.extern_quelle is None
    assert manueller.name == "Manueller Kontakt"


def test_sync_importierter_kontakt_wird_aktualisiert_nicht_dupliziert(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    _patch_client(monkeypatch, [_anlage_roh()], {"174": DETAIL_HTML})
    cfg = _config(org_a)
    asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))
    objekt = db.query(Objekt).filter(Objekt.org_id == org_a.id).one()
    vorher = {k.extern_id: k.id for k in db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id)}

    geaendertes_detail = DETAIL_HTML.replace("max.mustermann@example.at", "max.neu@example.at")
    _patch_client(monkeypatch, [_anlage_roh(ChangeDate="2026-08-01T00:00:00")], {"174": geaendertes_detail})
    asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    kontakte = db.query(ObjektKontakt).filter(ObjektKontakt.objekt_id == objekt.id).all()
    assert len(kontakte) == 3  # keine Dubletten
    nachher = {k.extern_id: k.id for k in kontakte}
    assert nachher == vorher  # dieselben Zeilen wiederverwendet
    max_kontakt = next(k for k in kontakte if k.name == "Max Mustermann")
    assert max_kontakt.email == "max.neu@example.at"


def test_sync_plausibilitaetsuntergrenze_verhindert_schreiben(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    for i in range(5):
        db.add(BmaImportSatz(
            org_id=org_a.id, extern_id=str(i), status=BMA_SATZ_AKTIV,
            zuordnung=BMA_ZUORDNUNG_OFFEN, erst_gesehen_am=jetzt, zuletzt_gesehen_am=jetzt,
        ))
    db.commit()

    _patch_client(monkeypatch, [_anlage_roh()], {"174": DETAIL_HTML})
    cfg = _config(org_a)
    lauf = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    assert lauf.status == BMA_LAUF_FEHLER
    assert "unplausibel" in lauf.meldung
    assert db.query(Objekt).filter(Objekt.org_id == org_a.id).count() == 0
    # bestehende Saetze bleiben aktiv (kein "verschwunden"-Markieren bei einem verworfenen Lauf)
    assert db.query(BmaImportSatz).filter(
        BmaImportSatz.org_id == org_a.id, BmaImportSatz.status == BMA_SATZ_AKTIV
    ).count() == 5


def test_sync_abgelaufene_session_aendert_nichts(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    cfg = _config(org_a)

    async def fake_hole_anlagen(self, seiten_groesse=200):
        raise BmaSessionAbgelaufenError("Session abgelaufen (Test)")

    monkeypatch.setattr(BmaClient, "hole_anlagen", fake_hole_anlagen)
    lauf = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    assert lauf.status == BMA_LAUF_SESSION_ABGELAUFEN
    assert db.query(Objekt).filter(Objekt.org_id == org_a.id).count() == 0
    assert cfg.letzter_lauf_status == BMA_LAUF_SESSION_ABGELAUFEN


def test_sync_unvollstaendige_konfiguration_wird_nicht_versucht(monkeypatch, sync_db):
    db, org_a, _ = sync_db
    cfg = OrgBmaImportConfig(org_id=org_a.id, enabled=True)  # kein Cookie hinterlegt

    aufrufe = {"n": 0}

    async def fake_hole_anlagen(self, seiten_groesse=200):
        aufrufe["n"] += 1
        return []

    monkeypatch.setattr(BmaClient, "hole_anlagen", fake_hole_anlagen)
    lauf = asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg))

    assert lauf.status == BMA_LAUF_FEHLER
    assert aufrufe["n"] == 0  # kein Netzzugriff versucht


def test_sync_tenant_isolation(monkeypatch, sync_db):
    db, org_a, org_b = sync_db
    _patch_client(monkeypatch, [_anlage_roh()], {"174": DETAIL_HTML})
    cfg_a = _config(org_a)
    asyncio.run(bma_sync.sync_org_bma(db, org_a, cfg_a))

    assert db.query(Objekt).filter(Objekt.org_id == org_a.id).count() == 1
    assert db.query(Objekt).filter(Objekt.org_id == org_b.id).count() == 0
    assert db.query(BmaImportSatz).filter(BmaImportSatz.org_id == org_b.id).count() == 0
    assert db.query(BmaImportLauf).filter(BmaImportLauf.org_id == org_b.id).count() == 0


# ── PR 4: bma_loop.py (reine Zeitlogik, kein Netz/DB) ────────────────────────

from app.services.bma_import.bma_loop import _ist_heute_faellig, _VIENNA_TZ  # noqa: E402


def _wien_jetzt_mit_uhrzeit(stunde: int, minute: int) -> datetime:
    from datetime import datetime as _dt
    heute = _dt.now(_VIENNA_TZ)
    return heute.replace(hour=stunde, minute=minute, second=0, microsecond=0)


def test_ist_heute_faellig_vor_geplanter_zeit_ist_nicht_faellig(monkeypatch):
    from app.services.bma_import import bma_loop

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _wien_jetzt_mit_uhrzeit(3, 0)

    monkeypatch.setattr(bma_loop, "datetime", _FakeDatetime)
    assert _ist_heute_faellig(None, 5, 0) is False


def test_ist_heute_faellig_nach_geplanter_zeit_ohne_vorlauf_ist_faellig(monkeypatch):
    from app.services.bma_import import bma_loop

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _wien_jetzt_mit_uhrzeit(5, 1)

    monkeypatch.setattr(bma_loop, "datetime", _FakeDatetime)
    assert _ist_heute_faellig(None, 5, 0) is True


def test_ist_heute_faellig_bereits_heute_gelaufen_ist_nicht_erneut_faellig(monkeypatch):
    from app.services.bma_import import bma_loop

    jetzt_wien = _wien_jetzt_mit_uhrzeit(5, 30)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return jetzt_wien

    monkeypatch.setattr(bma_loop, "datetime", _FakeDatetime)
    letzter_lauf_utc = jetzt_wien.astimezone(UTC).replace(tzinfo=None)  # DB-Konvention: naive UTC
    assert _ist_heute_faellig(letzter_lauf_utc, 5, 0) is False


def test_ist_heute_faellig_letzter_lauf_war_gestern_ist_wieder_faellig(monkeypatch):
    from datetime import timedelta

    from app.services.bma_import import bma_loop

    jetzt_wien = _wien_jetzt_mit_uhrzeit(5, 30)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return jetzt_wien

    monkeypatch.setattr(bma_loop, "datetime", _FakeDatetime)
    gestern_utc = (jetzt_wien - timedelta(days=1)).astimezone(UTC).replace(tzinfo=None)
    assert _ist_heute_faellig(gestern_utc, 5, 0) is True
