"""Echter VECTOR-Index gegen eine bereits migrierte MariaDB."""

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.tenant import set_tenant_context
from app.models.master import FireDept
from app.models.suche import SearchChunk, SearchDocument
from app.services.suche import vektor


@pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")
def test_suche_vektor(monkeypatch):
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    monkeypatch.setattr(settings, "SEARCH_EMBED_MODEL", "test_" + uuid4().hex[:12])
    monkeypatch.setattr(settings, "SEARCH_EMBED_DIM", 3)
    org_ids = []
    tabelle = vektor.tabellenname()
    try:
        with Session(engine) as db:
            set_tenant_context(db, None)
            assert vektor.vektor_faehig(db)
            vektor.stelle_tabelle_sicher(db)
            vektor.stelle_tabelle_sicher(db)
            orgs = [FireDept(slug="suche-vec-" + uuid4().hex, name="VECTOR-Test " + name) for name in ("A", "B")]
            db.add_all(orgs)
            db.flush()
            org_ids = [org.id for org in orgs]
            docs = []
            for org, quelle in [(orgs[0], "objekt"), (orgs[0], "objekt"),
                                (orgs[0], "katplan_seite"), (orgs[1], "objekt")]:
                doc = SearchDocument(org_id=org.id, quelle=quelle, quell_id=uuid4().hex,
                                     titel="Vektortest", inhalt_hash="a" * 64)
                doc.chunks = [SearchChunk(org_id=org.id, chunk_nr=0, text="Test", inhalt_hash="b" * 64)]
                db.add(doc)
                db.flush()
                docs.append(doc)
            for doc, wert in zip(docs, [[1, 0, 0], [0.8, 0.6, 0], [0, 1, 0], [1, 0, 0]], strict=True):
                vektor.speichere(db, doc.org_id, doc.id, [(doc.chunks[0].id, "b" * 64, wert)])
            db.commit()
            treffer = vektor.knn(db, org_ids[0], [1, 0, 0], ["objekt", "katplan_seite"], 3)
            assert [t[0] for t in treffer] == [d.id for d in docs[:3]]
            assert [t[2] for t in treffer] == pytest.approx([0, 0.2, 1], abs=1e-5)
            assert docs[3].id not in {t[0] for t in treffer}
            assert [t[0] for t in vektor.knn(db, org_ids[0], [1, 0, 0], ["katplan_seite"], 3)] == [docs[2].id]
            assert [t[0] for t in vektor.knn(db, org_ids[1], [1, 0, 0], ["objekt"], 3)] == [docs[3].id]
            plan = db.execute(text(
                f"EXPLAIN SELECT document_id, chunk_id FROM {tabelle} "
                "ORDER BY VEC_DISTANCE_COSINE(embedding, VEC_FromText(:v)) LIMIT :kandidaten"
            ), {"v": json.dumps([1, 0, 0]), "kandidaten": 15}).mappings().all()
            assert any(row.get("key") == "embedding" for row in plan)
            doc = docs[0]
            chunk = doc.chunks[0].id
            vektor.speichere(db, doc.org_id, doc.id, [(chunk, "c" * 64, [1, 0, 0])])
            assert vektor.lade_hashes(db, doc.org_id, doc.id) == {chunk: "c" * 64}
            vektor.speichere(db, org_ids[1], doc.id, [(chunk, "d" * 64, [0, 1, 0])])
            assert vektor.lade_hashes(db, doc.org_id, doc.id) == {chunk: "c" * 64}
            vektor.loesche_chunks(db, org_ids[1], [chunk])
            assert vektor.lade_hashes(db, doc.org_id, doc.id)
            vektor.loesche_chunks(db, doc.org_id, [chunk])
            assert vektor.lade_hashes(db, doc.org_id, doc.id) == {}
            vektor.loesche_dokument(db, docs[1].org_id, docs[1].id)
            assert vektor.lade_hashes(db, docs[1].org_id, docs[1].id) == {}
    finally:
        try:
            with Session(engine) as db:
                set_tenant_context(db, None)
                db.execute(text(f"DROP TABLE IF EXISTS {tabelle}"))
                for ident in org_ids:
                    org = db.get(FireDept, ident)
                    if org is not None:
                        db.delete(org)
                db.commit()
        finally:
            vektor.setze_faehigkeit_zurueck()
            engine.dispose()
