"""Geschäftslogik für Statusmeldungen im GSL-Einheitenmodus."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.core.audit import write_audit
from app.db import get_db
from app.models.major_incident import (
    EINHEIT_STATUS_AKTIV,
    EINHEIT_STATUS_BEENDET,
    EINHEIT_STATUS_LABEL,
    EinheitSiteDispatch,
    IncidentSite,
    LageEinheit,
    MajorIncident,
    MajorIncidentStatus,
    SitePhase,
)
from app.models.master import VehicleMaster
from app.models.user import DeviceToken
from app.services import resource_service
from app.services.resource_service import STATUS_BEREITGESTELLT, STATUS_IM_EINSATZ, dispatch_aktiv_filter

Quelle = Literal["tablet", "funk", "mcp", "simulation"]

# Gesamtansicht (E1): Diese Routen zeigen den Einsatz, ohne eine Bedienung oder
# Token-Ausgabe zu ermoeglichen. Die Namen sind bewusst an die Endpunktfunktionen
# gebunden, damit neue Fuehrungsrouten standardmaessig gesperrt bleiben.
EINHEIT_GESAMTANSICHT_ROUTEN = frozenset({
    # Board und Einsatzstellen
    "lage_overview", "lage_board", "lage_kopf_oob", "phase_column_partial",
    "board_sector_filter_partial", "site_card_partial", "site_detail", "site_druck",
    # Karte und Positionsdaten
    "lage_karte", "lage_karte_sites", "lage_karte_sektoren", "lage_karte_cross_markers",
    "vehicle_positions",
    # Funkjournal und Stab (ausschliesslich lesende Ansichten)
    "lage_funkjournal", "funkjournal_rows", "lage_stab", "stab_tafel",
    "stab_journal", "stab_einsatzjournal",
    # Einsatzjournal, Ressourcen und Dashboard
    "lage_journal_entry_detail", "lage_journal_media_image", "lage_ressourcen",
    "lage_ressourcen_journal", "lage_ressourcen_kraefteuebersicht",
    "lage_ressourcen_planung", "lage_dashboard",
    # Uebergreifende Meldungen und Medien
    "cross_marker_board_col", "cross_marker_panel", "cross_marker_media_serve",
    "lage_media_serve", "lage_media_thumb",
    # Der Editor bleibt gesperrt; nur die unveraenderliche Druckansicht ist erlaubt.
    "lagedokument_druck",
})


def ist_einheit_geraet(request: HTTPConnection, db: Session) -> bool:
    """Prueft und cached pro HTTP-Request das Profil des aktiven Geraetetokens."""
    if hasattr(request.state, "ist_einheit_geraet"):
        return request.state.ist_einheit_geraet

    user = getattr(request.state, "user", None)
    if not getattr(request.state, "is_device", False) or user is None:
        request.state.ist_einheit_geraet = False
        return False

    token_id = getattr(request.state, "device_token_id", None)
    if token_id is not None:
        device_token = (
            db.query(DeviceToken)
            .filter(
                DeviceToken.id == token_id,
                DeviceToken.user_id == user.id,
                DeviceToken.revoked_at.is_(None),
            )
            .first()
        )
    else:
        # Gleiches Legacy-Fallback-Verhalten wie device_api._get_device_token().
        device_token = (
            db.query(DeviceToken)
            .filter(DeviceToken.user_id == user.id, DeviceToken.revoked_at.is_(None))
            .order_by(DeviceToken.created_at.desc())
            .first()
        )
    request.state.ist_einheit_geraet = bool(device_token and device_token.gsl_profil == "einheit")
    return request.state.ist_einheit_geraet


def einheit_geraet_nur_lesen(
    request: HTTPConnection,
    db: Session = Depends(get_db),
) -> None:
    """Sperrt Einheit-Tablets ausserhalb der bewusst kleinen Gesamtansicht-Readlist."""
    # WebSockets passieren die HTTP-Middleware nicht und werden separat behandelt.
    if request.scope["type"] == "websocket":
        return
    if getattr(request.state, "user", None) is None or not ist_einheit_geraet(request, db):
        return

    # FastAPI setzt endpoint vor der Aufloesung von Router-Dependencies. Der
    # Route-Fallback dokumentiert und schuetzt gegen abweichende Starlette-Versionen.
    endpoint = request.scope.get("endpoint") or getattr(request.scope.get("route"), "endpoint", None)
    endpoint_name = getattr(endpoint, "__name__", None)
    if request.scope.get("method") not in {"GET", "HEAD"} or endpoint_name not in EINHEIT_GESAMTANSICHT_ROUTEN:
        raise HTTPException(status_code=403, detail="Im Einheitenmodus nur lesender Zugriff")

    # Die Middleware laedt User-Objekte fuer jeden HTTP-Request neu; dieses
    # transiente Attribut kann deshalb nicht in andere Requests ueberlaufen.
    request.state.user.gsl_nur_lesen = True

ERLAUBTE_UEBERGAENGE: dict[str, frozenset[str]] = {
    "zugewiesen": frozenset({"bestaetigt", "anfahrt", "vor_ort", "nicht_durchfuehrbar"}),
    "bestaetigt": frozenset({"anfahrt", "vor_ort", "nicht_durchfuehrbar"}),
    "anfahrt": frozenset({"bestaetigt", "vor_ort", "nicht_durchfuehrbar"}),
    "vor_ort": frozenset({"bestaetigt", "in_arbeit", "abgeschlossen", "nicht_durchfuehrbar"}),
    "in_arbeit": frozenset({"bestaetigt", "vor_ort", "abgeschlossen", "nicht_durchfuehrbar"}),
    "abgeschlossen": frozenset(),
    "nicht_durchfuehrbar": frozenset(),
}


class EinheitKonflikt(Exception):
    """Fachlicher Konflikt, der von API-Aufrufern als 409 umgesetzt wird."""

    def __init__(self, code: str, details: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class EinheitKontext:
    device_token: DeviceToken | None
    vehicle: VehicleMaster | None
    einheit: LageEinheit
    lage: MajorIncident
    org_id: int
    quelle: Quelle
    simulation: bool = False


def _iso_z(wert: datetime | None) -> str | None:
    if wert is None:
        return None
    return wert.strftime("%Y-%m-%dT%H:%M:%SZ")


def kontext_fuer_geraet(
    db: Session, user, device_token: DeviceToken, lage_id: int | None = None,
) -> EinheitKontext | None:
    """Löst den berechtigten Einheiten-Kontext eines Gerätebenutzers auf."""
    if (
        not user.is_device
        or device_token.user_id != user.id
        or device_token.revoked_at is not None
        or device_token.vehicle_master_id is None
        or device_token.gsl_profil != "einheit"
        or not user.org_id
    ):
        return None
    query = (
        db.query(LageEinheit, MajorIncident)
        .join(MajorIncident, MajorIncident.id == LageEinheit.lage_id)
        .filter(
            LageEinheit.vehicle_id == device_token.vehicle_master_id,
            LageEinheit.status.in_((STATUS_BEREITGESTELLT, STATUS_IM_EINSATZ)),
            MajorIncident.status == MajorIncidentStatus.active,
            MajorIncident.org_id == user.org_id,
        )
    )
    if lage_id is not None:
        query = query.filter(MajorIncident.id == lage_id)
    result = query.order_by(MajorIncident.started_at.desc(), MajorIncident.id.desc()).first()
    if result is None:
        return None
    einheit, lage = result
    return EinheitKontext(
        device_token=device_token,
        vehicle=db.get(VehicleMaster, device_token.vehicle_master_id),
        einheit=einheit,
        lage=lage,
        org_id=user.org_id,
        quelle="tablet",
    )


def kontext_fuer_einheit(
    db: Session, user, einheit_id: int, *, quelle: Quelle,
) -> EinheitKontext:
    """Löst den Kontext für stellvertretend erfasste Statusmeldungen auf."""
    einheit = db.get(LageEinheit, einheit_id)
    if einheit is None:
        raise LookupError("Einheit nicht gefunden")
    lage = db.get(MajorIncident, einheit.lage_id)
    if (
        lage is None
        or lage.org_id != user.org_id
        or lage.status != MajorIncidentStatus.active
    ):
        raise LookupError("Einheit nicht gefunden")
    return EinheitKontext(
        device_token=None,
        vehicle=db.get(VehicleMaster, einheit.vehicle_id) if einheit.vehicle_id else None,
        einheit=einheit,
        lage=lage,
        org_id=user.org_id,
        quelle=quelle,
        simulation=quelle == "simulation",
    )


def auftrag_laden(db: Session, ctx: EinheitKontext, dispatch_id: int) -> EinheitSiteDispatch:
    dispatch = (
        db.query(EinheitSiteDispatch)
        .join(IncidentSite, IncidentSite.id == EinheitSiteDispatch.site_id)
        .filter(
            EinheitSiteDispatch.id == dispatch_id,
            EinheitSiteDispatch.einheit_id == ctx.einheit.id,
            IncidentSite.major_incident_id == ctx.lage.id,
        )
        .first()
    )
    if dispatch is None:
        raise LookupError("Auftrag nicht gefunden")
    return dispatch


def _auftragsdaten(dispatch: EinheitSiteDispatch) -> dict:
    site = dispatch.site
    from app.services.gsl_live_service import _combined_site_address
    return {
        "dispatch_id": dispatch.id,
        "site_id": site.id,
        "bezeichnung": site.bezeichnung,
        "einsatzgrund": site.einsatzgrund,
        "adresse": _combined_site_address(site),
        "lat": site.lat,
        "lng": site.lng,
        "priority": site.priority.name if site.priority else None,
        "phase": site.phase.value,
        "auftrag": dispatch.auftrag,
        "einheit_status": dispatch.einheit_status,
        "einheit_status_label": EINHEIT_STATUS_LABEL[dispatch.einheit_status],
        "reihenfolge": dispatch.reihenfolge,
        "version": dispatch.version,
        "dispatched_at": _iso_z(dispatch.dispatched_at),
        "status_at": _iso_z(dispatch.status_at),
        "geaendert_at": _iso_z(dispatch.geaendert_at),
        "gmaps_url": (
            f"https://maps.google.com/?q={site.lat},{site.lng}"
            if site.lat is not None and site.lng is not None else None
        ),
    }


def auftraege_fuer_einheit(db: Session, ctx: EinheitKontext) -> dict:
    dispatches = (
        db.query(EinheitSiteDispatch)
        .join(IncidentSite, IncidentSite.id == EinheitSiteDispatch.site_id)
        .filter(
            EinheitSiteDispatch.einheit_id == ctx.einheit.id,
            IncidentSite.major_incident_id == ctx.lage.id,
        )
        .all()
    )
    zurueckgezogen = [d for d in dispatches if d.withdrawn_at is not None]
    abgeschlossen = [d for d in dispatches if d.withdrawn_at is None and d.beendet_at is not None]
    aktiv = [d for d in dispatches if d.withdrawn_at is None and d.beendet_at is None]
    aktuell_dispatch = next(
        (d for d in aktiv if d.site_id == ctx.einheit.incident_site_id
         and d.einheit_status in EINHEIT_STATUS_AKTIV),
        None,
    )
    priority_order = {"sofort": 0, "dringend": 1, "normal": 2, "aufschiebbar": 3}
    weitere_dispatches = [d for d in aktiv if d is not aktuell_dispatch]
    weitere_dispatches.sort(key=lambda d: (
        d.reihenfolge is None,
        d.reihenfolge if d.reihenfolge is not None else 0,
        priority_order.get(d.site.priority.name if d.site.priority else "", 4),
        d.dispatched_at,
    ))
    fahrzeug = None
    if ctx.vehicle:
        fahrzeug = ctx.vehicle.code or ctx.vehicle.name
    return {
        "kopf": {
            "fahrzeug": fahrzeug or ctx.einheit.label,
            "einheit_id": ctx.einheit.id,
            "einheit_label": ctx.einheit.label,
            "lage_id": ctx.lage.id,
            "lage_name": ctx.lage.name,
            "is_exercise": ctx.lage.is_exercise,
            "offen": len(aktiv),
            "erledigt": len(abgeschlossen),
        },
        "kategorien": {
            "aktuell": _auftragsdaten(aktuell_dispatch) if aktuell_dispatch else None,
            "weitere": [_auftragsdaten(d) for d in weitere_dispatches],
            "abgeschlossen": [_auftragsdaten(d) for d in abgeschlossen],
            "zurueckgezogen": [_auftragsdaten(d) for d in zurueckgezogen],
        },
        "naechster_dispatch_id": weitere_dispatches[0].id if aktuell_dispatch is None and weitere_dispatches else None,
        "server_time": _iso_z(datetime.now(UTC)),
    }


def setze_einheit_status(
    db: Session,
    ctx: EinheitKontext,
    dispatch: EinheitSiteDispatch,
    neuer_status: str,
    *,
    user_id: int | None,
    author_name: str | None,
    grund: str | None = None,
    unterbrechen: bool = False,
    erfasst_at: datetime | None = None,
) -> dict:
    """Wendet einen erlaubten Einheiten-Statuswechsel an, ohne zu committen."""
    if dispatch.withdrawn_at is not None:
        raise EinheitKonflikt("auftrag_zurueckgezogen")
    alter_status = dispatch.einheit_status
    if alter_status == neuer_status:
        return {
            "geaendert": False, "dispatch_id": dispatch.id,
            "einheit_status": alter_status, "version": dispatch.version,
            "phase_geaendert": False, "unterbrochen_dispatch_id": None,
            "alle_einheiten_fertig": _alle_einheiten_fertig(db, dispatch.site_id),
        }
    if neuer_status not in ERLAUBTE_UEBERGAENGE.get(alter_status, frozenset()):
        raise EinheitKonflikt("ungueltiger_uebergang")
    if neuer_status == "nicht_durchfuehrbar" and not (grund and grund.strip()):
        raise ValueError("Grund für nicht durchführbar erforderlich")

    now = datetime.now(UTC)
    unterbrochen_dispatch_id = None
    if neuer_status in EINHEIT_STATUS_AKTIV:
        # Tests und manche Hintergrundpfade arbeiten mit autoflush=False.
        # Der Konflikt muss auch dann bereits vorgenommene Statuswechsel sehen.
        db.flush()
        anderer = (
            db.query(EinheitSiteDispatch)
            .join(IncidentSite, IncidentSite.id == EinheitSiteDispatch.site_id)
            .filter(
                EinheitSiteDispatch.einheit_id == ctx.einheit.id,
                EinheitSiteDispatch.id != dispatch.id,
                dispatch_aktiv_filter(),
                EinheitSiteDispatch.einheit_status.in_(EINHEIT_STATUS_AKTIV),
            )
            .first()
        )
        if anderer:
            if not unterbrechen:
                raise EinheitKonflikt("aktiver_auftrag", {
                    "dispatch_id": anderer.id, "site_id": anderer.site_id,
                    "bezeichnung": anderer.site.bezeichnung,
                })
            anderer.einheit_status = "bestaetigt"
            anderer.status_at = now
            anderer.letzte_rueckmeldung_at = now
            if ctx.einheit.incident_site_id == anderer.site_id:
                ctx.einheit.incident_site_id = None
            from app.services.site_log_service import add_site_log
            add_site_log(
                db, anderer.site, "einheit", f"{ctx.einheit.label}: Auftrag unterbrochen",
                user_id=user_id, author_name=author_name, einheit_id=ctx.einheit.id,
                erfasst_at=erfasst_at,
            )
            unterbrochen_dispatch_id = anderer.id

    dispatch.einheit_status = neuer_status
    dispatch.status_at = now
    dispatch.letzte_rueckmeldung_at = now
    # version/geaendert_at zählen nur Führungsänderungen am Auftrag (Konflikterkennung, Plan 4.1).
    if neuer_status == "bestaetigt" and dispatch.bestaetigt_at is None:
        dispatch.bestaetigt_at = now
    if neuer_status == "vor_ort" and dispatch.vor_ort_at is None:
        dispatch.vor_ort_at = now
    if neuer_status in EINHEIT_STATUS_BEENDET:
        dispatch.beendet_at = now
        dispatch.beendet_grund = grund.strip() if grund else None

    site = dispatch.site
    if neuer_status in EINHEIT_STATUS_AKTIV:
        ctx.einheit.incident_site_id = site.id
        ctx.einheit.sector_id = site.sector_id
    elif neuer_status in EINHEIT_STATUS_BEENDET or neuer_status == "bestaetigt":
        if ctx.einheit.incident_site_id == site.id:
            ctx.einheit.incident_site_id = None
    if ctx.einheit.status == STATUS_BEREITGESTELLT:
        ctx.einheit.status = STATUS_IM_EINSATZ
        ctx.einheit.committed_at = now

    suffix = f" – {grund.strip()}" if grund and grund.strip() else ""
    if ctx.quelle == "funk":
        suffix += " (per Funk)"
    elif ctx.simulation:
        suffix += " (Simulation)"
    from app.services.site_log_service import add_site_log
    add_site_log(
        db, site, "einheit", f"{ctx.einheit.label}: {EINHEIT_STATUS_LABEL[neuer_status]}{suffix}",
        user_id=user_id, author_name=author_name, einheit_id=ctx.einheit.id,
        erfasst_at=erfasst_at,
    )
    if neuer_status in {"bestaetigt", "abgeschlossen", "nicht_durchfuehrbar"}:
        resource_service._journal(
            db, ctx.lage.id, f"{ctx.einheit.label}: {EINHEIT_STATUS_LABEL[neuer_status]}",
            category="ressource", author_name=author_name, user_id=user_id,
        )

    phase_geaendert = False
    if neuer_status in {"vor_ort", "in_arbeit"}:
        from app.services import lagemeldung_service
        lagemeldung_service.ensure_timer(site, db)
        if site.phase in {
            SitePhase.eingegangen, SitePhase.erkundung, SitePhase.bewertet, SitePhase.disponiert,
        }:
            from app.services.major_incident_service import setze_site_phase
            ausloeser = f"{ctx.einheit.label} {'vor Ort' if neuer_status == 'vor_ort' else 'in Arbeit'}"
            phase_geaendert = setze_site_phase(
                db, site, SitePhase.in_arbeit, user_id=user_id,
                author_name=author_name, ausloeser=ausloeser,
            )
    write_audit(
        db, "gsl.einheit.status", user_id=user_id,
        payload={
            "lage_id": ctx.lage.id, "site_id": site.id, "dispatch_id": dispatch.id,
            "einheit_id": ctx.einheit.id, "von": alter_status, "nach": neuer_status,
            "quelle": ctx.quelle,
            "device_token_id": ctx.device_token.id if ctx.device_token else None,
            "erfasst_at": _iso_z(erfasst_at),
        },
    )
    # Die Fertigmeldung berücksichtigt auch den eben geänderten Auftrag.
    db.flush()
    return {
        "geaendert": True, "dispatch_id": dispatch.id,
        "einheit_status": neuer_status, "version": dispatch.version,
        "phase_geaendert": phase_geaendert,
        "unterbrochen_dispatch_id": unterbrochen_dispatch_id,
        "alle_einheiten_fertig": _alle_einheiten_fertig(db, site.id),
    }


def _alle_einheiten_fertig(db: Session, site_id: int) -> bool:
    return not db.query(EinheitSiteDispatch).filter(
        EinheitSiteDispatch.site_id == site_id,
        EinheitSiteDispatch.withdrawn_at.is_(None),
        EinheitSiteDispatch.beendet_at.is_(None),
    ).first()


def hat_tablet(db: Session, einheit: LageEinheit) -> bool:
    """Ob für das Fahrzeug dieser Einheit ein aktives Einheiten-Tablet existiert."""
    if einheit.vehicle_id is None:
        return False
    return db.query(DeviceToken.id).filter(
        DeviceToken.vehicle_master_id == einheit.vehicle_id,
        DeviceToken.revoked_at.is_(None),
        DeviceToken.gsl_profil == "einheit",
    ).first() is not None
