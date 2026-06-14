"""Ressourcen-Disposition für Großschadenslagen (SKKM).

Einzige Registry: LageEinheit.
Pool = sector_id IS NULL (Reserve im SKKM-Sinne).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.major_incident import (
    GslStaffAssignment,
    LageEinheit,
    LageEinheitLeader,
    LageJournalEntry,
    MajorIncident,
    Sector,
)

# ── Status-Konstanten ─────────────────────────────────────────────────────────

STATUS_ANGEFORDERT   = "angefordert"
STATUS_BEREITGESTELLT = "bereitgestellt"
STATUS_IM_EINSATZ    = "im_einsatz"
STATUS_ABGERUECKT    = "abgerueckt"

VALID_STATUSES = {
    STATUS_ANGEFORDERT, STATUS_BEREITGESTELLT, STATUS_IM_EINSATZ, STATUS_ABGERUECKT,
}

STATUS_LABEL = {
    STATUS_ANGEFORDERT:    "Angefordert",
    STATUS_BEREITGESTELLT: "Bereitgestellt",
    STATUS_IM_EINSATZ:     "Im Einsatz",
    STATUS_ABGERUECKT:     "Abgerückt",
}

# Welche Timestamp-Spalte wird bei Statuswechsel gesetzt?
_STATUS_TIMESTAMP = {
    STATUS_ANGEFORDERT:    "requested_at",
    STATUS_BEREITGESTELLT: "arrived_at",
    STATUS_IM_EINSATZ:     "committed_at",
    STATUS_ABGERUECKT:     "released_at",
}

RESOURCE_TYPE_LABEL = {
    "fahrzeug": "Fahrzeug",
    "extern":   "Externe Kräfte",
    "material": "Material/Gerät",
}

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

RESSOURCE_CATEGORIES = {"ressource", "ressource_fhr"}


def _journal(
    db: Session,
    lage_id: int,
    text: str,
    category: str = "ressource",
    author_name: str | None = None,
    user_id: int | None = None,
) -> None:
    db.add(LageJournalEntry(
        major_incident_id=lage_id,
        ts=datetime.now(UTC),
        category=category,
        text=text,
        author_name=author_name,
        user_id=user_id,
    ))


def _get_einheit(db: Session, einheit_id: int, lage_id: int) -> LageEinheit:
    e = db.get(LageEinheit, einheit_id)
    if not e or e.lage_id != lage_id:
        raise ValueError("Einheit nicht gefunden")
    return e


# ── Ressource anlegen ─────────────────────────────────────────────────────────

def add_resource(
    db: Session,
    lage_id: int,
    label: str,
    resource_type: str = "fahrzeug",
    *,
    vehicle_id: int | None = None,
    org_name: str | None = None,
    bos: str | None = None,
    qty: int | None = None,
    unit: str | None = None,
    is_from_org: bool = False,
    status: str = STATUS_BEREITGESTELLT,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    if resource_type not in RESOURCE_TYPE_LABEL:
        raise ValueError(f"Ungültiger resource_type: {resource_type}")
    if status not in VALID_STATUSES:
        raise ValueError(f"Ungültiger Status: {status}")

    now = datetime.now(UTC)
    e = LageEinheit(
        lage_id=lage_id,
        vehicle_id=vehicle_id,
        label=label.strip(),
        resource_type=resource_type,
        org_name=org_name,
        bos=bos,
        qty=qty,
        unit=unit,
        status=status,
        is_from_org=is_from_org,
        added_at=now,
    )
    if status == STATUS_ANGEFORDERT:
        e.requested_at = now
    else:
        e.arrived_at = now
    db.add(e)
    db.flush()

    _journal(db, lage_id,
             f"Ressource hinzugefügt: {label} [{RESOURCE_TYPE_LABEL.get(resource_type, resource_type)}]",
             category="ressource", author_name=author_name, user_id=user_id)
    return e


# ── Zuordnung Abschnitt / Einsatzstelle / Pool ────────────────────────────────

def assign_to_sector(
    db: Session,
    einheit_id: int,
    lage_id: int,
    sector_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    e = _get_einheit(db, einheit_id, lage_id)

    # Abschnitt gehört zur Lage prüfen
    sector = db.get(Sector, sector_id)
    if not sector or sector.major_incident_id != lage_id:
        raise ValueError("Abschnitt nicht gefunden")

    e.sector_id = sector_id
    e.incident_site_id = None
    if e.status == STATUS_BEREITGESTELLT:
        e.status = STATUS_IM_EINSATZ
        e.committed_at = datetime.now(UTC)

    _journal(db, lage_id,
             f'{e.label} -> Abschnitt "{sector.name}" zugeordnet',
             category="ressource", author_name=author_name, user_id=user_id)
    return e


def assign_to_site(
    db: Session,
    einheit_id: int,
    lage_id: int,
    site_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    from app.models.major_incident import IncidentSite
    e = _get_einheit(db, einheit_id, lage_id)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise ValueError("Einsatzstelle nicht gefunden")

    # Site impliziert den zugehörigen Sector
    e.sector_id = site.sector_id
    e.incident_site_id = site_id
    if e.status == STATUS_BEREITGESTELLT:
        e.status = STATUS_IM_EINSATZ
        e.committed_at = datetime.now(UTC)

    _journal(db, lage_id,
             f'{e.label} -> Einsatzstelle "{site.bezeichnung}" zugeordnet',
             category="ressource", author_name=author_name, user_id=user_id)
    return e


def move_to_pool(
    db: Session,
    einheit_id: int,
    lage_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    e = _get_einheit(db, einheit_id, lage_id)
    prev_sector = e.sector_id
    e.sector_id = None
    e.incident_site_id = None
    if e.status == STATUS_IM_EINSATZ:
        e.status = STATUS_BEREITGESTELLT

    _journal(db, lage_id,
             f"{e.label} → Pool/Reserve zurückgeführt"
             + (f" (war Abschnitt {prev_sector})" if prev_sector else ""),
             category="ressource", author_name=author_name, user_id=user_id)
    return e


# ── Status setzen ─────────────────────────────────────────────────────────────

def set_status(
    db: Session,
    einheit_id: int,
    lage_id: int,
    status: str,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    if status not in VALID_STATUSES:
        raise ValueError(f"Ungültiger Status: {status}")

    e = _get_einheit(db, einheit_id, lage_id)
    old_status = e.status
    e.status = status

    ts_field = _STATUS_TIMESTAMP.get(status)
    if ts_field:
        setattr(e, ts_field, datetime.now(UTC))

    # Auto-Abschnittszuweisung: wenn Einheit bereits einer Einsatzstelle mit Abschnitt zugeordnet ist
    if status == STATUS_IM_EINSATZ and not e.sector_id and e.incident_site_id:
        from app.models.major_incident import IncidentSite
        site = db.get(IncidentSite, e.incident_site_id)
        if site and site.sector_id:
            e.sector_id = site.sector_id

    _journal(db, lage_id,
             f"{e.label}: Status {STATUS_LABEL.get(old_status, old_status)}"
             f" → {STATUS_LABEL.get(status, status)}",
             category="ressource", author_name=author_name, user_id=user_id)
    return e


# ── Einheitsführer setzen / ablösen ──────────────────────────────────────────

def rotate_einheit_leadership(
    db: Session,
    einheit_id: int,
    lage_id: int,
    *,
    member_id: int | None = None,
    person_name: str | None = None,
    note: str | None = None,
    created_by: int | None = None,
    author_name: str | None = None,
) -> LageEinheitLeader:
    if not member_id and not person_name:
        raise ValueError("member_id oder person_name erforderlich")

    e = _get_einheit(db, einheit_id, lage_id)
    now = datetime.now(UTC)

    # Aktuellen Führer beenden
    if e.leader_assignment_id:
        old = db.get(LageEinheitLeader, e.leader_assignment_id)
        if old and old.end_at is None:
            old.end_at = now

    new_leader = LageEinheitLeader(
        einheit_id=einheit_id,
        member_id=member_id,
        person_name=person_name,
        start_at=now,
        predecessor_id=e.leader_assignment_id,
        note=note,
        created_by=created_by,
        created_at=now,
    )
    db.add(new_leader)
    db.flush()

    e.leader_assignment_id = new_leader.id
    e.commander_label = person_name or str(member_id)

    _journal(db, lage_id,
             f"{e.label}: Einheitsführer → {new_leader.display_name}",
             category="ressource_fhr", author_name=author_name, user_id=created_by)
    return new_leader


# ── EL / AbsLtr ablösen (GslStaffAssignment) ─────────────────────────────────

def rotate_gsl_leadership(
    db: Session,
    lage: MajorIncident,
    role_code: str,
    *,
    sector_id: int | None = None,
    member_id: int | None = None,
    person_name: str | None = None,
    org_id: int,
    note: str | None = None,
    created_by: int | None = None,
    author_name: str | None = None,
) -> GslStaffAssignment:
    """EL (role_code='EL') oder Abschnittsleiter (role_code='AbsLtr') ablösen/neu besetzen."""
    if not member_id and not person_name:
        raise ValueError("member_id oder person_name erforderlich")

    from app.models.major_incident import GslStaffRole
    role = db.query(GslStaffRole).filter(GslStaffRole.code == role_code).first()
    if not role:
        raise ValueError(f"Rolle {role_code} nicht gefunden")

    now = datetime.now(UTC)

    # Alten Eintrag beenden und Pointer ermitteln
    old_id: int | None = None
    if role_code == "EL":
        if lage.leader_assignment_id:
            old = db.get(GslStaffAssignment, lage.leader_assignment_id)
            if old and old.end_at is None:
                old.end_at = now
                old_id = old.id
    else:
        # Abschnittsleiter: suche aktiven Eintrag für diesen Sector
        old = (
            db.query(GslStaffAssignment)
            .filter(
                GslStaffAssignment.incident_id == lage.id,
                GslStaffAssignment.role_id == role.id,
                GslStaffAssignment.sector_id == sector_id,
                GslStaffAssignment.end_at.is_(None),
            )
            .first()
        )
        if old:
            old.end_at = now
            old_id = old.id

    new_asgn = GslStaffAssignment(
        incident_id=lage.id,
        role_id=role.id,
        org_id=org_id,
        member_id=member_id,
        person_name=person_name,
        is_lead=True,
        start_at=now,
        predecessor_id=old_id,
        sector_id=sector_id,
        note=note,
        created_by=created_by,
        created_at=now,
    )
    db.add(new_asgn)
    db.flush()

    if role_code == "EL":
        lage.leader_assignment_id = new_asgn.id
    elif sector_id:
        sector = db.get(Sector, sector_id)
        if sector:
            sector.leader_assignment_id = new_asgn.id
            sector.leader_label = person_name or ""

    target = "Einsatzleiter" if role_code == "EL" else f"Abschnittsleiter Abschnitt {sector_id}"
    _journal(db, lage.id,
             f"{target} → {person_name or str(member_id)}",
             category="ressource_fhr", author_name=author_name, user_id=created_by)
    return new_asgn


# ── Kräfteübersicht ───────────────────────────────────────────────────────────

def kraefteuebersicht(db: Session, lage: MajorIncident) -> dict[str, Any]:
    """Vollständiges S2-Lagebild: Pool + Abschnitte + Leiter aller Ebenen."""
    einheiten = lage.einheiten

    # Pool: sector_id IS NULL und nicht im aktiven Einsatz
    pool = [
        e for e in einheiten
        if e.sector_id is None and e.status not in (STATUS_IM_EINSATZ, STATUS_ABGERUECKT)
    ]
    # Im Einsatz ohne Abschnitt: sector_id IS NULL, aber bereits aktiv eingeteilt
    im_einsatz_no_sector = [
        e for e in einheiten
        if e.sector_id is None and e.status == STATUS_IM_EINSATZ
    ]
    abgerueckt = [e for e in einheiten if e.status == STATUS_ABGERUECKT]

    sectors = sorted(lage.sectors, key=lambda s: s.sort_order)
    sector_map: dict[int, list[LageEinheit]] = {s.id: [] for s in sectors}
    for e in einheiten:
        if e.sector_id and e.sector_id in sector_map and e.status != STATUS_ABGERUECKT:
            sector_map[e.sector_id].append(e)

    # Doppelverplanung: vehicle_id mehrfach active
    vid_list = [
        e.vehicle_id for e in einheiten
        if e.vehicle_id and e.status == STATUS_IM_EINSATZ
    ]
    conflict_vids = {v for v in vid_list if vid_list.count(v) > 1}

    # Aktueller EL
    el_asgn = db.get(GslStaffAssignment, lage.leader_assignment_id) if lage.leader_assignment_id else None

    sector_data = []
    for s in sectors:
        abs_asgn = db.get(GslStaffAssignment, s.leader_assignment_id) if s.leader_assignment_id else None
        sector_data.append({
            "sector": s,
            "einheiten": sector_map.get(s.id, []),
            "leader": abs_asgn,
        })

    return {
        "pool": pool,
        "im_einsatz_no_sector": im_einsatz_no_sector,
        "sectors": sector_data,
        "abgerueckt": abgerueckt,
        "conflict_vids": conflict_vids,
        "el_asgn": el_asgn,
        "total": len(einheiten),
        "reserve_count": len(pool),
        "im_einsatz_count": sum(1 for e in einheiten if e.status == STATUS_IM_EINSATZ),
    }
