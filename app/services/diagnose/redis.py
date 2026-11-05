"""Redis-Zaehler lokal und aus gespeicherten Worker-Heartbeats."""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.admin_security import utcnow
from app.models.worker_runtime import WorkerRuntime
from app.services import ws_bus
from app.services.diagnose.registry import CheckResult, Finding, register_check


@register_check("redis.zustand", "redis", "Publisher, Subscriber, Reconnects und Fehlerzaehler")
def check(db, budget):
    t = WorkerRuntime.__table__
    values = [budget.runtime.get("redis", ws_bus.status())] + [r[0] for r in budget.rows(db, select(t.c.redis).where(
        t.c.expires_at > utcnow()).order_by(t.c.worker_id))]
    result = CheckResult()
    grenze = utcnow() - timedelta(hours=24)
    for key in ("connected", "publisher_ok", "subscriber_connected", "reconnects",
                "publish_failures", "subscriber_failures"):
        count = sum(int(v.get(key, 0) or 0) if key.endswith(("failures", "reconnects"))
                    else int(v.get(key) is False) for v in values if v.get("configured"))
        if count:
            zaehler = key in ("reconnects", "publish_failures", "subscriber_failures")
            aktuell = False
            for wert in values:
                if not wert.get("configured") or not wert.get(key):
                    continue
                zeit = wert.get("last_error_at")
                # Persistierte ISO-Zeitpunkte stammen aus ws_bus; fehlende Zeiten
                # belegen bei intakter Verbindung keinen aktuellen Fehler.
                try:
                    kuerzlich = bool(zeit and datetime.fromisoformat(zeit).replace(tzinfo=None) >= grenze)
                except (ValueError, TypeError):
                    kuerzlich = False
                aktuell |= kuerzlich or any(wert.get(k) is False for k in (
                    "connected", "publisher_ok", "subscriber_connected"))
            result.findings.append(Finding(
                key, "Redis: " + key + (" (seit Prozessstart)" if zaehler else ""),
                "info" if zaehler and not aktuell else "warn",
                evidenz={"anzahl": count, "seit_prozessstart": zaehler},
            ))
    return result
