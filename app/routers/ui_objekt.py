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
    OBJEKT_STATUS_LABELS,
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
    lade_auswahl,
    naechste_nummer,
    status_uebergang_erlaubt,
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
    kategorie: int | None = None,
    revision: str = "",
    merkmal: int | None = None,
):
    from sqlalchemy import ColumnElement, or_

    query = (
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
    if kategorie:
        query = query.filter(Objekt.kategorie_id == kategorie)
    if revision == "faellig":
        query = query.filter(Objekt.revision_datum.isnot(None), Objekt.revision_datum <= date.today())
    if merkmal:
        query = query.filter(
            Objekt.id.in_(
                db.query(ObjektMerkmal.objekt_id).filter(ObjektMerkmal.merkmal_id == merkmal)
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
        "filter_kategorie": kategorie,
        "filter_revision": revision,
        "filter_merkmal": merkmal,
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
    verwendet = db.query(Objekt).filter(Objekt.kategorie_id == kat.id).first()
    if verwendet:
        return RedirectResponse(url="/objekte/kataloge?error=in_use", status_code=303)
    db.delete(kat)
    db.commit()
    return RedirectResponse(url="/objekte/kataloge?saved=1", status_code=303)


# ── Objekt-Detail ──────────────────────────────────────────────────────────────

def _detail_context(request: Request, db: Session, user: User, objekt: Objekt) -> dict:
    from sqlalchemy import func as _func

    from app.models.objekt import ObjektDokumentSeite
    dokument_count = (
        db.query(_func.count(ObjektDokumentSeite.id))
        .filter(ObjektDokumentSeite.objekt_id == objekt.id)
        .scalar()
    ) or 0
    return {
        "user": user,
        "objekt": objekt,
        "kategorien": _kategorien(db),
        "status_labels": OBJEKT_STATUS_LABELS,
        "gefahr_piktogramme": lade_auswahl(db, objekt.org_id, AUSWAHL_PIKTOGRAMM),
        "kontakt_arten": lade_auswahl(db, objekt.org_id, AUSWAHL_KONTAKTART),
        "dokument_count": dokument_count,
        "vollstaendigkeit": berechne_vollstaendigkeit(
            objekt,
            kontakt_count=len(objekt.kontakte),
            gefahren_count=len(objekt.gefahren),
            dokument_count=dokument_count,
        ),
        "ist_verwalter": is_objekt_verwalter(user),
    }


@router.get("/{objekt_id}", response_class=HTMLResponse)
def objekt_detail(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    return templates.TemplateResponse(
        request, "objekt/detail.html", _detail_context(request, db, user, objekt)
    )


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


# ── Objekt loeschen (org_admin/system_admin) ───────────────────────────────────

def _loesche_objekt(db: Session, objekt: Objekt, user: User) -> None:
    """Loescht ein Objekt vollstaendig: erst alle Dokumente ueber den Service
    (Dateien auf Platte + Storage-Quota-Freigabe, siehe delete_dokument), dann
    das Objekt selbst (Kind-Zeilen via DB-Kaskade). Commit macht der Aufrufer."""
    from app.models.objekt import ObjektDokument
    from app.services.objekt_dokument_service import delete_dokument

    dokumente = (
        db.query(ObjektDokument)
        .filter(ObjektDokument.objekt_id == objekt.id)
        .all()
    )
    for dokument in dokumente:
        delete_dokument(dokument, db)

    write_audit(db, "objekt.deleted", org_id=objekt.org_id, user_id=user.id,
                entity_type="objekt", entity_id=objekt.id,
                payload={"name": objekt.name, "nummer": objekt.nummer,
                         "dokumente_geloescht": len(dokumente)})
    db.delete(objekt)


@router.post("/bulk-loeschen")
def objekte_bulk_loeschen(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
    objekt_ids: str = Form(""),
):
    """Loescht mehrere Objekte aus der Listen-Auswahl (nur org_admin/system_admin)."""
    ids = [int(t) for t in objekt_ids.split(",") if t.strip().isdigit()]
    for objekt_id in ids:
        objekt = db.query(Objekt).filter(Objekt.id == objekt_id).first()
        if objekt is None:
            continue  # fremde Org (Tenant-Filter) oder bereits geloescht
        _loesche_objekt(db, objekt, user)
    db.commit()
    return RedirectResponse(url="/objekte/", status_code=303)


@router.post("/{objekt_id}/loeschen")
def objekt_loeschen(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("org_admin")),
    _guard: None = Depends(require_objekt_enabled),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    _loesche_objekt(db, objekt, user)
    db.commit()
    return RedirectResponse(url="/objekte/", status_code=303)


# ── Abschnitt: Gefahren ────────────────────────────────────────────────────────

def _gefahren_katalog(db: Session) -> list[GefahrenKatalog]:
    return (
        db.query(GefahrenKatalog)
        .filter(GefahrenKatalog.aktiv.is_(True))
        .order_by(GefahrenKatalog.sort, GefahrenKatalog.name)
        .all()
    )


@router.get("/{objekt_id}/gefahren", response_class=HTMLResponse)
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


@router.post("/{objekt_id}/gefahren/neu", response_class=HTMLResponse)
def gefahr_neu(
    objekt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("objekt_verwalter")),
    _guard: None = Depends(require_objekt_enabled),
    gefahr_id: int = Form(...),
    un_nummer: str = Form(""),
    detail: str = Form(""),
):
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


@router.post("/{objekt_id}/gefahren/{gefahr_eintrag_id}/loeschen", response_class=HTMLResponse)
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


@router.get("/{objekt_id}/merkmale", response_class=HTMLResponse)
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


@router.post("/{objekt_id}/merkmale/neu", response_class=HTMLResponse)
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


@router.post("/{objekt_id}/merkmale/{zuordnung_id}/loeschen", response_class=HTMLResponse)
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

def _telefone_to_json(telefone_raw: str) -> str | None:
    import json as _json
    nummern = [t.strip() for t in telefone_raw.replace(";", ",").split(",") if t.strip()]
    return _json.dumps(nummern, ensure_ascii=False) if nummern else None


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
    telefone: str = Form(""),
    email: str = Form(""),
    erreichbarkeit: str = Form(""),
):
    objekt = _objekt_or_404(db, objekt_id, user)
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    if art not in lade_auswahl(db, objekt.org_id, AUSWAHL_KONTAKTART):
        art = "sonstig"
    max_sort = max([k.sort for k in objekt.kontakte], default=0)
    kontakt = ObjektKontakt(
        org_id=objekt.org_id,
        objekt_id=objekt.id,
        art=art,
        name=name.strip(),
        telefone_json=_telefone_to_json(telefone),
        email=email.strip() or None,
        erreichbarkeit=erreichbarkeit.strip() or None,
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
    telefone: str = Form(""),
    email: str = Form(""),
    erreichbarkeit: str = Form(""),
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
    daten = {
        "art": art,
        "name": name.strip(),
        "telefone_json": _telefone_to_json(telefone),
        "email": email.strip() or None,
        "erreichbarkeit": erreichbarkeit.strip() or None,
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
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name ist erforderlich")
    if piktogramm_typ not in lade_auswahl(db, user.org_id, AUSWAHL_PIKTOGRAMM):
        piktogramm_typ = "sonstig"
    existiert = db.query(GefahrenKatalog).filter(GefahrenKatalog.name == name.strip()).first()
    if existiert:
        return RedirectResponse(url="/objekte/kataloge?error=exists&tab=gefahren", status_code=303)
    db.add(GefahrenKatalog(org_id=user.org_id, name=name.strip(),
                           piktogramm_typ=piktogramm_typ, sort=sort, aktiv=True))
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
):
    eintrag = db.query(GefahrenKatalog).filter(GefahrenKatalog.id == gefahr_id).first()
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Gefahr nicht gefunden")
    if piktogramm_typ not in lade_auswahl(db, user.org_id, AUSWAHL_PIKTOGRAMM):
        piktogramm_typ = "sonstig"
    eintrag.name = name.strip()
    eintrag.piktogramm_typ = piktogramm_typ
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
    import json as _json
    geometry = None
    if k.geometry_json:
        try:
            geometry = _json.loads(k.geometry_json)
        except (ValueError, TypeError):
            geometry = None
    return {
        "id": k.id,
        "typ": k.typ,
        "lat": k.lat,
        "lng": k.lng,
        "geometry": geometry,
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
    from app.models.incident import Incident
    from app.models.objekt import OBJEKT_EINSATZ_QUELLEN, ObjektEinsatz

    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    verknuepfungen = (
        db.query(ObjektEinsatz)
        .options(selectinload(ObjektEinsatz.objekt).selectinload(Objekt.gefahren),
                 selectinload(ObjektEinsatz.objekt).selectinload(Objekt.bma))
        .filter(ObjektEinsatz.incident_id == incident_id)
        .order_by(ObjektEinsatz.status, ObjektEinsatz.erstellt_am)
        .all()
    )
    verknuepfte_ids = {v.objekt_id for v in verknuepfungen}
    kandidaten = (
        db.query(Objekt)
        .filter(Objekt.status.in_(("freigegeben", "in_ueberarbeitung")))
        .order_by(Objekt.nummer)
        .all()
    )
    return {
        "user": user,
        "incident": incident,
        "verknuepfungen": verknuepfungen,
        "quellen_labels": OBJEKT_EINSATZ_QUELLEN,
        "kandidaten": [o for o in kandidaten if o.id not in verknuepfte_ids],
        "darf_verknuepfen": is_objekt_verwalter(user) or any(
            r.code in ("incident_leader",) for r in user.roles
        ),
        "gefahr_piktogramme": lade_auswahl(db, user.org_id, AUSWAHL_PIKTOGRAMM),
    }


@router.get("/einsatz-panel/{incident_id}", response_class=HTMLResponse)
def einsatz_panel(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_LESE_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    return templates.TemplateResponse(
        request, "incident/_objekt_panel.html",
        _panel_context(request, db, user, incident_id),
    )


@router.post("/einsatz-panel/{incident_id}/verknuepfen", response_class=HTMLResponse)
def einsatz_manuell_verknuepfen(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_MATCH_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
    objekt_id: int = Form(...),
):
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz

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
        db.commit()
    return templates.TemplateResponse(
        request, "incident/_objekt_panel.html",
        _panel_context(request, db, user, incident_id),
    )


@router.post("/einsatz-panel/{incident_id}/{verknuepfung_id}/bestaetigen", response_class=HTMLResponse)
def einsatz_match_bestaetigen(
    incident_id: int,
    verknuepfung_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_MATCH_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.models.objekt import OBJEKT_EINSATZ_BESTAETIGT, ObjektEinsatz

    verknuepfung = (
        db.query(ObjektEinsatz)
        .filter(ObjektEinsatz.id == verknuepfung_id, ObjektEinsatz.incident_id == incident_id)
        .first()
    )
    if verknuepfung is None:
        raise HTTPException(status_code=404, detail="Verknuepfung nicht gefunden")
    verknuepfung.status = OBJEKT_EINSATZ_BESTAETIGT
    verknuepfung.bestaetigt_von_id = user.id
    write_audit(db, "objekt.einsatz_bestaetigt", org_id=user.org_id, user_id=user.id,
                entity_type="objekt", entity_id=verknuepfung.objekt_id,
                incident_id=incident_id, payload={"quelle": verknuepfung.quelle})
    db.commit()
    return templates.TemplateResponse(
        request, "incident/_objekt_panel.html",
        _panel_context(request, db, user, incident_id),
    )


@router.post("/einsatz-panel/{incident_id}/{verknuepfung_id}/loesen", response_class=HTMLResponse)
def einsatz_match_loesen(
    incident_id: int,
    verknuepfung_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_MATCH_ROLLEN)),
    _guard: None = Depends(require_objekt_enabled),
):
    from app.models.objekt import ObjektEinsatz

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
    db.delete(verknuepfung)
    db.commit()
    return templates.TemplateResponse(
        request, "incident/_objekt_panel.html",
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
