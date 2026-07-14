"""MS Teams Webhook – Adaptive Card Posting."""
from __future__ import annotations

import logging

logger = logging.getLogger("einsatzleiter.teams")


async def post_teams_karte(webhook_url: str, titel: str, text: str, url: str | None = None) -> bool:
    """Sendet eine MessageCard an den angegebenen Teams-Webhook.

    Async (httpx.AsyncClient) statt synchronem httpx.post — ein synchroner Aufruf
    würde den Event-Loop bis zu 10 s blockieren und damit ALLE gleichzeitigen
    Requests des Worker-Prozesses verzögern, nicht nur den aufrufenden (STAB-4).
    Fehler werden nur geloggt, nicht weitergegeben (non-blocking).
    Gibt True bei Erfolg zurück.
    """
    import httpx

    if not webhook_url or not webhook_url.startswith("https://"):
        logger.warning("Teams-Webhook-URL ungültig oder leer")
        return False

    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": titel,
        "themeColor": "d42225",
        "sections": [{"activityTitle": titel, "activityText": text}],
    }
    if url:
        payload["potentialAction"] = [{
            "@type": "OpenUri",
            "name": "Öffnen",
            "targets": [{"os": "default", "uri": url}],
        }]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Teams-Webhook-Fehler: %s", exc)
        return False


async def post_teams_adaptive_card(webhook_url: str, payload: dict) -> bool:
    """Sendet ein bereits fertig gebautes Adaptive-Card-Payload (siehe teams_card.py)
    an den angegebenen Teams-Webhook. Anders als post_teams_karte() (MessageCard) kann
    diese Karte Bilder enthalten, die auch über Workflows-Webhooks zuverlässig
    dargestellt werden (siehe teams_card.py-Moduldocstring)."""
    import httpx

    if not webhook_url or not webhook_url.startswith("https://"):
        logger.warning("Teams-Webhook-URL ungültig oder leer")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Teams-Webhook-Fehler: %s", exc)
        return False
