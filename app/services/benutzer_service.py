"""Gemeinsame Benutzerverwaltung fuer Admin-UI und MCP."""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.audit import write_audit
from app.core.permissions import authorize_user_mutation, has_role, manageable_role_codes, permitted_submitted_roles
from app.core.security import hash_password
from app.models.master import Member
from app.models.password_reset import PasswordResetToken
from app.models.user import Role, User, UserRole


def validate_member_link(
    db: Session, member_id: int | None, target_org_id: int | None, target_is_device: bool,
    user_id: int | None = None,
) -> str | None:
    """Prueft eine Benutzer-Mitglied-Verknuepfung wie die Admin-Oberflaeche."""
    if member_id is None:
        return None
    if target_is_device:
        return "member_invalid"
    member = db.query(Member).execution_options(include_all_tenants=True).filter(Member.id == member_id).first()
    if member is None or member.org_id != target_org_id:
        return "member_invalid"
    existing = db.query(User).execution_options(include_all_tenants=True).filter(User.member_id == member_id)
    if user_id is not None:
        existing = existing.filter(User.id != user_id)
    return "member_taken" if existing.first() is not None else None


def mitglied_verknuepfung_setzen(
    db: Session, actor: User, org_id: int, benutzer_id: int, mitglied_id: int | None, *,
    dry_run: bool = False, manuell_bestaetigt: bool = False, audit_prefix: str = "admin",
    client_id: str | None = None,
) -> dict[str, object]:
    """Geschuetztes Verknuepfen/Trennen; Transaktionsabschluss beim Aufrufer.

    Bestehende Links werden nie ersetzt. Der bedingte UPDATE und der bestehende
    UNIQUE-Index verhindern auch bei konkurrierenden Aufrufen verlorene Zuordnungen.
    """
    if not has_role(actor, "admin") or actor.org_id != org_id:
        raise ValueError("Keine Berechtigung fuer die Benutzer-Mitgliederverwaltung.")
    user = db.query(User).filter(
        User.id == benutzer_id, User.org_id == org_id, User.is_device.is_(False),
    ).populate_existing().first()
    if user is None:
        raise ValueError("Benutzer nicht gefunden.")
    try:
        authorize_user_mutation(db, actor, user, "member_link")
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc
    if user.is_system_admin:
        raise ValueError("Systemadministratoren koennen nicht bearbeitet werden.")
    error = validate_member_link(db, mitglied_id, org_id, user.is_device, user.id)
    if error == "member_taken":
        raise ValueError("Mitglied ist bereits mit einem Benutzer verknuepft.")
    if error:
        raise ValueError("Mitglied nicht gefunden.")
    old = user.member_id
    if old is not None and mitglied_id is not None and old != mitglied_id:
        raise ValueError("Benutzer ist bereits verknuepft. Bestehende Verknuepfung zuerst explizit trennen.")
    changed = old != mitglied_id
    if changed and mitglied_id is not None and not manuell_bestaetigt:
        from app.services.benutzer_mitglied_service import abgleichen

        safe = abgleichen(db, org_id)["sicher"]
        if not any(row["benutzer_id"] == benutzer_id and row["mitglied_id"] == mitglied_id for row in safe):
            raise ValueError("Kein sicherer eindeutiger Treffer. Manuelle Bestaetigung erforderlich.")
    result = {
        "benutzer_id": benutzer_id, "mitglied_id": {"alt": old, "neu": mitglied_id},
        "dry_run": dry_run, "geaendert": changed and not dry_run,
        "wuerde_aendern": changed,
        "status": "simuliert" if dry_run and changed else "erfolgreich" if changed else "uebersprungen",
    }
    if not changed or dry_run:
        return result
    try:
        with db.begin_nested():
            updated = db.query(User).filter(
                User.id == benutzer_id, User.org_id == org_id, User.member_id == old,
            ).update({User.member_id: mitglied_id}, synchronize_session=False)
            if updated != 1:
                raise ValueError("Verknuepfung wurde zwischenzeitlich geaendert. Bitte erneut abgleichen.")
            write_audit(
                db, f"{audit_prefix}.user.member_{'unlinked' if mitglied_id is None else 'linked'}",
                org_id=org_id, user_id=actor.id, entity_type="user", entity_id=benutzer_id,
                payload={"mitglied_id": {"alt": old, "neu": mitglied_id},
                         "client_id": client_id, "manuell_bestaetigt": manuell_bestaetigt},
            )
            db.flush()
    except IntegrityError as exc:
        raise ValueError("Mitglied wurde zwischenzeitlich mit einem anderen Benutzer verknuepft.") from exc
    db.expire(user, ["member_id", "member"])
    return result


def update_user_roles(db: Session, actor: User, target: User, submitted_codes: list[str], action: str) -> list[str]:
    """Ersetzt verwaltbare Rollen und behaelt nicht verwaltbare Rollen bei."""
    allowed_submitted = permitted_submitted_roles(db, actor, submitted_codes, action=action, target=target)
    existing_codes = {role.code for role in target.roles}
    all_roles = {role.code: role for role in db.query(Role).all()}
    manageable = manageable_role_codes(actor, set(all_roles))
    final_codes = (existing_codes - manageable) | allowed_submitted
    db.query(UserRole).filter(UserRole.user_id == target.id).delete()
    for code in final_codes:
        db.add(UserRole(user_id=target.id, role_id=all_roles[code].id))
    return sorted(final_codes)


def create_user(
    db: Session, *, org_id: int, username: str, display_name: str, email: str = "", full_name: str = "",
    phone: str = "", member_id: int | None = None, password: str | None = None,
) -> tuple[User, str]:
    """Legt einen lokalen Benutzer mit zufaelligem Passwort an."""
    username = username.strip()
    if not username:
        raise ValueError("Benutzername darf nicht leer sein.")
    # Die UNIQUE-Constraints gelten systemweit, auch wenn die Session auf die
    # Organisation des Administrators eingeschraenkt ist. Nur die ID abfragen.
    identifiers = db.query(User.id).execution_options(include_all_tenants=True)
    if identifiers.filter(User.username == username).first():
        raise ValueError("Benutzername ist bereits vergeben.")
    email_clean = email.strip().lower() or None
    if email_clean and identifiers.filter(User.email == email_clean).first():
        raise ValueError("E-Mail-Adresse ist bereits vergeben.")
    member_error = validate_member_link(db, member_id, org_id, False)
    if member_error:
        raise ValueError("Mitglied ist ungueltig oder bereits verknuepft.")
    generated_password = password or secrets.token_urlsafe(16)
    user = User(
        username=username, display_name=display_name.strip() or username, full_name=full_name.strip() or None,
        email=email_clean, phone=phone.strip() or None, password_hash=hash_password(generated_password),
        org_id=org_id, member_id=member_id,
    )
    db.add(user)
    db.flush()
    return user, generated_password


def einladung_erstellen(db: Session, user: User, requesting_ip: str | None = None) -> str:
    """Erstellt eine neue Einladung und entwertet offene Tokens dieses Benutzers."""
    now = datetime.now(UTC)
    offene_tokens = db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).all()
    for token in offene_tokens:
        token.used_at = now
    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=now + timedelta(hours=settings.INVITE_TTL_HOURS),
        requesting_ip=requesting_ip, zweck="einladung",
    ))
    db.commit()
    base = settings.effective_public_base_url.rstrip("/")
    return f"{base}/passwort-zuruecksetzen?token={raw_token}"


async def einladung_senden(db: Session, user: User, requesting_ip: str | None = None) -> None:
    """Erstellt eine Einladung und versendet sie an den lokalen Benutzer."""
    if not user.email:
        raise ValueError("Benutzer hat keine E-Mail-Adresse.")
    einladung_url = einladung_erstellen(db, user, requesting_ip)
    from app.services.benutzer_mail_service import send_einladung

    await send_einladung(
        to=user.email, username=user.username, einladung_url=einladung_url,
        user_display_name=user.full_name or user.display_name or user.username,
        db=db, org_id=user.org_id,
    )


async def send_password_reset_mail(
    user: User, db: Session, requesting_ip: str | None = None, *, durch_admin: bool = False,
) -> None:
    """Entwertet alte Reset-Tokens, erstellt einen neuen und versendet die Reset-Mail.

    Token (und ein vom Aufrufer vorbereitetes Audit) werden vor dem Versand committet,
    damit der Link auch bei einem spaeten SMTP-Fehler gueltig bleibt (Verhalten der Admin-UI).
    """
    if not user.email:
        raise ValueError("Benutzer hat keine E-Mail-Adresse.")
    now = datetime.now(UTC)
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None),
    ).update({"used_at": now})
    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TTL_MIN), requesting_ip=requesting_ip,
    ))
    db.commit()
    from app.services.mail_service import send_password_reset

    reset_url = f"{settings.effective_public_base_url.rstrip('/')}/passwort-zuruecksetzen?token={raw_token}"
    await send_password_reset(
        to=user.email, reset_url=reset_url, user_display_name=user.full_name or user.display_name or user.username,
        durch_admin=durch_admin, db=db, org_id=user.org_id,
    )
