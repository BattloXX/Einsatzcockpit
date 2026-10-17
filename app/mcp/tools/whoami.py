from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.master import Member


@register_tool(
    name="mcp_whoami",
    description="Zeigt den aktuell verbundenen Einsatzcockpit-Benutzer.",
    required_roles=("readonly",),
)
async def mcp_whoami(context: MCPContext) -> dict[str, object]:
    from app.services.einheit_service import einheiten_fuer_benutzer

    member = None
    if context.user.member_id is not None:
        member = (
            context.db.query(Member)
            .filter(Member.id == context.user.member_id, Member.org_id == context.org_id)
            .first()
        )
    return {
        "name": context.user.display_name,
        "org": context.user.org.name if context.user.org else "",
        "rollen": sorted(context.user.role_codes),
        "mitglied": {"id": member.id, "name": member.full_name} if member else None,
        "fuehrt_einheiten": [
            {
                "lage_id": lage.id,
                "lage_name": lage.name,
                "einheit_id": einheit.id,
                "einheit_label": einheit.label,
                "is_exercise": lage.is_exercise,
            }
            for einheit, lage, _leader in einheiten_fuer_benutzer(context.db, context.user)
        ],
    }
