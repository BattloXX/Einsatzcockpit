"""Org-gefilterte Betriebsdaten des Suchindex."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.master import OrgSettings
from app.models.suche import SearchChunk, SearchDocument, SearchOutbox

QUELLEN_LABELS = {
    "objekt": "Objekte", "objekt_dokument_seite": "Objektdokumentseiten",
    "katplan_element": "Plan-Elemente (Einsatz-/Katastrophenpläne)",
    "katplan_seite": "Plan-Quellseiten (Einsatz-/Katastrophenpläne)",
}


def indexstatus(db: Session, org_id: int) -> dict:
    dokumente = {q: (anzahl, stand) for q, anzahl, stand in db.execute(select(
        SearchDocument.quelle, func.count(SearchDocument.id), func.max(SearchDocument.indexiert_am),
    ).where(SearchDocument.org_id == org_id).group_by(SearchDocument.quelle))}
    chunks = dict(db.execute(select(SearchDocument.quelle, func.count(SearchChunk.id)).join(
        SearchChunk, SearchChunk.document_id == SearchDocument.id,
    ).where(SearchDocument.org_id == org_id, SearchChunk.org_id == org_id)
        .group_by(SearchDocument.quelle)).all())
    offen, aelteste = db.execute(select(func.count(SearchOutbox.id), func.min(SearchOutbox.erstellt_am)).where(
        SearchOutbox.org_id == org_id, SearchOutbox.status.in_(("pending", "retry")),
    )).one()
    failed = db.scalar(select(func.count(SearchOutbox.id)).where(
        SearchOutbox.org_id == org_id, SearchOutbox.status == "failed",
    ))
    fehler = db.scalars(select(SearchOutbox).where(
        SearchOutbox.org_id == org_id, SearchOutbox.letzter_fehler.is_not(None),
    ).order_by(SearchOutbox.id.desc()).limit(10)).all()
    return {
        "aktiv": bool(db.scalar(select(OrgSettings.suche_aktiv).where(OrgSettings.org_id == org_id))),
        "quellen": [dict(quelle=q, label=label, dokumente=dokumente.get(q, (0, None))[0],
                         chunks=chunks.get(q, 0), stand=dokumente.get(q, (0, None))[1])
                    for q, label in QUELLEN_LABELS.items()],
        "offen": offen, "failed": failed, "aelteste": aelteste, "fehler": fehler,
    }
