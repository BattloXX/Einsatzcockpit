"""CLI-Helfer für Admin-Aufgaben.

Verwendung:
  python -m app.cli create-admin --username admin --password geheim
  python -m app.cli create-api-key --label "Alarmierungssystem"
  python -m app.cli promote-to-system-admin --username admin
"""
import argparse
import sys

from app.core.security import generate_api_key, generate_sms_gateway_token, hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.user import ApiKey, Role, SmsGatewayToken, User, UserRole


def create_admin(username: str, password: str, display_name: str = "") -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"User '{username}' existiert bereits.")
            return
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name or username,
            active=True,
        )
        db.add(user)
        db.flush()
        # system_admin statt admin: dieser Account entsteht immer ohne org_id (org-los,
        # organisationsuebergreifend) -- mit der Rolle "admin" waere er in jeder org-gescopten
        # Einstellungsseite (z.B. Wetter) unsichtbar gefangen: is_sysadmin=False verhindert den
        # Org-Waehler, org_id=None verhindert den Fallback -- die Seite zeigt dann nur noch
        # "Keine Organisation ausgewaehlt" ohne jeden Ausweg.
        admin_role = db.query(Role).filter(Role.code == "system_admin").first()
        if admin_role:
            db.add(UserRole(user_id=user.id, role_id=admin_role.id))
        db.commit()
        print(f"✓ Admin '{username}' angelegt (ID {user.id}, Rolle system_admin).")
    finally:
        db.close()


def promote_to_system_admin(username: str) -> None:
    """Repariert Bestandskonten, die vor diesem Fix mit Rolle 'admin' + org_id=None
    angelegt wurden (z.B. der allererste Bootstrap-Admin) und dadurch in org-gescopten
    Einstellungsseiten (Wetter, ...) ohne Org-Auswahl feststecken."""
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"User '{username}' nicht gefunden.")
            return
        role = db.query(Role).filter(Role.code == "system_admin").first()
        if not role:
            print("Rolle 'system_admin' nicht gefunden (Rollen-Seed nicht gelaufen?).")
            return
        has_it = db.query(UserRole).filter(
            UserRole.user_id == user.id, UserRole.role_id == role.id
        ).first()
        if has_it:
            print(f"'{username}' hat bereits die Rolle system_admin.")
            return
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        print(f"✓ '{username}' ist jetzt system_admin.")
    finally:
        db.close()


def create_api_key(label: str) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        raw_key = generate_api_key()
        key = ApiKey(key_hash=hash_api_key(raw_key), label=label)
        db.add(key)
        db.commit()
        print(f"✓ API-Key angelegt: {raw_key}")
        print("   → Diesen Key sicher speichern, er wird nicht erneut angezeigt!")
    finally:
        db.close()


def create_sms_gateway_token(label: str, org_id: int) -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        raw_key = generate_sms_gateway_token()
        tok = SmsGatewayToken(token_hash=hash_api_key(raw_key), label=label, org_id=org_id)
        db.add(tok)
        db.commit()
        print(f"✓ SMS-Gateway-Token angelegt: {raw_key}")
        print("   → Diesen Token sicher speichern, er wird nicht erneut angezeigt!")
        print("   → Als GATEWAY_TOKEN in der .env des SMS-Gateway-Containers eintragen.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command")

    p_admin = sub.add_parser("create-admin")
    p_admin.add_argument("--username", required=True)
    p_admin.add_argument("--password", required=True)
    p_admin.add_argument("--display-name", default="")

    p_key = sub.add_parser("create-api-key")
    p_key.add_argument("--label", required=True)

    p_sms = sub.add_parser("create-sms-gateway-token")
    p_sms.add_argument("--label", required=True)
    p_sms.add_argument("--org-id", type=int, required=True, help="ID der Feuerwehr (fire_dept.id)")

    p_promote = sub.add_parser("promote-to-system-admin")
    p_promote.add_argument("--username", required=True)

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.username, args.password, args.display_name)
    elif args.command == "create-api-key":
        create_api_key(args.label)
    elif args.command == "create-sms-gateway-token":
        create_sms_gateway_token(args.label, args.org_id)
    elif args.command == "promote-to-system-admin":
        promote_to_system_admin(args.username)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
