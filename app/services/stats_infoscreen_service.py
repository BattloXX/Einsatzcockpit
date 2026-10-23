"""Datensparsame Monitoransicht auf Basis der gemeinsamen Einsatzstatistik."""
from datetime import UTC, date, datetime
from statistics import median
from typing import cast

from sqlalchemy.orm import Session

from app.core.timezones import org_tz, to_org_tz
from app.models.master import FireDept
from app.services.stats_service import get_stats

MONTHS = ["Jänner", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def get_infoscreen_data(db: Session, org: FireDept) -> dict:
    as_of = datetime.now(UTC)
    today = as_of.astimezone(org_tz(org)).date()
    von = date(today.year, 1, 1)
    stats = get_stats(db, org.id, von, today, as_of=as_of.replace(tzinfo=None))
    local_starts = [cast(datetime, to_org_tz(i.started_at, org)) for i in stats.incidents]
    # Der erste gespeicherte Einsatz ist die belegbare Untergrenze, kein behaupteter
    # Erfassungsbeginn. Auch importierte Einsätze vor der Org-Anlage zählen mit.
    first = min(local_starts).date() if local_starts else None
    month_count = today.month - first.month + 1 if first else 0
    durations = []
    arrivals = []
    time_of_day = [0] * 4
    weekdays = [0] * 7
    duration_bins = [0] * 4
    snapshot = as_of.replace(tzinfo=None)
    for incident, started in zip(stats.incidents, local_starts, strict=True):
        time_of_day[started.hour // 6] += 1
        weekdays[started.weekday()] += 1
        # Einsatzbereitschaft ist ein fachlicher Endzeitpunkt. Automatische
        # Verwaltungsabschlüsse (48h/LIS) ergeben keine verlässliche Einsatzdauer.
        ended = incident.ready_again_at
        if ended is None and not (incident.closed_via_lis_auto or incident.autoclose_warn_sent_at):
            ended = incident.closed_at
        if ended and incident.started_at <= ended <= snapshot:
            minutes = (ended - incident.started_at).total_seconds() / 60
            durations.append(minutes)
            duration_bins[0 if minutes < 30 else 1 if minutes < 60 else 2 if minutes <= 120 else 3] += 1
        arrived = incident.on_scene_at
        if (arrived and incident.started_at <= arrived <= snapshot
                and (ended is None or arrived <= ended)
                and (incident.departed_at is None or arrived >= incident.departed_at)):
            arrivals.append((arrived - incident.started_at).total_seconds() / 60)

    by_month = {row["month"]: row for row in stats.by_month}
    months = []
    for month, label in enumerate(MONTHS, 1):
        available = first is not None and first.month <= month <= today.month
        row = by_month.get(f"{today.year}-{month:02d}", {})
        months.append({
            "label": label, "current": month == today.month, "future": month > today.month,
            **{key: row.get(key, 0) if available else None for key in ("count", "fire", "technical", "other")},
        })

    return {
        "generated_at": as_of.isoformat(), "timezone": str(org_tz(org)),
        "year": today.year, "from": von.isoformat(), "to": today.isoformat(),
        "coverage_from": first.isoformat() if first else None, "observed_months": month_count,
        "kpis": {
            "total": stats.total, "fire": stats.fire_count, "technical": stats.technical_count,
            "current_month": months[today.month - 1]["count"],
            "monthly_average": round(stats.total / month_count, 1) if month_count else None,
            "duration": round(median(durations), 1) if durations else None,
            "arrival": round(median(arrivals), 1) if arrivals else None,
        },
        "samples": {"duration": len(durations), "arrival": len(arrivals)},
        "months": months,
        "alarm_types": [{key: row[key] for key in ("code", "count", "category", "percent")}
                        for row in stats.by_alarm_type],
        "fleet": [{"code": row["code"], "count": row["anzahl_einsaetze"]}
                  for row in stats.vehicle_fleet_stats],
        # Ca. 1-km-Raster: weder Adressen noch IDs, Nummern, Texte oder genaue
        # Koordinaten an den passwortfreien Monitor übertragen.
        "map": [{"lat": round(row["lat"], 2), "lng": round(row["lng"], 2),
                 "category": row["category"]} for row in stats.map_markers],
        "map_center": [org.fallback_lat if org.fallback_lat is not None else 47.466,
                       org.fallback_lng if org.fallback_lng is not None else 9.752],
        "time_of_day": [{"label": label, "count": count if stats.total else None}
                        for label, count in zip(["00–06 Uhr", "06–12 Uhr", "12–18 Uhr", "18–24 Uhr"],
                                                time_of_day, strict=True)],
        "weekdays": [{"label": label, "count": count if stats.total else None}
                     for label, count in zip(WEEKDAYS, weekdays, strict=True)],
        "durations": [{"label": label, "count": count if durations else None}
                      for label, count in zip(["Unter 30 Min.", "30–60 Min.", "1–2 Stunden", "Über 2 Stunden"],
                                              duration_bins, strict=True)],
    }
