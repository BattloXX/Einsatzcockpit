"""Idempotenter Vektorabgleich und transaktionale Planung ohne Outbox-Import."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.master import OrgSettings
from app.models.suche import SearchChunk, SearchDocument, SearchOutbox
from app.services.suche import vektor
from app.services.suche.embedding import EmbeddingProvider


def stelle_chunk_sicher(db: Session, doc: SearchDocument) -> list[SearchChunk]:
    """Ergaenzt bei leeren Dokumenten einen echten, per KNN aufloesbaren Chunk."""
    chunks = list(db.scalars(select(SearchChunk).where(
        SearchChunk.org_id == doc.org_id, SearchChunk.document_id == doc.id,
    ).order_by(SearchChunk.chunk_nr)))
    if not chunks:
        text = "\n".join(wert for wert in (doc.titel, doc.untertitel, doc.search_keywords) if wert)
        chunk = SearchChunk(org_id=doc.org_id, document_id=doc.id, chunk_nr=0, text=text,
                            inhalt_hash=hashlib.sha256(text.encode()).hexdigest())
        db.add(chunk)
        db.flush()
        db.expire(doc, ["chunks"])
        chunks.append(chunk)
    return chunks


def plane_vektor_job(db: Session, org_id: int, document_id: int) -> int:
    """Plant hoechstens eine offene Zeile; kein Commit und kein Provider-Aufruf."""
    if not settings.SEARCH_SEMANTIC_ENABLED:
        return 0
    if not db.scalar(select(OrgSettings.suche_aktiv).where(OrgSettings.org_id == org_id)):
        return 0
    db.flush()
    vorhanden = db.scalar(select(SearchOutbox.id).where(
        SearchOutbox.org_id == org_id, SearchOutbox.quelle == "vektor",
        SearchOutbox.quell_id == str(document_id), SearchOutbox.status.in_(("pending", "retry")),
    ).limit(1))
    if vorhanden is not None:
        return 0
    jetzt = datetime.now(UTC).replace(tzinfo=None)
    db.add(SearchOutbox(org_id=org_id, quelle="vektor", quell_id=str(document_id),
                       status="pending", versuche=0, erstellt_am=jetzt, naechster_versuch_am=jetzt))
    return 1


def synchronisiere_vektoren(db: Session, org_id: int, document_id: int) -> str:
    """Gleicht nur DB-Zustaende ab; Transaktion und Wiederholung gehoeren dem Worker."""
    if not vektor.vektor_faehig(db):
        return "uebersprungen"
    vektor.stelle_tabelle_sicher(db)
    doc = db.scalar(select(SearchDocument).where(
        SearchDocument.org_id == org_id, SearchDocument.id == document_id,
    ))
    if doc is None:
        vektor.loesche_dokument(db, org_id, document_id)
        return "geloescht"
    chunks = stelle_chunk_sicher(db, doc)
    hashes = vektor.lade_hashes(db, org_id, document_id)
    geaendert = [chunk for chunk in chunks if hashes.get(chunk.id) != chunk.inhalt_hash]
    if geaendert:
        embeddings = EmbeddingProvider().embed_dokumente([(doc.titel, chunk.text) for chunk in geaendert])
        vektor.speichere(db, org_id, document_id, [
            (chunk.id, chunk.inhalt_hash, embedding)
            for chunk, embedding in zip(geaendert, embeddings, strict=True)
        ])
    vektor.loesche_chunks(db, org_id, sorted(set(hashes) - {chunk.id for chunk in chunks}))
    return "synchron"


def plane_fehlende(db: Session, org_id: int) -> int:
    """Prueft hoechstens 500 Dokumente und plant fehlende oder veraltete Vektoren."""
    if not settings.SEARCH_SEMANTIC_ENABLED or not vektor.vektor_faehig(db):
        return 0
    vektor.stelle_tabelle_sicher(db)
    geplant = 0
    for doc in db.scalars(select(SearchDocument).where(
        SearchDocument.org_id == org_id,
    ).order_by(SearchDocument.id).limit(500)):
        chunks = list(db.scalars(select(SearchChunk).where(
            SearchChunk.org_id == org_id, SearchChunk.document_id == doc.id,
        )))
        hashes = vektor.lade_hashes(db, org_id, doc.id)
        if not chunks or hashes != {chunk.id: chunk.inhalt_hash for chunk in chunks}:
            geplant += plane_vektor_job(db, org_id, doc.id)
    return geplant
