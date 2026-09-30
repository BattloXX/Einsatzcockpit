"""MCP-Werkzeuge fuer Objektentwuerfe und zentrale Kontakte."""

from __future__ import annotations

import asyncio
import threading
from datetime import date
from typing import Any

from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.audit import write_audit
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.mcp.context import MCPContext
from app.mcp.registry import register_tool
from app.models.kontakt import Kontakt
from app.models.master import OrgSettings, SystemSettings
from app.models.objekt import (
    AUSWAHL_DOKUMENTART,
    AUSWAHL_KONTAKTART,
    OBJEKT_STATUS_ARCHIVIERT,
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_UEBERARBEITUNG,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektBMA,
    ObjektKategorie,
    ObjektKontakt,
)
from app.services import kontakt_service
from app.services.objekt_pflege_schreiben_service import (
    ObjektFehler,
    bma_speichern,
    erstelle_objekt,
    gefahr_anlegen,
    gefahr_entfernen,
    kontakt_zuordnen,
    kontakt_zuordnung_aendern,
    kontakt_zuordnung_entfernen,
    merkmal_entfernen,
    merkmal_zuordnen,
    suche_objekte,
    wohnanlage_speichern,
    zusatzadresse_anlegen,
    zusatzadresse_entfernen,
)
from app.services.objekt_pflege_service import hole_offenen_pflegeauftrag
from app.services.objekt_plan_upload_service import finde_passendes_objekt
from app.services.objekt_service import (
    aktualisiere_felder,
    erstelle_arbeitskopie,
    hole_arbeitskopie,
    lade_auswahl,
    objekt_effective_enabled,
)

MAX_LIMIT = 50


def objekt_modul_aktiv(org_id: int, db: object) -> bool:
    return objekt_effective_enabled(org_id, db)  # type: ignore[arg-type]


def kontakte_modul_aktiv(org_id: int, db: Any) -> bool:
    system = (
        db.query(SystemSettings)
        .filter(SystemSettings.key == "kontakte_module_enabled", SystemSettings.value == "true")
        .first()
    )  # type: ignore[attr-defined]
    org = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()  # type: ignore[attr-defined]
    return bool(system and org and org.kontakte_module_enabled)


def _limit(limit: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit muss zwischen 1 und 50 liegen.")


def _stammdaten_normalisieren(stammdaten: dict[str, Any]) -> dict[str, Any]:
    """Konvertiert das MCP-ISO-Datum für das Date-Feld der Datenbank."""
    daten = dict(stammdaten)
    revision_datum = daten.get("revision_datum")
    if revision_datum is not None and revision_datum != "":
        if not isinstance(revision_datum, str):
            raise ValueError("revision_datum muss ein ISO-Datum sein.")
        try:
            daten["revision_datum"] = date.fromisoformat(revision_datum)
        except ValueError as exc:
            raise ValueError("revision_datum muss ein ISO-Datum sein.") from exc
    elif revision_datum == "":
        daten["revision_datum"] = None
    return daten


def _wohnanlage_speichern_mcp(
    objekt: Objekt, wohnanlage: dict[str, Any], *, context: MCPContext
) -> None:
    vorhanden = bool(wohnanlage.get("vorhanden", True))
    daten = {key: value for key, value in wohnanlage.items() if key != "vorhanden"}
    erlaubte = {"wohneinheiten", "geschosse", "stiegen", "hausverwaltung_kontakt_id", "hinweise"}
    if set(daten) - erlaubte:
        raise ValueError("Unbekannte Wohnanlagen-Felder: " + ", ".join(sorted(set(daten) - erlaubte)))
    hausverwaltung_id = daten.get("hausverwaltung_kontakt_id")
    if hausverwaltung_id is not None and not any(k.id == hausverwaltung_id for k in objekt.kontakte):
        raise ValueError("Hausverwaltung muss eine Kontakt-Zuordnung dieses Objekts sein.")
    wohnanlage_speichern(
        context.db, objekt, user_id=context.user.id, vorhanden=vorhanden, daten=daten, quelle="mcp"
    )


def _objekt_kandidat(objekt: Objekt) -> dict[str, object]:
    return {
        "id": objekt.id,
        "anzeige_nummer": objekt.nummer,
        "name": objekt.name,
        "adresse": " ".join(x for x in (objekt.strasse, objekt.hausnummer, objekt.plz, objekt.ort) if x),
        "status": objekt.status,
    }


def _kontakt_kandidat(kontakt: Kontakt) -> dict[str, object]:
    return {
        "id": kontakt.id,
        "anzeigename": kontakt.anzeigename,
        "organisation": kontakt.organisation,
        "funktion": kontakt.funktion,
    }


def _geocoding_starten(objekt_id: int, strasse: str | None, hausnummer: str | None, ort: str | None) -> None:
    if not (strasse or ort):
        return

    def laufen() -> None:
        from app.services.geocoding import geocode_address

        try:
            geo = asyncio.run(geocode_address(strasse, hausnummer, ort))
            if not geo:
                return
            db = SessionLocal()
            set_tenant_context(db, None)
            try:
                objekt = db.get(Objekt, objekt_id)
                if objekt and objekt.lat is None and objekt.lng is None:
                    objekt.lat, objekt.lng = geo.lat, geo.lng
                    db.commit()
            finally:
                db.close()
        except Exception:
            return

    threading.Thread(target=laufen, name=f"mcp-geocode-objekt-{objekt_id}", daemon=True).start()


@register_tool(
    name="objekt_kataloge",
    description="Liest gueltige Objekt-Katalogwerte.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_kataloge(context: MCPContext) -> dict[str, object]:
    db, org_id = context.db, context.org_id
    return {
        "kategorien": [{"id": x.id, "name": x.name} for x in db.query(ObjektKategorie).filter_by(aktiv=True).all()],
        "gefahren": [{"id": x.id, "name": x.name} for x in db.query(GefahrenKatalog).filter_by(aktiv=True).all()],
        "merkmale": [{"id": x.id, "name": x.name} for x in db.query(MerkmalKatalog).filter_by(aktiv=True).all()],
        "kontaktarten": lade_auswahl(db, org_id, AUSWAHL_KONTAKTART),
        "dokumentarten": lade_auswahl(db, org_id, AUSWAHL_DOKUMENTART),
    }


@register_tool(
    name="objekt_suchen",
    description="Sucht Objekte der eigenen Organisation.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_suchen(context: MCPContext, q: str = "", status: str = "", limit: int = 25) -> dict[str, object]:
    _limit(limit)
    return {
        "objekte": [
            _objekt_kandidat(o)
            | {"hat_arbeitskopie": bool(context.db.query(Objekt).filter(Objekt.entwurf_von_id == o.id).first())}
            for o in suche_objekte(context.db, context.org_id, q, status, limit)
        ]
    }


@register_tool(
    name="objekt_lesen",
    description="Liest ein Objekt ohne Telefon- oder E-Mail-Klartext.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_lesen(context: MCPContext, objekt_id: int, arbeitskopie: bool = False) -> dict[str, object]:
    objekt = (
        context.db.query(Objekt)
        .options(
            selectinload(Objekt.bma),
            selectinload(Objekt.kontakte).selectinload(ObjektKontakt.zentraler_kontakt),
            selectinload(Objekt.gefahren),
            selectinload(Objekt.merkmale),
            selectinload(Objekt.zusatzadressen),
        )
        .filter(Objekt.id == objekt_id, Objekt.org_id == context.org_id)
        .first()
    )
    if objekt is None:
        raise ValueError("Objekt nicht gefunden.")
    gefundene_arbeitskopie = context.db.query(Objekt).filter(Objekt.entwurf_von_id == objekt.id).first()
    basis_objekt_id = objekt.entwurf_von_id
    if arbeitskopie and gefundene_arbeitskopie is not None:
        basis_objekt_id = objekt.id
        objekt = (
            context.db.query(Objekt)
            .options(
                selectinload(Objekt.bma),
                selectinload(Objekt.kontakte).selectinload(ObjektKontakt.zentraler_kontakt),
                selectinload(Objekt.gefahren), selectinload(Objekt.merkmale), selectinload(Objekt.zusatzadressen),
            )
            .filter(Objekt.id == gefundene_arbeitskopie.id, Objekt.org_id == context.org_id)
            .one()
        )
    return _objekt_kandidat(objekt) | {
        "basis_objekt_id": basis_objekt_id,
        "vulgoname": objekt.vulgoname,
        "adresse": {"strasse": objekt.strasse, "hausnummer": objekt.hausnummer, "plz": objekt.plz, "ort": objekt.ort},
        "bma": {"bma_nummer": objekt.bma.bma_nummer, "rfl_nummer": objekt.bma.rfl_nummer} if objekt.bma else None,
        "kontakte": [
            {
                "zuordnung_id": k.id,
                "kontakt_id": k.kontakt_id,
                "anzeigename": k.zentraler_kontakt.anzeigename if k.zentraler_kontakt else None,
                "funktion": k.zentraler_kontakt.funktion if k.zentraler_kontakt else None,
                "art": k.art,
                "sort": k.sort,
                "erreichbarkeit": k.erreichbarkeit,
                "freigaben_anzahl": len(k.freigaben),
            }
            for k in objekt.kontakte
        ],
        "gefahren": [{"id": g.id, "gefahr_id": g.gefahr_id, "sort": g.sort} for g in objekt.gefahren],
        "merkmale": [{"id": m.id, "merkmal_id": m.merkmal_id, "hinweis": m.hinweis} for m in objekt.merkmale],
        "zusatzadressen": [
            {"id": z.id, "bezeichnung": z.bezeichnung, "strasse": z.strasse, "hausnummer": z.hausnummer,
             "plz": z.plz, "ort": z.ort, "sort": z.sort}
            for z in objekt.zusatzadressen
        ],
        "arbeitskopie_id": gefundene_arbeitskopie.id if gefundene_arbeitskopie else None,
        "hat_arbeitskopie": bool(gefundene_arbeitskopie),
        "hinweis": (
            f"Arbeitskopie vorhanden (ID {gefundene_arbeitskopie.id}); mit arbeitskopie=true deren Kinder lesen."
            if gefundene_arbeitskopie and not arbeitskopie else None
        ),
        "offener_pflegeauftrag": bool(hole_offenen_pflegeauftrag(context.db, gefundene_arbeitskopie or objekt)),
        "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}",
    }


@register_tool(
    name="kontakt_suchen",
    description="Sucht zentrale Kontakte ohne Telefon oder E-Mail.",
    required_roles=("objekt_verwalter",),
    module_check=lambda org_id, db: objekt_modul_aktiv(org_id, db) and kontakte_modul_aktiv(org_id, db),
)
async def kontakt_suchen(context: MCPContext, q: str = "", limit: int = 25) -> dict[str, object]:
    _limit(limit)
    kontakte, _ = kontakt_service.list_kontakte(context.db, q=q)
    return {"kontakte": [_kontakt_kandidat(k) for k in kontakte[:limit]]}


@register_tool(
    name="objekt_duplikate_pruefen",
    description="Prueft moegliche Objekt-Dubletten vor der Anlage.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_duplikate_pruefen(
    context: MCPContext,
    name: str = "",
    strasse: str = "",
    hausnummer: str = "",
    plz: str = "",
    ort: str = "",
    bma_nummer: str = "",
    rfl_nummer: str = "",
) -> dict[str, object]:
    identitaet = {
        "name": name,
        "strasse": strasse,
        "hausnummer": hausnummer,
        "ort": ort,
        "bma_nummer": bma_nummer or rfl_nummer,
    }
    exakt = finde_passendes_objekt(context.db, context.org_id, identitaet)
    query = context.db.query(Objekt).filter(Objekt.org_id == context.org_id, Objekt.entwurf_von_id.is_(None))
    if plz and strasse:
        kandidaten = (
            query.filter(
                Objekt.plz == plz.strip(),
                Objekt.strasse.ilike(f"%{strasse.strip()}%"),
                *([Objekt.hausnummer == hausnummer.strip()] if hausnummer else []),
            )
            .order_by(Objekt.nummer)
            .limit(MAX_LIMIT)
            .all()
        )
    elif name.strip():
        kandidaten = query.filter(Objekt.name.ilike(f"%{name.strip()}%")).order_by(Objekt.nummer).limit(MAX_LIMIT).all()
    else:
        kandidaten = []
    if exakt and exakt not in kandidaten:
        kandidaten.insert(0, exakt)
    return {"duplikate_gefunden": bool(kandidaten), "kandidaten": [_objekt_kandidat(o) for o in kandidaten]}


@register_tool(
    name="kontakt_duplikate_pruefen",
    description="Prueft moegliche Kontakt-Dubletten.",
    required_roles=("objekt_verwalter",),
    module_check=lambda org_id, db: objekt_modul_aktiv(org_id, db) and kontakte_modul_aktiv(org_id, db),
)
async def kontakt_duplikate_pruefen(
    context: MCPContext, anzeigename: str, organisation: str = "", email: str = "", telefone: list[str] | None = None
) -> dict[str, object]:
    kandidaten = kontakt_service.find_duplicate_candidates(
        context.db, anzeigename=anzeigename, organisation=organisation, email=email, telefone=telefone or []
    )
    return {"duplikate_gefunden": bool(kandidaten), "kandidaten": [_kontakt_kandidat(k) for k in kandidaten]}


def _kontakte_anlegen(
    context: MCPContext, objekt: Objekt, kontakte: list[dict[str, Any]], bestaetigt: bool
) -> tuple[list[str], list[ObjektKontakt]]:
    hinweise = []
    zuordnungen = []
    for eintrag in kontakte:
        if not isinstance(eintrag, dict) or not eintrag.get("art"):
            raise ValueError("Jeder Kontakt braucht eine Kontaktart.")
        if eintrag.get("kontakt_id"):
            kontakt = (
                context.db.query(Kontakt)
                .filter(Kontakt.id == eintrag["kontakt_id"], Kontakt.org_id == context.org_id)
                .first()
            )
            if kontakt is None:
                raise ValueError("Bestehender Kontakt wurde nicht gefunden.")
            if eintrag.get("abweichende_daten"):
                hinweise.append(
                    f"Bestehender Kontakt {kontakt.anzeigename} wurde nicht veraendert; "
                    "abweichende Daten bitte im Einsatzcockpit pruefen."
                )
        elif isinstance(eintrag.get("neu"), dict):
            daten = eintrag["neu"]
            name = str(daten.get("anzeigename") or "").strip()
            if not name:
                raise ValueError("Ein neuer Kontakt braucht einen Anzeigenamen.")
            telefone = daten.get("telefone") or []
            nummern = [str(x.get("nummer", "")) if isinstance(x, dict) else str(x) for x in telefone]
            duplikate = kontakt_service.find_duplicate_candidates(
                context.db,
                anzeigename=name,
                organisation=daten.get("organisation"),
                email=daten.get("email"),
                telefone=nummern,
            )
            if duplikate and not bestaetigt:
                raise ValueError(
                    "Kontakt-Dublette gefunden. Bitte duplikat_bestaetigt=true setzen. Kandidaten: "
                    + ", ".join(str(x.id) for x in duplikate)
                )
            kontakt = kontakt_service.create_kontakt(
                context.db,
                daten,
                [x if isinstance(x, dict) else {"nummer": x} for x in telefone],
                [],
                org_id=context.org_id,
                user_id=context.user.id,
                commit=False,
            )
        else:
            raise ValueError("Kontakt braucht kontakt_id oder neu.")
        erreichbarkeit = eintrag.get("erreichbarkeit")
        if erreichbarkeit is not None:
            if not isinstance(erreichbarkeit, str) or len(erreichbarkeit) > 200:
                raise ValueError("Erreichbarkeit darf maximal 200 Zeichen lang sein.")
        zuordnung = kontakt_zuordnen(
            context.db,
            objekt,
            kontakt,
            art=str(eintrag["art"]),
            erreichbarkeit=erreichbarkeit,
            user_id=context.user.id,
            quelle="mcp",
        )
        zuordnungen.append(zuordnung)
    return hinweise, zuordnungen


@register_tool(
    name="objekt_anlegen",
    description="Legt ausschliesslich einen Objekt-Entwurf an.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_anlegen(
    context: MCPContext,
    stammdaten: dict[str, Any],
    bma: dict[str, Any] | None = None,
    gefahren: list[dict[str, Any]] | None = None,
    merkmale: list[dict[str, Any]] | None = None,
    zusatzadressen: list[dict[str, Any]] | None = None,
    kontakte: list[dict[str, Any]] | None = None,
    wohnanlage: dict[str, Any] | None = None,
    duplikat_bestaetigt: bool = False,
) -> dict[str, object]:
    if not isinstance(stammdaten, dict):
        raise ValueError("stammdaten muss ein Objekt sein.")
    erlaubte_stammdaten = {
        "name", "vulgoname", "kategorie_id", "strasse", "hausnummer", "plz", "ort", "lat", "lng",
        "informationen", "anfahrtsweg", "revision_datum",
    }
    if set(stammdaten) - erlaubte_stammdaten:
        raise ValueError("Unbekannte Stammdaten-Felder: " + ", ".join(sorted(set(stammdaten) - erlaubte_stammdaten)))
    stammdaten = _stammdaten_normalisieren(stammdaten)
    identitaet = {key: stammdaten.get(key, "") for key in ("name", "strasse", "hausnummer", "ort")}
    if bma:
        identitaet["bma_nummer"] = bma.get("bma_nummer") or bma.get("rfl_nummer") or ""
    duplikat = finde_passendes_objekt(context.db, context.org_id, identitaet)
    if duplikat and not duplikat_bestaetigt:
        raise ValueError(
            f"Objekt-Dublette gefunden (ID {duplikat.id}: {duplikat.name}). Bitte duplikat_bestaetigt=true setzen."
        )
    try:
        objekt, geocodieren = erstelle_objekt(
            context.db, org_id=context.org_id, user_id=context.user.id, quelle="mcp", **stammdaten
        )
        if bma:
            erlaubte = {c.name for c in ObjektBMA.__table__.columns} - {"id", "org_id", "objekt_id"}
            if set(bma) - erlaubte:
                raise ValueError("Unbekannte BMA-Felder: " + ", ".join(sorted(set(bma) - erlaubte)))
            bma_speichern(context.db, objekt, user_id=context.user.id, vorhanden=True, daten=bma, quelle="mcp")
        for gefahr in gefahren or []:
            if not isinstance(gefahr, dict) or not gefahr.get("gefahr_id"):
                raise ValueError("Jede Gefahr braucht gefahr_id.")
            gefahr_anlegen(context.db, objekt, user_id=context.user.id, quelle="mcp", **gefahr)
        for merkmal in merkmale or []:
            if not isinstance(merkmal, dict) or not merkmal.get("merkmal_id"):
                raise ValueError("Jedes Merkmal braucht merkmal_id.")
            merkmal_zuordnen(context.db, objekt, user_id=context.user.id, quelle="mcp", **merkmal)
        for adresse in zusatzadressen or []:
            if not isinstance(adresse, dict):
                raise ValueError("Jede Zusatzadresse muss ein Objekt sein.")
            zusatzadresse_anlegen(context.db, objekt, user_id=context.user.id, quelle="mcp", **adresse)
        hinweise, _ = _kontakte_anlegen(context, objekt, kontakte or [], duplikat_bestaetigt)
        if wohnanlage is not None:
            if not isinstance(wohnanlage, dict):
                raise ValueError("wohnanlage muss ein Objekt sein.")
            _wohnanlage_speichern_mcp(objekt, wohnanlage, context=context)
        write_audit(
            context.db,
            "objekt.mcp_angelegt",
            org_id=context.org_id,
            user_id=context.user.id,
            entity_type="objekt",
            entity_id=objekt.id,
            payload={"nummer": objekt.nummer},
        )
        context.db.commit()
    except Exception as exc:
        context.db.rollback()
        if isinstance(exc, ObjektFehler):
            raise ValueError(str(exc)) from exc
        raise
    if geocodieren:
        _geocoding_starten(objekt.id, objekt.strasse, objekt.hausnummer, objekt.ort)
    return {
        "objekt_id": objekt.id,
        "anzeige_nummer": objekt.nummer,
        "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}",
        "status": "entwurf",
        "hinweis": "Entwurf - bitte im Einsatzcockpit pruefen und freigeben.",
        "hinweise": hinweise,
    }


def _entfern_id(eintrag: int | dict[str, Any], bezeichnung: str) -> int:
    if isinstance(eintrag, int):
        return eintrag
    if isinstance(eintrag, dict) and isinstance(eintrag.get("id"), int):
        return eintrag["id"]
    raise ValueError(f"Jeder zu entfernende {bezeichnung}-Eintrag braucht eine ID.")


@register_tool(
    name="objekt_aktualisieren",
    description="Aktualisiert einen Objektentwurf oder legt fuer ein freigegebenes Objekt eine Arbeitskopie an.",
    required_roles=("objekt_verwalter",),
    module_check=objekt_modul_aktiv,
)
async def objekt_aktualisieren(
    context: MCPContext,
    objekt_id: int,
    stammdaten: dict[str, Any] | None = None,
    bma: dict[str, Any] | None = None,
    gefahren_hinzufuegen: list[dict[str, Any]] | None = None,
    gefahren_entfernen: list[int | dict[str, Any]] | None = None,
    merkmale_hinzufuegen: list[dict[str, Any]] | None = None,
    merkmale_entfernen: list[int | dict[str, Any]] | None = None,
    zusatzadressen_hinzufuegen: list[dict[str, Any]] | None = None,
    zusatzadressen_entfernen: list[int | dict[str, Any]] | None = None,
    kontakte_hinzufuegen: list[dict[str, Any]] | None = None,
    kontakte_entfernen: list[int | dict[str, Any]] | None = None,
    kontakte_aendern: list[dict[str, Any]] | None = None,
    wohnanlage: dict[str, Any] | None = None,
    duplikat_bestaetigt: bool = False,
) -> dict[str, object]:
    """Aendert ausschliesslich den Entwurf bzw. die Arbeitskopie eines Objekts."""
    db = context.db
    basis = (
        db.query(Objekt)
        .filter(Objekt.id == objekt_id, Objekt.org_id == context.org_id, Objekt.entwurf_von_id.is_(None))
        .first()
    )
    if basis is None:
        raise ValueError("Objekt nicht gefunden.")
    if basis.status == OBJEKT_STATUS_ARCHIVIERT:
        raise ValueError("Archivierte Objekte koennen nicht aktualisiert werden.")
    kontakt_operation = bool((kontakte_hinzufuegen or []) or (kontakte_entfernen or []) or (kontakte_aendern or []))
    if kontakt_operation and not kontakte_modul_aktiv(context.org_id, db):
        raise ValueError("Kontakte-Modul ist nicht aktiv; Kontaktzuordnungen koennen nicht bearbeitet werden.")

    erlaubte_stammdaten = {
        "name", "vulgoname", "kategorie_id", "strasse", "hausnummer", "plz", "ort", "lat", "lng",
        "informationen", "anfahrtsweg", "revision_datum",
    }
    if stammdaten is not None and (not isinstance(stammdaten, dict) or set(stammdaten) - erlaubte_stammdaten):
        unbekannt = set(stammdaten or {}) - erlaubte_stammdaten
        raise ValueError("Unbekannte Stammdaten-Felder: " + ", ".join(sorted(unbekannt)))
    if stammdaten is not None:
        stammdaten = _stammdaten_normalisieren(stammdaten)

    try:
        if basis.status == OBJEKT_STATUS_FREIGEGEBEN:
            objekt = erstelle_arbeitskopie(db, basis, context.user.id)
        elif basis.status == OBJEKT_STATUS_UEBERARBEITUNG:
            arbeitskopie = hole_arbeitskopie(db, basis)
            if arbeitskopie is None:
                raise ValueError("Objekt ist in Ueberarbeitung, aber die Arbeitskopie fehlt.")
            objekt = arbeitskopie
        elif basis.status == OBJEKT_STATUS_ENTWURF:
            objekt = basis
        else:
            raise ValueError("Objekt kann in seinem aktuellen Status nicht aktualisiert werden.")
        assert objekt is not None
        auftrag = hole_offenen_pflegeauftrag(db, basis)
        if auftrag is not None and auftrag.arbeitskopie_id == objekt.id:
            raise ValueError(
                "Die Arbeitskopie gehoert zu einem offenen Pflegeauftrag und kann nicht per MCP bearbeitet werden."
            )

        geaenderte_felder: list[dict[str, object]] = []
        if stammdaten:
            vorher = {feld: getattr(objekt, feld) for feld in stammdaten}
            for feld in aktualisiere_felder(db, objekt, stammdaten, "stammdaten", context.user.id, "mcp"):
                geaenderte_felder.append({"feld": feld, "vorher": vorher[feld], "nachher": getattr(objekt, feld)})

        if bma is not None:
            if not isinstance(bma, dict):
                raise ValueError("bma muss ein Objekt sein.")
            vorhanden = bool(bma.get("vorhanden", True))
            bma_daten = {key: value for key, value in bma.items() if key != "vorhanden"}
            erlaubte_bma = {c.name for c in ObjektBMA.__table__.columns} - {"id", "org_id", "objekt_id"}
            if set(bma_daten) - erlaubte_bma:
                raise ValueError("Unbekannte BMA-Felder: " + ", ".join(sorted(set(bma_daten) - erlaubte_bma)))
            vorher = {feld: getattr(objekt.bma, feld) for feld in bma_daten} if objekt.bma else {}
            bma_speichern(db, objekt, user_id=context.user.id, vorhanden=vorhanden, daten=bma_daten, quelle="mcp")
            for feld, alt in vorher.items():
                if vorhanden and getattr(objekt.bma, feld) != alt:
                    geaenderte_felder.append(
                        {"feld": f"bma.{feld}", "vorher": alt, "nachher": getattr(objekt.bma, feld)}
                    )
            if not vorhanden and vorher:
                geaenderte_felder.append({"feld": "bma", "vorher": "vorhanden", "nachher": None})

        for gefahr in gefahren_hinzufuegen or []:
            if not isinstance(gefahr, dict) or not gefahr.get("gefahr_id"):
                raise ValueError("Jede Gefahr braucht gefahr_id.")
            neu = gefahr_anlegen(db, objekt, user_id=context.user.id, quelle="mcp", **gefahr)
            geaenderte_felder.append({"feld": "gefahren", "vorher": None, "nachher": neu.id})
        for eintrag in gefahren_entfernen or []:
            ident = _entfern_id(eintrag, "Gefahren")
            gefahr_entfernen(db, objekt, ident, user_id=context.user.id, quelle="mcp")
            geaenderte_felder.append({"feld": "gefahren", "vorher": ident, "nachher": None})
        for merkmal in merkmale_hinzufuegen or []:
            if not isinstance(merkmal, dict) or not merkmal.get("merkmal_id"):
                raise ValueError("Jedes Merkmal braucht merkmal_id.")
            neues_merkmal = merkmal_zuordnen(db, objekt, user_id=context.user.id, quelle="mcp", **merkmal)
            if neues_merkmal is not None:
                geaenderte_felder.append({"feld": "merkmale", "vorher": None, "nachher": neues_merkmal.id})
        for eintrag in merkmale_entfernen or []:
            ident = _entfern_id(eintrag, "Merkmal")
            merkmal_entfernen(db, objekt, ident, user_id=context.user.id, quelle="mcp")
            geaenderte_felder.append({"feld": "merkmale", "vorher": ident, "nachher": None})
        for adresse in zusatzadressen_hinzufuegen or []:
            if not isinstance(adresse, dict):
                raise ValueError("Jede Zusatzadresse muss ein Objekt sein.")
            neue_adresse = zusatzadresse_anlegen(db, objekt, user_id=context.user.id, quelle="mcp", **adresse)
            geaenderte_felder.append({"feld": "zusatzadressen", "vorher": None, "nachher": neue_adresse.id})
        for eintrag in zusatzadressen_entfernen or []:
            ident = _entfern_id(eintrag, "Zusatzadress")
            zusatzadresse_entfernen(db, objekt, ident, user_id=context.user.id, quelle="mcp")
            geaenderte_felder.append({"feld": "zusatzadressen", "vorher": ident, "nachher": None})
        hinweise, neue_zuordnungen = _kontakte_anlegen(context, objekt, kontakte_hinzufuegen or [], duplikat_bestaetigt)
        for zuordnung in neue_zuordnungen:
            geaenderte_felder.append({"feld": "kontakte", "vorher": None, "nachher": zuordnung.id})
        for eintrag in kontakte_aendern or []:
            if not isinstance(eintrag, dict) or not isinstance(eintrag.get("zuordnung_id"), int):
                raise ValueError("Jede Kontakt-Aenderung braucht zuordnung_id.")
            erlaubte = {"zuordnung_id", "art", "sort", "erreichbarkeit"}
            if set(eintrag) - erlaubte:
                raise ValueError("Unbekannte Kontakt-Aenderungsfelder: " + ", ".join(sorted(set(eintrag) - erlaubte)))
            geaendert = kontakt_zuordnung_aendern(
                db, objekt, eintrag["zuordnung_id"], art=eintrag.get("art"), sort=eintrag.get("sort"),
                erreichbarkeit=eintrag.get("erreichbarkeit"), user_id=context.user.id, quelle="mcp",
            )
            geaenderte_felder.append({"feld": "kontakte", "vorher": geaendert.id, "nachher": geaendert.id})
        for eintrag in kontakte_entfernen or []:
            ident = _entfern_id(eintrag, "Kontakt")
            kontakt_zuordnung_entfernen(db, objekt, ident, user_id=context.user.id, quelle="mcp")
            geaenderte_felder.append({"feld": "kontakte", "vorher": ident, "nachher": None})
        if wohnanlage is not None:
            if not isinstance(wohnanlage, dict):
                raise ValueError("wohnanlage muss ein Objekt sein.")
            _wohnanlage_speichern_mcp(objekt, wohnanlage, context=context)
        write_audit(db, "objekt.mcp_aktualisiert", org_id=context.org_id, user_id=context.user.id,
                    entity_type="objekt", entity_id=objekt.id, payload={"basis_objekt_id": basis.id})
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, ObjektFehler):
            raise ValueError(str(exc)) from exc
        raise
    return {
        "objekt_id": objekt.id,
        "basis_objekt_id": basis.id,
        "geaenderte_felder": geaenderte_felder,
        "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}",
        "hinweise": hinweise,
        "kontakte_hinzugefuegt": [{"zuordnung_id": z.id, "kontakt_id": z.kontakt_id} for z in neue_zuordnungen],
        "hinweis": "Aenderungen liegen als Arbeitskopie vor - bitte im Einsatzcockpit pruefen und freigeben.",
    }
