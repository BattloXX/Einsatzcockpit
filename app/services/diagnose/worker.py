"""Heartbeats und Leader ohne Hostnamen oder Prozess-Freitexte ausgeben."""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.admin_security import utcnow
from app.models.worker_runtime import WorkerRuntime
from app.services.diagnose.registry import CheckResult, Finding, register_check
from app.services.worker_gesundheit import worker_ausfaelle


@register_check("worker.zustand", "worker",
                "Heartbeats, Leader, Loop-Fristen, Prozessressourcen und unsaubere Neustarts")
def check(db, budget):
    t = WorkerRuntime.__table__
    rows = budget.rows(db, select(t.c.hostname, t.c.started_at, t.c.expires_at, t.c.heartbeat_at, t.c.is_leader,
                                  t.c.leader_error, t.c.loops, t.c.pid, t.c.metriken).order_by(t.c.worker_id))
    now = utcnow()
    active = [r for r in rows if r.expires_at > now]
    leaders = Counter(r.hostname for r in active if r.is_leader)
    result = CheckResult()
    metrics = {"abgelaufen": sum(r.expires_at <= now for r in rows),
               "ausfall": len(worker_ausfaelle(rows, now)),
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
            schweregrad = ("fail" if key in ("mehrere_leader_pro_host", "leader_fehlt")
                           else "info" if key == "abgelaufen" else "warn")
            result.findings.append(Finding(key, "Worker: " + key, schweregrad, {"anzahl": count}))
    result.findings.extend(ressourcen_befunde(rows, now))
    return result


def ressourcen_befunde(zeilen, jetzt) -> list[Finding]:
    """Nur aktive Worker bewerten; alte Heartbeats sind keine aktuellen Messungen."""
    from app.services.prozess_metriken import host_indizes, unsaubere_enden

    befunde = []
    indizes = host_indizes(zeilen)
    for host in unsaubere_enden(zeilen, jetzt):
        anzahl = host["anzahl_24h"]
        if anzahl >= 3:
            befunde.append(Finding({"typ": "unsaubere_enden", "host_index": host["host_index"]},
                "Worker: unsaubere Neustarts", "fail" if anzahl >= 10 else "warn", host))
    for zeile in zeilen:
        if zeile.expires_at <= jetzt or not zeile.metriken:
            continue
        metriken = zeile.metriken
        pruefungen = (
            ("rss_mb", metriken.get("rss_mb"), 600, None),
            ("loop_verzug", metriken.get("loop_verzug", {}).get("max_ms_15min"), 5000, 30000),
            ("langsame_queries", metriken.get("langsame_queries", {}).get("anzahl_15min"), 20, None),
        )
        for name, wert, warn, fail in pruefungen:
            if wert is not None and wert >= warn:
                befunde.append(Finding({"typ": name, "host_index": indizes[zeile.hostname], "pid": zeile.pid},
                    "Worker: " + name,
                    "fail" if fail is not None and wert >= fail else "warn",
                    {"host_index": indizes[zeile.hostname], "pid": zeile.pid, "wert": wert}))
    return befunde


def host_befunde(metriken: dict) -> list[Finding]:
    befunde = []
    verfuegbar = metriken.get("MemAvailable")
    if verfuegbar is not None and verfuegbar < 300:
        befunde.append(Finding("speicher_knapp", "Host: verfuegbarer Speicher knapp",
            "fail" if verfuegbar < 150 else "warn", {"verfuegbar_mb": verfuegbar}))
    gesamt, frei = metriken.get("SwapTotal"), metriken.get("SwapFree")
    if gesamt and frei is not None:
        prozent = 100 * (gesamt - frei) / gesamt
        if prozent >= 80:
            befunde.append(Finding("swap_voll", "Host: Swap stark belegt", "fail" if prozent >= 95 else "warn",
                                   {"belegt_prozent": prozent}))
    for name, schwelle in (("memory", 5), ("io", 10)):
        wert = metriken.get("psi", {}).get(name, {}).get("full", {}).get("avg300")
        if wert is not None and wert >= schwelle:
            befunde.append(Finding("psi_" + name, "Host: Ressourcendruck " + name, "warn", {"avg300": wert}))
    return befunde


@register_check("system.ressourcen", "system", "Host-Speicher, Swap und PSI-Ressourcendruck")
def check_host(db, budget):
    from app.services.prozess_metriken import host_lesen

    budget.tick()
    return CheckResult(host_befunde(host_lesen()))
