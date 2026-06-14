"""Resilience-Helfer für Post-Commit-Nebenwirkungen.

Muster: Einsatz ist sicher gespeichert → jede Folge-Aktion darf einzeln
scheitern, ohne dass der gesamte Request oder das Alert-System abbricht.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("einsatzleiter.resilience")


def run_side_effect(label: str, fn, *args, **kwargs):
    """Führt fn(*args, **kwargs) aus; loggt Fehler, wirft nie."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("Nebenwirkung '%s' fehlgeschlagen (kein Datenverlust)", label)
        return None


async def run_side_effect_async(label: str, coro):
    """Awaitet ein Coroutine-Objekt; loggt Fehler, wirft nie.

    Verwendung:
        await run_side_effect_async("broadcast", broadcast_org(org_id, event))
    """
    try:
        return await coro
    except Exception:
        logger.exception("Async-Nebenwirkung '%s' fehlgeschlagen (kein Datenverlust)", label)
        return None
