"""Backup-Befunde mit stabilen Schluesseln und automatischer Aufloesung."""
from app.services.backup_status import pruefen
from app.services.diagnose.registry import CheckResult, Finding, register_check


@register_check("backup", "backup", "Backup, Restore-Probe, Off-Site und Speicher pruefen")
def check(db, budget):
    budget.tick()
    result = pruefen()
    return CheckResult([Finding(
        b["key"], b["titel"], b["status"], result["evidence"],
        empfehlung=b["empfehlung"],
        ursache_hypothese="Backup-Zeitplan, letzter Lauf oder verfuegbare Ressourcen pruefen.",
    ) for b in result["befunde"]])
