"""MCP-Adapter fuer die GSL-Ressourcenkarte.

Die Datei enthaelt bewusst keine Fachlogik: alle Mutationen laufen durch die
gleichen Services wie die Weboberflaeche.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.audit import write_audit
from app.core.permissions import has_role
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.major_incident import EinheitSiteDispatch, IncidentSite, LageEinheit, MajorIncident, SiteLogEntry
from app.services import gk_zugang_service, resource_service, ressource_karte_service, ressource_pflege_service
from app.services.mi_feature_service import get_mi_features

READ = ("incident_leader", "admin", "recorder", "readonly")
EDIT = ("incident_leader", "admin", "recorder")
_BEREICHE = {"allgemein", "fuehrer", "einsaetze", "journal", "personal", "ausstattung", "kommunikation", "zugang"}
_STAMM_FELDER = {
    "status",
    "abschnitt_id",
    "funkrufname",
    "bereitstellungsraum",
    "org_name",
    "bos",
    "menge",
    "einheit",
    "bemerkung",
}


def _module_check(org_id: int, db: Any) -> bool:
    return get_mi_features(db, org_id)["ressourcen"]


def _lage(context: MCPContext, lage_id: int) -> MajorIncident:
    lage = context.db.get(MajorIncident, lage_id)
    if lage is None or lage.org_id != context.org_id:
        raise ValueError("Lage nicht gefunden.")
    if not _module_check(context.org_id, context.db):
        raise ValueError("Das GSL-Ressourcenmodul ist für diese Organisation nicht aktiviert.")
    return lage


def _einheit(context: MCPContext, lage: MajorIncident, einheit_id: int) -> LageEinheit:
    einheit = context.db.get(LageEinheit, einheit_id)
    if einheit is None or einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden.")
    return einheit


def _edit(context: MCPContext, lage: MajorIncident) -> None:
    if not has_role(context.user, *EDIT):
        raise ValueError("Für diese Aktion fehlen die erforderlichen Berechtigungen.")
    if str(lage.status) != "active":
        raise ValueError("Die Lage ist nicht aktiv.")


def _author(context: MCPContext) -> str:
    return f"{context.user.display_name or context.user.username} (MCP)"


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.isoformat() + "Z"
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _zugang(db: Any, einheit: LageEinheit, can_edit: bool) -> dict[str, Any]:
    status = gk_zugang_service.zugang_status(db, einheit)
    # Whitelist: weder Klartext-Token, Link noch sonstige interne Zugangswerte.
    result = {
        key: status.get(key)
        for key in (
            "zustand",
            "laeuft_ab_at",
            "generation",
            "widerruf_grund",
            "widerrufen_at",
            "aktive_sitzungen",
            "letzte_aktivitaet_at",
            "letzter_versand",
            "versandprotokoll",
            "sitzung_aktiv",
        )
    }
    result["telefon"] = status.get("nummer_anzeige") if can_edit else status.get("nummer_maske")
    return _json(result)


def _audit(context: MCPContext, action: str, lage: MajorIncident, entity_id: int, **payload: Any) -> None:
    write_audit(context.db, action, org_id=context.org_id, user_id=context.user.id,
                entity_type="gsl_auftrag", entity_id=entity_id,
                payload={"lage_id": lage.id, "via": "mcp", **payload})


async def _broadcast(lage_id: int, site_id: int, einheit_id: int, dispatch_id: int | None = None) -> None:
    from app.services.broadcast import broadcast_lage
    await broadcast_lage(lage_id, {"type": "site:card_changed", "site_id": site_id})
    event: dict[str, Any] = {"type": "einheit:changed", "einheit_id": einheit_id, "site_id": site_id}
    if dispatch_id is not None:
        event["dispatch_id"] = dispatch_id
    await broadcast_lage(lage_id, event)


def _push_geplant(db: Any, einheit: LageEinheit) -> str:
    from app.services.einheit_service import hat_tablet
    return "geplant" if hat_tablet(db, einheit) else "übersprungen"


def _personal(db: Any, einheit: LageEinheit) -> dict[str, Any]:
    from app.models.major_incident import LageEinheitPerson

    personen = (
        db.query(LageEinheitPerson)
        .filter(LageEinheitPerson.einheit_id == einheit.id, LageEinheitPerson.bis_at.is_(None))
        .order_by(LageEinheitPerson.id)
        .all()
    )
    return _json(
        {
            "modus": einheit.personal_modus,
            "staerke": {
                "gesamt": einheit.staerke_gesamt or 0,
                "fuehrung": einheit.staerke_fuehrung or 0,
                "agt": einheit.staerke_agt or 0,
                "sanitaeter": einheit.staerke_sanitaeter or 0,
            },
            "bemerkung": einheit.personal_bemerkung,
            "personen": [
                {
                    "id": p.id,
                    "member_id": p.member_id,
                    "name": p.name,
                    "funktion": p.funktion,
                    "qualifikationen": p.qualifikationen,
                    "herkunft": p.herkunft,
                    "bemerkung": p.bemerkung,
                }
                for p in personen
            ],
        }
    )


@register_tool(
    name="gsl_ressourcen_liste",
    description="Listet Ressourcen einer aktiven GSL-Lage.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressourcen_liste(
    context: MCPContext, lage_id: int, status: str = "", abschnitt_id: int | None = None, typ: str = ""
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        can_edit = has_role(context.user, *EDIT)
        rows = []
        for einheit in lage.einheiten:
            if (
                status
                and einheit.status != status
                or typ
                and einheit.resource_type != typ
                or (abschnitt_id is not None and einheit.sector_id != abschnitt_id)
            ):
                continue
            card = ressource_karte_service.karte(context.db, lage, einheit)
            leader = card["gruppenkommandant"]["current"]
            rows.append(
                {
                    "id": einheit.id,
                    "bezeichnung": einheit.label,
                    "typ": einheit.resource_type,
                    "status": einheit.status,
                    "abschnitt_id": einheit.sector_id,
                    "gruppenkommandant": None
                    if not leader
                    else {"name": leader["name"], "telefon": leader["phone"] if can_edit else None},
                    "zugang": _zugang(context.db, einheit, can_edit)["zustand"],
                    "aktueller_einsatz": card["einsaetze"]["aktuell"],
                }
            )
        return _json({"ressourcen": rows})
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_details",
    description="Liest Bereiche einer GSL-Ressource; Zugänge enthalten nie Link oder Token.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_details(
    context: MCPContext, lage_id: int, einheit_id: int, bereiche: list[str] | None = None
) -> dict[str, object]:
    try:
        lage, einheit = _lage(context, lage_id), None
        einheit = _einheit(context, lage, einheit_id)
        selected = set(bereiche or _BEREICHE)
        unknown = selected - _BEREICHE
        if unknown:
            raise ValueError("Unbekannte Bereiche: " + ", ".join(sorted(unknown)))
        card = ressource_karte_service.karte(context.db, lage, einheit)
        result: dict[str, Any] = {}
        mapping = {
            "allgemein": card["allgemein"],
            "fuehrer": card["gruppenkommandant"],
            "einsaetze": card["einsaetze"],
            "kommunikation": card["kommunikation"],
        }
        for key, value in mapping.items():
            if key in selected:
                result[key] = value
        if "journal" in selected:
            result["journal"] = [row.__dict__ for row in ressource_karte_service.journal(context.db, lage, einheit)]
        if "personal" in selected:
            result["personal"] = _personal(context.db, einheit)
        if "ausstattung" in selected:
            result["ausstattung"] = ressource_pflege_service.ausstattung_liste(context.db, einheit)
        if "zugang" in selected:
            result["zugang"] = _zugang(context.db, einheit, has_role(context.user, *EDIT))
        return _json(result)
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_aktualisieren",
    description="Aktualisiert Status, Abschnitt oder Stammdaten einer Ressource.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_aktualisieren(
    context: MCPContext, lage_id: int, einheit_id: int, felder: dict[str, Any]
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        einheit = _einheit(context, lage, einheit_id)
        unknown = set(felder) - _STAMM_FELDER
        if unknown:
            raise ValueError("Unbekannte Felder: " + ", ".join(sorted(unknown)))
        author = _author(context)
        if "status" in felder:
            resource_service.set_status(
                context.db, einheit.id, lage.id, felder["status"], user_id=context.user.id, author_name=author
            )
        if "abschnitt_id" in felder:
            resource_service.assign_to_sector(
                context.db, einheit.id, lage.id, felder["abschnitt_id"], user_id=context.user.id, author_name=author
            )
        values = {
            "funkrufname": None,
            "org_name": None,
            "bos": None,
            "bereitstellungsraum": None,
            "qty": None,
            "unit": None,
        }
        source = {"menge": "qty", "einheit": "unit"}
        if any(key in felder for key in {"funkrufname", "bereitstellungsraum", "org_name", "bos", "menge", "einheit"}):
            for key in values:
                values[key] = felder.get(
                    next((old for old, new in source.items() if new == key), key), getattr(einheit, key)
                )
            resource_service.aktualisiere_einheit_stamm(
                context.db, lage, einheit, user_id=context.user.id, author_name=author, **values
            )
        # bemerkung hat keine Stammdaten-Servicefunktion und wird daher bewusst nicht behauptet.
        if "bemerkung" in felder:
            raise ValueError("bemerkung wird vom vorhandenen Service nicht unterstützt.")
        context.db.commit()
        return {"ressource": _json(ressource_karte_service.karte(context.db, lage, einheit)["allgemein"])}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_fuehrer_setzen",
    description="Setzt Gruppenkommandant oder Stellvertretung einer Ressource.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_fuehrer_setzen(
    context: MCPContext,
    lage_id: int,
    einheit_id: int,
    mitglied_id: int | None = None,
    name: str | None = None,
    telefon: str | None = None,
    modus: str = "auto",
    stellvertreter: bool = False,
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        einheit = _einheit(context, lage, einheit_id)
        service = resource_service.setze_stellvertreter if stellvertreter else resource_service.setze_gruppenkommandant
        result = service(
            context.db,
            lage,
            einheit,
            member_id=mitglied_id,
            person_name=name,
            telefon=telefon,
            modus=modus,
            user_id=context.user.id,
            author_name=_author(context),
            quelle="mcp",
        )
        context.db.commit()
        return {"aenderung": result.aenderung, "sms": "geplant" if result.auto_sms else "übersprungen"}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_zugang_senden",
    description="Rotiert und versendet einen Zugang ausschließlich per SMS; nie als Link in der Antwort.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_zugang_senden(
    context: MCPContext, lage_id: int, einheit_id: int, bestaetigt: bool = False
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        einheit = _einheit(context, lage, einheit_id)
        if not bestaetigt:
            raise ValueError("bestaetigt=true ist für den SMS-Versand erforderlich.")
        result = await gk_zugang_service.sende_zugangs_sms(
            context.db, lage, einheit, user_id=context.user.id, ausloeser="mcp", bestaetigt=True
        )
        return _json({"status": result.status, "fehler": result.fehler, "generation": result.generation})
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_zugang_widerrufen",
    description="Widerruft den Gruppenkommandanten-Zugang einer Ressource.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_zugang_widerrufen(context: MCPContext, lage_id: int, einheit_id: int) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        einheit = _einheit(context, lage, einheit_id)
        gk_zugang_service.widerrufe(context.db, einheit.id, grund="manuell", user_id=context.user.id)
        context.db.commit()
        return {"status": "widerrufen"}
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_journal",
    description="Liest das Ressourcenjournal oder fügt einen manuellen MCP-Eintrag hinzu.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_journal(
    context: MCPContext,
    lage_id: int,
    einheit_id: int,
    typen: list[str] | None = None,
    seit: str | None = None,
    limit: int = 50,
    neuer_eintrag: str | None = None,
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        einheit = _einheit(context, lage, einheit_id)
        if not 1 <= limit <= 200:
            raise ValueError("limit muss zwischen 1 und 200 liegen.")
        if neuer_eintrag is not None:
            _edit(context, lage)
            entry = resource_service.journal_eintrag_manuell(
                context.db, lage, einheit, text=neuer_eintrag, user_id=context.user.id, author_name=_author(context)
            )
            entry.quelle = "mcp"
            context.db.commit()
        vor_ts = datetime.fromisoformat(seit.replace("Z", "+00:00")) if seit else None
        rows = ressource_karte_service.journal(
            context.db, lage, einheit, typen=set(typen) if typen else None, vor_ts=vor_ts, limit=limit
        )
        return _json({"journal": [row.__dict__ for row in rows]})
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_personal",
    description="Liest oder pflegt Personal; unterstützte Aktionen folgen dem Ressourcenpflege-Service.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_personal(
    context: MCPContext, lage_id: int, einheit_id: int, aktion: str = "lesen", daten: dict[str, Any] | None = None
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        einheit = _einheit(context, lage, einheit_id)
        daten = daten or {}
        if aktion == "lesen":
            return _personal(context.db, einheit)
        _edit(context, lage)
        author = _author(context)
        result: Any = None
        if aktion == "setzen":
            result = ressource_pflege_service.personal_setzen(
                context.db, lage, einheit, user_id=context.user.id, author_name=author, quelle="mcp", **daten
            )
        elif aktion == "hinzufuegen":
            result = ressource_pflege_service.person_hinzufuegen(
                context.db, lage, einheit, user_id=context.user.id, author_name=author, **daten
            )
        elif aktion == "entfernen":
            ressource_pflege_service.person_entfernen(
                context.db, lage, einheit, daten.pop("person_id"), user_id=context.user.id, author_name=author, **daten
            )
        elif aktion == "verstaerken":
            ressource_pflege_service.verstaerken(
                context.db, lage, einheit, user_id=context.user.id, author_name=author, **daten
            )
        elif aktion == "abloesen":
            result = ressource_pflege_service.abloesen(
                context.db,
                lage,
                einheit,
                daten.pop("person_alt_id"),
                user_id=context.user.id,
                author_name=author,
                **daten,
            )
        else:
            raise ValueError("Aktion wird vom vorhandenen Personal-Service nicht unterstützt.")
        context.db.commit()
        return _json(
            {"aktion": aktion, "ergebnis": getattr(result, "id", result), "personal": _personal(context.db, einheit)}
        )
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_ausstattung",
    description="Liest oder pflegt Ausstattung. Status ändern erfolgt über aktion='menge' mit status im daten-Objekt.",
    required_roles=READ,
    module_check=_module_check,
)
async def gsl_ressource_ausstattung(
    context: MCPContext, lage_id: int, einheit_id: int, aktion: str = "lesen", daten: dict[str, Any] | None = None
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        einheit = _einheit(context, lage, einheit_id)
        daten = daten or {}
        if aktion == "lesen":
            return _json({"ausstattung": ressource_pflege_service.ausstattung_liste(context.db, einheit)})
        _edit(context, lage)
        author = _author(context)
        result: Any = None
        if aktion == "hinzufuegen":
            result = ressource_pflege_service.ausstattung_hinzufuegen(
                context.db, lage, einheit, user_id=context.user.id, author_name=author, **daten
            )
        elif aktion in {"menge", "status"}:
            result = ressource_pflege_service.ausstattung_aendern(
                context.db, lage, einheit, daten.pop("zeile_id"), user_id=context.user.id, author_name=author, **daten
            )
        elif aktion == "entfernen":
            ressource_pflege_service.ausstattung_entfernen(
                context.db, lage, einheit, daten.pop("zeile_id"), user_id=context.user.id, author_name=author
            )
        else:
            raise ValueError("Aktion wird vom vorhandenen Ausstattungs-Service nicht unterstützt.")
        context.db.commit()
        return _json(
            {
                "aktion": aktion,
                "ergebnis": getattr(result, "id", result),
                "ausstattung": ressource_pflege_service.ausstattung_liste(context.db, einheit),
            }
        )
    except Exception:
        context.db.rollback()
        raise


@register_tool(
    name="gsl_ressource_anlegen",
    description="Legt eine GSL-Ressource über denselben Service wie die Weboberfläche an.",
    required_roles=READ, module_check=_module_check,
)
async def gsl_ressource_anlegen(
    context: MCPContext, lage_id: int, resource_type: str, label: str, vehicle_id: int | None = None,
    org_name: str | None = None, bos: str | None = None, qty: int | None = None, unit: str | None = None,
    funkrufname: str | None = None, status: str | None = None, sektor_id: int | None = None,
    bereitstellungsraum: str | None = None, gk_name: str | None = None, gk_member_id: int | None = None,
    gk_telefon: str | None = None, stv_name: str | None = None, personal_gesamt: int | None = None,
    bemerkung: str | None = None,
) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        gk = ({"member_id": gk_member_id, "person_name": gk_name or "", "telefon": gk_telefon, "modus": "auto"}
              if gk_member_id is not None or gk_name else None)
        stv = {"person_name": stv_name, "modus": "auto"} if stv_name else None
        personal = {"gesamt": personal_gesamt} if personal_gesamt is not None else None
        result = resource_service.lege_einheit_an(
            context.db, lage, resource_type=resource_type, label=label, vehicle_id=vehicle_id,
            org_name=org_name, bos=bos, qty=qty, unit=unit, funkrufname=funkrufname, status=status,
            sektor_id=sektor_id, bereitstellungsraum=bereitstellungsraum, gk=gk, stellvertreter=stv,
            personal=personal, bemerkung=bemerkung, user_id=context.user.id, author_name=_author(context),
        )
        context.db.commit()
        if result.auto_sms:
            await gk_zugang_service.sende_auto_sms(result.auto_sms)
        from app.services.print_dispatcher import autoprint_gsl_einheit_background
        await autoprint_gsl_einheit_background(result.einheit.id)
        qr = gk_zugang_service.zugang_status(context.db, result.einheit).get("qr", {})
        return {"einheit_id": result.einheit.id, "label": result.einheit.label,
                "gk_gesetzt": bool(result.gk_ergebnis),
                "sms": "geplant" if result.auto_sms else "übersprungen",
                "qr_druck": "angefordert" if qr.get("druck_status") != "nicht_angefordert" else "nicht_angefordert"}
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_einheit_disponieren", description="Disponiert eine Einheit zu einer Einsatzstelle.",
               required_roles=READ, module_check=_module_check)
async def gsl_einheit_disponieren(context: MCPContext, lage_id: int, einheit_id: int, site_id: int,
                                  auftrag: str | None = None, reihenfolge: int | None = None) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        einheit = _einheit(context, lage, einheit_id)
        site = context.db.get(IncidentSite, site_id)
        if site is None or site.major_incident_id != lage.id:
            raise ValueError("Einsatzstelle nicht gefunden")
        dispatch = resource_service.dispatch_to_site(context.db, einheit_id, lage.id, site_id, auftrag=auftrag,
            reihenfolge=reihenfolge, author_name=_author(context), user_id=context.user.id, quelle="mcp")
        context.db.add(SiteLogEntry(
            incident_site_id=site_id, kind="resource", text=f"Einheit disponiert: {einheit.label}",
            user_id=context.user.id, author_name=_author(context),
        ))
        _audit(context, "gsl.auftrag.disponiert", lage, dispatch.id, site_id=site_id, einheit_id=einheit_id)
        from app.services.gsl_auftrag_events import plane_benachrichtigungen, sende_nach_commit
        notification = plane_benachrichtigungen(context.db, lage, einheit, dispatch, "neu")
        context.db.commit()
        await sende_nach_commit(notification)
        await _broadcast(lage.id, site_id, einheit_id, dispatch.id)
        return {"dispatch_id": dispatch.id, "sms": "geplant" if notification.sms else "übersprungen",
                "push": _push_geplant(context.db, einheit)}
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_auftrag_aendern", description="Ändert einen GSL-Auftrag.", required_roles=READ,
               module_check=_module_check)
async def gsl_auftrag_aendern(context: MCPContext, lage_id: int, dispatch_id: int, auftrag: str | None = None,
                              reihenfolge: int | None = None) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        dispatch = context.db.get(EinheitSiteDispatch, dispatch_id)
        if dispatch is None or dispatch.einheit.lage_id != lage.id:
            raise ValueError("Disposition nicht gefunden.")
        old = dispatch.auftrag
        changed = resource_service.aendere_auftrag(context.db, dispatch, auftrag=auftrag, reihenfolge=reihenfolge,
            author_name=_author(context), user_id=context.user.id, quelle="mcp")
        _audit(context, "gsl.auftrag.geaendert", lage, dispatch.id, site_id=dispatch.site_id)
        from app.services.gsl_auftrag_events import plane_benachrichtigungen, sende_nach_commit
        notification = (plane_benachrichtigungen(context.db, lage, dispatch.einheit, dispatch, "geaendert")
                        if changed and old != dispatch.auftrag else None)
        context.db.commit()
        if notification:
            await sende_nach_commit(notification)
        await _broadcast(lage.id, dispatch.site_id, dispatch.einheit_id, dispatch.id)
        return {"dispatch_id": dispatch.id, "geaendert": changed,
                "sms": "geplant" if notification and notification.sms else "übersprungen",
                "push": _push_geplant(context.db, dispatch.einheit) if notification else "übersprungen"}
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_auftrag_zurueckziehen", description="Zieht eine Einheit von einer Einsatzstelle ab.",
               required_roles=READ, module_check=_module_check)
async def gsl_auftrag_zurueckziehen(context: MCPContext, lage_id: int, einheit_id: int, site_id: int,
                                    grund: str | None = None) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        _edit(context, lage)
        einheit = _einheit(context, lage, einheit_id)
        site = context.db.get(IncidentSite, site_id)
        if site is None or site.major_incident_id != lage.id:
            raise ValueError("Einsatzstelle nicht gefunden")
        resource_service.withdraw_from_site(context.db, einheit_id, lage.id, site_id, author_name=_author(context),
                                            user_id=context.user.id, grund=grund, quelle="mcp")
        dispatch = (
            context.db.query(EinheitSiteDispatch).filter_by(einheit_id=einheit_id, site_id=site_id)
            .order_by(EinheitSiteDispatch.id.desc()).first()
        )
        assert dispatch is not None
        context.db.add(SiteLogEntry(
            incident_site_id=site_id, kind="resource", text=f"Einheit abgezogen: {einheit.label}",
            user_id=context.user.id, author_name=_author(context),
        ))
        _audit(context, "gsl.auftrag.zurueckgezogen", lage, dispatch.id, site_id=site_id, einheit_id=einheit_id)
        from app.services.gsl_auftrag_events import plane_benachrichtigungen, sende_nach_commit
        notification = plane_benachrichtigungen(context.db, lage, einheit, dispatch, "zurueckgezogen")
        context.db.commit()
        await sende_nach_commit(notification)
        await _broadcast(lage.id, site_id, einheit_id, dispatch.id)
        return {"dispatch_id": dispatch.id, "sms": "geplant" if notification.sms else "übersprungen",
                "push": _push_geplant(context.db, einheit)}
    except Exception:
        context.db.rollback()
        raise


@register_tool(name="gsl_ressource_qr",
               description=("Liest oder widerruft QR-Zugangsdaten ohne Token, Link oder PIN; "
                            "Klartext-Zugangsdaten dürfen MCP nie erreichen."),
               required_roles=READ, module_check=_module_check)
async def gsl_ressource_qr(context: MCPContext, lage_id: int, einheit_id: int, aktion: str) -> dict[str, object]:
    try:
        lage = _lage(context, lage_id)
        einheit = _einheit(context, lage, einheit_id)
        if aktion == "status":
            qr = gk_zugang_service.zugang_status(context.db, einheit).get("qr", {})
            fields = ("status", "laeuft_ab_at", "generation", "sitzung_aktiv", "qr_druck_at", "druck_status")
            return _json({key: qr.get(key) for key in fields})
        if aktion != "widerrufen":
            raise ValueError("Aktion muss status oder widerrufen sein.")
        _edit(context, lage)
        gk_zugang_service.widerrufe(context.db, einheit.id, grund="mcp", user_id=context.user.id, typ="qr")
        _audit(context, "gsl.zugang.qr_widerrufen", lage, einheit.id, einheit_id=einheit.id)
        context.db.commit()
        return {"status": "widerrufen"}
    except Exception:
        context.db.rollback()
        raise
