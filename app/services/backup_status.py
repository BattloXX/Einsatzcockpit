"""Lesende Backup-Bewertung; keine DB, Netzwerkproben oder Reparaturen."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.admin_mcp.zeit import iso_utc
from app.config import settings
from app.services.backup_service import parse_dump_dateiname

logger = logging.getLogger(__name__)


def sichere_meldung(text: str) -> str:
    """Zusaetzliche Grenze fuer persistierte Fehler: keine URLs, Secrets oder Pfade."""
    from app.admin_mcp.masking import mask_text

    secrets = [settings.BACKUP_REMOTE_PASSWORD, settings.BACKUP_REMOTE_KEY]
    for url in (settings.DATABASE_URL, settings.WEATHER_DATABASE_URL):
        try:
            secrets.extend([url, unquote(urlsplit(url).password or "")])
        except ValueError:
            logger.warning("Ungueltige Datenbank-URL bei Status-Maskierung")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[MASKIERT]")
    text = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s]+", "[URL]", text)
    text = re.sub(r"(?:[A-Za-z]:)?[/\\][^\s,;]+", "[PFAD]", text)
    return mask_text(text)[:600]


def schreibe_status(out: Path, name: str, daten: dict) -> None:
    """Best effort, atomarer Austausch mit privaten Dateirechten."""
    temp = None
    try:
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", dir=out, prefix=".status-", delete=False) as fh:
            temp = Path(fh.name)
            os.fchmod(fh.fileno(), 0o600)
            json.dump({**daten, "finished_at": iso_utc(datetime.now(UTC))}, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, out / name)
    except Exception:  # Status darf den eigentlichen Lauf nie beeinflussen.
        logger.warning("Backup-Statusdatei konnte nicht geschrieben werden")
    finally:
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                logger.warning("Temporäre Backup-Statusdatei konnte nicht entfernt werden")


def _lesen(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            return {}
        daten = json.loads(path.read_text())
        if (not isinstance(daten, dict) or type(daten.get("ok")) is not bool
                or not isinstance(daten.get("finished_at"), str)):
            return {}
        stamp = datetime.fromisoformat(daten["finished_at"])
        if stamp.tzinfo is None:
            return {}
        return daten
    except (OSError, ValueError, TypeError, OverflowError):
        return {}


def pruefen(backup_dir: Path | None = None, *, now: datetime | None = None,
            max_age_h: float | None = None, restore_max_age_d: float | None = None,
            offsite_enabled: bool | None = None) -> dict:
    """Uhr und Schwellen injizierbar; Evidenz ausschliesslich aus erlaubten Feldern."""
    out = backup_dir if backup_dir is not None else Path(settings.BACKUP_DIR)
    now = (now or datetime.now(UTC)).astimezone(UTC)
    grenze = max_age_h if max_age_h is not None else settings.BACKUP_MAX_AGE_H
    restore_grenze = 24 * (restore_max_age_d if restore_max_age_d is not None else settings.BACKUP_RESTORE_MAX_AGE_D)
    remote = settings.BACKUP_REMOTE_ENABLED if offsite_enabled is None else offsite_enabled
    backup, restore = (_lesen(out / name) for name in (".status-backup.json", ".status-restore.json"))
    dumps = []
    medien = []
    try:
        for path in out.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            parsed = parse_dump_dateiname(path.name)
            if parsed:
                stat = path.stat()
                dumps.append((path.name, parsed[0], stat.st_size, stat.st_mtime))
            elif path.name.startswith("medien-") and path.name.endswith(".tar.gz"):
                medien.append(path.stat().st_mtime)
    except OSError:
        logger.warning("Backup-Verzeichnis nicht vollständig lesbar")
    # Nur Tages-Dumps bewerten: Pre-Update-Dumps duerfen ein ausgefallenes Tages-Backup nicht verdecken.
    letzter = max((d for d in dumps if not d[1].startswith("pre-update-")), key=lambda d: d[3], default=None)
    backup_zeit = datetime.fromisoformat(backup["finished_at"]) if backup.get("ok") else (
        datetime.fromtimestamp(letzter[3], UTC) if letzter else None)
    restore_zeit = datetime.fromisoformat(restore["finished_at"]) if restore else None

    def alter(zeit):
        return max(0.0, (now - zeit).total_seconds() / 3600) if zeit else None

    def bewerten(stunden, limit, lauf):
        if lauf.get("ok") is False:
            return "fail"
        if stunden is None:
            return "unbekannt"
        return "ok" if stunden <= limit else "warn" if stunden <= 2 * limit else "fail"

    backup_alter, restore_alter = alter(backup_zeit), alter(restore_zeit)
    backup_status = bewerten(backup_alter, grenze, backup)
    restore_status = bewerten(restore_alter, restore_grenze, restore)
    offsite = backup.get("offsite")
    upload_ok = (offsite.get("ok") if isinstance(offsite, dict) and offsite.get("enabled") is True else None)
    offsite_status = "deaktiviert" if not remote else "ok" if upload_ok is True else "warn"
    try:
        frei = shutil.disk_usage(out).free
    except OSError:
        frei = None
    knapp = frei is not None and letzter is not None and frei < 2 * letzter[2]
    befunde = []
    empfehlungen = []

    def befund(key, titel, status, empfehlung):
        befunde.append({"key": key, "titel": titel, "status": status, "empfehlung": empfehlung})
        empfehlungen.append(empfehlung)

    if backup_status != "ok":
        fehlgeschlagen = backup.get("ok") is False
        befund("backup_fehlgeschlagen" if fehlgeschlagen else "backup_zu_alt",
               "Letzter Backup-Lauf fehlgeschlagen" if fehlgeschlagen else "Backup veraltet oder unbekannt",
               "warn" if backup_status == "unbekannt" else backup_status,
               "systemctl status ec-backup.timer; journalctl -u ec-backup.service; python -m app.cli backup")
    if restore_status != "ok":
        befund("restore_probe_zu_alt", "Restore-Probe fehlgeschlagen, veraltet oder unbekannt",
               "warn" if restore_status == "unbekannt" else restore_status,
               "Restore-Probe pruefen: python -m app.cli restore-test")
    if offsite_status == "warn":
        befund("offsite_fehlgeschlagen", "Off-Site-Upload fehlgeschlagen oder unbekannt", "warn",
               "Off-Site-Konfiguration und journalctl -u ec-backup.service pruefen; python -m app.cli backup")
    if knapp:
        befund("speicher_knapp", "Freier Speicher kleiner als zwei letzte Dumps", "warn",
               "Speicher im Backup-Verzeichnis freigeben oder erweitern; Retention pruefen")
    hinweise = []
    medien_alter = alter(datetime.fromtimestamp(max(medien), UTC)) if medien else None
    if medien_alter is not None and medien_alter > grenze:
        hinweise.append("Medienarchiv veraltet; Medien-Sicherung pruefen")
    counts: dict[str, int] = {}
    for _, typ, _, _ in dumps:
        counts[typ] = counts.get(typ, 0) + 1
    states = [backup_status, restore_status, offsite_status, "warn" if knapp else "ok"]
    status = next((s for s in ("fail", "warn", "unbekannt") if s in states), "ok")
    evidence = {"backup": {"status": backup_status, "alter_stunden": backup_alter,
                              "finished_at": iso_utc(backup_zeit), "dateiname": letzter[0] if letzter else None,
                              "groesse": letzter[2] if letzter else None},
                "restore": {"status": restore_status, "alter_stunden": restore_alter,
                            "finished_at": iso_utc(restore_zeit),
                            "dump": restore.get("dump") if isinstance(restore.get("dump"), str)
                            and parse_dump_dateiname(restore["dump"]) else None},
                "offsite": {"enabled": remote, "status": offsite_status,
                            "alter_stunden": alter(datetime.fromisoformat(backup["finished_at"]))
                            if remote and upload_ok is True and backup else None},
                "dumps_je_typ": counts, "freier_platz_mb": frei / 1024**2 if frei is not None else None,
                "medien_alter_stunden": medien_alter, "hinweise": hinweise,
                "empfehlungen": empfehlungen, "overall_status": status}
    return {"status": status, "checked_at": iso_utc(now), "evidence": evidence,
            "empfehlungen": empfehlungen, "befunde": befunde}
