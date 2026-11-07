"""Lesende, org-gefilterte technische Infoscreen-Diagnose ohne Zugangsdaten."""
from datetime import UTC, datetime, timedelta

from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.infoscreen_agent import InfoscreenDevice, InfoscreenDisplay
from app.services.infoscreen_agent_resolver import Display, resolve
from app.services.infoscreen_agent_snapshot import alarm_url, lade_overrides, lade_schedule
from app.services.infoscreen_aktivitaet import lade_aktivitaet


@register_tool(
    name="infoscreen_geraete_liste",
    description="Liest technische Infoscreen-Geraete der eigenen Organisation ohne Zugangsdaten.",
    required_roles=("org_admin",),
)
async def infoscreen_geraete_liste(context: MCPContext) -> dict[str, object]:
    now = datetime.now(UTC).replace(tzinfo=None)
    devices = context.db.query(InfoscreenDevice).filter(InfoscreenDevice.org_id == context.org_id).order_by(
        InfoscreenDevice.id).all()
    displays = context.db.query(InfoscreenDisplay).filter(InfoscreenDisplay.org_id == context.org_id).all()
    return {"geraete": [
        {"id": d.id, "name": d.name,
         "status": "online" if d.status == "active" and (
             (d.agent_presence_expires_at and d.agent_presence_expires_at > now)
             or (d.last_seen_at and d.last_seen_at > now - timedelta(minutes=3))) else "offline",
         "agent_version": d.agent_version, "os_version": d.os_version,
         "last_heartbeat": d.last_seen_at.isoformat() + "Z" if d.last_seen_at else None,
         "channel": d.channel, "update_status": d.update_status, "update_error_code": d.update_error_code,
         "display_count": sum(s.device_id == d.id for s in displays)} for d in devices]}


@register_tool(
    name="infoscreen_display_status",
    description="Liest aktuellen Resolver-Sollzustand und letzten Display-Heartbeat ohne URLs oder Einsatzdaten.",
    required_roles=("org_admin",),
)
async def infoscreen_display_status(context: MCPContext, device_id: int) -> dict[str, object]:
    device = context.db.query(InfoscreenDevice).filter(
        InfoscreenDevice.id == device_id, InfoscreenDevice.org_id == context.org_id).first()
    if device is None:
        raise ValueError("Geraet nicht gefunden.")
    now = datetime.now(UTC).replace(tzinfo=None)
    activity = lade_aktivitaet(context.db, context.org_id)
    displays = context.db.query(InfoscreenDisplay).filter(
        InfoscreenDisplay.org_id == context.org_id, InfoscreenDisplay.device_id == device.id).order_by(
            InfoscreenDisplay.id).all()
    result = []
    for display in displays:
        url = alarm_url(context.db, display)
        state = resolve(Display(
            display.id, context.org_id, url or "about:blank",
            device.status != "active" or not display.aktiv or url is None,
            display.gsl_override_erlaubt, display.nachlauf_min, device.config_version, display.revision),
            now, activity, lade_overrides(context.db, display), lade_schedule(context.db, display))
        reported = display.reported_status or {}
        # Explizite Feldauswahl: weder Resolver-URL/Rotation noch rohe JSON-Diagnose ausgeben.
        result.append({"id": display.id, "name": display.name, "mode": state["mode"],
                       "display_power": state["display_power"], "valid_until": state["valid_until"],
                       "power": reported.get("power"), "page_state": reported.get("page_state"),
                       "backend": reported.get("backend"), "verified": reported.get("verified"),
                       "override_active": state["mode"] in ("override", "manual")})
    return {"device_id": device.id, "displays": result}
