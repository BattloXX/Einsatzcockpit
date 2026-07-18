"""CLI-Helfer für Admin-Aufgaben.

Verwendung:
  python -m app.cli create-admin --username admin --password geheim
  python -m app.cli create-api-key --label "Alarmierungssystem"
  python -m app.cli promote-to-system-admin --username admin
  python -m app.cli backup                 # Dumps beider DBs + Medien
  python -m app.cli restore-test           # Restore-Probe des neuesten Dumps
"""
import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

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


# ── Backup / Disaster-Recovery ────────────────────────────────────────────────

def _dump_db(cfg, ziel: Path, dump_bin: str) -> int:
    """Streamt einen mariadb-dump gzip-komprimiert nach ziel. Rueckgabe: Bytes."""
    from app.services import backup_service as bs
    argv, env_zusatz = bs.build_dump_argv(cfg, dump_bin)
    full_env = {**os.environ, **env_zusatz}
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)
    assert proc.stdout is not None
    try:
        with gzip.open(ziel, "wb") as gz:
            for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):  # type: ignore[union-attr]
                gz.write(chunk)
    finally:
        proc.stdout.close()
    err = proc.stderr.read() if proc.stderr else b""
    if proc.wait() != 0:
        ziel.unlink(missing_ok=True)
        raise RuntimeError(f"{dump_bin} fehlgeschlagen: {err.decode('utf-8', 'ignore')[:600]}")
    return ziel.stat().st_size


def _tar_medien(ziel: Path, medien_root: Path, backup_dir: Path) -> int:
    """Packt medien_root nach ziel (tar.gz), schliesst das Backup-Verzeichnis aus."""
    medien_root = medien_root.resolve()
    excl_prefix: str | None = None
    try:
        rel = backup_dir.resolve().relative_to(medien_root)
        excl_prefix = f"{medien_root.name}/{rel.as_posix()}"
    except ValueError:
        excl_prefix = None  # Backup-Dir liegt ausserhalb der Medien → nichts auszuschliessen

    def _filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if excl_prefix and (ti.name == excl_prefix or ti.name.startswith(excl_prefix + "/")):
            return None
        return ti

    with tarfile.open(ziel, "w:gz") as tar:
        tar.add(medien_root, arcname=medien_root.name, filter=_filter)
    return ziel.stat().st_size


def run_backup(out_dir: str = "", keep: int = -1, include_media: int = -1) -> int:
    """Sichert beide DBs (+ optional Medien) und raeumt alte Backups auf."""
    from app.config import settings
    from app.services import backup_service as bs

    out = Path(out_dir or settings.BACKUP_DIR)
    out.mkdir(parents=True, exist_ok=True)
    behalten = settings.BACKUP_KEEP_DAILY if keep < 0 else keep
    medien = settings.BACKUP_INCLUDE_MEDIA if include_media < 0 else bool(include_media)
    jetzt = datetime.now(UTC)

    dbs = [("einsatzleiter", settings.DATABASE_URL)]
    if (settings.WEATHER_DATABASE_URL or "").strip():
        dbs.append(("einsatzleiter_weather", settings.WEATHER_DATABASE_URL))

    fehler = 0
    erzeugt: list[Path] = []
    for label, url in dbs:
        ziel = out / bs.dump_dateiname(label, jetzt)
        try:
            groesse = _dump_db(bs.parse_database_url(url), ziel, settings.BACKUP_DUMP_BIN)
            print(f"✓ DB-Dump {label}: {ziel.name} ({groesse // 1024} KB)")
            erzeugt.append(ziel)
            bs.prune_backups(out, label, behalten)
        except Exception as exc:  # noqa: BLE001 — Sammelbetrieb, Rest weiterversuchen
            fehler += 1
            print(f"✗ DB-Dump {label} fehlgeschlagen: {exc}", file=sys.stderr)

    if medien:
        medien_root = Path(settings.MEDIA_STORAGE_DIR).parent  # = app_storage
        if medien_root.is_dir():
            ziel = out / bs.medien_dateiname(jetzt)
            try:
                groesse = _tar_medien(ziel, medien_root, out)
                print(f"✓ Medien-Archiv: {ziel.name} ({groesse // 1024} KB)")
                erzeugt.append(ziel)
                bs.prune_backups(out, "medien", behalten)
            except Exception as exc:  # noqa: BLE001
                fehler += 1
                print(f"✗ Medien-Archiv fehlgeschlagen: {exc}", file=sys.stderr)
        else:
            print(f"• Medien-Verzeichnis {medien_root} fehlt — uebersprungen")

    # Off-Site-Upload (3-2-1): frisch erzeugte Dumps an die Gegenstelle schieben.
    if settings.BACKUP_REMOTE_ENABLED and erzeugt:
        if _remote_upload(erzeugt, out) != 0:
            fehler += 1

    if fehler:
        print(f"Backup mit {fehler} Fehler(n) beendet.", file=sys.stderr)
    else:
        print(f"Backup vollstaendig nach {out}.")
    return 1 if fehler else 0


def _remote_upload(dateien: list[Path], backup_dir: Path) -> int:
    """Laedt die uebergebenen Backups an das konfigurierte Remote-Ziel. 0 = ok."""
    from app.services import remote_backup_service as rbs
    cfg = rbs.config_aus_settings()
    try:
        rbs.config_pruefen(cfg)
    except ValueError as exc:
        print(f"✗ Remote-Upload-Konfiguration ungueltig: {exc}", file=sys.stderr)
        return 1
    try:
        rbs.upload(cfg, dateien, backup_dir)
        print(f"✓ Off-Site-Upload ({len(dateien)} Datei(en)) → {cfg.ziel_beschreibung()}")
        return 0
    except Exception as exc:  # noqa: BLE001 — Off-Site-Fehler sichtbar machen
        print(f"✗ Off-Site-Upload fehlgeschlagen ({cfg.ziel_beschreibung()}): {exc}",
              file=sys.stderr)
        return 1


def backup_upload(alle: bool = False) -> int:
    """Laedt vorhandene Backups aus BACKUP_DIR an das Remote-Ziel (ohne neuen Dump).

    Standard: nur die neuesten Dumps je Typ. Mit alle=True das ganze Verzeichnis.
    Dient auch dem Testen der Remote-Konfiguration.
    """
    from app.config import settings
    from app.services import remote_backup_service as rbs

    if not settings.BACKUP_REMOTE_ENABLED:
        print("• BACKUP_REMOTE_ENABLED=false — Remote-Upload ist deaktiviert.", file=sys.stderr)
        return 1
    out = Path(settings.BACKUP_DIR)
    if not out.is_dir():
        print(f"✗ Backup-Verzeichnis {out} fehlt.", file=sys.stderr)
        return 1
    if alle or not settings.BACKUP_REMOTE_ONLY_LATEST:
        dateien = sorted(p for p in out.glob("*") if p.is_file())
    else:
        dateien = rbs.neueste_je_praefix(out, ("einsatzleiter", "einsatzleiter_weather", "medien"))
    if not dateien and rbs.config_aus_settings().protocol != "rclone":
        print(f"✗ Keine Backups in {out} gefunden.", file=sys.stderr)
        return 1
    return _remote_upload(dateien, out)


def restore_test(scratch_db: str = "") -> int:
    """Restore-Probe: neuesten Haupt-Dump in eine Wegwerf-DB einspielen + pruefen.

    Beweist bei jedem Lauf, dass der Dump tatsaechlich wiederherstellbar ist.
    Ruehrt die Produktions-DB nie an (harte Namens-Pruefung) und raeumt die
    Wegwerf-DB in jedem Fall wieder ab.
    """
    from app.config import settings
    from app.services import backup_service as bs

    cfg = bs.parse_database_url(settings.DATABASE_URL)
    scratch = scratch_db or settings.BACKUP_RESTORE_TEST_DB
    if not re.fullmatch(r"[A-Za-z0-9_]+", scratch):
        print(f"✗ Ungueltiger Restore-Test-DB-Name: {scratch!r}", file=sys.stderr)
        return 1
    if scratch == cfg.database:
        print("✗ Restore-Test-DB darf nicht die Produktions-DB sein!", file=sys.stderr)
        return 1

    dumps = sorted(Path(settings.BACKUP_DIR).glob("einsatzleiter-*.sql.gz"))
    if not dumps:
        print(f"✗ Kein Haupt-Dump in {settings.BACKUP_DIR} gefunden.", file=sys.stderr)
        return 1
    neuester = dumps[-1]
    client = settings.BACKUP_CLIENT_BIN
    full_env = {**os.environ, "MYSQL_PWD": cfg.password}

    def _admin(sql: str) -> None:
        argv, _ = bs.build_admin_argv(cfg, sql, client)
        r = subprocess.run(argv, env=full_env, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "ignore")[:600])

    print(f"Restore-Probe: {neuester.name} → DB {scratch}")
    _admin(f"DROP DATABASE IF EXISTS `{scratch}`; "
           f"CREATE DATABASE `{scratch}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    try:
        restore_argv, _ = bs.build_restore_argv(cfg, scratch, client)
        proc = subprocess.Popen(restore_argv, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)
        assert proc.stdin is not None
        with gzip.open(neuester, "rb") as gz:
            shutil.copyfileobj(gz, proc.stdin)
        proc.stdin.close()
        err = proc.stderr.read() if proc.stderr else b""
        if proc.wait() != 0:
            print(f"✗ Restore fehlgeschlagen: {err.decode('utf-8', 'ignore')[:600]}", file=sys.stderr)
            return 1

        for sql in bs.verify_sql():
            argv, _ = bs.build_query_argv(cfg, scratch, sql, client)
            r = subprocess.run(argv, env=full_env, capture_output=True)
            if r.returncode != 0:
                print(f"✗ Verifikation fehlgeschlagen ({sql}): "
                      f"{r.stderr.decode('utf-8', 'ignore')[:300]}", file=sys.stderr)
                return 1
        print("✓ Restore-Probe bestanden (Dump ist wiederherstellbar).")
        return 0
    finally:
        try:
            _admin(f"DROP DATABASE IF EXISTS `{scratch}`")
        except Exception as exc:  # noqa: BLE001
            print(f"! Warnung: Wegwerf-DB {scratch} konnte nicht entfernt werden: {exc}",
                  file=sys.stderr)


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

    p_backup = sub.add_parser("backup", help="Dumps beider DBs (+ Medien) mit Retention")
    p_backup.add_argument("--out", default="", help="Zielverzeichnis (Default: BACKUP_DIR)")
    p_backup.add_argument("--keep", type=int, default=-1, help="Anzahl behaltener Backups je Typ")
    p_backup.add_argument("--no-media", action="store_true", help="Medien-Archiv ueberspringen")

    p_rtest = sub.add_parser("restore-test", help="Restore-Probe des neuesten Haupt-Dumps")
    p_rtest.add_argument("--scratch-db", default="", help="Name der Wegwerf-DB (Default: BACKUP_RESTORE_TEST_DB)")

    p_upload = sub.add_parser("backup-upload", help="Vorhandene Backups an das Remote-Ziel laden")
    p_upload.add_argument("--all", action="store_true", help="Alle Backups statt nur der neuesten je Typ")

    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin(args.username, args.password, args.display_name)
    elif args.command == "create-api-key":
        create_api_key(args.label)
    elif args.command == "create-sms-gateway-token":
        create_sms_gateway_token(args.label, args.org_id)
    elif args.command == "promote-to-system-admin":
        promote_to_system_admin(args.username)
    elif args.command == "backup":
        sys.exit(run_backup(args.out, args.keep, 0 if args.no_media else -1))
    elif args.command == "restore-test":
        sys.exit(restore_test(args.scratch_db))
    elif args.command == "backup-upload":
        sys.exit(backup_upload(args.all))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
