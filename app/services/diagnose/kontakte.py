"""Verwaiste Referenzen und vorsichtige Dublettenhinweise nur als IDs."""
from sqlalchemy import and_, func, select

from app.services.diagnose.helpers import orphans, table
from app.services.diagnose.registry import MAX_IDS, CheckResult, Finding, register_check


@register_check("kontakte.integritaet", "kontakte", "Kontakt-, Mitglied- und Benutzerbeziehungen", kosten="mittel")
def check(db, budget):
    result = CheckResult()
    orphans(db, budget, result, ("user", "member", "kontakt", "kontakt_organisation_funktion",
                                "kontakt_adresse", "kontakt_telefon", "kontakt_email"))
    a = table("kontakt")
    b = a.alias()
    rows = budget.rows(db, select(a.c.id, b.c.id).where(and_(a.c.id < b.c.id, a.c.org_id == b.c.org_id,
        func.trim(a.c.email) != "", func.lower(func.trim(a.c.email)) == func.lower(func.trim(b.c.email))))
        .order_by(a.c.id, b.c.id))
    if rows:
        result.findings.append(Finding("email_dublette", "Kontakte mit gleicher E-Mail (Dublettenhypothese)",
            evidenz={"paare_mindestens": len(rows)},
            betroffene_ids={"kontakt_paare": [list(r) for r in rows[:MAX_IDS]]}))
    return result
