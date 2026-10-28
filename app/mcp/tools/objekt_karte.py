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
        _karten_objekt_dict(k) | {
            "symbolname": labels.get(k.typ, k.typ), "sort": k.sort,
            "position_status": k.position_status, "revision": k.revision,
            "quelle_dokument_id": k.quelle_dokument_id, "quelle_seite": k.quelle_seite,
            "genauigkeit_m": k.genauigkeit_m, "geprueft_von": k.geprueft_von,
            "geprueft_am": k.geprueft_am.isoformat() if k.geprueft_am else None,
        }
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


@register_tool(
    name="objekt_karte_vorschlaege",
    description=(
        "Verwaltet bis 30 Kartenvorschläge: anlegen, aendern, verwerfen, auflisten. "
        "Koordinaten nie erfinden; ohne belastbare Position lat/lng weglassen. "
        "Vorschläge ändern die produktive Karte nicht. Freigabe erfolgt separat durch Berechtigte. "
        "pruefen=true: Trockenlauf mit Rollback. aendern: genau ein Eintrag und vorschlag_id. "
        "Eintrag: symbol_code, operation (hinzufuegen|aendern|entfernen), lat/lng oder geometry, label, "
        "rotation, position_methode, genauigkeit_m, erkennungs_konfidenz (0..1), quelle_dokument_id, "
        "quelle_seite, quelle_ausschnitt ([x0,y0,x1,y1], 0..1), begruendung. "
        "Markeränderungen benötigen basis_marker_id und expected_revision. filter_status ist der Freigabestatus."
    ),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_vorschlaege(
    context: MCPContext, objekt_id: int, aktion: str,
    vorschlaege: list[dict] | None = None, vorschlag_id: int | None = None,
    filter_status: str | None = None, pruefen: bool = False,
) -> dict[str, object]:
    from app.core.permissions import has_role
    from app.mcp.context import MCPPermissionError
    from app.services import objekt_karte_vorschlag_service as service

    if aktion not in {"anlegen", "aendern", "verwerfen", "auflisten"}:
        raise ValueError("Unbekannte Aktion")
    if context.org_id != context.user.org_id:
        raise MCPPermissionError("Organisation stimmt nicht ueberein")
    if aktion != "auflisten" and not has_role(context.user, "objekt_verwalter"):
        raise MCPPermissionError("Objektverwaltung erforderlich")
    db = context.db
    objekt = service.produktives_objekt(db, context.org_id, objekt_id)
    if aktion == "auflisten":
        return {"vorschlaege": [
            service.vorschlag_dict(v) for v in service.vorschlaege_auflisten(db, objekt, filter_status)
        ]}
    if vorschlaege is not None and (not isinstance(vorschlaege, list) or len(vorschlaege) > 30
                                   or any(not isinstance(v, dict) for v in vorschlaege)):
        raise ValueError("Maximal 30 Vorschlaege als Liste von Objekten")
    if aktion == "anlegen" and (not vorschlaege or vorschlag_id is not None):
        raise ValueError("anlegen benoetigt Vorschlaege ohne vorschlag_id")
    if aktion == "aendern" and (vorschlag_id is None or not vorschlaege or len(vorschlaege) != 1):
        raise ValueError("aendern benoetigt vorschlag_id und genau einen Vorschlag")
    if aktion == "verwerfen" and (vorschlag_id is None or vorschlaege):
        raise ValueError("verwerfen benoetigt nur vorschlag_id")
    try:
        if aktion == "verwerfen":
            assert vorschlag_id is not None
            ergebnisse = [service.vorschlag_verwerfen(db, objekt, vorschlag_id, user_id=context.user.id)]
        else:
            ergebnisse = [service.vorschlag_speichern(db, objekt, daten, user_id=context.user.id,
                          vorschlag_id=vorschlag_id) for daten in (vorschlaege or [])]
        antwort: dict[str, object] = {"vorschlaege": ergebnisse, "gespeichert": not pruefen}
        if pruefen:
            db.rollback()
        else:
            db.commit()
        return antwort
    except Exception:
        db.rollback()
        raise


@register_tool(
    name="objekt_karte_pruefen",
    description=("Prüft die Objektkarte lesend auf Dubletten, fehlende Positionen und veraltete Quellen. "
                 "Koordinaten nie erfinden. Veraltet wird nur in der Antwort markiert; Freigabe erfolgt separat."),
    required_roles=EINSATZ_LESE_ROLLEN,
    module_check=objekt_modul_aktiv,
)
async def objekt_karte_pruefen(context: MCPContext, objekt_id: int) -> dict[str, object]:
    from app.services.objekt_karte_vorschlag_service import produktives_objekt, pruefe_karte

    return pruefe_karte(context.db, produktives_objekt(context.db, context.org_id, objekt_id))
