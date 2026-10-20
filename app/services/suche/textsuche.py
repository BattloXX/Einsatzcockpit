"""Volltext-Kandidaten mit MariaDB-FULLTEXT und portablem LIKE-Rueckfall."""

import re
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import match
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.suche import SearchChunk, SearchDocument
from app.services.suche.synonyme import expandiere, suchwoerter


@dataclass(frozen=True)
class Kandidat:
    document_id: int
    chunk_id: int | None
    rang: int


def baue_boolean_query(woerter: list[str]) -> tuple[str, list[str]]:
    """Nur einfache Tokens zulassen; mehrteilige Synonyme im FT weglassen."""
    teile = []
    kurze = []
    for wort in suchwoerter(" ".join(woerter)):
        # Auch interne Satzzeichen duerfen keine Boolean-Operatoren einschleusen.
        tokens = re.findall(r"[^\W_]+", wort, flags=re.UNICODE)
        for token in tokens:
            if len(token) < 3:
                kurze.append(token)
                continue
            alternativen = [w for w in expandiere([token]) if len(w) >= 3 and w.isalnum()]
            if len(alternativen) > 1:
                teile.append("+(" + " ".join(w + "*" for w in alternativen) + ")")
            else:
                teile.append("+" + token + "*")
    return " ".join(teile), list(dict.fromkeys(kurze))


def _like(wort: str) -> str:
    return "%" + wort.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _wort_bedingung(wort: str, *, mit_chunk: bool = False, nur_titel: bool = False) -> ColumnElement[bool]:
    spalten: list[ColumnElement] = [SearchDocument.titel.expression]
    if not nur_titel:
        spalten.append(SearchDocument.search_keywords.expression)
        if mit_chunk:
            spalten.append(SearchChunk.text.expression)
    return sa.or_(*(sa.func.lower(spalte).like(_like(w), escape="\\")
                    for w in expandiere([wort]) for spalte in spalten))


def volltext_kandidaten(
    db: Session, org_id: int, woerter: list[str], *, quellen: list[str] | None = None, limit: int = 60,
) -> list[Kandidat]:
    if not woerter or limit <= 0:
        return []
    woerter = suchwoerter(" ".join(woerter))
    if not woerter:
        return []
    dokument_filter = [SearchDocument.org_id == org_id]
    if quellen is not None:
        dokument_filter.append(SearchDocument.quelle.in_(quellen))
    chunk_filter = [*dokument_filter, SearchChunk.org_id == org_id]
    titel_score = sa.case((sa.or_(*(_wort_bedingung(w, nur_titel=True) for w in woerter)), 1), else_=0)
    dokument_score: ColumnElement = titel_score
    chunk_score: ColumnElement = titel_score
    parameter = {}
    if db.get_bind().dialect.name in ("mysql", "mariadb"):
        query, kurze = baue_boolean_query(woerter)
        if query:
            parameter["q"] = query
            chunk_score = match(SearchChunk.text.expression, against=sa.bindparam("q")).in_boolean_mode()
            dokument_score = match(
                SearchDocument.titel.expression, SearchDocument.search_keywords.expression, against=sa.bindparam("q"),
            ).in_boolean_mode()
            chunk_filter.append(chunk_score > 0)
            dokument_filter.append(dokument_score > 0)
        for wort in kurze:
            chunk_filter.append(_wort_bedingung(wort, mit_chunk=True))
            dokument_filter.append(_wort_bedingung(wort))
    else:
        for wort in woerter:
            chunk_filter.append(_wort_bedingung(wort, mit_chunk=True))
            dokument_filter.append(_wort_bedingung(wort))
    chunks = sa.select(
        SearchDocument.id.label("document_id"), SearchChunk.id.label("chunk_id"),
        chunk_score.label("score"), SearchChunk.chunk_nr.label("chunk_nr"),
    ).join(SearchChunk, SearchChunk.document_id == SearchDocument.id).where(*chunk_filter)
    dokumente = sa.select(
        SearchDocument.id.label("document_id"), sa.literal(None, type_=sa.BigInteger).label("chunk_id"),
        dokument_score.label("score"), sa.literal(-1).label("chunk_nr"),
    ).where(*dokument_filter)
    # Jeder Zweig liefert jeden Primaerschluessel genau einmal; der Dokumentzweig
    # besitzt ausschliesslich chunk_id=None. Damit sind die Mengen disjunkt.
    treffer = sa.union_all(chunks, dokumente).subquery()
    abfrage = sa.select(treffer.c.document_id, treffer.c.chunk_id).order_by(
        treffer.c.score.desc(), treffer.c.document_id, treffer.c.chunk_nr, treffer.c.chunk_id,
    ).limit(limit)
    return [Kandidat(zeile.document_id, zeile.chunk_id, rang)
            for rang, zeile in enumerate(db.execute(abfrage, parameter), start=1)]
