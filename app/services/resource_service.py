"""Ressourcen-Disposition für Großschadenslagen (SKKM).

Einzige Registry: LageEinheit.
Pool = sector_id IS NULL (Reserve im SKKM-Sinne).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session, joinedload

from app.core.audit import write_audit
from app.core.telefon import telefon_maske, telefon_zu_e164_at
from app.models.major_incident import (
    EINHEIT_STATUS_AKTIV,
    EINHEIT_STATUS_COLOR,
    EINHEIT_STATUS_LABEL,
    EinheitSiteDispatch,
    GslStaffAssignment,
    IncidentSite,
    LageEinheit,
    LageEinheitLeader,
    LageJournalEntry,
    MajorIncident,
    Sector,
    SiteLogEntry,
)
from app.models.master import Member, VehicleMaster

if TYPE_CHECKING:
    from app.services.gk_zugang_service import AutoSmsAuftrag

# ── Status-Konstanten ─────────────────────────────────────────────────────────

STATUS_ANGEFORDERT = "angefordert"
STATUS_BEREITGESTELLT = "bereitgestellt"
STATUS_IM_EINSATZ = "im_einsatz"
STATUS_ABGERUECKT = "abgerueckt"

VALID_STATUSES = {
    STATUS_ANGEFORDERT,
    STATUS_BEREITGESTELLT,
    STATUS_IM_EINSATZ,
    STATUS_ABGERUECKT,
}

STATUS_LABEL = {
    STATUS_ANGEFORDERT: "Angefordert",
    STATUS_BEREITGESTELLT: "Bereitgestellt",
    STATUS_IM_EINSATZ: "Im Einsatz",
    STATUS_ABGERUECKT: "Abgerückt",
}

# Welche Timestamp-Spalte wird bei Statuswechsel gesetzt?
_STATUS_TIMESTAMP = {
    STATUS_ANGEFORDERT: "requested_at",
    STATUS_BEREITGESTELLT: "arrived_at",
    STATUS_IM_EINSATZ: "committed_at",
    STATUS_ABGERUECKT: "released_at",
}

RESOURCE_TYPE_LABEL = {
    "fahrzeug": "Fahrzeug",
    "extern": "Externe Kräfte",
    "material": "Material/Gerät",
    "verband": "Verband",
}

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

RESSOURCE_CATEGORIES = {"ressource", "ressource_fhr", "ressource_manuell"}


def dispatch_aktiv_filter():
    """Disposition belegt die Einheit: nicht zurückgezogen und nicht beendet."""
    return and_(
        EinheitSiteDispatch.withdrawn_at.is_(None),
        EinheitSiteDispatch.beendet_at.is_(None),
    )


def _journal(
    db: Session,
    lage_id: int,
    text: str,
    category: str = "ressource",
    author_name: str | None = None,
    user_id: int | None = None,
    *,
    einheit_id: int | None = None,
    site_id: int | None = None,
    ereignis_typ: str | None = None,
    quelle: str | None = None,
) -> None:
    db.add(
        LageJournalEntry(
            major_incident_id=lage_id,
            ts=datetime.now(UTC),
            category=category,
            text=text,
            author_name=author_name,
            user_id=user_id,
            einheit_id=einheit_id,
            site_id=site_id,
            ereignis_typ=ereignis_typ,
            quelle=quelle,
        )
    )


def _get_einheit(db: Session, einheit_id: int, lage_id: int) -> LageEinheit:
    e = db.get(LageEinheit, einheit_id)
    if not e or e.lage_id != lage_id:
        raise ValueError("Einheit nicht gefunden")
    return e


def journal_eintrag_manuell(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    text: str,
    site_id: int | None = None,
    user_id: int,
    author_name: str,
) -> LageJournalEntry:
    """Erfasst einen unveraenderbaren manuellen Ressourceneintrag."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    text = text.strip()
    if not 1 <= len(text) <= 1000:
        raise ValueError("Text muss zwischen 1 und 1000 Zeichen lang sein")
    if site_id is not None:
        site = db.get(IncidentSite, site_id)
        if site is None or site.major_incident_id != lage.id:
            raise ValueError("Einsatzstelle nicht gefunden")
    entry = LageJournalEntry(
        major_incident_id=lage.id,
        ts=datetime.now(UTC),
        category="ressource_manuell",
        text=text,
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        site_id=site_id,
        ereignis_typ="manuell",
        quelle="manuell",
    )
    db.add(entry)
    db.flush()
    return entry


def journal_eintrag_stornieren(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    entry_id: int,
    *,
    grund: str,
    user_id: int,
    author_name: str,
) -> LageJournalEntry:
    """Markiert einen Ressourceneintrag nachpruefbar als storniert."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    grund = grund.strip()
    if not grund:
        raise ValueError("Stornogrund erforderlich")
    entry = db.get(LageJournalEntry, entry_id)
    if (
        entry is None
        or entry.major_incident_id != lage.id
        or entry.einheit_id != einheit.id
        or not entry.category.startswith("ressource")
    ):
        raise ValueError("Journaleintrag nicht gefunden")
    if entry.storniert_at is not None:
        raise ValueError("Journaleintrag bereits storniert")
    entry.storniert_at = datetime.now(UTC)
    entry.storniert_von = author_name
    entry.storno_grund = grund
    return entry


# ── Ressource anlegen ─────────────────────────────────────────────────────────


def add_resource(
    db: Session,
    lage_id: int,
    label: str,
    resource_type: str = "fahrzeug",
    *,
    vehicle_id: int | None = None,
    org_name: str | None = None,
    bos: str | None = None,
    qty: int | None = None,
    unit: str | None = None,
    is_from_org: bool = False,
    status: str = STATUS_BEREITGESTELLT,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    if resource_type not in RESOURCE_TYPE_LABEL:
        raise ValueError(f"Ungültiger resource_type: {resource_type}")
    if status not in VALID_STATUSES:
        raise ValueError(f"Ungültiger Status: {status}")

    now = datetime.now(UTC)
    e = LageEinheit(
        lage_id=lage_id,
        vehicle_id=vehicle_id,
        label=label.strip(),
        resource_type=resource_type,
        org_name=org_name,
        bos=bos,
        qty=qty,
        unit=unit,
        status=status,
        is_from_org=is_from_org,
        added_at=now,
        status_at=now,
    )
    if vehicle_id:
        from app.models.master import VehicleMaster

        vehicle = db.get(VehicleMaster, vehicle_id)
        if vehicle:
            e.funkrufname = vehicle.funkrufname
    if status == STATUS_ANGEFORDERT:
        e.requested_at = now
    else:
        e.arrived_at = now
    db.add(e)
    db.flush()

    _journal(
        db,
        lage_id,
        f"Ressource hinzugefügt: {label} [{RESOURCE_TYPE_LABEL.get(resource_type, resource_type)}]",
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=e.id,
        ereignis_typ="angelegt",
        quelle="manuell",
    )
    if status == STATUS_ABGERUECKT:
        from app.services.gk_zugang_service import widerrufe

        widerrufe(db, e.id, grund="abgerueckt", user_id=user_id)
    return e


@dataclass
class EinheitAnlegenErgebnis:
    """Ergebnis der atomar vorbereiteten Einheitserfassung.

    Die Funktion committet bewusst nicht. Der Aufrufer entscheidet damit ueber die
    Transaktionsgrenze und kann bei einem Fehler alle angelegten Teilobjekte
    zurueckrollen.
    """

    einheit: LageEinheit
    gk_ergebnis: GkErgebnis | None
    auto_sms: AutoSmsAuftrag | None
    warnungen: list[str]


def _eingabe_dict(wert: Mapping[str, Any] | None, name: str) -> dict[str, Any] | None:
    if wert is None:
        return None
    if not isinstance(wert, Mapping):
        raise ValueError(f"{name} muss ein Objekt sein")
    return dict(wert)


def lege_einheit_an(
    db: Session,
    lage: MajorIncident,
    *,
    resource_type: str,
    label: str,
    vehicle_id: int | None = None,
    org_name: str | None = None,
    bos: str | None = None,
    qty: int | None = None,
    unit: str | None = None,
    funkrufname: str | None = None,
    status: str | None = None,
    sektor_id: int | None = None,
    bereitstellungsraum: str | None = None,
    gk: Mapping[str, Any] | None = None,
    stellvertreter: Mapping[str, Any] | None = None,
    personal: Mapping[str, Any] | None = None,
    personen: Iterable[Mapping[str, Any]] | None = None,
    ausstattung: Iterable[Mapping[str, Any]] | None = None,
    bemerkung: str | None = None,
    user_id: int,
    author_name: str,
) -> EinheitAnlegenErgebnis:
    """Legt eine GSL-Einheit samt optionaler Stammdaten atomar im Caller-Commit an."""
    from app.services import ressource_pflege_service

    actual_label = label.strip()
    if not actual_label:
        raise ValueError("Bezeichnung fehlt")
    if resource_type not in RESOURCE_TYPE_LABEL:
        raise ValueError(f"Ungültiger resource_type: {resource_type}")
    actual_status = status or STATUS_BEREITGESTELLT
    if actual_status not in VALID_STATUSES:
        raise ValueError(f"Ungültiger Status: {actual_status}")

    vehicle: VehicleMaster | None = None
    if vehicle_id is not None:
        vehicle = db.get(VehicleMaster, vehicle_id)
        if vehicle is None:
            raise ValueError("Fahrzeug nicht gefunden")
        if vehicle.dept_id != lage.org_id:
            raise ValueError("Fahrzeug gehört nicht zur Organisation der Lage")
        duplicate_vehicle = db.query(LageEinheit.id).filter(
            LageEinheit.lage_id == lage.id,
            LageEinheit.vehicle_id == vehicle_id,
            LageEinheit.status != STATUS_ABGERUECKT,
        ).first()
        if duplicate_vehicle:
            raise ValueError("Fahrzeug ist bereits in der Lage")

    # Externe Kraefte und Material duerfen dieselbe Bezeichnung tragen, sofern
    # sie nicht derselben Organisation und demselben Typ zugeordnet sind.
    duplicate_label = db.query(LageEinheit.id).filter(
        LageEinheit.lage_id == lage.id,
        LageEinheit.resource_type == resource_type,
        func.lower(LageEinheit.label) == actual_label.casefold(),
        func.coalesce(LageEinheit.org_name, "") == (org_name or "").strip(),
    ).first()
    if duplicate_label:
        raise ValueError("Einheit mit Bezeichnung, Typ und Organisation ist bereits in der Lage")

    gk_daten = _eingabe_dict(gk, "GK")
    stv_daten = _eingabe_dict(stellvertreter, "Stellvertreter")
    personal_daten = _eingabe_dict(personal, "Personal")
    personen_liste = list(personen or [])
    ausstattung_liste = list(ausstattung or [])
    if any(not isinstance(person, Mapping) for person in personen_liste):
        raise ValueError("Personen müssen Objekte sein")
    if any(not isinstance(zeile, Mapping) for zeile in ausstattung_liste):
        raise ValueError("Ausstattung muss aus Objekten bestehen")

    einheit = add_resource(
        db, lage.id, actual_label, resource_type=resource_type, vehicle_id=vehicle_id,
        org_name=(org_name or "").strip() or None, bos=(bos or "").strip() or None,
        qty=qty, unit=(unit or "").strip() or None, status=actual_status,
        author_name=author_name, user_id=user_id,
    )
    aktualisiere_einheit_stamm(
        db, lage, einheit,
        funkrufname=einheit.funkrufname if funkrufname is None else funkrufname,
        org_name=org_name, bos=bos,
        bereitstellungsraum=bereitstellungsraum, qty=qty, unit=unit,
        user_id=user_id, author_name=author_name,
    )
    if sektor_id is not None:
        assign_to_sector(db, einheit.id, lage.id, sektor_id, author_name=author_name, user_id=user_id)

    gk_ergebnis: GkErgebnis | None = None
    if gk_daten is not None:
        gk_ergebnis = setze_gruppenkommandant(
            db, lage, einheit, member_id=gk_daten.get("member_id"),
            person_name=gk_daten.get("person_name"), telefon=gk_daten.get("telefon"),
            modus=gk_daten.get("modus", "auto"), user_id=user_id, author_name=author_name,
        )
    if stv_daten is not None:
        setze_stellvertreter(
            db, lage, einheit, member_id=stv_daten.get("member_id"),
            person_name=stv_daten.get("person_name"), telefon=stv_daten.get("telefon"),
            modus=stv_daten.get("modus", "auto"), user_id=user_id, author_name=author_name,
        )

    if personen_liste:
        ressource_pflege_service.modus_wechseln(
            db, lage, einheit, "liste", user_id=user_id, author_name=author_name,
        )
        for person in personen_liste:
            ressource_pflege_service.person_hinzufuegen(
                db, lage, einheit, member_id=person.get("member_id"), name=person.get("name"),
                funktion=person.get("funktion", "mannschaft"), qualifikationen=person.get("qualifikationen"),
                herkunft=person.get("herkunft", "frei"), bemerkung=person.get("bemerkung"),
                user_id=user_id, author_name=author_name,
            )
    elif personal_daten is not None:
        ressource_pflege_service.personal_setzen(
            db, lage, einheit, gesamt=personal_daten.get("gesamt", 0),
            fuehrung=personal_daten.get("fuehrung"), agt=personal_daten.get("agt"),
            sanitaeter=personal_daten.get("sanitaeter"),
            bemerkung=personal_daten.get("bemerkung", bemerkung),
            user_id=user_id, author_name=author_name,
        )
    elif bemerkung is not None:
        # Bemerkungen zur Mannschaft nutzen dieselbe Validierung und Auditspur.
        ressource_pflege_service.personal_setzen(
            db, lage, einheit, gesamt=0, bemerkung=bemerkung,
            user_id=user_id, author_name=author_name,
        )

    for zeile in ausstattung_liste:
        ressource_pflege_service.ausstattung_hinzufuegen(
            db, lage, einheit, kategorie=zeile.get("kategorie", ""),
            bezeichnung=zeile.get("bezeichnung"), menge=zeile.get("menge", 1),
            status=zeile.get("status", "einsatzbereit"), bemerkung=zeile.get("bemerkung"),
            ist_faehigkeit=zeile.get("ist_faehigkeit"), stamm_ref_typ=zeile.get("stamm_ref_typ"),
            stamm_ref_id=zeile.get("stamm_ref_id"), user_id=user_id, author_name=author_name,
        )

    write_audit(
        db, "gsl.einheit.angelegt", org_id=lage.org_id, user_id=user_id,
        entity_type="lage_einheit", entity_id=einheit.id,
        payload={"lage_id": lage.id, "einheit_id": einheit.id, "resource_type": resource_type},
    )
    return EinheitAnlegenErgebnis(
        einheit=einheit, gk_ergebnis=gk_ergebnis,
        auto_sms=gk_ergebnis.auto_sms if gk_ergebnis else None, warnungen=[],
    )


# ── Zuordnung Abschnitt / Einsatzstelle / Pool ────────────────────────────────


def sync_units_sector_to_site(db: Session, site: IncidentSite) -> int:
    """Gleicht LageEinheit.sector_id aller an dieser Einsatzstelle aktiv disponierten
    Einheiten mit dem aktuellen Abschnitt der Einsatzstelle ab (site.sector_id).

    Wird aufgerufen, wenn eine Einsatzstelle einem Abschnitt zugeordnet wird (manuell
    oder automatisch via Polygon), damit die Kräfteübersicht (die rein nach
    LageEinheit.sector_id gruppiert) die Ressourcen im richtigen Abschnitt zeigt.
    Committed nicht selbst — der Aufrufer committet. Gibt Anzahl geänderter Einheiten zurück.
    """
    einheit_ids = {
        d.einheit_id
        for d in db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.site_id == site.id,
            dispatch_aktiv_filter(),
        )
        .all()
    }
    einheit_ids.update(e.id for e in db.query(LageEinheit).filter(LageEinheit.incident_site_id == site.id).all())
    if not einheit_ids:
        return 0

    changed = 0
    for e in db.query(LageEinheit).filter(LageEinheit.id.in_(einheit_ids)).all():
        if e.sector_id != site.sector_id:
            e.sector_id = site.sector_id
            changed += 1
    return changed


def assign_to_sector(
    db: Session,
    einheit_id: int,
    lage_id: int,
    sector_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    e = _get_einheit(db, einheit_id, lage_id)

    # Abschnitt gehört zur Lage prüfen
    sector = db.get(Sector, sector_id)
    if not sector or sector.major_incident_id != lage_id:
        raise ValueError("Abschnitt nicht gefunden")

    e.sector_id = sector_id
    e.incident_site_id = None
    if e.status == STATUS_BEREITGESTELLT:
        e.status = STATUS_IM_EINSATZ
        e.committed_at = datetime.now(UTC)

    _journal(
        db,
        lage_id,
        f'{e.label} -> Abschnitt "{sector.name}" zugeordnet',
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=e.id,
        ereignis_typ="status",
        quelle="manuell",
    )
    return e


def assign_to_site(
    db: Session,
    einheit_id: int,
    lage_id: int,
    site_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    e = _get_einheit(db, einheit_id, lage_id)

    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise ValueError("Einsatzstelle nicht gefunden")

    # Neuem Dispatch-System übergeben: disponieren + sofort vor Ort
    dispatch_to_site(db, einheit_id, lage_id, site_id, author_name=author_name, user_id=user_id)
    _, conflict = set_vor_ort_at_site(db, einheit_id, lage_id, site_id, author_name=author_name, user_id=user_id)
    if conflict:
        # Altes Verhalten: Konflikt ignorieren, altes einfach überschreiben
        resolve_vor_ort_conflict(db, einheit_id, lage_id, site_id, author_name=author_name, user_id=user_id)
    return e


# ── Mehrfach-Disposition (Dispatch-System) ────────────────────────────────────


def dispatch_to_site(
    db: Session,
    einheit_id: int,
    lage_id: int,
    site_id: int,
    *,
    auftrag: str | None = None,
    reihenfolge: int | None = None,
    author_name: str | None = None,
    user_id: int | None = None,
) -> EinheitSiteDispatch:
    """Disponiert eine Einheit für eine Einsatzstelle (vor_ort_at=NULL = alarmiert).

    Fehler wenn bereits aktiv disponiert (weder abgezogen noch beendet).
    """
    e = _get_einheit(db, einheit_id, lage_id)
    if e.verband_id is not None:
        verband = db.get(LageEinheit, e.verband_id)
        label = verband.label if verband else str(e.verband_id)
        raise ValueError(f"Einheit gehoert zu Verband {label}; bitte den Verband disponieren")
    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise ValueError("Einsatzstelle nicht gefunden")
    db.flush()

    existing = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.einheit_id == einheit_id,
            EinheitSiteDispatch.site_id == site_id,
            dispatch_aktiv_filter(),
        )
        .first()
    )
    if existing:
        raise ValueError(f'Einheit bereits für "{site.bezeichnung}" disponiert')

    auftrag, reihenfolge = _validiere_auftragsdaten(auftrag, reihenfolge)
    now = datetime.now(UTC)
    dispatch = EinheitSiteDispatch(
        einheit_id=einheit_id,
        site_id=site_id,
        dispatched_at=now,
        status_at=now,
        dispatched_by=user_id,
        author_name=author_name,
        auftrag=auftrag,
        reihenfolge=reihenfolge,
    )
    db.add(dispatch)
    db.flush()

    if e.status == STATUS_BEREITGESTELLT:
        e.status = STATUS_IM_EINSATZ
        e.committed_at = now
        e.status_at = now

    _journal(
        db,
        lage_id,
        f'{e.label} → Einsatzstelle "{site.bezeichnung}" disponiert (DISPONIERT)',
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=e.id,
        site_id=site.id,
        ereignis_typ="disponiert",
        quelle="manuell",
    )
    return dispatch


def _validiere_auftragsdaten(
    auftrag: str | None,
    reihenfolge: int | None,
) -> tuple[str | None, int | None]:
    bereinigt = auftrag.strip() if auftrag else None
    if bereinigt and len(bereinigt) > 2000:
        raise ValueError("Auftrag darf höchstens 2000 Zeichen haben")
    if reihenfolge is not None and reihenfolge < 1:
        raise ValueError("Reihenfolge muss mindestens 1 sein")
    return bereinigt, reihenfolge


def aendere_auftrag(
    db: Session,
    dispatch: EinheitSiteDispatch,
    *,
    auftrag: str | None,
    reihenfolge: int | None,
    author_name: str | None,
    user_id: int | None,
) -> bool:
    """Ändert Auftragsdaten ohne selbst zu committen."""
    auftrag, reihenfolge = _validiere_auftragsdaten(auftrag, reihenfolge)
    if dispatch.auftrag == auftrag and dispatch.reihenfolge == reihenfolge:
        return False
    now = datetime.now(UTC)
    dispatch.auftrag = auftrag
    dispatch.reihenfolge = reihenfolge
    dispatch.version += 1
    dispatch.geaendert_at = now
    text = f"Auftrag geändert: {dispatch.einheit.label}"
    db.add(
        SiteLogEntry(
            incident_site_id=dispatch.site_id,
            kind="resource",
            text=text,
            user_id=user_id,
            author_name=author_name,
        )
    )
    _journal(
        db,
        dispatch.einheit.lage_id,
        text,
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=dispatch.einheit_id,
        site_id=dispatch.site_id,
        ereignis_typ="status",
        quelle="manuell",
    )
    return True


def oeffne_auftrag_wieder(
    db: Session,
    dispatch: EinheitSiteDispatch,
    *,
    author_name: str | None,
    user_id: int | None,
) -> None:
    """Öffnet einen beendeten Auftrag ohne selbst zu committen wieder."""
    if dispatch.beendet_at is None:
        raise ValueError("Auftrag ist nicht beendet")
    now = datetime.now(UTC)
    dispatch.einheit_status = "zugewiesen"
    dispatch.status_at = now
    dispatch.beendet_at = None
    dispatch.beendet_grund = None
    dispatch.version += 1
    dispatch.geaendert_at = now
    text = f"Auftrag wiedereröffnet: {dispatch.einheit.label}"
    db.add(
        SiteLogEntry(
            incident_site_id=dispatch.site_id,
            kind="resource",
            text=text,
            user_id=user_id,
            author_name=author_name,
        )
    )
    _journal(
        db,
        dispatch.einheit.lage_id,
        text,
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=dispatch.einheit_id,
        site_id=dispatch.site_id,
        ereignis_typ="status",
        quelle="manuell",
    )


def set_vor_ort_at_site(
    db: Session,
    einheit_id: int,
    lage_id: int,
    site_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> tuple[EinheitSiteDispatch | None, EinheitSiteDispatch | None]:
    """Markiert Einheit als 'vor Ort' an site_id.

    Gibt (dispatch, None) bei Erfolg zurück.
    Gibt (None, conflict_dispatch) zurück wenn Einheit bereits vor Ort an anderer Stelle —
    der Caller muss dann resolve_vor_ort_conflict() aufrufen nach Nutzerbestätigung.
    """
    e = _get_einheit(db, einheit_id, lage_id)
    site = db.get(IncidentSite, site_id)
    if not site or site.major_incident_id != lage_id:
        raise ValueError("Einsatzstelle nicht gefunden")
    db.flush()

    conflict = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.einheit_id == einheit_id,
            EinheitSiteDispatch.site_id != site_id,
            dispatch_aktiv_filter(),
            EinheitSiteDispatch.einheit_status.in_(EINHEIT_STATUS_AKTIV),
        )
        .first()
    )
    if conflict:
        return None, conflict

    dispatch = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.einheit_id == einheit_id,
            EinheitSiteDispatch.site_id == site_id,
            dispatch_aktiv_filter(),
        )
        .first()
    )
    if not dispatch:
        now_d = datetime.now(UTC)
        dispatch = EinheitSiteDispatch(
            einheit_id=einheit_id,
            site_id=site_id,
            dispatched_at=now_d,
            status_at=now_d,
            dispatched_by=user_id,
            author_name=author_name,
        )
        db.add(dispatch)
        db.flush()

    now = datetime.now(UTC)
    dispatch.vor_ort_at = now
    if dispatch.einheit_status != "in_arbeit":
        dispatch.einheit_status = "vor_ort"
    dispatch.status_at = now
    e.incident_site_id = site_id
    e.sector_id = site.sector_id
    if e.status == STATUS_BEREITGESTELLT:
        e.status = STATUS_IM_EINSATZ
        e.committed_at = datetime.now(UTC)

    _journal(
        db,
        lage_id,
        f'{e.label} → Einsatzstelle "{site.bezeichnung}" VOR ORT',
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=e.id,
        site_id=site.id,
        ereignis_typ="status",
        quelle="manuell",
    )
    return dispatch, None


def resolve_vor_ort_conflict(
    db: Session,
    einheit_id: int,
    lage_id: int,
    new_site_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> EinheitSiteDispatch:
    """Zieht Einheit von bisheriger Vor-Ort-Stelle ab und setzt Vor-Ort an new_site_id."""
    now = datetime.now(UTC)
    db.flush()
    old_dispatches = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.einheit_id == einheit_id,
            EinheitSiteDispatch.site_id != new_site_id,
            dispatch_aktiv_filter(),
            EinheitSiteDispatch.einheit_status.in_(EINHEIT_STATUS_AKTIV),
        )
        .all()
    )
    e = _get_einheit(db, einheit_id, lage_id)
    for d in old_dispatches:
        d.withdrawn_at = now
        old_site = db.get(IncidentSite, d.site_id)
        if old_site:
            _journal(
                db,
                lage_id,
                f'{e.label} von "{old_site.bezeichnung}" abgezogen (Verlegung)',
                category="ressource",
                author_name=author_name,
                user_id=user_id,
                einheit_id=e.id,
                site_id=old_site.id,
                ereignis_typ="zurueckgezogen",
                quelle="manuell",
            )
    db.flush()

    dispatch, _ = set_vor_ort_at_site(
        db,
        einheit_id,
        lage_id,
        new_site_id,
        author_name=author_name,
        user_id=user_id,
    )
    return dispatch  # type: ignore[return-value]


def withdraw_from_site(
    db: Session,
    einheit_id: int,
    lage_id: int,
    site_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
    grund: str | None = None,
) -> None:
    """Zieht Einheit von einer Einsatzstelle ab (withdrawn_at setzen)."""
    if grund is not None and len(grund.strip()) > 500:
        raise ValueError("Grund darf höchstens 500 Zeichen haben")
    e = _get_einheit(db, einheit_id, lage_id)
    dispatch = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.einheit_id == einheit_id,
            EinheitSiteDispatch.site_id == site_id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
        )
        .first()
    )
    if not dispatch:
        raise ValueError("Keine aktive Disposition für diese Einsatzstelle")

    now = datetime.now(UTC)
    dispatch.withdrawn_at = now
    dispatch.withdrawn_by = user_id
    dispatch.withdrawn_author = author_name
    dispatch.withdrawn_grund = (grund.strip() if grund else "") or None
    dispatch.version += 1
    dispatch.geaendert_at = now
    if e.incident_site_id == site_id:
        e.incident_site_id = None

    site = db.get(IncidentSite, site_id)
    text = f'{e.label} von "{site.bezeichnung if site else site_id}" abgezogen'
    if dispatch.withdrawn_grund:
        text += f" – {dispatch.withdrawn_grund}"
    _journal(
        db,
        lage_id,
        text,
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=e.id,
        site_id=site_id,
        ereignis_typ="zurueckgezogen",
        quelle="manuell",
    )


def get_active_dispatches_for_site(
    db: Session,
    site_id: int,
) -> list[EinheitSiteDispatch]:
    """Aktive (weder abgezogene noch beendete) Dispatches für eine Einsatzstelle."""
    db.flush()
    return (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.site_id == site_id,
            dispatch_aktiv_filter(),
        )
        .order_by(EinheitSiteDispatch.dispatched_at)
        .all()
    )


def get_dispatches_for_site_anzeige(
    db: Session,
    site_id: int,
) -> list[EinheitSiteDispatch]:
    """Nicht zurückgezogene Dispatches: aktive (nach Reihenfolge, dann Zeit) vor beendeten."""
    db.flush()
    dispatches = (
        db.query(EinheitSiteDispatch)
        .filter(
            EinheitSiteDispatch.site_id == site_id,
            EinheitSiteDispatch.withdrawn_at.is_(None),
        )
        .all()
    )
    # Sortierung in Python: wenige Zeilen je Stelle, vermeidet datenbankspezifische
    # NULL-Sortierung (SQLite vs. MariaDB).
    aktive = sorted(
        (d for d in dispatches if d.beendet_at is None),
        key=lambda d: (d.reihenfolge is None, d.reihenfolge or 0, d.dispatched_at),
    )
    beendete = sorted(
        (d for d in dispatches if d.beendet_at is not None),
        key=lambda d: d.beendet_at or datetime.min,
    )
    return aktive + beendete


def get_dispatch_counts_for_site(
    db: Session,
    site_id: int,
) -> dict[str, int]:
    """Liefert Alarm-, Vor-Ort- und Fertig-Zähler für eine Einsatzstelle."""
    return get_dispatch_counts_for_sites(db, [site_id])[site_id]


def get_dispatch_counts_for_sites(db: Session, site_ids: list[int]) -> dict[int, dict[str, int]]:
    """Liefert Alarm-, Vor-Ort- und Fertig-Zähler für mehrere Stellen in einer Abfrage."""
    if not site_ids:
        return {}
    db.flush()
    rows = (
        db.query(
            EinheitSiteDispatch.site_id,
            func.sum(case((and_(dispatch_aktiv_filter(), EinheitSiteDispatch.vor_ort_at.is_(None)), 1), else_=0)).label(
                "alarmed"
            ),
            func.sum(
                case((and_(dispatch_aktiv_filter(), EinheitSiteDispatch.vor_ort_at.is_not(None)), 1), else_=0)
            ).label("vor_ort"),
            func.sum(
                case(
                    (and_(EinheitSiteDispatch.withdrawn_at.is_(None), EinheitSiteDispatch.beendet_at.is_not(None)), 1),
                    else_=0,
                )
            ).label("fertig"),
        )
        .filter(
            EinheitSiteDispatch.site_id.in_(site_ids),
        )
        .group_by(EinheitSiteDispatch.site_id)
        .all()
    )
    counts = {site_id: {"alarmed": 0, "vor_ort": 0, "fertig": 0} for site_id in site_ids}
    for site_id, alarmed, vor_ort, fertig in rows:
        counts[site_id] = {
            "alarmed": int(alarmed or 0),
            "vor_ort": int(vor_ort or 0),
            "fertig": int(fertig or 0),
        }
    return counts


def get_einheiten_chips_for_sites(
    db: Session,
    sites: list[IncidentSite],
    *,
    org_settings: Any | None = None,
    jetzt: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    """Liefert die Einheiten-Chips und Rückmeldungswarnungen je Einsatzstelle.

    Die Einheiten werden mit den Dispositionen geladen, damit das Board nicht pro
    Karte weitere Queries auslöst. DB-Zeitstempel sind naive UTC-Werte.
    """
    if not sites:
        return {}

    db.flush()
    site_ids = [site.id for site in sites]
    dispatches = (
        db.query(EinheitSiteDispatch)
        .options(joinedload(EinheitSiteDispatch.einheit))
        .filter(
            EinheitSiteDispatch.site_id.in_(site_ids),
            EinheitSiteDispatch.withdrawn_at.is_(None),
        )
        .all()
    )
    by_site: dict[int, list[EinheitSiteDispatch]] = {site_id: [] for site_id in site_ids}
    for dispatch in dispatches:
        by_site.setdefault(dispatch.site_id, []).append(dispatch)

    now = jetzt or datetime.now(UTC).replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    from app.services import lagemeldung_service

    result: dict[int, dict[str, Any]] = {}
    for site in sites:
        site_dispatches = by_site.get(site.id, [])
        active = sorted(
            (dispatch for dispatch in site_dispatches if dispatch.beendet_at is None),
            key=lambda dispatch: (
                dispatch.reihenfolge is None,
                dispatch.reihenfolge or 0,
                dispatch.dispatched_at,
            ),
        )
        completed = sorted(
            (dispatch for dispatch in site_dispatches if dispatch.beendet_at is not None),
            key=lambda dispatch: dispatch.beendet_at or datetime.min,
        )
        chips = [
            {
                "einheit_id": dispatch.einheit_id,
                "label": dispatch.einheit.label if dispatch.einheit else "Einheit",
                "einheit_status": dispatch.einheit_status,
                "label_status": EINHEIT_STATUS_LABEL.get(dispatch.einheit_status, dispatch.einheit_status),
                "farbe": EINHEIT_STATUS_COLOR.get(dispatch.einheit_status, "muted"),
                "beendet": dispatch.beendet_at is not None,
            }
            for dispatch in active + completed
        ]

        ohne_rueckmeldung_min: int | None = None
        interval = lagemeldung_service.interval_minutes_for(site, org_settings)
        if interval is not None:
            overdue_minutes = []
            for dispatch in active:
                if dispatch.einheit_status not in EINHEIT_STATUS_AKTIV:
                    continue
                last_update = max(
                    timestamp
                    for timestamp in (
                        dispatch.letzte_rueckmeldung_at,
                        dispatch.status_at,
                        dispatch.dispatched_at,
                    )
                    if timestamp is not None
                )
                minutes = int((now - last_update).total_seconds() // 60)
                if minutes > interval:
                    overdue_minutes.append(minutes)
            if overdue_minutes:
                ohne_rueckmeldung_min = max(overdue_minutes)

        result[site.id] = {
            "chips": chips,
            "alle_fertig": bool(site_dispatches) and not active,
            "ohne_rueckmeldung_min": ohne_rueckmeldung_min,
        }
    return result


def move_to_pool(
    db: Session,
    einheit_id: int,
    lage_id: int,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    e = _get_einheit(db, einheit_id, lage_id)
    prev_sector = e.sector_id
    e.sector_id = None
    e.incident_site_id = None
    if e.status == STATUS_IM_EINSATZ:
        e.status = STATUS_BEREITGESTELLT
        e.status_at = datetime.now(UTC)

    _journal(
        db,
        lage_id,
        f"{e.label} → Pool/Reserve zurückgeführt" + (f" (war Abschnitt {prev_sector})" if prev_sector else ""),
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=e.id,
        ereignis_typ="status",
        quelle="manuell",
    )
    return e


# ── Status setzen ─────────────────────────────────────────────────────────────


def set_status(
    db: Session,
    einheit_id: int,
    lage_id: int,
    status: str,
    *,
    author_name: str | None = None,
    user_id: int | None = None,
) -> LageEinheit:
    if status not in VALID_STATUSES:
        raise ValueError(f"Ungültiger Status: {status}")

    e = _get_einheit(db, einheit_id, lage_id)
    old_status = e.status
    e.status = status
    e.status_at = datetime.now(UTC)

    ts_field = _STATUS_TIMESTAMP.get(status)
    if ts_field:
        setattr(e, ts_field, e.status_at)

    # Auto-Abschnittszuweisung: wenn Einheit bereits einer Einsatzstelle mit Abschnitt zugeordnet ist
    if status == STATUS_IM_EINSATZ and not e.sector_id and e.incident_site_id:
        from app.models.major_incident import IncidentSite

        site = db.get(IncidentSite, e.incident_site_id)
        if site and site.sector_id:
            e.sector_id = site.sector_id

    _journal(
        db,
        lage_id,
        f"{e.label}: Status {STATUS_LABEL.get(old_status, old_status)} → {STATUS_LABEL.get(status, status)}",
        category="ressource",
        author_name=author_name,
        user_id=user_id,
        einheit_id=e.id,
        site_id=e.incident_site_id,
        ereignis_typ="abgerueckt" if status == STATUS_ABGERUECKT else "status",
        quelle="manuell",
    )
    return e


# ── Gruppenkommandant / Stellvertreter ───────────────────────────────────────


@dataclass
class GkErgebnis:
    aenderung: Literal["keine", "korrektur", "wechsel", "neu", "telefon"]
    leader: LageEinheitLeader | None
    zugang_gesperrt: bool = False
    auto_sms: AutoSmsAuftrag | None = None


def _fuehrungseingabe(
    db: Session,
    lage: MajorIncident,
    *,
    member_id: int | None,
    person_name: str | None,
    telefon: str | None,
) -> tuple[int | None, str, str | None, str | None]:
    """Normalisiert eine Führungszuweisung, ohne Stammdaten zu ändern."""
    name = (person_name or "").strip()[:120]
    member: Member | None = None
    if member_id is not None:
        member = (
            db.query(Member)
            .filter(Member.id == member_id, Member.org_id == lage.org_id, Member.active.is_(True))
            .first()
        )
        if not member:
            raise ValueError("Mitglied nicht gefunden oder nicht aktiv")
        name = member.full_name.strip()[:120]
    if not member_id and not name:
        raise ValueError("member_id oder person_name erforderlich")

    # None bedeutet: beim Mitglied die hinterlegte Nummer vorbelegen. Ein leerer
    # Formwert entfernt dagegen bewusst die Nummer der Lagezuweisung.
    phone = member.phone if telefon is None and member else telefon
    phone = phone.strip() if phone else None
    phone_e164 = telefon_zu_e164_at(phone)
    if phone and not phone_e164:
        raise ValueError("Telefonnummer ungueltig")
    return member_id, name, phone, phone_e164


def _gleiche_person(
    leader: LageEinheitLeader,
    member_id: int | None,
    person_name: str,
) -> bool:
    if member_id is not None:
        return leader.member_id == member_id
    return leader.member_id is None and (leader.person_name or "").casefold() == person_name.casefold()


def setze_gruppenkommandant(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    member_id: int | None = None,
    person_name: str | None = None,
    telefon: str | None = None,
    modus: str = "auto",
    note: str | None = None,
    user_id: int | None = None,
    author_name: str | None = None,
    quelle: str = "manuell",
) -> GkErgebnis:
    """Setzt den Gruppenkommandanten mit Historie und idempotenter Erkennung."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    if modus not in {"auto", "wechsel", "korrektur"}:
        raise ValueError("Ungueltiger Modus")

    member_id, name, phone, phone_e164 = _fuehrungseingabe(
        db,
        lage,
        member_id=member_id,
        person_name=person_name,
        telefon=telefon,
    )
    old = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    if old and (old.end_at is not None or old.rolle != "fuehrer"):
        old = None
    same_person = old is not None and _gleiche_person(old, member_id, name)

    if old and same_person and old.phone_e164 == phone_e164 and modus != "korrektur":
        return GkErgebnis(aenderung="keine", leader=old)

    now = datetime.now(UTC)
    if old and (same_person or modus == "korrektur"):
        old_phone_e164 = old.phone_e164
        old.member_id = member_id
        old.person_name = name
        old.phone = phone
        old.phone_e164 = phone_e164
        old.note = note
        einheit.commander_label = name
        if old_phone_e164 != phone_e164:
            old.phone_version += 1
            _journal(
                db,
                lage.id,
                f"{einheit.label}: Telefonnummer geaendert: "
                f"{telefon_maske(old_phone_e164) or 'keine'} -> {telefon_maske(phone_e164) or 'keine'}",
                category="ressource_fhr",
                author_name=author_name,
                user_id=user_id,
                einheit_id=einheit.id,
                ereignis_typ="gk_telefon",
                quelle=quelle,
            )
            from app.services.gk_zugang_service import widerrufe

            widerrufe(db, einheit.id, grund="telefon", user_id=user_id, typ="personal")
            from app.services.gk_zugang_service import plane_auto_sms
            return GkErgebnis(aenderung="telefon", leader=old, zugang_gesperrt=True,
                              auto_sms=plane_auto_sms(db, lage, einheit, old, "telefon"))
        return GkErgebnis(aenderung="korrektur", leader=old)

    predecessor_id = old.id if old else None
    if old:
        old.end_at = now
        old.ende_grund = "wechsel"
        old.ende_von = user_id
    new_leader = LageEinheitLeader(
        einheit_id=einheit.id,
        member_id=member_id,
        person_name=name,
        start_at=now,
        predecessor_id=predecessor_id,
        note=note,
        created_by=user_id,
        created_at=now,
        rolle="fuehrer",
        phone=phone,
        phone_e164=phone_e164,
        phone_version=1,
    )
    db.add(new_leader)
    db.flush()
    einheit.leader_assignment_id = new_leader.id
    einheit.commander_label = name
    if old:
        text = f"{einheit.label}: Gruppenkommandant gewechselt: {old.display_name} -> {new_leader.display_name}"
        ereignis_typ = "gk_gewechselt"
        aenderung: Literal["wechsel", "neu"] = "wechsel"
        from app.services.gk_zugang_service import widerrufe

        widerrufe(db, einheit.id, grund="wechsel", user_id=user_id)
    else:
        text = f"{einheit.label}: Gruppenkommandant zugewiesen: {new_leader.display_name}"
        ereignis_typ = "gk_zugewiesen"
        aenderung = "neu"
    _journal(
        db,
        lage.id,
        text,
        category="ressource_fhr",
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ=ereignis_typ,
        quelle=quelle,
    )
    from app.services.gk_zugang_service import plane_auto_sms
    return GkErgebnis(aenderung=aenderung, leader=new_leader, zugang_gesperrt=old is not None,
                      auto_sms=plane_auto_sms(db, lage, einheit, new_leader, aenderung))


def setze_stellvertreter(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    member_id: int | None = None,
    person_name: str | None = None,
    telefon: str | None = None,
    modus: str = "auto",
    note: str | None = None,
    user_id: int | None = None,
    author_name: str | None = None,
    quelle: str = "manuell",
) -> GkErgebnis:
    """Setzt oder korrigiert den aktiven Stellvertreter einer Einheit."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    if modus not in {"auto", "wechsel", "korrektur"}:
        raise ValueError("Ungueltiger Modus")
    member_id, name, phone, phone_e164 = _fuehrungseingabe(
        db,
        lage,
        member_id=member_id,
        person_name=person_name,
        telefon=telefon,
    )
    old = (
        db.query(LageEinheitLeader)
        .filter(
            LageEinheitLeader.einheit_id == einheit.id,
            LageEinheitLeader.rolle == "stellvertreter",
            LageEinheitLeader.end_at.is_(None),
        )
        .first()
    )
    same_person = old is not None and _gleiche_person(old, member_id, name)
    if old and same_person and old.phone_e164 == phone_e164 and modus != "korrektur":
        return GkErgebnis(aenderung="keine", leader=old)
    now = datetime.now(UTC)
    if old and (same_person or modus == "korrektur"):
        old_phone_e164 = old.phone_e164
        old.member_id = member_id
        old.person_name = name
        old.phone = phone
        old.phone_e164 = phone_e164
        old.note = note
        if old_phone_e164 != phone_e164:
            old.phone_version += 1
            _journal(
                db,
                lage.id,
                f"{einheit.label}: Telefonnummer Stellvertreter geaendert: "
                f"{telefon_maske(old_phone_e164) or 'keine'} -> {telefon_maske(phone_e164) or 'keine'}",
                category="ressource_fhr",
                author_name=author_name,
                user_id=user_id,
                einheit_id=einheit.id,
                ereignis_typ="gk_telefon",
                quelle=quelle,
            )
            return GkErgebnis(aenderung="telefon", leader=old)
        return GkErgebnis(aenderung="korrektur", leader=old)
    if old:
        old.end_at = now
        old.ende_grund = "wechsel"
        old.ende_von = user_id
    leader = LageEinheitLeader(
        einheit_id=einheit.id,
        member_id=member_id,
        person_name=name,
        start_at=now,
        predecessor_id=old.id if old else None,
        note=note,
        created_by=user_id,
        created_at=now,
        rolle="stellvertreter",
        phone=phone,
        phone_e164=phone_e164,
        phone_version=1,
    )
    db.add(leader)
    db.flush()
    _journal(
        db,
        lage.id,
        f"{einheit.label}: Stellvertreter {'gewechselt' if old else 'zugewiesen'}: {leader.display_name}",
        category="ressource_fhr",
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ="gk_gewechselt" if old else "gk_zugewiesen",
        quelle=quelle,
    )
    return GkErgebnis(aenderung="wechsel" if old else "neu", leader=leader)


def entferne_gruppenkommandant(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    user_id: int | None,
    author_name: str | None,
) -> LageEinheitLeader | None:
    """Beendet die aktuelle Gruppenkommandanten-Zuweisung."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    old = db.get(LageEinheitLeader, einheit.leader_assignment_id) if einheit.leader_assignment_id else None
    if not old or old.end_at is not None:
        return None
    old.end_at = datetime.now(UTC)
    old.ende_grund = "entfernt"
    old.ende_von = user_id
    einheit.leader_assignment_id = None
    einheit.commander_label = None
    _journal(
        db,
        lage.id,
        f"{einheit.label}: Gruppenkommandant entfernt: {old.display_name}",
        category="ressource_fhr",
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ="gk_gewechselt",
        quelle="manuell",
    )
    from app.services.gk_zugang_service import widerrufe

    widerrufe(db, einheit.id, grund="entfernt", user_id=user_id)
    return old


def entferne_stellvertreter(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    user_id: int | None,
    author_name: str | None,
) -> LageEinheitLeader | None:
    """Beendet die aktive Stellvertreter-Zuweisung."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    old = (
        db.query(LageEinheitLeader)
        .filter(
            LageEinheitLeader.einheit_id == einheit.id,
            LageEinheitLeader.rolle == "stellvertreter",
            LageEinheitLeader.end_at.is_(None),
        )
        .first()
    )
    if not old:
        return None
    old.end_at = datetime.now(UTC)
    old.ende_grund = "entfernt"
    old.ende_von = user_id
    _journal(
        db,
        lage.id,
        f"{einheit.label}: Stellvertreter entfernt: {old.display_name}",
        category="ressource_fhr",
        author_name=author_name,
        user_id=user_id,
        einheit_id=einheit.id,
        ereignis_typ="gk_gewechselt",
        quelle="manuell",
    )
    return old


def aktualisiere_einheit_stamm(
    db: Session,
    lage: MajorIncident,
    einheit: LageEinheit,
    *,
    funkrufname: str | None,
    org_name: str | None,
    bos: str | None,
    bereitstellungsraum: str | None,
    qty: int | None,
    unit: str | None,
    user_id: int | None,
    author_name: str | None,
) -> list[str]:
    """Aktualisiert die editierbaren Stammdaten und journalisiert echte Aenderungen."""
    if einheit.lage_id != lage.id:
        raise ValueError("Einheit nicht gefunden")
    values = {
        "Funkrufname": ("funkrufname", funkrufname, 40),
        "Organisation": ("org_name", org_name, 120),
        "BOS": ("bos", bos, 20),
        "Bereitstellungsraum": ("bereitstellungsraum", bereitstellungsraum, 200),
        "Menge": ("qty", qty, None),
        "Einheit": ("unit", unit, 20),
    }
    changed: list[str] = []
    for label, (field, value, maximum) in values.items():
        if isinstance(value, str):
            value = value.strip() or None
            if maximum is not None and len(value or "") > maximum:
                raise ValueError(f"{label} ist zu lang")
        if getattr(einheit, field) != value:
            setattr(einheit, field, value)
            changed.append(label)
    if changed:
        _journal(
            db,
            lage.id,
            f"{einheit.label}: Stammdaten geaendert: {', '.join(changed)}",
            category="ressource",
            author_name=author_name,
            user_id=user_id,
            einheit_id=einheit.id,
            ereignis_typ="status",
            quelle="manuell",
        )
    return changed


def rotate_einheit_leadership(
    db: Session,
    einheit_id: int,
    lage_id: int,
    *,
    member_id: int | None = None,
    person_name: str | None = None,
    note: str | None = None,
    created_by: int | None = None,
    author_name: str | None = None,
) -> LageEinheitLeader:
    """Kompatibler Legacy-Wrapper für ältere Aufrufer."""
    lage = db.get(MajorIncident, lage_id)
    einheit = _get_einheit(db, einheit_id, lage_id)
    if not lage:
        raise ValueError("Lage nicht gefunden")
    result = setze_gruppenkommandant(
        db,
        lage,
        einheit,
        member_id=member_id,
        person_name=person_name,
        note=note,
        user_id=created_by,
        author_name=author_name,
    )
    if not result.leader:
        raise ValueError("Gruppenkommandant konnte nicht gesetzt werden")
    return result.leader


# ── EL / AbsLtr ablösen (GslStaffAssignment) ─────────────────────────────────


def rotate_gsl_leadership(
    db: Session,
    lage: MajorIncident,
    role_code: str,
    *,
    sector_id: int | None = None,
    member_id: int | None = None,
    person_name: str | None = None,
    org_id: int,
    note: str | None = None,
    created_by: int | None = None,
    author_name: str | None = None,
) -> GslStaffAssignment:
    """EL (role_code='EL') oder Abschnittsleiter (role_code='AbsLtr') ablösen/neu besetzen."""
    if not member_id and not person_name:
        raise ValueError("member_id oder person_name erforderlich")

    from app.models.major_incident import GslStaffRole

    role = db.query(GslStaffRole).filter(GslStaffRole.code == role_code).first()
    if not role:
        raise ValueError(f"Rolle {role_code} nicht gefunden")

    now = datetime.now(UTC)

    # Alten Eintrag beenden und Pointer ermitteln
    old_id: int | None = None
    if role_code == "EL":
        if lage.leader_assignment_id:
            old = db.get(GslStaffAssignment, lage.leader_assignment_id)
            if old and old.end_at is None:
                old.end_at = now
                old_id = old.id
    else:
        # Abschnittsleiter: suche aktiven Eintrag für diesen Sector
        old = (
            db.query(GslStaffAssignment)
            .filter(
                GslStaffAssignment.incident_id == lage.id,
                GslStaffAssignment.role_id == role.id,
                GslStaffAssignment.sector_id == sector_id,
                GslStaffAssignment.end_at.is_(None),
            )
            .first()
        )
        if old:
            old.end_at = now
            old_id = old.id

    new_asgn = GslStaffAssignment(
        incident_id=lage.id,
        role_id=role.id,
        org_id=org_id,
        member_id=member_id,
        person_name=person_name,
        is_lead=True,
        start_at=now,
        predecessor_id=old_id,
        sector_id=sector_id,
        note=note,
        created_by=created_by,
        created_at=now,
    )
    db.add(new_asgn)
    db.flush()

    if role_code == "EL":
        lage.leader_assignment_id = new_asgn.id
    elif sector_id:
        sector = db.get(Sector, sector_id)
        if sector:
            sector.leader_assignment_id = new_asgn.id
            sector.leader_label = person_name or ""

    target = "Einsatzleiter" if role_code == "EL" else f"Abschnittsleiter Abschnitt {sector_id}"
    _journal(
        db,
        lage.id,
        f"{target} → {person_name or str(member_id)}",
        category="ressource_fhr",
        author_name=author_name,
        user_id=created_by,
    )
    return new_asgn


# ── Kräfteübersicht ───────────────────────────────────────────────────────────


def kraefteuebersicht(db: Session, lage: MajorIncident) -> dict[str, Any]:
    """Vollständiges S2-Lagebild: Pool + Abschnitte + Leiter aller Ebenen."""
    einheiten = lage.einheiten
    # Kinder werden ausschließlich unter ihrem Verband dargestellt. So bleiben
    # Karten und Zähler konsistent mit den Summen aus der Ressourcenpflege.
    eigenstaendige = [e for e in einheiten if e.verband_id is None]

    # Pool: sector_id IS NULL und nicht im aktiven Einsatz
    pool = [e for e in eigenstaendige if e.sector_id is None and e.status not in (STATUS_IM_EINSATZ, STATUS_ABGERUECKT)]
    # Im Einsatz ohne Abschnitt: sector_id IS NULL, aber bereits aktiv eingeteilt
    im_einsatz_no_sector = [e for e in eigenstaendige if e.sector_id is None and e.status == STATUS_IM_EINSATZ]
    abgerueckt = [e for e in eigenstaendige if e.status == STATUS_ABGERUECKT]

    sectors = sorted(lage.sectors, key=lambda s: s.sort_order)
    sector_map: dict[int, list[LageEinheit]] = {s.id: [] for s in sectors}
    for e in eigenstaendige:
        if e.sector_id and e.sector_id in sector_map and e.status != STATUS_ABGERUECKT:
            sector_map[e.sector_id].append(e)

    # Doppelverplanung: vehicle_id mehrfach active
    vid_list = [e.vehicle_id for e in einheiten if e.vehicle_id and e.status == STATUS_IM_EINSATZ]
    conflict_vids = {v for v in vid_list if vid_list.count(v) > 1}

    # Aktueller EL
    el_asgn = db.get(GslStaffAssignment, lage.leader_assignment_id) if lage.leader_assignment_id else None

    sector_data = []
    for s in sectors:
        abs_asgn = db.get(GslStaffAssignment, s.leader_assignment_id) if s.leader_assignment_id else None
        sector_data.append(
            {
                "sector": s,
                "einheiten": sector_map.get(s.id, []),
                "leader": abs_asgn,
            }
        )

    einheit_ids = [e.id for e in einheiten]
    dispatched_sites_by_einheit: dict[int, list[EinheitSiteDispatch]] = {}
    active_dispatched_sites_by_einheit: dict[int, list[EinheitSiteDispatch]] = {}
    letzte_rueckmeldung_by_einheit: dict[int, datetime] = {}
    if einheit_ids:
        all_dispatches = (
            db.query(EinheitSiteDispatch)
            .filter(
                EinheitSiteDispatch.einheit_id.in_(einheit_ids),
                EinheitSiteDispatch.withdrawn_at.is_(None),
            )
            .all()
        )
        for d in all_dispatches:
            dispatched_sites_by_einheit.setdefault(d.einheit_id, []).append(d)
            if d.beendet_at is None:
                active_dispatched_sites_by_einheit.setdefault(d.einheit_id, []).append(d)
                if d.letzte_rueckmeldung_at is not None:
                    previous = letzte_rueckmeldung_by_einheit.get(d.einheit_id)
                    if previous is None or d.letzte_rueckmeldung_at > previous:
                        letzte_rueckmeldung_by_einheit[d.einheit_id] = d.letzte_rueckmeldung_at

    from app.services.ressource_pflege_service import verband_summen

    kinder_by_verband: dict[int, list[LageEinheit]] = {}
    for e in einheiten:
        if e.verband_id is not None:
            kinder_by_verband.setdefault(e.verband_id, []).append(e)
    verband_summen_by_id = {
        e.id: verband_summen(db, e) for e in eigenstaendige if e.resource_type == "verband"
    }

    return {
        "pool": pool,
        "im_einsatz_no_sector": im_einsatz_no_sector,
        "sectors": sector_data,
        "abgerueckt": abgerueckt,
        "conflict_vids": conflict_vids,
        "el_asgn": el_asgn,
        "total": len(eigenstaendige),
        "reserve_count": len(pool),
        "im_einsatz_count": sum(1 for e in eigenstaendige if e.status == STATUS_IM_EINSATZ),
        "dispatched_sites_by_einheit": dispatched_sites_by_einheit,
        "active_dispatched_sites_by_einheit": active_dispatched_sites_by_einheit,
        "letzte_rueckmeldung_by_einheit": letzte_rueckmeldung_by_einheit,
        "verband_kinder_by_id": kinder_by_verband,
        "verband_summen_by_id": verband_summen_by_id,
    }
