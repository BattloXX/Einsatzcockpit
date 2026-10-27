"""Dateipruefung mit Keyset-Pagination und begrenzten Dateisystemzugriffen."""
from pathlib import Path

from sqlalchemy import and_, func, or_, select

from app.config import settings
from app.services.diagnose.helpers import collect_ids, orphans, table
from app.services.diagnose.registry import MAX_IDS, CheckResult, Finding, register_check


@register_check("objekte.dokumente", "objekte", "Dateien, lesbare Seiten und Pflichtdaten", kosten="schwer")
def check(db, budget):
    result = CheckResult()
    orphans(db, budget, result, ("objekt_dokument", "objekt_dokument_seite"))
    d, p, o = table("objekt_dokument"), table("objekt_dokument_seite"), table("objekt")
    collect_ids(db, budget, result, d.name, and_(d.c.status == "fertig", ~select(p.c.id).where(
        p.c.dokument_id == d.c.id).exists()), "seiten_fehlen", "Fertiges Dokument ohne Seiten")
    collect_ids(db, budget, result, p.name, or_(p.c.einzel_pdf_pfad.is_(None),
        p.c.volltext.is_(None), func.trim(p.c.volltext) == ""), "seite_ohne_index",
        "Dokumentseiten ohne PDF-Verweis oder Text-Index")
    # Nur technisch verpflichtende Namen produktiver Objekte, keine geratenen Adresspflichten.
    count = db.execute(select(func.count()).select_from(o).where(o.c.entwurf_von_id.is_(None),
        func.trim(o.c.name) == "")).scalar_one()
    if count:
        result.findings.append(Finding("pflichtdaten", "Objekte ohne Pflichtname", evidenz={"anzahl": count}))
    root = Path(settings.OBJEKT_MEDIA_DIR).resolve()
    for source, column in ((d, d.c.pfad), (p, p.c.einzel_pdf_pfad)):
        last = 0
        missing = []
        total = 0
        while budget.remaining:
            budget.tick()
            rows = budget.rows(db, select(source.c.id, column).where(source.c.id > last)
                               .order_by(source.c.id), page_size=100)
            if not rows:
                break
            for ident, path in rows:
                budget.tick()
                target = (root / (path or "")).resolve()
                if not path or not target.is_relative_to(root) or not target.is_file():
                    total += 1
                    if len(missing) < MAX_IDS:
                        missing.append(ident)
            last = rows[-1][0]
            if len(rows) < 100:
                break
        else:
            budget.complete = False
        if total:
            result.findings.append(Finding([source.name, "datei"], "Dokumentdateien fehlen oder sind unzulaessig",
                "warn", {"anzahl_mindestens": total}, {source.name: missing}))
    return result
