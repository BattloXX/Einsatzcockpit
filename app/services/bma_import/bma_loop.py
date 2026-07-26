"""Background-Loops fuer den BMA-Webplattform-Import (Landeswarnzentrale Vorarlberg).

Zwei getrennte Loops:
- bma_import_loop: prueft periodisch, ob fuer eine Org der taegliche Sync faellig
  ist (pro Org konfigurierbare Uhrzeit, OrgBmaImportConfig.sync_stunde/sync_minute -
  anders als der globale Fixzeitpunkt in nachschlagewerk_sync.py, da jede Org einen
  eigenen Zeitpunkt waehlen kann). Ein Org-Fehler blockiert nie den Zyklus fuer
  andere Orgs (Muster: lis_loop.py/dibos_loop.py).
- bma_import_keepalive_loop: haelt das hinterlegte Session-Cookie zwischen den
  taeglichen Laeufen aktiv (siehe app/services/bma_import/bma_client.py::keepalive) -
  unabhaengig vom Sync-Intervall, da die tatsaechliche Lebensdauer einer Session
  unbekannt ist (siehe Plan "BMA-Import", Abschnitt Risiken).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.services.loop_utils import iteration_watch

logger = logging.getLogger("einsatzleiter.bma_import.loop")

# Pruefintervall des taeglichen Sync-Loops - kein fester Alarm, sondern ein
# periodischer Blick auf "ist fuer irgendeine Org der konfigurierte Zeitpunkt
# heute schon erreicht und noch nicht gelaufen".
_CHECK_INTERVAL_S = 300

try:
    from zoneinfo import ZoneInfo
    _VIENNA_TZ = ZoneInfo("Europe/Vienna")
except Exception:  # pragma: no cover
    _VIENNA_TZ = UTC  # type: ignore[assignment]


def _zu_wien(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_VIENNA_TZ)


def _ist_heute_faellig(letzter_lauf_am: datetime | None, sync_stunde: int, sync_minute: int) -> bool:
    """True, wenn der geplante Zeitpunkt heute (Europe/Vienna) bereits erreicht ist
    UND seitdem noch kein Lauf stattgefunden hat."""
    jetzt = datetime.now(_VIENNA_TZ)
    geplant_heute = jetzt.replace(hour=sync_stunde, minute=sync_minute, second=0, microsecond=0)
    if jetzt < geplant_heute:
        return False
    letzter_lauf_wien = _zu_wien(letzter_lauf_am)
    if letzter_lauf_wien is not None and letzter_lauf_wien >= geplant_heute:
        return False  # heute (seit dem geplanten Zeitpunkt) bereits gelaufen
    return True


async def bma_import_loop() -> None:
    from app.config import settings
    if not getattr(settings, "BMA_IMPORT_ENABLED", True):
        logger.info("bma_import_loop: deaktiviert (BMA_IMPORT_ENABLED=False)")
        return

    logger.info("bma_import_loop gestartet (Pruefintervall %ds)", _CHECK_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_CHECK_INTERVAL_S)
            with iteration_watch(logger, "bma_import_loop", _CHECK_INTERVAL_S):
                await _pruefe_alle_orgs()
        except asyncio.CancelledError:
            logger.info("bma_import_loop beendet")
            break
        except Exception:
            logger.exception("bma_import_loop: Iteration fehlgeschlagen")


async def _pruefe_alle_orgs() -> None:
    def _lade_faellige() -> list[tuple[int, int]]:
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.bma_import import OrgBmaImportConfig
        from app.models.master import FireDept
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            configs = (
                db.query(OrgBmaImportConfig)
                .filter(OrgBmaImportConfig.enabled == True)  # noqa: E712
                .all()
            )
            faellig = []
            for cfg in configs:
                if not _ist_heute_faellig(cfg.letzter_lauf_am, cfg.sync_stunde, cfg.sync_minute):
                    continue
                org = db.get(FireDept, cfg.org_id)
                if org:
                    faellig.append((org.id, cfg.id))
            return faellig
        finally:
            db.close()

    paare = await asyncio.to_thread(_lade_faellige)
    for org_id, config_id in paare:
        try:
            await _sync_one_org(org_id, config_id)
        except Exception:
            logger.exception("bma_import_loop: Org %s fehlgeschlagen", org_id)


async def _sync_one_org(org_id: int, config_id: int) -> None:
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.models.bma_import import OrgBmaImportConfig
    from app.models.master import FireDept
    from app.services.bma_import.bma_sync import sync_org_bma

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        org = db.get(FireDept, org_id)
        config = db.get(OrgBmaImportConfig, config_id)
        if not org or not config or not config.enabled:
            return
        lauf = await sync_org_bma(db, org, config, ausloeser="loop")
        logger.info(
            "bma_import_loop: Org %s fertig (status=%s, gefunden=%d, neu=%d, aktualisiert=%d, "
            "vorschlaege=%d, fehler=%d)",
            org_id, lauf.status, lauf.gefunden, lauf.neu_angelegt, lauf.aktualisiert,
            lauf.vorschlaege, lauf.fehler,
        )
    finally:
        db.close()


async def bma_import_keepalive_loop() -> None:
    from app.config import settings
    if not getattr(settings, "BMA_IMPORT_ENABLED", True):
        return

    interval = getattr(settings, "BMA_IMPORT_KEEPALIVE_INTERVAL_S", 600)
    logger.info("bma_import_keepalive_loop gestartet (Intervall %ds)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            with iteration_watch(logger, "bma_import_keepalive_loop", interval):
                await _keepalive_alle_orgs()
        except asyncio.CancelledError:
            logger.info("bma_import_keepalive_loop beendet")
            break
        except Exception:
            logger.exception("bma_import_keepalive_loop: Iteration fehlgeschlagen")


async def _keepalive_alle_orgs() -> None:
    def _lade_konfigurierte() -> list[tuple[int, str, str]]:
        from app.core.crypto import decrypt_secret
        from app.core.tenant import set_tenant_context
        from app.db import SessionLocal
        from app.models.bma_import import OrgBmaImportConfig
        db = SessionLocal()
        set_tenant_context(db, None)
        try:
            configs = (
                db.query(OrgBmaImportConfig)
                .filter(
                    OrgBmaImportConfig.enabled == True,  # noqa: E712
                    OrgBmaImportConfig.keepalive_aktiv == True,  # noqa: E712
                )
                .all()
            )
            ergebnis: list[tuple[int, str, str]] = []
            for cfg in configs:
                if not cfg.is_fully_configured:
                    continue
                # is_fully_configured garantiert base_url/session_cookie_enc gesetzt - hier
                # nur fuer die Typpruefung explizit gemacht (Muster: lis_sync.py::sync_organization).
                assert cfg.base_url and cfg.session_cookie_enc
                try:
                    ergebnis.append((cfg.org_id, cfg.base_url, decrypt_secret(cfg.session_cookie_enc)))
                except Exception:
                    logger.exception("bma_import_keepalive_loop: Cookie fuer Org %s nicht entschluesselbar", cfg.org_id)
            return ergebnis
        finally:
            db.close()

    konfigurierte = await asyncio.to_thread(_lade_konfigurierte)
    for org_id, base_url, cookie in konfigurierte:
        from app.services.bma_import.bma_client import BmaClient
        client = BmaClient(base_url, cookie)
        try:
            ok = await client.keepalive()
            if not ok:
                logger.info("bma_import_keepalive_loop: Org %s - Session vermutlich abgelaufen", org_id)
        except Exception:
            logger.exception("bma_import_keepalive_loop: Org %s fehlgeschlagen", org_id)
        finally:
            await client.aclose()
