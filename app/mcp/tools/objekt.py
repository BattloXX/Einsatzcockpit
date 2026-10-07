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
    ObjektGefahr,
    ObjektKategorie,
    ObjektKontakt,
    ObjektMerkmal,
)
from app.services import kontakt_service
from app.services.objekt_pflege_schreiben_service import (
    ObjektFehler,
    bma_speichern,
    erstelle_objekt,
    gefahr_aendern,
    gefahr_anlegen,
    gefahr_entfernen,
    kontakt_zuordnen,
    kontakt_zuordnung_aendern,
    kontakt_zuordnung_entfernen,
    merkmal_aendern,
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
    links_aus_form,
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
    description=(
        "Liefert Stammdaten inkl. informationen (allgemeiner Hinweistext) und anfahrtsweg, BMA, Gefahren mit "
        "Details, Merkmale mit Hinweis, Zusatzadressen, Wohnanlage und Kontakt-Zuordnungen ohne Kontakt-Klartext."
    ),
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
            selectinload(Objekt.wohnanlage),
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
                selectinload(Objekt.gefahren),
                selectinload(Objekt.merkmale),
                selectinload(Objekt.zusatzadressen),
                selectinload(Objekt.wohnanlage),
            )
            .filter(Objekt.id == gefundene_arbeitskopie.id, Objekt.org_id == context.org_id)
            .one()
        )
    return _objekt_kandidat(objekt) | {
        "basis_objekt_id": basis_objekt_id,
        "vulgoname": objekt.vulgoname,
        "kategorie_id": objekt.kategorie_id,
        "kategorie": objekt.kategorie.name if objekt.kategorie else None,
        "lat": objekt.lat,
        "lng": objekt.lng,
        "informationen": objekt.informationen,
        "anfahrtsweg": objekt.anfahrtsweg,
        "revision_datum": objekt.revision_datum.isoformat() if objekt.revision_datum else None,
        "adresse": {"strasse": objekt.strasse, "hausnummer": objekt.hausnummer, "plz": objekt.plz, "ort": objekt.ort},
        "bma": (
            {
                column.name: getattr(objekt.bma, column.name)
                for column in ObjektBMA.__table__.columns
                if column.name not in {"id", "org_id", "objekt_id", "benachrichtigung_sms", "benachrichtigung_email"}
            }
            | {
                "benachrichtigung_sms_gesetzt": bool(objekt.bma.benachrichtigung_sms),
                "benachrichtigung_email_gesetzt": bool(objekt.bma.benachrichtigung_email),
            }
            if objekt.bma
            else None
        ),
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
        "gefahren": [
            {
                "id": g.id,
                "gefahr_id": g.gefahr_id,
                "name": g.gefahr.name if g.gefahr else None,
                "un_nummer": g.un_nummer,
                "stoffname": g.stoffname,
                "gefahrklasse": g.gefahrklasse,
                "gefahrnummer": g.gefahrnummer,
                "detail": g.detail,
                "links": g.links,
                "sort": g.sort,
            }
            for g in objekt.gefahren
        ],
        "merkmale": [
            {
                "id": m.id,
                "merkmal_id": m.merkmal_id,
                "name": m.merkmal.name if m.merkmal else None,
                "hinweis": m.hinweis,
            }
            for m in objekt.merkmale
        ],
        "zusatzadressen": [
            {"id": z.id, "bezeichnung": z.bezeichnung, "strasse": z.strasse, "hausnummer": z.hausnummer,
             "plz": z.plz, "ort": z.ort, "sort": z.sort}
            for z in objekt.zusatzadressen
        ],
        "wohnanlage": (
            {
                "wohneinheiten": objekt.wohnanlage.wohneinheiten,
                "geschosse": objekt.wohnanlage.geschosse,
                "stiegen": objekt.wohnanlage.stiegen,
                "hausverwaltung_kontakt_id": objekt.wohnanlage.hausverwaltung_kontakt_id,
                "hinweise": objekt.wohnanlage.hinweise,
            }
            if objekt.wohnanlage
            else None
        ),
        "arbeitskopie_id": gefundene_arbeitskopie.id if gefundene_arbeitskopie else None,
        "hat_arbeitskopie": bool(gefundene_arbeitskopie),
        "arbeitskopie_hinweis": (
            f"Arbeitskopie vorhanden (ID {gefundene_arbeitskopie.id}); mit arbeitskopie=true deren Kinder lesen."
            if gefundene_arbeitskopie and not arbeitskopie else None
        ),
        "offener_pflegeauftrag": bool(hole_offenen_pflegeauftrag(context.db, gefundene_arbeitskopie or objekt)),
        "ui_link": f"{settings.effective_public_base_url.rstrip('/')}/objekte/{objekt.id}",
    }


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


_KONTAKT_NEU_FELDER = (
    "typ", "anzeigename", "vorname", "nachname", "funktion", "organisation", "email", "erreichbarkeit", "notizen",
)
_KONTAKT_TELEFON_SCHLUESSEL = (("telefon", "Telefon"), ("mobil", "Mobil"), ("handy", "Mobil"))


def _kontakt_eintrag_normalisieren(eintrag: Any) -> Any:
    """Erlaubt neben {art, neu: {...}} auch flache Kontakte (art, vorname, nachname, mobil, telefon, email)."""
    if not isinstance(eintrag, dict) or eintrag.get("kontakt_id") or isinstance(eintrag.get("neu"), dict):
        return eintrag
    flach = {k: v for k, v in eintrag.items() if k != "art"}
    if not flach:
        return eintrag
    unbekannt = set(flach) - set(_KONTAKT_NEU_FELDER) - {"telefone", "duplikat_bestaetigt"} - {
        k for k, _ in _KONTAKT_TELEFON_SCHLUESSEL
    }
    if unbekannt:
        raise ValueError(
            "Unbekannte Kontaktfelder: " + ", ".join(sorted(unbekannt))
            + ". Erlaubt: art, kontakt_id oder neu{anzeigename|vorname+nachname, organisation, funktion, email, "
            "telefone[{nummer,label}]} bzw. flach vorname, nachname, telefon, mobil, email."
        )
    daten = {k: flach[k] for k in _KONTAKT_NEU_FELDER if flach.get(k)}
    if not daten.get("anzeigename"):
        daten["anzeigename"] = " ".join(x for x in (daten.get("vorname"), daten.get("nachname")) if x)
    telefone = list(flach.get("telefone") or [])
    for schluessel, label in _KONTAKT_TELEFON_SCHLUESSEL:
        if flach.get(schluessel):
            telefone.append({"nummer": str(flach[schluessel]), "label": label})
    daten["telefone"] = telefone
    return {k: v for k, v in eintrag.items() if k in ("art", "erreichbarkeit")} | {"neu": daten}


def _kontakte_anlegen(
    context: MCPContext,
    objekt: Objekt,
    kontakte: list[dict[str, Any]],
    bestaetigt: bool,
    feldname: str,
) -> tuple[list[str], list[ObjektKontakt]]:
    hinweise = []
    zuordnungen = []
    for index, roh_eintrag in enumerate(kontakte, start=1):
        eintrag = _kontakt_eintrag_normalisieren(roh_eintrag)
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
            if not str(daten.get("anzeigename") or "").strip():
                daten = daten | {
                    "anzeigename": " ".join(x for x in (daten.get("vorname"), daten.get("nachname")) if x)
                }
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
                kandidaten = ", ".join(
                    f"{kandidat.id} {kandidat.anzeigename} ({kandidat.organisation or '-'})"
                    for kandidat in duplikate
                )
                raise ValueError(
                    f'Neuer Kontakt "{name}" ({feldname}[{index}]) ist moegliche Dublette von: {kandidaten}. '
                    "Mit duplikat_bestaetigt=true trotzdem anlegen oder vorhandene kontakt_id verwenden."
                )
            kontakt = kontakt_service.create_kontakt(
                context.db,
                {"typ": "person"} | {k: v for k, v in daten.items() if k in _KONTAKT_NEU_FELDER and v is not None},
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
            unbekannt = set(merkmal) - {"merkmal_id", "hinweis"}
            if unbekannt:
                raise ValueError("Unbekannte Merkmal-Felder: " + ", ".join(sorted(unbekannt)))
            merkmal_zuordnen(context.db, objekt, user_id=context.user.id, quelle="mcp", **merkmal)
        for adresse in zusatzadressen or []:
            if not isinstance(adresse, dict):
                raise ValueError("Jede Zusatzadresse muss ein Objekt sein.")
            zusatzadresse_anlegen(context.db, objekt, user_id=context.user.id, quelle="mcp", **adresse)
        hinweise, _ = _kontakte_anlegen(context, objekt, kontakte or [], duplikat_bestaetigt, "kontakte")
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


def _basis_objekt_auflosen(db: Any, org_id: int, objekt_id: int) -> Objekt | None:
    """Löst auch die ID einer Arbeitskopie auf ihr Basisobjekt auf."""
    objekt = db.query(Objekt).filter(Objekt.id == objekt_id, Objekt.org_id == org_id).first()
    if objekt is None:
        return None
    if objekt.entwurf_von_id is None:
        return objekt
    return (
        db.query(Objekt)
        .filter(Objekt.id == objekt.entwurf_von_id, Objekt.org_id == org_id, Objekt.entwurf_von_id.is_(None))
        .first()
    )


def _kontakt_zuordnung_id_auflosen(
    db: Any, objekt: Objekt, basis: Objekt, eintrag: int | dict[str, Any]
) -> int:
    """Nimmt auch eine Zuordnungs-ID aus dem Basisobjekt fuer dessen Arbeitskopie an."""
    ident = _entfern_id(eintrag, "Kontakt")
    if db.query(ObjektKontakt).filter(ObjektKontakt.id == ident, ObjektKontakt.objekt_id == objekt.id).first():
        return ident
    if objekt.entwurf_von_id is None:
        return ident
    basis_zuordnung = (
        db.query(ObjektKontakt)
        .filter(ObjektKontakt.id == ident, ObjektKontakt.objekt_id == basis.id)
        .first()
    )
    if basis_zuordnung is None:
        return ident
    treffer = (
        db.query(ObjektKontakt)
        .filter(
            ObjektKontakt.objekt_id == objekt.id,
            ObjektKontakt.kontakt_id == basis_zuordnung.kontakt_id,
            ObjektKontakt.art == basis_zuordnung.art,
        )
        .all()
    )
    if len(treffer) != 1:
        raise ValueError(
            "Kontakt-Zuordnung aus dem Basisobjekt ist in der Arbeitskopie nicht eindeutig vorhanden; "
            "bitte zuordnung_id aus objekt_lesen mit arbeitskopie=true verwenden."
        )
    return treffer[0].id


def _merkmal_zuordnung_id_auflosen(db: Any, objekt: Objekt, basis: Objekt, ident: int) -> int:
    """Nimmt auch eine Merkmal-ID aus dem Basisobjekt fuer dessen Arbeitskopie an."""
    if db.query(ObjektMerkmal).filter(ObjektMerkmal.id == ident, ObjektMerkmal.objekt_id == objekt.id).first():
        return ident
    if objekt.entwurf_von_id is None:
        return ident
    basis_zuordnung = (
        db.query(ObjektMerkmal).filter(ObjektMerkmal.id == ident, ObjektMerkmal.objekt_id == basis.id).first()
    )
    if basis_zuordnung is not None:
        treffer = (
            db.query(ObjektMerkmal)
            .filter(
                ObjektMerkmal.objekt_id == objekt.id,
                ObjektMerkmal.merkmal_id == basis_zuordnung.merkmal_id,
            )
            .all()
        )
        if len(treffer) == 1:
            return treffer[0].id
    raise ValueError(
        "Merkmal-Zuordnung aus dem Basisobjekt ist in der Arbeitskopie nicht eindeutig vorhanden; "
        "bitte id aus objekt_lesen mit arbeitskopie=true verwenden."
    )


def _gefahr_zuordnung_id_auflosen(db: Any, objekt: Objekt, basis: Objekt, ident: int) -> int:
    """Nimmt auch eine Gefahren-ID aus dem Basisobjekt fuer dessen Arbeitskopie an."""
    if db.query(ObjektGefahr).filter(ObjektGefahr.id == ident, ObjektGefahr.objekt_id == objekt.id).first():
        return ident
    if objekt.entwurf_von_id is None:
        return ident
    basis_gefahr = db.query(ObjektGefahr).filter(ObjektGefahr.id == ident, ObjektGefahr.objekt_id == basis.id).first()
    if basis_gefahr is not None:
        treffer = (
            db.query(ObjektGefahr)
            .filter(
                ObjektGefahr.objekt_id == objekt.id,
                ObjektGefahr.gefahr_id == basis_gefahr.gefahr_id,
                ObjektGefahr.sort == basis_gefahr.sort,
            )
            .all()
        )
        if len(treffer) == 1:
            return treffer[0].id
    raise ValueError(
        "Gefahren-Eintrag aus dem Basisobjekt ist in der Arbeitskopie nicht eindeutig vorhanden; "
        "bitte id aus objekt_lesen mit arbeitskopie=true verwenden."
    )


def _gefahr_aenderungsdaten(eintrag: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
    erlaubte = {"id", "un_nummer", "stoffname", "gefahrklasse", "gefahrnummer", "detail", "links"}
    unbekannt = set(eintrag) - erlaubte
    if unbekannt:
        raise ValueError("Unbekannte Gefahren-Aenderungsfelder: " + ", ".join(sorted(unbekannt)))
    ident = eintrag.get("id")
    if not isinstance(ident, int):
        raise ValueError("Jede Gefahren-Aenderung braucht eine ID.")
    daten: dict[str, Any] = {}
    berichtswerte: dict[str, Any] = {}
    for feld in ("un_nummer", "stoffname", "gefahrklasse", "gefahrnummer", "detail"):
        if feld not in eintrag:
            continue
        wert = eintrag[feld]
        if not isinstance(wert, str):
            raise ValueError(f"{feld} muss ein String sein.")
        daten[feld] = wert.strip() or None
        berichtswerte[feld] = daten[feld]
    if "links" in eintrag:
        links = eintrag["links"]
        if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
            raise ValueError("links muss eine Liste von Objekten sein.")
        for link in links:
            unbekannt_link = set(link) - {"label", "url"}
            if unbekannt_link:
                raise ValueError("Unbekannte Gefahren-Link-Felder: " + ", ".join(sorted(unbekannt_link)))
            if not isinstance(link.get("label", ""), str) or not isinstance(link.get("url", ""), str):
                raise ValueError("label und url muessen Strings sein.")
        daten["links_json"] = links_aus_form(
            [link.get("label", "") for link in links], [link.get("url", "") for link in links]
        )
        berichtswerte["links"] = links
    return ident, daten, berichtswerte


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
    gefahren_aendern: list[dict[str, Any]] | None = None,
    merkmale_hinzufuegen: list[dict[str, Any]] | None = None,
    merkmale_entfernen: list[int | dict[str, Any]] | None = None,
    merkmale_aendern: list[dict[str, Any]] | None = None,
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
    basis = _basis_objekt_auflosen(db, context.org_id, objekt_id)
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
            # Die Kindzeilen der Arbeitskopie werden mit expliziter objekt_id angelegt.
            # Fuer nachfolgende Aenderungen im selben MCP-Aufruf muessen sie bereits
            # abfragbar sein, damit Basis-IDs auf ihre Kopien aufgeloest werden koennen.
            db.flush()
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
        hinweise: list[str] = []
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
        for eintrag in gefahren_aendern or []:
            if not isinstance(eintrag, dict):
                raise ValueError("Jede Gefahren-Aenderung muss ein Objekt sein.")
            ident, daten, berichtswerte = _gefahr_aenderungsdaten(eintrag)
            ident = _gefahr_zuordnung_id_auflosen(db, objekt, basis, ident)
            vorher_eintrag = (
                db.query(ObjektGefahr).filter(ObjektGefahr.id == ident, ObjektGefahr.objekt_id == objekt.id).first()
            )
            if vorher_eintrag is None:
                raise ValueError("Gefahren-Eintrag nicht gefunden.")
            vorher = {
                feld: vorher_eintrag.links if feld == "links" else getattr(vorher_eintrag, feld)
                for feld in berichtswerte
            }
            geaenderte_gefahr = gefahr_aendern(
                db, objekt, ident, user_id=context.user.id, daten=daten, quelle="mcp"
            )
            for feld, alt in vorher.items():
                nachher_wert: Any = (
                    geaenderte_gefahr.links if feld == "links" else getattr(geaenderte_gefahr, feld)
                )
                if alt != nachher_wert:
                    geaenderte_felder.append(
                        {"feld": f"gefahren.{feld}", "vorher": alt, "nachher": nachher_wert}
                    )
        for merkmal in merkmale_hinzufuegen or []:
            if not isinstance(merkmal, dict) or not merkmal.get("merkmal_id"):
                raise ValueError("Jedes Merkmal braucht merkmal_id.")
            unbekannt = set(merkmal) - {"merkmal_id", "hinweis"}
            if unbekannt:
                raise ValueError("Unbekannte Merkmal-Felder: " + ", ".join(sorted(unbekannt)))
            vorhandenes_merkmal = (
                db.query(ObjektMerkmal)
                .filter(ObjektMerkmal.objekt_id == objekt.id, ObjektMerkmal.merkmal_id == merkmal["merkmal_id"])
                .first()
            )
            if vorhandenes_merkmal is not None:
                if "hinweis" in merkmal:
                    alt = vorhandenes_merkmal.hinweis
                    geaendertes_merkmal = merkmal_aendern(
                        db, objekt, vorhandenes_merkmal.id, user_id=context.user.id,
                        hinweis=merkmal["hinweis"], quelle="mcp"
                    )
                    if alt != geaendertes_merkmal.hinweis:
                        geaenderte_felder.append(
                            {"feld": "merkmale.hinweis", "vorher": alt, "nachher": geaendertes_merkmal.hinweis}
                        )
                else:
                    hinweise.append(f"Merkmal {merkmal['merkmal_id']} ist bereits zugeordnet.")
                continue
            neues_merkmal = merkmal_zuordnen(db, objekt, user_id=context.user.id, quelle="mcp", **merkmal)
            if neues_merkmal is not None:
                geaenderte_felder.append({"feld": "merkmale", "vorher": None, "nachher": neues_merkmal.id})
        for eintrag in merkmale_entfernen or []:
            ident = _entfern_id(eintrag, "Merkmal")
            merkmal_entfernen(db, objekt, ident, user_id=context.user.id, quelle="mcp")
            geaenderte_felder.append({"feld": "merkmale", "vorher": ident, "nachher": None})
        for eintrag in merkmale_aendern or []:
            if not isinstance(eintrag, dict):
                raise ValueError("Jede Merkmal-Aenderung muss ein Objekt sein.")
            unbekannt = set(eintrag) - {"id", "hinweis"}
            if unbekannt:
                raise ValueError("Unbekannte Merkmal-Aenderungsfelder: " + ", ".join(sorted(unbekannt)))
            if not isinstance(eintrag.get("id"), int):
                raise ValueError("Jede Merkmal-Aenderung braucht eine ID.")
            ident = _merkmal_zuordnung_id_auflosen(db, objekt, basis, eintrag["id"])
            vorher_merkmal = (
                db.query(ObjektMerkmal).filter(ObjektMerkmal.id == ident, ObjektMerkmal.objekt_id == objekt.id).first()
            )
            if vorher_merkmal is None:
                raise ValueError("Merkmal-Zuordnung nicht gefunden.")
            vorher_hinweis = vorher_merkmal.hinweis
            geaendertes_merkmal = merkmal_aendern(
                db, objekt, ident, user_id=context.user.id, hinweis=eintrag.get("hinweis"), quelle="mcp"
            )
            if vorher_hinweis != geaendertes_merkmal.hinweis:
                geaenderte_felder.append(
                    {
                        "feld": "merkmale.hinweis",
                        "vorher": vorher_hinweis,
                        "nachher": geaendertes_merkmal.hinweis,
                    }
                )
        for adresse in zusatzadressen_hinzufuegen or []:
            if not isinstance(adresse, dict):
                raise ValueError("Jede Zusatzadresse muss ein Objekt sein.")
            neue_adresse = zusatzadresse_anlegen(db, objekt, user_id=context.user.id, quelle="mcp", **adresse)
            geaenderte_felder.append({"feld": "zusatzadressen", "vorher": None, "nachher": neue_adresse.id})
        for eintrag in zusatzadressen_entfernen or []:
            ident = _entfern_id(eintrag, "Zusatzadress")
            zusatzadresse_entfernen(db, objekt, ident, user_id=context.user.id, quelle="mcp")
            geaenderte_felder.append({"feld": "zusatzadressen", "vorher": ident, "nachher": None})
        kontakt_hinweise, neue_zuordnungen = _kontakte_anlegen(
            context, objekt, kontakte_hinzufuegen or [], duplikat_bestaetigt, "kontakte_hinzufuegen"
        )
        hinweise.extend(kontakt_hinweise)
        for zuordnung in neue_zuordnungen:
            geaenderte_felder.append({"feld": "kontakte", "vorher": None, "nachher": zuordnung.id})
        for eintrag in kontakte_aendern or []:
            if not isinstance(eintrag, dict) or not isinstance(eintrag.get("zuordnung_id"), int):
                raise ValueError("Jede Kontakt-Aenderung braucht zuordnung_id.")
            erlaubte = {"zuordnung_id", "art", "sort", "erreichbarkeit"}
            if set(eintrag) - erlaubte:
                raise ValueError("Unbekannte Kontakt-Aenderungsfelder: " + ", ".join(sorted(set(eintrag) - erlaubte)))
            geaenderte_kontaktzuordnung = kontakt_zuordnung_aendern(
                db, objekt, eintrag["zuordnung_id"], art=eintrag.get("art"), sort=eintrag.get("sort"),
                erreichbarkeit=eintrag.get("erreichbarkeit"), user_id=context.user.id, quelle="mcp",
            )
            geaenderte_felder.append(
                {
                    "feld": "kontakte",
                    "vorher": geaenderte_kontaktzuordnung.id,
                    "nachher": geaenderte_kontaktzuordnung.id,
                }
            )
        for eintrag in kontakte_entfernen or []:
            if isinstance(eintrag, dict) and isinstance(eintrag.get("kontakt_id"), int) and "id" not in eintrag:
                treffer = [
                    k for k in objekt.kontakte
                    if k.kontakt_id == eintrag["kontakt_id"] and (not eintrag.get("art") or k.art == eintrag["art"])
                ]
                if len(treffer) != 1:
                    raise ValueError(
                        "Kontakt ist diesem Objekt nicht (eindeutig) zugeordnet; bitte zuordnung_id aus objekt_lesen "
                        "verwenden (bei Arbeitskopie mit arbeitskopie=true)."
                    )
                ident = treffer[0].id
            else:
                ident = _kontakt_zuordnung_id_auflosen(db, objekt, basis, eintrag)
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
