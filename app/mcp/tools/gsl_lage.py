"""MCP-Adapter fuer das datensparsame GSL-Lagebild (nur lesend)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.mcp.tools.gsl import READ, _json
from app.models.major_incident import IncidentSite, MajorIncident
from app.services import gsl_mcp_service, major_incident_service
from app.services.mi_feature_service import get_mi_features

_BEREICHE = {"allgemein", "auftraege", "log", "medien", "material", "meldungen"}


def _feature(name: str):
    return lambda org_id, db: get_mi_features(db, org_id)[name]


def _lage(context: MCPContext, lage_id: int | None) -> MajorIncident:
    lage = (
        major_incident_service.get_active_lage(context.db, context.org_id)
        if lage_id is None
        else context.db.get(MajorIncident, lage_id)
    )
    if lage is None and lage_id is None:
        raise ValueError("Keine aktive Lage.")
    if lage is None or lage.org_id != context.org_id:
        raise ValueError("Lage nicht gefunden.")
    return lage


def _antwort(lage: MajorIncident, **inhalt: Any) -> dict[str, Any]:
    return _json({"ui_link": f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}", **inhalt})


def _seit(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else ""))
    except ValueError as exc:
        raise ValueError("Ungültiger Zeitpunkt für seit.") from exc
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


@register_tool(name="gsl_lagen_liste", description="Listet Lagen der eigenen Organisation.", required_roles=READ)
async def gsl_lagen_liste(context: MCPContext, status: str = "aktiv", limit: int = 25) -> dict[str, object]:
    try:
        if status not in {"aktiv", "beendet", "alle"}:
            raise ValueError("Ungültiger Status.")
        if not 1 <= limit <= 100:
            raise ValueError("limit muss zwischen 1 und 100 liegen.")
        lagen = gsl_mcp_service.lagen_liste(context.db, context.org_id, status, limit)
        return _json({"lagen": lagen, "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/lage"})
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_lagebild", description="Liest das aktuelle GSL-Lagebild.", required_roles=READ)
async def gsl_lagebild(context: MCPContext, lage_id: int | None = None) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        features = get_mi_features(context.db, context.org_id)
        return _antwort(
            lage,
            lagebild=gsl_mcp_service.lagebild(
                context.db, lage, funkjournal_aktiv=features["funkjournal"], meldungen_aktiv=features["meldungen"]
            ),
        )
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_einsatzstellen_liste", description="Listet Einsatzstellen einer GSL-Lage.", required_roles=READ
)
async def gsl_einsatzstellen_liste(
    context: MCPContext,
    lage_id: int | None = None,
    phase: str | None = None,
    prio: int | None = None,
    abschnitt_id: int | None = None,
    suchtext: str = "",
    inklusive_erledigte: bool = False,
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        return _antwort(
            lage,
            einsatzstellen=gsl_mcp_service.einsatzstellen_liste(
                context.db,
                lage,
                phase=phase,
                prio=prio,
                abschnitt_id=abschnitt_id,
                suchtext=suchtext,
                inklusive_erledigte=inklusive_erledigte,
            ),
        )
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_einsatzstelle_lesen", description="Liest datensparsame Details einer Einsatzstelle.", required_roles=READ
)
async def gsl_einsatzstelle_lesen(
    context: MCPContext, lage_id: int, site_id: int, bereiche: list[str] | None = None
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        site = context.db.get(IncidentSite, site_id)
        if site is None or site.major_incident_id != lage.id or site.org_id != context.org_id:
            raise ValueError("Einsatzstelle nicht gefunden.")
        selected = set(bereiche or _BEREICHE)
        unknown = selected - _BEREICHE
        if unknown:
            raise ValueError("Unbekannte Bereiche: " + ", ".join(sorted(unknown)))
        features = get_mi_features(context.db, context.org_id)
        selected.discard("material") if not features["geraeteverleih"] else None
        selected.discard("meldungen") if not features["meldungen"] else None
        return _antwort(
            lage,
            ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}?site={site.id}",
            einsatzstelle=gsl_mcp_service.einsatzstelle_lesen(
                context.db, lage, site, selected, material_aktiv=features["geraeteverleih"]
            ),
        )
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_kraefteuebersicht",
    description="Liest die GSL-Kräfteübersicht.",
    required_roles=READ,
    module_check=_feature("ressourcen"),
)
async def gsl_kraefteuebersicht(context: MCPContext, lage_id: int | None = None) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        return _antwort(lage, kraefteuebersicht=gsl_mcp_service.kraefteuebersicht(context.db, lage))
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_abschnitte",
    description="Listet GSL-Abschnitte und Sektoren.",
    required_roles=READ,
    module_check=_feature("sektoren"),
)
async def gsl_abschnitte(
    context: MCPContext, lage_id: int | None = None, mit_geometrie: bool = False
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        return _antwort(lage, abschnitte=gsl_mcp_service.abschnitte(lage, mit_geometrie=mit_geometrie))
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_journal", description="Liest Lage- und Funkjournal ohne Autorendaten.", required_roles=READ)
async def gsl_journal(
    context: MCPContext,
    lage_id: int | None = None,
    quellen: list[str] | None = None,
    seit: str = "",
    kategorie: str | None = None,
    nur_offen: bool = False,
    limit: int = 100,
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        selected = set(quellen if quellen is not None else ["lagejournal", "funkjournal"])
        unknown = selected - {"lagejournal", "funkjournal"}
        if unknown:
            raise ValueError("Unbekannte Quellen: " + ", ".join(sorted(unknown)))
        if not get_mi_features(context.db, context.org_id)["funkjournal"]:
            selected.discard("funkjournal")
        if not 1 <= limit <= 200:
            raise ValueError("limit muss zwischen 1 und 200 liegen.")
        return _antwort(
            lage,
            eintraege=gsl_mcp_service.journal(
                context.db,
                lage,
                quellen=selected,
                seit=_seit(seit),
                kategorie=kategorie,
                nur_offen=nur_offen,
                limit=limit,
            ),
        )
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_meldungen",
    description="Listet datensparsame Bürgermeldungen.",
    required_roles=READ,
    module_check=_feature("meldungen"),
)
async def gsl_meldungen(context: MCPContext, lage_id: int | None = None, status: str = "new") -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        if status not in {"new", "accepted", "rejected", "alle"}:
            raise ValueError("Ungültiger Status.")
        return _antwort(lage, meldungen=gsl_mcp_service.meldungen(context.db, lage, status))
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_uebergreifend_liste",
    description="Listet einsatzstellenübergreifende Lageinfos.",
    required_roles=READ,
    module_check=_feature("uebergreifend"),
)
async def gsl_uebergreifend_liste(
    context: MCPContext, lage_id: int | None = None, status: str = "aktiv"
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        if status not in {"aktiv", "unbestaetigt", "in_bearbeitung", "beobachtung", "behoben", "alle"}:
            raise ValueError("Ungültiger Status.")
        return _antwort(lage, marker=gsl_mcp_service.uebergreifend_liste(context.db, lage, status))
    except Exception:
        context.db.rollback()
        raise
