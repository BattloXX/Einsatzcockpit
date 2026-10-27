"""Begrenzte Anti-Joins; ausschliesslich IDs verlassen die Datenbank."""
from sqlalchemy import and_, select

from app.db import Base
from app.services.diagnose.registry import MAX_IDS, Finding


def table(name):
    return Base.metadata.tables[name]


def collect_ids(db, budget, result, name, condition, kern, titel, schweregrad="warn"):
    source = table(name)
    rows = budget.rows(db, select(source.c.id).where(condition).order_by(source.c.id))
    if rows:
        result.findings.append(Finding(kern, titel, schweregrad,
            {"anzahl_mindestens": len(rows)}, {name: [r[0] for r in rows[:MAX_IDS]]}))


def orphans(db, budget, result, names):
    for name in names:
        source = table(name)
        for fk in sorted(source.foreign_keys, key=lambda f: f.parent.name):
            target = fk.column.table.alias()
            match = target.c[fk.column.name] == fk.parent
            # Bei expliziten Org-Tabellen sind auch org-fremde Ziele ungueltig.
            if "org_id" in source.c and "org_id" in target.c:
                match = and_(match, target.c.org_id == source.c.org_id)
            missing = ~select(target.c[fk.column.name]).where(match).exists()
            collect_ids(db, budget, result, name, and_(fk.parent.isnot(None), missing),
                [name, fk.parent.name], "Verwaiste Beziehung: " + name + "." + fk.parent.name, "fail")
