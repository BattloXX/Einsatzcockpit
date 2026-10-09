"""Teams notification helpers for road closures.

This module intentionally has no dependency on the incident alarm path.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.models.master import FireDept
from app.models.road_closure import RoadClosure, RoadClosureNotification, RoadClosureTeamsConfig
from app.services.road_closure_flags import strassensperren_effective_enabled
from app.services.road_closure_public_service import fingerprint, public_closure_dict

logger = logging.getLogger("einsatzleiter.road_closure_notify")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def ereignis_aus_changes(changes: list[dict], *, now: datetime) -> str | None:
    fields = {item.get("feld", item.get("field")) for item in changes}
    result = None
    for item in changes:
        if item.get("feld", item.get("field")) != "valid_until":
            continue
        before, after = item.get("vorher", item.get("before")), item.get("nachher", item.get("after"))
        if isinstance(before, str):
            before = datetime.fromisoformat(before.removesuffix("Z")) if before else None
        if isinstance(after, str):
            after = datetime.fromisoformat(after.removesuffix("Z")) if after else None
        if before is not None and after is None:
            result = "verlaengert"  # jetzt unbefristet
        elif after is not None and before is not None and after > before:
            result = "verlaengert"
        elif after is not None and (before is None or after < before):
            # Ende vorgezogen bzw. erstmals gesetzt: nur „vorzeitig beendet“, wenn es (fast) schon vorbei ist.
            result = "vorzeitig_beendet" if after <= now + timedelta(hours=1) else "geaendert"
    meaningful = {
        "restriction_type", "street", "from_text", "to_text", "city", "direction", "geometry_geojson",
        "valid_from", "max_weight_t", "max_height_m", "max_width_m", "max_length_m", "reason", "exceptions", "title",
    }
    if result is not None:
        return result
    return "geaendert" if fields & meaningful else None


def enqueue(db: Session, closure: RoadClosure, ereignis: str, *, source: str = "ui", user_id: int | None = None,
            manuell: bool = False, now: datetime | None = None) -> RoadClosureNotification | None:
    now = now or _now()
    try:
        with db.begin_nested():
            config = db.query(RoadClosureTeamsConfig).filter(RoadClosureTeamsConfig.org_id == closure.org_id).first()
            org = db.get(FireDept, closure.org_id)
            if (not config or not config.enabled or not config.webhook_url_enc or not org
                    or not strassensperren_effective_enabled(org.id, db)):
                return None
            automatic_flags = {
                "neu": config.auto_neu,
                "geaendert": config.auto_aenderung, "verlaengert": config.auto_aenderung,
                "vorzeitig_beendet": config.auto_aenderung, "reaktiviert": config.auto_aenderung,
                "aufgehoben": config.auto_aufhebung, "ersetzt": config.auto_aufhebung,
            }
            if not manuell and (not closure.teams_melden or not automatic_flags.get(ereignis, False)):
                return None
            fp = fingerprint(public_closure_dict(closure, org))
            key = f"manuell:{fp[:16]}:{now:%Y%m%d%H%M}" if manuell else f"{ereignis}:{fp[:16]}"
            existing = db.query(RoadClosureNotification).filter(
                RoadClosureNotification.road_closure_id == closure.id, RoadClosureNotification.dedup_key == key
            ).first()
            existing = existing or next((obj for obj in db.new if isinstance(obj, RoadClosureNotification)
                                         and obj.road_closure_id == closure.id and obj.dedup_key == key), None)
            if existing:
                return existing
            if not manuell:
                pending = db.query(RoadClosureNotification).filter(
                    RoadClosureNotification.road_closure_id == closure.id,
                    RoadClosureNotification.status.in_(("pending", "retry")),
                ).first()
                if pending:
                    pending.ereignis, pending.dedup_key = ereignis, key
                    pending.payload_fingerprint, pending.next_attempt_at = fp, now
                    return pending
            row = RoadClosureNotification(org_id=closure.org_id, road_closure_id=closure.id, ereignis=ereignis,
                dedup_key=key, status="pending", next_attempt_at=now, source=source, triggered_by_user_id=user_id,
                payload_fingerprint=fp, created_at=now)
            db.add(row)
            return row
    except Exception:
        logger.warning("Could not enqueue road closure Teams notification", exc_info=True)
        return None


def build_road_closure_card(data: dict, *, ereignis: str, detail_url: str | None, intern_url: str,
                            map_url: str | None) -> dict:
    labels = {"neu": "🚧 Neue Straßensperre", "geaendert": "✏️ Straßensperre geändert",
        "verlaengert": "⏩ Straßensperre verlängert", "vorzeitig_beendet": "✅ Straßensperre vorzeitig beendet",
        "aufgehoben": "✅ Straßensperre aufgehoben", "ersetzt": "🔁 Straßensperre ersetzt",
        "reaktiviert": "↩️ Straßensperre wieder aktiv", "manuell": "🚧 Straßensperre"}
    good = {"aufgehoben", "vorzeitig_beendet", "ersetzt"}
    color = "good" if ereignis in good else ("attention" if data.get("restriction_type") == "closed" else "warning")
    facts = [("Art", ", ".join([data.get("restriction_label", "")] + data.get("einschraenkungen", []))),
             ("Abschnitt", data.get("abschnitt")), ("Beginn", data.get("valid_from_local")),
             ("Ende", data.get("valid_until_local") or "unbefristet"), ("Grund", data.get("reason")),
             ("Einsatzgebiet", data.get("einsatzgebiet")), ("Ausnahmen", data.get("exceptions")),
             ("Richtung", data.get("direction_label"))]
    body = [{"type": "TextBlock", "text": labels.get(ereignis, labels["manuell"]), "color": color,
             "weight": "Bolder", "wrap": True},
            {"type": "TextBlock", "text": data.get("title", ""), "size": "Large", "weight": "Bolder", "wrap": True},
            {"type": "TextBlock", "text": data.get("city") or "", "wrap": True},
            {"type": "FactSet", "facts": [{"title": k, "value": str(v)} for k, v in facts if v]}]
    if map_url:
        body.append({"type": "Image", "url": map_url, "size": "Stretch", "altText": "Kartenausschnitt"})
    actions = []
    if detail_url:
        actions.append({"type": "Action.OpenUrl", "title": "Details anzeigen", "url": detail_url})
    actions.append({"type": "Action.OpenUrl", "title": "Im Einsatzcockpit öffnen", "url": intern_url})
    card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4",
            "msteams": {"width": "Full"}, "body": body, "actions": actions}
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "content": card,
    }]}


def build_test_card(org_name: str) -> dict:
    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "🚧 Testnachricht Straßensperren", "weight": "Bolder", "wrap": True},
            {"type": "TextBlock", "text": f"Die Teams-Anbindung von {org_name} funktioniert.", "wrap": True},
        ],
    }
    return {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive", "content": card,
    }]}


async def send_payload(webhook_url: str, payload: dict) -> tuple[bool, bool, str | None]:
    if not webhook_url.startswith("https://"):
        return False, False, "Ungültige Webhook-URL"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            response = await client.post(webhook_url, json=payload)
        if response.status_code >= 400:
            return False, response.status_code == 429 or response.status_code >= 500, f"HTTP {response.status_code}"
        return True, False, None
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return False, True, type(exc).__name__
    except Exception as exc:
        return False, False, type(exc).__name__
