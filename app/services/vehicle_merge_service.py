"""Fahrzeug-Dubletten zusammenfuehren und Referenzen tenant-sicher umhaengen."""
import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Query, Session

from app.core.audit import write_audit
from app.models.breathing import BreathingTroop
from app.models.fahrtenbuch import Fahrt
from app.models.foerderstrecke import FoerderPumpenTyp
from app.models.incident import Incident, IncidentColumn, IncidentVehicle, Message, RescuedPerson, Task
from app.models.major_incident import (
    IncidentSite,
    LageEinheit,
    MajorIncident,
    SiteResourceAssignment,
    VehiclePosition,
)
from app.models.master import AlarmDispatchVehicle, AlarmType, VehicleMaster
from app.models.teilnahme import Teilnahme
from app.models.user import DeviceToken, User


class VehicleMergeError(ValueError):
    """Die Fahrzeuge koennen nicht gefahrlos zusammengefuehrt werden."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def vehicle_reference_queries(
    db: Session, vehicle_id: int, org_id: int | None,
) -> tuple[tuple[str, Query, str], ...]:
    """Die zentrale Liste aller neun Referenzen auf VehicleMaster."""
    queries: list[tuple[str, Query, str, Any]] = [
        ("incident_vehicle", db.query(IncidentVehicle).join(Incident).filter(
            IncidentVehicle.vehicle_master_id == vehicle_id), "vehicle_master_id", Incident.primary_org_id),
        ("teilnahme", db.query(Teilnahme).filter(Teilnahme.fahrzeug_id == vehicle_id),
         "fahrzeug_id", Teilnahme.org_id),
        ("device_token", db.query(DeviceToken).join(User, DeviceToken.user_id == User.id).filter(
            DeviceToken.vehicle_master_id == vehicle_id), "vehicle_master_id", User.org_id),
        ("alarm_dispatch_vehicle", db.query(AlarmDispatchVehicle).join(AlarmType).filter(
            AlarmDispatchVehicle.vehicle_master_id == vehicle_id), "vehicle_master_id", AlarmType.org_id),
        ("foerder_pumpen_typ", db.query(FoerderPumpenTyp).filter(
            FoerderPumpenTyp.vehicle_id == vehicle_id), "vehicle_id", FoerderPumpenTyp.org_id),
        ("fahrt", db.query(Fahrt).filter(Fahrt.fahrzeug_id == vehicle_id), "fahrzeug_id", Fahrt.org_id),
        ("site_resource_assignment", db.query(SiteResourceAssignment).join(IncidentSite).join(
            MajorIncident).filter(SiteResourceAssignment.vehicle_id == vehicle_id),
         "vehicle_id", MajorIncident.org_id),
        ("lage_einheit", db.query(LageEinheit).join(MajorIncident).filter(
            LageEinheit.vehicle_id == vehicle_id), "vehicle_id", MajorIncident.org_id),
        ("vehicle_position", db.query(VehiclePosition).filter(
            VehiclePosition.vehicle_id == vehicle_id), "vehicle_id", VehiclePosition.org_id),
    ]
    return tuple(
        (table, query.filter(org_column == org_id) if org_id is not None else query, attribute)
        for table, query, attribute, org_column in queries
    )


def merge_incident_vehicle(
    db: Session, survivor: IncidentVehicle, duplicate: IncidentVehicle,
) -> None:
    for field_name in (
        "commander_member_id", "commander_name", "fahrer_member_id", "fahrer_name",
        "fahrer2_member_id", "fahrer2_name", "km_gefahren", "org_color_override",
        "lis_operation_unit_id",
    ):
        if getattr(survivor, field_name) is None:
            setattr(survivor, field_name, getattr(duplicate, field_name))
    if survivor.unit_status == "Einsatzbereit" and duplicate.unit_status != "Einsatzbereit":
        survivor.unit_status = duplicate.unit_status
        survivor.column_id = duplicate.column_id
    survivor.display_order = min(survivor.display_order, duplicate.display_order)
    survivor.created_at = min(survivor.created_at, duplicate.created_at)
    survivor.removed_at = (
        None if survivor.removed_at is None or duplicate.removed_at is None
        else max(survivor.removed_at, duplicate.removed_at)
    )
    for model in (Task, Message, RescuedPerson, BreathingTroop):
        # incident_id zusaetzlich zu vehicle_id filtern (auch wenn vehicle_id als FK auf
        # incident_vehicle.id ohnehin schon eindeutig ist) - CLAUDE.md verbietet ungefilterte
        # Bulk-Updates auf Tenant-Tabellen, also explizit auf den bereits tenant-geprueften
        # Einsatz von "duplicate" einschraenken statt sich nur auf die FK-Eindeutigkeit zu verlassen.
        db.query(model).filter(
            model.vehicle_id == duplicate.id, model.incident_id == duplicate.incident_id
        ).update({model.vehicle_id: survivor.id}, synchronize_session=False)
    for column in db.query(IncidentColumn).filter_by(incident_id=survivor.incident_id).all():
        if not column.card_order:
            continue
        try:
            order = json.loads(column.card_order)
        except (TypeError, ValueError):
            continue
        changed = False
        merged_order = []
        survivor_seen = False
        for card in order:
            if card.get("kind") == "vehicle" and card.get("id") == duplicate.id:
                card = {**card, "id": survivor.id}
                changed = True
            if card.get("kind") == "vehicle" and card.get("id") == survivor.id:
                if survivor_seen:
                    changed = True
                    continue
                survivor_seen = True
            merged_order.append(card)
        if changed:
            column.card_order = json.dumps(merged_order)
    db.delete(duplicate)
    db.flush()


def repoint_vehicle_references(
    db: Session, org_id: int, loser: VehicleMaster, winner: VehicleMaster, *,
    dedupe_assignments: bool,
) -> dict[str, int]:
    """Haengt die zentrale Referenzliste um; optional mit fachlicher Deduplizierung."""
    counts: dict[str, int] = {}
    for table, query, attribute in vehicle_reference_queries(db, loser.id, org_id):
        references = query.all()
        counts[table] = len(references)
        for reference in references:
            if table == "incident_vehicle":
                survivor = db.query(IncidentVehicle).filter(
                    IncidentVehicle.incident_id == reference.incident_id,
                    IncidentVehicle.vehicle_master_id == winner.id,
                    IncidentVehicle.id != reference.id,
                ).order_by(IncidentVehicle.removed_at.is_not(None), IncidentVehicle.id).first()
                if survivor is None:
                    reference.vehicle_master_id = winner.id
                else:
                    merge_incident_vehicle(db, survivor, reference)
                continue
            duplicate: Any = None
            if dedupe_assignments and table == "alarm_dispatch_vehicle":
                duplicate = db.query(AlarmDispatchVehicle).filter_by(
                    alarm_type_id=reference.alarm_type_id, vehicle_master_id=winner.id).first()
            elif dedupe_assignments and table == "lage_einheit":
                duplicate = db.query(LageEinheit).filter_by(
                    lage_id=reference.lage_id, vehicle_id=winner.id).first()
            elif dedupe_assignments and table == "site_resource_assignment":
                duplicate = db.query(SiteResourceAssignment).filter_by(
                    incident_site_id=reference.incident_site_id, vehicle_id=winner.id).first()
            if duplicate is not None:
                if table in ("lage_einheit", "site_resource_assignment"):
                    duplicate.label = winner.display_label
                db.delete(reference)
            else:
                setattr(reference, attribute, winner.id)
                if table in ("lage_einheit", "site_resource_assignment"):
                    reference.label = winner.display_label
                elif table == "vehicle_position":
                    reference.resource_label = winner.display_label
    db.flush()
    return counts


def merge_vehicles(
    db: Session, winner: VehicleMaster, loser: VehicleMaster, *, actor_user_id: int,
) -> None:
    """Fuehrt eine Fahrzeug-Dublette in das Gewinner-Fahrzeug zusammen."""
    if winner.id == loser.id:
        raise VehicleMergeError("same_vehicle", "Gewinner und Verlierer muessen verschieden sein.")
    if winner.dept_id != loser.dept_id:
        raise VehicleMergeError("different_org", "Fahrzeuge gehoeren nicht derselben Organisation.")
    if (db.query(Fahrt).filter(Fahrt.fahrzeug_id == winner.id).first() is not None
            and db.query(Fahrt).filter(Fahrt.fahrzeug_id == loser.id).first() is not None):
        raise VehicleMergeError("fahrtenbuch_conflict", "Beide Fahrzeuge besitzen Fahrtenbuch-Eintraege.")

    winner.km_aktuell = max(winner.km_aktuell or 0, loser.km_aktuell or 0)
    winner.betriebsstunden_aktuell = max(
        winner.betriebsstunden_aktuell or Decimal(0), loser.betriebsstunden_aktuell or Decimal(0))
    winner.seilwinde_bh_aktuell = max(
        winner.seilwinde_bh_aktuell or Decimal(0), loser.seilwinde_bh_aktuell or Decimal(0))
    for field_name in (
        "lis_reference_id", "kennzeichen", "taktisches_zeichen", "bos_override",
        "schaden_mail_override", "schaden_teams_webhook_override",
    ):
        if not getattr(winner, field_name):
            setattr(winner, field_name, getattr(loser, field_name))
    if not winner.qr_token and loser.qr_token:
        qr_token = loser.qr_token
        loser.qr_token = None
        db.flush()
        winner.qr_token = qr_token

    counts = repoint_vehicle_references(
        db, winner.dept_id, loser, winner, dedupe_assignments=True)
    remaining = {}
    for table, query, _attribute in vehicle_reference_queries(db, loser.id, None):
        count = query.count()
        if count:
            remaining[table] = count
    if remaining:
        raise VehicleMergeError(
            "references_remaining", f"Referenzen auf das alte Fahrzeug verbleiben: {remaining}")
    loser.deleted = True
    loser.active = False
    write_audit(
        db, "admin.vehicle.merged", user_id=actor_user_id,
        entity_type="vehicle_master", entity_id=loser.id,
        payload={"winner_id": winner.id, "loser_id": loser.id, "code": winner.code,
                 "reference_counts": counts},
    )
