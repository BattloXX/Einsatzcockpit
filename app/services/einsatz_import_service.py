"""Import historischer Einsätze aus den zwei Leitstellen-Excel-Exporten."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.timezones import org_tz
from app.models.incident import (
    FIXED_COLUMN_TITLES,
    FIXED_COLUMNS,
    Incident,
    IncidentColumn,
    IncidentVehicle,
)
from app.models.master import AlarmType, FireDept, VehicleMaster
from app.services.vehicle_merge_service import merge_incident_vehicle, repoint_vehicle_references

_LEITSTELLEN_ID_ALIASES = {"leitstellen nr.", "leitstellen nummer", "leitstellennummer"}
_DATE_FORMAT = "%d.%m.%Y %H:%M:%S"
_STATUS_TIMESTAMP_FIELDS = {
    "started_at",
    "taken_over_at",
    "departed_at",
    "on_scene_at",
    "ready_again_at",
    "closed_at",
}


class EinsatzImportError(ValueError):
    """Eine hochgeladene Datei ist kein unterstützter Leitstellen-Export."""


@dataclass
class ParsedImport:
    format: str
    groups: dict[str, list[dict[str, Any]]]


@dataclass
class ImportResult:
    format: str
    imported: int = 0
    updated: int = 0
    unchanged: int = 0
    vehicles_attached: int = 0
    vehicles_adhoc: int = 0
    row_errors: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _normalise_header(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _date(value: Any, org: FireDept | None = None) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.strptime(
            str(value).strip(), _DATE_FORMAT
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=org_tz(org))
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_einsatz_excel(raw: bytes) -> ParsedImport:
    """Erkennt Format A/B und gruppiert alle Daten nach Leitstellen-Nummer."""
    try:
        import openpyxl
    except (ImportError, OSError) as exc:
        logging.getLogger(__name__).exception("openpyxl kann nicht geladen werden")
        raise EinsatzImportError(
            "Excel-Funktion nicht verfügbar: Abhängigkeit openpyxl fehlt oder ist defekt. "
            "Bitte pip install -e . ausführen."
        ) from exc
    if not raw:
        raise EinsatzImportError("Die Datei ist leer.")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        header_values = next(rows)
    except (Exception, StopIteration) as exc:
        raise EinsatzImportError("Die Excel-Datei konnte nicht gelesen werden.") from exc

    headers = [_normalise_header(value) for value in header_values]
    header_set = set(headers)
    if "fahrzeug" in header_set and "funkrufname" in header_set:
        format_name = "A"
    elif "einsatzstichwort tatsächlich" in header_set or "koordinaten n" in header_set:
        format_name = "B"
    else:
        raise EinsatzImportError("Unbekanntes Format: Fahrzeug- oder Detail-Liste erwartet.")

    lis_index = next((i for i, name in enumerate(headers) if name in _LEITSTELLEN_ID_ALIASES), None)
    if lis_index is None:
        raise EinsatzImportError("Die Spalte Leitstellen-Nr. fehlt.")

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if lis_index >= len(row) or not _text(row[lis_index]):
            continue
        values: dict[str, Any] = {}
        for i, name in enumerate(headers):
            # Detail-Listen enthalten "Erst-Alarmierung" doppelt; der erste
            # Wert ist das Datum, der spätere kann den Stichwort-Code enthalten.
            if name:
                values.setdefault(name, row[i] if i < len(row) else None)
        operation_number = _text(row[lis_index])
        assert operation_number is not None
        groups.setdefault(operation_number, []).append(values)
    return ParsedImport(format=format_name, groups=groups)


def _create_fixed_columns(db: Session, incident: Incident) -> IncidentColumn:
    active = None
    for order, code in enumerate(FIXED_COLUMNS):
        column = IncidentColumn(
            incident=incident,
            code=code,
            title=FIXED_COLUMN_TITLES[code],
            column_kind="vehicles" if code == "active" else code,
            is_fixed=True,
            display_order=order,
        )
        db.add(column)
        if code == "active":
            active = column
    assert active is not None
    db.flush()
    return active


def _value(row: dict[str, Any], *names: str) -> Any:
    return next((row[name] for name in names if row.get(name) not in (None, "")), None)


def _reason(row: dict[str, Any], format_name: str) -> str | None:
    if format_name == "A":
        parts = [_text(row.get("objekt")), _text(row.get("kategorie"))]
    else:
        parts = [_text(row.get("einsatzablauf")), _text(row.get("tätigkeit/bemerkung")),
                 _text(row.get("bemerkung"))]
    return "\n".join(dict.fromkeys(part for part in parts if part)) or None


def _row_fields(row: dict[str, Any], format_name: str, org: FireDept | None) -> dict[str, Any]:
    closed = _date(row.get("ende"), org) if format_name == "B" else None
    closed = closed or _date(row.get("wieder einsatzbereit"), org)
    return {
        "started_at": _date(row.get("erst-alarmierung"), org),
        "taken_over_at": _date(row.get("übernommen"), org),
        "departed_at": _date(row.get("ausfahrt"), org),
        "on_scene_at": _date(row.get("am einsatzort"), org),
        "ready_again_at": _date(row.get("wieder einsatzbereit"), org),
        "closed_at": closed,
        "address_street": _text(row.get("straße/objekt")),
        "address_no": _text(row.get("hausnummer")),
        "address_city": _text(row.get("einsatzort")),
        "lat": _float(row.get("koordinaten n")),
        "lng": _float(row.get("koordinaten e")),
        "report_text": _text(row.get("alarmtext")) if format_name == "B" else _text(row.get("objekt")),
        "reason": _reason(row, format_name),
    }


def _alarm_code(db: Session, org_id: int, row: dict[str, Any]) -> tuple[str, bool]:
    requested = _text(row.get("einsatzstichwort tatsächlich"))
    if requested:
        alarm_type = db.query(AlarmType).filter(
            AlarmType.org_id == org_id, func.lower(AlarmType.code) == requested.lower()
        ).first()
        if alarm_type:
            return alarm_type.code, True
    return "T1", False


def _active_column(db: Session, incident: Incident) -> IncidentColumn:
    column = db.query(IncidentColumn).filter_by(incident_id=incident.id, code="active").first()
    return column or _create_fixed_columns(db, incident)


def _vehicle(db: Session, org_id: int, row: dict[str, Any]) -> tuple[VehicleMaster | None, bool]:
    code, name = _text(row.get("fahrzeug")), _text(row.get("funkrufname"))
    matches = []
    if code or name:
        criteria = []
        if code:
            criteria.append(func.lower(VehicleMaster.code) == code.lower())
        if name:
            criteria.append(func.lower(VehicleMaster.name) == name.lower())
        matches = db.query(VehicleMaster).filter(
            VehicleMaster.dept_id == org_id,
            VehicleMaster.deleted == False,  # noqa: E712
            or_(*criteria),
        ).all()
    real = next((item for item in matches if not item.is_adhoc), None)
    if real is not None:
        for adhoc in (item for item in matches if item.is_adhoc):
            _replace_adhoc_vehicle(db, org_id, adhoc, real)
        return real, False
    if matches:
        return matches[0], False
    if not code and not name:
        return None, False
    vehicle = VehicleMaster(
        dept_id=org_id, code=(code or name or "Fahrzeug")[:30], name=(name or code or "Fahrzeug")[:150],
        is_first_train=False, active=True, display_order=999, is_adhoc=True,
    )
    db.add(vehicle)
    db.flush()
    return vehicle, True


def _replace_adhoc_vehicle(
    db: Session, org_id: int, adhoc: VehicleMaster, real: VehicleMaster
) -> None:
    """Haengt alle tenant-geprueften Referenzen um und entfernt das Adhoc-Fahrzeug."""
    repoint_vehicle_references(db, org_id, adhoc, real, dedupe_assignments=False)
    db.delete(adhoc)
    db.flush()


def _deduplicate_incident_vehicle(
    db: Session, incident_id: int, vehicle_master_id: int
) -> IncidentVehicle | None:
    assignments = db.query(IncidentVehicle).filter_by(
        incident_id=incident_id, vehicle_master_id=vehicle_master_id
    ).order_by(IncidentVehicle.removed_at.is_not(None), IncidentVehicle.id).all()
    if not assignments:
        return None
    survivor = assignments[0]
    for duplicate in assignments[1:]:
        merge_incident_vehicle(db, survivor, duplicate)
    return survivor


def import_einsaetze(db: Session, parsed: ParsedImport, org_id: int, user_id: int) -> ImportResult:
    """Importiert gruppenweise mit Savepoints; bestehende Werte bleiben erhalten."""
    result = ImportResult(format=parsed.format)
    org = db.query(FireDept).filter(FireDept.id == org_id).one_or_none()
    for operation_number, rows in parsed.groups.items():
        counters_before = (
            result.imported, result.updated, result.unchanged,
            result.vehicles_attached, result.vehicles_adhoc, len(result.warnings),
        )
        savepoint = db.begin_nested()
        try:
            incident = db.query(Incident).filter_by(
                primary_org_id=org_id, lis_operation_number=operation_number
            ).first()
            created = incident is None
            row = rows[0]
            fields = _row_fields(row, parsed.format, org)
            alarm_code, alarm_recognised = _alarm_code(db, org_id, row)
            if created:
                incident = Incident(
                    primary_org_id=org_id, lis_operation_number=operation_number,
                    alarm_type_code=alarm_code, status="closed", is_exercise=False,
                    **{key: value for key, value in fields.items() if value is not None},
                )
                db.add(incident)
                try:
                    db.flush()
                except IntegrityError:
                    savepoint.rollback()
                    incident = db.query(Incident).filter_by(
                        primary_org_id=org_id, lis_operation_number=operation_number
                    ).first()
                    if incident is None:
                        raise
                    savepoint = db.begin_nested()
                    created = False
                if created:
                    _create_fixed_columns(db, incident)
                    result.imported += 1
            if incident is None:
                raise RuntimeError("Einsatz konnte nicht angelegt oder geladen werden")
            changed = False
            if not created:
                for key, value in fields.items():
                    if value is None:
                        continue
                    current = getattr(incident, key)
                    if key in _STATUS_TIMESTAMP_FIELDS and current != value:
                        setattr(incident, key, value)
                        changed = True
                    elif key not in _STATUS_TIMESTAMP_FIELDS and current in (None, ""):
                        setattr(incident, key, value)
                        changed = True
                # T1 ist beim Format-A-Import der dokumentierte Platzhalter und darf
                # durch den späteren Detail-Export präzisiert werden.
                if parsed.format == "B" and alarm_recognised and incident.alarm_type_code.upper() == "T1" \
                        and alarm_code.upper() != "T1":
                    incident.alarm_type_code = alarm_code
                    changed = True
                if changed:
                    result.updated += 1
                else:
                    result.unchanged += 1

            if not alarm_recognised:
                result.warnings.append(f"{operation_number}: Stichwort nicht erkannt – bitte prüfen")

            if parsed.format == "A":
                active_column = _active_column(db, incident)
                for vehicle_row in rows:
                    vehicle, adhoc = _vehicle(db, org_id, vehicle_row)
                    if vehicle is None:
                        continue
                    exists = _deduplicate_incident_vehicle(db, incident.id, vehicle.id)
                    if exists:
                        continue
                    db.add(IncidentVehicle(
                        incident_id=incident.id, column_id=active_column.id,
                        vehicle_master_id=vehicle.id, unit_status="Einsatzbereit",
                    ))
                    result.vehicles_attached += 1
                    result.vehicles_adhoc += int(adhoc)
            db.flush()
            savepoint.commit()
        except Exception as exc:
            savepoint.rollback()
            (result.imported, result.updated, result.unchanged,
             result.vehicles_attached, result.vehicles_adhoc, warning_count) = counters_before
            del result.warnings[warning_count:]
            result.row_errors += 1
            result.errors.append(f"{operation_number}: {exc}")

    write_audit(
        db, "admin.incident.imported", org_id=org_id, user_id=user_id,
        payload={
            "format": result.format, "imported": result.imported, "updated": result.updated,
            "unchanged": result.unchanged, "vehicles_attached": result.vehicles_attached,
            "vehicles_adhoc": result.vehicles_adhoc, "row_errors": result.row_errors,
        },
    )
    db.commit()
    return result
