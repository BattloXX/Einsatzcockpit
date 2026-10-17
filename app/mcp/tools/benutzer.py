"""Rein lesende MCP-Werkzeuge fuer Benutzer der eigenen Organisation."""

from sqlalchemy import or_

from app.core.permissions import ROLES, manageable_role_codes
from app.core.timezones import format_local_iso
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.mcp.tools.mitglieder import gruppenkommandant_funktionen
from app.models.master import Member
from app.models.user import Role, User, UserRole

MAX_LIMIT = 200
BENUTZER_ROLLEN = ("admin",)


def _limit_und_seite_pruefen(limit: int, seite: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit muss zwischen 1 und 200 liegen.")
    if seite < 1:
        raise ValueError("seite muss mindestens 1 sein.")


def _rollen(user: User) -> list[str]:
    return sorted(user.role_codes)


def _mitglied_payload(context: MCPContext, member_id: int | None) -> dict[str, object] | None:
    if member_id is None:
        return None
    member = context.db.query(Member).filter(Member.id == member_id, Member.org_id == context.org_id).first()
    return {"id": member.id, "name": f"{member.firstname} {member.lastname}"} if member else None


def _listen_payload(context: MCPContext, user: User) -> dict[str, object]:
    return {
        "id": user.id, "username": user.username, "display_name": user.display_name, "aktiv": user.active,
        "rollen": _rollen(user), "auth_provider": user.auth_provider,
        "last_login_at": format_local_iso(user.last_login_at, context.user.org),
        "mitglied": _mitglied_payload(context, user.member_id),
    }


@register_tool(
    name="benutzer_suchen",
    description=(
        "Sucht nicht technische Benutzer der eigenen Organisation nach Benutzername, Anzeigename, Name oder E-Mail. "
        "Erfordert admin; nur lesend, keine Seiteneffekte."
    ),
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_suchen(context: MCPContext, q: str = "", aktiv: bool | None = None, rolle: str = "",
                          mit_mitglied: bool | None = None, limit: int = 50, seite: int = 1) -> dict[str, object]:
    _limit_und_seite_pruefen(limit, seite)
    query = context.db.query(User).filter(User.org_id == context.org_id, User.is_device.is_(False))
    if q.strip():
        suchtext = f"%{q.strip()}%"
        query = query.filter(or_(User.username.ilike(suchtext), User.display_name.ilike(suchtext),
                                 User.full_name.ilike(suchtext), User.email.ilike(suchtext)))
    if aktiv is not None:
        query = query.filter(User.active == aktiv)
    if rolle.strip():
        query = query.filter(User.user_roles.any(UserRole.role.has(code=rolle.strip())))
    if mit_mitglied is not None:
        query = query.filter(User.member_id.is_not(None) if mit_mitglied else User.member_id.is_(None))
    gesamt = query.count()
    users = query.order_by(User.username, User.id).offset((seite - 1) * limit).limit(limit).all()
    return {"benutzer": [_listen_payload(context, user) for user in users], "gesamt": gesamt,
            "seite": seite, "truncated": seite * limit < gesamt}


@register_tool(
    name="benutzer_lesen",
    description=(
        "Liest einen nicht technischen Benutzer der eigenen Organisation mit Kontaktdaten und verknuepftem Mitglied. "
        "Erfordert admin; nur lesend, keine Seiteneffekte."
    ),
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_lesen(context: MCPContext, benutzer_id: int) -> dict[str, object]:
    user = context.db.query(User).filter(
        User.id == benutzer_id, User.org_id == context.org_id, User.is_device.is_(False)
    ).first()
    if user is None:
        raise ValueError("Benutzer nicht gefunden.")
    mitglied = _mitglied_payload(context, user.member_id)
    funktionen = gruppenkommandant_funktionen(context, user.member_id) if user.member_id is not None else []
    return _listen_payload(context, user) | {
        "full_name": user.full_name, "email": user.email, "phone": user.phone,
        "created_at": format_local_iso(user.created_at, context.user.org),
        "gesperrt_bis": format_local_iso(user.locked_until, context.user.org),
        "system_admin": user.is_system_admin,
        "mitglied": mitglied | {"gruppenkommandant_funktionen": funktionen} if mitglied else None,
        "gruppenkommandant_funktionen": funktionen,
    }


@register_tool(
    name="rollen_katalog",
    description=(
        "Listet Rollen mit Stufe und ob sie vom aktuellen Benutzer vergeben werden duerfen. "
        "Erfordert admin; nur lesend, keine Seiteneffekte."
    ),
    required_roles=BENUTZER_ROLLEN,
)
async def rollen_katalog(context: MCPContext) -> dict[str, object]:
    vergebbar = manageable_role_codes(context.user, set(ROLES))
    labels = {role.code: role.label for role in context.db.query(Role).filter(Role.code.in_(ROLES)).all()}
    return {"rollen": [
        {"code": code, "label": labels.get(code, code), "stufe": stufe, "vergebbar": code in vergebbar}
        for code, stufe in sorted(ROLES.items(), key=lambda item: (-item[1], item[0]))
    ]}
