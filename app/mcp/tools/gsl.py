"""MCP-Adapter fuer die GSL-Ressourcenkarte.

Die Datei enthaelt bewusst keine Fachlogik: alle Mutationen laufen durch die
gleichen Services wie die Weboberflaeche.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.permissions import has_role
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.major_incident import LageEinheit, MajorIncident
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
