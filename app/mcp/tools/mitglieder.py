"""Rein lesende MCP-Werkzeuge fuer Mitglieder der eigenen Organisation."""

from collections import defaultdict

from sqlalchemy import func, or_
from sqlalchemy.orm import lazyload

from app.core.audit import write_audit
from app.core.timezones import format_local_iso
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.major_incident import LageEinheit, LageEinheitLeader, MajorIncident, MajorIncidentStatus
from app.models.master import Member, MemberQualification, MemberTag, MemberTagAssignment, Qualification
from app.models.user import User
from app.services import mitglied_service

MAX_LIMIT = 200
MITGLIEDER_LESE_ROLLEN = ("admin", "fahrtenbuch_admin")
MITGLIEDER_SCHREIB_ROLLEN = ("admin",)


def _audit(context: MCPContext, aktion: str, mitglied_id: int, payload: dict[str, object]) -> None:
    write_audit(
        context.db,
        f"mcp.member.{aktion}",
        org_id=context.org_id,
        user_id=context.user.id,
        entity_type="member",
        entity_id=mitglied_id,
        payload=payload,
    )
    context.db.commit()


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


def gruppenkommandant_funktionen(context: MCPContext, member_id: int) -> list[dict[str, object]]:
    """Liest aktuelle Gruppenkommandant-Funktionen eines Mitglieds der eigenen Organisation."""
    funktionen = (
        context.db.query(LageEinheitLeader, LageEinheit, MajorIncident)
        .join(LageEinheit, LageEinheit.id == LageEinheitLeader.einheit_id)
        .join(MajorIncident, MajorIncident.id == LageEinheit.lage_id)
        .filter(
            LageEinheitLeader.member_id == member_id,
            LageEinheitLeader.end_at.is_(None),
            LageEinheit.leader_assignment_id == LageEinheitLeader.id,
            MajorIncident.org_id == context.org_id,
            MajorIncident.status == MajorIncidentStatus.active,
        )
        .order_by(MajorIncident.started_at.desc(), LageEinheit.label, LageEinheit.id)
        .all()
    )
    return [
        {"lage_id": lage.id, "lage_name": lage.name, "einheit_id": einheit.id, "einheit_label": einheit.label}
        for _leader, einheit, lage in funktionen
    ]


@register_tool(
    name="mitglieder_suchen",
    description=(
        "Sucht Mitglieder der eigenen Organisation nach Name, E-Mail, Telefon oder syBOS-ID. "
        "Erfordert die Rolle Admin oder Fahrtenbuch-Admin; das Werkzeug ist nur lesend und hat keine Seiteneffekte."
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
        "aktuellen Gruppenkommandant-Funktionen. Erfordert die Rolle Admin oder Fahrtenbuch-Admin; nur lesend, "
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

    return _mitglied_liste_payload(member, qualifikationen, tags, {}) | {
        "telefon": member.phone,
        "email": member.email,
        "sybos_id": member.sybos_id,
        "created_at": format_local_iso(member.created_at, context.user.org),
        "qualifikationen": qualifikationen[member.id],
        "benutzer": linked_user,
        "gruppenkommandant_funktionen": gruppenkommandant_funktionen(context, member.id),
    }


@register_tool(
    name="mitglieder_kataloge",
    description=(
        "Listet Qualifikationen, Tags und Mitgliederzahlen der eigenen Organisation. "
        "Erfordert die Rolle Admin oder Fahrtenbuch-Admin; das Werkzeug ist nur lesend und hat keine Seiteneffekte."
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


def _mitglied_schreib_payload(context: MCPContext, member: Member) -> dict[str, object]:
    qualifikationen, tags, benutzer = _zuordnungen(context, [member.id])
    return _mitglied_liste_payload(member, qualifikationen, tags, benutzer)


@register_tool(
    name="mitglied_anlegen",
    description=(
        "Legt ein Mitglied in der eigenen Organisation an und kann Qualifikationen sowie vorhandene Tags zuordnen. "
        "Erfordert admin; schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=MITGLIEDER_SCHREIB_ROLLEN,
)
async def mitglied_anlegen(
    context: MCPContext,
    vorname: str,
    nachname: str,
    telefon: str = "",
    email: str = "",
    sybos_id: str = "",
    qualifikationen: list[str] | None = None,
    tags: list[str] | None = None,
    dublette_ignorieren: bool = False,
) -> dict[str, object]:
    member = mitglied_service.mitglied_anlegen(
        context.db, context.org_id, vorname, nachname, telefon, email, sybos_id,
        dublette_ignorieren=dublette_ignorieren,
    )
    _, gesetzte_qualifikationen = mitglied_service.qualifikationen_setzen(
        context.db, context.org_id, member.id,
        [{"code": code, "gueltig_bis": None} for code in (qualifikationen or [])],
    )
    _, gesetzte_tags = mitglied_service.tags_setzen(context.db, context.org_id, member.id, tags or [])
    _audit(context, "created", member.id, {
        "vorname": member.firstname, "nachname": member.lastname, "telefon": member.phone,
        "email": member.email, "sybos_id": member.sybos_id,
        "qualifikationen": [eintrag["code"] for eintrag in gesetzte_qualifikationen],
        "tags": gesetzte_tags, "dublette_ignorieren": dublette_ignorieren,
    })
    return {"mitglied": _mitglied_schreib_payload(context, member)}


@register_tool(
    name="mitglied_aktualisieren",
    description=(
        "Aktualisiert erlaubte Stammdaten eines Mitglieds der eigenen Organisation. "
        "Erfordert admin; schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=MITGLIEDER_SCHREIB_ROLLEN,
)
async def mitglied_aktualisieren(
    context: MCPContext, mitglied_id: int, felder: dict[str, object]
) -> dict[str, object]:
    member, geaendert = mitglied_service.mitglied_aktualisieren(context.db, context.org_id, mitglied_id, felder)
    _audit(context, "updated", member.id, {"felder": geaendert})
    return {"mitglied": _mitglied_schreib_payload(context, member), "geaendert": geaendert}


async def _mitglied_aktiv_setzen(context: MCPContext, mitglied_id: int, aktiv: bool) -> dict[str, object]:
    member, status = mitglied_service.aktiv_setzen(context.db, context.org_id, mitglied_id, aktiv)
    _audit(context, "activated" if aktiv else "deactivated", member.id, {"aktiv": status})
    return {"mitglied": _mitglied_schreib_payload(context, member), "aktiv": status}


@register_tool(
    name="mitglied_aktivieren",
    description=(
        "Aktiviert ein Mitglied der eigenen Organisation idempotent. "
        "Erfordert admin; schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=MITGLIEDER_SCHREIB_ROLLEN,
)
async def mitglied_aktivieren(context: MCPContext, mitglied_id: int) -> dict[str, object]:
    return await _mitglied_aktiv_setzen(context, mitglied_id, True)


@register_tool(
    name="mitglied_deaktivieren",
    description=(
        "Deaktiviert ein Mitglied der eigenen Organisation idempotent. "
        "Erfordert admin; schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=MITGLIEDER_SCHREIB_ROLLEN,
)
async def mitglied_deaktivieren(context: MCPContext, mitglied_id: int) -> dict[str, object]:
    return await _mitglied_aktiv_setzen(context, mitglied_id, False)


@register_tool(
    name="mitglied_qualifikationen_setzen",
    description=(
        "Ersetzt alle Qualifikationen eines Mitglieds der eigenen Organisation. "
        "Erfordert admin; schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=MITGLIEDER_SCHREIB_ROLLEN,
)
async def mitglied_qualifikationen_setzen(
    context: MCPContext, mitglied_id: int, qualifikationen: list[dict[str, object]]
) -> dict[str, object]:
    member, gesetzt = mitglied_service.qualifikationen_setzen(
        context.db, context.org_id, mitglied_id, qualifikationen
    )
    payload = [
        {"code": eintrag["code"], "gueltig_bis": str(eintrag["gueltig_bis"] or "") or None}
        for eintrag in gesetzt
    ]
    _audit(context, "qualifications_set", member.id, {"qualifikationen": payload})
    return {"mitglied": _mitglied_schreib_payload(context, member), "qualifikationen": payload}


@register_tool(
    name="mitglied_tags_setzen",
    description=(
        "Ersetzt alle Tag-Zuordnungen eines Mitglieds der eigenen Organisation. "
        "Erfordert admin; schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=MITGLIEDER_SCHREIB_ROLLEN,
)
async def mitglied_tags_setzen(
    context: MCPContext, mitglied_id: int, tags: list[str]
) -> dict[str, object]:
    member, gesetzt = mitglied_service.tags_setzen(context.db, context.org_id, mitglied_id, tags)
    _audit(context, "tags_set", member.id, {"tags": gesetzt})
    return {"mitglied": _mitglied_schreib_payload(context, member), "tags": gesetzt}
