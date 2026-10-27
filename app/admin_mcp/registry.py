"""Diagnosewerkzeuge ohne Fachdatenmutationen."""
import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass

from app import log_buffer
from app.admin_mcp.masking import mask_text
from app.config import settings
from app.services import system_diagnose as diagnose


@dataclass(frozen=True)
class AdminTool:
    description: str
    module: str


ADMIN_TOOLS = {
    "admin_system_uebersicht": AdminTool("Version, Build und Gesamtstatus der Teilchecks lesen.", "system"),
    "admin_dienste_pruefen": AdminTool("Gespeicherte Dienstzustaende organisationsuebergreifend zaehlen.", "dienste"),
    "admin_worker_pruefen": AdminTool("Worker-, Leader- und Loop-Zustaende zaehlen.", "worker"),
    "admin_redis_pruefen": AdminTool("Lokalen Redis-Verbindungszustand lesen.", "redis"),
    "admin_datenbank_pruefen": AdminTool("DB-Verbindung, Pool und Migrationsstand lesen.", "datenbank"),
    "admin_gateway_pruefen": AdminTool("Presence und offene/ueberfaellige Druck-/SMS-Jobs zaehlen.", "gateway"),
    "admin_logs_analysieren": AdminTool("Lokale Logs gefiltert und dedupliziert zaehlen, ohne Rohtexte.", "logs"),
}


READ_TOOLS = tuple(ADMIN_TOOLS)
DIAG_TOOLS = {
    "admin_diagnose_starten": AdminTool("Lesende Diagnose als begrenzten Hintergrundlauf anfordern.", "diagnose"),
    "admin_diagnose_status": AdminTool("Status, Fortschritt und uebersprungene Checks eines Laufs lesen.", "diagnose"),
    "admin_fehler_liste": AdminTool("Befunde nach Modul, Schweregrad, Status und Zeit paginiert lesen.", "diagnose"),
    "admin_fehler_details": AdminTool("Maskierte Evidenz, begrenzte IDs und Befundhistorie lesen.", "diagnose"),
}
ADMIN_TOOLS.update(DIAG_TOOLS)


def collect(db, name: str, **arguments) -> dict:
    if name == "admin_system_uebersicht":
        parts = {}
        for key in READ_TOOLS:
            if key in ("admin_system_uebersicht", "admin_logs_analysieren"):
                continue
            try:
                parts[key] = collect(db, key)
            except Exception:
                db.rollback()
                parts[key] = {"status": "fail", "evidence": {"fehler": "Teilcheck nicht verfuegbar"}}
        version = diagnose.version_info()
        states = [p["status"] for p in parts.values()] + [version["status"]]
        status = next((s for s in ("fail", "warn", "unbekannt") if s in states), "ok")
        return {"status": status, "evidence": {"version": version["evidence"],
                "teilchecks": {k: v["status"] for k, v in parts.items()}}}
    if name == "admin_dienste_pruefen":
        return diagnose.service_summary(db)
    if name == "admin_gateway_pruefen":
        return diagnose.gateway_summary(db)
    if name == "admin_datenbank_pruefen":
        return diagnose.db_status(db)
    if name == "admin_worker_pruefen":
        result = diagnose.worker_status(db)
        evidence = result["evidence"]
        return {"status": result["status"], "evidence": {
            "aktive_worker": evidence["worker_count"], "abgelaufene_worker": len(evidence["expired_workers"]),
            "leader": len(evidence["leader_workers"]),
            "fehlerhafte_loops": sum(loop["state"] == "failed" or loop["overdue"]
                                     for w in evidence["workers"] for loop in w["loops"])}}
    if name == "admin_redis_pruefen":
        result = diagnose.redis_status()
        return {"status": result["status"], "evidence": {
            key: bool(result["evidence"].get(key)) for key in ("configured", "connected")}}
    level, limit, search = arguments.get("level", "WARNING"), arguments.get("limit", 100), arguments.get("suchtext", "")
    if level not in log_buffer.LEVEL_ORDER or type(limit) is not int or not 1 <= limit <= 200 or len(search) > 200:
        raise ValueError("Ungueltige Log-Filter")
    rows = log_buffer.get_entries(2000, level)
    rows = [row for row in rows if search.casefold() in row["msg"].casefold()][-limit:]
    # Selbst maskierte Freitexte koennten Namen/Einsatzdetails enthalten. Nach der
    # Maskierung nur eine Signatur ausgeben; identische Meldungen zusammenfassen.
    groups: Counter = Counter()
    for row in rows:
        message = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]\d+\s*", "", row["msg"])
        signature = hmac.new(settings.SECRET_KEY.encode(), mask_text(message).encode(), hashlib.sha256).hexdigest()[:16]
        groups[(row["level"], signature)] += 1
    return {"status": "warn" if rows else "ok", "evidence": {
        "quelle": "lokaler Worker-Ringpuffer", "rohtexte": "nicht ausgegeben",
        "gruppen": [{"level": level, "signatur": signature, "anzahl": count}
                    for (level, signature), count in groups.items()]}}


REPAIR_TOOLS = {
    "admin_reparatur_vorschlagen": AdminTool(
        "Zu einem Befund feste Reparaturaktionen mit Risiko vorschlagen.", "reparatur"),
    "admin_reparatur_simulieren": AdminTool(
        "Begrenzte Reparatur ohne Schreibzugriff simulieren und Freigabebedarf anzeigen.", "reparatur"),
    "admin_reparatur_ausfuehren": AdminTool(
        "Simulierte Reparatur mit Bestaetigung und Idempotency-Key kontrolliert ausfuehren.", "reparatur"),
    "admin_reparatur_status": AdminTool(
        "Maskierte Vorschau und gespeicherten Reparaturstatus lesen.", "reparatur"),
    "admin_reparatur_verifizieren": AdminTool(
        "Reparaturergebnis durch eine lesende Nachpruefung verifizieren.", "reparatur"),
}
