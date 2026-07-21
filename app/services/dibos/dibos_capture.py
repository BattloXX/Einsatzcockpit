"""Diagnose-/Tracing-Werkzeug: zeichnet echte DIBOS-EventHub-Rohdaten für eine Zeit
lang auf und hält daneben einen lesbaren Live-Snapshot (latest.json) für das Admin-UI.

Zweck: Die Feldnamen/Struktur in dibos_client.py wurden aus einem einzelnen
HTTP-Mitschnitt des Elvis-Desktop-Clients reverse-engineered, nicht aus einer
Dokumentation. Diese Funktion pollt für eine konfigurierbare Dauer echte
Antworten, schreibt sie als Rohdateien weg (wie beim LIS-Pendant) UND aktualisiert
nach jedem Poll-Zyklus einen geparsten Zwischenstand, damit ein system_admin ohne
Dateizugriff sieht, was gerade über die Leitung läuft — genau die Anforderung
"ein Tracing, das bei einem Einsatz mitliest".

Rein lesender Vorgang: Es werden KEINE Einsätze/Fahrzeuge/Meldungen in der DB
angelegt oder verändert. Ein parallel laufender Auto-Erkennungs-Loop
(dibos_loop.py) ist daher unproblematisch — dieser erkennt nur einen eigenen
Einsatz und startet ggf. diese Aufzeichnung, schreibt selbst aber ebenfalls nichts.

ACHTUNG Datenschutz: Die aufgezeichneten Rohdaten enthalten personenbezogene Daten
(Anrufer Name/Telefon je Einsatz). Deshalb:
  - Zugriff ist auf system_admin beschränkt (siehe ui_dibos.py-Routen).
  - Automatische Löschung nach TRACE_RETENTION_DAYS Tagen (purge_old_traces,
    täglicher Loop dibos_trace_retention_loop() in app.main-Lifespan).

Muster: app/services/lis/lis_capture.py (ExchangeRecorder, Registries, Retention,
ZIP-Bündelung) — hier fast 1:1 übernommen, ergänzt um den Live-Snapshot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.dibos.dibos_client import (
    DibosClient,
    DibosClientError,
    parse_events,
    parse_radios,
    parse_units,
)

logger = logging.getLogger("einsatzleiter.dibos.capture")

TRACE_ROOT = Path("app_storage/dibos_trace")

# Antworten über dieser Größe werden nicht vollständig auf Platte geschrieben
# und nicht in den Live-Snapshot übernommen. Ursprünglich 200_000 — das war zu
# knapp: GetPublicEvents (qty=15, jedes Event inkl. vollem Kommentarprotokoll
# und personResponseList) überschritt dieses Limit im echten Mitschnitt vom
# 21.07.2026 in JEDEM der 352 Poll-Zyklen, wurde also nie vollständig
# aufgezeichnet und lieferte ungültiges (abgeschnittenes) JSON — genau die
# Rohdaten, die für eine produktive Anbindung (Personenrückmeldungen,
# Objekt-Matching) gebraucht werden. 2_000_000 gibt ausreichend Spielraum für
# realistische Payloads, ohne die Aufzeichnung unbegrenzt wachsen zu lassen.
_MAX_CAPTURED_BYTES = 2_000_000

# Aufzeichnungen enthalten personenbezogene Daten (Anrufer) — nach dieser Frist
# automatisch löschen (siehe purge_old_traces()).
TRACE_RETENTION_DAYS = 7

# Operation -> (Live-Snapshot-Schlüssel, Parser-Funktion). Nur Operationen, die
# ein flaches JSON-Array liefern, werden live geparst (siehe dibos_client.py).
_LIVE_PARSERS = {
    "GetCurrentEvents": ("events", parse_events),
    "GetPublicEvents": ("public_events", parse_events),
    "GetCurrentUnits": ("units", parse_units),
    "GetCurrentRadios": ("radios", parse_radios),
}

# In-Memory-Registry laufender Aufzeichnungen je Org (verhindert Doppelstart,
# erlaubt Status-Abfrage/Abbruch aus dem Admin-UI). Geht bei Neustart verloren,
# was für ein Diagnose-Werkzeug unkritisch ist.
_active_traces: dict[int, asyncio.Task] = {}
_active_run_ids: dict[int, str] = {}


class ExchangeRecorder:
    """Schreibt Request/Response-Paare als Rohdateien + summary.json und pflegt
    daneben einen lesbaren Live-Snapshot (latest.json).

    Von der Netzwerklogik entkoppelt und daher ohne echten DIBOS-Zugang testbar.
    """

    def __init__(self, out_dir: Path, org_id: int, run_id: str, enrich_incidents: bool = False):
        self.out_dir = out_dir
        self.org_id = org_id
        self.run_id = run_id
        # Org-Opt-in (OrgDibosConfig.enrich_incidents): reichert während dieses
        # Traces laufend bestehende, aktive Einsätze mit DIBOS-Zusatzinfos an
        # (siehe dibos_enrich.py + _capture_once()). Default False — ändert
        # nichts am reinen Tracing-Verhalten bestehender Aufrufer/Tests.
        self.enrich_incidents = enrich_incidents
        self.seq = 0
        self.exchanges: list[dict] = []
        self.latest: dict = {"updated_at": None}
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def record(self, url: str, operation: str, request_bytes: bytes, response_bytes: bytes) -> None:
        self.seq += 1
        stamp = datetime.now(UTC).strftime("%H%M%S")
        req_path = self.out_dir / f"{self.seq:04d}_{stamp}_{operation}_request.xml"
        resp_path = self.out_dir / f"{self.seq:04d}_{stamp}_{operation}_response.json"

        req_path.write_bytes(request_bytes)
        truncated = len(response_bytes) > _MAX_CAPTURED_BYTES
        resp_path.write_bytes(response_bytes[:_MAX_CAPTURED_BYTES] if truncated else response_bytes)

        self.exchanges.append({
            "seq": self.seq,
            "operation": operation,
            "url": url,
            "request_file": req_path.name,
            "response_file": resp_path.name,
            "response_bytes": len(response_bytes),
            "truncated": truncated,
            "ts": datetime.now(UTC).isoformat(),
        })
        logger.debug(
            "DIBOS-Trace (Org %s): %s aufgezeichnet (%d Bytes)", self.org_id, operation, len(response_bytes),
        )

        parser_entry = _LIVE_PARSERS.get(operation)
        if parser_entry and not truncated and response_bytes:
            key, parser_fn = parser_entry
            try:
                data = json.loads(response_bytes)
            except (ValueError, UnicodeDecodeError):
                logger.exception("DIBOS-Trace: Live-Snapshot-Parsing für %s fehlgeschlagen", operation)
            else:
                if isinstance(data, list):
                    self.latest[key] = parser_fn(data)
                    self.latest["updated_at"] = datetime.now(UTC).isoformat()

    def write_latest(self) -> Path:
        path = self.out_dir / "latest.json"
        path.write_text(json.dumps(self.latest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

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


def trace_run_dir(org_id: int, run_id: str) -> Path:
    return TRACE_ROOT / str(org_id) / run_id


def _bundle_trace_into_zip(out_dir: Path, run_id: str) -> Path | None:
    """Fasst alle Rohdateien eines Laufs in ein ZIP zusammen und löscht danach die
    Einzeldateien (außer summary.json/latest.json, die das Admin-UI direkt liest,
    ohne das ZIP zu öffnen). Gibt None zurück, wenn es nichts zu bündeln gibt.
    """
    raw_files = sorted(
        p for p in out_dir.iterdir()
        if p.is_file() and p.suffix != ".zip" and p.name not in ("summary.json", "latest.json")
    )
    if not raw_files:
        return None
    zip_path = out_dir / f"{run_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in raw_files:
            zf.write(f, arcname=f.name)
    for f in raw_files:
        f.unlink()
    return zip_path


async def _capture_once(client: DibosClient, recorder: ExchangeRecorder) -> None:
    """Ein einzelner Poll-Zyklus über alle einsatzrelevanten Lese-Endpunkte.

    Jeder Aufruf einzeln try/except, damit ein einzelner fehlender Endpunkt (z.B.
    GetElvisNotification) den restlichen Poll-Zyklus nicht verhindert. client.*
    ruft intern on_exchange=recorder.record für uns auf — hier wird nur noch der
    Live-Snapshot am Ende des Zyklus geschrieben.

    Läuft dieser Trace mit enrich_incidents=True (Org-Opt-in), wird nach einem
    erfolgreichen GetCurrentEvents zusätzlich app.services.dibos.dibos_enrich
    aufgerufen — läuft für die GESAMTE Trace-Dauer (also i.d.R. den ganzen
    Einsatz) mit, nicht nur einmalig wie der leichte Erkennungs-Loop
    (dibos_loop.py, der nach Trace-Start keine weiteren GetCurrentEvents mehr
    abfragt, siehe dort is_trace_running()-Guard).
    """
    for coro_factory, label in (
        (client.get_current_events, "GetCurrentEvents"),
        (client.get_public_events, "GetPublicEvents"),
        (client.get_current_units, "GetCurrentUnits"),
        (client.get_current_radios, "GetCurrentRadios"),
        (client.get_elvis_notification, "GetElvisNotification"),
    ):
        try:
            result = await coro_factory()
        except DibosClientError:
            logger.exception("DIBOS-Trace: %s fehlgeschlagen (Org %s)", label, recorder.org_id)
            continue
        if label == "GetCurrentEvents" and recorder.enrich_incidents and result:
            await _enrich_and_broadcast(recorder.org_id, result)
    recorder.write_latest()


async def _enrich_and_broadcast(org_id: int, raw_events: list[dict]) -> None:
    """Reichert an (in einem Thread, da synchron/DB-blockierend) und broadcastet
    pro tatsächlich geänderten Einsatz — Fehler dürfen den Trace-Poll nie abbrechen.

    Zwei Broadcast-Typen: "dibos_sync" (voller Board-Reload) für jeden geänderten
    Einsatz, zusätzlich das gezielte "rsvp:changed" (nur Zu-/Absage-Widget neu
    laden, siehe app.js) für Einsätze mit neuen Personenrückmeldungen.
    """
    try:
        from app.services.dibos.dibos_enrich import enrich_events_for_org
        result = await asyncio.to_thread(enrich_events_for_org, org_id, raw_events)
    except Exception:
        logger.exception("DIBOS-Trace: Einsatzanreicherung fehlgeschlagen (Org %s)", org_id)
        return
    changed_ids = result.get("changed_ids") or []
    rsvp_changed_ids = result.get("rsvp_changed_ids") or []
    if not changed_ids and not rsvp_changed_ids:
        return
    from app.services.broadcast import manager
    for incident_id in changed_ids:
        try:
            await manager.broadcast(incident_id, {"type": "dibos_sync", "reload_board": True})
        except Exception:
            logger.exception("DIBOS-Trace: Broadcast für Einsatz %s fehlgeschlagen", incident_id)
    for incident_id in rsvp_changed_ids:
        try:
            await manager.broadcast(incident_id, {"type": "rsvp:changed"})
        except Exception:
            logger.exception("DIBOS-Trace: RSVP-Broadcast für Einsatz %s fehlgeschlagen", incident_id)


async def capture_traffic(
    client: DibosClient,
    recorder: ExchangeRecorder,
    duration_minutes: float,
    poll_interval_seconds: int,
) -> Path:
    """Pollt read-only echte DIBOS-Daten für duration_minutes und zeichnet jeden
    Austausch über recorder.record() auf (via client._on_exchange)."""
    stop_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)
    finished = False
    try:
        while True:
            await _capture_once(client, recorder)

            # Zwischenstand sichern, damit ein laufender Trace im Admin-UI sichtbar
            # ist, bevor die volle Dauer abgelaufen ist.
            recorder.write_summary(duration_minutes=int(duration_minutes), finished=False)

            remaining = (stop_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(poll_interval_seconds, remaining))
        finished = True
    except asyncio.CancelledError:
        logger.info("DIBOS-Trace (Org %s) abgebrochen", recorder.org_id)
        raise
    finally:
        recorder.write_summary(duration_minutes=int(duration_minutes), finished=finished)
        await client.aclose()
        try:
            _bundle_trace_into_zip(recorder.out_dir, recorder.run_id)
        except OSError:
            logger.exception("DIBOS-Trace (Org %s): Zusammenfassen in ZIP fehlgeschlagen", recorder.org_id)
        logger.info(
            "DIBOS-Trace (Org %s) beendet: %d Exchanges in %s",
            recorder.org_id, len(recorder.exchanges), recorder.out_dir,
        )
    return recorder.out_dir


def is_trace_running(org_id: int) -> bool:
    task = _active_traces.get(org_id)
    return task is not None and not task.done()


def _current_run_id(org_id: int) -> str | None:
    return _active_run_ids.get(org_id) if is_trace_running(org_id) else None


def cancel_trace(org_id: int) -> bool:
    task = _active_traces.get(org_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


async def start_trace_for_org(org_id: int, duration_minutes: float = 120) -> str:
    """Lädt die Org-Konfiguration, baut einen DibosClient und startet die
    Aufzeichnung als Hintergrund-Task. Gibt die run_id zurück.

    Wirft ValueError, wenn die Org nicht vollständig konfiguriert ist oder
    bereits eine Aufzeichnung läuft.
    """
    if is_trace_running(org_id):
        raise ValueError("Für diese Organisation läuft bereits eine Aufzeichnung.")

    # Opportunistische Bereinigung, falls der tägliche Retention-Loop seit dem
    # letzten Start noch nicht gelaufen ist (z.B. kurz nach einem Neustart).
    try:
        purge_old_traces()
    except Exception:
        logger.exception("Opportunistische DIBOS-Trace-Bereinigung fehlgeschlagen")

    from app.core.crypto import decrypt_secret
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.dibos import OrgDibosConfig

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        config = db.query(OrgDibosConfig).filter(OrgDibosConfig.org_id == org_id).first()
        if not config or not config.is_fully_configured:
            raise ValueError("DIBOS-Konfiguration unvollständig (URL, Gateway-/Servicekonto).")
        # is_fully_configured garantiert bereits, dass diese Felder gesetzt sind - hier
        # nur fuer die Typpruefung explizit gemacht (config.* bleibt sonst "str | None").
        assert (
            config.base_url and config.gateway_user and config.gateway_password_enc
            and config.service_user and config.service_password_enc
        )
        base_url, host, ag = config.base_url, config.host, config.ag
        poll_interval_seconds = config.poll_interval_seconds
        gateway_user = config.gateway_user
        gateway_password = decrypt_secret(config.gateway_password_enc)
        service_user = config.service_user
        service_password = decrypt_secret(config.service_password_enc)
        enrich_incidents = config.enrich_incidents
    finally:
        db.close()

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = trace_run_dir(org_id, run_id)
    recorder = ExchangeRecorder(out_dir, org_id, run_id, enrich_incidents=enrich_incidents)
    client = DibosClient(
        base_url, gateway_user, gateway_password, service_user, service_password,
        host=host, ag=ag, on_exchange=recorder.record,
    )

    task = asyncio.create_task(
        capture_traffic(client, recorder, duration_minutes, poll_interval_seconds)
    )
    _active_traces[org_id] = task
    _active_run_ids[org_id] = run_id
    logger.info("DIBOS-Trace gestartet: Org %s, Dauer %s min, run_id %s", org_id, duration_minutes, run_id)
    return run_id


def list_traces(org_id: int) -> list[dict]:
    """Listet vorhandene Aufzeichnungsläufe einer Org (aus den summary.json-Dateien)."""
    org_dir = TRACE_ROOT / str(org_id)
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


def read_latest(org_id: int, run_id: str) -> dict | None:
    """Liest den Live-Snapshot (latest.json) eines Laufs für die Admin-Live-Ansicht."""
    latest_path = trace_run_dir(org_id, run_id) / "latest.json"
    if not latest_path.is_file():
        return None
    try:
        return json.loads(latest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _parse_run_id(run_id: str) -> datetime | None:
    try:
        return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def delete_trace(org_id: int, run_id: str) -> bool:
    """Löscht einen einzelnen Aufzeichnungslauf. Läuft er noch, wird er zuerst abgebrochen."""
    if run_id == _current_run_id(org_id):
        cancel_trace(org_id)
    run_dir = trace_run_dir(org_id, run_id)
    if not run_dir.is_dir():
        return False
    shutil.rmtree(run_dir, ignore_errors=True)
    return True


def purge_old_traces(retention_days: int = TRACE_RETENTION_DAYS) -> int:
    """Löscht alle Aufzeichnungsläufe (aller Orgs), die älter als retention_days sind.

    Läuft ein Trace noch, wird er nie automatisch gelöscht — unabhängig vom Alter
    (Schutz über _current_run_id). Gibt die Anzahl gelöschter Läufe zurück.
    """
    if not TRACE_ROOT.is_dir():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted = 0
    for org_dir in TRACE_ROOT.iterdir():
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
            "DIBOS-Trace-Retention: %d abgelaufene Aufzeichnungsläufe gelöscht (älter als %d Tage).",
            deleted, retention_days,
        )
    return deleted


# ── Täglicher Retention-Loop (Muster: lis_capture.py / weather_retention.py) ─
_PURGE_HOUR = 4
_PURGE_MINUTE = 5  # 5min nach der LIS-Retention versetzt, damit beide nicht exakt gleichzeitig laufen

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


async def dibos_trace_retention_loop() -> None:
    """Täglicher Loop: löscht abgelaufene DIBOS-Trace-Aufzeichnungen (DSGVO — enthalten
    Personenbezug wie Anruferdaten, siehe Modul-Docstring)."""
    while True:
        await asyncio.sleep(_seconds_until_next(_PURGE_HOUR, _PURGE_MINUTE))
        try:
            await asyncio.to_thread(purge_old_traces)
        except Exception:
            logger.exception("Fehler im DIBOS-Trace-Retention-Loop")
