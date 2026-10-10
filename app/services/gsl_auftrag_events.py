"""Nebenwirkungen von GSL-Auftragsereignissen nach dem Datenbank-Commit."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.services import gk_zugang_service

logger = logging.getLogger("einsatzleiter.gsl_auftrag_events")


@dataclass(frozen=True)
class Benachrichtigung:
    """Nach dem Commit auszuführende, bewusst best-effort Nebenwirkungen."""

    sms: Any | None
    einheit_id: int
    dispatch_id: int
    ereignis: str


def plane_benachrichtigungen(
    db: Any, lage: Any, einheit: Any, dispatch: Any,
    ereignis: Literal["neu", "geaendert", "zurueckgezogen"],
) -> Benachrichtigung:
    """Plant die SMS im Savepoint; ein Fehler darf den Auftrag nie zurückrollen."""
    sms = None
    try:
        with db.begin_nested():
            sms = gk_zugang_service.plane_auftrag_sms(db, lage, einheit, dispatch, ereignis)
    except Exception:
        logger.exception("Auftrags-SMS konnte nicht geplant werden (lage=%s, dispatch=%s)", lage.id, dispatch.id)
    return Benachrichtigung(sms=sms, einheit_id=einheit.id, dispatch_id=dispatch.id, ereignis=ereignis)


async def sende_nach_commit(benachrichtigung: Benachrichtigung) -> None:
    """Versendet SMS und Tablet-Push erst nach erfolgreichem Commit."""
    if benachrichtigung.sms:
        await gk_zugang_service.sende_auto_sms(benachrichtigung.sms)
    from app.services.gsl_einheit_push import push_einheit_auftrag

    # Der Push nutzt blockierende Netzwerkaufrufe und läuft daher im Thread-Pool.
    await asyncio.to_thread(
        push_einheit_auftrag, benachrichtigung.einheit_id, benachrichtigung.dispatch_id, benachrichtigung.ereignis
    )
