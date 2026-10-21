"""Live-Kontaktsuche mit Verzeichnisrechten und einsatzgebundener Freigabe."""

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.incident import Incident
from app.models.kontakt import (
    EinsatzKontakt, Kontakt, KontaktAdresse, KontaktEmail, KontaktKategorie,
    KontaktKategorieZuordnung, KontaktTelefon,
)
from app.models.master import FireDept, OrgSettings, SystemSettings
from app.models.objekt import Objekt, ObjektEinsatz, ObjektKontakt
from app.services.kontakt_access_service import einsatz_kontakte, kontakt_zugriff_fuer_request
from app.services.suche import live
from app.services.suche.abfrage import suche
from app.services.suche.vertrag import SuchAnfrage
from tests.conftest import TestingSession
from tests.test_admin_rollenrechte import _login, _user


@pytest.fixture
def daten():
    with TestingSession() as db:
        set_tenant_context(db, None)
        org, fremd = [FireDept(slug=uuid4().hex, name=name) for name in ("Kontaktsuche", "Fremd")]
        db.add_all([org, fremd])
        db.flush()
        db.add(OrgSettings(org_id=org.id, suche_aktiv=True, kontakte_module_enabled=True))
        user = SimpleNamespace(org_id=org.id, roles=[SimpleNamespace(code="recorder")])
        einsatz, anderer = [Incident(primary_org_id=org.id, alarm_type_code="T1") for _ in range(2)]
        kontakt = Kontakt(
            org_id=org.id, anzeigename="Anna Berger", vorname="Anna", nachname="Berger",
            funktion="Wasserwart", organisation="Gemeinde", erreichbarkeit="Tagsueber",
            notizen="Notizgeheimnis", email="emailgeheimnis@example.at",
            datenquelle="Quellengeheimnis", quellendokument="Dokumentgeheimnis", externe_quelle_id="Externgeheimnis",
            telefone=[
                KontaktTelefon(org_id=org.id, nummer="06641234567", label="Dienst", verwendung="dienst"),
                KontaktTelefon(org_id=org.id, nummer="06649999999", label="Privat", verwendung="privat", bevorzugt=True),
                KontaktTelefon(org_id=org.id, nummer="06648888888", aktiv=False),
            ],
            email_adressen=[KontaktEmail(org_id=org.id, email="tabellengeheimnis@example.at")],
            adressen=[KontaktAdresse(org_id=org.id, strasse="Adressgeheimnis")],
        )
        kategorie = KontaktKategorie(org_id=org.id, name="Wasserversorgung")
        db.add_all([einsatz, anderer, kontakt, kategorie])
        db.flush()
        db.add(KontaktKategorieZuordnung(org_id=org.id, kontakt_id=kontakt.id, kategorie_id=kategorie.id))
        db.flush()
        yield SimpleNamespace(db=db, org=org, fremd=fremd, user=user, kontakt=kontakt,
                              einsatz=einsatz, anderer=anderer)
        db.rollback()


def _request(daten, rolle="recorder", **zustand):
    request = Request({"type": "http"})
    request.state.user = SimpleNamespace(org_id=daten.org.id, roles=[SimpleNamespace(code=rolle)])
    request.state.kontakte_enabled = True
    for key, wert in zustand.items():
        setattr(request.state, key, wert)
    return request


def _suche(daten, q="Anna", zugriff="voll", **kwargs):
    daten.db.flush()
    return suche(daten.db, daten.user, SuchAnfrage(q=q, typen=["kontakt"], **kwargs), kontakt_zugriff=zugriff)


@pytest.mark.parametrize("q", ["Anna", "Wasserwart", "Wasserversorgung", "+43 664 1234567"])
def test_voll_name_funktion_kategorie_telefon(daten, q):
    anfrage = SuchAnfrage(q=q)
    assert kontakt_zugriff_fuer_request(daten.db, _request(daten), anfrage) == "voll"
    ergebnis = _suche(daten, q)
    assert not ergebnis.teilweise
    treffer, = ergebnis.treffer
    assert treffer.quell_id == str(daten.kontakt.id)
    assert treffer.url == f"/kontakte/{daten.kontakt.id}"
    assert treffer.untertitel == "Wasserwart · Gemeinde"
    assert treffer.status == "Aktiv" and treffer.ist_aktuell
    assert treffer.telefone == [{"nummer": "06649999999", "label": "Privat"},
                               {"nummer": "06641234567", "label": "Dienst"}]
    assert treffer.grund == (["exakt", "live"] if q.startswith("+") else ["live"])
    assert "0664" not in treffer.auszug


def test_archiviert_nie(daten):
    daten.kontakt.archiviert = True
    for erledigt in (False, True):
        assert not _suche(daten, inkl_erledigt=erledigt).treffer
        assert not _suche(daten, "+43 664 1234567", inkl_erledigt=erledigt).treffer


def test_inaktiv_nur_mit_schalter(daten):
    daten.kontakt.aktiv = False
    assert not _suche(daten).treffer
    treffer, = _suche(daten, inkl_erledigt=True).treffer
    assert treffer.status == "Inaktiv" and not treffer.ist_aktuell


@pytest.mark.parametrize("q", ["Notizgeheimnis", "emailgeheimnis", "tabellengeheimnis", "Adressgeheimnis",
                               "Quellengeheimnis", "Dokumentgeheimnis", "Externgeheimnis"])
def test_interne_felder_nicht_durchsuchen_oder_ausgeben(daten, q):
    assert not _suche(daten, q).treffer
    assert q not in _suche(daten).model_dump_json()


def test_einsatzleser_global_keine_kontakte(daten):
    request = _request(daten, "breathing_supervisor")
    anfrage = SuchAnfrage(q="Anna")
    zugriff = kontakt_zugriff_fuer_request(daten.db, request, anfrage)
    assert zugriff == "keiner"
    assert not _suche(daten, zugriff=zugriff).treffer
    assert not live.suche_kontakte(daten.db, daten.user, anfrage, ["anna"], "einsatz")


@pytest.fixture
def zuordnungen(daten):
    db, org, einsatz = daten.db, daten.org, daten.einsatz
    db.add(EinsatzKontakt(org_id=org.id, incident_id=einsatz.id, kontakt_id=daten.kontakt.id, hinweis="Zustaendig"))
    kontakte = [daten.kontakt]
    for nr, (status, sichtbar) in enumerate([
        ("bestaetigt", True), ("vorschlag", True), ("bestaetigt", False),
    ], 1):
        kontakt = Kontakt(org_id=org.id, anzeigename=f"Anna Objekt {nr}", telefone=[
            KontaktTelefon(org_id=org.id, nummer="06641234567", verwendung="dienst"),
            KontaktTelefon(org_id=org.id, nummer="06649999999", verwendung="privat"),
        ])
        objekt = Objekt(org_id=org.id, nummer=nr, name=f"Objekt {nr}")
        db.add_all([kontakt, objekt])
        db.flush()
        db.add_all([
            ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=kontakt.id, einsatz_sichtbar=sichtbar),
            ObjektEinsatz(org_id=org.id, incident_id=einsatz.id, objekt_id=objekt.id, quelle="manuell", status=status),
        ])
        if nr == 1:
            # Dieselbe Person ueber zwei Freigabewege darf nur einmal erscheinen.
            db.add(ObjektKontakt(org_id=org.id, objekt_id=objekt.id, kontakt_id=daten.kontakt.id))
        kontakte.append(kontakt)
    anderer = Kontakt(org_id=org.id, anzeigename="Anna Anderer Einsatz")
    db.add(anderer)
    db.flush()
    db.add(EinsatzKontakt(org_id=org.id, incident_id=daten.anderer.id, kontakt_id=anderer.id))
    db.flush()
    return kontakte


def test_einsatz_freigaben_und_dienstnummern(daten, zuordnungen):
    request = _request(daten, "breathing_supervisor")
    daten.user = request.state.user
    anfrage = SuchAnfrage(q="Anna", kontext="einsatz", kontext_id=daten.einsatz.id)
    zugriff = kontakt_zugriff_fuer_request(daten.db, request, anfrage)
    assert zugriff == "einsatz"
    ergebnis = _suche(daten, zugriff=zugriff, kontext="einsatz", kontext_id=daten.einsatz.id)
    assert not ergebnis.teilweise
    assert {t.quell_id for t in ergebnis.treffer} == {str(k.id) for k in zuordnungen[:2]}
    assert len(ergebnis.treffer) == 2
    for treffer in ergebnis.treffer:
        assert treffer.url == f"/einsatz/{daten.einsatz.id}#einsatz-kontakte"
        assert [t["nummer"] for t in treffer.telefone] == ["06641234567"]
    assert len(einsatz_kontakte(daten.db, daten.einsatz)) == 2


def test_einsatz_keine_telefonnummernsuche(daten, zuordnungen):
    assert not _suche(daten, "+43 664 1234567", zugriff="einsatz",
                      kontext="einsatz", kontext_id=daten.einsatz.id).treffer


@pytest.mark.parametrize("feld", ["aktiv", "archiviert"])
def test_einsatz_nur_aktive_nicht_archivierte(daten, zuordnungen, feld):
    setattr(daten.kontakt, feld, feld == "archiviert")
    daten.db.flush()
    assert daten.kontakt.id not in {k.id for k, _ in einsatz_kontakte(daten.db, daten.einsatz)}


@pytest.mark.parametrize("zustand", [
    {"qr_incident_id": "anderer"}, {"qr_lage_id": 123}, {"is_device": True}, {"kontakte_enabled": False},
])
def test_request_sperren(daten, zustand):
    if zustand.get("qr_incident_id") == "anderer":
        zustand = {"qr_incident_id": daten.anderer.id}
    anfrage = SuchAnfrage(q="Anna", kontext="einsatz", kontext_id=daten.einsatz.id)
    assert kontakt_zugriff_fuer_request(daten.db, _request(daten, **zustand), anfrage) == "keiner"


def test_gsl_gesperrt(daten):
    anfrage = SuchAnfrage(q="Anna", kontext="gsl", kontext_id=123)
    assert kontakt_zugriff_fuer_request(daten.db, _request(daten), anfrage) == "keiner"


def test_qr_gleicher_einsatz(daten):
    anfrage = SuchAnfrage(q="Anna", kontext="einsatz", kontext_id=daten.einsatz.id)
    request = _request(daten, qr_incident_id=daten.einsatz.id, is_qr=True)
    assert kontakt_zugriff_fuer_request(daten.db, request, anfrage) == "einsatz"


def test_tenant(daten):
    fremd = Kontakt(org_id=daten.fremd.id, anzeigename="Anna Fremd", telefone=[
        KontaktTelefon(org_id=daten.fremd.id, nummer="06641234567"),
    ])
    daten.db.add(fremd)
    daten.db.flush()
    for q in ("Anna", "+43 664 1234567"):
        assert {t.quell_id for t in _suche(daten, q).treffer} == {str(daten.kontakt.id)}


def test_ohne_zugriff_keine_kontakte(daten):
    assert not suche(daten.db, daten.user, SuchAnfrage(q="Anna", typen=["kontakt"])).treffer


def test_einsatzrelevant_bei_gleichem_titelrang(daten):
    daten.kontakt.aktualisiert_am = datetime(2026, 1, 2)
    relevant = Kontakt(org_id=daten.org.id, anzeigename="Anna Relevant", einsatzrelevant=True,
                       aktualisiert_am=datetime(2026, 1, 1))
    daten.db.add(relevant)
    assert [t.quell_id for t in _suche(daten).treffer] == [str(relevant.id), str(daten.kontakt.id)]


def test_anbieterfehler_isoliert(daten, monkeypatch):
    def fehler(*args, **kwargs):
        raise RuntimeError("Kontaktsuche ausgefallen")
    monkeypatch.setattr(live, "suche_kontakte", fehler)
    ergebnis = _suche(daten)
    assert ergebnis.teilweise and "Kontakte nicht verfügbar" in ergebnis.hinweise


@pytest.fixture
def ui_daten(client, monkeypatch, daten):
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    db = daten.db
    flag = db.get(SystemSettings, "kontakte_module_enabled")
    vorher = flag.value if flag else None
    if flag is None:
        flag = SystemSettings(key="kontakte_module_enabled")
        db.add(flag)
    flag.value = "true"
    users = {}
    for rolle in ("recorder", "breathing_supervisor"):
        name = uuid4().hex
        _user(db, name, daten.org.id, rolle)
        users[rolle] = name
    db.commit()
    yield users
    set_tenant_context(db, None)
    flag = db.get(SystemSettings, "kontakte_module_enabled")
    if vorher is None:
        db.delete(flag)
    else:
        flag.value = vorher
    db.commit()


def test_ui_recorder_telefonlink_und_api(client, daten, ui_daten):
    assert _login(client, ui_daten["recorder"]).status_code == 302
    antwort = client.post("/suche/treffer", data={"q": "Anna", "_csrf": client.cookies.get("ec_csrf")})
    assert antwort.status_code == 200
    assert "Anna Berger" in antwort.text and 'href="tel:06641234567"' in antwort.text
    assert "Dienst:" in antwort.text
    antwort = client.post("/api/suche", json={"q": "Anna", "typen": ["kontakt"]},
                          headers={"X-CSRF-Token": client.cookies.get("ec_csrf")})
    assert antwort.status_code == 200
    assert antwort.json()["treffer"][0]["telefone"]


def test_ui_einsatzleser_global_ohne_kontakte(client, daten, ui_daten):
    assert _login(client, ui_daten["breathing_supervisor"]).status_code == 302
    antwort = client.post("/suche/treffer", data={"q": "Anna", "_csrf": client.cookies.get("ec_csrf")})
    assert antwort.status_code == 200
    assert "Anna Berger" not in antwort.text and 'href="tel:' not in antwort.text
    assert 'value="kontakt"' not in client.get("/suche").text


def test_ui_filtergruppe_nur_mit_modul(client, daten, ui_daten):
    assert _login(client, ui_daten["recorder"]).status_code == 302
    assert 'value="kontakt"' in client.get("/suche").text
    set_tenant_context(daten.db, None)
    daten.db.query(OrgSettings).filter_by(org_id=daten.org.id).one().kontakte_module_enabled = False
    daten.db.commit()
    assert 'value="kontakt"' not in client.get("/suche").text
    antwort = client.post("/suche/treffer", data={"q": "Anna", "_csrf": client.cookies.get("ec_csrf")})
    assert "Anna Berger" not in antwort.text
