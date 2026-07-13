"""Diagnose-Werkzeug: zeichnet echte LIS-SOAP-Rohdaten für eine Zeit lang auf.

Zweck: Die Feldnamen/Struktur in LIS_IPR_Schnittstellen_Dokumentation.md beruhen
auf einem einzelnen Netzwerkmitschnitt und sind an mehreren Stellen nicht zu 100%
verifiziert (siehe Kommentare in lis_client.py, v.a. GetOperationUnits). Diese
Funktion pollt für eine konfigurierbare Dauer echte Antworten und schreibt sie
als Rohdateien weg, damit die Mappings anschließend gegen echte Daten
nachgeschärft werden können — z.B. während/nach einem laufenden Einsatz.

Rein lesender Vorgang: Es werden KEINE Einsätze/Meldungen/Fahrzeugstatus in der
DB angelegt oder verändert (anders als lis_sync.py). Ein parallel laufender
regulärer Sync-Loop ist daher unproblematisch — die Aufzeichnung erzeugt nie
Dubletten, weil sie schlicht nichts in die Datenbank schreibt.

ACHTUNG Datenschutz: Die aufgezeichneten Rohdaten enthalten personenbezogene
Daten (Anrufer Name/Telefon auf Operationen, Mannschafts-Zu-/Absagen mit
Klarnamen). Deshalb:
  - Zugriff ist auf system_admin beschränkt (siehe ui_lis.py-Routen).
  - Automatische Löschung nach CAPTURE_RETENTION_DAYS Tagen (purge_old_captures,
    täglicher Loop lis_capture_retention_loop() in app.main-Lifespan).
"""
from __future__ import annotations

import asyncio
import json
import logging
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.lis.lis_client import LisClient, LisClientError

logger = logging.getLogger("einsatzleiter.lis.capture")

CAPTURE_ROOT = Path("app_storage/lis_capture")

# Antworten über dieser Größe werden nicht vollständig auf Platte geschrieben
# (v.a. DownloadDocument-Antworten mit Bilddaten) — nur die ersten Bytes,
# fürs Nachschärfen der Feldnamen reicht die Struktur, nicht der Volltext.
_MAX_CAPTURED_BYTES = 200_000

# Aufzeichnungen enthalten personenbezogene Daten (Anrufer, Mannschaft) —
# nach dieser Frist automatisch löschen (siehe purge_old_captures()).
CAPTURE_RETENTION_DAYS = 7

# In-Memory-Registry laufender Aufzeichnungen je Org (verhindert Doppelstart,
# erlaubt Status-Abfrage/Abbruch aus dem Admin-UI). Geht bei Neustart verloren,
# was für ein Diagnose-Werkzeug unkritisch ist.
_active_captures: dict[int, asyncio.Task] = {}
_active_run_ids: dict[int, str] = {}


class ExchangeRecorder:
    """Schreibt SOAP-Request/Response-Paare als Rohdateien + eine summary.json.

    Von der Netzwerklogik entkoppelt und daher ohne echten LIS-Zugang testbar.
    """

    def __init__(self, out_dir: Path, org_id: int, run_id: str):
        self.out_dir = out_dir
        self.org_id = org_id
        self.run_id = run_id
        self.seq = 0
        self.exchanges: list[dict] = []
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def record(self, url: str, soap_action: str, request_bytes: bytes, response_bytes: bytes) -> None:
        self.seq += 1
        op_name = soap_action.rsplit("/", 1)[-1] or "unknown"
        stamp = datetime.now(UTC).strftime("%H%M%S")
        req_path = self.out_dir / f"{self.seq:04d}_{stamp}_{op_name}_request.xml"
        resp_path = self.out_dir / f"{self.seq:04d}_{stamp}_{op_name}_response.bin"

        req_path.write_bytes(request_bytes)
        truncated = len(response_bytes) > _MAX_CAPTURED_BYTES
        resp_path.write_bytes(response_bytes[:_MAX_CAPTURED_BYTES] if truncated else response_bytes)

        self.exchanges.append({
            "seq": self.seq,
            "operation": op_name,
            "url": url,
            "request_file": req_path.name,
            "response_file": resp_path.name,
            "response_bytes": len(response_bytes),
            "truncated": truncated,
            "ts": datetime.now(UTC).isoformat(),
        })
        logger.debug("LIS-Capture (Org %s): %s aufgezeichnet (%d Bytes)", self.org_id, op_name, len(response_bytes))

    def write_summary(self, *, duration_minutes: int, finished: bool) -> Path:
        summary = {
            "org_id": self.org_id,
            "run_id": self.run_id,
            "duration_minutes": duration_minutes,
            "finished": finished,
            "exchange_count": len(self.exchanges),
            "exchanges": self.exchanges,
        }
        summary_path = self.out_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary_path


def capture_run_dir(org_id: int, run_id: str) -> Path:
    return CAPTURE_ROOT / str(org_id) / run_id


def _bundle_capture_into_zip(out_dir: Path, run_id: str) -> Path | None:
    """Fasst alle Rohdateien eines Laufs (request.xml/response.bin je Exchange +
    summary.json) in ein einzelnes ZIP im selben Verzeichnis zusammen und löscht
    danach die entpackten Einzeldateien (außer summary.json, das die Admin-UI
    weiterhin direkt liest, ohne das ZIP zu öffnen). Ein system_admin muss so nur
    noch eine Datei statt vieler kleiner xml/bin-Dateien sichern.

    Gibt None zurück, wenn es nichts zu bündeln gibt (z.B. sofortiger Abbruch
    vor dem ersten Exchange)."""
    raw_files = sorted(p for p in out_dir.iterdir() if p.is_file() and p.suffix != ".zip")
    if not raw_files:
        return None
    zip_path = out_dir / f"{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in raw_files:
            zf.write(f, arcname=f.name)
    for f in raw_files:
        if f.name != "summary.json":
            f.unlink()
    return zip_path


async def _capture_once(client: LisClient, recorder: ExchangeRecorder, organization_id: str) -> None:
    """Ein einzelner Poll-Zyklus: aktive Operationen + je Operation Tasks/Units/
    Dokumente. Deckt dieselben Endpunkte ab wie der reguläre Sync (lis_sync.py)
    — so entstehen echte Beispieldaten für alle fürs Mapping relevanten Objekte.

    Bewusst als eigene Funktion (statt Teil der Zeit-Schleife), damit sie ohne
    Timing-Abhängigkeiten direkt getestet werden kann.
    """
    try:
        operations = await client.get_operations_in_range(
            organization_id, operation_filter="ActiveParticipation",
        )
    except LisClientError:
        logger.exception("LIS-Capture: GetOperationsInRange fehlgeschlagen (Org %s)", recorder.org_id)
        operations = []

    for op in operations:
        op_id = op.get("Id")
        if not op_id:
            continue
        for coro_factory, label in (
            (lambda: client.get_tasks(op_id), "GetTasks"),
            (lambda: client.get_operation_units(organization_id, op_id), "GetOperationUnits"),
            (lambda: client.get_documents_by_operation_id(op_id), "GetDocumentsByOperationId"),
        ):
            try:
                await coro_factory()
            except LisClientError:
                logger.exception(
                    "LIS-Capture: %s für Operation %s fehlgeschlagen (Org %s)",
                    label, op_id, recorder.org_id,
                )


async def capture_traffic(
    client: LisClient,
    recorder: ExchangeRecorder,
    organization_id: str,
    duration_minutes: float,
    poll_interval_seconds: int,
) -> Path:
    """Pollt read-only echte LIS-Daten für duration_minutes und zeichnet jeden
    SOAP-Austausch über recorder.record() auf (via client._on_exchange, das der
    Aufrufer verdrahtet)."""
    stop_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)
    finished = False
    try:
        while True:
            await _capture_once(client, recorder, organization_id)

            # Zwischenstand sichern, damit ein laufender Capture-Lauf im Admin-UI
            # sichtbar ist, bevor die volle Dauer abgelaufen ist.
            recorder.write_summary(duration_minutes=int(duration_minutes), finished=False)

            remaining = (stop_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval_seconds, remaining))
        finished = True
    except asyncio.CancelledError:
        logger.info("LIS-Capture (Org %s) abgebrochen", recorder.org_id)
        raise
    finally:
        recorder.write_summary(duration_minutes=int(duration_minutes), finished=finished)
        try:
            _bundle_capture_into_zip(recorder.out_dir, recorder.run_id)
        except OSError:
            logger.exception(
                "LIS-Capture (Org %s): Zusammenfassen in ZIP fehlgeschlagen", recorder.org_id,
            )
        logger.info(
            "LIS-Capture (Org %s) beendet: %d Exchanges in %s",
            recorder.org_id, len(recorder.exchanges), recorder.out_dir,
        )
    return recorder.out_dir


def is_capture_running(org_id: int) -> bool:
    task = _active_captures.get(org_id)
    return task is not None and not task.done()


def _current_run_id(org_id: int) -> str | None:
    return _active_run_ids.get(org_id) if is_capture_running(org_id) else None


def cancel_capture(org_id: int) -> bool:
    task = _active_captures.get(org_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


async def start_capture_for_org(
    org_id: int,
    duration_minutes: float = 120,
) -> str:
    """Lädt die Org-Konfiguration, baut einen LisClient und startet die
    Aufzeichnung als Hintergrund-Task. Gibt die run_id zurück.

    Wirft ValueError, wenn die Org nicht vollständig konfiguriert ist oder
    bereits eine Aufzeichnung läuft.
    """
    if is_capture_running(org_id):
        raise ValueError("Für diese Organisation läuft bereits eine Aufzeichnung.")

    # Opportunistische Bereinigung, falls der tägliche Retention-Loop seit dem
    # letzten Start noch nicht gelaufen ist (z.B. kurz nach einem Neustart).
    try:
        purge_old_captures()
    except Exception:
        logger.exception("Opportunistische LIS-Capture-Bereinigung fehlgeschlagen")

    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.lis import OrgLisConfig

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        config = db.query(OrgLisConfig).filter(OrgLisConfig.org_id == org_id).first()
        if not config or not config.is_fully_configured:
            raise ValueError("LIS-Konfiguration unvollständig (URL, Organisation, Zugangsdaten).")
        # is_fully_configured garantiert bereits, dass diese Felder gesetzt sind - hier
        # nur fuer die Typpruefung explizit gemacht (config.* bleibt sonst "str | None").
        assert config.base_url and config.organization_id and config.username and config.password_enc
        base_url, site, organization_id, username, project_id = (
            config.base_url, config.site, config.organization_id, config.username, config.project_id,
        )
        poll_interval_seconds = config.poll_interval_seconds
        password = decrypt_secret(config.password_enc)
        password_is_hash = config.password_is_hash
    finally:
        db.close()

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = capture_run_dir(org_id, run_id)
    recorder = ExchangeRecorder(out_dir, org_id, run_id)
    client = LisClient(
        base_url, site, username, password, on_exchange=recorder.record,
        project_id=project_id, password_is_hash=password_is_hash,
        organization_id=organization_id,
    )

    # Muss vor GetTasks einmal aufgerufen werden, sonst NullReferenceException auf dem
    # LIS-Server (siehe select_operation()-Docstring in lis_client.py). Fehler hier
    # sollen die Aufzeichnung nicht verhindern — sie ist ein Diagnose-Werkzeug und
    # soll auch dann noch GetOperationsInRange/GetOperationUnits aufzeichnen können.
    try:
        await client.select_operation(organization_id)
    except LisClientError:
        logger.exception("LIS-Capture: SelectOperation für Org %s fehlgeschlagen", org_id)

    task = asyncio.create_task(
        capture_traffic(client, recorder, organization_id, duration_minutes, poll_interval_seconds)
    )
    _active_captures[org_id] = task
    _active_run_ids[org_id] = run_id
    logger.info("LIS-Capture gestartet: Org %s, Dauer %s min, run_id %s", org_id, duration_minutes, run_id)
    return run_id


def list_captures(org_id: int) -> list[dict]:
    """Listet vorhandene Aufzeichnungsläufe einer Org (aus den summary.json-Dateien)."""
    org_dir = CAPTURE_ROOT / str(org_id)
    if not org_dir.is_dir():
        return []
    runs = []
    for run_dir in sorted(org_dir.iterdir(), reverse=True):
        summary_path = run_dir / "summary.json"
        if not summary_path.is_file():
            continue
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        data["run_dir"] = run_dir.name
        data["running"] = run_dir.name == _current_run_id(org_id)
        runs.append(data)
    return runs


def _parse_run_id(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def delete_capture(org_id: int, run_id: str) -> bool:
    """Löscht einen einzelnen Aufzeichnungslauf. Läuft er noch, wird er zuerst abgebrochen."""
    if run_id == _current_run_id(org_id):
        cancel_capture(org_id)
    run_dir = capture_run_dir(org_id, run_id)
    if not run_dir.is_dir():
        return False
    import shutil
    shutil.rmtree(run_dir, ignore_errors=True)
    return True


def purge_old_captures(retention_days: int = CAPTURE_RETENTION_DAYS) -> int:
    """Löscht alle Aufzeichnungsläufe (aller Orgs), die älter als retention_days sind.

    Läuft ein Capture noch, wird er nie automatisch gelöscht — unabhängig vom Alter
    (Schutz über _current_run_id). Gibt die Anzahl gelöschter Läufe zurück.
    """
    if not CAPTURE_ROOT.is_dir():
        return 0
    import shutil
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted = 0
    for org_dir in CAPTURE_ROOT.iterdir():
        if not org_dir.is_dir():
            continue
        try:
            org_id = int(org_dir.name)
        except ValueError:
            continue
        for run_dir in org_dir.iterdir():
            if not run_dir.is_dir() or run_dir.name == _current_run_id(org_id):
                continue
            started = _parse_run_id(run_dir.name)
            if started is None or started >= cutoff:
                continue
            shutil.rmtree(run_dir, ignore_errors=True)
            deleted += 1
    if deleted:
        logger.info(
            "LIS-Capture-Retention: %d abgelaufene Aufzeichnungsläufe gelöscht (älter als %d Tage).",
            deleted, retention_days,
        )
    return deleted


# ── Täglicher Retention-Loop (Muster: weather_retention.py) ─────────────────
_PURGE_HOUR = 4
_PURGE_MINUTE = 0

try:
    from zoneinfo import ZoneInfo
    _VIENNA_TZ = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover
    _VIENNA_TZ = UTC  # type: ignore[assignment]


def _seconds_until_next(hour: int, minute: int) -> float:
    now = datetime.now(_VIENNA_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def lis_capture_retention_loop() -> None:
    """Täglicher Loop: löscht abgelaufene LIS-Capture-Aufzeichnungen (DSGVO — enthalten
    Personenbezug wie Anrufer-/Mannschaftsdaten, siehe Modul-Docstring)."""
    while True:
        await asyncio.sleep(_seconds_until_next(_PURGE_HOUR, _PURGE_MINUTE))
        try:
            await asyncio.to_thread(purge_old_captures)
        except Exception:
            logger.exception("Fehler im LIS-Capture-Retention-Loop")
