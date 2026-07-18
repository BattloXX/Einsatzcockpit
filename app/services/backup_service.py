"""Datenbank-Backup + getestete Restore-Probe (Disaster-Recovery-Baustein).

Deckt die operative Sicherung ab, die der JSON-Org-Konfig-Export (ui_backup.py)
NICHT leistet: vollstaendige, wiederherstellbare mariadb-Dumps beider Datenbanken
(Haupt-DB + Wetter-Zeitreihe) plus die Medien unter app_storage.

Aufteilung bewusst so, dass die risikoreiche Logik REIN und testbar ist:
- URL-Parsing, Kommandozeilen-Aufbau, Retention-Pruning, Verifikations-SQL sind
  seiteneffektfrei und unit-getestet (tests/test_backup_service.py).
- Die eigentliche Orchestrierung (Subprozesse, Dateien) ruft nur diese Bausteine;
  die Restore-Probe beweist bei jedem Lauf, dass der Dump einspielbar ist.

Passwoerter werden IMMER ueber die Umgebung (MYSQL_PWD) an mariadb/mariadb-dump
uebergeben, nie als Argument -- sonst waeren sie in der Prozessliste (ps) sichtbar.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

logger = logging.getLogger("einsatzleiter.backup")

# Zeitstempel im Dateinamen: sortierbar, ohne Sonderzeichen, UTC.
_STAMP_FMT = "%Y%m%d-%H%M%SZ"


@dataclass(frozen=True)
class DbConfig:
    """Verbindungsdaten einer MariaDB, aus einer SQLAlchemy-URL destilliert."""
    host: str
    port: int
    user: str
    password: str
    database: str


def parse_database_url(url: str) -> DbConfig:
    """Zerlegt eine SQLAlchemy-MySQL-URL (mysql+pymysql://user:pw@host:port/db).

    Toleriert prozentkodierte Zeichen in Benutzer/Passwort (z. B. @ oder / im
    Passwort) und fehlenden Port (Default 3306).
    """
    teile = urlsplit(url)
    if not teile.hostname or not teile.path.strip("/"):
        raise ValueError(f"Unvollstaendige DATABASE_URL (Host/DB fehlt): {url!r}")
    return DbConfig(
        host=teile.hostname,
        port=teile.port or 3306,
        user=unquote(teile.username or ""),
        password=unquote(teile.password or ""),
        database=teile.path.lstrip("/"),
    )


def _verbindungs_argv(cfg: DbConfig) -> list[str]:
    """Gemeinsame Verbindungsflags (ohne Passwort -- das kommt via MYSQL_PWD)."""
    return [f"--host={cfg.host}", f"--port={cfg.port}", f"--user={cfg.user}"]


def build_dump_argv(cfg: DbConfig, dump_bin: str = "mariadb-dump") -> tuple[list[str], dict[str, str]]:
    """Argumentliste + Env fuer einen konsistenten Voll-Dump einer DB.

    --single-transaction: konsistenter Snapshot ohne Tabellensperren (InnoDB).
    --routines/--triggers/--events: Prozeduren/Trigger/Events mitsichern.
    --no-tablespaces: vermeidet PROCESS-Rechte-Anforderung (CloudPanel-DB-User).
    """
    argv = [
        dump_bin,
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--no-tablespaces",
        "--default-character-set=utf8mb4",
        *_verbindungs_argv(cfg),
        cfg.database,
    ]
    return argv, {"MYSQL_PWD": cfg.password}


def build_restore_argv(
    cfg: DbConfig, ziel_db: str | None = None, client_bin: str = "mariadb",
) -> tuple[list[str], dict[str, str]]:
    """Argumentliste + Env, um einen Dump per stdin in ziel_db einzuspielen.

    ziel_db erlaubt das Einspielen in eine andere Datenbank als die Quelle
    (Restore-Probe in eine Wegwerf-DB), ohne die Produktions-DB zu beruehren.
    """
    argv = [client_bin, *_verbindungs_argv(cfg), ziel_db or cfg.database]
    return argv, {"MYSQL_PWD": cfg.password}


def build_query_argv(
    cfg: DbConfig, ziel_db: str, sql: str, client_bin: str = "mariadb",
) -> tuple[list[str], dict[str, str]]:
    """Argv fuer eine lesende Anweisung gegen eine konkrete DB (Verifikation)."""
    argv = [client_bin, *_verbindungs_argv(cfg), "--batch", "--skip-column-names",
            "--execute", sql, ziel_db]
    return argv, {"MYSQL_PWD": cfg.password}


def build_admin_argv(
    cfg: DbConfig, sql: str, client_bin: str = "mariadb",
) -> tuple[list[str], dict[str, str]]:
    """Argv fuer eine administrative SQL-Anweisung (CREATE/DROP DATABASE ...).

    Ohne DB-Namen verbunden -- fuer Anweisungen, die keine bestehende DB brauchen.
    """
    argv = [client_bin, *_verbindungs_argv(cfg), "--execute", sql]
    return argv, {"MYSQL_PWD": cfg.password}


def dump_dateiname(db_label: str, jetzt: datetime | None = None) -> str:
    """Sortierbarer Dump-Dateiname, z. B. 'einsatzleiter-20260718-013000Z.sql.gz'."""
    stamp = (jetzt or datetime.now(UTC)).strftime(_STAMP_FMT)
    return f"{db_label}-{stamp}.sql.gz"


def medien_dateiname(jetzt: datetime | None = None) -> str:
    """Sortierbarer Medien-Archivname, z. B. 'medien-20260718-013000Z.tar.gz'."""
    stamp = (jetzt or datetime.now(UTC)).strftime(_STAMP_FMT)
    return f"medien-{stamp}.tar.gz"


def zu_loeschende(pfade: list[Path], behalten: int) -> list[Path]:
    """Waehlt die ueberzaehligen (aeltesten) Backups zur Loeschung aus.

    Sortiert nach Name (die Zeitstempel sind lexikografisch = chronologisch);
    die 'behalten' neuesten bleiben, der Rest wird zurueckgegeben.
    """
    if behalten <= 0:
        return []
    sortiert = sorted(pfade, key=lambda p: p.name)
    ueberzaehlig = len(sortiert) - behalten
    return sortiert[:ueberzaehlig] if ueberzaehlig > 0 else []


def prune_backups(verzeichnis: Path, praefix: str, behalten: int) -> list[Path]:
    """Loescht alte Backups eines Praefix, behaelt die 'behalten' neuesten.

    Gibt die tatsaechlich geloeschten Pfade zurueck. Fehler beim Loeschen
    einzelner Dateien werden geloggt, brechen aber den Lauf nicht ab.
    """
    if not verzeichnis.is_dir():
        return []
    kandidaten = sorted(verzeichnis.glob(f"{praefix}-*"))
    geloescht: list[Path] = []
    for pfad in zu_loeschende(kandidaten, behalten):
        try:
            pfad.unlink()
            geloescht.append(pfad)
        except OSError:
            logger.warning("Backup-Prune: konnte %s nicht loeschen", pfad)
    return geloescht


def verify_sql(mindest_tabellen: tuple[str, ...] = ("fire_dept", "users", "alembic_version")) -> list[str]:
    """SQL-Checks fuer die Restore-Probe: Schema-Version da, Kerntabellen befuellt.

    Eine wiederhergestellte DB gilt als plausibel, wenn alembic_version genau
    einen Eintrag hat und die Kerntabellen existieren (COUNT laeuft ohne Fehler).
    """
    checks = ["SELECT COUNT(*) FROM alembic_version"]
    checks += [f"SELECT COUNT(*) FROM {t}" for t in mindest_tabellen if t != "alembic_version"]
    return checks


def _lauf(argv: list[str], env_zusatz: dict[str, str], **kw: object) -> subprocess.CompletedProcess:
    """subprocess.run mit gemischter Umgebung (Basis-Env + Passwort via MYSQL_PWD)."""
    import os
    env = {**os.environ, **env_zusatz}
    return subprocess.run(argv, env=env, **kw)  # type: ignore[call-overload]
