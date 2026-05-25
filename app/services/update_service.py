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
            # Strukturprüfung
            has_app = any(n.startswith("app/") or n.startswith("app\\") for n in names)
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


def apply_update(zip_path: Path, expected_sha256: str | None = None) -> dict:
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

    # Migrationen ausführen
    migrations_applied = _run_migrations()

    # Gunicorn graceful reload (SIGHUP)
    reloaded = _reload_server()

    return {
        "success": True,
        "message": "Update erfolgreich eingespielt",
        "files_updated": len(files_updated),
        "files_skipped": len(skipped),
        "migrations_applied": migrations_applied,
        "server_reloaded": reloaded,
    }


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
            os.kill(pid, signal.SIGHUP)
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
