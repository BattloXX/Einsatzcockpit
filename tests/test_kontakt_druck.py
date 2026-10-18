"""Druckauswahl, Datenschutz, Rechte, Profile und echte PDF-Ausgabe."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.tenant import TenantContextMissing, set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.tools import kontakt as mcp
from app.models.kontakt import (
    Kontakt,
    KontaktDruckprofil,
    KontaktEmail,
    KontaktKategorie,
    KontaktOrganisation,
    KontaktOrganisationFunktion,
)
from app.models.master import FireDept, OrgSettings
from app.models.objekt import Objekt, ObjektKontakt
from app.services import kontakt_druck_service as druck
from app.services import kontakt_service
from tests.test_kontakte_phase2 import _login, _setup_user


@pytest.fixture
def bestand(client):
    db = SessionLocal()
    set_tenant_context(db, None)
    unique = uuid4().hex[:12]
    org = FireDept(name="Druck-Testorganisation", slug="druck-" + unique)
    db.add(org)
    db.commit()
    user = _setup_user("druck_" + unique, "kontakt_verwalter", org_id=org.id)
    set_tenant_context(db, user.org_id)
    druck.standardkategorien_anlegen(db, user.org_id)
    cats = {k.name: k for k in druck.kategorien(db, user.org_id)}

    def add(name, categories=None, **kwargs):
        phones = kwargs.pop("phones", [{"nummer": "+43 1234", "label": "Zentrale"}])
        data = {
            "typ": "person",
            "anzeigename": name,
            "organisation": "Feuerwehr Test",
            "druck_freigegeben": True,
            **kwargs,
        }
        return kontakt_service.create_kontakt(
            db, data, phones, categories or [druck.KATEGORIEN[0]], org_id=user.org_id, user_id=user.id, commit=False
        )

    yield db, user, cats, add
    db.rollback()
    db.close()


def _logo_einrichten(db, user, tmp_path, monkeypatch, suffix=".png"):
    from PIL import Image

    monkeypatch.setattr(druck, "_LOGO_DIR", tmp_path)
    path = tmp_path / f"logo_org{user.org_id}{suffix}"
    if suffix == ".svg":
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
            '<rect width="100" height="60" fill="red"/></svg>'
        )
    else:
        Image.new("RGB", (100, 60), "red").save(path)
    db.get(FireDept, user.org_id).logo_path = f"/static/img/uploads/{path.name}"
    db.commit()
    return path


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".svg", ".webp"])
@pytest.mark.parametrize("layout", ["kompakt", "detail"])
def test_organisationslogo_in_vorschau_und_pdf(bestand, client, tmp_path, monkeypatch, suffix, layout):
    from pypdf import PdfReader

    db, user, cats, add = bestand
    add("Kontakt mit Logo")
    _logo_einrichten(db, user, tmp_path, monkeypatch, suffix)
    _login(client, user.username)
    params = {"einstellungen": druck.DruckEinstellungen(layout=layout).model_dump_json()}
    preview = client.get("/kontakte/druckvorschau", params=params)
    assert preview.status_code == 200
    assert 'alt="Organisationslogo von Druck-Testorganisation"' in preview.text
    assert 'src="data:image/' in preview.text
    pdf = client.get("/kontakte/druck.pdf", params=params)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    pages = PdfReader(BytesIO(pdf.content)).pages
    assert "Kontakt mit Logo" in pages[0].extract_text()
    if suffix != ".svg":
        assert len(pages[0].images) == 1
    opts = druck.DruckEinstellungen(layout=layout, logo_anzeigen=False)
    params["einstellungen"] = opts.model_dump_json()
    assert 'src="data:image/' not in client.get("/kontakte/druckvorschau", params=params).text
    pdf = client.get("/kontakte/druck.pdf", params=params)
    assert len(PdfReader(BytesIO(pdf.content)).pages[0].images) == 0
    profile = druck.profil_speichern(db, user.org_id, user.id, "Ohne Logo", opts)
    assert druck.profil_laden(db, user.org_id, user.id, str(profile.id)).logo_anzeigen is False


def test_logo_orgsettings_fallback_und_alte_profile(bestand, tmp_path, monkeypatch):
    db, user, cats, add = bestand
    path = _logo_einrichten(db, user, tmp_path, monkeypatch)
    org = db.get(FireDept, user.org_id)
    settings = db.query(OrgSettings).filter_by(org_id=user.org_id).one()
    settings.logo_path, org.logo_path = org.logo_path, None
    db.flush()
    assert druck.organisationslogo_data_uri(db, org).startswith("data:image/png;base64,")
    path.unlink()
    assert druck.organisationslogo_data_uri(db, org) == ""
    legacy = KontaktDruckprofil(
        org_id=user.org_id,
        user_id=user.id,
        name="Bisheriges Profil",
        einstellungen_json=druck.DruckEinstellungen(layout="detail").model_dump_json(exclude={"logo_anzeigen"}),
    )
    db.add(legacy)
    db.flush()
    assert druck.profil_laden(db, user.org_id, user.id, str(legacy.id)).logo_anzeigen is True


@pytest.mark.parametrize(
    "candidate",
    [
        "https://example.org/logo.png",
        "file:///etc/passwd",
        "/static/img/uploads/../logo.png",
        "/static/img/uploads/logo_org999999.png",
    ],
)
def test_logo_keine_externen_oder_fremden_pfade(bestand, tmp_path, monkeypatch, candidate):
    db, user, cats, add = bestand
    _logo_einrichten(db, user, tmp_path, monkeypatch)
    org = db.get(FireDept, user.org_id)
    org.logo_path = candidate
    assert druck.organisationslogo_data_uri(db, org) == ""


@pytest.mark.parametrize("content", [b"keine Bilddatei", b"x" * (2 * 1024 * 1024 + 1)])
def test_defektes_logo_blockiert_pdf_nicht(bestand, client, tmp_path, monkeypatch, content):
    db, user, cats, add = bestand
    path = _logo_einrichten(db, user, tmp_path, monkeypatch)
    path.write_bytes(content)
    _login(client, user.username)
    assert 'src="data:image/' not in client.get("/kontakte/druckvorschau").text
    assert client.get("/kontakte/druck.pdf").content.startswith(b"%PDF")


def test_logo_symlink_auf_fremdes_logo_wird_abgewiesen(bestand, tmp_path, monkeypatch):
    db, user, cats, add = bestand
    path = _logo_einrichten(db, user, tmp_path, monkeypatch)
    other = tmp_path / "logo_org999999.png"
    path.rename(other)
    path.symlink_to(other)
    assert druck.organisationslogo_data_uri(db, db.get(FireDept, user.org_id)) == ""


def test_pdf_fetcher_erlaubt_nur_eingebettetes_organisationslogo(monkeypatch):
    import weasyprint

    from app.services.pdf_service import render_kontaktverzeichnis_pdf

    logo = "data:image/png;base64,dGVzdA=="

    class PdfProbe:
        def __init__(self, *, string, url_fetcher):
            result = url_fetcher(logo)
            mime = result["mime_type"] if isinstance(result, dict) else result.content_type
            assert mime == "image/png"
            for forbidden in ("https://example.org/logo.png", "file:///etc/passwd", "data:image/png;base64,YQ=="):
                with pytest.raises(ValueError):
                    url_fetcher(forbidden)

        def write_pdf(self):
            return b"%PDF-test"

    monkeypatch.setattr(weasyprint, "HTML", PdfProbe)
    assert render_kontaktverzeichnis_pdf("<p>Test</p>", logo_data_uri=logo) == b"%PDF-test"


def test_standard_dedup_objektzuordnung_und_freigabe(bestand):
    db, user, cats, add = bestand
    kontakt = add("Kommandofunk", druck.KATEGORIEN[:3], einsatzrelevant=True)
    add("Nicht freigegeben", druck_freigegeben=False)
    add("Inaktiv", aktiv=False)
    add("BMA", [druck.KATEGORIEN[0], "Objektkontakte"])
    linked = add("Hausverwaltung ohne Objektkategorie")
    objekt = Objekt(org_id=user.org_id, name="Testobjekt", nummer="D-1")
    db.add(objekt)
    db.flush()
    db.add(ObjektKontakt(org_id=user.org_id, objekt_id=objekt.id, kontakt_id=linked.id, art="hausverwaltung"))
    db.flush()
    result = druck.auswahl(db, user.org_id, druck.DruckEinstellungen())
    assert [k["id"] for k in result["kontakte"]] == [kontakt.id]
    assert result["kontakte"][0]["kategorie"] == druck.KATEGORIEN[0]
    # Mehrere gewählte Kategorien führen ebenfalls zu genau einer Ausgabe.
    opts = druck.DruckEinstellungen(kategorien=[cats[n].id for n in druck.KATEGORIEN[:3]])
    assert druck.auswahl(db, user.org_id, opts)["anzahl"] == 1


def test_privates_niemals_in_ausgabe_oder_suche(bestand):
    db, user, cats, add = bestand
    kontakt = add(
        "Dienstkontakt",
        phones=[
            {"nummer": "+43 111", "verwendung": "privat"},
            {"nummer": "+43 222", "verwendung": "dienst", "aktiv": False},
            {"nummer": "+43 333", "verwendung": "dienst"},
        ],
        notizen="Private Notiz",
    )
    db.add_all(
        [
            KontaktEmail(org_id=user.org_id, kontakt_id=kontakt.id, email="privat@example.org", typ="privat"),
            KontaktEmail(org_id=user.org_id, kontakt_id=kontakt.id, email="dienst@example.org", typ="dienst"),
        ]
    )
    db.flush()
    db.expire(kontakt, ["email_adressen"])
    result = druck.auswahl(db, user.org_id, druck.DruckEinstellungen(kategorien=[], nur_freigegeben=False))
    serialized = json.dumps(result)
    assert "+43 333" in serialized and "dienst@example.org" in serialized
    assert "+43 111" not in serialized and "+43 222" not in serialized
    assert "privat@example.org" not in serialized and "Private Notiz" not in serialized
    assert druck.auswahl(db, user.org_id, druck.DruckEinstellungen(q="privat@example.org"))["anzahl"] == 0
    assert druck.auswahl(db, user.org_id, druck.DruckEinstellungen(q="+43 111"))["anzahl"] == 0


@pytest.mark.parametrize(
    "settings,expected",
    [
        ({"nur_einsatzrelevant": True}, ["Alpha"]),
        ({"typ": "stelle"}, ["Alpha"]),
        ({"typ": "person"}, ["Beta"]),
        ({"organisation": "Strom"}, ["Alpha"]),
        ({"funktion": "Bereitschaft"}, ["Alpha"]),
        ({"prioritaet": "hoch"}, ["Alpha"]),
        ({"q": "24/7"}, ["Alpha"]),
        ({"q": "Beta"}, ["Beta"]),
        ({"ohne_telefon_ausschliessen": True}, ["Alpha"]),
    ],
)
def test_filter(bestand, settings, expected):
    db, user, cats, add = bestand
    add(
        "Alpha",
        typ="stelle",
        organisation="Stromversorgung",
        funktion="Bereitschaft",
        druck_prioritaet="hoch",
        einsatzrelevant=True,
        erreichbarkeit="24/7",
    )
    add("Beta", typ="person", organisation="Gemeinde", funktion="Bauamt", phones=[])
    result = druck.auswahl(db, user.org_id, druck.DruckEinstellungen(**settings))
    assert sorted(k["name"] for k in result["kontakte"]) == expected


def test_strukturierte_organisation_funktion_und_gueltigkeit(bestand):
    db, user, cats, add = bestand
    kontakt = add("Person", organisation="", funktion="")
    org = KontaktOrganisation(org_id=user.org_id, name="Wasserwerk")
    db.add(org)
    db.flush()
    db.add(
        KontaktOrganisationFunktion(
            org_id=user.org_id, kontakt_id=kontakt.id, organisation_id=org.id, funktion="Rufbereitschaft", aktiv=True
        )
    )
    db.flush()
    db.expire(kontakt, ["organisations_funktionen"])
    assert (
        druck.auswahl(db, user.org_id, druck.DruckEinstellungen(organisation="Wasserwerk", funktion="Rufbereitschaft"))[
            "anzahl"
        ]
        == 1
    )
    kontakt.gueltig_bis = datetime.now(UTC).date() - timedelta(days=1)
    db.flush()
    assert druck.auswahl(db, user.org_id, druck.DruckEinstellungen())["anzahl"] == 0
    assert druck.auswahl(db, user.org_id, druck.DruckEinstellungen(nur_aktiv=False))["hinweise"]


def test_kategorien_pflege_ohne_kategorie_und_hinweise(bestand):
    db, user, cats, add = bestand
    kontakt = add("Ohne Telefon", phones=[], organisation="")
    kontakt.kategorien.clear()
    db.flush()
    assert druck.auswahl(db, user.org_id, druck.DruckEinstellungen())["anzahl"] == 0
    result = druck.auswahl(db, user.org_id, druck.DruckEinstellungen(kategorien=[]))
    assert result["anzahl"] == 1
    assert set(result["hinweise"][0]["hinweise"]) == {
        "Keine druckbare Telefonnummer",
        "Ohne Kategorie",
        "Ohne Organisationszuordnung",
    }
    category = druck.kategorie_speichern(db, user.org_id, name="Neue Kategorie", sortierung=-10, druck_standard=True)
    assert druck.kategorien(db, user.org_id)[0].id == category.id
    druck.kategorie_speichern(db, user.org_id, kategorie_id=category.id, name="Umbenannt", aktiv=False)
    assert category.name == "Umbenannt" and not category.aktiv
    with pytest.raises(ValueError):
        druck.auswahl(db, user.org_id, druck.DruckEinstellungen(kategorien=[category.id]))


def test_standardprofile_bleiben_nach_umbenennung_stabil(bestand):
    db, user, cats, add = bestand
    cat = cats["Feuerwehr intern"]
    kontakt = add("Internes Kommando", [cat.name])
    druck.kategorie_speichern(db, user.org_id, kategorie_id=cat.id, name="Kommando & Fachfunktionen")
    opts = druck.profil_laden(db, user.org_id, user.id, "intern")
    assert opts.kategorien == [cat.id]
    assert druck.auswahl(db, user.org_id, opts)["kontakte"][0]["id"] == kontakt.id
    druck.standardkategorien_anlegen(db, user.org_id)
    assert len(druck.kategorien(db, user.org_id)) == 8
    obj = cats["Objektkontakte"]
    druck.kategorie_speichern(db, user.org_id, kategorie_id=obj.id, name="Gebäudekontakte")
    assert obj.objektkontakte is True
    assert druck.profil_laden(db, user.org_id, user.id, "gsl").layout == "detail"


def test_filter_bewusst_erweitern_und_felder_ausblenden(bestand, client):
    db, user, cats, add = bestand
    add(
        "Objektkontakt",
        ["Objektkontakte"],
        aktiv=False,
        druck_freigegeben=False,
        druckhinweis="Verborgener Druckhinweis",
    )
    db.commit()
    opts = druck.DruckEinstellungen(
        kategorien=[], nur_aktiv=False, nur_freigegeben=False, objektkontakte_ausschliessen=False, felder=[]
    )
    assert druck.auswahl(db, user.org_id, opts)["anzahl"] == 1
    _login(client, user.username)
    response = client.get("/kontakte/druckvorschau", params={"einstellungen": opts.model_dump_json()})
    assert response.status_code == 200
    assert "Objektkontakt" in response.text
    assert "+43 1234" not in response.text and "Verborgener Druckhinweis" not in response.text
    assert "Feuerwehr Test" not in response.text
    # Kategoriepflege ist auf den bestehenden Verwaltungsrollen beschränkt.
    assert client.get("/kontakte/druckkategorien").status_code == 200


def test_sortierungen_und_dubletten(bestand):
    db, user, cats, add = bestand
    add("Zulu", druck_prioritaet="hoch", druck_sortierung=10, organisation="Alpha")
    add("Alpha", druck_prioritaet="niedrig", druck_sortierung=-1, organisation="Zulu")
    for sorting, first in [("prioritaet", "Zulu"), ("name", "Alpha"), ("organisation", "Zulu"), ("manuell", "Alpha")]:
        result = druck.auswahl(db, user.org_id, druck.DruckEinstellungen(sortierung=sorting))
        assert result["kontakte"][0]["name"] == first
        assert all("Mögliche Dublette" in h["hinweise"] for h in result["hinweise"])


def test_profile_aktuelle_daten_und_eigentuemer(bestand):
    db, user, cats, add = bestand
    kontakt = add("Vorher")
    opts = druck.DruckEinstellungen(layout="detail", kategorien=[], sortierung="name", felder=["email"])
    profil = druck.profil_speichern(db, user.org_id, user.id, "Mein Profil", opts)
    assert druck.profil_laden(db, user.org_id, user.id, str(profil.id)) == opts
    assert "Vorher" not in profil.einstellungen_json
    kontakt.anzeigename = "Nachher"
    db.flush()
    assert (
        druck.auswahl(db, user.org_id, druck.profil_laden(db, user.org_id, user.id, str(profil.id)))["kontakte"][0][
            "name"
        ]
        == "Nachher"
    )
    with pytest.raises(ValueError):
        druck.profil_laden(db, user.org_id, user.id + 999, str(profil.id))
    with pytest.raises(ValueError):
        druck.profil_loeschen(db, user.org_id, user.id + 999, profil.id)
    replacement = druck.profil_speichern(db, user.org_id, user.id, "Mein Profil", druck.DruckEinstellungen())
    assert replacement.id == profil.id
    druck.profil_loeschen(db, user.org_id, user.id, profil.id)
    assert not any(p["persoenlich"] for p in druck.profile(db, user.org_id, user.id))


def test_mandantenisolation_auch_mit_system_session(bestand):
    db, user, cats, add = bestand
    add("Eigener Kontakt")
    org = FireDept(name="Fremde Organisation", slug="druck-fremd")
    db.add(org)
    db.flush()
    other = Kontakt(org_id=org.id, anzeigename="Fremder Kontakt", druck_freigegeben=True)
    category = KontaktKategorie(org_id=org.id, name="Fremde Kategorie")
    db.add_all([other, category])
    db.flush()
    # Explizite Filter schützen auch eine Session mit System-Kontext.
    set_tenant_context(db, None)
    result = druck.auswahl(db, user.org_id, druck.DruckEinstellungen(kategorien=[]))
    assert [k["name"] for k in result["kontakte"]] == ["Eigener Kontakt"]
    with pytest.raises(ValueError):
        druck.auswahl(db, user.org_id, druck.DruckEinstellungen(kategorien=[category.id]))
    with pytest.raises(ValueError):
        druck.kategorie_speichern(db, user.org_id, name="Übernahme", kategorie_id=category.id)
    druck.profil_speichern(db, user.org_id, user.id, "Org eins", druck.DruckEinstellungen())
    assert not any(p["persoenlich"] for p in druck.profile(db, org.id, user.id))


def test_profil_fail_closed_ohne_tenant():
    db = SessionLocal()
    try:
        with pytest.raises(TenantContextMissing):
            db.query(KontaktDruckprofil).all()
    finally:
        db.close()


def test_kennzeichnungen_ui_und_telefondatenschutz_beim_bearbeiten(bestand, client):
    db, user, cats, add = bestand
    kontakt = add("Bearbeiten", phones=[{"nummer": "+43 999", "verwendung": "privat"}])
    db.commit()
    _login(client, user.username)
    response = client.get(f"/kontakte/{kontakt.id}/bearbeiten")
    assert 'name="druck_freigegeben"' in response.text
    response = client.post(
        f"/kontakte/{kontakt.id}",
        data={
            "_csrf": client.cookies.get("ec_csrf"),
            "version": kontakt.version,
            "anzeigename": "Bearbeitet",
            "nummer": "+43 999",
            "kategorien": druck.KATEGORIEN[0],
            "druck_kennzeichnungen": "1",
            "aktiv": "1",
            "einsatzrelevant": "1",
            "druck_freigegeben": "1",
            "druck_prioritaet": "hoch",
            "druckhinweis": "Rufbereitschaft",
            "druck_sortierung": "5",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    db.expire_all()
    assert kontakt.einsatzrelevant and kontakt.druck_prioritaet == "hoch" and kontakt.druck_sortierung == 5
    assert kontakt.telefone[0].verwendung == "privat"
    assert druck.auswahl(db, user.org_id, druck.DruckEinstellungen())["kontakte"][0]["telefone"] == []


def test_dialog_vorschau_pdf_und_profil(bestand, client):
    db, user, cats, add = bestand
    kontakt = add("Sichtbarer Kontakt", einsatzrelevant=True)
    db.commit()
    _login(client, user.username)
    response = client.get("/kontakte/drucken")
    assert response.status_code == 200, response.text
    assert "1 passende Kontakte" in response.text
    assert "private, no-store" in response.headers["cache-control"]
    assert "PDF herunterladen" in response.text
    opts = druck.DruckEinstellungen(layout="detail", felder=list(druck.FELDER))
    params = {"einstellungen": opts.model_dump_json()}
    response = client.get("/kontakte/druckvorschau", params=params)
    assert response.status_code == 200 and "Sichtbarer Kontakt" in response.text
    assert "size: A4 portrait" in response.text
    response = client.get("/kontakte/druck.pdf", params=params)
    assert response.status_code == 200 and response.content.startswith(b"%PDF"), (
        response.text[:500] if response.status_code != 200 else ""
    )
    assert response.headers["cache-control"] == "private, no-store"
    response = client.post(
        "/kontakte/druckprofile",
        data={"_csrf": client.cookies.get("ec_csrf"), "name": "Einsatzleitung", **params},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get(response.headers["location"]).status_code == 200
    db.expire_all()
    assert kontakt.version == 0  # Druck verändert keine Kontaktdaten.
    assert client.get("/kontakte/druckvorschau", params={"einstellungen": '{"papier":"A0"}'}).status_code == 422


def test_mehrseitige_pdf_layouts(bestand):
    from datetime import datetime

    from pypdf import PdfReader

    from app.core.templating import templates
    from app.services.pdf_service import render_kontaktverzeichnis_pdf

    db, user, cats, add = bestand
    for i in range(85):
        add(f"Kontakt {i:03}", phones=[{"nummer": "+43 123 456789", "label": "Dienst"}])
    for layout in ("kompakt", "detail"):
        opts = druck.DruckEinstellungen(layout=layout, felder=list(druck.FELDER))
        data = druck.auswahl(db, user.org_id, opts)
        html = templates.env.get_template("kontakte/druckausgabe.html").render(
            opts=opts, org=db.get(FireDept, user.org_id), jetzt=datetime.now(UTC), **data
        )
        pdf = PdfReader(BytesIO(render_kontaktverzeichnis_pdf(html)))
        assert len(pdf.pages) > 1
        text = "\n".join(p.extract_text() for p in pdf.pages)
        assert text.count("Kontakt 000") == 1 and text.count("Kontakt 084") == 1
        assert "Seite 1 /" in text
        if layout == "kompakt":
            assert float(pdf.pages[0].mediabox.width) > float(pdf.pages[0].mediabox.height)
            assert "Telefonnummer(n)" in pdf.pages[1].extract_text()
        else:
            assert float(pdf.pages[0].mediabox.width) < float(pdf.pages[0].mediabox.height)


def test_mcp_gemeinsame_filter_und_profile(bestand):
    db, user, cats, add = bestand
    kontakt = add("MCP Einsatzkontakt")
    context = MCPContext(db=db, user=user, org_id=user.org_id)
    result = asyncio.run(mcp.kontakt_druckliste(context))
    assert result["anzahl"] == 1
    saved = asyncio.run(mcp.kontakt_druckprofil_speichern(context, "MCP Profil", {"layout": "detail"}))
    assert asyncio.run(mcp.kontakt_druckliste(context, profil_id=saved["id"]))["anzahl"] == 1
    read = asyncio.run(mcp.kontakt_lesen(context, kontakt.id))
    assert read["druck_freigegeben"] is True
    asyncio.run(mcp.kontakt_druckprofil_loeschen(context, int(saved["id"])))
    assert len(asyncio.run(mcp.kontakt_druckprofile(context))["profile"]) == 5


@pytest.mark.parametrize(
    "path", ["/kontakte/drucken", "/kontakte/druckvorschau", "/kontakte/druck.pdf", "/kontakte/druckkategorien"]
)
def test_einsatzrolle_hat_keinen_verzeichnisexport(client, path):
    user = _setup_user("druck_einsatz_" + uuid4().hex[:12], "readonly")
    _login(client, user.username)
    assert client.get(path).status_code == 403


def test_optionen_validieren():
    for values in ({"layout": "unbekannt"}, {"felder": ["notizen"]}, {"papier": "A0"}, {"beliebig": True}):
        with pytest.raises(ValidationError):
            druck.DruckEinstellungen(**values)


def test_mcp_http_druckwerkzeuge_und_schreibrechte(client):
    from tests.test_mcp_objekte import _rufe, _seed, _token

    seed = _seed("druck-http", {"admin": "kontakt_verwalter"})
    token = _token(client, seed, "admin")
    category = _rufe(client, token, "kontakt_kategorie_speichern", name="Einsatzkontakte", druck_standard=True)
    assert "id" in category, category
    contact = _rufe(
        client,
        token,
        "kontakt_anlegen",
        felder={
            "typ": "stelle",
            "anzeigename": "MCP Drucktest",
            "druck_freigegeben": True,
            "einsatzrelevant": True,
            "druck_prioritaet": "hoch",
        },
        telefone=[],
        kategorien=["Einsatzkontakte"],
    )
    assert "id" in contact, contact
    result = _rufe(client, token, "kontakt_druckliste")
    assert result["anzahl"] == 1
    assert result["kontakte"][0]["name"] == "MCP Drucktest"
    assert _rufe(client, token, "kontakt_lesen", kontakt_id=contact["id"])["einsatzrelevant"] is True
    profil = _rufe(
        client, token, "kontakt_druckprofil_speichern", name="HTTP Profil", einstellungen={"layout": "detail"}
    )
    assert _rufe(client, token, "kontakt_druckliste", profil_id=profil["id"])["anzahl"] == 1
    assert len(_rufe(client, token, "kontakt_druckprofile")["profile"]) == 6
    assert _rufe(client, token, "kontakt_druckprofil_loeschen", profil_id=int(profil["id"]))["geloescht"]
    # Nur Einsatzleserechte dürfen keines der neuen Tools aufrufen.
    reader = _seed("druck-http-reader", {"admin": "readonly"})
    reader_token = _token(client, reader, "admin")
    for name, args in [
        ("kontakt_druckliste", {}),
        ("kontakt_druckprofile", {}),
        ("kontakt_kategorie_speichern", {"name": "Verboten"}),
        ("kontakt_druckprofil_speichern", {"name": "Verboten", "einstellungen": {}}),
    ]:
        assert "__fehler__" in _rufe(client, reader_token, name, **args)


@pytest.mark.skipif(not os.environ.get("ECP_BROWSER_TEST"), reason="Browserabnahme mit ECP_BROWSER_TEST=1 aktivieren")
def test_browser_filter_vorschau_pdf_und_druck(bestand, client, tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    db, user, cats, add = bestand
    kontakt = add("Browser Einsatzkontakt", funktion="Bereitschaft", druckhinweis="24/7 erreichbar")
    db.commit()
    _logo_einrichten(db, user, tmp_path, monkeypatch)
    _login(client, user.username)

    def app_route(route):
        request = route.request
        if not request.url.startswith("http://ecp.test/"):
            route.abort()
            return
        response = client.request(
            request.method,
            request.url.replace("http://ecp.test", ""),
            content=request.post_data_buffer,
            headers={"Content-Type": request.headers.get("content-type", "")},
        )
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-encoding", "content-length"}
        }
        route.fulfill(status=response.status_code, headers=headers, body=response.content)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get("ECP_BROWSER_EXECUTABLE"))
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.route("**/*", app_route)
        page = context.new_page()
        page.goto("http://ecp.test/kontakte?q=Browser")
        page.get_by_role("link", name="Drucken", exact=True).click()
        expect(page.get_by_role("heading", name="1 passende Kontakte")).to_be_visible()
        frame = page.frame_locator("#druckvorschau")
        expect(frame.get_by_text("Browser Einsatzkontakt", exact=True)).to_be_visible()
        logo = frame.get_by_role("img", name="Organisationslogo von Druck-Testorganisation")
        expect(logo).to_be_visible()
        assert logo.evaluate("img => img.complete && img.naturalWidth > 0")
        page.locator('input[name="logo_anzeigen"]').uncheck()
        page.locator('select[name="layout"]').select_option("detail")
        page.get_by_role("button", name="Auswahl und Vorschau aktualisieren").click()
        expect(frame.get_by_role("heading", name="Kontaktverzeichnis")).to_be_visible()
        expect(frame.get_by_role("img")).to_have_count(0)
        page.screenshot(path=str(tmp_path / "druckdialog.png"), full_page=True)
        kontakt.druckhinweis = "Aktualisiert direkt vor dem Druck"
        db.commit()
        page.add_init_script("window.print = () => { window.__printed = true; };")
        page.get_by_role("button", name="Drucken", exact=True).click()
        page.wait_for_function("document.getElementById('druckvorschau').contentWindow.__printed === true")
        expect(frame.get_by_text("Aktualisiert direkt vor dem Druck", exact=False)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("link", name="PDF herunterladen").click()
        assert Path(download.value.path()).read_bytes().startswith(b"%PDF")
        page.locator('input[name="q"]').fill("Nicht vorhanden")
        page.get_by_role("button", name="Auswahl und Vorschau aktualisieren").click()
        expect(page.get_by_role("heading", name="0 passende Kontakte")).to_be_visible()
        browser.close()
