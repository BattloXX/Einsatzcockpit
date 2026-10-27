"""Planreferenzen pruefen; freigegebene Plaene bleiben unveraendert."""
from sqlalchemy import and_, select

from app.services.diagnose.helpers import collect_ids, orphans, table
from app.services.diagnose.registry import MAX_IDS, CheckResult, Finding, register_check


@register_check("katplan.integritaet", "katplan", "Planquellen, Ziele und Pruefungen", kosten="mittel")
def check(db, budget):
    result = CheckResult()
    orphans(db, budget, result, ("katplan_element", "katplan_link", "katplan_relation",
                                "katplan_dokument", "katplan_dokument_seite", "katplan_element_phase"))
    p, e, d = table("katplan_plan"), table("katplan_element"), table("katplan_dokument")
    collect_ids(db, budget, result, p.name, and_(p.c.status == "freigegeben",
        p.c.zuletzt_geprueft_am.is_(None)), "pruefung_fehlt", "Freigegebener Plan ohne Pruefdatum")
    collect_ids(db, budget, result, e.name, and_(e.c.quelle_dokument_id.isnot(None), ~select(d.c.id).where(
        d.c.id == e.c.quelle_dokument_id, d.c.plan_id == e.c.plan_id, d.c.org_id == e.c.org_id).exists()),
        "quelle_fehlt", "Elementquelle fehlt oder gehoert zu anderem Plan", "fail")
    link = table("katplan_link")
    targets = {"kontakt": "kontakt", "kontakt_organisation": "kontakt_organisation", "objekt": "objekt",
               "fahrzeug": "vehicle_master", "wasserstelle": "wasserstelle"}
    for kind, target_name in targets.items():
        target = table(target_name)
        org = target.c.dept_id if kind == "fahrzeug" else target.c.org_id
        collect_ids(db, budget, result, link.name, and_(link.c.ziel_typ == kind, ~select(target.c.id).where(
            target.c.id == link.c.ziel_id, org == link.c.org_id).exists()), ["link", kind],
            "Planverknuepfung ohne gueltiges Ziel", "fail")
    collect_ids(db, budget, result, link.name, ~link.c.ziel_typ.in_(targets), "zieltyp",
                "Unbekannter Plan-Zieltyp", "fail")
    r = table("katplan_relation")
    collect_ids(db, budget, result, r.name, ~select(e.c.id).where(e.c.uuid == r.c.zu_element_uuid,
        e.c.plan_id == r.c.plan_id, e.c.org_id == r.c.org_id).exists(), "relation_ziel",
        "Planrelation ohne Zielelement", "fail")
    # Dieselbe begrenzte Seiten-Syntax wie bei der Planuebernahme verwenden.
    from app.services.katplan_quelle_service import parse_seiten

    rows = budget.rows(db, select(e.c.id, e.c.quelle_seiten, d.c.seitenzahl, d.c.id).join(
        d, and_(d.c.id == e.c.quelle_dokument_id, d.c.plan_id == e.c.plan_id, d.c.org_id == e.c.org_id))
        .where(e.c.quelle_seiten.isnot(None), e.c.quelle_seiten != "").order_by(e.c.id))
    invalid = []
    page = table("katplan_dokument_seite")
    for ident, references, total, dokument_id in rows:
        budget.tick()
        try:
            requested = parse_seiten(references, total, limit=MAX_IDS + 1)
            if len(requested) > MAX_IDS or len(requested) > budget.remaining:
                result.vollstaendig = False
                continue
            available = {r[0] for r in budget.rows(db, select(page.c.seiten_nr).where(
                page.c.dokument_id == dokument_id, page.c.seiten_nr.in_(requested)))}
            if set(requested) - available:
                invalid.append(ident)
        except ValueError:
            invalid.append(ident)
    if invalid:
        result.findings.append(Finding("seitenreferenz", "Planquelle mit ungueltiger oder fehlender Zielseite",
            "fail", {"anzahl_mindestens": len(invalid)}, {"katplan_element": invalid[:MAX_IDS]}))
    return result
