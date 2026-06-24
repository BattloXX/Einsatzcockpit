"""MS Teams Webhook – Adaptive Card Posting."""
from __future__ import annotations

import logging

logger = logging.getLogger("einsatzleiter.teams")


def post_teams_karte(webhook_url: str, titel: str, text: str, url: str | None = None) -> bool:
    """Sendet eine MessageCard an den angegebenen Teams-Webhook.

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
        resp = httpx.post(webhook_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Teams-Webhook-Fehler: %s", exc)
        return False
