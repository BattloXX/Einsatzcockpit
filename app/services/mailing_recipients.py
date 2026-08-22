"""Aufloesung statischer, automatischer und dynamischer Mailing-Empfaengerlisten."""
import json
from datetime import date, datetime, time

from sqlalchemy.orm import Session

from app.models.incident import IncidentVehicle
from app.models.mailing import MailingRecipientList
from app.models.master import Member, MemberQualification, MemberTagAssignment, Qualification


def _rows(members):
    seen = set()
    result = []
    for m in members:
        email = (m.email or "").strip().lower()
        if email and email not in seen:
            seen.add(email)
            result.append({"email": email, "display_name": m.full_name, "vorname": m.firstname, "nachname": m.lastname})
    return result


def resolve_all_members(db: Session, org_id: int):
    return _rows(
        db.query(Member)
        .filter(Member.org_id == org_id, Member.active.is_(True), Member.email.isnot(None))
        .order_by(Member.lastname, Member.firstname)
        .all()
    )


def resolve_incident_commanders(db: Session, org_id: int):
    members = (
        db.query(Member)
        .join(MemberQualification, MemberQualification.member_id == Member.id)
        .join(Qualification, Qualification.id == MemberQualification.qualification_id)
        .filter(
            Member.org_id == org_id,
            Member.active.is_(True),
            Member.email.isnot(None),
            Qualification.is_einsatzleiter.is_(True),
        )
        .order_by(Member.lastname, Member.firstname)
        .distinct()
        .all()
    )
    return _rows(members)


def resolve_incident_participants(db: Session, org_id: int, incident_id: int | None):
    if not incident_id:
        return []
    ids = []
    for row in (
        db.query(
            IncidentVehicle.commander_member_id, IncidentVehicle.fahrer_member_id, IncidentVehicle.fahrer2_member_id
        )
        .filter(IncidentVehicle.incident_id == incident_id)
        .all()
    ):
        ids.extend(x for x in row if x)
    if not ids:
        return []
    return _rows(
        db.query(Member)
        .filter(Member.org_id == org_id, Member.id.in_(set(ids)), Member.active.is_(True), Member.email.isnot(None))
        .all()
    )


def resolve_recipient_list(db: Session, recipient_list: MailingRecipientList, incident_id: int | None = None):
    if recipient_list.kind == "static":
        return [
            {"email": e.email, "display_name": e.display_name or "", "vorname": "", "nachname": ""}
            for e in recipient_list.entries
        ]
    if recipient_list.kind == "dynamic":
        return resolve_dynamic_list(db, recipient_list.org_id, recipient_list.filter_json)
    resolvers = {"all_members": resolve_all_members, "incident_commanders": resolve_incident_commanders}
    if recipient_list.auto_source == "incident_participants":
        return resolve_incident_participants(db, recipient_list.org_id, incident_id)
    fn = resolvers.get(recipient_list.auto_source or "")
    return fn(db, recipient_list.org_id) if fn else []

def resolve_dynamic_list(db: Session, org_id: int, filter_json: str | None):
    try: filters = json.loads(filter_json or "{}")
    except (TypeError, ValueError): filters = {}
    q = db.query(Member).filter(Member.org_id == org_id, Member.email.isnot(None))
    if isinstance(filters.get("active"), bool): q = q.filter(Member.active.is_(filters["active"]))
    if filters.get("member_since_after"): q = q.filter(Member.created_at >= datetime.combine(date.fromisoformat(filters["member_since_after"]), time.min))
    if filters.get("member_since_before"): q = q.filter(Member.created_at <= datetime.combine(date.fromisoformat(filters["member_since_before"]), time.max))
    if filters.get("qualification_codes"):
        q = q.join(MemberQualification).join(Qualification).filter(Qualification.code.in_(filters["qualification_codes"]))
    if filters.get("tag_ids"):
        q = q.join(MemberTagAssignment, MemberTagAssignment.member_id == Member.id).filter(MemberTagAssignment.tag_id.in_(filters["tag_ids"]))
    return _rows(q.distinct().order_by(Member.lastname, Member.firstname).all())

def resolve_recipient_list_multi(db: Session, campaign):
    lists = [x.recipient_list for x in campaign.recipient_list_links]
    if not lists and campaign.recipient_list is not None: lists = [campaign.recipient_list]
    result, seen = [], set()
    for lst in lists:
        for row in resolve_recipient_list(db, lst, campaign.source_incident_id):
            key = row["email"].strip().lower()
            if key not in seen: seen.add(key); result.append({**row, "email": key})
    return result
