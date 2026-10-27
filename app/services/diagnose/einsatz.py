"""Nur belegbare aktuelle Zustaende, keine Rekonstruktion von Historien."""
from sqlalchemy import and_, or_, select

from app.models.incident import TASK_STATUS_VALUES
from app.models.major_incident import EINHEIT_STATUS_WERTE
from app.services.diagnose.helpers import collect_ids, orphans, table
from app.services.diagnose.registry import CheckResult, register_check


@register_check("einsatz.integritaet", "einsatz", "Dispositionen und Auftragsbeziehungen", kosten="mittel")
def check(db, budget):
    result = CheckResult()
    orphans(db, budget, result, ("einheit_site_dispatch", "task", "lage_einheit", "incident_site",
                                "lagefuehrung_feature", "einsatz_dokument"))
    task, incident = table("task"), table("incident")
    collect_ids(db, budget, result, "task", ~task.c.status.in_(TASK_STATUS_VALUES), "task_status",
                "Ungueltiger Auftragsstatus", "fail")
    collect_ids(db, budget, result, "task", and_(task.c.status.in_(("open", "in_progress")),
        select(incident.c.id).where(incident.c.id == task.c.incident_id, incident.c.status == "closed").exists()),
        "offen_in_geschlossen", "Offene Auftraege in abgeschlossenen Einsaetzen")
    collect_ids(db, budget, result, "task", or_(
        and_(task.c.status == "done", task.c.is_done.is_(False)),
        and_(task.c.status == "cancelled", task.c.is_cancelled.is_(False)),
        and_(task.c.status.in_(("open", "in_progress")),
             or_(task.c.is_done.is_(True), task.c.is_cancelled.is_(True)))),
        "task_flags", "Auftragsstatus widerspricht Abschlussmarkierung", "fail")
    d = table("einheit_site_dispatch")
    collect_ids(db, budget, result, d.name, ~d.c.einheit_status.in_(EINHEIT_STATUS_WERTE),
                "dispatch_status", "Ungueltiger Dispositionsstatus", "fail")
    unit, site = table("lage_einheit"), table("incident_site")
    collect_ids(db, budget, result, d.name, select(unit.c.id).join(
        site, site.c.id == d.c.site_id).where(unit.c.id == d.c.einheit_id,
        unit.c.lage_id != site.c.major_incident_id).correlate(d).exists(),
        "dispatch_lage", "Disposition verbindet unterschiedliche Lagen", "fail")
    # Mehrfach-Disposition ist im Modell ausdruecklich erlaubt, daher kein Dublettenalarm.
    return result
