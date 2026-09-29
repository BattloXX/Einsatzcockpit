from app.mcp.context import MCPContext
from app.mcp.registry import register_tool


@register_tool(
    name="mcp_whoami",
    description="Zeigt den aktuell verbundenen Einsatzcockpit-Benutzer.",
    required_roles=("readonly",),
)
async def mcp_whoami(context: MCPContext) -> dict[str, object]:
    return {
        "name": context.user.display_name,
        "org": context.user.org.name if context.user.org else "",
        "rollen": sorted(context.user.role_codes),
    }
