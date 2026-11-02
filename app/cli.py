"""CLI-Helfer für Admin-Aufgaben.

Verwendung:
  python -m app.cli create-admin --username admin --password geheim
  python -m app.cli create-api-key --label "Alarmierungssystem"
  python -m app.cli promote-to-system-admin --username admin
  python -m app.cli backup                 # Dumps beider DBs + Medien
  python -m app.cli restore-test           # Restore-Probe des neuesten Dumps
"""
import argparse
import fcntl
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Timer

from app.core.security import generate_api_key, generate_sms_gateway_token, hash_api_key, hash_password
from app.core.tenant import set_tenant_context
from app.db import SessionLocal  # Nutzt die zentrale Engine samt Snapshot-Connect-Listener.
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

@dataclass
class BackupResult:
    """Ergebnis eines run_backup()-Laufs, inkl. UI-tauglicher Fehlerdetails."""
    returncode: int
    created: list[Path]
    failures: list[str] = field(default_factory=list)


def _sanitisierter_dump_fehler(label: str, dump_bin: str, exc: Exception) -> str:
    """Bildet aus einer _dump_db-Exception eine fuer die Web-UI sichere Meldung.

    Wichtig: parse_database_url() haengt bei ValueError die rohe DATABASE_URL
    (inkl. Passwort im Klartext) an die Exception-Message an — die darf nie
    unveraendert an den Browser durchgereicht werden. Fuer alle Faelle ausser
    RuntimeError (das ist mariadb-dumps eigenes stderr, Passwort kommt dort
    nie vor, siehe MYSQL_PWD-Env in build_dump_argv) daher feste, generische
    Meldungen statt str(exc).
    """
    if isinstance(exc, FileNotFoundError):
        return f"{label}: {dump_bin} nicht gefunden – Paket mariadb-client installieren"
    if isinstance(exc, ValueError):
        return f"{label}: ungueltige Datenbank-URL (Host/Datenbank fehlt)"
    if isinstance(exc, RuntimeError):
        return f"{label}: {exc}"
    return f"{label}: unerwarteter Fehler beim Backup"


def _dump_db(cfg, ziel: Path, dump_bin: str) -> int:
    """Streamt einen portablen mariadb-dump gzip-komprimiert nach ziel.

    MariaDB kann in Bestandsdatenbanken identische, rein numerische FK-Namen
    exportieren. Diese waeren in einer frisch angelegten DB nicht eindeutig;
    daher werden sie beim Schreiben entfernt, damit MariaDB beim Restore selbst
    eindeutige Namen vergibt.
    """
    from app.services import backup_service as bs
    argv, env_zusatz = bs.build_dump_argv(cfg, dump_bin)
    full_env = {**os.environ, **env_zusatz}
    from app.config import settings
    partial = ziel.with_name(ziel.name + ".partial")
    abgelaufen = Event()
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=stderr, env=full_env)
        assert proc.stdout is not None

        def abbrechen() -> None:
            abgelaufen.set()
            proc.kill()

        timer = Timer(settings.BACKUP_DUMP_TIMEOUT_S, abbrechen)
        timer.start()
        try:
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as roh:
                with gzip.GzipFile(fileobj=roh, mode="wb") as gz:
                    for zeile in iter(proc.stdout.readline, b""):
                        gz.write(bs.normalisiere_dump_fk_namen(zeile))
                roh.flush()
                os.fsync(roh.fileno())
            rc = proc.wait()
            if abgelaufen.is_set():
                raise RuntimeError("Zeitlimit fuer DB-Dump ueberschritten")
            if rc != 0:
                stderr.seek(0)
                detail = stderr.read().decode("utf-8", "ignore")[-600:]
                raise RuntimeError(f"{dump_bin} fehlgeschlagen (Exit-Code {rc}): {detail}")
            os.replace(partial, ziel)
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            partial.unlink(missing_ok=True)
    return ziel.stat().st_size


def _tar_medien(ziel: Path, medien_root: Path, backup_dir: Path) -> int:
    """Archiviert mit Gesamtzeitlimit und veroeffentlicht nur fertige Archive."""
    from app.config import settings
    medien_root = medien_root.resolve()
    argv = ["tar", "-czf", "-", "-C", str(medien_root.parent)]
    if backup_dir.resolve().is_relative_to(medien_root):
        rel = backup_dir.resolve().relative_to(medien_root)
        argv += [f"--exclude={medien_root.name}/{rel.as_posix()}"]
    argv += [medien_root.name]
    partial = ziel.with_name(ziel.name + ".partial")
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as roh:
            try:
                result = subprocess.run(argv, stdout=roh, stderr=subprocess.PIPE,
                                        timeout=settings.BACKUP_DUMP_TIMEOUT_S)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Zeitlimit fuer Medien-Archiv ueberschritten") from exc
            # tar-Exit 1 = "Datei beim Lesen geaendert": bei laufendem Betrieb normal.
            if result.returncode not in (0, 1):
                raise RuntimeError("Medien-Archiv fehlgeschlagen")
            roh.flush()
            os.fsync(roh.fileno())
        os.replace(partial, ziel)
    finally:
        partial.unlink(missing_ok=True)
    return ziel.stat().st_size


def _freier_backup_pfad(out: Path, name: str, endung: str) -> Path:
    """Fortlaufender Suffix erhaelt auch bei identischer Sekunde die Sortierung."""
    stem = name.removesuffix(endung)
    muster = re.compile(re.escape(stem) + r"(?:_(\d{6,}))?" + re.escape(endung) + r"(?:\.partial)?$")
    nummern = [int(match[1] or 0) for p in out.glob(stem + "*")
               if (match := muster.fullmatch(p.name))]
    if not nummern:
        return out / name
    return out / f"{stem}_{max(nummern) + 1:06d}{endung}"


def run_backup(out_dir: str = "", keep: int = -1, include_media: int = -1,
               praefix: str = "") -> BackupResult:
    """Serialisiert alle Backup-Einstiegspunkte ueber eine Dateisperre."""
    from app.config import settings
    if praefix not in {"", "pre-update-"}:
        raise ValueError("Unbekannter Backup-Praefix")
    out = Path(out_dir or settings.BACKUP_DIR)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    fd = os.open(out / ".backup.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return BackupResult(1, [], ["Backup laeuft bereits"])
        try:
            for partial in out.glob("*.partial"):
                if partial.is_file() and datetime.now(UTC).timestamp() - partial.stat().st_mtime > 86400:
                    partial.unlink()
            return _run_backup_locked(str(out), keep, include_media, praefix)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _run_backup_locked(out_dir: str, keep: int, include_media: int, praefix: str) -> BackupResult:
    """Sichert beide DBs (+ optional Medien) und raeumt alte Backups auf."""
    from app.config import settings
    from app.services import backup_service as bs

    out = Path(out_dir or settings.BACKUP_DIR)
    out.mkdir(parents=True, exist_ok=True)
    retention_days = bs.DEFAULT_RETENTION_DAYS
    max_count = settings.BACKUP_KEEP_DAILY
    try:
        from app.db import SessionLocal
        policy_db = SessionLocal()
        try:
            retention_days, max_count = bs.lade_dump_policy(policy_db)
        finally:
            policy_db.close()
    except Exception:  # noqa: BLE001 — Backup muss bei nicht erreichbarer App-DB laufen
        bs.logger.warning(
            "DB-Dump-Retention konnte nicht geladen werden; verwende Fallbacks",
            exc_info=True,
        )
    db_max_count = (settings.UPDATE_BACKUP_KEEP if praefix else max_count) if keep < 0 else keep
    medien_behalten = settings.BACKUP_KEEP_DAILY if keep < 0 else keep
    medien = settings.BACKUP_INCLUDE_MEDIA if include_media < 0 else bool(include_media)
    jetzt = datetime.now(UTC)

    dbs = [("einsatzleiter", settings.DATABASE_URL)]
    if (settings.WEATHER_DATABASE_URL or "").strip():
        dbs.append(("einsatzleiter_weather", settings.WEATHER_DATABASE_URL))

    fehler = 0
    erzeugt: list[Path] = []
    failures: list[str] = []
    for label, url in dbs:
        label = praefix + label
        name = bs.dump_dateiname(label, jetzt)
        ziel = _freier_backup_pfad(out, name, ".sql.gz")
        try:
            vorherige = sorted(
                (p for p in out.iterdir() if p.is_file()
                 and (parsed := bs.parse_dump_dateiname(p.name))
                 and parsed[0].removeprefix("pre-update-") == label.removeprefix("pre-update-")),
                key=lambda p: p.name.removeprefix("pre-update-"),
            )
            if vorherige and shutil.disk_usage(out).free < vorherige[-1].stat().st_size * 1.5:
                raise RuntimeError("Zu wenig freier Speicherplatz fuer DB-Dump (letzter Dump × 1,5)")
            groesse = _dump_db(bs.parse_database_url(url), ziel, settings.BACKUP_DUMP_BIN)
            print(f"✓ DB-Dump {label}: {ziel.name} ({groesse // 1024} KB)")
            ziel.chmod(0o600)
            erzeugt.append(ziel)
            if praefix:
                bs.prune_backups(out, label, db_max_count)
            else:
                bs.prune_dump_backups(out, label, retention_days, db_max_count, jetzt)
        except Exception as exc:  # noqa: BLE001 — Sammelbetrieb, Rest weiterversuchen
            fehler += 1
            print(f"✗ DB-Dump {label} fehlgeschlagen: {exc}", file=sys.stderr)
            failures.append(_sanitisierter_dump_fehler(label, settings.BACKUP_DUMP_BIN, exc))

    if medien:
        medien_root = Path(settings.MEDIA_STORAGE_DIR).parent  # = app_storage
        if medien_root.is_dir():
            ziel = _freier_backup_pfad(out, bs.medien_dateiname(jetzt), ".tar.gz")
            try:
                groesse = _tar_medien(ziel, medien_root, out)
                print(f"✓ Medien-Archiv: {ziel.name} ({groesse // 1024} KB)")
                ziel.chmod(0o600)
                erzeugt.append(ziel)
                bs.prune_backups(out, "medien", medien_behalten)
            except Exception as exc:  # noqa: BLE001
                fehler += 1
                print(f"✗ Medien-Archiv fehlgeschlagen: {exc}", file=sys.stderr)
                failures.append("medien: Archivierung fehlgeschlagen")
        else:
            print(f"• Medien-Verzeichnis {medien_root} fehlt — uebersprungen")

    # Off-Site-Upload (3-2-1): frisch erzeugte Dumps an die Gegenstelle schieben.
    offsite_ok = None
    if settings.BACKUP_REMOTE_ENABLED and erzeugt:
        rc, detail = _remote_upload(erzeugt, out)
        offsite_ok = rc == 0
        if rc != 0:
            fehler += 1
            failures.append(detail or "Off-Site-Upload fehlgeschlagen")

    if fehler:
        print(f"Backup mit {fehler} Fehler(n) beendet.", file=sys.stderr)
    else:
        print(f"Backup vollstaendig nach {out}.")
    from app.services.backup_status import schreibe_status, sichere_meldung
    try:
        # Pre-Update-Laeufe getrennt fuehren: sie duerfen weder ein ausgefallenes
        # Tages-Backup verdecken noch dessen Bewertung verschlechtern.
        schreibe_status(out, ".status-backup-pre-update.json" if praefix else ".status-backup.json", {
            "ok": not fehler, "kind": "pre-update" if praefix else "daily",
            "files": [{"name": p.name, "size": p.stat().st_size} for p in erzeugt],
            "failures": [sichere_meldung(f) for f in failures], "offsite": {"enabled": settings.BACKUP_REMOTE_ENABLED,
                "ok": offsite_ok, "ziel_beschreibung": "Konfiguriertes Off-Site-Ziel"},
        })
    except Exception:
        bs.logger.warning("Backup-Status konnte nicht erstellt werden")
    return BackupResult(returncode=1 if fehler else 0, created=erzeugt, failures=failures)


def _remote_upload(dateien: list[Path], backup_dir: Path) -> tuple[int, str | None]:
    """Laedt die uebergebenen Backups an das konfigurierte Remote-Ziel. 0 = ok."""
    from app.services import remote_backup_service as rbs
    cfg = rbs.config_aus_settings()
    try:
        rbs.config_pruefen(cfg)
    except ValueError as exc:
        print(f"✗ Remote-Upload-Konfiguration ungueltig: {exc}", file=sys.stderr)
        return 1, "Off-Site-Upload: Konfiguration ungueltig"
    try:
        rbs.upload(cfg, dateien, backup_dir)
        print(f"✓ Off-Site-Upload ({len(dateien)} Datei(en)) → {cfg.ziel_beschreibung()}")
        return 0, None
    except Exception as exc:  # noqa: BLE001 — Off-Site-Fehler sichtbar machen
        print(f"✗ Off-Site-Upload fehlgeschlagen ({cfg.ziel_beschreibung()}): {exc}",
              file=sys.stderr)
        return 1, f"Off-Site-Upload fehlgeschlagen ({cfg.ziel_beschreibung()})"


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
        dateien = sorted(p for p in out.glob("*")
                         if p.is_file() and not p.name.endswith(".partial") and not p.name.startswith("."))
    else:
        dateien = rbs.neueste_je_praefix(out, ("einsatzleiter", "einsatzleiter_weather", "pre-update-einsatzleiter",
                                                "pre-update-einsatzleiter_weather", "medien"))
    if not dateien and rbs.config_aus_settings().protocol != "rclone":
        print(f"✗ Keine Backups in {out} gefunden.", file=sys.stderr)
        return 1
    return _remote_upload(dateien, out)[0]


def restore_test(scratch_db: str = "") -> int:
    """Status auch bei fehlgeschlagener Probe best effort schreiben."""
    from app.config import settings
    from app.services.backup_status import schreibe_status
    daten: dict = {"dump": None, "db_checks": [], "warnings": []}
    rc = 1
    try:
        rc = _restore_test(scratch_db, daten)
        return rc
    finally:
        schreibe_status(Path(settings.BACKUP_DIR), ".status-restore.json", {**daten, "ok": rc == 0})


def _restore_test(scratch_db: str, daten: dict) -> int:
    """Prueft Haupt-DB, vorhandene Wetter-DB und das neueste Medienarchiv."""
    from app.config import settings
    from app.services import backup_service as bs
    scratch = scratch_db or settings.BACKUP_RESTORE_TEST_DB
    if not re.fullmatch(r"[A-Za-z0-9_]+", scratch):
        print("Ungueltiger Restore-Test-DB-Name", file=sys.stderr)
        return 1
    try:
        cfg = bs.parse_database_url(settings.DATABASE_URL)
        wetter_cfg = (bs.parse_database_url(settings.WEATHER_DATABASE_URL)
                      if settings.WEATHER_DATABASE_URL.strip() else None)
        produktiv = {cfg.database} | ({wetter_cfg.database} if wetter_cfg else set())
        if {scratch, scratch + "_weather"} & produktiv:
            raise RuntimeError("Restore-Test-DB darf nicht die Produktions-DB sein")
        out = Path(settings.BACKUP_DIR)
        dumps = sorted(p for p in out.glob("einsatzleiter-*.sql.gz")
                       if bs.parse_dump_dateiname(p.name) and p.is_file())
        if not dumps:
            raise RuntimeError("Kein Haupt-Dump gefunden")
        daten["dump"] = dumps[-1].name
        daten["db_checks"].extend(["haupt.users", "haupt.fire_dept", "haupt.alembic_version"])
        if _restore_db(cfg, scratch, dumps[-1]):
            return 1
        wetter = sorted(p for p in out.glob("einsatzleiter_weather-*.sql.gz")
                        if bs.parse_dump_dateiname(p.name) and p.is_file())
        if wetter_cfg and not wetter:
            daten["warnings"].append("Kein Wetter-Dump vorhanden")
        if wetter_cfg and wetter:
            daten["db_checks"].extend(["wetter.weather_reading", "wetter.weather_reading_hourly",
                                       "wetter.abfluss_reading"])
        if wetter_cfg and wetter and _restore_db(wetter_cfg, scratch + "_weather", wetter[-1], wetter=True):
            return 1
        medien = sorted(p for p in out.glob("medien-*.tar.gz") if p.is_file())
        if not medien:
            daten["warnings"].append("Kein Medienarchiv vorhanden")
        if medien:
            # Bis EOF lesen: auch gzip-CRC/Laenge nach dem TAR-Endmarker pruefen.
            with gzip.open(medien[-1], "rb") as gz:
                while gz.read(1024 * 1024):
                    pass
            with tarfile.open(medien[-1], "r:gz") as tar:
                for eintrag in tar:
                    if eintrag.isfile():
                        fh = tar.extractfile(eintrag)
                        assert fh is not None
                        with fh:
                            while fh.read(1024 * 1024):
                                pass
        return 0
    except Exception as exc:  # noqa: BLE001 — CLI liefert einen Fehlercode
        print(f"Restore-Probe fehlgeschlagen: {exc}", file=sys.stderr)
        return 1


def _restore_db(cfg, scratch: str, neuester: Path, wetter: bool = False) -> int:
    from app.config import settings
    from app.services import backup_service as bs
    client = settings.BACKUP_CLIENT_BIN
    full_env = {**os.environ, "MYSQL_PWD": cfg.password}

    def _admin(sql: str) -> None:
        argv, _ = bs.build_admin_argv(cfg, sql, client)
        r = subprocess.run(argv, env=full_env, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "ignore")[:600])

    print(f"Restore-Probe: {neuester.name} → DB {scratch}")
    try:
        _admin(f"DROP DATABASE IF EXISTS `{scratch}`; "
               f"CREATE DATABASE `{scratch}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        restore_argv, _ = bs.build_restore_argv(cfg, scratch, client)
        proc = subprocess.Popen(restore_argv, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env)
        assert proc.stdin is not None
        try:
            # Die Dumps enthalten ueblicherweise selbst FOREIGN_KEY_CHECKS=0.
            # Das explizite Setzen macht auch historische/anders erzeugte Dumps
            # robust, deren CREATE TABLE-Reihenfolge nicht topologisch ist.
            proc.stdin.write(b"SET FOREIGN_KEY_CHECKS=0;\nSET UNIQUE_CHECKS=0;\n")
            with gzip.open(neuester, "rb") as gz:
                for zeile in iter(gz.readline, b""):
                    proc.stdin.write(bs.normalisiere_dump_fk_namen(zeile))
            proc.stdin.write(b"SET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n")
        finally:
            proc.stdin.close()
        err = proc.stderr.read() if proc.stderr else b""
        if proc.wait() != 0:
            print(f"✗ Restore fehlgeschlagen: {err.decode('utf-8', 'ignore')[:600]}", file=sys.stderr)
            return 1

        for sql in (["SELECT COUNT(*) FROM weather_reading",
                     "SELECT COUNT(*) FROM weather_reading_hourly",
                     "SELECT COUNT(*) FROM abfluss_reading"] if wetter else bs.verify_sql()):
            argv, _ = bs.build_query_argv(cfg, scratch, sql, client)
            r = subprocess.run(argv, env=full_env, capture_output=True)
            if r.returncode != 0:
                print(f"✗ Verifikation fehlgeschlagen ({sql}): "
                      f"{r.stderr.decode('utf-8', 'ignore')[:300]}", file=sys.stderr)
                return 1
            if not wetter:
                bs.pruefe_zeilenzahl(sql, r.stdout.decode())
            elif not r.stdout.decode().strip().isdigit():
                raise RuntimeError("Ungueltige Zeilenzahl in Wetter-DB")
        if not wetter:
            argv, _ = bs.build_query_argv(cfg, scratch, "SELECT version_num FROM alembic_version", client)
            r = subprocess.run(argv, env=full_env, capture_output=True)
            if r.returncode:
                raise RuntimeError("Alembic-Version konnte nicht gelesen werden")
            if r.stdout.decode().strip() != bs.aktueller_alembic_head():
                if neuester.name.startswith("pre-update-"):
                    print("Warnung: Pre-Update-Dump weicht vom aktuellen Alembic-Head ab.", file=sys.stderr)
                else:
                    raise RuntimeError("Alembic-Version weicht vom aktuellen Code-Head ab")
        print("✓ Restore-Probe bestanden (Dump ist wiederherstellbar).")
        return 0
    finally:
        try:
            _admin(f"DROP DATABASE IF EXISTS `{scratch}`")
        except Exception as exc:  # noqa: BLE001
            print(f"! Warnung: Wegwerf-DB {scratch} konnte nicht entfernt werden: {exc}",
                  file=sys.stderr)


def suche_neuaufbau(org_id: int | None = None, alle: bool = False, quelle: str | None = None) -> None:
    """Baut die ausgewaehlten Org-Indizes in einer eigenen System-Session auf."""
    from app.models.master import OrgSettings
    from app.services.suche.indexer import neuaufbau
    from app.services.suche.quellen import ADAPTER, adapter_fuer, lade_adapter

    lade_adapter()
    if quelle is not None:
        adapter_fuer(quelle)
    if (org_id is None) == (not alle):
        raise ValueError("Genau --org oder --alle angeben")
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org_ids = ([o.org_id for o in db.query(OrgSettings).filter(OrgSettings.suche_aktiv.is_(True))]
                   if alle else [org_id])
        for ident in org_ids:
            assert ident is not None
            for name in ([quelle] if quelle is not None else list(ADAPTER)):
                zaehler = neuaufbau(db, ident, name)
                print(f"Org {ident}, Quelle {name}: " + ", ".join(f"{k}={v}" for k, v in zaehler.items()))
    finally:
        db.close()


def suche_vektor_init() -> None:
    """Prueft VECTOR-Unterstuetzung und legt die konfigurierte Tabelle an."""
    from app.services.suche.vektor import stelle_tabelle_sicher, tabellenname, vektor_faehig

    with SessionLocal() as db:
        set_tenant_context(db, None)
        if not vektor_faehig(db):
            print("VECTOR wird von dieser Datenbank nicht unterstuetzt.")
            return
        stelle_tabelle_sicher(db)
        db.commit()
        print(f"Vektortabelle {tabellenname()} ist bereit.")


def suche_vektor_aufbau(org_id: int | None = None) -> None:
    """Plant den Vektoraufbau; Embeddings erstellt ausschliesslich der Worker."""
    from sqlalchemy import select

    from app.models.master import OrgSettings
    from app.models.suche import SearchDocument
    from app.services.suche.vektor import stelle_tabelle_sicher
    from app.services.suche.vektor_sync import plane_vektor_job

    with SessionLocal() as db:
        set_tenant_context(db, None)
        stelle_tabelle_sicher(db)
        dokumente = select(SearchDocument).join(
            OrgSettings, OrgSettings.org_id == SearchDocument.org_id,
        ).where(OrgSettings.suche_aktiv.is_(True))
        if org_id is not None:
            dokumente = dokumente.where(SearchDocument.org_id == org_id)
        anzahl = sum(plane_vektor_job(db, doc.org_id, doc.id) for doc in db.scalars(dokumente))
        db.commit()
        print(f"{anzahl} Vektor-Jobs geplant.")


def mfa_reset(username: str) -> None:
    """Notfallzugang ausschliesslich ueber die lokale Server-CLI."""
    from app.services import totp_service

    with SessionLocal() as db:
        set_tenant_context(db, None)
        user = db.query(User).filter(User.username == username).first()
        if user is None or not user.is_system_admin:
            raise ValueError("Systemadministrator nicht gefunden")
        totp_service.reset(db, user, None, local=True)
        db.commit()
        totp_service.notify_reset(db, user)


def mfa_global_aus() -> None:
    from app.services import totp_service

    with SessionLocal() as db:
        set_tenant_context(db, None)
        totp_service.set_enabled(db, False, None, None, local=True)
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command")
    p_update = sub.add_parser("update-run", help="Lokalen Hintergrund-Updatejob ausführen")
    p_update.add_argument("job_id")
    p_mfa = sub.add_parser("mfa-reset", help="Lokaler Notfallreset eines Systemadministrators")
    p_mfa.add_argument("username")
    sub.add_parser("mfa-global-aus", help="2FA lokal deaktivieren und Admin-Sessions widerrufen")

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

    p_suche = sub.add_parser("suche-neuaufbau", help="Suchindex neu aufbauen")
    org_wahl = p_suche.add_mutually_exclusive_group(required=True)
    org_wahl.add_argument("--org", type=int)
    org_wahl.add_argument("--alle", action="store_true")
    p_suche.add_argument("--quelle", choices=("objekt", "objekt_dokument_seite", "katplan_element", "katplan_seite"))
    sub.add_parser("suche-vektor-init", help="Vektortabelle pruefen und anlegen")
    p_vektor = sub.add_parser("suche-vektor-aufbau", help="Vektor-Jobs fuer Suchdokumente planen")
    p_vektor.add_argument("--org", type=int)

    p_osm = sub.add_parser("osm-gebaeude-import", help="Lokale OSM-Gebaeude importieren")
    p_osm.add_argument("--quelle", help="Nur diese konfigurierte Quelle importieren")
    p_osm.add_argument("--erzwingen", action="store_true")
    p_osm_aktiv = sub.add_parser("osm-gebaeude-aktivieren", help="Geprueften OSM-Import aktivieren")
    p_osm_aktiv.add_argument("lauf_id", type=int)
    sub.add_parser("osm-gebaeude-status", help="OSM-Importstatus anzeigen")

    args = parser.parse_args()
    if args.command == "update-run":
        from app.services.update_job import run_job
        run_job(args.job_id)
        return
    if args.command and args.command.startswith("osm-gebaeude-"):
        from app.services import osm_gebaeude_import as osm
        if args.command == "osm-gebaeude-status":
            print(json.dumps(osm.status(), ensure_ascii=False))
        elif args.command == "osm-gebaeude-aktivieren":
            print(json.dumps(osm.lauf_info(osm.aktivieren(args.lauf_id)), ensure_ascii=False))
        else:
            quellen = [(n, u) for n, u in osm.quellen() if not args.quelle or n == args.quelle]
            if not quellen:
                parser.error("Keine passende OSM-Quelle konfiguriert")
            fehler = False
            for name, url in quellen:
                lauf = osm.importieren(name, url, erzwingen=args.erzwingen)
                print(json.dumps({"quelle": name, **osm.lauf_info(lauf),
                                  "ergebnis": getattr(lauf, "ergebnis", lauf.status)}, ensure_ascii=False))
                fehler = fehler or lauf.status == "fehler"
            if fehler:
                sys.exit(1)
        return
    if args.command == "mfa-reset":
        mfa_reset(args.username)
    elif args.command == "mfa-global-aus":
        mfa_global_aus()
    elif args.command == "create-admin":
        create_admin(args.username, args.password, args.display_name)
    elif args.command == "create-api-key":
        create_api_key(args.label)
    elif args.command == "create-sms-gateway-token":
        create_sms_gateway_token(args.label, args.org_id)
    elif args.command == "promote-to-system-admin":
        promote_to_system_admin(args.username)
    elif args.command == "backup":
        result = run_backup(args.out, args.keep, 0 if args.no_media else -1)
        sys.exit(result.returncode)
    elif args.command == "restore-test":
        sys.exit(restore_test(args.scratch_db))
    elif args.command == "suche-neuaufbau":
        suche_neuaufbau(args.org, args.alle, args.quelle)
    elif args.command == "suche-vektor-init":
        suche_vektor_init()
    elif args.command == "suche-vektor-aufbau":
        suche_vektor_aufbau(args.org)
    elif args.command == "backup-upload":
        sys.exit(backup_upload(args.all))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
