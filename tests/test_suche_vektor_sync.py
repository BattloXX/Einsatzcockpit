"""Vektorabgleich mit SQLite, Fake-Provider und org-getrenntem Vektorspeicher."""

import hashlib
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import cli
from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.master import FireDept, OrgSettings
from app.models.objekt import Objekt
from app.models.suche import SearchChunk, SearchDocument, SearchOutbox
from app.services.suche import embedding, indexer, outbox, vektor, vektor_sync
from app.services.suche.quellen import IndexChunk, IndexDokument
from app.services.suche.textsuche import volltext_kandidaten
from tests.conftest import TestingSession


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def daten(monkeypatch):
    monkeypatch.setattr(settings, "SEARCH_ENABLED", True)
    monkeypatch.setattr(settings, "SEARCH_SEMANTIC_ENABLED", True)
    monkeypatch.setattr(settings, "SEARCH_INDEX_WORKER_ENABLED", True)
    speicher = {}
    aufrufe = []

    def speichere(db, org_id, document_id, zeilen):
        for chunk_id, hashwert, wert in zeilen:
            speicher[org_id, document_id, chunk_id] = (hashwert, wert)

    def loesche_dokument(db, org_id, document_id):
        for key in list(speicher):
            if key[:2] == (org_id, document_id):
                del speicher[key]

    def loesche_chunks(db, org_id, chunk_ids):
        for key in list(speicher):
            if key[0] == org_id and key[2] in chunk_ids:
                del speicher[key]

    def embed(self, paare):
        aufrufe.extend(paare)
        return [[1.0, 0.0, 0.0] for _ in paare]

    monkeypatch.setattr(vektor, "vektor_faehig", lambda *_, **__: True)
    monkeypatch.setattr(vektor, "stelle_tabelle_sicher", lambda *_, **__: None)
    monkeypatch.setattr(vektor, "speichere", speichere)
    monkeypatch.setattr(vektor, "lade_hashes", lambda db, org_id, document_id: {
        key[2]: wert[0] for key, wert in speicher.items() if key[:2] == (org_id, document_id)
    })
    monkeypatch.setattr(vektor, "loesche_dokument", loesche_dokument)
    monkeypatch.setattr(vektor, "loesche_chunks", loesche_chunks)
    monkeypatch.setattr(embedding.EmbeddingProvider, "embed_dokumente", embed)
    with TestingSession() as db:
        set_tenant_context(db, None)
        # Globaler after_flush-Listener hinterlaesst Jobs aus anderen Tests.
        for job in db.scalars(select(SearchOutbox)):
            db.delete(job)
        db.commit()
        orgs = [FireDept(slug=uuid4().hex, name="Vektor " + name) for name in ("A", "B")]
        db.add_all(orgs)
        db.flush()
        ids = [org.id for org in orgs]
        db.add_all([OrgSettings(org_id=ident, suche_aktiv=True) for ident in ids])
        db.commit()
        yield db, orgs[0], orgs[1], speicher, aufrufe
        db.rollback()
        for modell in (SearchOutbox, SearchChunk, SearchDocument, Objekt, OrgSettings):
            for zeile in db.scalars(select(modell).where(modell.org_id.in_(ids))):
                db.delete(zeile)
            db.flush()
        db.commit()


def _doc(db, org, texte=("Zufahrt Ost", "Sammelplatz")):
    doc = SearchDocument(org_id=org.id, quelle="objekt", quell_id=uuid4().hex,
                         titel="Feuerwehrhaus", untertitel="Wolfurt", search_keywords="Garage", inhalt_hash="a" * 64)
    doc.chunks = [SearchChunk(org_id=org.id, chunk_nr=i, text=text, inhalt_hash=_hash(text))
                  for i, text in enumerate(texte)]
    db.add(doc)
    db.flush()
    return doc


def _jobs(db, org):
    db.flush()
    return list(db.scalars(select(SearchOutbox).where(SearchOutbox.org_id == org.id)))


def _lauf(db):
    for job in db.scalars(select(SearchOutbox).where(SearchOutbox.status.in_(("pending", "retry")))):
        job.naechster_versuch_am = outbox._jetzt() - timedelta(seconds=1)
    db.commit()
    return outbox.verarbeite_faellige(db)


def test_neuanlage_und_unveraendert(daten):
    db, org, _, speicher, aufrufe = daten
    doc = _doc(db, org)
    assert vektor_sync.synchronisiere_vektoren(db, org.id, doc.id) == "synchron"
    assert aufrufe == [(doc.titel, c.text) for c in doc.chunks]
    assert {key[2] for key in speicher} == {c.id for c in doc.chunks}
    aufrufe.clear()
    assert vektor_sync.synchronisiere_vektoren(db, org.id, doc.id) == "synchron"
    assert aufrufe == []


def test_aenderung_und_entfernter_chunk(daten):
    db, org, _, speicher, aufrufe = daten
    doc = _doc(db, org)
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    behalten, entfernt = doc.chunks
    behalten.text = "Zufahrt West"
    behalten.inhalt_hash = _hash(behalten.text)
    entfernt_id = entfernt.id
    db.delete(entfernt)
    db.flush()
    aufrufe.clear()
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    assert aufrufe == [(doc.titel, "Zufahrt West")]
    assert speicher[org.id, doc.id, behalten.id][0] == behalten.inhalt_hash
    assert all(key[2] != entfernt_id for key in speicher)


def test_dokument_geloescht(daten):
    db, org, _, speicher, _ = daten
    doc = _doc(db, org)
    ident = doc.id
    vektor_sync.synchronisiere_vektoren(db, org.id, ident)
    db.delete(doc)
    db.flush()
    assert vektor_sync.synchronisiere_vektoren(db, org.id, ident) == "geloescht"
    assert speicher == {}


def test_ohne_chunk_erhaelt_echten_chunk(daten):
    db, org, _, speicher, aufrufe = daten
    doc = _doc(db, org, ())
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    assert len(doc.chunks) == 1
    chunk = doc.chunks[0]
    assert chunk.id > 0 and chunk.org_id == org.id and chunk.document_id == doc.id
    assert chunk.text == "Feuerwehrhaus\nWolfurt\nGarage"
    assert aufrufe == [(doc.titel, chunk.text)]
    assert (org.id, doc.id, chunk.id) in speicher
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    assert len(aufrufe) == 1


def test_org_trennung(daten):
    db, org, fremd, speicher, aufrufe = daten
    doc = _doc(db, org)
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    vorher = dict(speicher)
    aufrufe.clear()
    assert vektor_sync.synchronisiere_vektoren(db, fremd.id, doc.id) == "geloescht"
    assert speicher == vorher and aufrufe == []
    # Auch inkonsistent zugeordnete Chunks anderer Orgs werden nicht eingebettet.
    fremder_chunk = SearchChunk(org_id=fremd.id, document_id=doc.id, chunk_nr=99,
                               text="Fremdgeheimnis", inhalt_hash="f" * 64)
    db.add(fremder_chunk)
    db.flush()
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    assert aufrufe == []
    assert all(key[2] != fremder_chunk.id for key in speicher)


def test_nicht_vektorfaehig(daten, monkeypatch):
    db, org, _, speicher, aufrufe = daten
    doc = _doc(db, org)
    monkeypatch.setattr(vektor, "vektor_faehig", lambda *_, **__: False)
    assert vektor_sync.synchronisiere_vektoren(db, org.id, doc.id) == "uebersprungen"
    assert speicher == {} and aufrufe == []


def test_indexer_fallback_planung_aenderung_und_loeschung(daten, monkeypatch):
    db, org, _, speicher, _ = daten
    dok = IndexDokument("test", "Titel", "Untertitel", "Keywords", [])

    class Adapter:
        def dokument(self, *args):
            return dok

    monkeypatch.setattr(indexer, "adapter_fuer", lambda *_, **__: Adapter())
    assert indexer.indexiere(db, org.id, "objekt", "test") == "neu"
    doc = db.scalars(select(SearchDocument).where(SearchDocument.org_id == org.id)).one()
    assert doc.chunks[0].text == "Titel\nUntertitel\nKeywords"
    assert len(_jobs(db, org)) == 1
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    dok = replace(dok, chunks=[IndexChunk("Neuer Text")])
    assert indexer.indexiere(db, org.id, "objekt", "test") == "aktualisiert"
    assert speicher == {}
    assert len(_jobs(db, org)) == 1
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    _jobs(db, org)[0].status = "done"
    db.flush()
    dok = None
    assert indexer.indexiere(db, org.id, "objekt", "test") == "geloescht"
    assert speicher == {}
    assert len(_jobs(db, org)) == 2


@pytest.mark.parametrize("ausfall", ["host", "breaker"])
def test_worker_retry_blockiert_text_nicht(daten, monkeypatch, ausfall):
    db, org, _, _, _ = daten
    objekt = Objekt(org_id=org.id, name="Vektorausfalltest", status="freigegeben")
    db.add(objekt)
    db.commit()
    assert _lauf(db) == 1
    doc = db.scalars(select(SearchDocument).where(SearchDocument.org_id == org.id)).one()
    text_job = next(j for j in _jobs(db, org) if j.quelle == "objekt")
    vektor_job = next(j for j in _jobs(db, org) if j.quelle == "vektor")

    def kaputt(self, paare):
        raise embedding.EmbeddingFehler("Host aus" if ausfall == "host" else "Embedding-Circuit-Breaker offen")

    monkeypatch.setattr(embedding.EmbeddingProvider, "embed_dokumente", kaputt)
    for versuch in range(1, 9):
        vorher = outbox._jetzt()
        assert _lauf(db) == (2 if versuch == 2 else 1)
        assert vektor_job.status == ("failed" if versuch == 8 else "retry")
        assert vektor_job.versuche == versuch and vektor_job.gesperrt_bis is None
        assert vektor_job.naechster_versuch_am >= vorher + timedelta(seconds=min(3600, 30 * 2 ** (versuch - 1)))
        assert text_job.status == "done" and text_job.versuche == 1
        assert doc.id in {k.document_id for k in volltext_kandidaten(db, org.id, ["Vektorausfalltest"])}
        if versuch == 1:
            # Im naechsten Lauf scheitert zuerst der aeltere Vektor-Job;
            # der nachfolgende Text-Job muss trotzdem verarbeitet werden.
            objekt.name = "Vektorausfalltest aktualisiert"
            db.commit()
        elif versuch == 2:
            assert all(j.status == "done" for j in _jobs(db, org) if j.quelle == "objekt")
            assert db.get(SearchDocument, doc.id).titel == "Vektorausfalltest aktualisiert"


@pytest.mark.parametrize("schalter", ["flag", "org"])
def test_deaktiviert_keine_planung_und_job_done(daten, monkeypatch, schalter):
    db, org, _, _, aufrufe = daten
    doc = _doc(db, org)
    assert vektor_sync.plane_vektor_job(db, org.id, doc.id) == 1
    if schalter == "flag":
        monkeypatch.setattr(settings, "SEARCH_SEMANTIC_ENABLED", False)
    else:
        db.scalar(select(OrgSettings).where(OrgSettings.org_id == org.id)).suche_aktiv = False
        db.flush()
    assert vektor_sync.plane_vektor_job(db, org.id, doc.id) == 0
    objekt = Objekt(org_id=org.id, name="Deaktiviert", status="freigegeben")
    db.add(objekt)
    db.flush()
    indexer.indexiere(db, org.id, "objekt", str(objekt.id))
    assert len([j for j in _jobs(db, org) if j.quelle == "vektor"]) == 1
    _lauf(db)
    assert all(j.status == "done" for j in _jobs(db, org))
    assert aufrufe == []


def test_planung_pending_retry_und_org(daten):
    db, org, fremd, _, _ = daten
    doc = _doc(db, org)
    assert outbox.loese_auf(db, org.id, "vektor", str(doc.id)) == [("vektor", str(doc.id))]
    assert vektor_sync.plane_vektor_job(db, org.id, doc.id) == 1
    assert vektor_sync.plane_vektor_job(db, org.id, doc.id) == 0
    job = _jobs(db, org)[0]
    job.status = "retry"
    assert vektor_sync.plane_vektor_job(db, org.id, doc.id) == 0
    assert vektor_sync.plane_vektor_job(db, fremd.id, doc.id) == 1
    job.status = "done"
    assert vektor_sync.plane_vektor_job(db, org.id, doc.id) == 1


def test_cli_planung_ohne_duplikate(daten, monkeypatch, capsys):
    db, org, fremd, _, aufrufe = daten
    docs = [_doc(db, org), _doc(db, org), _doc(db, fremd)]
    db.commit()
    monkeypatch.setattr(cli, "SessionLocal", TestingSession)
    cli.suche_vektor_aufbau(org.id)
    assert capsys.readouterr().out == "2 Vektor-Jobs geplant.\n"
    cli.suche_vektor_aufbau(org.id)
    assert capsys.readouterr().out == "0 Vektor-Jobs geplant.\n"
    assert {j.quell_id for j in _jobs(db, org)} == {str(d.id) for d in docs[:2]}
    assert _jobs(db, fremd) == []
    aktiv = select(OrgSettings.org_id).where(OrgSettings.suche_aktiv.is_(True))
    erwartet = len(list(db.scalars(select(SearchDocument.id).where(SearchDocument.org_id.in_(aktiv))))) - 2
    cli.suche_vektor_aufbau()
    assert capsys.readouterr().out == f"{erwartet} Vektor-Jobs geplant.\n"
    assert len(_jobs(db, fremd)) == 1 and aufrufe == []


def test_fehlende_hashes_und_abgleich(daten, monkeypatch):
    db, org, _, speicher, _ = daten
    doc = _doc(db, org)
    leer = _doc(db, org, ())
    vektor_sync.synchronisiere_vektoren(db, org.id, doc.id)
    assert vektor_sync.plane_fehlende(db, org.id) == 1
    assert {j.quell_id for j in _jobs(db, org)} == {str(leer.id)}
    chunk = doc.chunks[0]
    speicher[org.id, doc.id, chunk.id] = ("alt", [0, 1, 0])
    assert vektor_sync.plane_fehlende(db, org.id) == 1
    assert vektor_sync.plane_fehlende(db, org.id) == 0
    for job in _jobs(db, org):
        job.status = "done"
    db.commit()
    monkeypatch.setattr(outbox, "neuaufbau", lambda *_, **__: {})
    outbox.abgleich(db)
    assert len([j for j in _jobs(db, org) if j.status == "pending"]) == 2


def test_fehlende_maximal_500_dokumente(daten):
    db, org, _, _, _ = daten
    for _ in range(501):
        _doc(db, org, ())
    assert vektor_sync.plane_fehlende(db, org.id) == 500
    assert len(_jobs(db, org)) == 500
