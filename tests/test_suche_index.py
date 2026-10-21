"""Gezielte Tests fuer Quellen-Whitelists, Index-Lebenszyklus und Neuaufbau."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.core.tenant import set_tenant_context
from app.models.katplan import (
    KatplanDokument,
    KatplanDokumentSeite,
    KatplanElement,
    KatplanLink,
    KatplanPlan,
    KatplanVersion,
    KatplanVersionElement,
)
from app.models.kontakt import Kontakt, KontaktTelefon
from app.models.master import FireDept, OrgSettings
from app.models.objekt import (
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektDokument,
    ObjektDokumentSeite,
    ObjektGefahr,
    ObjektKategorie,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektZusatzadresse,
)
from app.models.suche import SearchChunk, SearchDocument
from app.models.user import User
from app.services.katplan_freigabe_service import freigeben
from app.services.suche.indexer import indexiere, inhalt_hash, neuaufbau
from app.services.suche.quellen import IndexChunk, IndexDokument, adapter_fuer, html_zu_text, zerlege_text
from app.services.suche.textsuche import volltext_kandidaten
from tests.conftest import TestingSession


@pytest.fixture
def daten():
    with TestingSession() as db:
        set_tenant_context(db, None)
        a, b = [FireDept(slug=uuid4().hex, name=f"Index {name}") for name in ("A", "B")]
        db.add_all([a, b])
        db.flush()
        o = Objekt(org_id=a.id, nummer=42, name="Rattpack", status="freigegeben",
                   vulgoname="Werk", strasse="Werkstrasse", hausnummer="7", plz="6922", ort="Wolfurt",
                   informationen="Tor links, Telefon +43 664 1234567.", anfahrtsweg="Zufahrt Ost",
                   kontakt_info_template="GeheimeKontaktvorlage", lat=47.4, lng=9.7)
        db.add(o)
        db.flush()
        yield db, a, b, o
        db.rollback()


def _doc(db, org, quelle="objekt", quell_id=None):
    query = select(SearchDocument).where(SearchDocument.org_id == org.id, SearchDocument.quelle == quelle)
    if quell_id is not None:
        query = query.where(SearchDocument.quell_id == str(quell_id))
    return db.scalars(query).one()


def test_objekt_whitelist_drei_chunks_und_suche(daten):
    db, a, _, o = daten
    k = ObjektKategorie(org_id=a.id, name="Industrie")
    m = MerkmalKatalog(org_id=a.id, name="Photovoltaik")
    g = GefahrenKatalog(org_id=a.id, name="Gas")
    kontakt = Kontakt(org_id=a.id, anzeigename="GeheimerKontaktname")
    db.add_all([k, m, g, kontakt])
    db.flush()
    o.kategorie_id = k.id
    db.add_all([
        ObjektMerkmal(org_id=a.id, objekt_id=o.id, merkmal_id=m.id, hinweis="Dach"),
        ObjektGefahr(org_id=a.id, objekt_id=o.id, gefahr_id=g.id, stoffname="Methan",
                     un_nummer="1971", gefahrklasse="2", gefahrnummer="23", detail="Nordlager"),
        ObjektZusatzadresse(org_id=a.id, objekt_id=o.id, bezeichnung="Hintereingang", strasse="Nebenweg"),
        ObjektBMA(org_id=a.id, objekt_id=o.id, bma_nummer="4711", bmz_standort="Empfang",
                  schluesselsafe_inhalt="GeheimerSafecode", benachrichtigung_sms="GeheimesSmsziel",
                  benachrichtigung_email="geheim@example.org"),
        ObjektKontakt(org_id=a.id, objekt_id=o.id, kontakt_id=kontakt.id),
        KontaktTelefon(org_id=a.id, kontakt_id=kontakt.id, nummer="+43 699 9876543"),
    ])
    db.flush()
    assert indexiere(db, a.id, "objekt", str(o.id)) == "neu"
    doc = _doc(db, a)
    assert doc.titel == "42 Rattpack" and doc.untertitel == "Werkstrasse 7, 6922 Wolfurt"
    assert len(doc.chunks) == 3
    assert [c.abschnitt for c in doc.chunks] == ["Stammdaten", "Merkmale und Gefahren", "BMA"]
    assert all(c.text.startswith(doc.titel + "\n") for c in doc.chunks)
    text = "\n".join(c.text for c in doc.chunks) + doc.search_keywords
    for verboten in ("GeheimerKontaktname", "GeheimerSafecode", "GeheimesSmsziel", "geheim@example.org",
                     "GeheimeKontaktvorlage", "9876543", "1234567"):
        assert verboten.casefold() not in text.casefold()
    for erlaubt in ("[Tel.]", "Industrie", "Hintereingang", "Photovoltaik", "Methan", "1971", "Empfang"):
        assert erlaubt in text
    assert "pv" in doc.search_keywords.split() and "brandmeldezentrale" in doc.search_keywords
    assert {t.document_id for t in volltext_kandidaten(db, a.id, ["bmz"])} == {doc.id}
    assert json.loads(doc.meta_json) == {"objekt_id": o.id}
    assert (doc.lat, doc.lng) == (o.lat, o.lng)


def test_status_arbeitskopie_und_loeschung(daten):
    db, a, _, o = daten
    andere = [Objekt(org_id=a.id, name="Entwurf", status="entwurf"),
              Objekt(org_id=a.id, name="Archiv", status="archiviert"),
              Objekt(org_id=a.id, name="Kopie", status="freigegeben", entwurf_von_id=o.id)]
    db.add_all(andere)
    db.flush()
    adapter = adapter_fuer("objekt")
    assert adapter.alle_ids(db, a.id) == [str(o.id)]
    for obj in andere:
        assert indexiere(db, a.id, "objekt", str(obj.id)) == "fehlt"
    assert indexiere(db, a.id, "objekt", str(o.id)) == "neu"
    o.status = "in_ueberarbeitung"
    db.flush()
    assert indexiere(db, a.id, "objekt", str(o.id)) == "unveraendert"
    ident = _doc(db, a).id
    o.status = "archiviert"
    db.flush()
    assert indexiere(db, a.id, "objekt", str(o.id)) == "geloescht"
    assert db.scalars(select(SearchChunk).where(SearchChunk.document_id == ident)).all() == []
    assert indexiere(db, a.id, "objekt", str(o.id)) == "fehlt"


def test_hash_und_aktualisierung(daten):
    db, a, _, o = daten
    assert indexiere(db, a.id, "objekt", str(o.id)) == "neu"
    doc = _doc(db, a)
    zeit, chunk_ids = doc.indexiert_am, [c.id for c in doc.chunks]
    assert zeit.tzinfo is None
    assert indexiere(db, a.id, "objekt", str(o.id)) == "unveraendert"
    assert doc.indexiert_am == zeit and [c.id for c in doc.chunks] == chunk_ids
    o.informationen = "Neuer Hinweis"
    db.flush()
    assert indexiere(db, a.id, "objekt", str(o.id)) == "aktualisiert"
    assert len(doc.chunks) == 1 and "Neuer Hinweis" in doc.chunks[0].text
    assert "+43" not in doc.chunks[0].text
    # indexiere committet keine Fachtransaktion.
    db.rollback()
    assert db.scalar(select(SearchDocument).where(SearchDocument.id == doc.id)) is None


def test_inhalt_hash_alle_felder_ausser_zeit():
    dok = IndexDokument("1", "Titel", None, "keyword", [IndexChunk("Text", 2, "Abschnitt")],
                        meta={"b": 2, "a": 1})
    assert inhalt_hash(dok) == inhalt_hash(replace(dok, meta={"a": 1, "b": 2},
                                                  geaendert_am=datetime.now(UTC)))
    for aenderung in ({"titel": "Neu"}, {"keywords": "neu"}, {"untertitel": "Neu"},
                      {"sichtbarkeit": "vertraulich"}, {"lat": 1.2}, {"lng": 2.3}, {"quell_id": "2"},
                      {"meta": {}}, {"chunks": [IndexChunk("Text", 3, "Abschnitt")]}):
        assert inhalt_hash(dok) != inhalt_hash(replace(dok, **aenderung))


def test_objekt_dokument_freigabe_version_seiten(daten):
    db, a, _, o = daten
    d = ObjektDokument(org_id=a.id, objekt_id=o.id, dateiname_original="Plan.pdf", pfad="plan.pdf")
    db.add(d)
    db.flush()
    seite = ObjektDokumentSeite(org_id=a.id, objekt_id=o.id, dokument_id=d.id,
                                seiten_nr=4, dokumentart="brandschutzplan", volltext="LangesWort " * 600)
    leer = ObjektDokumentSeite(org_id=a.id, objekt_id=o.id, dokument_id=d.id, seiten_nr=5)
    db.add_all([seite, leer])
    db.flush()
    adapter = adapter_fuer("objekt_dokument_seite")
    assert adapter.alle_ids(db, a.id) == [str(seite.id)]
    assert indexiere(db, a.id, adapter.quelle, str(seite.id)) == "neu"
    doc = _doc(db, a, adapter.quelle)
    assert doc.titel == "Plan.pdf, Seite 4" and doc.untertitel == o.name
    assert len(doc.chunks) > 1 and {c.seite for c in doc.chunks} == {4}
    assert all(c.text.startswith(doc.titel + "\n") and c.abschnitt == "brandschutzplan" for c in doc.chunks)
    assert json.loads(doc.meta_json) == {"objekt_id": o.id, "dokument_id": d.id, "seiten_nr": 4}
    d.freigabe_status = "entwurf"
    db.flush()
    assert indexiere(db, a.id, adapter.quelle, str(seite.id)) == "geloescht"
    d.freigabe_status, d.ist_aktuelle_version = "freigegeben", False
    db.flush()
    assert adapter.alle_ids(db, a.id) == []
    assert adapter.dokument(db, a.id, str(seite.id)) is None
    d.ist_aktuelle_version = True
    seite.volltext, seite.titel = None, "Nur Titel"
    db.flush()
    assert indexiere(db, a.id, adapter.quelle, str(seite.id)) == "neu"
    assert _doc(db, a, adapter.quelle).chunks[0].seite == 4
    o.status = "archiviert"
    db.flush()
    assert adapter.dokument(db, a.id, str(seite.id)) is None


def _katplan(db, a):
    p = KatplanPlan(org_id=a.id, titel="Snapshotplan", status="in_pruefung")
    user = User(org_id=a.id, username=uuid4().hex, password_hash="test", display_name="Freigeber")
    db.add_all([p, user])
    db.flush()
    e = KatplanElement(org_id=a.id, plan_id=p.id, typ="info", kennung="I1", titel="Notfallhinweis",
                       beschreibung_html="<p>Sammelpunkt &amp; Zufahrt</p>", vertraulich=True,
                       daten_json=json.dumps({"telefon": "GeheimeNummer", "MOBIL": "GeheimesMobil",
                                              "TelKontakt": "GeheimesTel", "eMail": "GeheimeMail",
                                              "hinweis": "Lagerhalle", "anzahl": 12, "pegel": 1.5,
                                              "verschachtelt": {"text": "GeheimVerschachtelt"}}),
                       tags_json='["Hochwasser", {"telefon": "GeheimTag"}]')
    db.add(e)
    db.flush()
    return p, user, e


def test_katplan_snapshot_vertraulich_und_versionswechsel(daten):
    db, a, _, _ = daten
    p, user, e = _katplan(db, a)
    quelle, ident = "katplan_element", f"{p.id}:{e.uuid}"
    assert indexiere(db, a.id, quelle, ident) == "fehlt"
    v = freigeben(db, p, user)
    db.flush()
    assert indexiere(db, a.id, quelle, ident) == "neu"
    doc = _doc(db, a, quelle)
    assert doc.sichtbarkeit == "vertraulich" and doc.untertitel == "Snapshotplan"
    assert doc.titel == "I1 Notfallhinweis" and doc.chunks[0].text.startswith(doc.titel + "\n")
    text = doc.chunks[0].text + doc.search_keywords
    assert "Geheim" not in text and "geheim" not in text
    assert "Sammelpunkt & Zufahrt" in text and "Lagerhalle" in text and "Hochwasser" in text
    assert "12" in text and "1.5" in text
    assert json.loads(doc.meta_json) == {"plan_id": p.id, "version_id": v.id, "element_uuid": e.uuid}
    e.titel = "ArbeitsstandNeu"
    p.titel = "ArbeitsplanNeu"
    db.flush()
    assert indexiere(db, a.id, quelle, ident) == "unveraendert"
    db.delete(e)
    db.add(KatplanElement(org_id=a.id, plan_id=p.id, typ="info", titel="Neues Element"))
    p.status = "in_pruefung"
    freigeben(db, p, user)
    db.flush()
    assert neuaufbau(db, a.id, quelle)["geloescht"] == 1
    docs = db.scalars(select(SearchDocument).where(SearchDocument.org_id == a.id)).all()
    assert len(docs) == 1 and docs[0].titel == "Neues Element"
    assert all(c.document_id == docs[0].id for c in db.scalars(
        select(SearchChunk).where(SearchChunk.org_id == a.id)))


def test_katplan_planart_akteure_und_links_ohne_telefonnummern(daten):
    db, a, _, _ = daten
    p, user, e = _katplan(db, a)
    p.planart = "alarm_einsatzplan"
    e.akteure_json = '["polizei", "unbekannt-x"]'
    kontakt = Kontakt(org_id=a.id, anzeigename="Leitstelle +43 664 1234567",
                      funktion="Bereitschaft +43 664 2345678")
    db.add(kontakt)
    db.flush()
    db.add_all([
        KontaktTelefon(org_id=a.id, kontakt_id=kontakt.id, nummer="+43 699 9876543"),
        KatplanLink(org_id=a.id, plan_id=p.id, element_id=e.id, ziel_typ="kontakt",
                    ziel_id=kontakt.id, rolle="zu_verstaendigen",
                    hinweis="Lage melden +43 664 3456789"),
    ])
    v = freigeben(db, p, user)
    db.flush()
    snapshot = db.scalars(select(KatplanVersionElement).where(
        KatplanVersionElement.version_id == v.id)).one()
    assert "9876543" in snapshot.links_json
    p.planart = "massnahmenplan"
    e.akteure_json = '["feuerwehr"]'
    kontakt.anzeigename = "ArbeitsstandKontakt"
    db.flush()
    ident = f"{p.id}:{e.uuid}"
    assert indexiere(db, a.id, "katplan_element", ident) == "neu"
    doc = _doc(db, a, "katplan_element", ident)
    text = doc.chunks[0].text
    assert text.startswith(doc.titel + "\n") and "\n\n" not in text
    assert "Alarm- und Einsatzplan" in text.splitlines()
    assert "alarm- und einsatzplan" in doc.search_keywords
    assert "aep" in doc.search_keywords.split()
    assert "Akteure: Polizei, unbekannt-x" in text
    assert "polizei" in doc.search_keywords.split() and "unbekannt-x" in doc.search_keywords.split()
    assert "Leitstelle [Tel.] Bereitschaft [Tel.] Lage melden [Tel.]" in text.splitlines()
    for verboten in ("1234567", "2345678", "3456789", "9876543", "ArbeitsstandKontakt", "Maßnahmenplan"):
        assert verboten.casefold() not in (text + doc.search_keywords).casefold()


@pytest.mark.parametrize("planart,label,kuerzel", [
    ("alarm_einsatzplan", "Alarm- und Einsatzplan", "aep"),
    ("massnahmenplan", "Maßnahmenplan", "mp"),
    ("katastrophenplan", "Katastrophenplan", "kp"),
])
def test_katplan_seite_planart_keywords(daten, planart, label, kuerzel):
    db, a, _, _ = daten
    p, user, _ = _katplan(db, a)
    freigeben(db, p, user)
    p.planart = planart
    d = KatplanDokument(org_id=a.id, plan_id=p.id, titel="Quellplan", dateiname_original="plan.pdf",
                        pfad="plan.pdf", groesse_bytes=10, seitenzahl=1)
    db.add(d)
    db.flush()
    s = KatplanDokumentSeite(org_id=a.id, plan_id=p.id, dokument_id=d.id, seiten_nr=1,
                             einzel_pdf_pfad="seite.pdf", volltext="Quelltext")
    db.add(s)
    db.flush()
    assert indexiere(db, a.id, "katplan_seite", str(s.id)) == "neu"
    doc = _doc(db, a, "katplan_seite", s.id)
    assert label.casefold() in doc.search_keywords and kuerzel in doc.search_keywords.split()


@pytest.mark.parametrize("roh", ["kaputt{", "null", "{}", '[null, 3, "falsch", {"snapshot": null}]', ""])
def test_katplan_kaputte_akteure_und_links(daten, roh):
    db, a, _, _ = daten
    p, user, e = _katplan(db, a)

    def setze_kaputtes_json(mapper, connection, snapshot):
        snapshot.akteure_json = snapshot.links_json = roh

    # Vor dem INSERT setzen: Freigegebene Snapshots lassen sich nicht mehr aendern.
    event.listen(KatplanVersionElement, "before_insert", setze_kaputtes_json)
    try:
        v = freigeben(db, p, user)
        db.flush()
    finally:
        event.remove(KatplanVersionElement, "before_insert", setze_kaputtes_json)
    snapshot = db.scalars(select(KatplanVersionElement).where(
        KatplanVersionElement.version_id == v.id)).one()
    assert snapshot.akteure_json == snapshot.links_json == roh
    doc = adapter_fuer("katplan_element").dokument(db, a.id, f"{p.id}:{e.uuid}")
    assert doc is not None and doc.titel == "I1 Notfallhinweis"
    assert doc.chunks[0].text.startswith(doc.titel + "\n")


@pytest.mark.parametrize("plan_json,label", [
    ("kaputt{", "Maßnahmenplan"), ("[]", "Maßnahmenplan"), ("{}", "Maßnahmenplan"),
    ('{"planart": "unbekannt"}', None),
    ('{"planart": [], "akteure": null}', "Maßnahmenplan"),
    ('{"planart": {}, "akteure": [null, {"schluessel": []}]}', "Maßnahmenplan"),
])
def test_katplan_plan_snapshot_fallback(daten, plan_json, label):
    db, a, _, _ = daten
    p, user, e = _katplan(db, a)
    p.planart = "massnahmenplan"
    e.akteure_json = '["unbekannt-x"]'

    def setze_plan_json(mapper, connection, snapshot):
        snapshot.plan_json = plan_json

    event.listen(KatplanVersion, "before_insert", setze_plan_json)
    try:
        v = freigeben(db, p, user)
        db.flush()
    finally:
        event.remove(KatplanVersion, "before_insert", setze_plan_json)
    assert v.plan_json == plan_json
    doc = adapter_fuer("katplan_element").dokument(db, a.id, f"{p.id}:{e.uuid}")
    assert doc is not None and doc.untertitel == p.titel
    assert "Akteure: unbekannt-x" in doc.chunks[0].text
    assert "unbekannt-x" in doc.keywords
    if label:
        assert label in doc.chunks[0].text and "mp" in doc.keywords.split()
    else:
        assert "Maßnahmenplan" not in doc.chunks[0].text and "mp" not in doc.keywords.split()


def test_katplan_seite_archiv_und_planstatus(daten):
    db, a, _, _ = daten
    p, user, _ = _katplan(db, a)
    freigeben(db, p, user)
    d = KatplanDokument(org_id=a.id, plan_id=p.id, titel="Quellplan", dateiname_original="plan.pdf",
                        pfad="plan.pdf", groesse_bytes=10, seitenzahl=2)
    db.add(d)
    db.flush()
    s = KatplanDokumentSeite(org_id=a.id, plan_id=p.id, dokument_id=d.id, seiten_nr=2,
                             einzel_pdf_pfad="seite.pdf", volltext="Katplantext " * 400)
    db.add(s)
    db.flush()
    adapter = adapter_fuer("katplan_seite")
    assert indexiere(db, a.id, adapter.quelle, str(s.id)) == "neu"
    doc = _doc(db, a, adapter.quelle)
    assert doc.titel == "Quellplan, Seite 2"
    assert len(doc.chunks) > 1 and {c.seite for c in doc.chunks} == {2}
    assert all(c.text.startswith(doc.titel + "\n") for c in doc.chunks)
    d.archiviert = True
    db.flush()
    assert adapter.alle_ids(db, a.id) == []
    assert indexiere(db, a.id, adapter.quelle, str(s.id)) == "geloescht"
    d.archiviert, p.status = False, "archiviert"
    db.flush()
    assert adapter.dokument(db, a.id, str(s.id)) is None
    assert adapter_fuer("katplan_element").alle_ids(db, a.id) == []
    p.status, p.aktuelle_version_id = "entwurf", None
    db.flush()
    assert adapter.alle_ids(db, a.id) == []


def test_neuaufbau_tenant_und_verwaiste_dokumente(daten):
    db, a, b, o = daten
    fremd = Objekt(org_id=b.id, name="FremdesWerk", status="freigegeben")
    db.add(fremd)
    db.flush()
    # Auch im Systemmodus muessen saemtliche Adapter explizit isolieren.
    assert neuaufbau(db, a.id)["neu"] == 1
    assert db.scalars(select(SearchDocument).where(SearchDocument.org_id == b.id)).all() == []
    assert volltext_kandidaten(db, b.id, ["Rattpack"]) == []
    assert adapter_fuer("objekt").dokument(db, a.id, str(fremd.id)) is None
    assert adapter_fuer("objekt").alle_ids(db, a.id) == [str(o.id)]
    assert indexiere(db, b.id, "objekt", str(fremd.id)) == "neu"
    db.commit()
    set_tenant_context(db, a.id)
    assert neuaufbau(db, a.id)["unveraendert"] == 1
    assert db.info["current_org_id"] == a.id
    o.status = "archiviert"
    db.flush()
    assert neuaufbau(db, a.id, "objekt")["geloescht"] == 1
    set_tenant_context(db, None)
    assert _doc(db, b).quell_id == str(fremd.id)


def test_zerlege_text_grenzen_ueberlappung_und_html():
    assert zerlege_text(" \n\t ") == []
    assert zerlege_text("kurzer Text") == ["kurzer Text"]
    text = " ".join(f"Wort{i:03}" for i in range(80))
    chunks = zerlege_text(text, 100, 30)
    assert len(chunks) > 1 and all(len(c) <= 100 for c in chunks)
    assert set(" ".join(chunks).split()) == set(text.split())
    for links, rechts in zip(chunks, chunks[1:], strict=False):
        assert links.split()[-1] in rechts.split()[:4]
    assert zerlege_text("Erster Satz. Zweiter Satz. Dritter Satz.", 30, 0)[0] == "Erster Satz. Zweiter Satz."
    assert zerlege_text("Erster langer Absatz\n\nZweiter Absatz mit Text und mehr", 30, 0)[0] == "Erster langer Absatz"
    assert zerlege_text("x" * 120 + " Ende", 50, 10) == ["x" * 120, "Ende"]
    with pytest.raises(ValueError):
        zerlege_text("Text", 10, 10)
    assert html_zu_text("<p>Tor &amp; Hof</p><p>Ost</p>") == "Tor & Hof Ost"
    assert html_zu_text(None) == ""


def test_cli_org_direkt(daten, monkeypatch, capsys):
    import app.cli as cli

    db, a, b, _ = daten
    db.add_all([OrgSettings(org_id=a.id, suche_aktiv=True), OrgSettings(org_id=b.id, suche_aktiv=False)])
    db.commit()
    monkeypatch.setattr(cli, "SessionLocal", TestingSession)
    monkeypatch.setattr("sys.argv", ["app.cli", "suche-neuaufbau", "--org", str(a.id), "--quelle", "objekt"])
    cli.main()
    assert f"Org {a.id}, Quelle objekt: neu=1" in capsys.readouterr().out
    assert _doc(db, a).quelle == "objekt"
    assert db.scalars(select(SearchDocument).where(SearchDocument.org_id == b.id)).all() == []
