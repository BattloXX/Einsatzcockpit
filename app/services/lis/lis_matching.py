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
from app.services.lis.lis_mapping import _normalize_text, normalize_address

DEFAULT_WINDOW_HOURS = 3

# Fallback-Teilstring-Match (siehe find_matching_incident()): Mindestlänge des
# normalisierten Straßennamens, ab der ein Substring-Treffer als aussagekräftig
# gilt — verhindert, dass sehr kurze/generische Fragmente fälschlich matchen.
_MIN_FALLBACK_STREET_LEN = 5


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
    lis_operation_number: str | None = None,
    house_no: str | None = None,
    report_text: str | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> Incident | None:
    """Findet einen bereits vorhandenen aktiven Incident, der zu einer LIS-Operation passt.

    Vier Verknüpfungswege (der Reihe nach versucht):
    1) Direkter Treffer über eine bereits gemerkte lis_operation_id — schnellster,
       eindeutiger Pfad, wird bei jedem Sync-Durchlauf zuerst versucht.
    2) Direkter Treffer über die stabile Leitstellen-Einsatznummer
       (lis_operation_number, z.B. "f26006436") — bewusst UNABHÄNGIG vom Status
       (auch geschlossene Einsätze), weil die LIS-GUID (Operation.Id) sich
       zwischen zwei Polls ändern kann, während die Einsatznummer stabil bleibt
       (Vorfall 2026-07-21: Einsatz f26006436 wurde doppelt angelegt — Ursache
       war u.a., dass GUID-Änderungen bzw. Auto-Close+Reopen mit neuer GUID nur
       gegen AKTIVE Kandidaten prüften; die Einsatznummer selbst floss bisher
       gar nicht ins Matching ein, obwohl sie dafür der zuverlässigste Schlüssel
       wäre). Nur relevant, wenn lis_operation_number mitgegeben wird — andere
       Aufrufer (api_v1.py, serial_alarm_service.py) kennen keine LIS-Nummer und
       lassen dieses Argument weg, dann wird diese Stufe übersprungen.
    3) Heuristik über Alarmstichwort (alarm_type_code) + normalisierte Adresse
       (Straße/Hausnummer/Ort), eingeschränkt auf ein Zeitfenster von
       window_hours um started_at. Der freie Einsatzgrund-/Meldungstext wird
       hier NICHT verglichen — zwei Quellen (z.B. Alarmierungs-Rohtext vs. LIS)
       formulieren denselben Alarm oft unterschiedlich (Vorfall 2026-07-11:
       Einsätze #200/#201 an derselben Adresse mit identischem Stichwort
       blieben getrennt, weil ihr Meldungstext nach Normalisierung nicht exakt
       gleich war). Stichwort + Adresse gelten als ausreichend eindeutig für
       denselben Einsatz.
    4) Fallback, NUR wenn (3) keinen Treffer liefert: Teilstring-Match des
       Straßennamens der einen Seite im freien report_text der anderen Seite
       (Vorfall 2026-07-14: der lokale Pager-Text-Parser des seriellen Gateways
       scheiterte an einem zusätzlichen Ortsteil-/Rufnamen-Präfix
       ("bregenz VORKLOSTER untere burggräflergasse…", "_drehleiter r2 lauterach
       fellentorstraße…") und lieferte leere Adressfelder — die Operation kam
       vom LIS mit korrekter Adresse, matchte aber nicht, weil (3) eine
       strukturierte Adresse auf BEIDEN Seiten braucht. Absichtlich NUR ein
       Fallback (nicht Teil von (3)) UND zusätzlich hart darauf beschränkt, dass
       mindestens eine der beiden Seiten tatsächlich KEINE strukturierte Adresse
       hat (siehe target_address/cand_address_norm-Prüfung im Code): an
       Sturmtagen können mehrere echte, unterschiedliche Einsätze mit demselben
       Stichwort (häufig T9) binnen weniger Minuten auflaufen — die haben aber
       durchgehend eine strukturierte Adresse auf beiden Seiten und erreichen
       diesen Fallback dadurch nie, selbst wenn ein Straßenname zufällig im
       Meldungstext des jeweils anderen vorkäme.

    Ab Stufe 3 werden ausschließlich AKTIVE Einsätze der Org verglichen —
    abgeschlossene Einsätze scheiden dort aus (reduziert False-Positives bei
    wiederkehrenden Alarmen an derselben Adresse, z.B. BMA-Fehlalarme). Stufe 2
    (Einsatznummer) ist die einzige Ausnahme, siehe oben.
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

    if lis_operation_number:
        by_number = (
            db.query(Incident)
            .filter(
                Incident.primary_org_id == org_id,
                Incident.lis_operation_number == lis_operation_number,
            )
            .first()
        )
        if by_number:
            return by_number

    candidates = (
        db.query(Incident)
        .filter(
            Incident.primary_org_id == org_id,
            Incident.status == "active",
            Incident.alarm_type_code == alarm_type_code,
        )
        .all()
    )

    def _within_window(candidate: Incident) -> bool:
        if started_at is None or candidate.started_at is None:
            return True
        return abs(_as_aware(started_at) - _as_aware(candidate.started_at)) <= timedelta(hours=window_hours)

    target_address = normalize_address(street, city)
    target_house = _normalize_text(house_no) if house_no else ""
    if target_address:
        for candidate in candidates:
            if not _within_window(candidate):
                continue
            if normalize_address(candidate.address_street, candidate.address_city) != target_address:
                continue
            # Hausnummer nur als Ausschlusskriterium nutzen, wenn BEIDE Seiten
            # eine haben — fehlt sie auf einer Seite, bleibt es bei der reinen
            # Straße/Ort-Übereinstimmung (Rückwärtskompatibilität, house_no ist
            # bei den meisten Aufrufern gar nicht bekannt).
            cand_house = _normalize_text(candidate.address_no) if candidate.address_no else ""
            if target_house and cand_house and target_house != cand_house:
                continue
            return candidate

    own_street_norm = _normalize_text(street)
    own_report_norm = _normalize_text(report_text)
    for candidate in candidates:
        if not _within_window(candidate):
            continue
        cand_address_norm = normalize_address(candidate.address_street, candidate.address_city)
        # Zusätzliche Sicherheitsschranke (unabhängig vom Substring-Vergleich selbst):
        # der Fallback darf NUR greifen, wenn mindestens eine der beiden Seiten
        # tatsächlich keine strukturierte Adresse hat — haben beide eine (auch wenn
        # sie sich unterscheiden, z.B. zwei echte T9-Einsätze an einem Sturmtag),
        # bleibt es bei der bereits getroffenen (Nicht-)Entscheidung aus Schritt (2).
        if target_address and cand_address_norm:
            continue
        cand_street_norm = _normalize_text(candidate.address_street)
        cand_report_norm = _normalize_text(candidate.report_text)
        if (
            len(own_street_norm) >= _MIN_FALLBACK_STREET_LEN
            and cand_report_norm
            and own_street_norm in cand_report_norm
        ):
            return candidate
        if (
            len(cand_street_norm) >= _MIN_FALLBACK_STREET_LEN
            and own_report_norm
            and cand_street_norm in own_report_norm
        ):
            return candidate

    return None
