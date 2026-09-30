"""MCP-Werkzeuge fuer die zentrale Kontaktverwaltung."""

from __future__ import annotations

from typing import Any

from app.core.audit import write_audit
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.kontakt import KONTAKT_TYP_PERSON, KONTAKT_TYP_STELLE, Kontakt
from app.models.master import OrgSettings, SystemSettings
from app.services import kontakt_service

MAX_LIMIT = 50
KONTAKT_ROLLEN = ("kontakt_verwalter", "objekt_verwalter")
KONTAKT_FELDER = {
    "typ",
    "anzeigename",
    "vorname",
    "nachname",
    "funktion",
    "organisation",
    "email",
    "erreichbarkeit",
    "notizen",
}


def kontakte_modul_aktiv(org_id: int, db: Any) -> bool:
    system = (
        db.query(SystemSettings)
        .filter(SystemSettings.key == "kontakte_module_enabled", SystemSettings.value == "true")
        .first()
    )
    org = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    return bool(system and org and org.kontakte_module_enabled)


def _limit(limit: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit muss zwischen 1 und 50 liegen.")


def _kandidat(kontakt: Kontakt) -> dict[str, object]:
    return {
        "id": kontakt.id,
        "anzeigename": kontakt.anzeigename,
        "organisation": kontakt.organisation,
        "funktion": kontakt.funktion,
        "typ": kontakt.typ,
    }


def _telefon(telefon: Any) -> dict[str, object]:
    return {
        "nummer": telefon.nummer,
        "label": telefon.label,
        "sort": telefon.sort,
        "bevorzugt": telefon.bevorzugt,
        "sms_eignung": telefon.sms_eignung,
    }


def _kategorien(kontakt: Kontakt) -> list[str]:
    return sorted(zuordnung.kategorie.name for zuordnung in kontakt.kategorien if zuordnung.kategorie)


def _kontakt_daten(kontakt: Kontakt) -> dict[str, object]:
    return {
        feld: getattr(kontakt, feld)
        for feld in (
            "typ", "anzeigename", "vorname", "nachname", "funktion", "organisation", "email",
            "erreichbarkeit", "notizen",
        )
    } | {"telefone": [_telefon(telefon) for telefon in kontakt.telefone], "kategorien": _kategorien(kontakt)}


def _validiere_felder(felder: dict[str, Any]) -> dict[str, Any]:
    unbekannt = set(felder) - KONTAKT_FELDER
    if unbekannt:
        raise ValueError("Unbekannte Kontaktfelder: " + ", ".join(sorted(unbekannt)))
    typ = felder.get("typ")
    if typ is not None and typ not in (KONTAKT_TYP_PERSON, KONTAKT_TYP_STELLE):
        raise ValueError("typ muss 'person' oder 'stelle' sein.")
    return dict(felder)


def _audit(context: MCPContext, action: str, kontakt_id: int, payload: dict[str, object] | None = None) -> None:
    write_audit(
        context.db,
        action,
        org_id=context.org_id,
        user_id=context.user.id,
        entity_type="kontakt",
        entity_id=kontakt_id,
        payload=payload,
    )
    context.db.commit()


@register_tool(
    name="kontakt_suchen",
    description="Sucht zentrale Kontakte der eigenen Organisation.",
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_suchen(
    context: MCPContext,
    q: str = "",
    typ: str = "all",
    kategorie: int | None = None,
    limit: int = 25,
    seite: int = 1,
) -> dict[str, object]:
    _limit(limit)
    if seite < 1:
        raise ValueError("seite muss mindestens 1 sein.")
    if typ not in ("all", KONTAKT_TYP_PERSON, KONTAKT_TYP_STELLE):
        raise ValueError("typ muss 'all', 'person' oder 'stelle' sein.")
    kontakte, gesamt = kontakt_service.list_kontakte(
        context.db, q=q, typ=typ, kategorie_id=kategorie, page=seite
    )
    # Der Service verwendet seine UI-Seitengroesse. MCP begrenzt die sichtbare Antwort zusaetzlich.
    return {"kontakte": [_kandidat(kontakt) for kontakt in kontakte[:limit]], "gesamt": gesamt, "seite": seite}


@register_tool(
    name="kontakt_duplikate_pruefen",
    description="Prüft mögliche Kontakt-Dubletten.",
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_duplikate_pruefen(
    context: MCPContext, anzeigename: str, organisation: str = "", email: str = "", telefone: list[str] | None = None
) -> dict[str, object]:
    kandidaten = kontakt_service.find_duplicate_candidates(
        context.db, anzeigename=anzeigename, organisation=organisation, email=email, telefone=telefone or []
    )
    return {"duplikate_gefunden": bool(kandidaten), "kandidaten": [_kandidat(kontakt) for kontakt in kandidaten]}


@register_tool(
    name="kontakt_lesen",
    description="Liest einen zentralen Kontakt mit Telefonen, Kategorien und Objektzuordnungen.",
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_lesen(context: MCPContext, kontakt_id: int) -> dict[str, object]:
    kontakt = kontakt_service.get_kontakt(context.db, kontakt_id)
    if kontakt is None:
        raise ValueError("Kontakt nicht gefunden.")
    zuordnungen = kontakt_service.list_objektzuordnungen(context.db, kontakt.id)
    return _kandidat(kontakt) | _kontakt_daten(kontakt) | {
        "version": kontakt.version,
        "objektzuordnungen": [
            {
                "zuordnung_id": zuordnung.id,
                "objekt_id": zuordnung.objekt_id,
                "objekt_nummer": zuordnung.objekt.nummer if zuordnung.objekt else None,
                "objekt_name": zuordnung.objekt.name if zuordnung.objekt else None,
                "art": zuordnung.art,
                "sort": zuordnung.sort,
                "erreichbarkeit": zuordnung.erreichbarkeit,
            }
            for zuordnung in zuordnungen
        ],
    }


@register_tool(
    name="kontakt_kategorien",
    description="Listet die Kontaktkategorien der eigenen Organisation.",
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_kategorien(context: MCPContext) -> dict[str, object]:
    kategorien = kontakt_service.list_kategorien(context.db)
    return {"kategorien": [{"id": kategorie.id, "name": kategorie.name} for kategorie in kategorien]}


@register_tool(
    name="kontakt_anlegen",
    description=(
        "Legt einen zentralen Kontakt an: felder={typ: person|stelle, anzeigename oder vorname+nachname, "
        "organisation?, funktion?, email?, erreichbarkeit?, notizen?}; telefone=[{nummer, label?, sort?, "
        "bevorzugt?, sms_eignung?}]. Moegliche Dubletten oder ungueltige Eingaben werden als ToolError gemeldet; "
        "mit duplikat_bestaetigt=true eine bekannte Dublette trotzdem anlegen."
    ),
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_anlegen(
    context: MCPContext,
    felder: dict[str, Any],
    telefone: list[dict[str, Any]] | None = None,
    kategorien: list[str] | None = None,
    duplikat_bestaetigt: bool = False,
) -> dict[str, object]:
    daten = _validiere_felder(felder)
    telefonliste = telefone or []
    kandidaten = kontakt_service.find_duplicate_candidates(
        context.db,
        anzeigename=str(daten.get("anzeigename") or ""),
        organisation=str(daten.get("organisation") or ""),
        email=str(daten.get("email") or ""),
        telefone=[str(telefon.get("nummer") or "") for telefon in telefonliste],
    )
    if kandidaten and not duplikat_bestaetigt:
        raise ValueError("Mögliche Kontakt-Dublette gefunden. Mit duplikat_bestaetigt=true bestätigen.")
    kontakt = kontakt_service.create_kontakt(
        context.db, daten, telefonliste, kategorien or [], org_id=context.org_id, user_id=context.user.id, commit=False
    )
    _audit(context, "kontakt.mcp_angelegt", kontakt.id, {"duplikat_bestaetigt": duplikat_bestaetigt})
    return _kandidat(kontakt) | {"version": kontakt.version}


@register_tool(
    name="kontakt_aktualisieren",
    description="Aktualisiert einen zentralen Kontakt mit Versionsschutz.",
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_aktualisieren(
    context: MCPContext,
    kontakt_id: int,
    version: int,
    felder: dict[str, Any] | None = None,
    telefone: list[dict[str, Any]] | None = None,
    kategorien: list[str] | None = None,
) -> dict[str, object]:
    kontakt = kontakt_service.get_kontakt(context.db, kontakt_id)
    if kontakt is None:
        raise ValueError("Kontakt nicht gefunden.")
    vorher = _kontakt_daten(kontakt)
    try:
        aktualisiert = kontakt_service.update_kontakt(
            context.db,
            kontakt_id,
            _validiere_felder(felder or {}),
            telefone if telefone is not None else vorher["telefone"],  # type: ignore[arg-type]
            kategorien if kategorien is not None else vorher["kategorien"],  # type: ignore[arg-type]
            version=version,
            org_id=context.org_id,
            user_id=context.user.id,
        )
    except kontakt_service.KontaktKonflikt:
        return {
            "kontakt_id": kontakt_id,
            "konflikt": True,
            "fehler": (
                "Der Kontakt wurde inzwischen geändert. Bitte neu laden und mit der aktuellen Version "
                "erneut versuchen."
            ),
        }
    nachher = _kontakt_daten(aktualisiert)
    _audit(context, "kontakt.mcp_aktualisiert", kontakt_id, {"vorher": vorher, "nachher": nachher})
    return {"kontakt_id": kontakt_id, "version": aktualisiert.version, "vorher": vorher, "nachher": nachher}


@register_tool(
    name="kontakt_archivieren",
    description="Archiviert einen zentralen Kontakt; bei Objektzuordnungen nur nach Bestätigung.",
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_archivieren(context: MCPContext, kontakt_id: int, bestaetigt: bool = False) -> dict[str, object]:
    kontakt = kontakt_service.get_kontakt(context.db, kontakt_id)
    if kontakt is None:
        raise ValueError("Kontakt nicht gefunden.")
    zuordnungen = kontakt_service.list_objektzuordnungen(context.db, kontakt_id)
    if zuordnungen and not bestaetigt:
        raise ValueError("Der Kontakt hat Objektzuordnungen. Mit bestaetigt=true archivieren.")
    kontakt_service.archive_kontakt(context.db, kontakt_id, user_id=context.user.id)
    _audit(context, "kontakt.mcp_archiviert", kontakt_id, {"objektzuordnungen_anzahl": len(zuordnungen)})
    return {"kontakt_id": kontakt_id, "archiviert": True, "objektzuordnungen_anzahl": len(zuordnungen)}


@register_tool(
    name="kontakt_zusammenfuehren",
    description="Führt zwei zentrale Kontakte nach expliziter Bestätigung zusammen.",
    required_roles=KONTAKT_ROLLEN,
    module_check=kontakte_modul_aktiv,
)
async def kontakt_zusammenfuehren(
    context: MCPContext,
    quelle_id: int,
    ziel_id: int,
    feldwahl: dict[str, str] | None = None,
    bestaetigt: bool = False,
) -> dict[str, object]:
    if not bestaetigt:
        raise ValueError("Das Zusammenführen erfordert bestaetigt=true.")
    ergebnis = kontakt_service.merge_kontakte(
        context.db, quelle_id, ziel_id, feldwahl or {}, user_id=context.user.id
    )
    _audit(
        context,
        "kontakt.mcp_zusammengefuehrt",
        ziel_id,
        {"quelle_id": quelle_id, "freigabe_konflikte": ergebnis.freigabe_konflikte},
    )
    return {
        "quelle_id": quelle_id,
        "ziel_id": ergebnis.kontakt.id,
        "version": ergebnis.kontakt.version,
        "freigabe_konflikte": ergebnis.freigabe_konflikte,
    }
