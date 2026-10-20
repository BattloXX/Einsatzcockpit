"""Echte FULLTEXT-Pruefung gegen eine bereits migrierte MariaDB."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.tenant import set_tenant_context
from app.models.master import FireDept
from app.models.suche import SearchChunk, SearchDocument
from app.services.suche.textsuche import volltext_kandidaten


@pytest.mark.skipif(not os.environ.get("MARIADB_TEST_URL"), reason="MARIADB_TEST_URL nicht gesetzt")
def test_suche_fulltext():
    engine = create_engine(os.environ["MARIADB_TEST_URL"])
    org_ids = []
    try:
        with Session(engine) as db:
            set_tenant_context(db, None)
            assert engine.dialect.name in ("mysql", "mariadb")
            orgs = [FireDept(slug="suche-ft-" + uuid4().hex, name="FULLTEXT-Test " + name) for name in ("A", "B")]
            db.add_all(orgs)
            db.flush()
            org_ids = [org.id for org in orgs]
            doc = SearchDocument(org_id=org_ids[0], quelle="objekt", quell_id="ft-test",
                                 titel="Rattpack B2", search_keywords="", inhalt_hash="a" * 64)
            doc.chunks = [SearchChunk(org_id=org_ids[0], chunk_nr=0,
                                      text="Rattpack B2\nBrandmeldezentrale im Technikraum", inhalt_hash="b" * 64)]
            db.add(doc)
            db.commit()  # InnoDB macht FULLTEXT-Eintraege erst nach Commit sichtbar.
            doc_id, chunk_id = doc.id, doc.chunks[0].id
            for woerter in (["bmz"], ["brandmelde"], ["bmz", "rattpack"]):
                treffer = volltext_kandidaten(db, org_ids[0], woerter)
                assert any(k.document_id == doc_id and k.chunk_id == chunk_id for k in treffer)
            assert any(k.document_id == doc_id for k in volltext_kandidaten(db, org_ids[0], ["b2"]))
            assert volltext_kandidaten(db, org_ids[0], ['+bmz"*)(-'])
            assert volltext_kandidaten(db, org_ids[1], ["bmz"]) == []
            plan = db.execute(text(
                "EXPLAIN SELECT id FROM search_chunk WHERE org_id = :org "
                "AND MATCH(text) AGAINST(:q IN BOOLEAN MODE)"
            ), {"org": org_ids[0], "q": "+brandmelde*"}).mappings().all()
            assert any(row.get("key") == "ft_search_chunk_text" or row.get("type") == "fulltext" for row in plan)
    finally:
        try:
            with Session(engine) as db:
                set_tenant_context(db, None)
                for ident in org_ids:
                    org = db.get(FireDept, ident)
                    if org is not None:
                        db.delete(org)  # Suchdokumente und Chunks: FK-Cascade.
                db.commit()
        finally:
            engine.dispose()
