"""In-app ZIP-Update-Mechanismus.

Ablauf:
1. system_admin lädt ein Release-ZIP hoch (POST /admin/system/update)
2. ZIP wird strukturell validiert (muss app/, pyproject.toml enthalten)
3. Optional: erwarteter SHA256 wird gegen die Upload-Datei geprüft (Manipulationsschutz)
4. Inhalt wird sicher (ohne Zip-Slip) in ein temporäres Verzeichnis extrahiert
5. Kritische Dateien (.env, alembic/versions/, static/img/uploads/) bleiben unangetastet
6. Neue Dateien werden über die bestehende Installation kopiert
7. alembic upgrade head wird ausgeführt
8. Gunicorn erhält SIGHUP (graceful reload) oder systemctl restart
"""

import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).parent.parent.parent  # Projektverzeichnis

# Dateien und Verzeichnisse, die beim Update NIEMALS überschrieben werden
PROTECTED_PATHS = {
    ".env",
    ".env.local",
    "alembic/versions",     # eigene Migrationen bleiben
    "app/static/img/uploads",  # hochgeladene Logos etc.
}


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


def apply_update(zip_path: Path, expected_sha256: str | None = None, install_deps: bool = False) -> dict:
    """
    Extrahiert das ZIP und kopiert Dateien über die bestehende Installation.

    Sicherheit:
    - validate_zip prüft strukturell und gegen Zip-Slip / Symlinks.
    - Falls expected_sha256 angegeben ist, muss er exakt mit dem Datei-Hash übereinstimmen.
    - Beim Extrahieren wird jeder Zielpfad mit os.path.commonpath gegen das Tmp-Verzeichnis
      verglichen, sodass auch kaputt geprüfte ZIPs nichts außerhalb schreiben können.
    """
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

    files_updated: list[str] = []
    skipped: list[str] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir).resolve()

        # Sichere Extraktion: jeder Member einzeln gegen Zip-Slip prüfen
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                # Ziel-Pfad berechnen
                target_path = (tmp / member.filename).resolve()
                # Muss innerhalb des Tmp-Verzeichnisses liegen
                try:
                    common = Path(os.path.commonpath([str(tmp), str(target_path)]))
                except ValueError:
                    return {"success": False, "message": f"Unsicherer Pfad abgelehnt: {member.filename}"}
                if common != tmp:
                    return {"success": False, "message": f"Zip-Slip-Versuch abgelehnt: {member.filename}"}
                # Symlinks ablehnen
                if member.external_attr >> 16 & 0o170000 == 0o120000:
                    return {"success": False, "message": f"Symlink im ZIP nicht erlaubt: {member.filename}"}
                # Extrahieren
                zf.extract(member, tmp)

        # Falls das ZIP einen Root-Ordner hat (z.B. release-2.0.0/), diesen als Basis nehmen
        entries = list(tmp.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            src_root = entries[0]
        else:
            src_root = tmp

        # Dateien kopieren
        for src_file in src_root.rglob("*"):
            if src_file.is_dir():
                continue
            if src_file.is_symlink():
                # Defensive: sollte schon vorher rausgefiltert sein
                continue
            rel = src_file.relative_to(src_root).as_posix()
            if _is_protected(rel):
                skipped.append(rel)
                continue
            dst = APP_ROOT / rel
            # Zusätzlicher Schutz: dst muss unter APP_ROOT liegen
            try:
                dst_resolved = dst.resolve()
                common = Path(os.path.commonpath([str(APP_ROOT.resolve()), str(dst_resolved)]))
            except ValueError:
                skipped.append(rel)
                continue
            if common != APP_ROOT.resolve():
                skipped.append(rel)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst)
            files_updated.append(rel)

    # Optional: Python-Abhängigkeiten nachziehen (z. B. neue pyproject-Dependencies)
    deps_installed = _run_pip_install() if install_deps else "übersprungen"

    # Migrationen ausführen
    migrations_applied = _run_migrations()

    # Gunicorn graceful reload (SIGHUP)
    reloaded = _reload_server()

    return {
        "success": True,
        "message": "Update erfolgreich eingespielt",
        "files_updated": len(files_updated),
        "files_skipped": len(skipped),
        "deps_installed": deps_installed,
        "migrations_applied": migrations_applied,
        "server_reloaded": reloaded,
    }


def _run_pip_install() -> str:
    """Installiert die pyproject-Abhängigkeiten ins venv (pip install -e .).

    Nötig, wenn ein Update neue Dependencies mitbringt (z. B. pdf2image).
    Gibt "OK" oder eine gekürzte Fehlermeldung zurück.
    """
    python = APP_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python")  # Fallback: System-Python (Dev)
    try:
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "-e", ".", "--no-input", "--quiet"],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            return "OK"
        return f"Fehler: {result.stderr[-500:]}"
    except Exception as e:
        return f"Fehler: {e}"


def _run_migrations() -> str:
    """Führt alembic upgrade head aus. Gibt Ausgabe oder Fehlermeldung zurück."""
    python = APP_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python")  # Fallback: System-Python
    try:
        result = subprocess.run(
            [str(python), "-m", "alembic", "upgrade", "head"],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return "OK"
        return f"Fehler: {result.stderr[:500]}"
    except Exception as e:
        return f"Fehler: {e}"


def _reload_server() -> bool:
    """Sendet SIGHUP an den Gunicorn-Master-Prozess (graceful reload)."""
    pidfile = APP_ROOT / "gunicorn.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, signal.SIGHUP)  # type: ignore[attr-defined]
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    # Fallback: systemctl restart (benötigt sudo-Rechte via sudoers)
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "einsatzleiter"],
            timeout=10,
            capture_output=True,
        )
        return True
    except Exception:
        return False


def get_current_version() -> str:
    """Liest die aktuelle Version aus pyproject.toml."""
    try:
        content = (APP_ROOT / "pyproject.toml").read_text()
        for line in content.splitlines():
            if line.strip().startswith("version"):
                return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unbekannt"


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
        if prerelease:
            # Alle Releases abrufen (sortiert nach Datum absteigend) – erster Treffer ist neuester
            releases = _github_json(
                f"https://api.github.com/repos/{repo}/releases?per_page=10", token
            )
            data = releases[0] if releases else {}
        else:
            data = _github_json(
                f"https://api.github.com/repos/{repo}/releases/latest", token
            )

        tag = data.get("tag_name", "").lstrip("v")
        assets = data.get("assets", [])

        # Server-ZIP: eigenes Release-Asset bevorzugen; Fallback: GitHub-Quellcode-ZIP
        zip_url = next((a["browser_download_url"] for a in assets if a["name"].endswith(".zip")), None)
        download_url = zip_url or data.get("zipball_url")

        # Android-APK: erstes .apk-Asset im Release (falls vorhanden)
        apk_asset = next((a for a in assets if a["name"].endswith(".apk")), None)

        return {
            "current_version": current,
            "latest_tag": tag,
            "download_url": download_url,
            "has_update": bool(tag) and tag != current,
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
            "download_url": None,
            "has_update": False,
            "is_prerelease": False,
            "error": str(exc)[:200],
        }


def list_github_branches(repo: str = GITHUB_REPO, token: str | None = None) -> list[dict]:
    """Listet Branches des Repos (Name + Commit-SHA). Leere Liste bei Fehlern."""
    try:
        branches = _github_json(
            f"https://api.github.com/repos/{repo}/branches?per_page=50", token
        )
        return [
            {"name": b.get("name", ""), "sha": (b.get("commit") or {}).get("sha", "")[:7]}
            for b in branches
            if b.get("name")
        ]
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
        data = _github_json(
            f"https://api.github.com/repos/{repo}/commits/{branch}", token
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
            "download_url": f"https://api.github.com/repos/{repo}/zipball/{branch}",
        }
    except Exception as exc:
        return {"branch": branch, "sha": None, "error": str(exc)[:200]}


def is_git_checkout() -> bool:
    """True, wenn die Installation ein echtes Git-Arbeitsverzeichnis ist."""
    return (APP_ROOT / ".git").exists()


def _scrub(text: str, token: str | None) -> str:
    """Entfernt ein evtl. in Git-Fehlermeldungen enthaltenes Token."""
    if token and token in text:
        text = text.replace(token, "***")
    return text


def _run_git(args: list[str], timeout: int = 300) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(APP_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:  # git fehlt / Timeout
        return 1, "", str(exc)


def git_update(branch: str, token: str | None = None, install_deps: bool = False) -> dict:
    """Aktualisiert eine Git-Installation exakt auf origin/<branch>.

    Nutzt `git fetch` + `git reset --hard` → der Arbeitsbaum entspricht danach
    BYTE-genau dem Remote-Stand (inkl. gelöschter/umbenannter Dateien, die das
    ZIP-Overlay liegen ließ) und ist SAUBER: ein anschließendes `git pull` /
    `git status` auf der Konsole läuft konfliktfrei weiter (kein "local changes
    would be overwritten" mehr). Git-ignorierte bzw. nicht getrackte Dateien
    (.env, DBs, Uploads, app_storage) bleiben unberührt — reset --hard fasst nur
    getrackte Dateien an, `git clean` wird bewusst NICHT ausgeführt.
    """
    if not is_git_checkout():
        return {"success": False, "message": "Kein Git-Arbeitsverzeichnis – bitte ZIP-Update verwenden."}

    remote = f"https://github.com/{GITHUB_REPO}.git"
    if token:
        remote = f"https://x-access-token:{token}@github.com/{GITHUB_REPO}.git"

    before = _run_git(["rev-parse", "HEAD"])[1]

    rc, _out, err = _run_git(["fetch", "--force", remote, branch])
    if rc != 0:
        return {"success": False, "message": f"git fetch fehlgeschlagen: {_scrub(err, token)[:300]}"}

    rc, _out, err = _run_git(["reset", "--hard", "FETCH_HEAD"])
    if rc != 0:
        return {"success": False, "message": f"git reset fehlgeschlagen: {_scrub(err, token)[:300]}"}

    # Lokalen Branch-Namen auf den Zielbranch setzen (kein detached HEAD), damit
    # die Konsole danach denselben Branch trackt.
    _run_git(["checkout", "-B", branch])

    after = _run_git(["rev-parse", "HEAD"])[1]
    changed = 0
    if before and after and before != after:
        rc2, out2, _ = _run_git(["diff", "--name-only", before, after])
        if rc2 == 0:
            changed = len([ln for ln in out2.splitlines() if ln.strip()])

    deps_installed = _run_pip_install() if install_deps else "übersprungen"
    migrations_applied = _run_migrations()
    reloaded = _reload_server()

    return {
        "success": True,
        "message": f"Git-Update auf {branch} @ {after[:7]} eingespielt" if after else "Git-Update eingespielt",
        "files_updated": changed,
        "files_skipped": 0,
        "deps_installed": deps_installed,
        "migrations_applied": migrations_applied,
        "server_reloaded": reloaded,
        "via": "git",
    }


def deploy_github_branch(
    branch: str, download_url: str, token: str | None = None, install_deps: bool = False,
) -> dict:
    """Einheitlicher Branch-Deploy: bevorzugt Git (sauberer, konsistent zur
    Konsole); nur ohne Git-Checkout Fallback auf das Zipball-Overlay.

    Damit machen Web-Update und `git pull` auf der Konsole DASSELBE und stören
    sich nicht mehr gegenseitig.
    """
    if is_git_checkout():
        return git_update(branch, token=token, install_deps=install_deps)
    return download_and_apply_github_update(download_url, token=token, install_deps=install_deps)


def download_and_apply_github_update(
    download_url: str, token: str | None = None, install_deps: bool = False,
) -> dict:
    """Lädt das Release-/Branch-ZIP von GitHub herunter und spielt es ein.

    Folgt Weiterleitungen (zipball_url leitet auf CDN um). Schreibt in eine temporäre
    Datei, ruft apply_update() auf und löscht die Datei anschließend.
    """
    import urllib.request as _req

    tmp_path: Path | None = None
    try:
        request = _req.Request(download_url, headers=_github_headers(token))
        with _req.urlopen(request, timeout=300) as resp:
            data = resp.read()

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)

        return apply_update(tmp_path, install_deps=install_deps)
    except Exception as exc:
        return {"success": False, "message": f"Download fehlgeschlagen: {exc}"}
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
