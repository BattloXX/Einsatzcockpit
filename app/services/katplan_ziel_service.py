"""Stammdatenziele fuer Katastrophenplaene, ohne Abhaengigkeit vom Planservice."""

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.dependencies import _set_module_states
from app.models.kontakt import Kontakt, KontaktOrganisation
from app.models.master import VehicleMaster
from app.models.objekt import Objekt
from app.models.wasserstelle import Wasserstelle
from app.services import kontakt_service
from app.services.objekt_pflege_schreiben_service import suche_objekte
from app.services.objekt_service import objekt_effective_enabled

_ZIELE = {"kontakt": Kontakt, "kontakt_organisation": KontaktOrganisation, "objekt": Objekt,
          "fahrzeug": VehicleMaster, "wasserstelle": Wasserstelle}


def ziel_laden(db: Session, org_id: int | None, ziel_typ: str, ziel_id: int) -> Any:
    if org_id is None or ziel_typ not in _ZIELE:
        return None
    modell: Any = _ZIELE[ziel_typ]
    query = db.query(modell).filter(modell.id == ziel_id)
    if ziel_typ == "fahrzeug":
        query = query.filter(VehicleMaster.dept_id == org_id)
    else:
        query = query.filter(modell.org_id == org_id)
    ziel = query.one_or_none()
    if ziel is None or ziel in db.deleted:
        return None
    # Die Session hat autoflush=False. Auch noch nicht gespeicherte Sperren
    # muessen sofort gelten; SQL-Filter allein wuerden den alten DB-Wert sehen.
    if isinstance(ziel, VehicleMaster):
        return ziel if ziel.dept_id == org_id and not ziel.deleted and ziel.active else None
    if ziel.org_id != org_id:
        return None
    if isinstance(ziel, Objekt):
        return ziel if ziel.entwurf_von_id is None and ziel.status != "archiviert" else None
    if not ziel.aktiv or (isinstance(ziel, Kontakt) and ziel.archiviert):
        return None
    return ziel


def ziel_verfuegbar(db: Session, org_id: int | None, ziel_typ: str) -> bool:
    if org_id is None or ziel_typ not in _ZIELE:
        return False
    if ziel_typ in ("kontakt", "kontakt_organisation"):
        # Derselbe gebuendelte Helfer wie request.state.kontakte_enabled.
        request = Request({"type": "http", "headers": []})
        request.state.kontakte_enabled = False
        _set_module_states(request, org_id, db)
        return bool(request.state.kontakte_enabled)
    if ziel_typ == "objekt":
        return objekt_effective_enabled(org_id, db)
    # Wasserstellen haben keinen Modulschalter im Bestand.
    return True


def _anzeige(ziel: Any) -> tuple[str, str]:
    if isinstance(ziel, Kontakt):
        return ziel.anzeigename, " / ".join(v for v in (ziel.funktion, ziel.organisation) if v)
    if isinstance(ziel, KontaktOrganisation):
        return ziel.name, ziel.kurzname or ziel.organisationstyp
    if isinstance(ziel, Objekt):
        return ziel.name, " ".join(v for v in (ziel.strasse, ziel.hausnummer, ziel.plz, ziel.ort) if v)
    if isinstance(ziel, VehicleMaster):
        return ziel.name, " / ".join(v for v in (ziel.code, ziel.funkrufname) if v)
    if isinstance(ziel, Wasserstelle):
        return ziel.bezeichnung, ziel.typ_label
    return "Ziel nicht verfügbar", ""


def ziel_suchen(db: Session, org_id: int, ziel_typ: str, q: str, limit: int = 20) -> list[dict]:
    if limit <= 0 or not ziel_verfuegbar(db, org_id, ziel_typ):
        return []
    limit = min(limit, 200)
    werte: list[Any]
    if ziel_typ == "kontakt":
        # list_kontakte paginiert. Org/aktiv auch ohne Tenant-Kontext explizit pruefen.
        werte = []
        page = 1
        while len(werte) < limit:
            kontakte, total = kontakt_service.list_kontakte(db, q=q, page=page)
            werte.extend(k for k in kontakte if k.org_id == org_id and k.aktiv and not k.archiviert)
            if not kontakte or page * kontakt_service.PRO_SEITE >= total:
                break
            page += 1
    elif ziel_typ == "objekt":
        werte = [o for o in suche_objekte(db, org_id, q, "", limit) if o.status != "archiviert"]
    else:
        modell: Any = _ZIELE[ziel_typ]
        term = f"%{q.strip()}%"
        query = db.query(modell)
        if ziel_typ == "kontakt_organisation":
            query = query.filter(modell.org_id == org_id, modell.aktiv.is_(True),
                                 or_(modell.name.ilike(term), modell.kurzname.ilike(term))).order_by(
                                     modell.name, modell.id)
        elif ziel_typ == "fahrzeug":
            query = query.filter(modell.dept_id == org_id, modell.deleted.is_(False), modell.active.is_(True),
                                 or_(modell.name.ilike(term), modell.code.ilike(term),
                                     modell.funkrufname.ilike(term))).order_by(modell.name, modell.id)
        else:
            query = query.filter(modell.org_id == org_id, modell.aktiv.is_(True),
                                 modell.bezeichnung.ilike(term)).order_by(modell.bezeichnung, modell.id)
        werte = query.limit(limit).all()
    return [{"id": z.id, "titel": _anzeige(z)[0], "untertitel": _anzeige(z)[1]} for z in werte[:limit]]

