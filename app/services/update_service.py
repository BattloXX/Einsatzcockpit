"""Updates: Vorprüfung, Backup, Code-Austausch, Dependencies, Migration, Reload."""

import base64
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

Progress = Callable[[str, str], None]


def _progress(progress: Progress | None, step: str, state: str) -> None:
    if progress:
        progress(step, state)


APP_ROOT = Path(__file__).parent.parent.parent  # Projektverzeichnis

# Dateien und Verzeichnisse, die beim Update NIEMALS überschrieben werden
PROTECTED_PATHS = {
    ".env",
    ".env.local",
    ".git",
    ".venv",
    ".update-rollback",
    ".update.lock",
    "app/static/img/uploads",  # hochgeladene Logos etc.
    "app_storage",          # Nutzerdaten, Medien und Backups
}


DOCKER_MESSAGE = "Updates im Container bitte mit docker compose pull && docker compose up -d ausführen."
logger = logging.getLogger(__name__)


def is_docker() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("EC_RUNTIME", "").lower() == "docker"


def _backup_gate(backup: dict) -> dict | None:
    if not backup["success"] and backup["required"]:
        return {"success": False,
                "message": "Update abgebrochen, Backup fehlgeschlagen, Code unverändert: " + backup["message"],
                "files_updated": 0, "files_skipped": 0, "deps_installed": "übersprungen",
                "migrations_applied": "übersprungen", "server_reloaded": False, "backup": backup}
    return None


def _dump_hint(backup: dict) -> str:
    return "DB-Restore aus dem Pre-Update-Dump: " + (
        ", ".join(backup.get("files", [])) or "kein Dump verfügbar (Backup-Konfiguration prüfen)"
    )


def _alembic_state(command: str) -> str | None:
    python = APP_ROOT / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python) if python.exists() else "python", "-m", "alembic", command],
            cwd=str(APP_ROOT), capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            # Nur Revisions-IDs vergleichen; '(head)' ändert sich durch neue Skripte.
            return "\n".join(sorted(line.split()[0] for line in result.stdout.splitlines() if line.strip()))
        logger.warning("alembic %s fehlgeschlagen: %s", command, result.stderr[-800:])
    except Exception:
        logger.exception("alembic %s nicht abrufbar", command)
    return None


def _finish_update(
    result: dict, install_deps: bool, rollback: Callable[[], str], progress: Progress | None = None
) -> dict:
    _progress(progress, "dependencies", "running")
    deps_ok, deps = _run_pip_install() if install_deps else (True, "übersprungen")
    result.update(deps_installed=deps, migrations_applied="übersprungen", server_reloaded=False)
    _progress(progress, "dependencies", "success" if deps_ok else "failed")
    if not deps_ok:
        result.update(success=False, message=f"Abhängigkeiten konnten nicht installiert werden: {deps}. "
                      + rollback() + " " + _dump_hint(result["backup"]))
        return result
    _progress(progress, "migration", "running")
    before = _alembic_state("current")
    heads = _alembic_state("heads")
    result.update(migrations_from=before, migrations_to=heads)
    logger.info("Update-Migration: current=%s heads=%s", before, heads)
    ok, message = _run_migrations()
    _progress(progress, "migration", "success" if ok else "failed")
    result["migrations_applied"] = message
    if not ok:
        after = _alembic_state("current")
        result["migrations_after"] = after
        if before is not None and after is not None and before == after:
            recovery = rollback()
        else:
            recovery = "Kein automatischer Code-Rollback: Revisionen angewendet oder Revisionsstand unbekannt."
        result.update(success=False, message=f"Migration fehlgeschlagen: {message}. {recovery} "
                      "Die DB kann teilweise migriert sein (MariaDB-DDL ist nicht transaktional). "
                      + _dump_hint(result["backup"]))
        return result
    result.update(success=True)
    if progress is None:
        result["server_reloaded"] = _reload_server()
        if not result["server_reloaded"]:
            result.update(success=False, message=RELOAD_ERROR + ". Code und Migration sind bereits eingespielt.")
    return result


@contextmanager
def _update_lock():
    """Haelt pro Installation exklusiv den Update-Lock; wartet bei Kontention nicht."""
    lock_path = APP_ROOT / ".update.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _is_protected(rel_path: str) -> bool:
    for p in PROTECTED_PATHS:
        if rel_path == p or rel_path.startswith(p + "/") or rel_path.startswith(p + os.sep):
            return True
    return False


def validate_zip(zip_path: Path) -> tuple[bool, str]:
    """Prüft ob das ZIP eine gültige App-Struktur enthält und keine Zip-Slip-Pfade hat."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            # Strukturprüfung — auch GitHub-Zipballs akzeptieren, die alles unter
            # einem Root-Ordner (z. B. BattloXX-Einsatzcockpit-<sha>/) verpacken
            has_app = any(
                n.startswith(("app/", "app\\")) or "/app/" in n.replace("\\", "/")
                for n in names
            )
            has_pyproject = any(n == "pyproject.toml" or n.endswith("/pyproject.toml") for n in names)
            if not has_app:
                return False, "ZIP enthält kein app/-Verzeichnis"
            if not has_pyproject:
                return False, "ZIP enthält keine pyproject.toml"
            # Zip-Slip-Prüfung: keine absoluten Pfade, keine ".."-Komponenten
            for n in names:
                normalized = n.replace("\\", "/")
                if normalized.startswith("/") or normalized.startswith("\\"):
                    return False, f"Unsicherer absoluter Pfad im ZIP: {n}"
                if any(part == ".." for part in normalized.split("/")):
                    return False, f"Unsicherer Pfad-Traversal im ZIP: {n}"
            # Symlinks ablehnen
            for info in zf.infolist():
                if info.external_attr >> 16 & 0o170000 == 0o120000:
                    return False, f"Symlinks sind nicht erlaubt: {info.filename}"
        return True, "OK"
    except zipfile.BadZipFile:
        return False, "Ungültige ZIP-Datei"


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def apply_update(
    zip_path: Path, expected_sha256: str | None = None, install_deps: bool = False, progress: Progress | None = None
) -> dict:
    """
    Extrahiert das ZIP und kopiert Dateien über die bestehende Installation.

    Sicherheit:
    - validate_zip prüft strukturell und gegen Zip-Slip / Symlinks.
    - Falls expected_sha256 angegeben ist, muss er exakt mit dem Datei-Hash übereinstimmen.
    - Staging und Zielpfade werden vor dem Austausch geprüft.
    - Bestehende Dateien werden für einen Code-Rollback gesichert.
    """
    if is_docker():
        return {"success": False, "message": DOCKER_MESSAGE}
    with _update_lock() as acquired:
        if not acquired:
            return {"success": False, "locked": True, "message": "Update bereits in Arbeit"}
        return _apply_update_locked(zip_path, expected_sha256, install_deps, progress)


def _apply_update_locked(
    zip_path: Path, expected_sha256: str | None, install_deps: bool, progress: Progress | None = None
) -> dict:
    """Implementierung von apply_update(), waehrend der Update-Lock gehalten wird."""
    valid, msg = validate_zip(zip_path)
    if not valid:
        return {"success": False, "message": msg}

    if expected_sha256:
        actual = compute_sha256(zip_path)
        if actual.lower() != expected_sha256.strip().lower():
            return {
                "success": False,
                "message": "SHA256-Prüfsumme stimmt nicht überein. Erwartet: "
                f"{expected_sha256[:16]}…, tatsächlich: {actual[:16]}…",
            }

    skipped: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir).resolve()
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.infolist():
                    if not (tmp / member.filename).resolve().is_relative_to(tmp):
                        return {"success": False, "message": "Unsicherer ZIP-Zielpfad"}
                    zf.extract(member, tmp)
        except (OSError, zipfile.BadZipFile) as exc:
            return {"success": False, "message": f"ZIP konnte nicht bereitgestellt werden: {exc}"}
        entries = list(tmp.iterdir())
        src_root = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
        if not (src_root / "app").is_dir() or not (src_root / "pyproject.toml").is_file():
            return {"success": False, "message": "ZIP muss app/ und pyproject.toml auf derselben Ebene enthalten."}
        files: list[tuple[Path, str]] = []
        for src in src_root.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(src_root).as_posix()
            dst = APP_ROOT / rel
            symlink = any(path.is_symlink() for path in [dst, *dst.parents] if path != APP_ROOT)
            if _is_protected(rel) or symlink or not dst.resolve().is_relative_to(APP_ROOT.resolve()):
                skipped.append(rel)
                continue
            files.append((src, rel))

        _progress(progress, "backup", "running")
        backup = _run_pre_migration_backup()
        _progress(progress, "backup", "success" if backup["success"] else "failed")
        blocked = _backup_gate(backup)
        if blocked:
            return blocked
        _progress(progress, "code", "running")
        project = APP_ROOT / "pyproject.toml"
        old_hash = compute_sha256(project) if project.is_file() else None
        rollback_root = APP_ROOT / ".update-rollback"
        rollback_dir = rollback_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        rollback_dir.mkdir(parents=True, mode=0o700)
        replaced: list[str] = []
        created: list[str] = []
        # Zuerst sämtliche Originale sichern, erst danach die erste Datei ersetzen.
        for _src, rel in files:
            dst = APP_ROOT / rel
            if dst.exists():
                saved = rollback_dir / "files" / rel
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, saved)
                replaced.append(rel)
            else:
                created.append(rel)
        (rollback_dir / "manifest.json").write_text(json.dumps({"replaced": replaced, "created": created}))

        def rollback() -> str:
            try:
                for rel in replaced:
                    shutil.copy2(rollback_dir / "files" / rel, APP_ROOT / rel)
                for rel in created:
                    (APP_ROOT / rel).unlink(missing_ok=True)
                return f"Code wiederhergestellt aus {rollback_dir}."
            except OSError as exc:
                return f"Code-Rollback fehlgeschlagen: {exc}; Sicherung: {rollback_dir}."

        try:
            for src, rel in files:
                dst = APP_ROOT / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        except OSError as exc:
            return {"success": False, "message": f"Code-Austausch fehlgeschlagen: {exc}. "
                    + rollback() + " " + _dump_hint(backup), "backup": backup}
        _progress(progress, "code", "success")
        changed_project = old_hash != compute_sha256(project)

    result = _finish_update(
        {
            "success": True,
            "message": "Update erfolgreich eingespielt",
            "files_updated": len(files),
            "files_skipped": len(skipped),
            "backup": backup,
            "rollback_dir": str(rollback_dir),
        },
        install_deps or changed_project,
        rollback,
        progress,
    )
    if result["success"]:
        for old in sorted((p for p in rollback_root.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True)[3:]:
            shutil.rmtree(old)
    return result


def _run_pip_install() -> tuple[bool, str]:
    """Installiert die pyproject-Abhängigkeiten ins venv (pip install -e .).

    Nötig, wenn ein Update neue Dependencies mitbringt (z. B. pdf2image).

    PYTHONNOUSERSITE=1 verhindert, dass pip bei einem venv mit nicht beschreibbarem
    site-packages (Berechtigungsproblem) still auf eine User-Installation
    (~/.local/...) ausweicht. Ohne dieses Flag meldet pip in diesem Fall Exit-Code 0
    ("Successfully installed"), obwohl die Pakete fürs venv unsichtbar bleiben -
    der nächste Start/Backup crasht dann mit ModuleNotFoundError, ohne dass der
    eigentliche Update-Schritt je einen Fehler gezeigt hätte (siehe DR-Vorfall
    2026-08-21: venv-site-packages war root-owned statt App-User-owned).
    Der String-Check bleibt als zusätzliches Sicherheitsnetz, falls die
    Ausweich-Installation trotz des Flags durchrutscht.
    Gibt "OK" oder eine gekürzte Fehlermeldung zurück.
    """
    python = APP_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python")  # Fallback: System-Python (Dev)
    try:
        env = {**os.environ, "PYTHONNOUSERSITE": "1"}
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "-e", ".", "--no-input", "--quiet"],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )
        ausgabe = f"{result.stdout}\n{result.stderr}"
        if "Defaulting to user installation" in ausgabe:
            return False, (
                "site-packages im venv nicht beschreibbar (Berechtigungsproblem) - "
                "pip wäre sonst still auf eine unsichtbare User-Installation "
                "ausgewichen. venv-Besitzrechte prüfen (z. B. chown -R <App-User> .venv)."
            )
        if result.returncode == 0:
            return True, "OK"
        return False, f"Fehler: {result.stderr[-500:]}"
    except Exception as e:
        return False, f"Fehler: {e}"


def _run_migrations() -> tuple[bool, str]:
    """Führt alembic upgrade head aus. Gibt Ausgabe oder Fehlermeldung zurück."""
    from app.config import settings

    python = APP_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python")  # Fallback: System-Python
    try:
        result = subprocess.run(
            [str(python), "-m", "alembic", "upgrade", "head"],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=settings.UPDATE_MIGRATION_TIMEOUT_S,
        )
        if result.returncode == 0:
            return True, "OK"
        return False, f"Fehler: {result.stderr[-800:]}"
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return False, f"Zeitlimit der Migration überschritten: {stderr[-800:]}"
    except Exception as e:
        return False, f"Fehler: {e}"


def _run_pre_migration_backup() -> dict:
    """Erzeugt vor Migrationen DB-Dumps und liefert ein template-taugliches Ergebnis."""
    from app.config import settings

    required = bool(settings.UPDATE_REQUIRE_BACKUP)
    if not settings.UPDATE_BACKUP_BEFORE_MIGRATE:
        return {"success": True, "required": required, "message": "deaktiviert", "files": []}
    try:
        from app.cli import run_backup
        result = run_backup(
            out_dir=settings.BACKUP_DIR,
            keep=-1,
            include_media=0,
            praefix="pre-update-",
        )
        files = [str(path) for path in result.created]
        if result.returncode == 0:
            return {"success": True, "required": required,
                    "message": f"OK ({len(files)} DB-Dump(s))", "files": files}
        detail = "; ".join(result.failures)[:400]
        message = f"Backup fehlgeschlagen (Exit-Code {result.returncode})"
        if detail:
            message += f": {detail}"
        return {"success": False, "required": required, "message": message, "files": files}
    except Exception as exc:
        return {"success": False, "required": required,
                "message": f"Backup fehlgeschlagen: {exc}", "files": []}


RELOAD_ERROR = "Neustart fehlgeschlagen, bitte manuell: sudo systemctl restart einsatzleiter"


def _reload_server() -> bool:
    """Graceful Reload per PID-Datei, sonst vorhandenes sudo-Recht nutzen."""
    from app.config import settings

    paths = [APP_ROOT / "gunicorn.pid"]
    if settings.GUNICORN_PID_FILE:
        paths.insert(0, Path(settings.GUNICORN_PID_FILE))
    for pidfile in paths:
        try:
            pid = int(pidfile.read_text().strip())
            if pid <= 1:
                continue
            os.kill(pid, 0)
            os.kill(pid, signal.SIGHUP)  # type: ignore[attr-defined]
            return True
        except OSError, ValueError:
            continue
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "einsatzleiter"],
            timeout=15,
            capture_output=True,
        )
        if result.returncode == 0:
            return True
    except OSError, subprocess.SubprocessError:
        logger.warning(RELOAD_ERROR)
        return False
    logger.warning(RELOAD_ERROR)
    return False


def parse_calver(text: str) -> tuple[int, ...] | None:
    """CalVer mit optionalem v-Präfix und Tages-Suffix lesen."""
    if not re.fullmatch(r"v?[0-9]{4}\.[0-9]{2}\.[0-9]{2}(?:\.[0-9]+)?", text):
        return None
    return tuple(int(part) for part in text.removeprefix("v").split("."))


def is_newer(latest: str, current: str) -> bool:
    """CalVer numerisch vergleichen; unbekannte Versionen wie bisher behandeln."""
    left, right = parse_calver(latest), parse_calver(current)
    if left is None or right is None:
        return latest.removeprefix("v") != current.removeprefix("v")
    return (*left[:3], left[3] if len(left) == 4 else 0) > (
        *right[:3], right[3] if len(right) == 4 else 0
    )


def get_current_version() -> str:
    """Code-Version lesen; bestätigte alte Releases über lokale Metadaten erkennen."""
    current = "unbekannt"
    try:
        content = (APP_ROOT / "pyproject.toml").read_text()
        for line in content.splitlines():
            if line.strip().startswith("version"):
                current = line.split("=")[1].strip().strip('"').strip("'")
                break
    except Exception:
        return current
    try:
        installed = json.loads((APP_ROOT / "app_storage/update/installed.json").read_text())
        tag = installed.get("tag")
        if installed.get("kind") != "release" or not isinstance(tag, str) or parse_calver(tag) is None:
            return current
        if is_git_checkout():
            rc, sha, _ = _run_git(["rev-parse", "HEAD"], timeout=2)
            if rc or not sha or sha != installed.get("sha"):
                return current
        elif parse_calver(current) is None or is_newer(current, tag):
            return current
        return tag.removeprefix("v")
    except Exception:
        return current


GITHUB_REPO = "BattloXX/Einsatzcockpit"
# SystemSettings-Keys fuer das GitHub-Update
GITHUB_TOKEN_KEY = "github_update_token_enc"   # Fernet-verschlüsselter PAT (private Repos)
DEPLOYED_REF_KEY = "update_deployed_ref"       # JSON {branch, sha, datum} des letzten Branch-Updates


def _github_headers(token: str | None = None) -> dict:
    headers = {
        "User-Agent": "Einsatzcockpit-Updater/1.0",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_json(url: str, token: str | None = None, timeout: int = 10):
    """GET auf die GitHub-API, JSON-Antwort. Wirft bei HTTP-/Netzfehlern."""
    import json as _json
    import urllib.request as _req

    request = _req.Request(url, headers=_github_headers(token))
    with _req.urlopen(request, timeout=timeout) as resp:
        return _json.loads(resp.read())


def get_github_token(db) -> str | None:
    """Liest den (Fernet-verschlüsselten) GitHub-PAT aus SystemSettings. None wenn keiner gesetzt."""
    from app.models.master import SystemSettings
    row = db.query(SystemSettings).filter(SystemSettings.key == GITHUB_TOKEN_KEY).first()
    if not row or not row.value:
        return None
    try:
        from app.services.ai_service import decrypt_api_key
        return decrypt_api_key(row.value)
    except Exception:
        return None


def check_github_release(
    repo: str = GITHUB_REPO, prerelease: bool = False, token: str | None = None,
) -> dict:
    """Prüft GitHub auf verfügbare Releases und vergleicht mit der aktuellen Version.

    prerelease=True: berücksichtigt auch Pre-Releases (sucht in der Gesamt-Release-Liste).
    prerelease=False (Standard): nur stabile Releases (/releases/latest).
    token: optionaler PAT für private Repos.
    """
    current = get_current_version()
    try:
        data: dict
        if prerelease:
            # Alle Releases abrufen (sortiert nach Datum absteigend) – erster Treffer ist neuester
            releases = _github_json(
                f"https://api.github.com/repos/{repo}/releases?per_page=10", token
            )
            data = next((release for release in releases if not release.get("draft")), {})
        else:
            data = _github_json(
                f"https://api.github.com/repos/{repo}/releases/latest", token
            )

        tag_name = data.get("tag_name", "")
        tag = tag_name.removeprefix("v")
        assets = data.get("assets", [])

        # Server-ZIP: eigenes Release-Asset bevorzugen; Fallback: GitHub-Quellcode-ZIP
        zip_url = next((a["browser_download_url"] for a in assets if a["name"].endswith(".zip")), None)
        download_url = zip_url or data.get("zipball_url")

        # Android-APK: erstes .apk-Asset im Release (falls vorhanden)
        apk_asset = next((a for a in assets if a["name"].endswith(".apk")), None)

        return {
            "current_version": current,
            "latest_tag": tag,
            "tag_name": tag_name,
            "download_url": download_url,
            "has_update": bool(tag) and is_newer(tag_name, current),
            "is_downgrade": (parse_calver(tag_name) is not None and parse_calver(current) is not None
                             and is_newer(current, tag_name)),
            "is_prerelease": bool(data.get("prerelease", False)),
            "release_name": data.get("name", ""),
            "release_notes": (data.get("body") or "")[:500],
            "apk_url":     apk_asset["browser_download_url"] if apk_asset else None,
            "apk_name":    apk_asset["name"] if apk_asset else None,
            "apk_size_mb": round(apk_asset["size"] / 1024 / 1024, 1) if apk_asset else None,
        }
    except Exception as exc:
        return {
            "current_version": current,
            "latest_tag": None,
            "tag_name": None,
            "download_url": None,
            "has_update": False,
            "is_downgrade": False,
            "is_prerelease": False,
            "error": str(exc)[:200],
        }


def validate_release_tag(tag: str, current: dict, token: str | None = None) -> dict | None:
    """Bindet die Installation an das angezeigte, weiterhin aktuelle veröffentlichte Release."""
    if not _valid_ref(tag):
        return {"success": False, "message": "Ungültiger oder fehlender Release-Tag. Bitte erneut prüfen."}
    try:
        release = _github_json(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{quote(tag, safe='/')}", token
        )
        if release.get("draft") or release.get("tag_name") != tag:
            return {"success": False, "message": "Release existiert nicht oder ist ein Entwurf. Bitte erneut prüfen."}
        if current.get("tag_name") != tag:
            return {"success": False,
                    "message": "Das aktuelle Release weicht vom angezeigten Tag ab. Bitte erneut prüfen."}
    except Exception as exc:
        return {"success": False,
                "message": f"Release-Tag konnte nicht geprüft werden: {_scrub(str(exc), token)[:200]}"}
    return None


def list_github_branches(repo: str = GITHUB_REPO, token: str | None = None) -> list[dict]:
    """Listet Branches des Repos (Name + Commit-SHA). Leere Liste bei Fehlern."""
    try:
        branches = []
        for page in range(1, 11):
            batch = _github_json(
                f"https://api.github.com/repos/{repo}/branches?per_page=100&page={page}", token
            )
            branches.extend(batch)
            if len(batch) < 100:
                break
        result = [
            {"name": b.get("name", ""), "sha": (b.get("commit") or {}).get("sha", "")[:7]}
            for b in branches
            if b.get("name")
        ]
        return sorted(result, key=lambda b: b["name"] != "main")
    except Exception:
        return []


def check_github_branch(
    branch: str, repo: str = GITHUB_REPO, token: str | None = None,
) -> dict:
    """Holt den letzten Commit eines Branches (für das direkte Repo-Update).

    download_url zeigt auf den API-Zipball-Endpunkt (funktioniert mit PAT auch
    für private Repos und folgt der CDN-Weiterleitung).
    """
    try:
        if not _valid_ref(branch):
            raise ValueError("Ungültiger Branch-Name")
        segment = quote(branch, safe="/")
        data = _github_json(
            f"https://api.github.com/repos/{repo}/commits/{segment}", token
        )
        commit = data.get("commit") or {}
        author = commit.get("author") or {}
        message = (commit.get("message") or "").split("\n")[0]
        return {
            "branch": branch,
            "sha": data.get("sha", ""),
            "sha_short": (data.get("sha") or "")[:7],
            "commit_message": message[:120],
            "commit_date": author.get("date", ""),
            "commit_author": author.get("name", ""),
            "download_url": f"https://api.github.com/repos/{repo}/zipball/{segment}",
        }
    except Exception as exc:
        return {"branch": branch, "sha": None, "error": str(exc)[:200]}


def is_git_checkout() -> bool:
    """True, wenn die Installation ein echtes Git-Arbeitsverzeichnis ist."""
    return (APP_ROOT / ".git").exists()


def _scrub(text: str, token: str | None) -> str:
    """Entfernt ein evtl. in Git-Fehlermeldungen enthaltenes Token."""
    if token:
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        text = text.replace(token, "***").replace(encoded, "***")
    return text


def _run_git(args: list[str], timeout: int = 300, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:  # git fehlt / Timeout
        return 1, "", str(exc)


def git_update(
    branch: str, token: str | None = None, install_deps: bool = False, progress: Progress | None = None
) -> dict:
    """Aktualisiert per fetch und einzelnem Branch-Checkout, ohne andere Branches zu ändern."""
    return _git_sync(branch, token=token, install_deps=install_deps, branch_checkout=branch, progress=progress)


def git_update_release(
    tag: str, token: str | None = None, install_deps: bool = False, progress: Progress | None = None
) -> dict:
    """Aktualisiert einen Git-Checkout auf einen Release-Tag (detached HEAD)."""
    return _git_sync(tag, token=token, install_deps=install_deps, branch_checkout=None, progress=progress)


def _git_sync(
    ref: str, *, token: str | None, install_deps: bool, branch_checkout: str | None, progress: Progress | None = None
) -> dict:
    """Synchronisiert einen Branch oder Release-Tag unter exklusivem Update-Lock."""
    if is_docker():
        return {"success": False, "message": DOCKER_MESSAGE}

    if not is_git_checkout():
        return {"success": False, "message": "Kein Git-Arbeitsverzeichnis – bitte ZIP-Update verwenden."}

    with _update_lock() as acquired:
        if not acquired:
            return {"success": False, "locked": True, "message": "Update bereits in Arbeit"}
        return _git_sync_locked(
            ref, token=token, install_deps=install_deps, branch_checkout=branch_checkout, progress=progress
        )


def _git_sync_locked(
    ref: str, *, token: str | None, install_deps: bool, branch_checkout: str | None, progress: Progress | None = None
) -> dict:

    if not _valid_ref(ref) or _run_git(["check-ref-format", "--branch", ref])[0] != 0:
        return {"success": False, "message": "Ungültiger Branch-/Tag-Name"}
    remote = f"https://github.com/{GITHUB_REPO}.git"
    rc, before, err = _run_git(["rev-parse", "HEAD"])
    if rc:
        return {"success": False, "message": f"Git-Ausgangsstand nicht lesbar: {_scrub(err, token)}"}
    previous_branch = _run_git(["symbolic-ref", "-q", "--short", "HEAD"])[1]
    env = dict(os.environ)
    if token:
        auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {auth}")
    rc, _out, err = _run_git(["fetch", "--force", remote, "--", ref], env=env)
    if rc:
        return {"success": False, "message": f"git fetch fehlgeschlagen: {_scrub(err, token)[:300]}"}
    _progress(progress, "backup", "running")
    backup = _run_pre_migration_backup()
    _progress(progress, "backup", "success" if backup["success"] else "failed")
    blocked = _backup_gate(backup)
    if blocked:
        return {**blocked, "via": "git"}
    _progress(progress, "code", "running")
    args = (["checkout", "--force", "-B", branch_checkout, "FETCH_HEAD"] if branch_checkout is not None
            else ["checkout", "--force", "--detach", "FETCH_HEAD"])
    rc, _out, err = _run_git(args)
    if rc:
        return {"success": False, "message": f"git checkout fehlgeschlagen: {_scrub(err, token)[:300]}",
                "backup": backup}
    _progress(progress, "code", "success")
    after = _run_git(["rev-parse", "HEAD"])[1]
    changed = len(_run_git(["diff", "--name-only", before, after])[1].splitlines())

    def rollback() -> str:
        args = (["checkout", "--force", "-B", previous_branch, before] if previous_branch
                else ["checkout", "--force", "--detach", before])
        rc, _out, err = _run_git(args)
        if rc:
            return f"Git-Rollback auf {before} fehlgeschlagen: {_scrub(err, token)[:300]}."
        return f"Code auf {before} wiederhergestellt."

    return _finish_update(
        {
            "success": True,
            "message": f"Git-Update auf {ref} @ {after[:7]} eingespielt",
            "files_updated": changed,
            "files_skipped": 0,
            "via": "git",
            "backup": backup,
        },
        install_deps,
        rollback,
        progress,
    )


def _valid_ref(ref: str) -> bool:
    # Bewusst enger als Git: keine Optionsfragmente oder Reflog-Ausdrücke akzeptieren.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", ref) or "--" in ref or ".." in ref:
        return False
    # Auch Nicht-Git-Installationen ohne git-Binary müssen ZIP-Deploys prüfen können.
    return all(part and not part.startswith(".") and not part.endswith((".", ".lock"))
               for part in ref.split("/"))


def deploy_github_branch(
    branch: str,
    download_url: str,
    token: str | None = None,
    install_deps: bool = False,
    progress: Progress | None = None,
) -> dict:
    """Einheitlicher Branch-Deploy: bevorzugt Git (sauberer, konsistent zur
    Konsole); nur ohne Git-Checkout Fallback auf das Zipball-Overlay.

    Damit machen Web-Update und `git pull` auf der Konsole DASSELBE und stören
    sich nicht mehr gegenseitig.
    """
    if is_docker():
        return {"success": False, "message": DOCKER_MESSAGE}

    if not _valid_ref(branch):
        return {"success": False, "message": "Ungültiger Branch-Name"}
    download_url = f"https://api.github.com/repos/{GITHUB_REPO}/zipball/{quote(branch, safe='/')}"
    if is_git_checkout():
        return git_update(
            branch, token=token, install_deps=install_deps, **({"progress": progress} if progress else {})
        )
    return download_and_apply_github_update(
        download_url, token=token, install_deps=install_deps, **({"progress": progress} if progress else {})
    )


def deploy_github_release(
    download_url: str,
    tag_name: str,
    token: str | None = None,
    install_deps: bool = False,
    progress: Progress | None = None,
) -> dict:
    """Installiert Releases in Git-Checkouts per Tag, sonst als geschuetztes ZIP-Overlay."""
    if is_docker():
        return {"success": False, "message": DOCKER_MESSAGE}

    if not _valid_ref(tag_name):
        return {"success": False, "message": "Ungültiger Tag-Name"}
    if is_git_checkout():
        return git_update_release(
            tag_name, token=token, install_deps=install_deps, **({"progress": progress} if progress else {})
        )
    return download_and_apply_github_update(
        download_url, token=token, install_deps=install_deps, **({"progress": progress} if progress else {})
    )


def download_and_apply_github_update(
    download_url: str,
    token: str | None = None,
    install_deps: bool = False,
    progress: Progress | None = None,
) -> dict:
    """Lädt das Release-/Branch-ZIP von GitHub herunter und spielt es ein.

    Folgt Weiterleitungen (zipball_url leitet auf CDN um). Schreibt in eine temporäre
    Datei, ruft apply_update() auf und löscht die Datei anschließend.
    """
    if is_docker():
        return {"success": False, "message": DOCKER_MESSAGE}

    import urllib.request as _req

    tmp_path: Path | None = None
    try:
        request = _req.Request(download_url, headers=_github_headers(token))
        with _req.urlopen(request, timeout=300) as resp:
            data = resp.read()

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        if progress:
            return apply_update(tmp_path, install_deps=install_deps, progress=progress)
        return apply_update(tmp_path, install_deps=install_deps)
    except Exception as exc:
        return {"success": False, "message": f"Download fehlgeschlagen: {exc}"}
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
