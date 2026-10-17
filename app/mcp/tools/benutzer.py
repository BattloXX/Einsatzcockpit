"""Rein lesende MCP-Werkzeuge fuer Benutzer der eigenen Organisation."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import or_

from app.core.audit import write_audit
from app.core.permissions import ROLES, authorize_user_mutation, manageable_role_codes, permitted_submitted_roles
from app.core.timezones import format_local_iso
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.mcp.tools.mitglieder import gruppenkommandant_funktionen
from app.models.master import Member
from app.models.mcp import MCPOAuthToken
from app.models.user import Role, User, UserRole
from app.services import benutzer_service

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


def _audit(context: MCPContext, aktion: str, user: User, payload: dict[str, object] | None = None) -> None:
    write_audit(
        context.db, f"mcp.user.{aktion}", org_id=context.org_id, user_id=context.user.id,
        entity_type="user", entity_id=user.id, payload=payload,
    )
    context.db.commit()


def _ziel_benutzer(context: MCPContext, benutzer_id: int, aktion: str) -> User:
    user = context.db.query(User).filter(
        User.id == benutzer_id, User.org_id == context.org_id, User.is_device.is_(False)
    ).first()
    if user is None:
        raise ValueError("Benutzer nicht gefunden.")
    try:
        authorize_user_mutation(context.db, context.user, user, aktion)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc
    if user.is_system_admin:
        raise ValueError("Systemadministratoren koennen nicht bearbeitet werden.")
    return user


def _rollen_streng_setzen(context: MCPContext, user: User, rollen: list[str], aktion: str) -> list[str]:
    all_roles = {role.code for role in context.db.query(Role).all()}
    requested = set(rollen)
    invalid = requested - all_roles
    allowed = permitted_submitted_roles(context.db, context.user, rollen, action=aktion, target=user)
    denied = (requested - allowed) | invalid | ({"system_admin"} & requested)
    if denied:
        context.db.commit()
        raise ValueError(f"Nicht vergebbare Rollen: {', '.join(sorted(denied))}.")
    if user.id == context.user.id and "admin" not in requested:
        raise ValueError("Sie koennen sich die Administratorrolle nicht selbst entziehen.")
    return benutzer_service.update_user_roles(context.db, context.user, user, rollen, aktion)


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


@register_tool(
    name="benutzer_anlegen",
    description=(
        "Legt einen lokalen Benutzer der eigenen Organisation an. "
        "Erfordert admin, schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_anlegen(
    context: MCPContext, username: str, display_name: str, email: str = "", full_name: str = "", phone: str = "",
    rollen: list[str] | None = None, mitglied_id: int | None = None, willkommensmail: bool = True,
) -> dict[str, object]:
    requested_roles = rollen or []
    all_roles = {role.code for role in context.db.query(Role).all()}
    allowed = permitted_submitted_roles(context.db, context.user, requested_roles, action="create")
    denied = (set(requested_roles) - allowed) | (set(requested_roles) - all_roles)
    denied |= {"system_admin"} & set(requested_roles)
    if denied:
        context.db.commit()
        raise ValueError(f"Nicht vergebbare Rollen: {', '.join(sorted(denied))}.")
    user, password = benutzer_service.create_user(
        context.db, org_id=context.org_id, username=username, display_name=display_name, email=email,
        full_name=full_name, phone=phone, member_id=mitglied_id,
    )
    benutzer_service.update_user_roles(context.db, context.user, user, requested_roles, "create")
    _audit(context, "created", user, {
        "rollen": sorted(allowed), "mitglied_id": mitglied_id, "willkommensmail": willkommensmail,
    })
    # Der Benutzer ist bereits gespeichert; ein Mailfehler darf die Anlage nicht als gescheitert melden.
    mail_gesendet = False
    if willkommensmail and user.email:
        try:
            await benutzer_service.send_welcome_mail(user, password, context.db)
            mail_gesendet = True
        except Exception:
            mail_gesendet = False
    return {"benutzer": _listen_payload(context, user), "mail_gesendet": mail_gesendet}


@register_tool(
    name="benutzer_aktualisieren",
    description=(
        "Aktualisiert erlaubte Stammdaten eines Benutzers der eigenen Organisation. "
        "Erfordert admin, schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_aktualisieren(context: MCPContext, benutzer_id: int, felder: dict[str, object]) -> dict[str, object]:
    user = _ziel_benutzer(context, benutzer_id, "edit")
    allowed = {"display_name", "full_name", "email", "phone"}
    unknown = set(felder) - allowed
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}.")
    changes: dict[str, object] = {}
    for field in allowed:
        if field not in felder:
            continue
        value = felder[field]
        if not isinstance(value, str):
            raise ValueError(f"{field} muss ein Text sein.")
        new = value.strip() or None
        if field == "display_name":
            new = value.strip()
            if not new:
                raise ValueError("display_name darf nicht leer sein.")
        if field == "email" and new:
            new = new.lower()
            duplicate = context.db.query(User).filter(User.email == new, User.id != user.id).first()
            if duplicate:
                raise ValueError("E-Mail-Adresse ist bereits vergeben.")
        old = getattr(user, field)
        if old != new:
            setattr(user, field, new)
            changes[field] = {"alt": old, "neu": new}
    _audit(context, "updated", user, {"felder": changes})
    return {"benutzer": _listen_payload(context, user), "geaendert": changes}


@register_tool(
    name="benutzer_rollen_setzen",
    description=(
        "Ersetzt die Rollen eines Benutzers der eigenen Organisation. "
        "Erfordert admin, schreibt Daten und erzeugt einen Audit-Eintrag."
    ),
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_rollen_setzen(context: MCPContext, benutzer_id: int, rollen: list[str]) -> dict[str, object]:
    user = _ziel_benutzer(context, benutzer_id, "roles")
    final_roles = _rollen_streng_setzen(context, user, rollen, "roles")
    _audit(context, "roles_set", user, {"rollen": final_roles})
    return {"benutzer": _listen_payload(context, user), "rollen": final_roles}


async def _benutzer_aktiv_setzen(context: MCPContext, benutzer_id: int, aktiv: bool) -> dict[str, object]:
    action = "activate" if aktiv else "deactivate"
    user = _ziel_benutzer(context, benutzer_id, action)
    if not aktiv and user.id == context.user.id:
        raise ValueError("Sie koennen sich nicht selbst deaktivieren.")
    old = user.active
    user.active = aktiv
    if not aktiv:
        now = datetime.now(UTC).replace(tzinfo=None)
        for token in context.db.query(MCPOAuthToken).filter(
            MCPOAuthToken.user_id == user.id, MCPOAuthToken.revoked_at.is_(None)
        ):
            token.revoked_at = now
    _audit(context, "activated" if aktiv else "deactivated", user, {"aktiv": {"alt": old, "neu": aktiv}})
    return {"benutzer": _listen_payload(context, user), "aktiv": {"alt": old, "neu": aktiv}}


@register_tool(
    name="benutzer_aktivieren",
    description="Aktiviert einen Benutzer der eigenen Organisation; erfordert admin und erzeugt einen Audit-Eintrag.",
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_aktivieren(context: MCPContext, benutzer_id: int) -> dict[str, object]:
    return await _benutzer_aktiv_setzen(context, benutzer_id, True)


@register_tool(
    name="benutzer_deaktivieren",
    description="Deaktiviert einen Benutzer und widerruft MCP-Tokens; erfordert admin und erzeugt einen Audit-Eintrag.",
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_deaktivieren(context: MCPContext, benutzer_id: int) -> dict[str, object]:
    return await _benutzer_aktiv_setzen(context, benutzer_id, False)


@register_tool(
    name="benutzer_mitglied_verknuepfen",
    description="Verknuepft oder loest ein Mitglied; erfordert admin und erzeugt einen Audit-Eintrag.",
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_mitglied_verknuepfen(
    context: MCPContext, benutzer_id: int, mitglied_id: int | None = None
) -> dict[str, object]:
    user = _ziel_benutzer(context, benutzer_id, "member_link")
    error = benutzer_service.validate_member_link(context.db, mitglied_id, context.org_id, user.is_device, user.id)
    if error == "member_taken":
        raise ValueError("Mitglied ist bereits mit einem Benutzer verknuepft.")
    if error:
        raise ValueError("Mitglied nicht gefunden.")
    old = user.member_id
    user.member_id = mitglied_id
    _audit(context, "member_linked", user, {"mitglied_id": {"alt": old, "neu": mitglied_id}})
    return {"benutzer": _listen_payload(context, user), "mitglied_id": {"alt": old, "neu": mitglied_id}}


@register_tool(
    name="benutzer_passwort_reset_mail",
    description=(
        "Sendet eine Passwort-Reset-Mail an einen lokalen Benutzer; "
        "erfordert admin und erzeugt einen Audit-Eintrag."
    ),
    required_roles=BENUTZER_ROLLEN,
)
async def benutzer_passwort_reset_mail(context: MCPContext, benutzer_id: int) -> dict[str, object]:
    user = _ziel_benutzer(context, benutzer_id, "reset_mail")
    if user.auth_provider != "local":
        raise ValueError("Passwort-Reset ist nur fuer lokale Benutzer moeglich.")
    try:
        await benutzer_service.send_password_reset_mail(user, context.db)
    except ValueError:
        raise
    except Exception:
        raise ValueError("Passwort-Reset-Mail konnte nicht gesendet werden.") from None
    _audit(context, "password_reset_mail", user)
    return {"benutzer": _listen_payload(context, user), "mail_gesendet": True}
