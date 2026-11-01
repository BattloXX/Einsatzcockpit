"""App-Befunde aggregieren; ausschliesslich budgetierte SELECTs."""

from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import select

from app.models.admin_security import utcnow
from app.models.app_geraet import AppGeraet, AppGeraetFehler
from app.models.user import DeviceToken
from app.services.app_geraet_auswertung import REGELN, befunde, soll_version
from app.services.diagnose.helpers import table
from app.services.diagnose.registry import MAX_IDS, CheckResult, Finding, register_check


@register_check("app.geraete", "app", "App-Versionen, Geraetegesundheit und Fehler-Cluster", kosten="leicht")
def check(db, budget):
    now = utcnow()
    rows = budget.rows(
        db,
        select(AppGeraet, DeviceToken.duty_active, DeviceToken.revoked_at)
        .outerjoin(DeviceToken, DeviceToken.id == AppGeraet.device_token_id)
        .order_by(AppGeraet.id),
    )
    if not rows:
        return CheckResult(vollstaendig=budget.complete)
    budget.tick()
    soll = soll_version()
    budget.tick()
    ids = [r[0].id for r in rows]
    f = table("app_geraet_fehler")
    errors = budget.rows(
        db,
        select(AppGeraetFehler).where(f.c.geraet_id.in_(ids)).order_by(f.c.geraet_id, f.c.id),
    )
    by_device: dict[int, list] = defaultdict(list)
    clusters: dict[tuple[str, str], set[int]] = defaultdict(set)
    for (error,) in errors:
        by_device[error.geraet_id].append(error)
        clusters[error.komponente, error.code].add(error.geraet_id)
    groups: dict[str, list[int]] = defaultdict(list)
    for geraet, duty, revoked in rows:
        budget.tick()
        token = SimpleNamespace(duty_active=duty, revoked_at=revoked) if geraet.device_token_id else None
        for code in befunde(geraet, soll, token=token, fehler=by_device[geraet.id], jetzt=now):
            groups[code].append(geraet.id)
    result = CheckResult(vollstaendig=budget.complete)
    if soll is None:
        result.hinweis = "Soll-Version unbekannt; Versionsvergleich derzeit nicht moeglich."
    for code, affected in groups.items():
        title, severity, cause, recommendation = REGELN[code]
        result.findings.append(
            Finding(
                code,
                f"{len(affected)} Geraete: {title}",
                severity,
                {"anzahl_mindestens": len(affected)},
                {"app_geraet": affected[:MAX_IDS]},
                "Hypothese: " + cause,
                recommendation,
            )
        )
    for (component, code), affected_set in sorted(clusters.items()):
        if len(affected_set) < 2:
            continue
        evidence = {"anzahl_mindestens": len(affected_set), "komponente": component, "code": code}
        if (component == "poller" and code == "auth") or component == "fcm":
            evidence.update(
                modul="push",
                push_befunde_vergleichen=True,
                hinweis="Mit Push-Befunden aus admin_push_pruefen vergleichen.",
            )
        result.findings.append(
            Finding(
                ["cluster", component, code],
                f"Fehler-Cluster: {component}/{code} auf {len(affected_set)} Geraeten",
                "warn",
                evidence,
                {"app_geraet": sorted(affected_set)[:MAX_IDS]},
                "Hypothese: Mehrere Installationen sind von derselben Stoerung betroffen.",
                "App-Versionen, Fehlerzeitpunkte und gegebenenfalls Push-Befunde vergleichen.",
            )
        )
    return result
