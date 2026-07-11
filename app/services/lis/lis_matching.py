"""Verknüpfungs-Logik: LIS-Operation ↔ vorhandener (API-)Incident.

Die LIS-API liefert keine Leitstellennummer, die 1:1 auf einen bereits per
POST /api/v1/einsatz angelegten Incident verweist. Die Verknüpfung erfolgt
daher heuristisch über Alarmstichwort + Adresse innerhalb eines Zeitfensters
(siehe Plan / Nutzer-Entscheidung: 3 Stunden). Der freie Einsatzgrund-/
Meldungstext wird bewusst NICHT verglichen — siehe find_matching_incident().
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.services.lis.lis_mapping import normalize_address

DEFAULT_WINDOW_HOURS = 3


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def find_matching_incident(
    db: Session,
    org_id: int,
    *,
    alarm_type_code: str,
    street: str | None,
    city: str | None,
    started_at: datetime | None,
    lis_operation_id: str | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> Incident | None:
    """Findet einen bereits vorhandenen aktiven Incident, der zu einer LIS-Operation passt.

    Zwei Verknüpfungswege:
    1) Direkter Treffer über eine bereits gemerkte lis_operation_id — schnellster,
       eindeutiger Pfad, wird bei jedem Sync-Durchlauf zuerst versucht.
    2) Heuristik über Alarmstichwort (alarm_type_code) + normalisierte Adresse
       (Straße/Ort), eingeschränkt auf ein Zeitfenster von window_hours um
       started_at. Der freie Einsatzgrund-/Meldungstext wird NICHT verglichen —
       zwei Quellen (z.B. Alarmierungs-Rohtext vs. LIS) formulieren denselben
       Alarm oft unterschiedlich (Vorfall 2026-07-11: Einsätze #200/#201 an
       derselben Adresse mit identischem Stichwort blieben getrennt, weil ihr
       Meldungstext nach Normalisierung nicht exakt gleich war). Stichwort +
       Adresse gelten als ausreichend eindeutig für denselben Einsatz.

    Es werden ausschließlich AKTIVE Einsätze der Org verglichen — abgeschlossene
    Einsätze scheiden aus (reduziert False-Positives bei wiederkehrenden Alarmen
    an derselben Adresse, z.B. BMA-Fehlalarme).
    """
    if lis_operation_id:
        by_id = (
            db.query(Incident)
            .filter(
                Incident.primary_org_id == org_id,
                Incident.lis_operation_id == lis_operation_id,
            )
            .first()
        )
        if by_id:
            return by_id

    target_address = normalize_address(street, city)
    if not target_address:
        # Ohne Adresse ist die Heuristik nicht zuverlässig genug —
        # lieber keinen (Fehl-)Match als einen falschen.
        return None

    candidates = (
        db.query(Incident)
        .filter(
            Incident.primary_org_id == org_id,
            Incident.status == "active",
            Incident.alarm_type_code == alarm_type_code,
        )
        .all()
    )

    for candidate in candidates:
        if normalize_address(candidate.address_street, candidate.address_city) != target_address:
            continue
        if started_at is not None and candidate.started_at is not None:
            delta = abs(_as_aware(started_at) - _as_aware(candidate.started_at))
            if delta > timedelta(hours=window_hours):
                continue
        return candidate

    return None
