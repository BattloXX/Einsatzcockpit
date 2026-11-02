"""Dauerhafter Update-Status; nur die lokale CLI führt Updates aus."""

import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.config import settings
from app.core.audit import write_audit
from app.db import SessionLocal
from app.services import backup_service as bs
from app.services import update_service as us

FINAL = {"success", "failed", "unconfirmed"}
STEPS = ("backup", "code", "dependencies", "migration", "reload")
STEP_LABELS = {
    "backup": "Sicherung",
    "code": "Code einspielen",
    "dependencies": "Abhängigkeiten",
    "migration": "Datenbank-Migration",
    "reload": "Neustart",
}
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def directory() -> Path:
    return us.APP_ROOT / "app_storage" / "update"


@contextmanager
def _guard():
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".status.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _private_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _save(status: dict) -> None:
    _private_json(directory() / "status.json", status)


def _read() -> dict | None:
    try:
        return json.loads((directory() / "status.json").read_text())
    except OSError, ValueError:
        return None


def _alive(pid: int | None) -> bool:
    if not pid or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        try:
            # Ein noch nicht vom Elternprozess abgeholter Exit ist kein lebender Job.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return fields[0] not in {"Z", "X"}
        except FileNotFoundError:
            return False
        except (OSError, IndexError):
            return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _clean(value, token: str | None = None):
    if isinstance(value, dict):
        return {
            key: _clean(item, token)
            for key, item in value.items()
            if not re.search(r"token|password|secret", key, re.I)
        }
    if isinstance(value, list):
        return [_clean(item, token) for item in value]
    if isinstance(value, str):
        value = us._scrub(value, token)
        value = re.sub(r"(https?://|mysql[^:]*://)[^\s/@]+:[^\s/@]+@", r"\1***@", value)
        return re.sub(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+", "***", value)
    return value


def _complete(status: dict, state: str, message: str, *, audit: bool = True) -> None:
    status.update(state=state, message=message, finished_at=_now())
    status["result"]["success"] = state == "success"
    status["result"]["message"] = message
    for step in status["steps"]:
        if step["status"] == "running":
            step.update(status="success" if state == "success" else "failed", finished_at=_now())
    _save(status)
    if state == "success":
        _save_installed(status)
    if audit:
        _audit(status)


def _save_installed(status: dict) -> None:
    """Nur bestätigte Release-Installationen als Versionsquelle speichern."""
    if status.get("kind") != "release":
        return
    try:
        sha = None
        if us.is_git_checkout():
            rc, head, _ = us._run_git(["rev-parse", "HEAD"], timeout=2)
            if not rc:
                sha = head or None
        _private_json(directory() / "installed.json", {
            "kind": "release", "tag": status["ziel"], "sha": sha, "installed_at": _now(),
        })
    except Exception:
        logger.warning("Installierte Release-Version konnte nicht gespeichert werden")


def _audit(status: dict) -> None:
    if status.get("audited"):
        return
    try:
        with SessionLocal() as db:
            write_audit(
                db,
                "system.update_job",
                user_id=status["user_id"],
                payload={key: status[key] for key in ("job_id", "kind", "ziel", "state")},
            )
            deployed_sha = status.get("expected_sha") or status.get("deployed_sha")
            if status["state"] == "success" and status["kind"] == "branch" and deployed_sha:
                from app.routers.ui_settings import _save_deployed_ref

                _save_deployed_ref(db, status["user_id"], status["ziel"], deployed_sha)
            db.commit()
        status["audited"] = True
        _save(status)
    except Exception:
        logger.warning("Update-Abschluss konnte nicht im Audit gespeichert werden")


def get_status() -> dict | None:
    if not (directory() / "status.json").exists():
        return None
    with _guard():
        status = _read()
        if status and status["state"] in {"queued", "running"} and not _alive(status.get("pid")):
            _complete(status, "failed", "Prozess unerwartet beendet")
            _cleanup(status["job_id"])
        return status


def start_job(
    kind: str,
    ziel: str,
    user_id: int,
    *,
    zip_path: Path | None = None,
    expected_sha256: str | None = None,
    install_deps: bool = False,
) -> dict:
    """Vorprüfen und detachten; Token werden ausschließlich im Kind geladen."""
    if us.is_docker():
        raise ValueError(us.DOCKER_MESSAGE)
    if kind not in {"zip", "release", "branch"} or (kind != "zip" and not us._valid_ref(ziel)):
        raise ValueError("Ungültiges Update-Ziel")
    if expected_sha256 and not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("Ungültige SHA256-Prüfsumme")
    if kind == "zip":
        if zip_path is None:
            raise ValueError("ZIP-Datei fehlt")
        valid, message = us.validate_zip(zip_path)
        if not valid:
            raise ValueError(message)
        if expected_sha256 and us.compute_sha256(zip_path).lower() != expected_sha256.lower():
            raise ValueError("SHA256-Prüfsumme stimmt nicht überein")
    with _guard():
        previous = _read()
        if previous and previous["state"] not in FINAL:
            if previous["state"] in {"restarting", "verifying"} or _alive(previous.get("pid")):
                raise ValueError("Update bereits in Arbeit")
            _complete(previous, "failed", "Prozess unerwartet beendet")
            _cleanup(previous["job_id"])
        with us._update_lock() as acquired:
            if not acquired:
                raise ValueError("Update bereits in Arbeit")
        job_id = uuid.uuid4().hex
        status = {
            "job_id": job_id,
            "state": "queued",
            "kind": kind,
            "ziel": ziel,
            "user_id": user_id,
            "pid": None,
            "server_pid": os.getpid(),
            "started_at": _now(),
            "finished_at": None,
            "message": "Update eingereiht",
            "result": {},
            "steps": [{"name": name, "status": "queued", "started_at": None, "finished_at": None} for name in STEPS],
        }
        root = directory()
        try:
            if zip_path:
                fd = os.open(root / f"{job_id}.zip", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as target, zip_path.open("rb") as source:
                    shutil.copyfileobj(source, target)
            _private_json(
                root / f"{job_id}.params.json",
                {
                    "expected_sha256": expected_sha256,
                    "install_deps": install_deps,
                },
            )
            _save(status)
            python = us.APP_ROOT / ".venv" / "bin" / "python"
            fd = os.open(root / f"{job_id}.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as log, open(os.devnull, "rb") as stdin:
                process = subprocess.Popen(
                    [str(python) if python.exists() else sys.executable, "-m", "app.cli", "update-run", job_id],
                    cwd=str(us.APP_ROOT),
                    start_new_session=True,
                    stdin=stdin,
                    stdout=log,
                    stderr=log,
                    close_fds=True,
                )
            status["pid"] = process.pid
            _save(status)
        except Exception:
            _complete(status, "failed", "Update-Prozess konnte nicht gestartet werden")
            _cleanup(job_id)
        return status


def _cleanup(job_id: str) -> None:
    for suffix in ("zip", "params.json"):
        (directory() / f"{job_id}.{suffix}").unlink(missing_ok=True)


def run_job(job_id: str) -> None:
    # Backup-CLI und Bibliotheken dürfen keine Zugangsdaten in das UI-Log drucken.
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        _run_job(job_id)


def _run_job(job_id: str) -> None:
    """Lokaler CLI-Einstieg; vor Restart alles dauerhaft schreiben."""
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Ungültige Job-ID")
    with _guard():
        status = _read()
        if not status or status["job_id"] != job_id or status["state"] != "queued":
            return
        params_path = directory() / f"{job_id}.params.json"
        try:
            params = json.loads(params_path.read_text())
            if not isinstance(params["install_deps"], bool):
                raise ValueError("Ungültige Parameter")
        except OSError, ValueError, KeyError, TypeError:
            _complete(status, "failed", "Update-Parameter konnten nicht gelesen werden")
            _cleanup(job_id)
            return
        params_path.unlink()
        status.update(state="running", pid=os.getpid(), message="Update läuft")
        _save(status)
    token = None
    # Unterprozesse liefern Ausgaben an den Service zurück. Keine fremden Ausgaben
    # mit möglichen DB-Zugangsdaten in das für die UI bestimmte Log übernehmen.
    previous_logging_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)

    def progress(name: str, state: str) -> None:
        with _guard():
            step = next(item for item in status["steps"] if item["name"] == name)
            step["status"] = state
            step["started_at" if state == "running" else "finished_at"] = _now()
            label = "läuft" if state == "running" else "abgeschlossen" if state == "success" else "fehlgeschlagen"
            status["message"] = f"{STEP_LABELS[name]}: {label}"
            _save(status)
        with (directory() / f"{job_id}.log").open("a") as log:
            log.write(f"{_now()} {name}: {state}\n")

    try:
        with SessionLocal() as db:
            token = us.get_github_token(db)
        if status["kind"] == "zip":
            result = us.apply_update(
                directory() / f"{job_id}.zip",
                expected_sha256=params["expected_sha256"],
                install_deps=params["install_deps"],
                progress=progress,
            )
        elif status["kind"] == "branch":
            if not us.is_git_checkout():
                branch_info = us.check_github_branch(status["ziel"], token=token)
                if not branch_info.get("sha"):
                    raise ValueError("Branch nicht verfügbar")
                status["deployed_sha"] = branch_info["sha"]
            result = us.deploy_github_branch(
                status["ziel"], "", token=token, install_deps=params["install_deps"], progress=progress
            )
        else:
            # Release im Kind erneut prüfen, niemals einen beliebigen Download-URL persistieren.
            data = us._github_json(
                f"https://api.github.com/repos/{us.GITHUB_REPO}/releases/tags/{status['ziel']}", token
            )
            if data.get("draft") or data.get("tag_name") != status["ziel"]:
                raise ValueError("Release nicht verfügbar")
            download_url = next(
                (
                    asset["browser_download_url"]
                    for asset in data.get("assets", [])
                    if asset.get("name", "").endswith(".zip")
                ),
                data.get("zipball_url", ""),
            )
            result = us.deploy_github_release(
                download_url,
                status["ziel"],
                token=token,
                install_deps=params["install_deps"],
                progress=progress,
            )
        with _guard():
            status["result"] = _clean(result, token)
            if not result.get("success"):
                _complete(status, "failed", status["result"].get("message", "Update fehlgeschlagen"))
                return
            sha = None
            if us.is_git_checkout():
                rc, sha, _ = us._run_git(["rev-parse", "HEAD"])
                if rc or not sha:
                    _complete(
                        status,
                        "failed",
                        "Git-Zielstand nicht lesbar. Code und Migration sind bereits eingespielt; "
                        "Neustart bitte manuell prüfen.",
                    )
                    return
            status.update(
                state="restarting",
                restart_started_at=_now(),
                restart_boot_ticks=int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf("SC_CLK_TCK")),
                expected_head=bs.aktueller_alembic_head(),
                expected_sha=sha,
                expected_version=us.get_current_version(),
                message="Neustart wird ausgeführt",
            )
            _cleanup(job_id)
            _save(status)
        progress("reload", "running")
        if not us._reload_server():
            with _guard():
                _complete(status, "failed", us.RELOAD_ERROR + ". Code und Migration sind bereits eingespielt.")
        # Bei SIGHUP beendet sich nur dieser Job. Die neue App bestätigt den Erfolg.
    except Exception:
        with _guard():
            _complete(status, "failed", "Update fehlgeschlagen. Serverprotokoll und Backup prüfen.")
    finally:
        _cleanup(job_id)
        logging.disable(previous_logging_level)


def _server_started_after(restart_at: str, boot_ticks: int | None = None) -> bool:
    try:
        # /proc/<pid>/stat: Feld 22; Prozessname kann Leerzeichen enthalten.
        fields = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
        started_ticks = int(fields[19])
        if boot_ticks is not None:
            # Gleiche Boot-Uhr: keine Rundungsfehler durch das sekundengenaue btime.
            return started_ticks >= boot_ticks
        ticks = started_ticks / os.sysconf("SC_CLK_TCK")
        boot = next(
            int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime ")
        )
        return boot + ticks >= datetime.fromisoformat(restart_at).timestamp()
    except OSError, ValueError, IndexError, StopIteration:
        return False


def _verification_matches(status: dict) -> bool:
    if os.getpid() == status["server_pid"]:
        return False
    if not _server_started_after(status["restart_started_at"], status.get("restart_boot_ticks")):
        return False
    head = bs.aktueller_alembic_head()
    with SessionLocal() as db:
        revisions = set(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if revisions != {head} or head != status.get("expected_head"):
        return False
    if status.get("expected_sha"):
        rc, sha, _ = us._run_git(["rev-parse", "HEAD"])
        if rc or sha != status["expected_sha"]:
            return False
    return True


def verify_after_restart(*, audit: bool = True) -> None:
    """Best-effort-Nachprüfung; Startup nutzt nur DB-Leserechte, Polling auditiert."""
    if not (directory() / "status.json").exists():
        return
    try:
        with _guard():
            status = _read()
            if not status:
                return
            if status["state"] in FINAL:
                if audit:
                    _audit(status)
                return
            if status["state"] not in {"restarting", "verifying"}:
                return
            status["state"] = "verifying"
            try:
                matches = _verification_matches(status)
            except Exception:
                matches = False
            if matches:
                status["result"]["server_reloaded"] = True
                _complete(status, "success", "Update und Neustart bestätigt", audit=audit)
            elif (
                datetime.now(UTC) - datetime.fromisoformat(status["restart_started_at"])
            ).total_seconds() >= settings.UPDATE_VERIFY_TIMEOUT_S:
                _complete(
                    status,
                    "unconfirmed",
                    "Neustart nicht bestätigt. Prozess, Git-Stand und Alembic-Revision bitte manuell prüfen.",
                    audit=audit,
                )
            else:
                status["message"] = "Neustart und Datenbank werden geprüft"
                _save(status)
    except Exception:
        logger.warning("Update-Nachprüfung derzeit nicht möglich")


def public_status() -> dict | None:
    status = get_status()
    if not status:
        return None
    try:
        lines = (directory() / f"{status['job_id']}.log").read_text(errors="replace").splitlines()[-40:]
    except OSError:
        lines = []
    return _clean({**status, "log": "\n".join(lines)})
