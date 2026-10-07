"""MCP-Zugriff auf die Stammdaten der eigenen Organisation."""

import asyncio
import base64
import hashlib
from pathlib import Path

from app.config import settings
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.master import FireDept, OrgSettings

_STANDARDLOGO = "/static/img/Logo-rot.png"
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


def _logo_datei(logo_path: str) -> tuple[str, Path, bool]:
    """Liefert ausschliesslich existierende Dateien unter app/static."""
    static_root = Path("app/static").resolve()
    datei = (Path("app") / logo_path.lstrip("/")).resolve()
    if datei.is_relative_to(static_root) and datei.is_file():
        return logo_path, datei, logo_path == _STANDARDLOGO

    standard_datei = (Path("app") / _STANDARDLOGO.lstrip("/")).resolve()
    return _STANDARDLOGO, standard_datei, True


@register_tool(
    name="organisation_lesen",
    description="Liest Stammdaten und Logo der eigenen Organisation (z. B. fuer Briefkoepfe oder Berichte).",
    required_roles=("readonly",),
)
async def organisation_lesen(context: MCPContext, logo_als_bild: bool = True) -> dict[str, object]:
    org = context.db.get(FireDept, context.org_id)
    if org is None:
        raise ValueError("Die eigene Organisation wurde nicht gefunden.")
    org_settings = context.db.query(OrgSettings).filter(OrgSettings.org_id == context.org_id).first()
    logo_path, datei, ist_standardlogo = _logo_datei(
        (org_settings.logo_path if org_settings and org_settings.logo_path else None)
        or org.logo_path
        or _STANDARDLOGO
    )
    inhalt = await asyncio.to_thread(datei.read_bytes)
    mime = _MIME_TYPES.get(datei.suffix.lower(), "application/octet-stream")
    logo: dict[str, object] = {
        "url": f"{settings.effective_public_base_url.rstrip('/')}{logo_path}",
        "ist_standardlogo": ist_standardlogo,
        "dateiname": datei.name,
        "mime": mime,
        "groesse_bytes": len(inhalt),
        "sha256": hashlib.sha256(inhalt).hexdigest(),
    }
    if logo_als_bild:
        if len(inhalt) > settings.MCP_LOGO_INLINE_MAX_BYTES:
            logo["hinweis"] = (
                f"Logo zu gross fuer inline ({len(inhalt)} Bytes, "
                f"max. {settings.MCP_LOGO_INLINE_MAX_BYTES} Bytes)."
            )
        elif mime == "image/svg+xml":
            logo["svg_text"] = inhalt.decode("utf-8", errors="replace")
            logo["hinweis"] = "SVG wird nicht als Bild geliefert, Quelltext in svg_text."
        else:
            logo["inhalt_base64"] = base64.b64encode(inhalt).decode("ascii")

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "short_code": org.short_code,
        "bos": org.bos,
        "color": org.color,
        "contact_email": org.contact_email,
        "contact_phone": org.contact_phone,
        "street": org.street,
        "city": org.city,
        "timezone": org.timezone,
        "fallback_lat": org.fallback_lat,
        "fallback_lng": org.fallback_lng,
        "primary_color": org_settings.primary_color if org_settings else None,
        "footer_text": org_settings.footer_text if org_settings else None,
        "logo": logo,
    }
