"""Datensparsame zentrale Suche ueber dieselbe Engine wie die Oberflaeche."""

from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.permissions import ROLES
from app.mcp.context import MCPContext, MCPPermissionError, load_live_context
from app.mcp.registry import TOOLS, register_tool
from app.models.katplan import KatplanDokumentSeite
from app.models.master import OrgSettings
from app.models.objekt import ObjektDokumentSeite
from app.services.suche.abfrage import suche
from app.services.suche.synonyme import entferne_telefonnummern
from app.services.suche.vertrag import SuchAnfrage, Treffer

# has_role kennt nur Admin-Ausnahmen, keine Ranghierarchie; identisch zu ui_suche._leser.
_LESE_ROLLEN = tuple(rolle for rolle, rang in ROLES.items() if rang >= ROLES["readonly"])
_DETAILS = {
    "objekt": "objekt_lesen",
    "objekt_dokument_seite": "objekt_dokument_text",
    "katplan_element": "katplan_element_lesen",
    "katplan_seite": "katplan_dokument_auszug",
    "gsl_stelle": "gsl_einsatzstelle_lesen",
    "gsl_meldung": "gsl_journal",
    "gsl_einheit": "gsl_ressource_details",
    "einsatz": "einsatz_lesen",
    "einsatz_meldung": "einsatz_lesen",
    "strassensperre": "strassensperre_lesen",
    "wasserstelle": "wasserstelle_lesen",
}
_FELDER = (
    "quelle", "quell_id", "titel", "untertitel", "auszug", "fundstelle", "status",
    "ist_aktuell", "grund", "distanz_m", "stand", "url",
)


def suche_aktiv(org_id: int, db: Session) -> bool:
    if not settings.SEARCH_ENABLED or not settings.SEARCH_MCP_ENABLED:
        return False
    org = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(org and org.suche_aktiv)


def _utc(wert: datetime | None) -> str | None:
    if wert is None:
        return None
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=UTC)
    return wert.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _details_erlaubt(context: MCPContext, name: str) -> bool:
    definition = TOOLS.get(name)
    if definition is None:
        return False
    try:
        load_live_context(context.db, context.user.id, context.org_id, definition.required_roles)
    except MCPPermissionError:
        return False
    return definition.module_check is None or definition.module_check(context.org_id, context.db)


def _parameter(context: MCPContext, t: Treffer, anfrage: SuchAnfrage) -> dict[str, object] | None:
    if t.quelle == "katplan_element":
        plan_id, _, element_id = t.quell_id.partition(":")
        return {"plan_id": int(plan_id), "element_id": element_id}
    if t.quelle == "objekt_dokument_seite":
        seite = context.db.query(ObjektDokumentSeite).filter(
            ObjektDokumentSeite.org_id == context.org_id, ObjektDokumentSeite.id == int(t.quell_id),
        ).first()
        if seite:
            return {"dokument_id": seite.dokument_id, "von_seite": seite.seiten_nr, "bis_seite": seite.seiten_nr}
        return None
    if t.quelle == "katplan_seite":
        planseite = context.db.query(KatplanDokumentSeite).filter(
            KatplanDokumentSeite.org_id == context.org_id, KatplanDokumentSeite.id == int(t.quell_id),
        ).first()
        if planseite:
            return {"plan_id": planseite.plan_id, "dokument_id": planseite.dokument_id, "seite": planseite.seiten_nr}
        return None
    if t.quelle in ("gsl_stelle", "gsl_einheit", "gsl_meldung"):
        parameter: dict[str, object] = {"lage_id": anfrage.kontext_id}
        if t.quelle == "gsl_stelle":
            parameter["site_id"] = int(t.quell_id)
        elif t.quelle == "gsl_einheit":
            parameter["einheit_id"] = int(t.quell_id)
        return parameter
    if t.quelle == "einsatz_meldung":
        return {"incident_id": anfrage.kontext_id}
    feld = {"objekt": "objekt_id", "einsatz": "incident_id",
            "strassensperre": "road_closure_id", "wasserstelle": "wasserstelle_id"}.get(t.quelle)
    return {feld: int(t.quell_id)} if feld else None


@register_tool(
    name="ecp_suchen",
    description=(
        "Durchsucht Objekte, Objektdokumente, freigegebene Katastrophenplan-Elemente und -Quellseiten "
        "im Index sowie live Einsätze, Straßensperren und Wasserstellen; mit kontext=gsl auch "
        "Einsatzstellen, Meldungen und Einheiten einer Lage. Liefert Fundstellen ohne Personendaten; "
        "Details über die genannten Fach-Werkzeuge."
    ),
    required_roles=_LESE_ROLLEN,
    module_check=suche_aktiv,
)
async def ecp_suchen(
    context: MCPContext, q: str, kontext: str = "global", kontext_id: int | None = None,
    typen: list[str] | None = None, inkl_erledigt: bool = False, ganze_org: bool = False,
    lat: float | None = None, lng: float | None = None, radius_m: int | None = None, limit: int = 20,
) -> dict[str, object]:
    try:
        anfrage = SuchAnfrage.model_validate({
            "q": q, "kontext": kontext, "kontext_id": kontext_id, "typen": typen or [],
            "inkl_erledigt": inkl_erledigt, "ganze_org": ganze_org,
            "lat": lat, "lng": lng, "radius_m": radius_m, "limit": limit,
        })
    except ValidationError as exc:
        fehler: dict[str, str] = {}
        for eintrag in exc.errors():
            feld = ".".join(str(teil) for teil in eintrag["loc"]) or "Anfrage"
            fehler.setdefault(feld, eintrag["msg"])
        raise ValueError("; ".join(f"{feld}: {meldung}" for feld, meldung in fehler.items())) from None
    try:
        ergebnis = suche(context.db, context.user, anfrage)
    except PermissionError:
        raise ValueError("Kontext nicht gefunden.") from None
    treffer = []
    rechte: dict[str, bool] = {}
    for t in ergebnis.treffer:
        daten = t.model_dump(include=set(_FELDER))
        daten["stand"] = _utc(t.stand)
        for feld in ("titel", "untertitel", "auszug"):
            if isinstance(daten[feld], str):
                daten[feld] = entferne_telefonnummern(daten[feld])
        daten["auszug"] = daten["auszug"][:300]
        if t.url and not urlsplit(t.url).scheme and not urlsplit(t.url).netloc:
            daten["url"] = urljoin(settings.effective_public_base_url.rstrip("/") + "/", t.url.lstrip("/"))
        name = _DETAILS.get(t.quelle)
        if name:
            if name not in rechte:
                rechte[name] = _details_erlaubt(context, name)
            if rechte[name]:
                parameter = _parameter(context, t, anfrage)
                if parameter is not None:
                    daten.update(details_werkzeug=name, details_parameter=parameter)
        treffer.append(daten)
    return {"treffer": treffer, "teilweise": ergebnis.teilweise, "index_stand": _utc(ergebnis.index_stand),
            "hinweise": ergebnis.hinweise, "modi": ergebnis.modi}
