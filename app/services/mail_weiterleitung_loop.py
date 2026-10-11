"""Hintergrundlauf: Weiterleitungen senden/wiederholen und Protokoll bereinigen."""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.services.mail_weiterleitung_service import bereinige_logs, verarbeite_faellige

logger = logging.getLogger("einsatzleiter.mail_weiterleitung_loop")


async def run_mail_weiterleitung_once(log_id: int | None = None) -> int:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        n = await verarbeite_faellige(db, log_id)
        if log_id is None:
            bereinige_logs(db)
        return n
    finally:
        db.close()


async def mail_weiterleitung_loop() -> None:
    if not settings.MAIL_WEITERLEITUNG_LOOP_ENABLED:
        return
    while True:
        try:
            await run_mail_weiterleitung_once()
        except Exception:
            logger.exception("mail_weiterleitung.loop_failed")
        await asyncio.sleep(max(1, settings.MAIL_WEITERLEITUNG_LOOP_INTERVAL_S))
