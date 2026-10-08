"""MCP-Werkzeuge fuer die zentrale Kontaktverwaltung."""

from __future__ import annotations

import json
from typing import Any

from app.core.audit import write_audit
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.kontakt import (
    KONTAKT_TYP_PERSON, KONTAKT_TYP_STELLE, Kontakt, KontaktImportBatch,
    KontaktImportVorschau, KontaktOrganisation, KontaktOrganisationFunktion,
)
from app.models.master import OrgSettings, SystemSettings
from app.services import kontakt_service
from app.services import kontakt_mcp_import_service as structured_import

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
    organisation_id: int | None = None,
    funktion: str = "",
    quelle: str = "",
    externe_id: str = "",
    aktiv: bool | None = None,
    aktualisiert_seit: str = "",
    vollstaendig: bool = False,
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
    # The legacy service deliberately remains the broad, backwards-compatible
    # search. Structured filters are narrowed afterwards until the list query is
    # migrated to the same relations.
    if any((organisation_id, funktion, quelle, externe_id, aktiv is not None, aktualisiert_seit)):
        query = context.db.query(Kontakt).filter(Kontakt.archiviert.is_(False))
        if aktiv is not None: query = query.filter(Kontakt.aktiv == aktiv)
        if organisation_id or funktion:
            query = query.join(KontaktOrganisationFunktion)
            if organisation_id: query = query.filter(KontaktOrganisationFunktion.organisation_id == organisation_id)
            if funktion: query = query.filter(KontaktOrganisationFunktion.funktion.ilike(f"%{funktion}%"))
        if quelle or externe_id:
            from app.models.kontakt import KontaktExterneReferenz
            query = query.join(KontaktExterneReferenz)
            if quelle: query = query.filter(KontaktExterneReferenz.quelle == quelle)
            if externe_id: query = query.filter(KontaktExterneReferenz.extern_id == externe_id)
        if aktualisiert_seit: query = query.filter(Kontakt.aktualisiert_am >= aktualisiert_seit)
        kontakte, gesamt = query.order_by(Kontakt.anzeigename, Kontakt.id).limit(limit).all(), query.count()
    result = [structured_import.kontakt_payload(kontakt) if vollstaendig else _kandidat(kontakt) for kontakt in kontakte[:limit]]
    return {"kontakte": result, "gesamt": gesamt, "seite": seite}


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
    return _kandidat(kontakt) | _kontakt_daten(kontakt) | structured_import.kontakt_payload(kontakt) | {
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


@register_tool(
    name="kontakt_organisation_upsert",
    description="Legt eine Organisation an oder aktualisiert sie über externe_id bzw. Namen.",
    required_roles=KONTAKT_ROLLEN, module_check=kontakte_modul_aktiv,
)
async def kontakt_organisation_upsert(context: MCPContext, organisation: dict[str, Any]) -> dict[str, object]:
    item = structured_import.upsert_organisation(context.db, context.org_id, organisation)
    context.db.commit()
    return {"organisations_id": item.id, "name": item.name, "aktiv": item.aktiv}


@register_tool(
    name="kontakt_organisation_suchen",
    description="Sucht Organisationen einschließlich übergeordneter Organisation und Ansprechpartnern.",
    required_roles=KONTAKT_ROLLEN, module_check=kontakte_modul_aktiv,
)
async def kontakt_organisation_suchen(context: MCPContext, q: str = "", limit: int = 25) -> dict[str, object]:
    _limit(limit)
    query = context.db.query(KontaktOrganisation).filter(KontaktOrganisation.org_id == context.org_id)
    if q.strip(): query = query.filter(KontaktOrganisation.name.ilike(f"%{q.strip()}%"))
    organisationen = query.order_by(KontaktOrganisation.name).limit(limit).all()
    return {"organisationen": [{"id": item.id, "name": item.name, "kurzname": item.kurzname, "typ": item.organisationstyp, "uebergeordnete_organisation_id": item.uebergeordnete_organisation_id, "aktiv": item.aktiv, "ansprechpartner": [{"kontakt_id": mapping.kontakt_id, "funktion": mapping.funktion} for mapping in context.db.query(KontaktOrganisationFunktion).filter_by(organisation_id=item.id, aktiv=True).all()]} for item in organisationen]}


@register_tool(
    name="kontakt_funktion_zuordnen",
    description="Ordnet einem bestehenden Kontakt eine Funktion in einer Organisation zu oder aktualisiert sie.",
    required_roles=KONTAKT_ROLLEN, module_check=kontakte_modul_aktiv,
)
async def kontakt_funktion_zuordnen(
    context: MCPContext, kontakt_id: int, organisation: dict[str, Any], funktion: dict[str, Any]
) -> dict[str, object]:
    kontakt = kontakt_service.get_kontakt(context.db, kontakt_id)
    if kontakt is None: raise ValueError("Kontakt nicht gefunden.")
    org = structured_import.upsert_organisation(context.db, context.org_id, organisation)
    payload = {"organisationen": [{**funktion, "organisation_id": org.id}]}
    structured_import._sync_relations(context.db, kontakt, payload, context.org_id, False)
    kontakt.version += 1
    context.db.commit()
    return structured_import.kontakt_payload(kontakt)


@register_tool(
    name="kontakt_bulk_upsert",
    description="Validiert und führt strukturierte Kontakt-Upserts aus. dry_run=true schreibt nicht; produktive Aufrufe benötigen bestaetigt=true.",
    required_roles=KONTAKT_ROLLEN, module_check=kontakte_modul_aktiv,
)
async def kontakt_bulk_upsert(
    context: MCPContext, kontakte: list[dict[str, Any]], modus: str = "merge", dry_run: bool = True,
    idempotency_key: str = "", bestaetigt: bool = False, quelle: str = "MCP",
) -> dict[str, object]:
    if len(kontakte) > structured_import.MAX_BATCH: raise ValueError("Hoechstens 250 Kontakte pro Aufruf")
    if not dry_run and not bestaetigt: raise ValueError("Produktive Massenänderungen benötigen bestaetigt=true oder kontakt_import_ausfuehren.")
    results: list[dict[str, object]] = []
    for row in kontakte:
        status, kontakt, candidates = structured_import.upsert_contact(
            context.db, context.org_id, context.user.id, row, quelle=quelle, modus=modus
        )
        results.append({"status": status, "kontakt_id": kontakt.id if kontakt else None, "kandidaten": candidates})
    if dry_run:
        context.db.rollback()
    else:
        context.db.commit()
    return {"dry_run": dry_run, "idempotency_key": idempotency_key or None, "ergebnisse": results}


@register_tool(
    name="kontakt_import_vorschau",
    description="Erstellt ohne Datenänderung eine persistierte Vorschau eines strukturierten Kontaktimports.",
    required_roles=KONTAKT_ROLLEN, module_check=kontakte_modul_aktiv,
)
async def kontakt_import_vorschau(
    context: MCPContext, quelle: str, kontakte: list[dict[str, Any]], quellendatum: str = "",
    importmodus: str = "merge", organisationen: list[dict[str, Any]] | None = None, optionen: dict[str, Any] | None = None,
) -> dict[str, object]:
    if len(kontakte) > structured_import.MAX_BATCH: raise ValueError("Hoechstens 250 Kontakte pro Vorschau")
    results: list[dict[str, object]] = []
    for row in kontakte:
        match, candidates = structured_import.find_match(context.db, context.org_id, row, quelle)
        if candidates:
            status = "DUPLICATE_CANDIDATE"
        elif match is None:
            status = "NEW" if importmodus != "update_only" else "UNCHANGED"
        else:
            status = "UPDATE" if importmodus != "create_only" else "UNCHANGED"
        results.append({"status": status, "kontakt_id": match.id if match else None, "version": match.version if match else None, "kandidaten": candidates})
    request = {"quelle": quelle, "quellendatum": quellendatum, "modus": importmodus, "kontakte": kontakte, "organisationen": organisationen or [], "optionen": optionen or {}}
    preview = KontaktImportVorschau(org_id=context.org_id, user_id=context.user.id, zeilen_json=json.dumps(request), ergebnis_json=json.dumps(results))
    context.db.add(preview); context.db.commit()
    return {"preview_id": preview.id, "ergebnisse": results, "neue_kontakte": sum(x["status"] == "NEW" for x in results), "aktualisierungen": sum(x["status"] == "UPDATE" for x in results), "dublettenkandidaten": [x for x in results if x["status"] == "DUPLICATE_CANDIDATE"]}


@register_tool(
    name="kontakt_import_ausfuehren",
    description="Führt ausschließlich eine zuvor erzeugte, bestätigte Importvorschau aus und protokolliert den Batch.",
    required_roles=KONTAKT_ROLLEN, module_check=kontakte_modul_aktiv,
)
async def kontakt_import_ausfuehren(
    context: MCPContext, preview_id: int, bestaetigte_konfliktentscheidungen: dict[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, object]:
    preview = context.db.query(KontaktImportVorschau).filter_by(id=preview_id, org_id=context.org_id, user_id=context.user.id).first()
    if preview is None: raise ValueError("Importvorschau nicht gefunden.")
    if idempotency_key:
        old = context.db.query(KontaktImportBatch).filter_by(org_id=context.org_id, idempotency_key=idempotency_key).first()
        if old: return {"batch_id": old.id, "erfolg": old.status == "completed", **json.loads(old.ergebnis_json)}
    request, planned = json.loads(preview.zeilen_json), json.loads(preview.ergebnis_json or "[]")
    results: list[dict[str, object]] = []
    for index, row in enumerate(request["kontakte"]):
        plan = planned[index]
        if plan["status"] == "DUPLICATE_CANDIDATE":
            results.append({"status": "SKIPPED", "reason": "Dublettenentscheidung erforderlich"}); continue
        match, _candidates = structured_import.find_match(context.db, context.org_id, row, request["quelle"])
        if plan.get("kontakt_id") and (match is None or match.id != plan["kontakt_id"] or match.version != plan.get("version")):
            results.append({"status": "CONFLICT", "reason": "Datensatz wurde nach der Vorschau verändert"}); continue
        status, kontakt, candidates = structured_import.upsert_contact(context.db, context.org_id, context.user.id, row, quelle=request["quelle"], modus=request["modus"])
        results.append({"status": status, "kontakt_id": kontakt.id if kontakt else None, "kandidaten": candidates})
    summary = {"angelegt": sum(x["status"] == "NEW" for x in results), "aktualisiert": sum(x["status"] == "UPDATE" for x in results), "uebersprungen": sum(x["status"] in {"SKIPPED", "UNCHANGED"} for x in results), "fehler": sum(x["status"] in {"CONFLICT", "DUPLICATE_CANDIDATE"} for x in results), "ergebnisse": results}
    batch = KontaktImportBatch(org_id=context.org_id, user_id=context.user.id, quelle=request["quelle"], idempotency_key=idempotency_key or None, request_json=preview.zeilen_json, ergebnis_json=json.dumps(summary))
    context.db.add(batch); context.db.commit()
    return {"batch_id": batch.id, "erfolg": summary["fehler"] == 0, **summary}


@register_tool(
    name="kontakt_import_status", description="Liest den revisionssicheren Ergebnisstand eines Kontaktimport-Batches.",
    required_roles=KONTAKT_ROLLEN, module_check=kontakte_modul_aktiv,
)
async def kontakt_import_status(context: MCPContext, batch_id: int) -> dict[str, object]:
    batch = context.db.query(KontaktImportBatch).filter_by(id=batch_id, org_id=context.org_id).first()
    if batch is None: raise ValueError("Import-Batch nicht gefunden.")
    return {"batch_id": batch.id, "status": batch.status, "quelle": batch.quelle, **json.loads(batch.ergebnis_json)}
