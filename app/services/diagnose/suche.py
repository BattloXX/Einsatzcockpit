"""Persistente Such-Outbox und Index-Zeitpunkte, keine Suchtexte."""
from datetime import timedelta

from sqlalchemy import and_, or_

from app.models.admin_security import utcnow
from app.services.diagnose.helpers import collect_ids, orphans, table
from app.services.diagnose.registry import CheckResult, register_check


@register_check("suche.rueckstand", "suche", "Suchindex und persistente Index-Outbox", kosten="mittel")
def check(db, budget):
    result = CheckResult()
    orphans(db, budget, result, ("search_chunk", "search_document", "search_outbox"))
    q, d = table("search_outbox"), table("search_document")
    collect_ids(db, budget, result, q.name, and_(q.c.status.in_(("pending", "retry")),
        or_(q.c.erstellt_am < utcnow() - timedelta(minutes=5), q.c.versuche >= 5)), "outbox_alt",
        "Such-Outbox mit Rueckstand")
    collect_ids(db, budget, result, d.name, d.c.quelle_geaendert_am > d.c.indexiert_am, "index_alt",
                "Suchindex aelter als gespeicherter Quellzeitpunkt")
    result.hinweis = "Offline-Client-Outbox: kein serverseitiges Modell vorhanden"
    return result
