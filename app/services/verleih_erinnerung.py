"""Automatische Geräteverleih-Erinnerungs-SMS – Background-Loop.

Prüft alle 60 Sekunden ob fällige Erinnerungen vorhanden sind:
- status=ausgeliehen
- erinnerung_geplant_at <= now
- erinnerung_gesendet_at is None
- telefon not null
"""
import asyncio
import logging
from datetime import UTC, datetime

from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.verleih import VerleihAusleihe, VerleihStatus
from app.services import verleih_service as svc

logger = logging.getLogger("einsatzleiter.verleih_erinnerung")


async def verleih_erinnerung_loop() -> None:
    from app.services.loop_utils import iteration_watch
    while True:
        await asyncio.sleep(60)
        try:
            with iteration_watch(logger, "verleih_erinnerung_loop", 60):
                await _check_fällige_erinnerungen()
        except Exception:
            logger.exception("Fehler im Verleih-Erinnerungs-Loop")


def _lade_faellige() -> list[dict]:
    """DB-Arbeit für den Threadpool (Audit B2): fällige Erinnerungen als plaine Dicts."""
    with SessionLocal() as db:
        set_tenant_context(db, None)  # system_admin-Modus: alle Orgs
        now = datetime.now(UTC)
        faellige = (
            db.query(VerleihAusleihe)
            .filter(
                VerleihAusleihe.status == VerleihStatus.ausgeliehen,
                VerleihAusleihe.erinnerung_geplant_at <= now,
                VerleihAusleihe.erinnerung_gesendet_at.is_(None),
                VerleihAusleihe.telefon.isnot(None),
            )
            .all()
        )
        return [
            {
                "id": a.id,
                "org_id": a.org_id,
                "telefon": a.telefon,
                "text": svc.get_sms_erinnerung_text(db, a.org_id, a),  # type: ignore[arg-type]
            }
            for a in faellige
        ]


def _markiere_gesendet(ausleihe_id: int) -> None:
    with SessionLocal() as db:
        set_tenant_context(db, None)
        ausleihe = db.get(VerleihAusleihe, ausleihe_id)
        if ausleihe is not None and ausleihe.erinnerung_gesendet_at is None:
            ausleihe.erinnerung_gesendet_at = datetime.now(UTC)
            db.commit()


async def _check_fällige_erinnerungen() -> None:
    from app.services.sms_service import send_sms

    for item in await asyncio.to_thread(_lade_faellige):
        try:
            ok = await send_sms(item["org_id"], item["telefon"], item["text"])
            if ok:
                await asyncio.to_thread(_markiere_gesendet, item["id"])
                logger.info(
                    "Erinnerungs-SMS gesendet: Ausleihe %s, Org %s",
                    item["id"], item["org_id"],
                )
            else:
                logger.warning("Erinnerungs-SMS fehlgeschlagen: Ausleihe %s", item["id"])
        except Exception:
            logger.exception("Fehler bei Erinnerungs-SMS für Ausleihe %s", item["id"])
