"""MariaDB-Vektorspeicher ausserhalb von ORM-Metadaten und Migrationen."""

import json
import re
import threading
from weakref import WeakKeyDictionary

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import settings

_faehigkeiten: WeakKeyDictionary[Engine, bool] = WeakKeyDictionary()
_sperre = threading.Lock()


def tabellenname() -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", settings.SEARCH_EMBED_MODEL.lower()).strip("_")
    dim = settings.SEARCH_EMBED_DIM
    name = f"search_vector_{slug}_{dim}"
    if not slug or not re.fullmatch(r"[a-z0-9_]+", slug) or dim < 1 or len(name) > 64:
        raise ValueError("Ungueltiger Vektortabellenname")
    return name


def setze_faehigkeit_zurueck() -> None:
    with _sperre:
        _faehigkeiten.clear()


def vektor_faehig(db: Session) -> bool:
    bind = db.get_bind()
    if bind.dialect.name not in ("mysql", "mariadb"):
        return False
    engine = bind if isinstance(bind, Engine) else bind.engine
    with _sperre:
        if engine not in _faehigkeiten:
            try:
                # Eigene Connection: ein fehlgeschlagenes DDL beruehrt die Session nicht.
                with engine.connect() as conn:
                    conn.execute(sa.text("CREATE TEMPORARY TABLE search_vector_probe (v VECTOR(3) NOT NULL)"))
                    conn.execute(sa.text("DROP TEMPORARY TABLE search_vector_probe"))
                _faehigkeiten[engine] = True
            except sa.exc.SQLAlchemyError:
                _faehigkeiten[engine] = False
        return _faehigkeiten[engine]


def stelle_tabelle_sicher(db: Session) -> None:
    if not vektor_faehig(db):
        return
    db.execute(sa.text(
        f"CREATE TABLE IF NOT EXISTS {tabellenname()} ("
        "chunk_id BIGINT PRIMARY KEY, org_id BIGINT NOT NULL, document_id BIGINT NOT NULL, "
        "inhalt_hash CHAR(64) NOT NULL, "
        f"embedding VECTOR({settings.SEARCH_EMBED_DIM}) NOT NULL, "
        "VECTOR INDEX embedding (embedding) DISTANCE=cosine, KEY (org_id, document_id))"
    ))


def speichere(db: Session, org_id: int, document_id: int,
              zeilen: list[tuple[int, str, list[float]]]) -> None:
    if not zeilen or not vektor_faehig(db):
        return
    # Auch bei einem kollidierenden chunk_id niemals fremde Org-Daten ueberschreiben.
    db.execute(sa.text(
        f"INSERT INTO {tabellenname()} (chunk_id, org_id, document_id, inhalt_hash, embedding) "
        "VALUES (:chunk, :org, :doc, :hash, VEC_FromText(:v)) ON DUPLICATE KEY UPDATE "
        "inhalt_hash=IF(org_id=:org AND document_id=:doc, VALUES(inhalt_hash), inhalt_hash), "
        "embedding=IF(org_id=:org AND document_id=:doc, VALUES(embedding), embedding)"
    ), [{"chunk": chunk, "org": org_id, "doc": document_id, "hash": hashwert, "v": json.dumps(vektor)}
        for chunk, hashwert, vektor in zeilen])


def lade_hashes(db: Session, org_id: int, document_id: int) -> dict[int, str]:
    if not vektor_faehig(db):
        return {}
    return {int(zeile[0]): str(zeile[1]) for zeile in db.execute(sa.text(
        f"SELECT chunk_id, inhalt_hash FROM {tabellenname()} WHERE org_id=:org AND document_id=:doc"
    ), {"org": org_id, "doc": document_id})}


def loesche_dokument(db: Session, org_id: int, document_id: int) -> None:
    if vektor_faehig(db):
        db.execute(sa.text(f"DELETE FROM {tabellenname()} WHERE org_id=:org AND document_id=:doc"),
                   {"org": org_id, "doc": document_id})


def loesche_chunks(db: Session, org_id: int, chunk_ids: list[int]) -> None:
    if chunk_ids and vektor_faehig(db):
        db.execute(sa.text(
            f"DELETE FROM {tabellenname()} WHERE org_id=:org AND chunk_id IN :chunks"
        ).bindparams(sa.bindparam("chunks", expanding=True)), {"org": org_id, "chunks": chunk_ids})


def knn(db: Session, org_id: int, vektor: list[float], quellen: list[str],
        k: int) -> list[tuple[int, int, float]]:
    if k <= 0 or not quellen or not vektor_faehig(db):
        return []
    distanz = "VEC_DISTANCE_COSINE(embedding, VEC_FromText(:v))"
    joins = (
        " JOIN search_document d ON d.id=v.document_id AND d.org_id=:org "
        "JOIN search_chunk c ON c.id=v.chunk_id AND c.document_id=d.id AND c.org_id=:org "
        "WHERE v.org_id=:org AND d.quelle IN :quellen "
    )
    parameter = {"org": org_id, "v": json.dumps(vektor), "quellen": quellen, "k": k, "kandidaten": k * 5}
    index_sql = (
        "SELECT v.document_id, v.chunk_id, v.distanz FROM ("
        f"SELECT document_id, chunk_id, org_id, {distanz} AS distanz FROM {tabellenname()} "
        f"ORDER BY {distanz} LIMIT :kandidaten) v" + joins + "ORDER BY v.distanz LIMIT :k"
    )
    abfrage = sa.text(index_sql).bindparams(sa.bindparam("quellen", expanding=True))
    zeilen = db.execute(abfrage, parameter).all()
    if len(zeilen) < k:
        # Der Org-Filter im Scan verhindert Verlust durch globale ANN-Kandidaten.
        exakt_sql = (
            f"SELECT v.document_id, v.chunk_id, {distanz} AS distanz FROM {tabellenname()} v"
            + " IGNORE INDEX (embedding)" + joins + "ORDER BY distanz LIMIT :k"
        )
        zeilen = db.execute(sa.text(exakt_sql).bindparams(sa.bindparam("quellen", expanding=True)), parameter).all()
    return [(int(z[0]), int(z[1]), float(z[2])) for z in zeilen]
