"""SKKM-Stabsbesetzungs-Service: Besetzung, Ablöse, Soll-Check."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.major_incident import GslStaffAssignment, GslStaffRole

# Pflicht-Codes gemäß SKKM-Richtlinie (Default, überschreibbar via OrgSettings)
DEFAULT_REQUIRED = {"EL", "LDS", "S1", "S2", "S3", "S4", "S5", "S6"}


# ── Rollen-Abfragen ────────────────────────────────────────────────────────────

def get_roles(db: Session, org_id: int) -> list[GslStaffRole]:
    """Gibt systemweite + org-eigene Rollen sortiert zurück."""
    return (
        db.query(GslStaffRole)
        .filter((GslStaffRole.org_id.is_(None)) | (GslStaffRole.org_id == org_id))
        .order_by(GslStaffRole.sort_order)
        .all()
    )


def get_required_codes(db, org_id: int) -> set[str]:
    """Konfigurierbare Pflicht-Rollen je Org (Fallback: DEFAULT_REQUIRED)."""
    from app.models.master import OrgSettings
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    if settings and settings.gsl_required_staff_roles:
        try:
            codes = json.loads(settings.gsl_required_staff_roles)
            if isinstance(codes, list):
                return set(codes)
        except (json.JSONDecodeError, TypeError):
            pass
    return DEFAULT_REQUIRED


# ── Aktive Besetzungen ─────────────────────────────────────────────────────────

def active_assignments(db: Session, incident_id: int) -> list[GslStaffAssignment]:
    return (
        db.query(GslStaffAssignment)
        .filter(
            GslStaffAssignment.incident_id == incident_id,
            GslStaffAssignment.end_at.is_(None),
        )
        .all()
    )


# ── Besetzung anlegen ──────────────────────────────────────────────────────────

def assign(
    db: Session,
    incident_id: int,
    org_id: int,
    role_id: int,
    person_name: str | None,
    member_id: int | None,
    is_lead: bool,
    start_at: datetime,
    note: str | None,
    created_by: int | None,
) -> GslStaffAssignment:
    """Legt eine neue Besetzung an. Wirft ValueError bei Constraint-Verletzungen."""
    role = db.get(GslStaffRole, role_id)
    if not role:
        raise ValueError("Unbekannte Rolle")
    if not person_name and not member_id:
        raise ValueError("person_name oder member_id muss gesetzt sein")

    # Doppelbesetzungs-Schutz: max. eine is_lead=True pro Rolle (außer allows_multiple)
    if is_lead and not role.allows_multiple:
        conflict = (
            db.query(GslStaffAssignment)
            .filter(
                GslStaffAssignment.incident_id == incident_id,
                GslStaffAssignment.role_id == role_id,
                GslStaffAssignment.is_lead.is_(True),
                GslStaffAssignment.end_at.is_(None),
            )
            .first()
        )
        if conflict:
            raise ValueError(
                f"Rolle {role.code} ist bereits als Leiter besetzt. Bitte zuerst ablösen."
            )

    asgn = GslStaffAssignment(
        incident_id=incident_id,
        role_id=role_id,
        org_id=org_id,
        member_id=member_id,
        person_name=person_name,
        is_lead=is_lead,
        start_at=start_at,
        note=note,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    db.add(asgn)
    db.flush()
    return asgn


# ── Ablöse-Workflow ────────────────────────────────────────────────────────────

def replace_assignment(
    db: Session,
    old_id: int,
    incident_id: int,
    org_id: int,
    person_name: str | None,
    member_id: int | None,
    at: datetime,
    note: str | None,
    created_by: int | None,
) -> GslStaffAssignment:
    """Atomar: altes end_at setzen, neue Besetzung mit predecessor anlegen."""
    old = db.get(GslStaffAssignment, old_id)
    if not old or old.incident_id != incident_id:
        raise ValueError("Besetzung nicht gefunden")
    if old.end_at is not None:
        raise ValueError("Besetzung ist bereits beendet")
    if not person_name and not member_id:
        raise ValueError("person_name oder member_id muss gesetzt sein")

    old.end_at = at

    new = GslStaffAssignment(
        incident_id=incident_id,
        role_id=old.role_id,
        org_id=org_id,
        member_id=member_id,
        person_name=person_name,
        is_lead=old.is_lead,
        start_at=at,
        predecessor_id=old_id,
        note=note,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    db.add(new)
    db.flush()
    return new


def end_assignment(db: Session, asgn_id: int, incident_id: int, at: datetime) -> GslStaffAssignment:
    """Beendet eine Besetzung ohne Nachfolger."""
    asgn = db.get(GslStaffAssignment, asgn_id)
    if not asgn or asgn.incident_id != incident_id:
        raise ValueError("Besetzung nicht gefunden")
    if asgn.end_at is not None:
        raise ValueError("Besetzung ist bereits beendet")
    asgn.end_at = at
    db.flush()
    return asgn


# ── Soll-Check (Ampel-Logik) ───────────────────────────────────────────────────

SOLL_RED    = "red"
SOLL_YELLOW = "yellow"
SOLL_GREEN  = "green"


def soll_check(
    db: Session,
    incident_id: int,
    org_id: int,
) -> dict:
    """
    Gibt zurück:
      overall: red|yellow|green
      roles: [{role, status, warnings, assignment|None}]
    """
    roles = get_roles(db, org_id)
    required_codes = get_required_codes(db, org_id)
    now = datetime.now(UTC)

    active = active_assignments(db, incident_id)
    by_role: dict[int, list[GslStaffAssignment]] = {}
    for a in active:
        by_role.setdefault(a.role_id, []).append(a)

    # Alle aktiven Personen zählen (für Mehrfach-Besetzungs-Warnung)
    person_role_count: dict[str, int] = {}
    for a in active:
        key = str(a.member_id) if a.member_id else (a.person_name or "")
        if key:
            person_role_count[key] = person_role_count.get(key, 0) + 1

    result_roles = []
    overall = SOLL_GREEN

    for role in roles:
        assignments = by_role.get(role.id, [])
        lead_assignments = [a for a in assignments if a.is_lead]
        warnings = []
        status = SOLL_GREEN

        if role.code in required_codes:
            if not lead_assignments:
                status = SOLL_RED
                warnings.append(f"{role.code} unbesetzt")
            else:
                # Ablöse-Warnung: Besetzung > 8h
                for a in lead_assignments:
                    duration = now - a.start_at.replace(tzinfo=UTC) if a.start_at.tzinfo is None else now - a.start_at
                    if duration > timedelta(hours=8):
                        if status != SOLL_RED:
                            status = SOLL_YELLOW
                        h = int(duration.total_seconds() // 3600)
                        warnings.append(f"Ablöse planen ({h}h im Dienst)")
        else:
            if lead_assignments:
                status = SOLL_GREEN

        # Mehrfachbesetzungs-Warnung (selbe Person in mehreren Funktionen)
        for a in lead_assignments:
            key = str(a.member_id) if a.member_id else (a.person_name or "")
            if key and person_role_count.get(key, 0) > 1:
                if status == SOLL_GREEN:
                    status = SOLL_YELLOW
                warnings.append(f"{a.display_name} besetzt mehrere Funktionen gleichzeitig")

        if status == SOLL_RED and overall != SOLL_RED:
            overall = SOLL_RED
        elif status == SOLL_YELLOW and overall == SOLL_GREEN:
            overall = SOLL_YELLOW

        result_roles.append({
            "role": role,
            "status": status,
            "warnings": warnings,
            "lead_assignments": lead_assignments,
            "all_assignments": assignments,
        })

    return {"overall": overall, "roles": result_roles}
