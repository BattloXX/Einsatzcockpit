"""Standortmeldung der Android-Geraete (24-h-Ziel); ausschliesslich budgetierte SELECTs."""

from datetime import timedelta

from sqlalchemy import select

from app.models.admin_security import utcnow
from app.models.user import DeviceToken
from app.services.diagnose.registry import MAX_IDS, CheckResult, Finding, register_check
from app.services.geraet_standort import STATUS_TEXT, VERALTET_NACH

AKTIV_SEIT = timedelta(days=7)
BLOCKIERT = ("permission_denied", "background_permission_missing", "location_disabled")


@register_check("app.standort", "app", "Standortmeldung der Android-Geraete (mindestens alle 24 h)", kosten="leicht")
def check(db, budget):
    now = utcnow()
    rows = budget.rows(
        db,
        select(DeviceToken.id, DeviceToken.last_location_at, DeviceToken.last_location_status)
        .where(
            DeviceToken.revoked_at.is_(None),
            DeviceToken.platform == "android",
            DeviceToken.last_used_at >= now - AKTIV_SEIT,
        )
        .order_by(DeviceToken.id),
    )
    result = CheckResult(vollstaendig=budget.complete)
    veraltet = [i for i, at, _ in rows if at is None or now - at > VERALTET_NACH]
    blockiert = [i for i, _, status in rows if status in BLOCKIERT]
    if veraltet:
        result.findings.append(Finding(
            "standort_veraltet", f"{len(veraltet)} Geraete ohne frische Position (> 24 h)", "warn",
            {"anzahl_mindestens": len(veraltet), "aktive_geraete": len(rows)}, {"device_token": veraltet[:MAX_IDS]},
            "Hypothese: Berechtigung fehlt, Ortung aus, Akku-Optimierung beendet die App oder der Server ist zu alt.",
            "Standortstatus je Geraet pruefen (Geraete-Login > Detail) und Berechtigungen am Geraet kontrollieren.",
        ))
    if blockiert:
        gruende = sorted({STATUS_TEXT[s] for _, _, s in rows if s in BLOCKIERT})
        result.findings.append(Finding(
            "standort_blockiert", f"{len(blockiert)} Geraete melden einen Standort-Hinderungsgrund", "warn",
            {"anzahl_mindestens": len(blockiert), "gruende": gruende},
            {"device_token": blockiert[:MAX_IDS]},
            "Hypothese: Standortberechtigung (auch Hintergrund) oder Ortungsdienst fehlt am Geraet.",
            "In den Android-Einstellungen Standort 'Immer zulassen' setzen und Ortung einschalten.",
        ))
    return result
