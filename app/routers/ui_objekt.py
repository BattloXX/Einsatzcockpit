"""Objektverwaltung Router (PR 1: Grundmodul).

Alle Routen brauchen require_objekt_enabled (HTTP 404 wenn Modul inaktiv).
Prefix: /objekte

Rollen:
- Lesen: alle angemeldeten Nutzer der Org (Entwuerfe nur objekt_verwalter+)
- Schreiben: objekt_verwalter (org_admin/system_admin implizit)
- Kataloge/Loeschen: org_admin
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, selectinload

from app.core.audit import write_audit
from app.core.permissions import is_objekt_verwalter, require_role
from app.core.templating import templates
from app.db import get_db
from app.models.objekt import (
    AUSWAHL_DOKUMENTART,
    AUSWAHL_KONTAKTART,
    AUSWAHL_PIKTOGRAMM,
    OBJEKT_STATUS_ENTWURF,
    OBJEKT_STATUS_FREIGEGEBEN,
    OBJEKT_STATUS_LABELS,
    OBJEKT_STATUS_UEBERARBEITUNG,
    SYMBOL_STILE,
    GefahrenKatalog,
    MerkmalKatalog,
    Objekt,
    ObjektAuswahl,
    ObjektBMA,
    ObjektChange,
    ObjektDokumentSeite,
    ObjektGefahr,
    ObjektKartenObjekt,
    ObjektKategorie,
    ObjektKontakt,
    ObjektMerkmal,
    ObjektSymbol,
    ObjektWohnanlage,
    ObjektZusatzadresse,
)
from app.models.user import User
from app.services.objekt_service import (
    aktualisiere_felder,
    berechne_vollstaendigkeit,
    erstelle_arbeitskopie,
    fehlende_kartensymbole,
    gefahr_links,
    hole_arbeitskopie,
    lade_auswahl,
    naechste_nummer,
    nur_produktiv,
    status_uebergang_erlaubt,
    telefone_aus_form,
    uebernimm_arbeitskopie,
    verwirf_arbeitskopie,
    write_objekt_change,
)

# Auswahl-Typen, die in der Verwaltung pflegbar sind (Reihenfolge = Tab-Reihenfolge)
_AUSWAHL_TYPEN = (AUSWAHL_KONTAKTART, AUSWAHL_DOKUMENTART, AUSWAHL_PIKTOGRAMM)
_AUSWAHL_LABELS = {
    AUSWAHL_KONTAKTART: "Kontaktarten",
    AUSWAHL_DOKUMENTART: "Dokumentarten",
    AUSWAHL_PIKTOGRAMM: "Gefahren-Piktogramme",
}

router = APIRouter(prefix="/objekte", tags=["objekt"])

# Alle Rollen der Org duerfen lesen (require_role laesst admin/org_admin immer durch)
_LESE_ROLLEN = (
    "readonly", "recorder", "breathing_supervisor", "incident_leader",
    "fahrtenbuch_admin", "objekt_verwalter",
)


# ── Guard ──────────────────────────────────────────────────────────────────────

def require_objekt_enabled(request: Request) -> None:
    """Guard-Dependency: HTTP 404 wenn Objekt-Modul nicht effektiv aktiv (System+Org)."""
    if not getattr(request.state, "objekt_enabled", False):
        raise HTTPException(status_code=404, detail="Nicht gefunden")


def _objekt_or_404(db: Session, objekt_id: int, user: User) -> Objekt:
    """Laedt ein Objekt (Tenant-Filter greift automatisch); Entwuerfe nur fuer Verwalter."""
    objekt = (
        db.query(Objekt)
        .options(
            selectinload(Objekt.bma),
            selectinload(Objekt.zusatzadressen),
            selectinload(Objekt.gefahren),
            selectinload(Objekt.merkmale),
            selectinload(Objekt.kontakte),
            selectinload(Objekt.wohnanlage),
        )
        .filter(Objekt.id == objekt_id)
        .first()
    )
    if objekt is None:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden")
    if objekt.status == OBJEKT_STATUS_ENTWURF and not is_objekt_verwalter(user):
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden")
    return objekt


def _objekt_arbeitsstand(db: Session, objekt_id: int, user: User, request: Request) -> Objekt:
    """Wie _objekt_or_404, liefert aber fuer objekt_verwalter transparent die offene
    Arbeitskopie statt des produktiven Objekts, solange eine existiert - die einzige Stelle,
    an der Bearbeitungsrouten (Stammdaten/BMA/Gefahren/Merkmale/Kontakte/Wohnanlage/Karte)
    zwischen "produktivem Objekt" und "Arbeitskopie" unterscheiden muessen. Alle Kindzeilen
    werden ueber objekt.id angelegt/gesucht - Schreibrouten wirken dadurch automatisch auf
    die Kopie, ohne selbst etwas ueber Arbeitskopien zu wissen.

    URL bleibt immer /objekte/{produktive_id}/... - die Kopie hat keine eigene, nach aussen
    sichtbare URL. `?fassung=produktiv` erzwingt (fuer Verwalter) die produktive Ansicht,
    z. B. zum Vergleichen waehrend eine Ueberarbeitung laeuft.
    """
    basis = _objekt_or_404(db, objekt_id, user)
    if not is_objekt_verwalter(user):
        return basis
    if request.query_params.get("fassung") == "produktiv":
        return basis
    kopie = hole_arbeitskopie(db, basis)
    return kopie if kopie is not None else basis


def _kategorien(db: Session, nur_aktive: bool = True) -> list[ObjektKategorie]:
    q = db.query(ObjektKategorie)
    if nur_aktive:
        q = q.filter(ObjektKategorie.aktiv.is_(True))
    return q.order_by(ObjektKategorie.sort, ObjektKategorie.name).all()


async def _geocode_objekt(objekt_id: int, strasse: str | None, hausnummer: str | None, ort: str | None) -> None:
    """Background: Geocodiert Objektadresse (Muster _geocode_incident, api_v1.py)."""
    from app.core.tenant import set_tenant_context
    from app.db import SessionLocal
    from app.services.geocoding import geocode_address

    if not (strasse or ort):
        return
    try:
        geo = await geocode_address(strasse, hausnummer, ort)
    except Exception:
        import logging as _logging
        _logging.getLogger("einsatzleiter.geocoding").exception(
            "Background-Geocoding fuer Objekt %d fehlgeschlagen", objekt_id
        )
        return
    if not geo:
        return

    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        objekt = db.get(Objekt, objekt_id)
        if objekt and objekt.lat is None and objekt.lng is None:
            objekt.lat = geo.lat
            objekt.lng = geo.lng
            db.commit()
    except Exception:
        import logging as _logging
        _logging.getLogger("einsatzleiter.geocoding").exception(
            "Background-Geocoding DB-Speicherung fuer Objekt %d fehlgeschlagen", objekt_id
        )
    finally:
        db.close()


# ── Objektliste ────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def objekt_liste(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    q: str = "",
    status: str = "",
    kategorie: str = "",
    revision: str = "",
    merkmal: str = "",
):
    from sqlalchemy import ColumnElement, or_

    # <select>-Formular sendet bei "Alle" ein leeres value="" statt den Parameter
    # wegzulassen - int|None wuerde das nicht als None behandeln, sondern einen
    # 422 werfen (Vorfall: Filtern in der Objektverwaltung schlug fehl).
    kategorie_id = int(kategorie) if kategorie.strip().isdigit() else None
    merkmal_id = int(merkmal) if merkmal.strip().isdigit() else None

    query = nur_produktiv(
        db.query(Objekt)
        .options(
            selectinload(Objekt.bma),
            selectinload(Objekt.kategorie),
            selectinload(Objekt.merkmale),
            selectinload(Objekt.kontakte),
            selectinload(Objekt.gefahren),
        )
    )
    # Entwuerfe sieht nur objekt_verwalter+
    verwalter = is_objekt_verwalter(user)
    if not verwalter:
        query = query.filter(Objekt.status != OBJEKT_STATUS_ENTWURF)
    if q.strip():
        term = f"%{q.strip()}%"
        filters: list[ColumnElement[bool]] = [
            Objekt.name.like(term),
            Objekt.vulgoname.like(term),
            Objekt.strasse.like(term),
            Objekt.ort.like(term),
        ]
        if q.strip().isdigit():
            filters.append(Objekt.nummer == int(q.strip()))
        query = query.filter(or_(*filters))
    if status:
        query = query.filter(Objekt.status == status)
    if kategorie_id:
        query = query.filter(Objekt.kategorie_id == kategorie_id)
    if revision == "faellig":
        query = query.filter(Objekt.revision_datum.isnot(None), Objekt.revision_datum <= date.today())
    if merkmal_id:
        query = query.filter(
            Objekt.id.in_(
                db.query(ObjektMerkmal.objekt_id).filter(ObjektMerkmal.merkmal_id == merkmal_id)
            )
        )

    objekte = query.order_by(Objekt.nummer).all()

    rows = [
        {
            "objekt": o,
            "vollstaendigkeit": berechne_vollstaendigkeit(
                o, kontakt_count=len(o.kontakte), gefahren_count=len(o.gefahren)
            ),
        }
        for o in objekte
    ]

    merkmal_katalog = (
        db.query(MerkmalKatalog)
        .filter(MerkmalKatalog.aktiv.is_(True))
        .order_by(MerkmalKatalog.sort, MerkmalKatalog.name)
        .all()
    )

    return templates.TemplateResponse(request, "objekt/liste.html", {
        "user": user,
        "rows": rows,
        "kategorien": _kategorien(db),
        "merkmal_katalog": merkmal_katalog,
        "status_labels": OBJEKT_STATUS_LABELS,
        "filter_q": q,
        "filter_status": status,
        "filter_kategorie": kategorie_id,
        "filter_revision": revision,
        "filter_merkmal": merkmal_id,
        "ist_verwalter": verwalter,
        "heute": date.today(),
    })


# ── Neues Objekt ───────────────────────────────────────────────────────────────

@router.get("/neu", response_class=HTMLResponse)
def objekt_neu_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    return templates.TemplateResponse(request, "objekt/formular.html", {
        "user": user,
        "kategorien": _kategorien(db),
    })


@router.post("/neu")
def objekt_neu(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    vulgoname: str = Form(""),
    kategorie_id: str = Form(""),
    strasse: str = Form(""),
    hausnummer: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    lat: str = Form(""),
    lng: str = Form(""),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")

    # Koordinaten aus der OSM-Adressvalidierung (falls der Nutzer einen Treffer
    # uebernommen hat) — dann kein Hintergrund-Geocoding noetig.
    validiert_lat = float(lat) if lat.strip() else None
    validiert_lng = float(lng) if lng.strip() else None

    objekt = Objekt(
        org_id=user.org_id,
        nummer=naechste_nummer(db, user.org_id),  # type: ignore[arg-type]
        name=name.strip(),
        vulgoname=vulgoname.strip() or None,
        kategorie_id=int(kategorie_id) if kategorie_id.strip() else None,
        strasse=strasse.strip() or None,
        hausnummer=hausnummer.strip() or None,
        plz=plz.strip() or None,
        ort=ort.strip() or None,
        lat=validiert_lat,
        lng=validiert_lng,
        status=OBJEKT_STATUS_ENTWURF,
        erstellt_von_id=user.id,
        aktualisiert_von_id=user.id,
    )
    db.add(objekt)
    db.flush()
    write_objekt_change(db, objekt.id, user.org_id, "stammdaten", "angelegt",
                        before=None, after=objekt.name, user_id=user.id)
    write_audit(db, "objekt.created", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id,
                payload={"name": objekt.name, "nummer": objekt.nummer})
    db.commit()

    # Nur geocoden, wenn keine validierten Koordinaten uebernommen wurden.
    if (strasse.strip() or ort.strip()) and objekt.lat is None:
        background_tasks.add_task(
            _geocode_objekt, objekt.id, objekt.strasse, objekt.hausnummer, objekt.ort
        )

    return RedirectResponse(url=f"/objekte/{objekt.id}", status_code=303)


# ── OSM-Adresssuche (interaktive Validierung bei der Objekt-Anlage) ─────────────
# WICHTIG: vor den /{objekt_id}-Routen registriert (statischer Pfad).

@router.get("/adress-suche")
async def objekt_adress_suche(
    request: Request,
    q: str = "",
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    """Liefert OSM/Nominatim-Adresskandidaten als JSON fuer die Objekt-Anlage."""
    from app.services.geocoding import search_addresses

    return {"treffer": await search_addresses(q, limit=6)}


@router.get("/gefahrgut/lookup")
def gefahrgut_lookup(
    request: Request,
    un: str = "",
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    """Anreicherung einer Gefahr per UN-Nummer aus der offenen Gefahrgut-DB (BAM)."""
    from app.services.gefahrgut_service import generierte_links, lookup_un

    treffer = lookup_un(un)
    return {
        "gefunden": treffer is not None,
        "stoffname": (treffer or {}).get("stoffname"),
        "gefahrklasse": (treffer or {}).get("klasse"),
        "gefahrnummer": (treffer or {}).get("gefahrnummer"),
        "klassifizierungscode": (treffer or {}).get("klassifizierungscode"),
        "verpackungsgruppe": (treffer or {}).get("verpackungsgruppe"),
        "links": generierte_links(
            un, (treffer or {}).get("stoffname"), (treffer or {}).get("gefahrnummer")
        ),
    }


# ── Katalog-Admin: Kategorien (org_admin) ──────────────────────────────────────
# WICHTIG: vor den /{objekt_id}-Routen registriert, sonst faengt der
# int-Pfadparameter "kataloge" ab (422 statt Katalogseite).

@router.get("/kataloge", response_class=HTMLResponse)
def kataloge(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    from sqlalchemy import func
    verwendung: dict[int, int] = {
        kid: cnt
        for kid, cnt in (
            db.query(Objekt.kategorie_id, func.count(Objekt.id))
            .filter(Objekt.kategorie_id.isnot(None))
            .group_by(Objekt.kategorie_id)
            .all()
        )
        if kid is not None
    }
    gefahren_verwendung: dict[int, int] = {
        gid: cnt
        for gid, cnt in db.query(ObjektGefahr.gefahr_id, func.count(ObjektGefahr.id))
        .group_by(ObjektGefahr.gefahr_id)
        .all()
    }
    merkmal_verwendung: dict[int, int] = {
        mid: cnt
        for mid, cnt in db.query(ObjektMerkmal.merkmal_id, func.count(ObjektMerkmal.id))
        .group_by(ObjektMerkmal.merkmal_id)
        .all()
    }

    # Pflegbare Auswahllisten (Kontaktarten/Dokumentarten/Piktogramme) je Typ,
    # inkl. Verwendungszaehlern (Referenz per String-Code, nicht FK).
    auswahl: dict[str, list[ObjektAuswahl]] = {typ: [] for typ in _AUSWAHL_TYPEN}
    for eintrag in (
        db.query(ObjektAuswahl)
        .order_by(ObjektAuswahl.typ, ObjektAuswahl.sort, ObjektAuswahl.name)
        .all()
    ):
        auswahl.setdefault(eintrag.typ, []).append(eintrag)
    auswahl_verwendung: dict[str, dict[str, int]] = {
        AUSWAHL_KONTAKTART: {
            code: cnt for code, cnt in
            db.query(ObjektKontakt.art, func.count(ObjektKontakt.id))
            .group_by(ObjektKontakt.art).all()
        },
        AUSWAHL_DOKUMENTART: {
            code: cnt for code, cnt in
            db.query(ObjektDokumentSeite.dokumentart, func.count(ObjektDokumentSeite.id))
            .filter(ObjektDokumentSeite.dokumentart.isnot(None))
            .group_by(ObjektDokumentSeite.dokumentart).all()
        },
        AUSWAHL_PIKTOGRAMM: {
            code: cnt for code, cnt in
            db.query(GefahrenKatalog.piktogramm_typ, func.count(GefahrenKatalog.id))
            .group_by(GefahrenKatalog.piktogramm_typ).all()
        },
    }

    return templates.TemplateResponse(request, "objekt/kataloge.html", {
        "user": user,
        "kategorien": _kategorien(db, nur_aktive=False),
        "verwendung": verwendung,
        "gefahren": (
            db.query(GefahrenKatalog)
            .order_by(GefahrenKatalog.sort, GefahrenKatalog.name)
            .all()
        ),
        "gefahren_verwendung": gefahren_verwendung,
        "gefahr_piktogramme": lade_auswahl(db, user.org_id, AUSWAHL_PIKTOGRAMM),
        "merkmale": (
            db.query(MerkmalKatalog)
            .order_by(MerkmalKatalog.sort, MerkmalKatalog.name)
            .all()
        ),
        "merkmal_verwendung": merkmal_verwendung,
        "auswahl": auswahl,
        "auswahl_verwendung": auswahl_verwendung,
        "auswahl_typen": _AUSWAHL_TYPEN,
        "auswahl_labels": _AUSWAHL_LABELS,
        "symbole": (
            db.query(ObjektSymbol)
            .order_by(ObjektSymbol.sort, ObjektSymbol.name)
            .all()
        ),
        "symbol_verwendung": {
            typ: cnt for typ, cnt in
            db.query(ObjektKartenObjekt.typ, func.count(ObjektKartenObjekt.id))
            .group_by(ObjektKartenObjekt.typ).all()
        },
        "symbol_stile": SYMBOL_STILE,
        "aktiver_tab": request.query_params.get("tab", "kategorien"),
    })


@router.get("/karten-symbole.json")
def karten_symbole_json(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    """Org-Symbolkatalog fuer das client-seitige Rendering (objekt_karte.js)."""
    from app.services.objekt_symbol_service import symbol_katalog_json
    return {"symbole": symbol_katalog_json(db, user.org_id)}


@router.post("/kataloge/kategorien/neu")
def kategorie_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    sort: int = Form(0),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    existiert = db.query(ObjektKategorie).filter(ObjektKategorie.name == name.strip()).first()
    if existiert:
        return RedirectResponse(url="/objekte/kataloge?error=exists", status_code=303)
    db.add(ObjektKategorie(org_id=user.org_id, name=name.strip(), sort=sort, aktiv=True))
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1", status_code=303)


@router.post("/kataloge/kategorien/{kategorie_id}/edit")
def kategorie_edit(
    kategorie_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    sort: int = Form(0),
    aktiv: str = Form(""),
):
    kat = db.query(ObjektKategorie).filter(ObjektKategorie.id == kategorie_id).first()
    if kat is None:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    kat.name = name.strip()
    kat.sort = sort
    kat.aktiv = bool(aktiv)
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1", status_code=303)


@router.post("/kataloge/kategorien/{kategorie_id}/loeschen")
def kategorie_loeschen(
    kategorie_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    kat = db.query(ObjektKategorie).filter(ObjektKategorie.id == kategorie_id).first()
    if kat is None:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    verwendet = nur_produktiv(db.query(Objekt)).filter(Objekt.kategorie_id == kat.id).first()
    if verwendet:
        return RedirectResponse(url="/objekte/kataloge?error=in_use", status_code=303)
    db.delete(kat)
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1", status_code=303)


# ── Objekt-Detail ──────────────────────────────────────────────────────────────

def _detail_context(request: Request, db: Session, user: User, objekt: Objekt) -> dict:
    from sqlalchemy import func as _func

    from app.models.objekt import ObjektDokumentSeite

    # Dokumente haengen IMMER an der produktiven Zeile (out of scope fuer die
    # Arbeitskopie-Versionierung, siehe objekt.py) - bei einer Arbeitskopie (objekt.id
    # ist dann die Kopie) muss ueber entwurf_von_id auf die Basis-id gezaehlt werden,
    # sonst zeigt der Dokumente-Tab faelschlich 0 Dokumente waehrend der Ueberarbeitung.
    produktiv_objekt_id = objekt.entwurf_von_id or objekt.id
    dokument_count = (
        db.query(_func.count(ObjektDokumentSeite.id))
        .filter(ObjektDokumentSeite.objekt_id == produktiv_objekt_id)
        .scalar()
    ) or 0
    arbeitskopie = None
    if objekt.entwurf_von_id is None and objekt.status == OBJEKT_STATUS_UEBERARBEITUNG:
        arbeitskopie = hole_arbeitskopie(db, objekt)

    from app.models.bma_import import BmaImportSatz
    bma_import_satz = (
        db.query(BmaImportSatz).filter(BmaImportSatz.objekt_id == produktiv_objekt_id).first()
    )

    return {
        "user": user,
        "objekt": objekt,
        "produktiv_objekt_id": produktiv_objekt_id,
        "arbeitskopie": arbeitskopie,
        "bma_import_satz": bma_import_satz,
        "kategorien": _kategorien(db),
        "status_labels": OBJEKT_STATUS_LABELS,
        "gefahr_piktogramme": lade_auswahl(db, objekt.org_id, AUSWAHL_PIKTOGRAMM),
        "kontakt_arten": lade_auswahl(db, objekt.org_id, AUSWAHL_KONTAKTART),
        "gefahr_links": gefahr_links,
        "dokument_count": dokument_count,
        "vollstaendigkeit": berechne_vollstaendigkeit(
            objekt,
            kontakt_count=len(objekt.kontakte),
            gefahren_count=len(objekt.gefahren),
            dokument_count=dokument_count,
        ),
        "fehlende_kartensymbole": fehlende_kartensymbole(objekt),
        "ist_verwalter": is_objekt_verwalter(user),
    }


# ── Brandschutzplan-Upload ohne Objekt-Vorauswahl ─────────────────────────────
# Die KI liest Name/Adresse/BMA-Nummer aus dem Dokument, BEVOR es gespeichert
# wird, und loest damit das Ziel-Objekt auf (bestehendes ergaenzen oder neues
# anlegen) - siehe app/services/objekt_plan_upload_service.py fuer die
# Begruendung des "Identify-first"-Ansatzes.
#
# WICHTIG: muss VOR "/{objekt_id}" registriert sein. "/{objekt_id}" hat KEINEN
# expliziten :int-Pfad-Konverter im Routen-String - Starlette matcht den Pfad
# rein stringbasiert (jedes Segment passt), die int-Typpruefung von objekt_id
# passiert erst danach bei der Parametervalidierung. Ohne diese Reihenfolge
# wuerde "/objekte/dokument-upload" faelschlich von "/{objekt_id}" abgefangen
# und mit 422 (ungueltiger Integer "dokument-upload") abgelehnt, statt hier
# anzukommen.

@router.get("/dokument-upload", response_class=HTMLResponse)
def dokument_upload_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.objekt_ki_service import ki_klassifikation_enabled
    return templates.TemplateResponse(request, "objekt/dokument_upload.html", {
        "user": user,
        "ki_enabled": ki_klassifikation_enabled(user.org_id, db),
    })


@router.post("/dokument-upload", response_class=HTMLResponse)
async def dokumente_upload_verarbeiten(
    request: Request, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled), dateien: list[UploadFile] = File(...),
):
    from app.config import settings
    from app.services.bma_import.bma_pdf_parser import ist_bma_datenblatt, parse_datenblatt_pdf
    from app.services.bma_import.bma_sync import hole_oder_erstelle_config, verarbeite_pdf_anlage
    from app.services.objekt_dokument_service import _detect_mime, store_dokument_upload, verarbeite_dokument
    from app.services.objekt_ki_service import analysiere_unklassifizierte_seiten, ki_klassifikation_enabled
    from app.services.objekt_plan_upload_service import (
        erstelle_objekt_aus_identitaet,
        finde_passendes_objekt,
        identifiziere_objekt,
    )

    org_id = user.org_id
    if org_id is None:
        raise HTTPException(status_code=400, detail="Benutzer ist keiner Organisation zugeordnet")
    ki_enabled = bool(org_id and ki_klassifikation_enabled(org_id, db))
    config = hole_oder_erstelle_config(db, org_id)
    ergebnisse, nacharbeiten = [], []
    bma_queue_relevant = False
    for datei in dateien:
        name = datei.filename or "dokument.pdf"
        data = await datei.read()
        if not data or len(data) > settings.OBJEKT_PDF_MAX_BYTES or _detect_mime(data) != "application/pdf":
            meldung = (
                "Leere Datei" if not data
                else "Datei ist zu groß" if len(data) > settings.OBJEKT_PDF_MAX_BYTES
                else "Nur PDF-Dateien sind erlaubt"
            )
            ergebnisse.append({"dateiname": name, "ok": False, "meldung": meldung})
            continue
        datenblatt = ist_bma_datenblatt(data)
        try:
            parsed = parse_datenblatt_pdf(data) if datenblatt else None
            if not datenblatt and not ki_enabled:
                ergebnisse.append({
                    "dateiname": name,
                    "ok": False,
                    "meldung": "Für diese Datei wird die KI-Klassifikation benötigt.",
                })
                continue
            identitaet = None if datenblatt else await identifiziere_objekt(data, name, org_id)
            await datei.seek(0)
            with db.begin_nested():
                neu = False
                quelle: str | None
                if datenblatt:
                    if parsed is None:
                        raise ValueError("BMA-Datenblatt konnte nicht gelesen werden")
                    satz, status = verarbeite_pdf_anlage(db, org_id, config, parsed["anlage"], parsed["kontakte"], user)
                    bma_queue_relevant = bma_queue_relevant or status in ("vorschlag", "offen")
                    objekt = db.get(Objekt, satz.objekt_id) if satz.objekt_id else None
                    if objekt is None:
                        # Reguläres Verlassen committet absichtlich den offenen Importsatz.
                        ergebnisse.append({
                            "dateiname": name,
                            "ok": True,
                            "meldung": "Manuelle Zuordnung in der BMA-Queue erforderlich",
                        })
                        continue
                    quelle = "bma_datenblatt"
                else:
                    if identitaet is None:
                        raise ValueError("Objektidentität konnte nicht ermittelt werden")
                    objekt = finde_passendes_objekt(db, org_id, identitaet)
                    neu = objekt is None
                    if objekt is None:
                        objekt = erstelle_objekt_aus_identitaet(db, user, identitaet)
                    status = "neu" if neu else "ergaenzt"
                    quelle = identitaet.get("quelle")
                dokument = await store_dokument_upload(datei, objekt, user, db)
                objekt_id, dokument_id = objekt.id, dokument.id
                write_objekt_change(db, objekt_id, objekt.org_id, "dokumente", "dokument_upload",
                                    before=None, after="1 Datei (Upload ohne Objekt-Auswahl)", user_id=user.id)
                write_audit(db, "objekt.dokument_uploaded", org_id=org_id, user_id=user.id,
                            entity_type="objekt", entity_id=objekt_id,
                            payload={
                                "anzahl": 1,
                                "neu_erstellt": neu,
                                "quelle": quelle,
                            })
                nacharbeiten.append((dokument_id, objekt_id, ki_enabled and not datenblatt,
                                     objekt.strasse if neu else None,
                                     objekt.hausnummer if neu else None,
                                     objekt.ort if neu else None))
                ergebnisse.append({"dateiname": name, "ok": True, "meldung": status,
                                   "objekt_id": objekt_id, "dokument_id": dokument_id})
        except (HTTPException, ValueError) as exc:
            ergebnisse.append({"dateiname": name, "ok": False,
                               "meldung": str(exc.detail if isinstance(exc, HTTPException) else exc)})
    db.commit()
    for dokument_id, objekt_id, analyse, strasse, hausnummer, ort in nacharbeiten:
        background_tasks.add_task(verarbeite_dokument, dokument_id)
        if analyse:
            background_tasks.add_task(analysiere_unklassifizierte_seiten, objekt_id)
        if strasse or ort:
            background_tasks.add_task(_geocode_objekt, objekt_id, strasse, hausnummer, ort)
    return templates.TemplateResponse(request, "objekt/dokument_upload.html", {
        "user": user, "ki_enabled": ki_enabled, "ergebnisse": ergebnisse,
        "bma_queue_relevant": bma_queue_relevant,
    })


# ── Globales Aenderungsprotokoll (alle Objekte) ─────────────────────────────
# WICHTIG: muss VOR "/{objekt_id}" registriert sein (siehe Begruendung beim
# Brandschutzplan-Upload oben) - sonst wuerde "/objekte/changelog" faelschlich
# von "/{objekt_id}" abgefangen und mit 422 (ungueltiger Integer "changelog") abgelehnt.

@router.get("/changelog", response_class=HTMLResponse)
def objekt_changelog(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    """Uebergreifendes Aenderungsprotokoll ueber ALLE Objekte der Org - Gegenstueck
    zu protokoll_partial() (nur ein einzelnes Objekt). Nuetzlich z.B. um nach einem
    BMA-Datenblatt-Mehrfach-Upload (mehrere Objekte auf einmal) auf einen Blick zu
    sehen, was sich ueberall veraendert hat, ohne jedes Objekt einzeln zu oeffnen."""
    changes = (
        db.query(ObjektChange)
        .order_by(ObjektChange.erstellt_am.desc(), ObjektChange.id.desc())
        .limit(200)
        .all()
    )
    objekt_ids = {c.objekt_id for c in changes}
    user_ids = {c.user_id for c in changes if c.user_id}
    objekte: dict[int, Objekt] = {}
    if objekt_ids:
        for o in db.query(Objekt).filter(Objekt.id.in_(objekt_ids)).all():
            objekte[o.id] = o
    benutzer: dict[int, User] = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            benutzer[u.id] = u
    return templates.TemplateResponse(request, "objekt/changelog.html", {
        "user": user,
        "changes": changes,
        "objekte": objekte,
        "benutzer": benutzer,
    })


@router.get("/{objekt_id}", response_class=HTMLResponse)
def objekt_detail(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    plan: str = "",
):
    # _objekt_arbeitsstand: objekt_id ist die produktive id; existiert eine offene
    # Arbeitskopie, wird sie fuer objekt_verwalter transparent statt der Basis geladen -
    # alle hx-get/hx-post-URLs in detail.html werden aus dem zurueckgegebenen objekt.id
    # gebaut, wirken dadurch automatisch auf die Kopie, ohne dass die einzelnen
    # Abschnitts-Routen selbst etwas von Arbeitskopien wissen muessen. ?fassung=produktiv
    # erzwingt die produktive Ansicht (Vergleich waehrend einer laufenden Ueberarbeitung).
    objekt = _objekt_arbeitsstand(db, objekt_id, user, request)
    ctx = _detail_context(request, db, user, objekt)
    # Banner nach Redirect vom objektlosen Brandschutzplan-Upload
    # (ui_objekt_dokumente.py::dokument_upload_verarbeiten): "neu" | "ergaenzt".
    ctx["plan_hinweis"] = plan if plan in ("neu", "ergaenzt") else ""
    return templates.TemplateResponse(request, "objekt/detail.html", ctx)


# ── Abschnitt: Stammdaten (HTMX-Inline-Edit) ──────────────────────────────────

@router.get("/{objekt_id}/stammdaten", response_class=HTMLResponse)
def stammdaten_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_stammdaten.html", _detail_context(request, db, user, objekt)
    )


@router.get("/{objekt_id}/stammdaten/bearbeiten", response_class=HTMLResponse)
def stammdaten_form(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_stammdaten_form.html", _detail_context(request, db, user, objekt)
    )


@router.post("/{objekt_id}/stammdaten", response_class=HTMLResponse)
def stammdaten_speichern(
    objekt_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    vulgoname: str = Form(""),
    kategorie_id: str = Form(""),
    strasse: str = Form(""),
    hausnummer: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    lat: str = Form(""),
    lng: str = Form(""),
    informationen: str = Form(""),
    anfahrtsweg: str = Form(""),
    revision_datum: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")

    adresse_vorher = (objekt.strasse, objekt.hausnummer, objekt.ort)
    daten = {
        "name": name.strip(),
        "vulgoname": vulgoname.strip() or None,
        "kategorie_id": int(kategorie_id) if kategorie_id.strip() else None,
        "strasse": strasse.strip() or None,
        "hausnummer": hausnummer.strip() or None,
        "plz": plz.strip() or None,
        "ort": ort.strip() or None,
        "lat": float(lat) if lat.strip() else None,
        "lng": float(lng) if lng.strip() else None,
        "informationen": informationen.strip() or None,
        "anfahrtsweg": anfahrtsweg.strip() or None,
        "revision_datum": datetime.strptime(revision_datum, "%Y-%m-%d").date() if revision_datum.strip() else None,
    }
    # Neues Revisionsdatum → Erinnerungs-Marker zuruecksetzen (erneute Erinnerung bei Faelligkeit)
    if daten["revision_datum"] != objekt.revision_datum:
        daten["revision_erinnert_am"] = None
    aktualisiere_felder(db, objekt, daten, bereich="stammdaten", user_id=user.id)
    db.commit()

    # Adresse geaendert und keine manuellen Koordinaten → neu geocodieren
    if (objekt.strasse, objekt.hausnummer, objekt.ort) != adresse_vorher and objekt.lat is None:
        background_tasks.add_task(
            _geocode_objekt, objekt.id, objekt.strasse, objekt.hausnummer, objekt.ort
        )

    return templates.TemplateResponse(
        request, "objekt/_stammdaten.html", _detail_context(request, db, user, objekt)
    )


# ── Abschnitt: BMA & Schluessel ────────────────────────────────────────────────

@router.get("/{objekt_id}/bma", response_class=HTMLResponse)
def bma_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_bma.html", _detail_context(request, db, user, objekt)
    )


@router.get("/{objekt_id}/bma/bearbeiten", response_class=HTMLResponse)
def bma_form(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_bma_form.html", _detail_context(request, db, user, objekt)
    )


@router.post("/{objekt_id}/bma", response_class=HTMLResponse)
def bma_speichern(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    bma_vorhanden: str = Form(""),
    bma_nummer: str = Form(""),
    rfl_nummer: str = Form(""),
    bmz_standort: str = Form(""),
    fbf_standort: str = Form(""),
    laufkarten_ablageort: str = Form(""),
    uebertragungseinrichtung: str = Form(""),
    schluesselsafe_vorhanden: str = Form(""),
    schluesselsafe_standort: str = Form(""),
    schluesselsafe_inhalt: str = Form(""),
    benachrichtigung_sms: str = Form(""),
    benachrichtigung_email: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)

    if not bma_vorhanden:
        # BMA-Block entfernen
        if objekt.bma is not None:
            write_objekt_change(db, objekt.id, objekt.org_id, "bma", "bma_entfernt",
                                before=objekt.bma.bma_nummer, after=None, user_id=user.id)
            db.delete(objekt.bma)
            objekt.bma = None
            db.commit()
        return templates.TemplateResponse(
            request, "objekt/_bma.html", _detail_context(request, db, user, objekt)
        )

    if objekt.bma is None:
        objekt.bma = ObjektBMA(org_id=objekt.org_id, objekt_id=objekt.id)
        db.add(objekt.bma)
        write_objekt_change(db, objekt.id, objekt.org_id, "bma", "bma_angelegt",
                            before=None, after=bma_nummer.strip() or "-", user_id=user.id)

    bma = objekt.bma
    daten = {
        "bma_nummer": bma_nummer.strip() or None,
        "rfl_nummer": rfl_nummer.strip() or None,
        "bmz_standort": bmz_standort.strip() or None,
        "fbf_standort": fbf_standort.strip() or None,
        "laufkarten_ablageort": laufkarten_ablageort.strip() or None,
        "uebertragungseinrichtung": uebertragungseinrichtung.strip() or None,
        "schluesselsafe_vorhanden": bool(schluesselsafe_vorhanden),
        "schluesselsafe_standort": schluesselsafe_standort.strip() or None,
        "schluesselsafe_inhalt": schluesselsafe_inhalt.strip() or None,
        "benachrichtigung_sms": benachrichtigung_sms.strip() or None,
        "benachrichtigung_email": benachrichtigung_email.strip() or None,
    }
    for feld, neu in daten.items():
        alt = getattr(bma, feld)
        if alt != neu:
            setattr(bma, feld, neu)
            write_objekt_change(db, objekt.id, objekt.org_id, "bma", feld,
                                before=alt, after=neu, user_id=user.id)
    db.commit()

    return templates.TemplateResponse(
        request, "objekt/_bma.html", _detail_context(request, db, user, objekt)
    )


# ── Abschnitt: Zusatzadressen ──────────────────────────────────────────────────

@router.get("/{objekt_id}/zusatzadressen", response_class=HTMLResponse)
def zusatzadressen_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_zusatzadressen.html", _detail_context(request, db, user, objekt)
    )


@router.post("/{objekt_id}/zusatzadressen/neu", response_class=HTMLResponse)
def zusatzadresse_neu(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    bezeichnung: str = Form(...),
    strasse: str = Form(""),
    hausnummer: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    if not bezeichnung.strip():
        raise HTTPException(status_code=400, detail="Bezeichnung ist erforderlich")
    max_sort = max([z.sort for z in objekt.zusatzadressen], default=0)
    adresse = ObjektZusatzadresse(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        bezeichnung=bezeichnung.strip(),
        strasse=strasse.strip() or None,
        hausnummer=hausnummer.strip() or None,
        plz=plz.strip() or None,
        ort=ort.strip() or None,
        sort=max_sort + 1,
    )
    db.add(adresse)
    write_objekt_change(db, objekt.id, objekt.org_id, "stammdaten", "zusatzadresse_neu",
                        before=None, after=adresse.bezeichnung, user_id=user.id)
    db.commit()
    db.refresh(objekt)
    return templates.TemplateResponse(
        request, "objekt/_zusatzadressen.html", _detail_context(request, db, user, objekt)
    )


@router.post("/{objekt_id}/zusatzadressen/{adresse_id}/loeschen", response_class=HTMLResponse)
def zusatzadresse_loeschen(
    objekt_id: int,
    adresse_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    adresse = (
        db.query(ObjektZusatzadresse)
        .filter(ObjektZusatzadresse.id == adresse_id, ObjektZusatzadresse.objekt_id == objekt.id)
        .first()
    )
    if adresse is None:
        raise HTTPException(status_code=404, detail="Zusatzadresse nicht gefunden")
    write_objekt_change(db, objekt.id, objekt.org_id, "stammdaten", "zusatzadresse_geloescht",
                        before=adresse.bezeichnung, after=None, user_id=user.id)
    db.delete(adresse)
    db.commit()
    db.refresh(objekt)
    return templates.TemplateResponse(
        request, "objekt/_zusatzadressen.html", _detail_context(request, db, user, objekt)
    )


# ── Abschnitt: Protokoll ───────────────────────────────────────────────────────

@router.get("/{objekt_id}/protokoll", response_class=HTMLResponse)
def protokoll_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    changes = (
        db.query(ObjektChange)
        .filter(ObjektChange.objekt_id == objekt.id)
        .order_by(ObjektChange.erstellt_am.desc(), ObjektChange.id.desc())
        .limit(200)
        .all()
    )
    user_ids = {c.user_id for c in changes if c.user_id}
    benutzer: dict[int, User] = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            benutzer[u.id] = u
    ctx = _detail_context(request, db, user, objekt)
    ctx["changes"] = changes
    ctx["benutzer"] = benutzer
    return templates.TemplateResponse(request, "objekt/_protokoll.html", ctx)


# ── Status-Workflow ────────────────────────────────────────────────────────────

@router.post("/{objekt_id}/status")
def status_wechseln(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    neuer_status: str = Form(...),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    if objekt.entwurf_von_id is not None:
        # Arbeitskopien duerfen ihren Status nicht ueber diese generische Route aendern -
        # sonst liesse sich eine Kopie direkt auf 'freigegeben' setzen und wuerde als
        # zweites, unverknuepftes Objekt liegen bleiben (Merge/Verwerfen umgangen). Der
        # einzige Weg ist POST /{id}/uebernehmen bzw. /verwerfen.
        raise HTTPException(
            status_code=400,
            detail="Arbeitskopien haben keinen eigenen Status - ueber 'Freigeben' bzw. "
                   "'Verwerfen' der Ueberarbeitung steuern",
        )
    if neuer_status not in OBJEKT_STATUS_LABELS:
        raise HTTPException(status_code=400, detail="Unbekannter Status")
    if not status_uebergang_erlaubt(objekt.status, neuer_status):
        raise HTTPException(
            status_code=400,
            detail=f"Statuswechsel {OBJEKT_STATUS_LABELS[objekt.status]} → "
                   f"{OBJEKT_STATUS_LABELS[neuer_status]} nicht erlaubt",
        )
    alt = objekt.status
    objekt.status = neuer_status
    objekt.aktualisiert_von_id = user.id
    write_objekt_change(db, objekt.id, objekt.org_id, "status", "status",
                        before=alt, after=neuer_status, user_id=user.id)
    write_audit(db, "objekt.status_changed", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id,
                payload={"von": alt, "nach": neuer_status})
    db.commit()
    return RedirectResponse(url=f"/objekte/{objekt.id}", status_code=303)


# ── Arbeitskopie-Workflow (Ueberarbeiten / Uebernehmen / Verwerfen) ────────────

@router.post("/{objekt_id}/ueberarbeiten")
def objekt_ueberarbeiten(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    """Legt eine Arbeitskopie eines freigegebenen Objekts an. Das produktive Objekt bleibt
    dabei inhaltlich unveraendert und weiterhin fuer Matching/Sync/Objektblatt aktiv - siehe
    erstelle_arbeitskopie() in objekt_service.py."""
    objekt = _objekt_or_404(db, objekt_id, user)
    try:
        kopie = erstelle_arbeitskopie(db, objekt, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(db, "objekt.arbeitskopie_erstellt", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id,
                payload={"kopie_id": kopie.id})
    db.commit()
    return RedirectResponse(url=f"/objekte/{objekt.id}", status_code=303)


@router.post("/{objekt_id}/uebernehmen")
def objekt_arbeitskopie_uebernehmen(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    """Uebernimmt die offene Arbeitskopie in das produktive Objekt (Merge) - siehe
    uebernimm_arbeitskopie() in objekt_service.py. objekt_id ist immer die produktive id."""
    basis = _objekt_or_404(db, objekt_id, user)
    kopie = hole_arbeitskopie(db, basis)
    if kopie is None:
        raise HTTPException(status_code=400, detail="Keine offene Arbeitskopie vorhanden")
    try:
        uebernimm_arbeitskopie(db, kopie, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(db, "objekt.arbeitskopie_uebernommen", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=basis.id, payload={})
    db.commit()
    return RedirectResponse(url=f"/objekte/{basis.id}", status_code=303)


@router.post("/{objekt_id}/verwerfen")
def objekt_arbeitskopie_verwerfen(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    """Verwirft die offene Arbeitskopie ohne Uebernahme; die produktive Version bleibt
    unveraendert. objekt_id ist immer die produktive id."""
    basis = _objekt_or_404(db, objekt_id, user)
    kopie = hole_arbeitskopie(db, basis)
    if kopie is None:
        raise HTTPException(status_code=400, detail="Keine offene Arbeitskopie vorhanden")
    try:
        verwirf_arbeitskopie(db, kopie, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(db, "objekt.arbeitskopie_verworfen", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=basis.id, payload={})
    db.commit()
    return RedirectResponse(url=f"/objekte/{basis.id}", status_code=303)


# ── Objekt loeschen (org_admin/system_admin) ───────────────────────────────────

def _loesche_objekt(db: Session, objekt: Objekt, user: User) -> list[Path]:
    """Loescht ein Objekt vollstaendig: erst alle Dokumente ueber den Service
    (Storage-Quota-Freigabe, siehe delete_dokument), dann das Objekt selbst
    (Kind-Zeilen via DB-Kaskade). Commit macht der Aufrufer.

    Gibt die Dokument-Verzeichnisse zurueck, die der Aufrufer NACH einem erfolgreichen
    db.commit() per raeume_dokument_verzeichnis_auf() von der Platte loeschen muss -
    vorher waere ein Commit-Fehler nicht mehr rueckgaengig zu machen (siehe
    delete_dokument())."""
    from app.models.objekt import ObjektDokument
    from app.services.objekt_dokument_service import delete_dokument

    if objekt.entwurf_von_id is not None:
        # Direktes Loeschen einer Arbeitskopie (statt ueber "Verwerfen") wuerde sonst die
        # Basis dauerhaft auf 'in_ueberarbeitung' stehen lassen (kein Uebergang mehr zurueck
        # zu 'freigegeben' in OBJEKT_STATUS_UEBERGAENGE) - ueber verwirf_arbeitskopie() geht
        # die Basis sauber zurueck auf 'freigegeben'. Arbeitskopien haben keine eigenen
        # Dokumente (out of scope, siehe objekt.py), der Dokumente-Pfad unten entfaellt.
        verwirf_arbeitskopie(db, objekt, user.id)
        write_audit(db, "objekt.arbeitskopie_geloescht", org_id=objekt.org_id, user_id=user.id,
                    entity_type="objekt", entity_id=objekt.id,
                    payload={"basis_objekt_id": objekt.entwurf_von_id})
        return []

    dokumente = (
        db.query(ObjektDokument)
        .filter(ObjektDokument.objekt_id == objekt.id)
        .all()
    )
    verzeichnisse = [delete_dokument(dokument, db) for dokument in dokumente]

    write_audit(db, "objekt.deleted", org_id=objekt.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id,
                payload={"name": objekt.name, "nummer": objekt.nummer,
                         "dokumente_geloescht": len(dokumente)})
    db.delete(objekt)
    return verzeichnisse


@router.post("/bulk-loeschen")
def objekte_bulk_loeschen(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    objekt_ids: str = Form(""),
):
    """Loescht mehrere Objekte aus der Listen-Auswahl (nur org_admin/system_admin)."""
    from app.services.objekt_dokument_service import raeume_dokument_verzeichnis_auf

    ids = [int(t) for t in objekt_ids.split(",") if t.strip().isdigit()]
    verzeichnisse: list[Path] = []
    for objekt_id in ids:
        objekt = db.query(Objekt).filter(Objekt.id == objekt_id).first()
        if objekt is None:
            continue  # fremde Org (Tenant-Filter) oder bereits geloescht
        verzeichnisse.extend(_loesche_objekt(db, objekt, user))
    db.commit()
    for verzeichnis in verzeichnisse:
        background_tasks.add_task(raeume_dokument_verzeichnis_auf, verzeichnis)
    return RedirectResponse(url="/objekte/", status_code=303)


@router.post("/bulk-freigeben")
def objekte_bulk_freigeben(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    objekt_ids: str = Form(""),
):
    """Setzt mehrere Objekte aus der Listen-Auswahl von 'entwurf' auf 'freigegeben' -
    der bewusst manuelle Migrationsweg fuer den heutigen Entwurfs-Bestand (keine
    automatische Statusmigration, siehe docs/plans/objekt-arbeitskopie-plan.md).
    Ignoriert Arbeitskopien und bereits freigegebene/archivierte Objekte."""
    ids = [int(t) for t in objekt_ids.split(",") if t.strip().isdigit()]
    anzahl = 0
    for objekt_id in ids:
        objekt = db.query(Objekt).filter(Objekt.id == objekt_id).first()
        if objekt is None or objekt.entwurf_von_id is not None:
            continue  # fremde Org (Tenant-Filter), bereits geloescht, oder Arbeitskopie
        if not status_uebergang_erlaubt(objekt.status, OBJEKT_STATUS_FREIGEGEBEN):
            continue
        alt = objekt.status
        objekt.status = OBJEKT_STATUS_FREIGEGEBEN
        objekt.aktualisiert_von_id = user.id
        write_objekt_change(db, objekt.id, objekt.org_id, "status", "status",
                            before=alt, after=objekt.status, user_id=user.id)
        anzahl += 1
    write_audit(db, "objekt.bulk_freigegeben", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=None, payload={"anzahl": anzahl, "angefragt": len(ids)})
    db.commit()
    return RedirectResponse(url="/objekte/", status_code=303)


@router.post("/{objekt_id}/loeschen")
def objekt_loeschen(
    objekt_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.objekt_dokument_service import raeume_dokument_verzeichnis_auf

    objekt = _objekt_or_404(db, objekt_id, user)
    verzeichnisse = _loesche_objekt(db, objekt, user)
    db.commit()
    for verzeichnis in verzeichnisse:
        background_tasks.add_task(raeume_dokument_verzeichnis_auf, verzeichnis)
    return RedirectResponse(url="/objekte/", status_code=303)


# ── Abschnitt: Gefahren ────────────────────────────────────────────────────────

def _gefahren_katalog(db: Session) -> list[GefahrenKatalog]:
    return (
        db.query(GefahrenKatalog)
        .filter(GefahrenKatalog.aktiv.is_(True))
        .order_by(GefahrenKatalog.sort, GefahrenKatalog.name)
        .all()
    )


@router.get("/{objekt_id:int}/gefahren", response_class=HTMLResponse)
def gefahren_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _detail_context(request, db, user, objekt)
    ctx["gefahren_katalog"] = _gefahren_katalog(db)
    return templates.TemplateResponse(request, "objekt/_gefahren.html", ctx)


@router.post("/{objekt_id:int}/gefahren/neu", response_class=HTMLResponse)
def gefahr_neu(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    gefahr_id: int = Form(...),
    un_nummer: str = Form(""),
    detail: str = Form(""),
    stoffname: str = Form(""),
    gefahrklasse: str = Form(""),
    gefahrnummer: str = Form(""),
    link_label: list[str] = Form(default=[]),
    link_url: list[str] = Form(default=[]),
):
    from app.services.objekt_service import links_aus_form
    objekt = _objekt_or_404(db, objekt_id, user)
    katalog = db.query(GefahrenKatalog).filter(GefahrenKatalog.id == gefahr_id).first()
    if katalog is None:
        raise HTTPException(status_code=404, detail="Gefahr nicht im Katalog")
    max_sort = max([g.sort for g in objekt.gefahren], default=0)
    eintrag = ObjektGefahr(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        gefahr_id=gefahr_id,
        un_nummer=un_nummer.strip() or None,
        detail=detail.strip() or None,
        stoffname=stoffname.strip() or None,
        gefahrklasse=gefahrklasse.strip() or None,
        gefahrnummer=gefahrnummer.strip() or None,
        links_json=links_aus_form(link_label, link_url),
        sort=max_sort + 1,
    )
    db.add(eintrag)
    write_objekt_change(db, objekt.id, objekt.org_id, "gefahren", "gefahr_neu",
                        before=None, after=katalog.name, user_id=user.id)
    db.commit()
    db.refresh(objekt)
    ctx = _detail_context(request, db, user, objekt)
    ctx["gefahren_katalog"] = _gefahren_katalog(db)
    return templates.TemplateResponse(request, "objekt/_gefahren.html", ctx)


@router.post("/{objekt_id:int}/gefahren/{gefahr_eintrag_id}/edit", response_class=HTMLResponse)
def gefahr_edit(
    objekt_id: int,
    gefahr_eintrag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    un_nummer: str = Form(""),
    detail: str = Form(""),
    stoffname: str = Form(""),
    gefahrklasse: str = Form(""),
    gefahrnummer: str = Form(""),
    link_label: list[str] = Form(default=[]),
    link_url: list[str] = Form(default=[]),
):
    from app.services.objekt_service import links_aus_form
    objekt = _objekt_or_404(db, objekt_id, user)
    eintrag = (
        db.query(ObjektGefahr)
        .filter(ObjektGefahr.id == gefahr_eintrag_id, ObjektGefahr.objekt_id == objekt.id)
        .first()
    )
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Gefahren-Eintrag nicht gefunden")
    eintrag.un_nummer = un_nummer.strip() or None
    eintrag.detail = detail.strip() or None
    eintrag.stoffname = stoffname.strip() or None
    eintrag.gefahrklasse = gefahrklasse.strip() or None
    eintrag.gefahrnummer = gefahrnummer.strip() or None
    eintrag.links_json = links_aus_form(link_label, link_url)
    write_objekt_change(db, objekt.id, objekt.org_id, "gefahren", "gefahr_bearbeitet",
                        before=None, after=eintrag.gefahr.name if eintrag.gefahr else None,
                        user_id=user.id)
    db.commit()
    db.refresh(objekt)
    ctx = _detail_context(request, db, user, objekt)
    ctx["gefahren_katalog"] = _gefahren_katalog(db)
    return templates.TemplateResponse(request, "objekt/_gefahren.html", ctx)


@router.post("/{objekt_id:int}/gefahren/{gefahr_eintrag_id}/loeschen", response_class=HTMLResponse)
def gefahr_loeschen(
    objekt_id: int,
    gefahr_eintrag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    eintrag = (
        db.query(ObjektGefahr)
        .filter(ObjektGefahr.id == gefahr_eintrag_id, ObjektGefahr.objekt_id == objekt.id)
        .first()
    )
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Gefahren-Eintrag nicht gefunden")
    write_objekt_change(db, objekt.id, objekt.org_id, "gefahren", "gefahr_geloescht",
                        before=eintrag.gefahr.name if eintrag.gefahr else str(eintrag.gefahr_id),
                        after=None, user_id=user.id)
    db.delete(eintrag)
    db.commit()
    db.refresh(objekt)
    ctx = _detail_context(request, db, user, objekt)
    ctx["gefahren_katalog"] = _gefahren_katalog(db)
    return templates.TemplateResponse(request, "objekt/_gefahren.html", ctx)


# ── Abschnitt: Merkmale ────────────────────────────────────────────────────────

def _merkmal_katalog(db: Session) -> list[MerkmalKatalog]:
    return (
        db.query(MerkmalKatalog)
        .filter(MerkmalKatalog.aktiv.is_(True))
        .order_by(MerkmalKatalog.sort, MerkmalKatalog.name)
        .all()
    )


@router.get("/{objekt_id:int}/merkmale", response_class=HTMLResponse)
def merkmale_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _detail_context(request, db, user, objekt)
    zugeordnet = {m.merkmal_id for m in objekt.merkmale}
    ctx["merkmal_katalog"] = [m for m in _merkmal_katalog(db) if m.id not in zugeordnet]
    return templates.TemplateResponse(request, "objekt/_merkmale.html", ctx)


@router.post("/{objekt_id:int}/merkmale/neu", response_class=HTMLResponse)
def merkmal_zuordnen(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    merkmal_id: int = Form(...),
    hinweis: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    katalog = db.query(MerkmalKatalog).filter(MerkmalKatalog.id == merkmal_id).first()
    if katalog is None:
        raise HTTPException(status_code=404, detail="Merkmal nicht im Katalog")
    bereits = any(m.merkmal_id == merkmal_id for m in objekt.merkmale)
    if not bereits:
        db.add(ObjektMerkmal(
            org_id=objekt.org_id,
            objekt_id=objekt.id,
            merkmal_id=merkmal_id,
            hinweis=hinweis.strip() or None,
        ))
        write_objekt_change(db, objekt.id, objekt.org_id, "merkmale", "merkmal_neu",
                            before=None, after=katalog.name, user_id=user.id)
        db.commit()
        db.refresh(objekt)
    ctx = _detail_context(request, db, user, objekt)
    zugeordnet = {m.merkmal_id for m in objekt.merkmale}
    ctx["merkmal_katalog"] = [m for m in _merkmal_katalog(db) if m.id not in zugeordnet]
    return templates.TemplateResponse(request, "objekt/_merkmale.html", ctx)


@router.post("/{objekt_id:int}/merkmale/{zuordnung_id}/loeschen", response_class=HTMLResponse)
def merkmal_entfernen(
    objekt_id: int,
    zuordnung_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    zuordnung = (
        db.query(ObjektMerkmal)
        .filter(ObjektMerkmal.id == zuordnung_id, ObjektMerkmal.objekt_id == objekt.id)
        .first()
    )
    if zuordnung is None:
        raise HTTPException(status_code=404, detail="Merkmal-Zuordnung nicht gefunden")
    write_objekt_change(db, objekt.id, objekt.org_id, "merkmale", "merkmal_entfernt",
                        before=zuordnung.merkmal.name if zuordnung.merkmal else str(zuordnung.merkmal_id),
                        after=None, user_id=user.id)
    db.delete(zuordnung)
    db.commit()
    db.refresh(objekt)
    ctx = _detail_context(request, db, user, objekt)
    zugeordnet = {m.merkmal_id for m in objekt.merkmale}
    ctx["merkmal_katalog"] = [m for m in _merkmal_katalog(db) if m.id not in zugeordnet]
    return templates.TemplateResponse(request, "objekt/_merkmale.html", ctx)


# ── Abschnitt: Kontakte ────────────────────────────────────────────────────────
# telefone_zu_json lebt in objekt_service.py (auch vom BMA-Webplattform-Import
# genutzt, siehe app/services/bma_import/bma_sync.py).


@router.get("/{objekt_id}/kontakte", response_class=HTMLResponse)
def kontakte_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_kontakte.html", _detail_context(request, db, user, objekt)
    )


@router.post("/{objekt_id}/kontakte/neu", response_class=HTMLResponse)
def kontakt_neu(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    art: str = Form("sonstig"),
    name: str = Form(...),
    telefon_nummer: list[str] = Form(default=[]),
    telefon_label: list[str] = Form(default=[]),
    telefon_sms: list[str] = Form(default=[]),
    email: str = Form(""),
    erreichbarkeit: str = Form(""),
    benachrichtigung_mail: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    if art not in lade_auswahl(db, objekt.org_id, AUSWAHL_KONTAKTART):
        art = "sonstig"
    max_sort = max([k.sort for k in objekt.kontakte], default=0)
    try:
        telefone_json = telefone_aus_form(telefon_nummer, telefon_label, telefon_sms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    kontakt = ObjektKontakt(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        art=art,
        name=name.strip(),
        telefone_json=telefone_json,
        email=email.strip() or None,
        erreichbarkeit=erreichbarkeit.strip() or None,
        benachrichtigung_mail=benachrichtigung_mail in ("1", "true", "on"),
        sort=max_sort + 1,
    )
    db.add(kontakt)
    write_objekt_change(db, objekt.id, objekt.org_id, "kontakte", "kontakt_neu",
                        before=None, after=kontakt.name, user_id=user.id)
    db.commit()
    db.refresh(objekt)
    return templates.TemplateResponse(
        request, "objekt/_kontakte.html", _detail_context(request, db, user, objekt)
    )


@router.post("/{objekt_id}/kontakte/{kontakt_id}", response_class=HTMLResponse)
def kontakt_speichern(
    objekt_id: int,
    kontakt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    art: str = Form("sonstig"),
    name: str = Form(...),
    telefon_nummer: list[str] = Form(default=[]),
    telefon_label: list[str] = Form(default=[]),
    telefon_sms: list[str] = Form(default=[]),
    email: str = Form(""),
    erreichbarkeit: str = Form(""),
    benachrichtigung_mail: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    kontakt = (
        db.query(ObjektKontakt)
        .filter(ObjektKontakt.id == kontakt_id, ObjektKontakt.objekt_id == objekt.id)
        .first()
    )
    if kontakt is None:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")
    if art not in lade_auswahl(db, objekt.org_id, AUSWAHL_KONTAKTART):
        art = "sonstig"
    try:
        telefone_json = telefone_aus_form(telefon_nummer, telefon_label, telefon_sms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    daten = {
        "art": art,
        "name": name.strip(),
        "telefone_json": telefone_json,
        "email": email.strip() or None,
        "erreichbarkeit": erreichbarkeit.strip() or None,
        "benachrichtigung_mail": benachrichtigung_mail in ("1", "true", "on"),
    }
    for feld, neu in daten.items():
        alt = getattr(kontakt, feld)
        if alt != neu:
            setattr(kontakt, feld, neu)
            write_objekt_change(db, objekt.id, objekt.org_id, "kontakte",
                                f"kontakt_{kontakt.name}_{feld}",
                                before=alt, after=neu, user_id=user.id)
    db.commit()
    db.refresh(objekt)
    return templates.TemplateResponse(
        request, "objekt/_kontakte.html", _detail_context(request, db, user, objekt)
    )


@router.get("/{objekt_id}/benachrichtigung", response_class=HTMLResponse)
def benachrichtigung_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _benachrichtigung_context(request, db, user, objekt)
    return templates.TemplateResponse(
        request, "objekt/_benachrichtigung.html", ctx
    )


@router.post("/{objekt_id}/benachrichtigung", response_class=HTMLResponse)
def benachrichtigung_speichern(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    kontakt_info_uebung: str = Form(""),
    kontakt_info_stichworte: str = Form(""),
    kontakt_info_betreff: str = Form(""),
    kontakt_info_template: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    aktualisiere_felder(
        db,
        objekt,
        {
            "kontakt_info_uebung": kontakt_info_uebung in ("1", "true", "on"),
            "kontakt_info_stichworte": kontakt_info_stichworte.strip() or None,
            "kontakt_info_betreff": kontakt_info_betreff.strip() or None,
            "kontakt_info_template": kontakt_info_template.strip() or None,
        },
        bereich="benachrichtigung",
        user_id=user.id,
    )
    db.commit()
    db.refresh(objekt)
    return templates.TemplateResponse(
        request, "objekt/_benachrichtigung.html",
        _benachrichtigung_context(request, db, user, objekt),
    )


def _benachrichtigung_context(request: Request, db: Session, user: User, objekt: Objekt) -> dict:
    from app.models.master import OrgSettings
    from app.services.objekt_kontakt_notify import loese_betreff, loese_template
    from app.services.sms_dispatch_service import render_template

    ctx = _detail_context(request, db, user, objekt)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == objekt.org_id).first()
    beispiel = {
        "objekt": objekt.name or "Beispielobjekt",
        "objektnummer": objekt.nummer or "123",
        "vulgoname": objekt.vulgoname or "Beispielbetrieb",
        "stichwort": "B2",
        "adresse": objekt.adresse_zeile or "Hauptstraße 1, 0000 Musterort",
        "ort": objekt.ort or "Musterort",
        "meldung": "Beispiel-Alarmtext",
        "einsatzgrund": "Brandverdacht",
        "datum": "26.08.2026",
        "zeit": "14:30",
        "feuerwehr": user.org.name if user.org else "Feuerwehr",
        "kontakt": "Max Mustermann",
        "leitstellennummer": "f26001234",
    }
    ctx["benachrichtigung_vorschau_betreff"] = render_template(
        loese_betreff(objekt, org_settings), beispiel
    )
    ctx["benachrichtigung_vorschau_text"] = render_template(
        loese_template(objekt, org_settings), beispiel
    )
    return ctx


@router.post("/{objekt_id}/kontakte/{kontakt_id}/loeschen", response_class=HTMLResponse)
def kontakt_loeschen(
    objekt_id: int,
    kontakt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    kontakt = (
        db.query(ObjektKontakt)
        .filter(ObjektKontakt.id == kontakt_id, ObjektKontakt.objekt_id == objekt.id)
        .first()
    )
    if kontakt is None:
        raise HTTPException(status_code=404, detail="Kontakt nicht gefunden")
    write_objekt_change(db, objekt.id, objekt.org_id, "kontakte", "kontakt_geloescht",
                        before=kontakt.name, after=None, user_id=user.id)
    # ist_offener_vorschlag() vergleicht importierte Kontakte bei jedem Abgleich live.
    db.delete(kontakt)
    db.commit()
    db.refresh(objekt)
    return templates.TemplateResponse(
        request, "objekt/_kontakte.html", _detail_context(request, db, user, objekt)
    )


# ── Abschnitt: Wohnanlage ──────────────────────────────────────────────────────

@router.get("/{objekt_id}/wohnanlage", response_class=HTMLResponse)
def wohnanlage_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_wohnanlage.html", _detail_context(request, db, user, objekt)
    )


@router.get("/{objekt_id}/wohnanlage/bearbeiten", response_class=HTMLResponse)
def wohnanlage_form(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_wohnanlage_form.html", _detail_context(request, db, user, objekt)
    )


@router.post("/{objekt_id}/wohnanlage", response_class=HTMLResponse)
def wohnanlage_speichern(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    wohnanlage_vorhanden: str = Form(""),
    wohneinheiten: str = Form(""),
    geschosse: str = Form(""),
    stiegen: str = Form(""),
    hausverwaltung_kontakt_id: str = Form(""),
    hinweise: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)

    if not wohnanlage_vorhanden:
        if objekt.wohnanlage is not None:
            write_objekt_change(db, objekt.id, objekt.org_id, "stammdaten", "wohnanlage_entfernt",
                                before="Wohnanlagen-Block", after=None, user_id=user.id)
            db.delete(objekt.wohnanlage)
            objekt.wohnanlage = None
            db.commit()
        return templates.TemplateResponse(
            request, "objekt/_wohnanlage.html", _detail_context(request, db, user, objekt)
        )

    if objekt.wohnanlage is None:
        objekt.wohnanlage = ObjektWohnanlage(org_id=objekt.org_id, objekt_id=objekt.id)
        db.add(objekt.wohnanlage)
        write_objekt_change(db, objekt.id, objekt.org_id, "stammdaten", "wohnanlage_angelegt",
                            before=None, after="Wohnanlagen-Block", user_id=user.id)

    kontakt_id = int(hausverwaltung_kontakt_id) if hausverwaltung_kontakt_id.strip() else None
    if kontakt_id is not None:
        gueltig = any(k.id == kontakt_id for k in objekt.kontakte)
        if not gueltig:
            kontakt_id = None

    wa = objekt.wohnanlage
    daten = {
        "wohneinheiten": int(wohneinheiten) if wohneinheiten.strip() else None,
        "geschosse": int(geschosse) if geschosse.strip() else None,
        "stiegen": int(stiegen) if stiegen.strip() else None,
        "hausverwaltung_kontakt_id": kontakt_id,
        "hinweise": hinweise.strip() or None,
    }
    for feld, neu in daten.items():
        alt = getattr(wa, feld)
        if alt != neu:
            setattr(wa, feld, neu)
            write_objekt_change(db, objekt.id, objekt.org_id, "stammdaten",
                                f"wohnanlage_{feld}", before=alt, after=neu, user_id=user.id)
    db.commit()
    db.refresh(objekt)
    return templates.TemplateResponse(
        request, "objekt/_wohnanlage.html", _detail_context(request, db, user, objekt)
    )


# ── Katalog-Admin: Gefahren + Merkmale (org_admin) ─────────────────────────────

@router.post("/kataloge/gefahren/neu")
def katalog_gefahr_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    piktogramm_typ: str = Form("sonstig"),
    sort: int = Form(0),
    link_label: list[str] = Form(default=[]),
    link_url: list[str] = Form(default=[]),
):
    from app.services.objekt_service import links_aus_form
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    if piktogramm_typ not in lade_auswahl(db, user.org_id, AUSWAHL_PIKTOGRAMM):
        piktogramm_typ = "sonstig"
    existiert = db.query(GefahrenKatalog).filter(GefahrenKatalog.name == name.strip()).first()
    if existiert:
        return RedirectResponse(url="/objekte/kataloge?error=exists&tab=gefahren", status_code=303)
    db.add(GefahrenKatalog(org_id=user.org_id, name=name.strip(),
                           piktogramm_typ=piktogramm_typ,
                           links_json=links_aus_form(link_label, link_url),
                           sort=sort, aktiv=True))
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1&tab=gefahren", status_code=303)


@router.post("/kataloge/gefahren/{gefahr_id}/edit")
def katalog_gefahr_edit(
    gefahr_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    piktogramm_typ: str = Form("sonstig"),
    sort: int = Form(0),
    aktiv: str = Form(""),
    link_label: list[str] = Form(default=[]),
    link_url: list[str] = Form(default=[]),
):
    from app.services.objekt_service import links_aus_form
    eintrag = db.query(GefahrenKatalog).filter(GefahrenKatalog.id == gefahr_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Gefahr nicht gefunden")
    if piktogramm_typ not in lade_auswahl(db, user.org_id, AUSWAHL_PIKTOGRAMM):
        piktogramm_typ = "sonstig"
    eintrag.name = name.strip()
    eintrag.piktogramm_typ = piktogramm_typ
    eintrag.links_json = links_aus_form(link_label, link_url)
    eintrag.sort = sort
    eintrag.aktiv = bool(aktiv)
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1&tab=gefahren", status_code=303)


@router.post("/kataloge/gefahren/{gefahr_id}/loeschen")
def katalog_gefahr_loeschen(
    gefahr_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    eintrag = db.query(GefahrenKatalog).filter(GefahrenKatalog.id == gefahr_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Gefahr nicht gefunden")
    verwendet = db.query(ObjektGefahr).filter(ObjektGefahr.gefahr_id == eintrag.id).first()
    if verwendet:
        return RedirectResponse(url="/objekte/kataloge?error=in_use&tab=gefahren", status_code=303)
    db.delete(eintrag)
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1&tab=gefahren", status_code=303)


@router.post("/kataloge/merkmale/neu")
def katalog_merkmal_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    icon: str = Form(""),
    sort: int = Form(0),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    existiert = db.query(MerkmalKatalog).filter(MerkmalKatalog.name == name.strip()).first()
    if existiert:
        return RedirectResponse(url="/objekte/kataloge?error=exists&tab=merkmale", status_code=303)
    db.add(MerkmalKatalog(org_id=user.org_id, code=None, name=name.strip(),
                          icon=icon.strip() or None, sort=sort, aktiv=True))
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1&tab=merkmale", status_code=303)


@router.post("/kataloge/merkmale/{merkmal_id}/edit")
def katalog_merkmal_edit(
    merkmal_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    icon: str = Form(""),
    sort: int = Form(0),
    aktiv: str = Form(""),
):
    eintrag = db.query(MerkmalKatalog).filter(MerkmalKatalog.id == merkmal_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Merkmal nicht gefunden")
    eintrag.name = name.strip()
    eintrag.icon = icon.strip() or None
    eintrag.sort = sort
    eintrag.aktiv = bool(aktiv)
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1&tab=merkmale", status_code=303)


@router.post("/kataloge/merkmale/{merkmal_id}/loeschen")
def katalog_merkmal_loeschen(
    merkmal_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    eintrag = db.query(MerkmalKatalog).filter(MerkmalKatalog.id == merkmal_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Merkmal nicht gefunden")
    verwendet = db.query(ObjektMerkmal).filter(ObjektMerkmal.merkmal_id == eintrag.id).first()
    if verwendet:
        return RedirectResponse(url="/objekte/kataloge?error=in_use&tab=merkmale", status_code=303)
    db.delete(eintrag)
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1&tab=merkmale", status_code=303)


# ── Kataloge: pflegbare Auswahllisten (Kontaktarten/Dokumentarten/Piktogramme) ──

def _slug_code(name: str) -> str:
    """Erzeugt einen stabilen Code aus einem Anzeigenamen (a-z0-9_)."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug[:40] or "eintrag"


def _auswahl_in_use(db: Session, typ: str, code: str) -> bool:
    """True, wenn der Code irgendwo referenziert wird (Loeschsperre)."""
    if typ == AUSWAHL_KONTAKTART:
        return db.query(ObjektKontakt).filter(ObjektKontakt.art == code).first() is not None
    if typ == AUSWAHL_DOKUMENTART:
        return db.query(ObjektDokumentSeite).filter(
            ObjektDokumentSeite.dokumentart == code).first() is not None
    if typ == AUSWAHL_PIKTOGRAMM:
        return db.query(GefahrenKatalog).filter(
            GefahrenKatalog.piktogramm_typ == code).first() is not None
    return False


def _auswahl_redirect(typ: str, status: str) -> RedirectResponse:
    return RedirectResponse(url=f"/objekte/kataloge?{status}&tab={typ}", status_code=303)


@router.post("/kataloge/auswahl/{typ}/neu")
def auswahl_neu(
    typ: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    icon: str = Form(""),
    sort: int = Form(0),
):
    if typ not in _AUSWAHL_TYPEN:
        raise HTTPException(status_code=404, detail="Unbekannte Auswahlliste")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    # Stabilen, eindeutigen Code aus dem Namen ableiten (wird nie mehr geaendert)
    basis = _slug_code(name)
    code = basis
    n = 2
    while (
        db.query(ObjektAuswahl)
        .filter(ObjektAuswahl.typ == typ, ObjektAuswahl.code == code)
        .first()
    ):
        code = f"{basis[:37]}_{n}"
        n += 1
    db.add(ObjektAuswahl(
        org_id=user.org_id, typ=typ, code=code, name=name.strip(),
        icon=icon.strip() or None, sort=sort, aktiv=True, system=False,
    ))
    db.commit()
    return _auswahl_redirect(typ, "saved=1")


@router.post("/kataloge/auswahl/{typ}/{eintrag_id}/edit")
def auswahl_edit(
    typ: str,
    eintrag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    icon: str = Form(""),
    sort: int = Form(0),
    aktiv: str = Form(""),
):
    if typ not in _AUSWAHL_TYPEN:
        raise HTTPException(status_code=404, detail="Unbekannte Auswahlliste")
    eintrag = (
        db.query(ObjektAuswahl)
        .filter(ObjektAuswahl.id == eintrag_id, ObjektAuswahl.typ == typ)
        .first()
    )
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    # Code bleibt stabil (Referenz); nur Label/Icon/Sortierung/Status aenderbar.
    eintrag.name = name.strip()
    eintrag.icon = icon.strip() or None
    eintrag.sort = sort
    # System-Eintraege duerfen nicht deaktiviert werden (immer verfuegbar halten)
    eintrag.aktiv = True if eintrag.system else bool(aktiv)
    db.commit()
    return _auswahl_redirect(typ, "saved=1")


@router.post("/kataloge/auswahl/{typ}/{eintrag_id}/loeschen")
def auswahl_loeschen(
    typ: str,
    eintrag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    if typ not in _AUSWAHL_TYPEN:
        raise HTTPException(status_code=404, detail="Unbekannte Auswahlliste")
    eintrag = (
        db.query(ObjektAuswahl)
        .filter(ObjektAuswahl.id == eintrag_id, ObjektAuswahl.typ == typ)
        .first()
    )
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    if eintrag.system:
        return _auswahl_redirect(typ, "error=system")
    if _auswahl_in_use(db, typ, eintrag.code):
        return _auswahl_redirect(typ, "error=in_use")
    db.delete(eintrag)
    db.commit()
    return _auswahl_redirect(typ, "saved=1")


# ── Kataloge: Karten-Symbole (mit Bild-Upload) ─────────────────────────────────

def _symbol_redirect(status: str) -> RedirectResponse:
    return RedirectResponse(url=f"/objekte/kataloge?{status}&tab=symbole", status_code=303)


async def _symbol_bild_speichern(
    db: Session, symbol: ObjektSymbol, bild: UploadFile | None,
) -> str | None:
    """Speichert ein hochgeladenes Symbolbild und setzt bild_pfad. Gibt eine Fehlermeldung
    zurueck (oder None bei Erfolg / kein Upload)."""
    from app.services.objekt_symbol_service import store_symbol_bild
    if bild is None or not bild.filename or symbol.org_id is None:
        return None
    daten = await bild.read()
    if not daten:
        return None
    try:
        rel = store_symbol_bild(symbol.org_id, symbol.id, bild.filename, daten)
    except ValueError as exc:
        return str(exc)
    symbol.bild_pfad = rel
    return None


@router.post("/kataloge/symbole/neu")
async def symbol_neu(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    stil: str = Form("box"),
    text: str = Form(""),
    sort: int = Form(0),
    bild: UploadFile | None = File(None),
):
    from app.services.objekt_symbol_service import stil_gueltig
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    if not stil_gueltig(stil):
        stil = "box"
    basis = _slug_code(name)
    code = basis
    n = 2
    while db.query(ObjektSymbol).filter(ObjektSymbol.code == code).first():
        code = f"{basis[:37]}_{n}"
        n += 1
    symbol = ObjektSymbol(
        org_id=user.org_id, code=code, name=name.strip(), stil=stil,
        text=(text.strip()[:12] or None), sort=sort, aktiv=True, system=False,
    )
    db.add(symbol)
    db.flush()  # ID fuer den Bild-Dateinamen
    fehler = await _symbol_bild_speichern(db, symbol, bild)
    if fehler:
        db.rollback()
        return _symbol_redirect("error=bild")
    if stil == "bild" and not symbol.bild_pfad:
        db.rollback()
        return _symbol_redirect("error=bild_fehlt")
    db.commit()
    return _symbol_redirect("saved=1")


@router.post("/kataloge/symbole/{symbol_id}/edit")
async def symbol_edit(
    symbol_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    name: str = Form(...),
    stil: str = Form("box"),
    text: str = Form(""),
    sort: int = Form(0),
    aktiv: str = Form(""),
    bild: UploadFile | None = File(None),
):
    from app.services.objekt_symbol_service import delete_symbol_bild, stil_gueltig
    symbol = db.query(ObjektSymbol).filter(ObjektSymbol.id == symbol_id).first()
    if symbol is None:
        raise HTTPException(status_code=404, detail="Symbol nicht gefunden")
    if not stil_gueltig(stil):
        stil = symbol.stil
    symbol.name = name.strip()
    symbol.stil = stil
    symbol.text = text.strip()[:12] or None
    symbol.sort = sort
    symbol.aktiv = True if symbol.system else bool(aktiv)
    fehler = await _symbol_bild_speichern(db, symbol, bild)
    if fehler:
        db.rollback()
        return _symbol_redirect("error=bild")
    if stil == "bild" and not symbol.bild_pfad:
        db.rollback()
        return _symbol_redirect("error=bild_fehlt")
    # Bild verwerfen, wenn der Stil weg von 'bild' gewechselt wurde
    if stil != "bild" and symbol.bild_pfad:
        delete_symbol_bild(symbol.bild_pfad)
        symbol.bild_pfad = None
    db.commit()
    return _symbol_redirect("saved=1")


@router.post("/kataloge/symbole/{symbol_id}/loeschen")
def symbol_loeschen(
    symbol_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.objekt_symbol_service import delete_symbol_bild
    symbol = db.query(ObjektSymbol).filter(ObjektSymbol.id == symbol_id).first()
    if symbol is None:
        raise HTTPException(status_code=404, detail="Symbol nicht gefunden")
    if symbol.system:
        return _symbol_redirect("error=system")
    in_use = db.query(ObjektKartenObjekt).filter(ObjektKartenObjekt.typ == symbol.code).first()
    if in_use:
        return _symbol_redirect("error=in_use")
    delete_symbol_bild(symbol.bild_pfad)
    db.delete(symbol)
    db.commit()
    return _symbol_redirect("saved=1")


# ── Abschnitt: Lagekarte (PR4) ─────────────────────────────────────────────────

def _karten_objekt_dict(k: ObjektKartenObjekt) -> dict:
    from app.models.objekt import parse_karten_geometry
    return {
        "id": k.id,
        "typ": k.typ,
        "lat": k.lat,
        "lng": k.lng,
        "geometry": parse_karten_geometry(k.geometry_json),
        "label": k.label,
    }


@router.get("/{objekt_id}/karte", response_class=HTMLResponse)
def karte_editor(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.services.objekt_symbol_service import lade_symbol_labels
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _detail_context(request, db, user, objekt)
    ctx["symbol_typen"] = lade_symbol_labels(db, objekt.org_id)
    return templates.TemplateResponse(request, "objekt/karte.html", ctx)


@router.get("/{objekt_id}/karte/einbettung", response_class=HTMLResponse)
def karte_readonly_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/_karte_readonly.html", _detail_context(request, db, user, objekt)
    )


@router.get("/{objekt_id}/karte/tab", response_class=HTMLResponse)
def karte_tab_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    """Lagekarte als editierbarer Detail-Tab (Palette + Editor inline).

    Verwalter bearbeiten direkt im Tab (editierbar), Leserollen sehen sie schreibgeschützt.
    Wird lazy bei Tab-Aktivierung geladen (siehe detail.html).
    """
    from app.services.objekt_symbol_service import lade_symbol_labels
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _detail_context(request, db, user, objekt)
    ctx["symbol_typen"] = lade_symbol_labels(db, objekt.org_id)
    return templates.TemplateResponse(request, "objekt/_karte_tab.html", ctx)


@router.get("/{objekt_id}/karte/objekte.json")
def karten_objekte_json(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    eintraege = (
        db.query(ObjektKartenObjekt)
        .filter(ObjektKartenObjekt.objekt_id == objekt.id)
        .order_by(ObjektKartenObjekt.sort, ObjektKartenObjekt.id)
        .all()
    )
    return {
        "objekt": {"id": objekt.id, "lat": objekt.lat, "lng": objekt.lng,
                   "name": objekt.name},
        "eintraege": [_karten_objekt_dict(k) for k in eintraege],
    }


@router.get("/{objekt_id}/hydranten.json")
async def objekt_hydranten(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    """Löschwasser-Entnahmestellen (OSM/OSMHydrant) um die Objektkoordinaten."""
    from app.config import settings
    from app.models.master import OrgSettings
    from app.services.hydrant_service import (
        fetch_osm_hydranten,
        manuelle_objekt_hydranten,
        merge_hydranten,
    )

    objekt = _objekt_or_404(db, objekt_id, user)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == objekt.org_id).first()
    enabled = settings.HYDRANT_ENABLED and (
        org_settings is None or org_settings.hydrant_layer_enabled
    )
    osm: list = []
    if enabled and objekt.lat is not None and objekt.lng is not None:
        osm = await fetch_osm_hydranten(objekt.lat, objekt.lng)
    karten = (
        db.query(ObjektKartenObjekt)
        .filter(
            ObjektKartenObjekt.objekt_id == objekt.id,
            ObjektKartenObjekt.typ.in_(("hydrant_ueberflur", "hydrant_unterflur")),
        )
        .all()
    )
    manuell = manuelle_objekt_hydranten(karten, objekt.lat, objekt.lng)
    return {"hydranten": merge_hydranten(osm, manuell), "stand": None}


@router.post("/{objekt_id}/karte/objekte")
async def karten_objekt_neu(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    import json as _json

    from app.models.objekt import OBJEKT_SYMBOL_TYPEN
    objekt = _objekt_or_404(db, objekt_id, user)
    daten = await request.json()
    typ = str(daten.get("typ", ""))
    if typ not in OBJEKT_SYMBOL_TYPEN and typ != "geometrie":
        raise HTTPException(status_code=400, detail="Unbekannter Symboltyp")
    geometry = daten.get("geometry")
    lat = daten.get("lat")
    lng = daten.get("lng")
    if geometry is None and (lat is None or lng is None):
        raise HTTPException(status_code=400, detail="lat/lng oder geometry erforderlich")

    max_sort = (
        db.query(ObjektKartenObjekt)
        .filter(ObjektKartenObjekt.objekt_id == objekt.id)
        .count()
    )
    eintrag = ObjektKartenObjekt(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        typ=typ,
        lat=float(lat) if lat is not None else None,
        lng=float(lng) if lng is not None else None,
        geometry_json=_json.dumps(geometry, ensure_ascii=False) if geometry else None,
        label=(str(daten.get("label") or "").strip() or None),
        sort=max_sort + 1,
    )
    db.add(eintrag)
    write_objekt_change(db, objekt.id, objekt.org_id, "karte", "symbol_neu",
                        before=None, after=typ, user_id=user.id)
    db.commit()
    return _karten_objekt_dict(eintrag)


@router.post("/{objekt_id}/karte/objekte/{eintrag_id}")
async def karten_objekt_update(
    objekt_id: int,
    eintrag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    import json as _json
    objekt = _objekt_or_404(db, objekt_id, user)
    eintrag = (
        db.query(ObjektKartenObjekt)
        .filter(ObjektKartenObjekt.id == eintrag_id, ObjektKartenObjekt.objekt_id == objekt.id)
        .first()
    )
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Kartenobjekt nicht gefunden")
    daten = await request.json()
    if "lat" in daten:
        eintrag.lat = float(daten["lat"]) if daten["lat"] is not None else None
    if "lng" in daten:
        eintrag.lng = float(daten["lng"]) if daten["lng"] is not None else None
    if "geometry" in daten:
        geometry = daten["geometry"]
        eintrag.geometry_json = _json.dumps(geometry, ensure_ascii=False) if geometry else None
    if "label" in daten:
        eintrag.label = (str(daten["label"] or "").strip() or None)
    db.commit()
    return _karten_objekt_dict(eintrag)


@router.post("/{objekt_id}/karte/objekte/{eintrag_id}/loeschen")
def karten_objekt_loeschen(
    objekt_id: int,
    eintrag_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    eintrag = (
        db.query(ObjektKartenObjekt)
        .filter(ObjektKartenObjekt.id == eintrag_id, ObjektKartenObjekt.objekt_id == objekt.id)
        .first()
    )
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Kartenobjekt nicht gefunden")
    write_objekt_change(db, objekt.id, objekt.org_id, "karte", "symbol_geloescht",
                        before=eintrag.typ, after=None, user_id=user.id)
    db.delete(eintrag)
    db.commit()
    return {"ok": True}


# ── PR5: Einsatz-Verknuepfung (Board-Panel) ────────────────────────────────────
# Match bestaetigen/loesen: incident_leader ODER objekt_verwalter (Entscheidung).

_MATCH_ROLLEN = ("incident_leader", "objekt_verwalter")


def _panel_context(request: Request, db: Session, user: User, incident_id: int) -> dict:
    from sqlalchemy import case, func

    from app.models.incident import Incident
    from app.models.objekt import (
        OBJEKT_EINSATZ_QUELLEN,
        OBJEKT_INFO_FEHLER,
        OBJEKT_INFO_GESENDET,
        ObjektEinsatz,
        ObjektKontaktBenachrichtigung,
    )

    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    # Explizites Scoping ueber die Org DES EINSATZES, nicht ueber user.org_id: bei
    # system_admin ist user.org_id NULL (models/user.py:38) bzw. beim Org-Wechsel per
    # ?org= die Heimat-Org des Admins - beides wuerde das Panel faelschlich leeren.
    # Der Einsatz selbst ist oben bereits tenant-gefiltert geladen.
    panel_org_id = incident.primary_org_id

    verknuepfungen = (
        db.query(ObjektEinsatz)
        .options(selectinload(ObjektEinsatz.objekt).selectinload(Objekt.gefahren),
                 selectinload(ObjektEinsatz.objekt).selectinload(Objekt.bma),
                 selectinload(ObjektEinsatz.objekt).selectinload(Objekt.kontakte))
        .filter(ObjektEinsatz.incident_id == incident_id, ObjektEinsatz.org_id == panel_org_id)
        .order_by(ObjektEinsatz.status, ObjektEinsatz.erstellt_am)
        .all()
    )
    verknuepfte_ids = {v.objekt_id for v in verknuepfungen}
    kandidaten = (
        nur_produktiv(db.query(Objekt))
        .options(selectinload(Objekt.bma))
        .filter(Objekt.status.in_(("freigegeben", "in_ueberarbeitung")))
        .order_by(Objekt.nummer)
        .all()
    )
    kandidaten = [o for o in kandidaten if o.id not in verknuepfte_ids]
    benachrichtigungen = {
        objekt_id: {"gesendet": gesendet or 0, "fehler": fehler or 0}
        for objekt_id, gesendet, fehler in db.query(
            ObjektKontaktBenachrichtigung.objekt_id,
            func.sum(case((ObjektKontaktBenachrichtigung.status == OBJEKT_INFO_GESENDET, 1), else_=0)),
            func.sum(case((ObjektKontaktBenachrichtigung.status == OBJEKT_INFO_FEHLER, 1), else_=0)),
        ).filter(
            ObjektKontaktBenachrichtigung.org_id == panel_org_id,
            ObjektKontaktBenachrichtigung.incident_id == incident_id,
            ObjektKontaktBenachrichtigung.objekt_id.in_(verknuepfte_ids),
        ).group_by(ObjektKontaktBenachrichtigung.objekt_id).all()
    } if verknuepfte_ids else {}
    hat_empfaenger = {
        v.objekt_id: any(
            (k.benachrichtigung_mail and bool((k.email or "").strip())) or bool(k.sms_nummern)
            for k in (v.objekt.kontakte if v.objekt else [])
            if k.org_id == panel_org_id
        )
        for v in verknuepfungen
    }
    if incident.lat is not None and incident.lng is not None:
        # Vorschläge nach Entfernung zum Einsatzort sortieren statt nach Objekt-Nr. —
        # bei der manuellen Verknüpfung sind die nächstgelegenen Objekte am relevantesten.
        from app.services.hydrant_service import _haversine_m
        inc_lat, inc_lng = incident.lat, incident.lng

        def _distanz(o: Objekt) -> float:
            if o.lat is None or o.lng is None:
                return float("inf")
            return _haversine_m(inc_lat, inc_lng, o.lat, o.lng)

        kandidaten = sorted(kandidaten, key=_distanz)
    return {
        "user": user,
        "incident": incident,
        "verknuepfungen": verknuepfungen,
        "quellen_labels": OBJEKT_EINSATZ_QUELLEN,
        "kandidaten": kandidaten,
        "benachrichtigungen": benachrichtigungen,
        "hat_empfaenger": hat_empfaenger,
        "darf_verknuepfen": is_objekt_verwalter(user) or any(
            r.code in ("incident_leader",) for r in user.roles
        ),
        "gefahr_piktogramme": lade_auswahl(db, user.org_id, AUSWAHL_PIKTOGRAMM),
        "gefahr_links": gefahr_links,
    }


def _panel_template(view: str | None) -> str:
    """Wählt das Objekt-Panel-Template: Board-Sidebar (default) oder Einsatzinfo-Seite.

    Beide Templates rendern aus demselben _panel_context (verknuepfungen, kandidaten,
    darf_verknuepfen, …) – so kann direkt auf der Einsatzinfo-Seite verknüpft/gelöst/
    bestätigt werden, ohne den Backend-Code zu duplizieren (view="info")."""
    return "incident/_ei_objekt_section.html" if view == "info" else "incident/_objekt_panel.html"


@router.get("/einsatz-panel/{incident_id}", response_class=HTMLResponse)
def einsatz_panel(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    view: str = "",
):
    return templates.TemplateResponse(
        request, _panel_template(view),
        _panel_context(request, db, user, incident_id),
    )


@router.post("/einsatz-panel/{incident_id}/verknuepfen", response_class=HTMLResponse)
async def einsatz_manuell_verknuepfen(
    incident_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_MATCH_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    objekt_id: int = Form(...),
    view: str = Form(""),
):
    from app.models.incident import Incident
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz
    from app.services.objekt_matching_service import erzeuge_gefahren_meldungen

    objekt = _objekt_or_404(db, objekt_id, user)
    existiert = (
        db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.incident_id == incident_id, ObjektEinsatz.objekt_id == objekt.id)
        .first()
    )
    if not existiert:
        db.add(ObjektEinsatz(
            org_id=objekt.org_id,
            objekt_id=objekt.id,
            incident_id=incident_id,
            quelle="manuell",
            status=OBJEKT_EINSATZ_BESTAETIGT,
            bestaetigt_von_id=user.id,
        ))
        write_audit(db, "objekt.einsatz_verknuepft", org_id=user.org_id, user_id=user.id,
                    entity_type="objekt", entity_id=objekt.id,
                    incident_id=incident_id, payload={"quelle": "manuell"})
        # Objektgefahren als Board-Meldungen (idempotent) + Board neu laden
        incident = db.get(Incident, incident_id)
        if incident is not None:
            erzeuge_gefahren_meldungen(db, incident, objekt)
        db.commit()
        from app.services.objekt_kontakt_notify import dispatch_objekt_einsatzinfo
        background_tasks.add_task(
            dispatch_objekt_einsatzinfo, incident_id, triggered_by_user_id=user.id
        )
        from app.services.print_dispatcher import autoprint_incident_updated_background
        background_tasks.add_task(autoprint_incident_updated_background, incident_id)
        try:
            from app.services.broadcast import manager
            await manager.broadcast(incident_id, {"type": "objektgefahren", "reload_board": True})
        except Exception:
            pass
    return templates.TemplateResponse(
        request, _panel_template(view),
        _panel_context(request, db, user, incident_id),
    )


@router.post("/einsatz-panel/{incident_id}/{verknuepfung_id}/bestaetigen", response_class=HTMLResponse)
def einsatz_match_bestaetigen(
    incident_id: int,
    verknuepfung_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_MATCH_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    view: str = Form(""),
):
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz

    verknuepfung = (
        db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.id == verknuepfung_id, ObjektEinsatz.incident_id == incident_id)
        .first()
    )
    if verknuepfung is None:
        raise HTTPException(status_code=404, detail="Verknuepfung nicht gefunden")
    if verknuepfung.status != OBJEKT_EINSATZ_BESTAETIGT:
        verknuepfung.status = OBJEKT_EINSATZ_BESTAETIGT
        verknuepfung.bestaetigt_von_id = user.id
        write_audit(db, "objekt.einsatz_bestaetigt", org_id=user.org_id, user_id=user.id,
                    entity_type="objekt", entity_id=verknuepfung.objekt_id,
                    incident_id=incident_id, payload={"quelle": verknuepfung.quelle})
        db.commit()
        from app.services.objekt_kontakt_notify import dispatch_objekt_einsatzinfo
        background_tasks.add_task(
            dispatch_objekt_einsatzinfo,
            incident_id,
            objekt_ids=[verknuepfung.objekt_id],
            triggered_by_user_id=user.id,
        )
        from app.services.print_dispatcher import autoprint_incident_updated_background
        background_tasks.add_task(autoprint_incident_updated_background, incident_id)
    return templates.TemplateResponse(
        request, _panel_template(view),
        _panel_context(request, db, user, incident_id),
    )


@router.post(
    "/einsatz-panel/{incident_id}/{verknuepfung_id}/benachrichtigen",
    response_class=HTMLResponse,
)
def einsatz_kontakte_benachrichtigen(
    incident_id: int,
    verknuepfung_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_MATCH_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    view: str = Form(""),
):
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz
    from app.services.objekt_kontakt_notify import dispatch_objekt_einsatzinfo

    verknuepfung = db.query(ObjektEinsatz).filter(
        ObjektEinsatz.id == verknuepfung_id,
        ObjektEinsatz.incident_id == incident_id,
        ObjektEinsatz.status == OBJEKT_EINSATZ_BESTAETIGT,
    ).first()
    if verknuepfung is None:
        raise HTTPException(status_code=404, detail="Verknuepfung nicht gefunden")
    write_audit(
        db, "objekt.kontakt_info_manuell", org_id=user.org_id, user_id=user.id,
        incident_id=incident_id, entity_type="objekt", entity_id=verknuepfung.objekt_id,
    )
    db.commit()
    background_tasks.add_task(
        dispatch_objekt_einsatzinfo, incident_id,
        objekt_ids=[verknuepfung.objekt_id], force=True, triggered_by_user_id=user.id,
    )
    return templates.TemplateResponse(
        request, _panel_template(view), _panel_context(request, db, user, incident_id)
    )


@router.post("/einsatz-panel/{incident_id}/{verknuepfung_id}/loesen", response_class=HTMLResponse)
async def einsatz_match_loesen(
    incident_id: int,
    verknuepfung_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_MATCH_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    view: str = Form(""),
):
    from app.models.objekt import ObjektEinsatz
    from app.services.objekt_matching_service import entferne_gefahren_meldungen

    verknuepfung = (
        db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.id == verknuepfung_id, ObjektEinsatz.incident_id == incident_id)
        .first()
    )
    if verknuepfung is None:
        raise HTTPException(status_code=404, detail="Verknuepfung nicht gefunden")
    write_audit(db, "objekt.einsatz_geloest", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=verknuepfung.objekt_id,
                incident_id=incident_id, payload={"quelle": verknuepfung.quelle})
    # Zugehoerige Objektgefahren-Meldungen mit entfernen
    objekt = db.get(Objekt, verknuepfung.objekt_id)
    entfernt = entferne_gefahren_meldungen(db, incident_id, objekt) if objekt else 0
    db.delete(verknuepfung)
    db.commit()
    if entfernt:
        try:
            from app.services.broadcast import manager
            await manager.broadcast(incident_id, {"type": "objektgefahren", "reload_board": True})
        except Exception:
            pass
    return templates.TemplateResponse(
        request, _panel_template(view),
        _panel_context(request, db, user, incident_id),
    )


# ── PR5: Einsatzhistorie am Objekt ─────────────────────────────────────────────

@router.get("/{objekt_id}/einsaetze", response_class=HTMLResponse)
def einsaetze_partial(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    limit: int = 20,
):
    from app.models.incident import Incident
    from app.models.objekt import OBJEKT_EINSATZ_QUELLEN, ObjektEinsatz

    objekt = _objekt_or_404(db, objekt_id, user)
    verknuepfungen = (
        db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.objekt_id == objekt.id)
        .order_by(ObjektEinsatz.erstellt_am.desc())
        .limit(limit)
        .all()
    )
    incident_ids = [v.incident_id for v in verknuepfungen]
    incidents: dict[int, Incident] = {}
    if incident_ids:
        for inc in db.query(Incident).filter(Incident.id.in_(incident_ids)).all():
            incidents[inc.id] = inc

    ctx = _detail_context(request, db, user, objekt)
    ctx["verknuepfungen"] = verknuepfungen
    ctx["incidents"] = incidents
    ctx["quellen_labels"] = OBJEKT_EINSATZ_QUELLEN
    return templates.TemplateResponse(request, "objekt/_einsaetze.html", ctx)


# ── PR5: Mobile Einsatzansicht ─────────────────────────────────────────────────

def _dok_zaehler(db: Session, objekt_id: int) -> dict[str, int]:
    """Seitenzahl je Dokumentart (fuer die Dokument-Kacheln der Einsatzansicht)."""
    from sqlalchemy import func as _func

    from app.models.objekt import ObjektDokumentSeite
    return {
        code: cnt
        for code, cnt in (
            db.query(ObjektDokumentSeite.dokumentart, _func.count(ObjektDokumentSeite.id))
            .filter(ObjektDokumentSeite.objekt_id == objekt_id,
                    ObjektDokumentSeite.dokumentart.isnot(None))
            .group_by(ObjektDokumentSeite.dokumentart)
            .all()
        )
        if code is not None
    }


def _dok_gesamt(db: Session, objekt_id: int) -> int:
    """Gesamtzahl der Dokumentseiten (auch unklassifizierte) fuer die Einsatzansicht."""
    from sqlalchemy import func as _func

    from app.models.objekt import ObjektDokumentSeite
    return (
        db.query(_func.count(ObjektDokumentSeite.id))
        .filter(ObjektDokumentSeite.objekt_id == objekt_id)
        .scalar()
    ) or 0


@router.get("/{objekt_id}/einsatz-fragment", response_class=HTMLResponse)
def einsatz_fragment(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    """Kompakter Objekt-Einsatzinhalt (ohne eigene Lagekarte) fuer die HTMX-Einbettung
    in die Einsatzinformation (incident/info.html)."""
    objekt = _objekt_or_404(db, objekt_id, user)
    ctx = _detail_context(request, db, user, objekt)
    ctx["dokumentarten"] = lade_auswahl(db, objekt.org_id, AUSWAHL_DOKUMENTART)
    ctx["dok_zaehler"] = _dok_zaehler(db, objekt.id)
    ctx["dok_gesamt"] = _dok_gesamt(db, objekt.id)
    ctx["kompakt"] = True
    return templates.TemplateResponse(request, "objekt/_einsatz_inhalt.html", ctx)


@router.get("/{objekt_id}/einsatz", response_class=HTMLResponse)
def einsatzansicht(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.models.incident import Incident
    from app.models.objekt import ObjektEinsatz

    objekt = _objekt_or_404(db, objekt_id, user)

    # Dokumentarten-Kacheln mit Seitenzahl
    dok_zaehler = _dok_zaehler(db, objekt.id)

    # Einsatzhistorie (letzte 10)
    verknuepfungen = (
        db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.objekt_id == objekt.id)
        .order_by(ObjektEinsatz.erstellt_am.desc())
        .limit(10)
        .all()
    )
    incidents: dict[int, Incident] = {}
    ids = [v.incident_id for v in verknuepfungen]
    if ids:
        for inc in db.query(Incident).filter(Incident.id.in_(ids)).all():
            incidents[inc.id] = inc

    ctx = _detail_context(request, db, user, objekt)
    ctx["dokumentarten"] = lade_auswahl(db, objekt.org_id, AUSWAHL_DOKUMENTART)
    ctx["dok_zaehler"] = dok_zaehler
    ctx["dok_gesamt"] = _dok_gesamt(db, objekt.id)
    ctx["verknuepfungen"] = verknuepfungen
    ctx["incidents"] = incidents
    return templates.TemplateResponse(request, "objekt/einsatz.html", ctx)


# ── PR7: Druck (Objektblatt + Mappe) ───────────────────────────────────────────

def _org_fuer_user(db: Session, user: User):
    from app.models.master import FireDept
    if user.org_id is None:
        return None
    return db.query(FireDept).filter(FireDept.id == user.org_id).first()


@router.get("/{objekt_id}/objektblatt.pdf")
def objektblatt_pdf(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    anhang: int = 0,
    hinweise: int = 0,
):
    from fastapi.responses import Response

    from app.services.objekt_pdf_service import objektblatt_mit_anhang

    objekt = _objekt_or_404(db, objekt_id, user)
    pdf = objektblatt_mit_anhang(
        objekt, _org_fuer_user(db, user), db, str(request.base_url),
        mit_anhang=bool(anhang), mit_hinweisen=bool(hinweise),
    )
    # inline: Browser-PDF-Viewer zeigt direkt an (Speichern dort weiterhin moeglich)
    name = f"{objekt.anzeige_nummer}_Objektblatt.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@router.post("/druck")
def objekte_mappe_drucken(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    objekt_ids: str = Form(...),
    mit_anhang: str = Form(""),
):
    from fastapi.responses import Response

    from app.services.objekt_pdf_service import sammelmappe

    try:
        ids = [int(s) for s in objekt_ids.split(",") if s.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungueltige Auswahl") from None
    if not ids:
        raise HTTPException(status_code=400, detail="Keine Objekte ausgewaehlt")

    objekte = [_objekt_or_404(db, oid, user) for oid in ids]
    pdf = sammelmappe(
        objekte, _org_fuer_user(db, user), db, str(request.base_url),
        mit_anhang=bool(mit_anhang),
    )
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Objektmappe.pdf"'},
    )
