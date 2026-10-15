"""Neutrale Kennzahlen fuer abgeschlossene oder laufende Planspiele."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from statistics import mean, median
from typing import Any

from sqlalchemy.orm import Session

from app.models.incident import Message, Task
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit
from app.models.planspiel import PlanspielBeobachtung, PlanspielRun, PlanspielRunEreignis, PlanspielZiel
from app.models.user import AuditLog, User

_ERLEDIGT = {"erledigt", "abgebrochen"}
_UEBERGAENGE = (
    ("eingang_erkundung", "Eingang -> Erkundung", "eingegangen", "erkundung"),
    ("erkundung_bewertet", "Erkundung -> Bewertet", "erkundung", "bewertet"),
    ("bewertet_disponiert", "Bewertet -> Disponiert", "bewertet", "disponiert"),
    ("disponiert_arbeit", "Disponiert -> In Arbeit", "disponiert", "in_arbeit"),
    ("arbeit_fertig", "In Arbeit -> Fertig gemeldet", "in_arbeit", "fertig_gemeldet"),
    ("eingang_fertig", "Eingang -> Fertig gemeldet", "eingegangen", "fertig_gemeldet"),
)
_AKTION_LABELS = {
    "entwurf": "Entwurf angelegt", "entwurf_geaendert": "Entwurf geändert", "gestartet": "Gestartet",
    "pausiert": "Pausiert", "fortgesetzt": "Fortgesetzt", "beendet": "Beendet",
    "folgemeldungen_gesetzt": "Folgemeldungen umgeschaltet", "ereignis_jetzt": "Ereignis sofort ausgelöst",
    "ereignis_uebersprungen": "Ereignis übersprungen", "ereignis_wiederholt": "Ereignis wiederholt",
    "freie_meldung": "Freie Meldung", "stichwort_geaendert": "Stichwort geändert",
    "generator_gestoppt": "Generator gestoppt", "generator_fortgesetzt": "Generator fortgesetzt",
    "welle_jetzt": "Welle ausgelöst", "wiederholt": "Als Wiederholung angelegt",
}
_DETAIL_LABELS = {"aktiv": "aktiv", "alt": "alt", "neu": "neu", "anzahl": "Anzahl", "ereignis_id": "Ereignis"}


def _aktion_label(action: str) -> str:
    kurz = action.removeprefix("planspiel.run.")
    return _AKTION_LABELS.get(kurz, kurz)


def _details(payload: dict[str, Any]) -> str:
    return ", ".join(
        f"{_DETAIL_LABELS.get(key, key)}: {value}" for key, value in payload.items() if value is not None
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _payload(audit: AuditLog) -> dict[str, Any]:
    try:
        value = json.loads(audit.payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _phase_value(site: IncidentSite) -> str:
    return getattr(site.phase, "value", str(site.phase))


def _minutes(start: datetime | None, end: datetime | None, tempo: float) -> float | None:
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds() * tempo / 60


def _summary(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return round(median(values), 2), round(mean(values), 2)


def auswertung(db: Session, run: PlanspielRun) -> dict[str, Any]:
    """Liest Kennzahlen eines Runs; alle Abfragen sind explizit org-begrenzt."""
    now = _now()
    ende = run.beendet_at or now
    real_min = _minutes(run.gestartet_at, ende, 1.0)
    pause_min = (run.pausiert_summe_s or 0) / 60
    uebungs_min = max(0.0, ((real_min or 0) - pause_min) * run.tempo) if real_min is not None else None
    snapshot = run.szenario_snapshot or {}
    inhalt = snapshot.get("inhalt") or {}

    events = db.query(PlanspielRunEreignis).filter(
        PlanspielRunEreignis.org_id == run.org_id,
        PlanspielRunEreignis.run_id == run.id,
    ).order_by(PlanspielRunEreignis.seq).all()
    event_rows = []
    status_counts: dict[str, int] = {}
    for event in events:
        status_counts[event.status] = status_counts.get(event.status, 0) + 1
        params = event.trigger_param or {}
        event_rows.append({
            "seq": event.seq, "status": event.status,
            "typ": params.get("ereignis_typ", event.quelle),
            "trigger": event.trigger_type, "faellig_sim_s": event.faellig_sim_s,
            "ausgefuehrt_sim_s": event.ausgefuehrt_sim_s,
            "ausgefuehrt_at": event.ausgefuehrt_at, "quelle": event.quelle,
            "sichtbarkeit": event.sichtbarkeit, "text": event.text, "ziel": event.ziel_typ,
            "ziel_id": event.ziel_id,
        })

    result: dict[str, Any] = {
        "kopf": {
            "titel": run.titel, "art": run.art,
            "szenario": {"code": snapshot.get("code"), "titel": inhalt.get("titel")},
            "seed": run.seed, "tempo": run.tempo, "gestartet_at": run.gestartet_at,
            "beendet_at": run.beendet_at, "dauer_real_min": real_min,
            "dauer_uebung_min": uebungs_min,
        },
        "ereignisse": event_rows,
        "ereignis_status": status_counts,
        "beobachtungen": [],
        "regie_aktionen": [],
    }

    users = {
        user.id: user.display_name or user.username
        for user in db.query(User).filter(User.org_id == run.org_id).all()
    }
    observations = db.query(PlanspielBeobachtung).filter(
        PlanspielBeobachtung.org_id == run.org_id,
        PlanspielBeobachtung.run_id == run.id,
    ).order_by(PlanspielBeobachtung.created_at).all()
    result["beobachtungen"] = [{
        "autor": users.get(row.user_id or -1, "Unbekannt"), "sim_s": row.sim_s,
        "text": row.text, "kategorie": row.kategorie, "lernziel": row.lernziel,
        "created_at": row.created_at,
    } for row in observations]
    audits = db.query(AuditLog).filter(
        AuditLog.org_id == run.org_id, AuditLog.entity_type == "planspiel_run",
        AuditLog.entity_id == run.id, AuditLog.action.like("planspiel.run.%"),
    ).order_by(AuditLog.created_at).all()
    result["regie_aktionen"] = [{
        "zeit": row.created_at, "aktion": row.action, "bezeichnung": _aktion_label(row.action),
        "benutzer": users.get(row.user_id or -1, "System"), "details": _details(_payload(row)),
    } for row in audits]

    if run.art == "einzel":
        messages = db.query(Message).filter(
            Message.incident_id == run.incident_id,
            Message.title.like("[Übung]%"),
        ).all() if run.incident_id else []
        tasks = db.query(Task).filter(Task.incident_id == run.incident_id).all() if run.incident_id else []
        result["einzel"] = {
            "meldungen": len(messages), "meldungen_erledigt": sum(row.is_done for row in messages),
            "aufgaben": len(tasks), "aufgaben_erledigt": sum(row.is_done for row in tasks),
        }
        return result

    ziele = db.query(PlanspielZiel).filter(
        PlanspielZiel.org_id == run.org_id, PlanspielZiel.run_id == run.id,
    ).order_by(PlanspielZiel.seq).all()
    site_ids = [ziel.site_id for ziel in ziele if ziel.site_id is not None]
    sites = db.query(IncidentSite).filter(
        IncidentSite.org_id == run.org_id, IncidentSite.major_incident_id == run.lage_id,
        IncidentSite.id.in_(site_ids or [-1]),
    ).all()
    site_by_id = {site.id: site for site in sites}
    phase_counts: dict[str, int] = {}
    for site in sites:
        phase = _phase_value(site)
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

    changes = db.query(AuditLog).filter(
        AuditLog.org_id == run.org_id, AuditLog.action == "major_incident.site.phase_changed",
        AuditLog.created_at >= (run.gestartet_at or now), AuditLog.created_at <= ende,
    ).order_by(AuditLog.created_at).all()
    phase_at: dict[tuple[int, str], datetime] = {}
    for audit in changes:
        payload = _payload(audit)
        if payload.get("lage_id") != run.lage_id or payload.get("site_id") not in site_by_id:
            continue
        key = (payload["site_id"], str(payload.get("to")))
        phase_at.setdefault(key, audit.created_at)

    times = []
    for code, label, start_phase, end_phase in _UEBERGAENGE:
        values: list[float] = []
        for site in sites:
            start = site.created_at if start_phase == "eingegangen" else phase_at.get((site.id, start_phase))
            finish = phase_at.get((site.id, end_phase))
            value = _minutes(start, finish, run.tempo)
            if value is not None:
                values.append(value)
        med, avg = _summary(values)
        times.append({"code": code, "bezeichnung": label, "anzahl": len(values), "median_min": med, "mittel_min": avg})

    finished_at = {site.id: phase_at.get((site.id, "fertig_gemeldet")) for site in sites}
    max_sim = max([ziel.erzeugt_sim_s or 0 for ziel in ziele] + [int((uebungs_min or 0) * 60)])
    verlauf = []
    for point in range(0, int(max_sim) + 1, 600):
        created = sum((ziel.erzeugt_sim_s or 0) <= point for ziel in ziele)
        done = 0
        if run.gestartet_at:
            for finished in finished_at.values():
                if finished and _minutes(run.gestartet_at, finished, run.tempo) is not None:
                    if (finished - run.gestartet_at).total_seconds() * run.tempo <= point:
                        done += 1
        verlauf.append({"sim_s": point, "neu": created, "fertig": done})

    dispatches = db.query(EinheitSiteDispatch, LageEinheit).join(
        LageEinheit, LageEinheit.id == EinheitSiteDispatch.einheit_id,
    ).filter(LageEinheit.lage_id == run.lage_id, EinheitSiteDispatch.site_id.in_(site_ids or [-1])).all()
    einheiten: dict[int, dict[str, Any]] = {}
    for dispatch, einheit in dispatches:
        row = einheiten.setdefault(einheit.id, {"label": einheit.label, "erkundung": 0, "abarbeitung": 0})
        if dispatch.auftragsart in {"erkundung", "abarbeitung"}:
            row[dispatch.auftragsart] += 1
    result["gsl"] = {
        "stellen_gesamt": len(ziele), "je_phase": phase_counts,
        "doppelmeldungen": sum(ziel.ist_duplikat_von is not None for ziel in ziele),
        "doppelmeldungen_erledigt": sum(
            ziel.ist_duplikat_von is not None
            and ziel.site_id is not None
            and ziel.site_id in site_by_id
            and _phase_value(site_by_id[ziel.site_id]) in _ERLEDIGT
            for ziel in ziele
        ),
        "zeiten": times, "verlauf": verlauf, "einheiten": list(einheiten.values()),
        "lagemeldungen_ueberfaellig": sum(
            site.naechste_lagemeldung_at is not None and site.naechste_lagemeldung_at < ende
            and _phase_value(site) not in _ERLEDIGT for site in sites
        ),
    }
    return result
