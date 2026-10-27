"""Heartbeats und Leader ohne Hostnamen oder Prozess-Freitexte ausgeben."""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.admin_security import utcnow
from app.models.worker_runtime import WorkerRuntime
from app.services.diagnose.registry import CheckResult, Finding, register_check


@register_check("worker.zustand", "worker", "Heartbeats, Leader je Host und Loop-Fristen")
def check(db, budget):
    t = WorkerRuntime.__table__
    rows = budget.rows(db, select(t.c.hostname, t.c.expires_at, t.c.heartbeat_at, t.c.is_leader,
                                  t.c.leader_error, t.c.loops).order_by(t.c.worker_id))
    now = utcnow()
    active = [r for r in rows if r.expires_at > now]
    leaders = Counter(r.hostname for r in active if r.is_leader)
    result = CheckResult()
    metrics = {"abgelaufen": sum(r.expires_at <= now for r in rows),
               "heartbeat_alt": sum(r.heartbeat_at < now - timedelta(seconds=20) for r in active),
               "mehrere_leader_pro_host": sum(n > 1 for n in leaders.values()),
               "leader_fehlt": int(not leaders), "loop_fehler": 0}
    for row in active:
        for values in row.loops.values():
            overdue = False
            last = values.get("last_started_at") if values.get("state") == "running" else values.get("last_success_at")
            if last:
                age = (now - datetime.fromisoformat(last).replace(tzinfo=None)).total_seconds()
                overdue = age > max(60, 3 * values.get("interval_s", 0))
            metrics["loop_fehler"] += int(bool(row.leader_error) or values.get("state") == "failed" or overdue)
    for key, count in metrics.items():
        if count:
            result.findings.append(Finding(key, "Worker: " + key,
                "fail" if key in ("mehrere_leader_pro_host", "leader_fehlt") else "warn", {"anzahl": count}))
    return result
