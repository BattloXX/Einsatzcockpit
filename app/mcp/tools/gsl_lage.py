"""MCP-Adapter fuer das datensparsame GSL-Lagebild (nur lesend)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.core.audit import write_audit
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.mcp.tools.gsl import READ, _author, _edit, _json
from app.models.major_incident import (
    CROSS_MARKER_STATUS_LABEL,
    CROSS_MARKER_TYPE_LABEL,
    JOURNAL_CATEGORIES,
    EinheitSiteDispatch,
    IncidentSite,
    MajorIncident,
    SitePhase,
    SitePriority,
)
from app.services import (
    einheit_service,
    gsl_broadcast,
    gsl_mcp_service,
    gsl_stelle_service,
    major_incident_service,
    resource_service,
)
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


def _site(context: MCPContext, lage: MajorIncident, site_id: int) -> IncidentSite:
    site = context.db.get(IncidentSite, site_id)
    if site is None or site.major_incident_id != lage.id or site.org_id != context.org_id:
        raise ValueError("Einsatzstelle nicht gefunden.")
    return site


def _dispatch(context: MCPContext, lage: MajorIncident, dispatch_id: int) -> EinheitSiteDispatch:
    dispatch = context.db.get(EinheitSiteDispatch, dispatch_id)
    if (
        dispatch is None
        or dispatch.site is None
        or dispatch.site.major_incident_id != lage.id
        or dispatch.site.org_id != context.org_id
    ):
        raise ValueError("Auftrag nicht gefunden.")
    return dispatch


def _koordinaten_pruefen(lat: float | None, lng: float | None) -> None:
    if lat is not None and not -90 <= lat <= 90:
        raise ValueError("lat muss zwischen -90 und 90 liegen.")
    if lng is not None and not -180 <= lng <= 180:
        raise ValueError("lng muss zwischen -180 und 180 liegen.")


def _audit(context: MCPContext, name: str, lage: MajorIncident, entity_id: int, **payload: Any) -> None:
    write_audit(
        context.db,
        f"gsl.mcp.{name}",
        org_id=context.org_id,
        user_id=context.user.id,
        entity_type="gsl_lage",
        entity_id=entity_id,
        payload={"lage_id": lage.id, "via": "mcp", **payload},
    )


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


@register_tool(
    name="gsl_einsatzstelle_anlegen",
    description="Legt eine Einsatzstelle in einer aktiven GSL-Lage an.",
    required_roles=READ,
)
async def gsl_einsatzstelle_anlegen(
    context: MCPContext, lage_id: int, bezeichnung: str, einsatzgrund: str = "", ort: str = "",
    strasse: str = "", hausnr: str = "", lat: float | None = None, lng: float | None = None,
    prio: int | None = None, abschnitt_id: int | None = None, trotz_duplikat: bool = False,
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        _koordinaten_pruefen(lat, lng)
        if prio is not None:
            try:
                priority = SitePriority(prio)
            except ValueError as exc:
                raise ValueError("prio muss zwischen 1 und 4 liegen.") from exc
        else:
            priority = None
        duplikate = gsl_stelle_service.finde_adress_duplikate(
            context.db, lage, ort=ort, strasse=strasse, hausnr=hausnr,
        )
        if duplikate and not trotz_duplikat:
            return _antwort(lage, angelegt=False, duplikate=[{
                "id": site.id, "bezeichnung": site.bezeichnung,
                "adresse": ", ".join(part for part in (site.strasse, site.hausnr, site.ort) if part),
                "phase": site.phase.value,
            } for site in duplikate], hinweis="Eine offene Einsatzstelle mit derselben Adresse existiert bereits.")
        site = await gsl_stelle_service.stelle_anlegen(
            context.db, lage, bezeichnung=bezeichnung, einsatzgrund=einsatzgrund, ort=ort, strasse=strasse,
            hausnr=hausnr, lat=lat, lng=lng, user_id=context.user.id, author_name=_author(context),
        )
        if priority is not None:
            gsl_stelle_service.stelle_prio_setzen(
                context.db, site, priority, user_id=context.user.id, author_name=_author(context),
            )
        if abschnitt_id is not None:
            gsl_stelle_service.stelle_abschnitt_setzen(context.db, site, abschnitt_id)
        _audit(context, "einsatzstelle_anlegen", lage, site.id, site_id=site.id)
        context.db.commit()
        await gsl_broadcast.stelle_angelegt(context.db, lage, site.id)
        return _antwort(lage, ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}?site={site.id}",
                         angelegt=True, id=site.id, phase=site.phase.value,
                         prio=site.priority.value if site.priority else None, abschnitt_id=site.sector_id,
                         lat=site.lat, lng=site.lng)
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_einsatzstelle_aendern",
    description="Ändert Felder einer Einsatzstelle in einer aktiven Lage.",
    required_roles=READ,
)
async def gsl_einsatzstelle_aendern(
    context: MCPContext, lage_id: int, site_id: int, felder: dict[str, Any],
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        site = _site(context, lage, site_id)
        erlaubt = {"bezeichnung", "einsatzgrund", "ort", "strasse", "hausnr", "lat", "lng", "prio", "abschnitt_id"}
        unbekannt = set(felder) - erlaubt
        if unbekannt:
            raise ValueError("Unbekannte Felder: " + ", ".join(sorted(unbekannt)))
        _koordinaten_pruefen(felder.get("lat"), felder.get("lng"))
        if "prio" in felder and felder["prio"] is not None:
            try:
                priority = SitePriority(felder["prio"])
            except (TypeError, ValueError) as exc:
                raise ValueError("prio muss zwischen 1 und 4 liegen oder null sein.") from exc
        else:
            priority = None
        if (
            "abschnitt_id" in felder
            and felder["abschnitt_id"] is not None
            and not isinstance(felder["abschnitt_id"], int)
        ):
            raise ValueError("abschnitt_id muss eine Ganzzahl oder null sein.")
        adresse_geaendert = any(key in felder for key in {"ort", "strasse", "hausnr"})
        if adresse_geaendert and "lat" not in felder and "lng" not in felder:
            site.lat = site.lng = None
        if any(key in felder for key in {"bezeichnung", "einsatzgrund", "ort", "strasse", "hausnr"}):
            await gsl_stelle_service.stelle_bearbeiten(
                context.db, site,
                bezeichnung=felder.get("bezeichnung", site.bezeichnung),
                einsatzgrund=felder.get("einsatzgrund", site.einsatzgrund),
                ort=felder.get("ort", site.ort), strasse=felder.get("strasse", site.strasse),
                hausnr=felder.get("hausnr", site.hausnr), user_id=context.user.id, author_name=_author(context),
            )
        if "lat" in felder:
            site.lat = felder["lat"]
        if "lng" in felder:
            site.lng = felder["lng"]
        if "prio" in felder:
            gsl_stelle_service.stelle_prio_setzen(
                context.db, site, priority, user_id=context.user.id, author_name=_author(context),
            )
        if "abschnitt_id" in felder:
            gsl_stelle_service.stelle_abschnitt_setzen(context.db, site, felder["abschnitt_id"])
        duplikate = gsl_stelle_service.finde_adress_duplikate(
            context.db, lage, ort=site.ort, strasse=site.strasse, hausnr=site.hausnr, ausser_site_id=site.id,
        ) if adresse_geaendert else []
        _audit(context, "einsatzstelle_aendern", lage, site.id, site_id=site.id, felder=sorted(felder))
        context.db.commit()
        if any(key in felder for key in {"bezeichnung", "einsatzgrund", "ort", "strasse", "hausnr", "lat", "lng"}):
            await gsl_broadcast.stelle_geaendert(lage.id, site.id)
        if "prio" in felder:
            await gsl_broadcast.prio_geaendert(lage.id, site.id)
        if "abschnitt_id" in felder:
            await gsl_broadcast.abschnitt_geaendert(lage.id, site.id, site.sector_id)
        result: dict[str, Any] = _antwort(
            lage, ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}?site={site.id}",
            id=site.id, phase=site.phase.value, prio=site.priority.value if site.priority else None,
            abschnitt_id=site.sector_id, lat=site.lat, lng=site.lng,
        )
        if duplikate:
            result["warnung_duplikate"] = [{"id": other.id, "bezeichnung": other.bezeichnung} for other in duplikate]
        return result
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_einsatzstelle_phase_setzen",
    description="Setzt eine manuelle Phase einer Einsatzstelle.",
    required_roles=READ,
)
async def gsl_einsatzstelle_phase_setzen(
    context: MCPContext, lage_id: int, site_id: int, phase: str,
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        site = _site(context, lage, site_id)
        if phase in {"erledigt", "abgebrochen"}:
            raise ValueError("Für erledigt oder abgebrochen bitte gsl_einsatz_abschliessen verwenden.")
        try:
            new_phase = SitePhase(phase)
        except ValueError as exc:
            erlaubt = ", ".join(
                item.value for item in SitePhase if item not in {SitePhase.erledigt, SitePhase.abgebrochen}
            )
            raise ValueError(f"Unbekannte Phase. Erlaubt sind: {erlaubt}.") from exc
        major_incident_service.setze_site_phase(
            context.db, site, new_phase, user_id=context.user.id, author_name=_author(context),
        )
        _audit(context, "einsatzstelle_phase_setzen", lage, site.id, site_id=site.id, phase=phase)
        context.db.commit()
        await gsl_broadcast.phase_geaendert(context.db, lage, site.id, new_phase)
        return _antwort(lage, site_id=site.id, phase=site.phase.value,
                         ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}?site={site.id}")
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_einsatzstelle_eintrag",
    description=(
        "Erfasst einen Chronik-Eintrag an einer Einsatzstelle; art ist note oder massnahmen "
        "(Lagemeldungen über gsl_lagemeldung_erfassen)."
    ),
    required_roles=READ,
)
async def gsl_einsatzstelle_eintrag(
    context: MCPContext, lage_id: int, site_id: int, text: str, art: str = "note",
) -> dict[str, object]:
    try:
        from app.models.major_incident import SITE_LOG_RESET_KINDS
        from app.services.site_log_service import add_site_log, normalisiere_user_kind

        lage = _lage(context, lage_id)
        _edit(context, lage)
        site = _site(context, lage, site_id)
        kind = normalisiere_user_kind(art)
        add_site_log(context.db, site, kind, text, user_id=context.user.id, author_name=_author(context))
        _audit(context, "einsatzstelle_eintrag", lage, site.id, site_id=site.id, art=kind)
        context.db.commit()
        await gsl_broadcast.site_log_geaendert(lage.id, site.id, kind in SITE_LOG_RESET_KINDS)
        return _antwort(lage, site_id=site.id, art=kind,
                         ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}?site={site.id}")
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_lagemeldung_erfassen",
    description="Erfasst eine strukturierte Lagemeldung und setzt deren Timer zurück.",
    required_roles=READ,
)
async def gsl_lagemeldung_erfassen(
    context: MCPContext, lage_id: int, site_id: int, lage: str = "", gefahren: str = "", massnahmen: str = "",
    fortschritt: str = "", text: str = "",
) -> dict[str, object]:
    try:
        from app.services.site_log_service import add_site_log, format_lagemeldung

        incident = _lage(context, lage_id)
        _edit(context, incident)
        site = _site(context, incident, site_id)
        inhalt = format_lagemeldung({
            "lage": lage, "gefahren": gefahren, "massnahmen": massnahmen,
            "fortschritt": fortschritt, "freitext": text,
        })
        if not inhalt:
            raise ValueError("Mindestens ein Feld der Lagemeldung muss gefüllt sein.")
        add_site_log(context.db, site, "lagemeldung", inhalt, user_id=context.user.id, author_name=_author(context))
        _audit(context, "lagemeldung_erfassen", incident, site.id, site_id=site.id)
        context.db.commit()
        await gsl_broadcast.site_log_geaendert(incident.id, site.id, True)
        return _antwort(incident, site_id=site.id, text=inhalt,
                         ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{incident.id}?site={site.id}")
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_einheit_status_melden",
    description="Erfasst eine per Funk gemeldete Statusänderung einer Einheit.",
    required_roles=READ,
)
async def gsl_einheit_status_melden(
    context: MCPContext, lage_id: int, dispatch_id: int, status: str, kommentar: str | None = None,
    grund: str | None = None,
) -> dict[str, object]:
    try:
        from app.services.site_log_service import add_site_log

        lage = _lage(context, lage_id)
        _edit(context, lage)
        dispatch = _dispatch(context, lage, dispatch_id)
        try:
            result = einheit_service.melde_status_per_funk(
                context.db, context.user, dispatch, status, author_name=_author(context), grund=grund,
                anzeige_quelle="Funk (MCP)",
            )
        except einheit_service.EinheitKonflikt as exc:
            raise ValueError(
                einheit_service.KONFLIKT_MELDUNGEN.get(exc.code, "Status kann nicht gesetzt werden.")
            ) from exc
        if kommentar and kommentar.strip():
            add_site_log(
                context.db, dispatch.site, "note", f"Funk (MCP) {dispatch.einheit.label}: {kommentar.strip()}",
                user_id=context.user.id, author_name=_author(context), einheit_id=dispatch.einheit_id,
            )
        _audit(context, "einheit_status_melden", lage, dispatch.id, dispatch_id=dispatch.id, status=status)
        context.db.commit()
        await gsl_broadcast.einheit_status_geaendert(
            context.db, lage, dispatch.site_id, dispatch.id, dispatch.einheit_id, result,
        )
        return _antwort(
            lage, dispatch_id=dispatch.id, status=result["einheit_status"], site_phase=dispatch.site.phase.value,
            abgezogen=dispatch.abgezogen_at is not None, phase_geaendert=result["phase_geaendert"],
            ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}?site={dispatch.site_id}",
        )
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_auftrag_wiedereroeffnen", description="Öffnet einen beendeten Auftrag wieder.", required_roles=READ
)
async def gsl_auftrag_wiedereroeffnen(context: MCPContext, lage_id: int, dispatch_id: int) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        dispatch = _dispatch(context, lage, dispatch_id)
        resource_service.oeffne_auftrag_wieder(
            context.db, dispatch, author_name=_author(context), user_id=context.user.id,
        )
        _audit(context, "auftrag_wiedereroeffnen", lage, dispatch.id, dispatch_id=dispatch.id, site_id=dispatch.site_id)
        context.db.commit()
        result = {
            "phase_geaendert": getattr(dispatch, "phase_geaendert", False),
            "unterbrochen_dispatch_id": None,
        }
        await gsl_broadcast.einheit_status_geaendert(
            context.db, lage, dispatch.site_id, dispatch.id, dispatch.einheit_id, result,
        )
        return _antwort(lage, dispatch_id=dispatch.id, status=dispatch.einheit_status,
                         site_phase=dispatch.site.phase.value, phase_geaendert=result["phase_geaendert"],
                         ui_link=f"{settings.effective_public_base_url.rstrip('/')}/lage/{lage.id}?site={dispatch.site_id}")
    except Exception:
        context.db.rollback()
        raise


def _plain_html(text: str) -> str:
    from html import escape
    parts = [part.replace("\n", "<br>") for part in escape(text).split("\n\n")]
    return "".join(f"<p>{part}</p>" for part in parts if part) or "<p></p>"


@register_tool(
    name="gsl_journal_eintrag", description="Erfasst einen Klartext-Eintrag im Lagejournal.", required_roles=READ
)
async def gsl_journal_eintrag(
    context: MCPContext, lage_id: int, betreff: str, kategorie: str = "sonstiges", text: str = "",
    von: str = "", an: str = "", veranlassung: str = "", site_id: int | None = None,
    einheit_id: int | None = None,
) -> dict[str, object]:
    try:
        from app.services import lage_journal_service
        lage = _lage(context, lage_id)
        _edit(context, lage)
        if kategorie not in JOURNAL_CATEGORIES:
            raise ValueError("Unbekannte Kategorie. Erlaubt sind: " + ", ".join(sorted(JOURNAL_CATEGORIES)))
        entry = lage_journal_service.journal_eintrag_anlegen(
            context.db, lage, text=betreff, category=kategorie, body_html=_plain_html(text), partner_from=von,
            partner_to=an, measure=veranlassung, user_id=context.user.id, author_name=_author(context),
            site_id=site_id, einheit_id=einheit_id,
        )
        entry.quelle = "mcp"
        context.db.flush()
        _audit(context, "journal_eintrag", lage, entry.id, entry_id=entry.id)
        context.db.commit()
        await gsl_broadcast.journal_geaendert(lage.id)
        return _antwort(lage, id=entry.id, kategorie=entry.category)
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_funkjournal_eintrag", description="Erfasst einen Funkjournaleintrag.", required_roles=READ,
               module_check=_feature("funkjournal"))
async def gsl_funkjournal_eintrag(
    context: MCPContext, lage_id: int, text: str, richtung: str = "in", partner: str = "", kanal: str = "",
    anfrage: bool = False, site_id: int | None = None,
) -> dict[str, object]:
    try:
        from app.services import funkjournal_service
        lage = _lage(context, lage_id)
        _edit(context, lage)
        entry = funkjournal_service.add_comm_entry(context.db, lage, direction=richtung, message=text,
            channel=kanal, partner=partner, is_request=anfrage, related_site_id=site_id,
            user_id=context.user.id, author_name=_author(context))
        _audit(context, "funkjournal_eintrag", lage, entry.id or 0, entry_id=entry.id)
        context.db.commit()
        await gsl_broadcast.funkjournal_geaendert(lage.id, site_id)
        return _antwort(lage, id=entry.id, erledigt=entry.handled)
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_funkjournal_erledigt", description="Setzt den Erledigt-Status eines Funkjournaleintrags.",
               required_roles=READ, module_check=_feature("funkjournal"))
async def gsl_funkjournal_erledigt(
    context: MCPContext, lage_id: int, entry_id: int, erledigt: bool = True,
) -> dict[str, object]:
    try:
        from app.services import funkjournal_service
        lage = _lage(context, lage_id)
        _edit(context, lage)
        try:
            entry = funkjournal_service.setze_erledigt(context.db, lage, entry_id, erledigt)
        except LookupError as exc:
            raise ValueError("Funkjournaleintrag nicht gefunden.") from exc
        _audit(context, "funkjournal_erledigt", lage, entry.id, entry_id=entry.id, erledigt=erledigt)
        context.db.commit()
        await gsl_broadcast.funkjournal_geaendert(lage.id, entry.related_site_id)
        return _antwort(lage, id=entry.id, erledigt=entry.handled)
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_meldung_bearbeiten", description="Bearbeitet eine Bürgermeldung datensparsam.",
    required_roles=READ, module_check=_feature("meldungen"),
)
async def gsl_meldung_bearbeiten(
    context: MCPContext, lage_id: int, report_id: int, aktion: str, site_id: int | None = None,
    bezeichnung: str | None = None, prio: int | None = None, trotz_duplikat: bool = False,
) -> dict[str, object]:
    try:
        from app.services import buergermeldung_service
        lage = _lage(context, lage_id)
        _edit(context, lage)
        if aktion not in {"annehmen", "ablehnen", "als_lageinfo"}:
            raise ValueError("aktion muss annehmen, ablehnen oder als_lageinfo sein.")
        try:
            report = buergermeldung_service.report(context.db, lage, report_id)
        except LookupError as exc:
            raise ValueError("Bürgermeldung nicht gefunden.") from exc
        if report.status != "new":
            raise ValueError("Bürgermeldung wurde bereits bearbeitet.")
        if aktion == "annehmen":
            duplicates = [] if site_id else gsl_stelle_service.finde_adress_duplikate(
                context.db, lage, ort=report.ort, strasse=report.strasse, hausnr=None)
            if duplicates and not trotz_duplikat:
                return _antwort(lage, bearbeitet=False,
                                duplikate=[{"id": s.id, "bezeichnung": s.bezeichnung} for s in duplicates])
            report, site = await buergermeldung_service.annehmen(context.db, lage, report_id,
                user_id=context.user.id, author_name=_author(context), bezeichnung=bezeichnung, site_id=site_id)
            if prio is not None:
                try:
                    priority = SitePriority(prio)
                except ValueError as exc:
                    raise ValueError("prio muss zwischen 1 und 4 liegen.") from exc
                gsl_stelle_service.stelle_prio_setzen(context.db, site, priority, user_id=context.user.id,
                                                      author_name=_author(context))
            _audit(context, "meldung_bearbeiten", lage, report.id, aktion=aktion, site_id=site.id)
            context.db.commit()
            if site_id is None:
                await gsl_broadcast.stelle_angelegt(context.db, lage, site.id)
            else:
                await gsl_broadcast.site_log_geaendert(lage.id, site.id)
            return _antwort(lage, bearbeitet=True, aktion=aktion, report_id=report.id, site_id=site.id)
        if aktion == "ablehnen":
            buergermeldung_service.ablehnen(context.db, lage, report_id, nur_neu=True)
            marker_id = None
        else:
            report, marker = buergermeldung_service.als_lageinfo(context.db, lage, report_id,
                user_id=context.user.id, author_name=_author(context), nur_neu=True)
            marker_id = marker.id
        _audit(context, "meldung_bearbeiten", lage, report.id, aktion=aktion)
        context.db.commit()
        if marker_id:
            await gsl_broadcast.cross_marker_geaendert(lage.id, marker_id)
        return _antwort(lage, bearbeitet=True, aktion=aktion, report_id=report.id)
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_uebergreifend_anlegen", description="Erstellt eine übergreifende Lageinfo.",
    required_roles=READ, module_check=_feature("uebergreifend"),
)
async def gsl_uebergreifend_anlegen(
    context: MCPContext, lage_id: int, titel: str, typ: str = "sonstiges", beschreibung: str = "", ort: str = "",
    strasse: str = "", hausnr: str = "", lat: float | None = None, lng: float | None = None,
) -> dict[str, object]:
    try:
        from app.services import cross_marker_service
        lage = _lage(context, lage_id)
        _edit(context, lage)
        _koordinaten_pruefen(lat, lng)
        if typ not in CROSS_MARKER_TYPE_LABEL:
            raise ValueError("Unbekannter Typ. Erlaubt sind: " + ", ".join(sorted(CROSS_MARKER_TYPE_LABEL)))
        marker = cross_marker_service.anlegen(
            context.db, lage, title=titel, marker_type=typ, description=beschreibung, ort=ort, strasse=strasse,
            hausnr=hausnr, lat=lat, lng=lng, user_id=context.user.id, author_name=_author(context),
        )
        context.db.flush()
        _audit(context, "uebergreifend_anlegen", lage, marker.id, marker_id=marker.id)
        context.db.commit()
        await gsl_broadcast.cross_marker_geaendert(lage.id, marker.id)
        return _antwort(lage, id=marker.id, status=marker.status)
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_uebergreifend_aendern", description="Ändert eine übergreifende Lageinfo.", required_roles=READ,
               module_check=_feature("uebergreifend"))
async def gsl_uebergreifend_aendern(
    context: MCPContext, lage_id: int, marker_id: int, felder: dict[str, Any] | None = None,
    status: str | None = None, eintrag: str | None = None,
) -> dict[str, object]:
    try:
        from app.services import cross_marker_service
        lage = _lage(context, lage_id)
        _edit(context, lage)
        fields = felder or {}
        allowed = {"title", "marker_type", "status", "description", "strasse", "hausnr", "ort", "lat", "lng"}
        if set(fields) - allowed:
            raise ValueError("Unbekannte Felder: " + ", ".join(sorted(set(fields) - allowed)))
        if status is not None:
            fields["status"] = status
        if fields.get("status") not in (None, *CROSS_MARKER_STATUS_LABEL):
            raise ValueError("Unbekannter Status.")
        if fields.get("marker_type") not in (None, *CROSS_MARKER_TYPE_LABEL):
            raise ValueError("Unbekannter Typ.")
        _koordinaten_pruefen(fields.get("lat"), fields.get("lng"))
        try:
            marker = cross_marker_service.bearbeiten(context.db, lage, marker_id, **fields)
            if eintrag:
                cross_marker_service.log_eintrag(
                    context.db, lage, marker_id, eintrag, context.user.id, _author(context)
                )
        except LookupError as exc:
            raise ValueError("Übergreifende Lageinfo nicht gefunden.") from exc
        _audit(context, "uebergreifend_aendern", lage, marker.id, marker_id=marker.id, felder=sorted(fields))
        context.db.commit()
        await gsl_broadcast.cross_marker_geaendert(lage.id, marker.id)
        return _antwort(lage, id=marker.id, status=marker.status)
    except Exception:
        context.db.rollback()
        raise
