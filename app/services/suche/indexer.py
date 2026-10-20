"""Idempotente Indexierung und Neuaufbau innerhalb des gesetzten Tenant-Kontexts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.suche import SearchChunk, SearchDocument
from app.services.suche.quellen import ADAPTER, IndexDokument, adapter_fuer, lade_adapter


def inhalt_hash(dok: IndexDokument) -> str:
    daten = asdict(dok)
    daten.pop("geaendert_am")
    return hashlib.sha256(json.dumps(daten, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _dokumente(org_id: int, quelle: str):
    return select(SearchDocument).where(
        SearchDocument.org_id == org_id, SearchDocument.quelle == quelle,
        SearchDocument.scope_typ == "org", SearchDocument.scope_id == 0,
    )


def _loesche_chunks(db: Session, doc: SearchDocument) -> None:
    # Explizites ORM-Loeschen funktioniert auch auf SQLite ohne FK-Pragma.
    for chunk in db.scalars(select(SearchChunk).where(
        SearchChunk.org_id == doc.org_id, SearchChunk.document_id == doc.id,
    )):
        db.delete(chunk)
    db.flush()
    db.expire(doc, ["chunks"])


def _loesche(db: Session, doc: SearchDocument) -> None:
    _loesche_chunks(db, doc)
    db.delete(doc)
    db.flush()


def indexiere(db: Session, org_id: int, quelle: str, quell_id: str) -> str:
    """Indexiert ohne Commit; der Aufrufer setzt zuvor den Tenant-Kontext."""
    dok = adapter_fuer(quelle).dokument(db, org_id, quell_id)
    doc = db.scalar(_dokumente(org_id, quelle).where(SearchDocument.quell_id == quell_id))
    if dok is None:
        if doc is None:
            return "fehlt"
        _loesche(db, doc)
        return "geloescht"
    hashwert = inhalt_hash(dok)
    if doc is not None and doc.inhalt_hash == hashwert:
        return "unveraendert"
    ergebnis = "neu" if doc is None else "aktualisiert"
    if doc is None:
        doc = SearchDocument(org_id=org_id, quelle=quelle, quell_id=quell_id, scope_typ="org", scope_id=0)
        db.add(doc)
    else:
        _loesche_chunks(db, doc)
    doc.titel = dok.titel
    doc.untertitel = dok.untertitel
    doc.search_keywords = dok.keywords
    doc.sichtbarkeit = dok.sichtbarkeit
    doc.lat = dok.lat
    doc.lng = dok.lng
    doc.meta_json = json.dumps(dok.meta, sort_keys=True)
    doc.inhalt_hash = hashwert
    doc.quelle_geaendert_am = dok.geaendert_am
    doc.indexiert_am = datetime.now(UTC).replace(tzinfo=None)
    doc.chunks = [SearchChunk(
        org_id=org_id, chunk_nr=nr, text=c.text, seite=c.seite, abschnitt=c.abschnitt,
        inhalt_hash=hashlib.sha256(c.text.encode()).hexdigest(),
    ) for nr, c in enumerate(dok.chunks)]
    db.flush()
    return ergebnis


def neuaufbau(db: Session, org_id: int, quelle: str | None = None) -> dict[str, int]:
    """Baut den Index auf und committet pro Quelle.

    Der Aufrufer muss set_tenant_context(db, org_id) oder Systemmodus (None)
    setzen. Ein int-Kontext reicht: Fachmodelle sind TenantScoped, Suchmodelle
    haben eigene Loader-Kriterien. Der bestehende Kontext wird nicht veraendert.
    """
    lade_adapter()
    quellen = [quelle] if quelle is not None else list(ADAPTER)
    zaehler = dict.fromkeys(("neu", "aktualisiert", "unveraendert", "geloescht", "fehlt"), 0)
    for name in quellen:
        ids = adapter_fuer(name).alle_ids(db, org_id)
        for quell_id in ids:
            zaehler[indexiere(db, org_id, name, quell_id)] += 1
        behalten = set(ids)
        for doc in db.scalars(_dokumente(org_id, name)).all():
            if doc.quell_id not in behalten:
                _loesche(db, doc)
                zaehler["geloescht"] += 1
        db.commit()
    return zaehler
