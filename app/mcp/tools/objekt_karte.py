"""MCP-Lesewerkzeuge fuer Lagekarten und den Symbolkatalog der Organisation."""

from app.core.permissions import EINSATZ_LESE_ROLLEN
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.mcp.tools.objekt import objekt_modul_aktiv
from app.models.objekt import OBJEKT_SYMBOL_TYPEN, Objekt
from app.services.objekt_karte_service import _karten_objekt_dict, karten_objekte_lesen, symbolkatalog_lesen
from app.services.objekt_symbol_service import lade_symbol_katalog


@register_tool(
    name="objekt_karte_lesen",
    description="Liest Objekt-Lagekarte und optional die vorhandene Arbeitskopie. Koordinaten nie erfinden.",
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_lesen(context: MCPContext, objekt_id: int, arbeitskopie: bool = False) -> dict[str, object]:
    db, org_id = context.db, context.org_id
    objekt = db.query(Objekt).filter(Objekt.id == objekt_id, Objekt.org_id == org_id).first()
    if objekt is None:
        raise ValueError("Objekt nicht gefunden.")
    kopie = db.query(Objekt).filter(Objekt.entwurf_von_id == objekt.id, Objekt.org_id == org_id).first()
    basis_objekt_id = objekt.entwurf_von_id
    if arbeitskopie and kopie is not None:
        basis_objekt_id = objekt.id
        objekt = kopie
    # Auch inaktive Katalogeintraege koennen noch von Bestandsmarkern verwendet werden.
    labels = dict(OBJEKT_SYMBOL_TYPEN) | {
        s.code: s.name for s in lade_symbol_katalog(db, org_id, nur_aktive=False)
    }
    marker = [
        _karten_objekt_dict(k) | {"symbolname": labels.get(k.typ, k.typ), "sort": k.sort}
        for k in karten_objekte_lesen(db, objekt)
    ]
    return {
        "objekt": {"id": objekt.id, "name": objekt.name, "lat": objekt.lat, "lng": objekt.lng},
        "marker": marker,
        "anzahl_marker": len(marker),
        "basis_objekt_id": basis_objekt_id,
        "hat_arbeitskopie": kopie is not None,
        "arbeitskopie_id": kopie.id if kopie else None,
        "hinweis": "Legacy-Marker sind ungeprüfter Bestand. Koordinaten nie erfinden.",
    }


@register_tool(
    name="objekt_karte_symbolkatalog",
    description="Liest den Symbolkatalog der eigenen Organisation. Koordinaten nie erfinden.",
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_symbolkatalog(context: MCPContext) -> dict[str, object]:
    return {"symbole": symbolkatalog_lesen(context.db, context.org_id)}
