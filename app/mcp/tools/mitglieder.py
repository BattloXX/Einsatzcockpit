"""Rein lesende MCP-Werkzeuge fuer Mitglieder der eigenen Organisation."""

from collections import defaultdict

from sqlalchemy import func, or_
from sqlalchemy.orm import lazyload

from app.core.timezones import format_local_iso
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.major_incident import LageEinheit, LageEinheitLeader, MajorIncident, MajorIncidentStatus
from app.models.master import Member, MemberQualification, MemberTag, MemberTagAssignment, Qualification
from app.models.user import User

MAX_LIMIT = 200
MITGLIEDER_LESE_ROLLEN = ("admin", "fahrtenbuch_admin")


def _limit_und_seite_pruefen(limit: int, seite: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit muss zwischen 1 und 200 liegen.")
    if seite < 1:
        raise ValueError("seite muss mindestens 1 sein.")


def _zuordnungen(
    context: MCPContext, member_ids: list[int]
) -> tuple[dict[int, list[dict[str, object]]], dict[int, list[str]], dict[int, dict[str, object]]]:
    """Laedt Qualifikationen, Tags und Benutzer jeweils gesammelt."""
    qualifikationen: dict[int, list[dict[str, object]]] = defaultdict(list)
    tags: dict[int, list[str]] = defaultdict(list)
    benutzer: dict[int, dict[str, object]] = {}
    if not member_ids:
        return qualifikationen, tags, benutzer

    for zuordnung, qualifikation in (
        context.db.query(MemberQualification, Qualification)
        .join(Qualification, Qualification.id == MemberQualification.qualification_id)
        .filter(MemberQualification.member_id.in_(member_ids))
        .order_by(MemberQualification.member_id, Qualification.code)
        .all()
    ):
        qualifikationen[zuordnung.member_id].append({
            "code": qualifikation.code,
            "valid_until": zuordnung.valid_until.isoformat() if zuordnung.valid_until else None,
        })

    for tag_zuordnung, tag in (
        context.db.query(MemberTagAssignment, MemberTag)
        .join(MemberTag, MemberTag.id == MemberTagAssignment.tag_id)
        .filter(MemberTagAssignment.member_id.in_(member_ids), MemberTag.org_id == context.org_id)
        .order_by(MemberTagAssignment.member_id, MemberTag.name)
        .all()
    ):
        tags[tag_zuordnung.member_id].append(tag.name)

    for user in (
        context.db.query(User)
        .filter(User.org_id == context.org_id, User.member_id.in_(member_ids))
        .order_by(User.id)
        .all()
    ):
        if user.member_id is not None:
            benutzer[user.member_id] = {"id": user.id, "username": user.username}
    return qualifikationen, tags, benutzer


def _mitglied_liste_payload(
    member: Member,
    qualifikationen: dict[int, list[dict[str, object]]],
    tags: dict[int, list[str]],
    benutzer: dict[int, dict[str, object]],
) -> dict[str, object]:
    return {
        "id": member.id,
        "vorname": member.firstname,
        "nachname": member.lastname,
        "aktiv": member.active,
        "ist_gruppenkommandant": member.ist_gruppenkommandant,
        "qualifikationen": [eintrag["code"] for eintrag in qualifikationen[member.id]],
        "tags": tags[member.id],
        "benutzer": benutzer.get(member.id),
    }


@register_tool(
    name="mitglieder_suchen",
    description=(
        "Sucht Mitglieder der eigenen Organisation nach Name, E-Mail, Telefon oder syBOS-ID. "
        "Erfordert admin oder fahrtenbuch_admin; das Werkzeug ist nur lesend und hat keine Seiteneffekte."
    ),
    required_roles=MITGLIEDER_LESE_ROLLEN,
)
async def mitglieder_suchen(
    context: MCPContext,
    q: str = "",
    aktiv: bool | None = True,
    qualifikation: str = "",
    tag: str = "",
    gruppenkommandant: bool | None = None,
    mit_benutzer: bool | None = None,
    limit: int = 50,
    seite: int = 1,
) -> dict[str, object]:
    _limit_und_seite_pruefen(limit, seite)
    query = context.db.query(Member).options(lazyload(Member.qualifications)).filter(Member.org_id == context.org_id)
    if q.strip():
        suchtext = f"%{q.strip()}%"
        query = query.filter(or_(
            Member.firstname.ilike(suchtext),
            Member.lastname.ilike(suchtext),
            Member.email.ilike(suchtext),
            Member.phone.ilike(suchtext),
            Member.sybos_id.ilike(suchtext),
        ))
    if aktiv is not None:
        query = query.filter(Member.active == aktiv)
    if gruppenkommandant is not None:
        query = query.filter(Member.ist_gruppenkommandant == gruppenkommandant)
    if qualifikation.strip():
        query = query.join(MemberQualification).join(Qualification).filter(Qualification.code == qualifikation.strip())
    if tag.strip():
        query = (
            query.join(MemberTagAssignment, MemberTagAssignment.member_id == Member.id)
            .join(MemberTag, MemberTag.id == MemberTagAssignment.tag_id)
            .filter(MemberTag.org_id == context.org_id, MemberTag.name == tag.strip())
        )
    if mit_benutzer is not None:
        benutzer_vorhanden = (
            context.db.query(User.id)
            .filter(User.org_id == context.org_id, User.member_id == Member.id)
            .correlate(Member)
            .exists()
        )
        query = query.filter(benutzer_vorhanden if mit_benutzer else ~benutzer_vorhanden)

    query = query.distinct()
    gesamt = query.count()
    members = (
        query.order_by(Member.lastname, Member.firstname, Member.id)
        .offset((seite - 1) * limit)
        .limit(limit)
        .all()
    )
    member_ids = [member.id for member in members]
    qualifikationen, tags, benutzer = _zuordnungen(context, member_ids)
    return {
        "mitglieder": [_mitglied_liste_payload(member, qualifikationen, tags, benutzer) for member in members],
        "gesamt": gesamt,
        "seite": seite,
        "truncated": seite * limit < gesamt,
    }


@register_tool(
    name="mitglied_lesen",
    description=(
        "Liest ein Mitglied der eigenen Organisation mit Kontaktdaten, Qualifikationen, Tags, Benutzer und "
        "aktuellen Gruppenkommandant-Funktionen. Erfordert admin oder fahrtenbuch_admin; nur lesend, "
        "keine Seiteneffekte."
    ),
    required_roles=MITGLIEDER_LESE_ROLLEN,
)
async def mitglied_lesen(context: MCPContext, mitglied_id: int) -> dict[str, object]:
    member = (
        context.db.query(Member)
        .options(lazyload(Member.qualifications))
        .filter(Member.id == mitglied_id, Member.org_id == context.org_id)
        .first()
    )
    if member is None:
        raise ValueError("Mitglied nicht gefunden.")

    qualifikationen, tags, benutzer = _zuordnungen(context, [member.id])
    linked_user = benutzer.get(member.id)
    if linked_user:
        user = (
            context.db.query(User)
            .filter(User.id == linked_user["id"], User.org_id == context.org_id, User.member_id == member.id)
            .first()
        )
        if user:
            linked_user = {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "aktiv": user.active,
            }

    funktionen = (
        context.db.query(LageEinheitLeader, LageEinheit, MajorIncident)
        .join(LageEinheit, LageEinheit.id == LageEinheitLeader.einheit_id)
        .join(MajorIncident, MajorIncident.id == LageEinheit.lage_id)
        .filter(
            LageEinheitLeader.member_id == member.id,
            LageEinheitLeader.end_at.is_(None),
            LageEinheit.leader_assignment_id == LageEinheitLeader.id,
            MajorIncident.org_id == context.org_id,
            MajorIncident.status == MajorIncidentStatus.active,
        )
        .order_by(MajorIncident.started_at.desc(), LageEinheit.label, LageEinheit.id)
        .all()
    )
    return _mitglied_liste_payload(member, qualifikationen, tags, {}) | {
        "telefon": member.phone,
        "email": member.email,
        "sybos_id": member.sybos_id,
        "created_at": format_local_iso(member.created_at, context.user.org),
        "qualifikationen": qualifikationen[member.id],
        "benutzer": linked_user,
        "gruppenkommandant_funktionen": [
            {
                "lage_id": lage.id,
                "lage_name": lage.name,
                "einheit_id": einheit.id,
                "einheit_label": einheit.label,
            }
            for _leader, einheit, lage in funktionen
        ],
    }


@register_tool(
    name="mitglieder_kataloge",
    description=(
        "Listet Qualifikationen, Tags und Mitgliederzahlen der eigenen Organisation. "
        "Erfordert admin oder fahrtenbuch_admin; das Werkzeug ist nur lesend und hat keine Seiteneffekte."
    ),
    required_roles=MITGLIEDER_LESE_ROLLEN,
)
async def mitglieder_kataloge(context: MCPContext) -> dict[str, object]:
    qualifikationen = context.db.query(Qualification).order_by(Qualification.code).all()
    tags = (
        context.db.query(MemberTag)
        .filter(MemberTag.org_id == context.org_id)
        .order_by(MemberTag.name, MemberTag.id)
        .all()
    )
    aktiv, inaktiv = (
        context.db.query(
            func.count(Member.id).filter(Member.active.is_(True)),
            func.count(Member.id).filter(Member.active.is_(False)),
        )
        .filter(Member.org_id == context.org_id)
        .one()
    )
    return {
        "qualifikationen": [
            {
                "code": qualification.code,
                "label": qualification.label,
                "is_gruppenkommandant": qualification.is_gruppenkommandant,
                "is_einsatzleiter": qualification.is_einsatzleiter,
                "maschinist_stufe": qualification.maschinist_stufe,
            }
            for qualification in qualifikationen
        ],
        "tags": [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in tags],
        "anzahl_aktive_mitglieder": aktiv,
        "anzahl_inaktive_mitglieder": inaktiv,
    }
