"""Off-Site-Upload der Backups (SFTP/SCP/rsync/FTP/FTPS/rclone).

Ergaenzt backup_service um die zweite Haelfte der 3-2-1-Regel: die lokal erzeugten
Dumps zeitgesteuert an einen entfernten Ort schieben. Aufgerufen automatisch am Ende
von `app.cli backup` (wenn BACKUP_REMOTE_ENABLED) oder separat via `app.cli backup-upload`.

Design wie backup_service: die Kommandozeilen-Bauer sind REINE, unit-getestete
Funktionen; upload() dispatcht und fuehrt aus (Runner/FTP-Factory injizierbar -> ohne
Netz testbar). Passwoerter landen NIE in einem Argv:
- SSH-Protokolle (sftp/scp/rsync) nutzen Key-Auth (BatchMode, kein Passwort-Prompt).
- FTP/FTPS nutzt ftplib; das Passwort geht ueber den Protokoll-Login, nicht die CLI.
"""
from __future__ import annotations

import ftplib
import logging
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("einsatzleiter.backup.remote")

SSH_PROTOKOLLE = ("sftp", "scp", "rsync")
FTP_PROTOKOLLE = ("ftp", "ftps")
ALLE_PROTOKOLLE = (*SSH_PROTOKOLLE, *FTP_PROTOKOLLE, "rclone")


@dataclass(frozen=True)
class RemoteConfig:
    protocol: str
    host: str
    port: int
    user: str
    password: str
    key: str
    path: str
    ssh_strict: str = "accept-new"
    rclone_remote: str = ""
    scp_bin: str = "scp"
    sftp_bin: str = "sftp"
    rsync_bin: str = "rsync"
    rclone_bin: str = "rclone"

    def ziel_beschreibung(self) -> str:
        """Menschenlesbares Ziel fuer Logs (ohne Geheimnisse)."""
        if self.protocol == "rclone":
            return f"rclone {self.rclone_remote}{self.path}"
        wer = f"{self.user}@" if self.user else ""
        return f"{self.protocol}://{wer}{self.host}:{self.port or '-'}{self.path or '/'}"


def config_pruefen(cfg: RemoteConfig) -> None:
    """Wirft ValueError bei unvollstaendiger/ungueltiger Remote-Konfiguration."""
    if cfg.protocol not in ALLE_PROTOKOLLE:
        raise ValueError(f"Unbekanntes Remote-Protokoll: {cfg.protocol!r} "
                         f"(erlaubt: {', '.join(ALLE_PROTOKOLLE)})")
    if cfg.protocol == "rclone":
        if not cfg.rclone_remote:
            raise ValueError("rclone: BACKUP_REMOTE_RCLONE_REMOTE fehlt (z. B. 'offsite:').")
        return
    if not cfg.host:
        raise ValueError(f"{cfg.protocol}: BACKUP_REMOTE_HOST fehlt.")
    if cfg.protocol in FTP_PROTOKOLLE and not cfg.user:
        raise ValueError(f"{cfg.protocol}: BACKUP_REMOTE_USER fehlt.")


def _ssh_optionen(cfg: RemoteConfig) -> list[str]:
    """Gemeinsame ssh-Optionen fuer scp/sftp (Port/Key/Hostkey, non-interaktiv)."""
    opts = ["-o", f"StrictHostKeyChecking={cfg.ssh_strict}", "-o", "BatchMode=yes"]
    if cfg.port:
        opts += ["-o", f"Port={cfg.port}"]
    if cfg.key:
        opts += ["-i", cfg.key]
    return opts


def _ziel(cfg: RemoteConfig) -> str:
    """user@host:pfad fuer scp/rsync."""
    wer = f"{cfg.user}@" if cfg.user else ""
    return f"{wer}{cfg.host}:{cfg.path}"


def build_scp_argv(cfg: RemoteConfig, dateien: Sequence[Path]) -> list[str]:
    return [cfg.scp_bin, *_ssh_optionen(cfg), *[str(d) for d in dateien], _ziel(cfg)]


def build_sftp_argv(cfg: RemoteConfig, batch_datei: Path) -> list[str]:
    """sftp im Batch-Modus (-b): non-interaktiv, Befehle aus batch_datei."""
    wer = f"{cfg.user}@" if cfg.user else ""
    return [cfg.sftp_bin, *_ssh_optionen(cfg), "-b", str(batch_datei), f"{wer}{cfg.host}"]


def sftp_batch_text(cfg: RemoteConfig, dateien: Sequence[Path]) -> str:
    """Batch-Skript fuer sftp -b: ins Zielverzeichnis wechseln, Dateien hochladen."""
    zeilen = []
    if cfg.path:
        zeilen.append(f'cd "{cfg.path}"')
    zeilen += [f'put "{d}"' for d in dateien]
    zeilen.append("bye")
    return "\n".join(zeilen) + "\n"


def build_rsync_argv(cfg: RemoteConfig, dateien: Sequence[Path]) -> list[str]:
    ssh_cmd = "ssh " + " ".join(_ssh_optionen(cfg))
    return [cfg.rsync_bin, "-az", "--partial", "-e", ssh_cmd,
            *[str(d) for d in dateien], _ziel(cfg)]


def build_rclone_argv(cfg: RemoteConfig, quelle: Path) -> list[str]:
    """rclone copy <quelle> <remote:pfad> — quelle ist Datei ODER Verzeichnis."""
    ziel = f"{cfg.rclone_remote}{cfg.path}"
    return [cfg.rclone_bin, "copy", str(quelle), ziel]


def ftp_upload(
    cfg: RemoteConfig,
    dateien: Sequence[Path],
    factory: Callable[[], ftplib.FTP] | None = None,
) -> None:
    """Laedt Dateien per FTP/FTPS hoch (ftplib). factory ist fuer Tests injizierbar."""
    if factory is None:
        factory = ftplib.FTP_TLS if cfg.protocol == "ftps" else ftplib.FTP
    ftp = factory()
    try:
        ftp.connect(cfg.host, cfg.port or 21)
        ftp.login(cfg.user, cfg.password)
        if cfg.protocol == "ftps" and isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()  # Datenkanal verschluesseln
        if cfg.path:
            ftp.cwd(cfg.path)
        for d in dateien:
            with open(d, "rb") as fh:
                ftp.storbinary(f"STOR {Path(d).name}", fh)
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 — Verbindung bestmoeglich schliessen
            ftp.close()


# Runner-Signatur wie subprocess.run (fuer Tests austauschbar).
Runner = Callable[..., subprocess.CompletedProcess]


def upload(
    cfg: RemoteConfig,
    dateien: Sequence[Path],
    backup_dir: Path,
    runner: Runner = subprocess.run,
    ftp_factory: Callable[[], ftplib.FTP] | None = None,
) -> None:
    """Dispatcht auf das konfigurierte Protokoll und fuehrt den Upload aus.

    Wirft bei Fehlern (ungueltige Config, Transfer-Fehler) eine Exception, damit
    der aufrufende Backup-Lauf einen Off-Site-Fehler sichtbar macht.
    """
    config_pruefen(cfg)
    if not dateien and cfg.protocol != "rclone":
        logger.info("Remote-Upload: keine Dateien zu uebertragen.")
        return
    if cfg.protocol == "ftp":
        logger.warning("Remote-Upload: FTP ist UNVERSCHLUESSELT — ftps/sftp bevorzugen.")

    if cfg.protocol in FTP_PROTOKOLLE:
        ftp_upload(cfg, dateien, ftp_factory)
        return

    if cfg.protocol == "rclone":
        # rclone kopiert das ganze Backup-Verzeichnis (dedupliziert serverseitig).
        argv = build_rclone_argv(cfg, backup_dir)
    elif cfg.protocol == "scp":
        argv = build_scp_argv(cfg, dateien)
    elif cfg.protocol == "rsync":
        argv = build_rsync_argv(cfg, dateien)
    elif cfg.protocol == "sftp":
        return _sftp_ausfuehren(cfg, dateien, runner)
    else:  # pragma: no cover — von config_pruefen abgedeckt
        raise ValueError(f"Unbehandeltes Protokoll: {cfg.protocol}")

    ergebnis = runner(argv, capture_output=True)
    if ergebnis.returncode != 0:
        stderr = (ergebnis.stderr or b"")
        text = stderr.decode("utf-8", "ignore") if isinstance(stderr, bytes) else str(stderr)
        raise RuntimeError(f"{cfg.protocol}-Upload fehlgeschlagen: {text[:600]}")


def _sftp_ausfuehren(cfg: RemoteConfig, dateien: Sequence[Path], runner: Runner) -> None:
    """Schreibt das sftp-Batch-Skript temporaer und fuehrt sftp -b aus."""
    with tempfile.NamedTemporaryFile("w", suffix=".sftp", delete=False, encoding="utf-8") as tf:
        tf.write(sftp_batch_text(cfg, dateien))
        batch = Path(tf.name)
    try:
        argv = build_sftp_argv(cfg, batch)
        ergebnis = runner(argv, capture_output=True)
        if ergebnis.returncode != 0:
            stderr = ergebnis.stderr or b""
            text = stderr.decode("utf-8", "ignore") if isinstance(stderr, bytes) else str(stderr)
            raise RuntimeError(f"sftp-Upload fehlgeschlagen: {text[:600]}")
    finally:
        batch.unlink(missing_ok=True)


def config_aus_settings() -> RemoteConfig:
    """Baut die RemoteConfig aus den globalen Settings."""
    from app.config import settings
    return RemoteConfig(
        protocol=(settings.BACKUP_REMOTE_PROTOCOL or "sftp").strip().lower(),
        host=settings.BACKUP_REMOTE_HOST.strip(),
        port=settings.BACKUP_REMOTE_PORT,
        user=settings.BACKUP_REMOTE_USER.strip(),
        password=settings.BACKUP_REMOTE_PASSWORD,
        key=settings.BACKUP_REMOTE_KEY.strip(),
        path=settings.BACKUP_REMOTE_PATH.strip(),
        ssh_strict=(settings.BACKUP_REMOTE_SSH_STRICT or "accept-new").strip(),
        rclone_remote=settings.BACKUP_REMOTE_RCLONE_REMOTE.strip(),
        scp_bin=settings.BACKUP_REMOTE_SCP_BIN,
        sftp_bin=settings.BACKUP_REMOTE_SFTP_BIN,
        rsync_bin=settings.BACKUP_REMOTE_RSYNC_BIN,
        rclone_bin=settings.BACKUP_REMOTE_RCLONE_BIN,
    )


def neueste_je_praefix(backup_dir: Path, praefixe: Sequence[str]) -> list[Path]:
    """Neueste Datei je Praefix (fuer den Upload nur der frischen Dumps)."""
    treffer: list[Path] = []
    for praefix in praefixe:
        kandidaten = sorted(backup_dir.glob(f"{praefix}-*"))
        if kandidaten:
            treffer.append(kandidaten[-1])
    return treffer
