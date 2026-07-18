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
import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("einsatzleiter.backup.remote")

SSH_PROTOKOLLE = ("sftp", "scp", "rsync")
FTP_PROTOKOLLE = ("ftp", "ftps")
ALLE_PROTOKOLLE = (*SSH_PROTOKOLLE, *FTP_PROTOKOLLE, "rclone", "graph")


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
    # Microsoft Graph (SharePoint/OneDrive) — Protokoll "graph"
    graph_tenant: str = ""
    graph_client: str = ""
    graph_secret: str = ""
    graph_drive_id: str = ""
    graph_folder: str = ""

    def ziel_beschreibung(self) -> str:
        """Menschenlesbares Ziel fuer Logs (ohne Geheimnisse)."""
        if self.protocol == "rclone":
            return f"rclone {self.rclone_remote}{self.path}"
        if self.protocol == "graph":
            return f"graph drive {self.graph_drive_id}/{self.graph_folder}"
        wer = f"{self.user}@" if self.user else ""
        return f"{self.protocol}://{wer}{self.host}:{self.port or '-'}{self.path or '/'}"

    def graph_ziel(self):
        from app.services.graph_backup_service import GraphZiel
        return GraphZiel(tenant=self.graph_tenant, client_id=self.graph_client,
                         secret=self.graph_secret, drive_id=self.graph_drive_id,
                         folder=self.graph_folder)


def config_pruefen(cfg: RemoteConfig) -> None:
    """Wirft ValueError bei unvollstaendiger/ungueltiger Remote-Konfiguration."""
    if cfg.protocol not in ALLE_PROTOKOLLE:
        raise ValueError(f"Unbekanntes Remote-Protokoll: {cfg.protocol!r} "
                         f"(erlaubt: {', '.join(ALLE_PROTOKOLLE)})")
    if cfg.protocol == "rclone":
        if not cfg.rclone_remote:
            raise ValueError("rclone: BACKUP_REMOTE_RCLONE_REMOTE fehlt (z. B. 'offsite:').")
        return
    if cfg.protocol == "graph":
        fehlend = [n for n, v in (("Tenant", cfg.graph_tenant), ("Client-ID", cfg.graph_client),
                                  ("Secret", cfg.graph_secret), ("Drive-ID", cfg.graph_drive_id))
                   if not v]
        if fehlend:
            raise ValueError("graph: fehlende Angaben: " + ", ".join(fehlend))
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

    if cfg.protocol == "graph":
        from app.services import graph_backup_service as gbs
        ziel = cfg.graph_ziel()
        for d in dateien:
            gbs.upload(ziel, Path(d))
        return

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


def config_aus_org(cfg_row: object, key_path: str = "") -> RemoteConfig:
    """Baut die RemoteConfig aus einer OrgBackupConfig-Zeile (Self-Service je Org).

    Das Passwort wird entschluesselt; ein privater SSH-Key (ssh_key_enc) wird NICHT
    hier verarbeitet, sondern vom Aufrufer via org_remote_config() in eine temporaere
    Datei materialisiert und als key_path uebergeben.
    """
    from app.core.crypto import decrypt_secret
    passwort = ""
    pw_enc = getattr(cfg_row, "password_enc", None)
    if pw_enc:
        passwort = decrypt_secret(pw_enc)
    graph_secret = ""
    gs_enc = getattr(cfg_row, "graph_client_secret_enc", None)
    if gs_enc:
        graph_secret = decrypt_secret(gs_enc)
    return RemoteConfig(
        protocol=(getattr(cfg_row, "protocol", "sftp") or "sftp").strip().lower(),
        host=(getattr(cfg_row, "host", "") or "").strip(),
        port=getattr(cfg_row, "port", 0) or 0,
        user=(getattr(cfg_row, "username", "") or "").strip(),
        password=passwort,
        key=key_path,
        path=(getattr(cfg_row, "remote_path", "") or "").strip(),
        ssh_strict=(getattr(cfg_row, "ssh_strict", "accept-new") or "accept-new").strip(),
        rclone_remote=(getattr(cfg_row, "rclone_remote", "") or "").strip(),
        graph_tenant=(getattr(cfg_row, "graph_tenant_id", "") or "").strip(),
        graph_client=(getattr(cfg_row, "graph_client_id", "") or "").strip(),
        graph_secret=graph_secret,
        graph_drive_id=(getattr(cfg_row, "graph_drive_id", "") or "").strip(),
        graph_folder=(getattr(cfg_row, "graph_folder", "") or "").strip(),
    )


@contextmanager
def org_remote_config(cfg_row: object) -> Iterator[RemoteConfig]:
    """Context-Manager: liefert eine einsatzbereite RemoteConfig einer Org.

    Materialisiert einen ggf. hinterlegten SSH-Key (ssh_key_enc) in eine temporaere
    Datei mit Rechten 0600 und raeumt sie danach wieder ab.
    """
    from app.core.crypto import decrypt_secret
    key_enc = getattr(cfg_row, "ssh_key_enc", None)
    key_datei: Path | None = None
    try:
        if key_enc:
            key_text = decrypt_secret(key_enc)
            fd, name = tempfile.mkstemp(prefix=".orgkey_", suffix=".pem")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(key_text if key_text.endswith("\n") else key_text + "\n")
            key_datei = Path(name)
            try:
                os.chmod(key_datei, 0o600)
            except OSError:
                pass
        yield config_aus_org(cfg_row, key_path=str(key_datei) if key_datei else "")
    finally:
        if key_datei is not None:
            key_datei.unlink(missing_ok=True)


def neueste_je_praefix(backup_dir: Path, praefixe: Sequence[str]) -> list[Path]:
    """Neueste Datei je Praefix (fuer den Upload nur der frischen Dumps)."""
    treffer: list[Path] = []
    for praefix in praefixe:
        kandidaten = sorted(backup_dir.glob(f"{praefix}-*"))
        if kandidaten:
            treffer.append(kandidaten[-1])
    return treffer


# ── Remote-Retention (alte Push-Archive am Ziel aufraeumen) ───────────────────

def waehle_zu_loeschen(namen: Sequence[str], keep: int) -> list[str]:
    """Waehlt die ueberzaehligen (aeltesten) Remote-Dateien aus.

    Die Archiv-Namen tragen einen sortierbaren UTC-Zeitstempel, daher genuegt
    lexikografisches Sortieren = chronologisch; die `keep` neuesten bleiben.
    """
    if keep <= 0:
        return []
    sortiert = sorted(namen)
    ueberzaehlig = len(sortiert) - keep
    return sortiert[:ueberzaehlig] if ueberzaehlig > 0 else []


def _ssh_host(cfg: RemoteConfig) -> str:
    return f"{cfg.user}@{cfg.host}" if cfg.user else cfg.host


def _remote_liste(cfg: RemoteConfig, praefix: str, runner: Runner,
                  ftp_factory: Callable[[], ftplib.FTP] | None) -> list[str]:
    """Listet die Remote-Dateinamen (Basenamen) mit gegebenem Praefix."""
    if cfg.protocol == "graph":
        from app.services import graph_backup_service as gbs
        return gbs.liste(cfg.graph_ziel(), praefix)
    if cfg.protocol in FTP_PROTOKOLLE:
        if ftp_factory is None:
            ftp_factory = ftplib.FTP_TLS if cfg.protocol == "ftps" else ftplib.FTP
        ftp = ftp_factory()
        try:
            ftp.connect(cfg.host, cfg.port or 21)
            ftp.login(cfg.user, cfg.password)
            if cfg.protocol == "ftps" and isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()
            if cfg.path:
                ftp.cwd(cfg.path)
            namen = [Path(n).name for n in ftp.nlst()]
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()
        return [n for n in namen if n.startswith(praefix)]

    if cfg.protocol == "rclone":
        argv = [cfg.rclone_bin, "lsf", f"{cfg.rclone_remote}{cfg.path}"]
    else:  # sftp/scp/rsync -> ssh
        pfad = cfg.path or "."
        argv = ["ssh", *_ssh_optionen(cfg), _ssh_host(cfg), f"ls -1 {shlex.quote(pfad)}"]
    res = runner(argv, capture_output=True)
    if res.returncode != 0:
        return []
    out = res.stdout.decode("utf-8", "ignore") if isinstance(res.stdout, bytes) else (res.stdout or "")
    return [Path(z.strip()).name for z in out.splitlines()
            if z.strip() and Path(z.strip()).name.startswith(praefix)]


def _remote_delete(cfg: RemoteConfig, namen: Sequence[str], runner: Runner,
                   ftp_factory: Callable[[], ftplib.FTP] | None) -> None:
    if not namen:
        return
    if cfg.protocol == "graph":
        from app.services import graph_backup_service as gbs
        ziel = cfg.graph_ziel()
        for n in namen:
            gbs.loesche(ziel, n)
        return
    if cfg.protocol in FTP_PROTOKOLLE:
        if ftp_factory is None:
            ftp_factory = ftplib.FTP_TLS if cfg.protocol == "ftps" else ftplib.FTP
        ftp = ftp_factory()
        try:
            ftp.connect(cfg.host, cfg.port or 21)
            ftp.login(cfg.user, cfg.password)
            if cfg.protocol == "ftps" and isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()
            if cfg.path:
                ftp.cwd(cfg.path)
            for n in namen:
                ftp.delete(n)
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001
                ftp.close()
        return

    if cfg.protocol == "rclone":
        for n in namen:
            runner([cfg.rclone_bin, "deletefile", f"{cfg.rclone_remote}{cfg.path}/{n}"],
                   capture_output=True)
        return

    # sftp/scp/rsync -> ssh rm
    pfad = cfg.path or "."
    ziele = " ".join(shlex.quote(f"{pfad}/{n}") for n in namen)
    runner(["ssh", *_ssh_optionen(cfg), _ssh_host(cfg), f"rm -f {ziele}"], capture_output=True)


def prune_remote(
    cfg: RemoteConfig,
    praefix: str,
    keep: int,
    runner: Runner = subprocess.run,
    ftp_factory: Callable[[], ftplib.FTP] | None = None,
) -> list[str]:
    """Loescht am Remote-Ziel die ueberzaehligen Archive (behaelt die `keep` neuesten).

    Best-effort: gibt die geloeschten Namen zurueck; Fehler werden geloggt, nicht geworfen.
    """
    try:
        namen = _remote_liste(cfg, praefix, runner, ftp_factory)
        weg = waehle_zu_loeschen(namen, keep)
        if weg:
            _remote_delete(cfg, weg, runner, ftp_factory)
        return weg
    except Exception:  # noqa: BLE001 — Retention darf den Backup-Lauf nie abbrechen
        logger.warning("Remote-Retention fehlgeschlagen (%s)", cfg.ziel_beschreibung())
        return []
